"""
Popfun 日志平台：Playwright 登录 → 打开 Discover 页 → 抓取 error 相关日志 → 推送到飞书。
配置：POPFUN_LOG_BASE_URL / POPFUN_LOG_USERNAME / POPFUN_LOG_PASSWORD（.env）/ FEISHU_POPFUN_LOG_CHAT_ID
抓取结果会写入 tasks/popfun_log_capture.txt 便于核对。

本文件为原 Playwright 实现备份；CDP 版见 popfun_log_cdp.py。
"""
import logging
import re
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path

from config import (
    FEISHU_POPFUN_LOG_CHAT_ID,
    POPFUN_LOG_BASE_URL,
    POPFUN_LOG_PASSWORD,
    POPFUN_LOG_USERNAME,
)
from lark_client import send_card_message, send_text_message

logger = logging.getLogger(__name__)

LOGIN_URL = f"{POPFUN_LOG_BASE_URL}/login"
# Discover 页固定地址；只抓表格内容，error 在代码里过滤。sampleSize:100 表示一页 100 条（与界面一致）
DISCOVER_URL = (
    f"{POPFUN_LOG_BASE_URL}/app/discover#/"
    "?_g=(filters:!(),refreshInterval:(pause:!f,value:0),time:(from:now-15m,to:now))"
    "&_a=(columns:!(message,log.file.path,host.name),filters:!(),hideChart:!f,"
    "index:'18ddc9e0-9664-4e2a-8466-f48f47301cf3',interval:auto,query:(language:kuery,query:''),"
    "sort:!(!('@timestamp',desc)),sampleSize:100)"
)

ERROR_PATTERN = re.compile(r"error", re.I)
# 错误日志路径特征：-error.log
ERROR_PATH_PATTERN = re.compile(r"-error\.log", re.I)


def _login_and_fetch_error_logs(headless: bool = True) -> list[dict]:
    """登录后打开 Discover，解析表格中的日志行，只保留 message 含 error 的。返回 [{message, path, host, ts}]"""
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        raise RuntimeError("请安装 playwright: pip install playwright && playwright install chromium")

    if not POPFUN_LOG_USERNAME or not POPFUN_LOG_PASSWORD:
        raise ValueError("请设置 POPFUN_LOG_USERNAME 和 POPFUN_LOG_PASSWORD（.env）")

    rows: list[dict] = []
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=headless)
        try:
            page = browser.new_page()
            page.set_default_timeout(25000)

            # 1) 登录
            logger.info("popfun_log: loading login %s", LOGIN_URL)
            page.goto(LOGIN_URL, wait_until="domcontentloaded", timeout=20000)
            page.wait_for_timeout(2000)

            # 常见登录表单：username / password 或 email
            username_sel = "input[name='username'], input[name='email'], input#username, input[type='text']"
            password_sel = "input[name='password'], input[type='password'], input#password"
            un = page.locator(username_sel).first
            pw = page.locator(password_sel).first
            if un.count() == 0 or pw.count() == 0:
                # 尝试 placeholder / label 等
                un = page.locator("input").first
                pw = page.locator("input").nth(1)
            un.fill(POPFUN_LOG_USERNAME)
            pw.fill(POPFUN_LOG_PASSWORD)
            page.wait_for_timeout(500)
            # 提交：button[type=submit] 或 包含登录/Login 的按钮
            submit = page.locator("button[type='submit'], input[type='submit'], button:has-text('登录'), button:has-text('Login'), button:has-text('Sign in')").first
            if submit.count() > 0:
                submit.click()
            else:
                page.keyboard.press("Enter")
            page.wait_for_timeout(3000)

            # 2) 打开 Discover 固定地址，只抓表格内容
            logger.info("popfun_log: loading discover %s", DISCOVER_URL)
            page.goto(DISCOVER_URL, wait_until="domcontentloaded", timeout=20000)
            # 等待数据加载：表格或“个命中”出现
            try:
                page.wait_for_selector("[data-test-subj='docTableRow'], .euiTableRow, table tbody tr, [class*='docTable']", timeout=15000)
            except Exception:
                pass
            page.wait_for_timeout(5000)

            # 3a) 若页面是 euiDataGrid（与你「查看元素」一致）：按 data-gridcell-column-id 取 @timestamp / message / log.file.path / host.name
            def _get_cell_text(row, col_id: str) -> str:
                try:
                    cell = row.locator(f'[data-gridcell-column-id="{col_id}"]')
                    if cell.count() == 0:
                        return ""
                    inner = cell.locator(".dscDiscoverGrid__cellValue")
                    if inner.count() > 0:
                        return inner.first.inner_text().strip()
                    return cell.first.inner_text().strip()
                except Exception:
                    return ""

            def _parse_eui_row_raw(row):
                """解析一行，返回 dict（不做 error 过滤），便于调试。"""
                path = _get_cell_text(row, "log.file.path")
                msg = _get_cell_text(row, "message")
                host = _get_cell_text(row, "host.name")
                ts = _get_cell_text(row, "@timestamp")
                if not ts and msg:
                    m = re.match(r"^(\d{4}/\d{2}/\d{2}\s+\d{2}:\d{2}:\d{2})", msg)
                    if m:
                        ts = m.group(1)
                return {"ts": ts, "path": path, "host": host, "message": msg}

            def _parse_eui_row(row) -> dict | None:
                raw = _parse_eui_row_raw(row)
                path, msg = raw["path"], raw["message"]
                if not path and not msg:
                    return None
                if not ERROR_PATH_PATTERN.search(path) and not ERROR_PATTERN.search(msg):
                    return None
                return {"message": (msg or "(未抓取到)")[:500], "path": path[:200], "host": raw["host"][:100], "ts": raw["ts"]}

            debug_lines: list[str] = []
            try:
                primary_sel = "[data-test-subj='euiDataGridBody'] .euiDataGridRow"
                grid_body = page.locator("[data-test-subj='euiDataGridBody']")
                if grid_body.count() == 0:
                    debug_lines.append("euiDataGridBody 未找到")
                else:
                    # 虚拟滚动：边滚边采。若 grid 容器 scrollHeight===clientHeight（不可滚），则用键盘 PageDown 驱动列表翻页。
                    seen_keys: set[tuple] = set()
                    scroll_step = 300
                    max_steps = 150
                    scroll_info = grid_body.first.evaluate("""
                        el => {
                            let target = el;
                            if (el.scrollHeight <= el.clientHeight + 20) {
                                let p = el.parentElement;
                                while (p && p !== document.body) {
                                    if (p.scrollHeight > p.clientHeight + 20) { target = p; break; }
                                    p = p.parentElement;
                                }
                            }
                            return { scrollTop: target.scrollTop, scrollHeight: target.scrollHeight, clientHeight: target.clientHeight, canScroll: target.scrollHeight > target.clientHeight + 20 };
                        }
                    """)
                    can_scroll = scroll_info.get("canScroll") is True
                    logger.info("popfun_log: 滚动容器 scrollHeight=%s clientHeight=%s canScroll=%s", scroll_info.get("scrollHeight"), scroll_info.get("clientHeight"), can_scroll)
                    no_new_steps = 0
                    if not can_scroll:
                        grid_body.first.click()
                        page.wait_for_timeout(300)
                    for step in range(max_steps):
                        eui_rows = page.locator(primary_sel)
                        ne = eui_rows.count()
                        for i in range(ne):
                            try:
                                raw = _parse_eui_row_raw(eui_rows.nth(i))
                                key = (raw["ts"], raw["path"], (raw["message"] or "")[:80])
                                if key in seen_keys:
                                    continue
                                seen_keys.add(key)
                                p = _parse_eui_row(eui_rows.nth(i))
                                if p:
                                    rows.append(p)
                            except Exception:
                                pass
                        if can_scroll:
                            result = grid_body.first.evaluate("""
                                el => {
                                    let target = el;
                                    if (el.scrollHeight <= el.clientHeight + 20) {
                                        let p = el.parentElement;
                                        while (p && p !== document.body) {
                                            if (p.scrollHeight > p.clientHeight + 20) { target = p; break; }
                                            p = p.parentElement;
                                        }
                                    }
                                    const before = target.scrollTop;
                                    target.scrollTop += """ + str(scroll_step) + """;
                                    const after = target.scrollTop;
                                    const bottom = (target.scrollTop + target.clientHeight >= target.scrollHeight - 10);
                                    return { atBottom: bottom, moved: after !== before };
                                }
                            """)
                            at_bottom = result.get("atBottom") is True
                            page.wait_for_timeout(400)
                            if at_bottom:
                                break
                        else:
                            before_step = len(seen_keys)
                            for _ in range(5):
                                page.keyboard.press("PageDown")
                                page.wait_for_timeout(280)
                            if step >= 6 and len(seen_keys) == before_step:
                                no_new_steps += 1
                                if no_new_steps >= 3:
                                    break
                            else:
                                no_new_steps = 0
                    logger.info("popfun_log: 虚拟滚动采集结束, 去重后 %d 行, error %d 条", len(seen_keys), len(rows))
                    # txt 仍存一份当前可见的原始 HTML（最后一屏）
                    raw_html = grid_body.first.evaluate("el => el.innerHTML")
                    debug_lines.append("=== 最原始数据：euiDataGridBody.innerHTML（最后一屏，未解析）===")
                    debug_lines.append(f"去重后总行数: {len(seen_keys)}, error 推送: {len(rows)}\n")
                    debug_lines.append(str(raw_html))
            except Exception as e:
                debug_lines.append(f"euiDataGrid 异常: {e}")
                logger.debug("popfun_log euiDataGrid try: %s", e)

            def _parse_row_cells(row, cell_sel: str) -> dict | None:
                """按内容识别 path / message / host，不依赖列顺序；时间从 message 开头解析。"""
                cells = row.locator(cell_sel)
                cnt = cells.count()
                if cnt < 3:
                    return None
                texts = []
                for j in range(cnt):
                    cell = cells.nth(j)
                    t = cell.inner_text().strip()
                    if not t:
                        try:
                            t = (cell.evaluate("el => (el && el.textContent) ? el.textContent.trim() : ''") or "").strip()
                        except Exception:
                            pass
                    texts.append(t)
                path = msg = host = ""
                for t in texts:
                    if not t:
                        continue
                    if ERROR_PATH_PATTERN.search(t) and ("/.pm2/" in t or "/home/" in t):
                        path = t
                    elif len(t) < 80 and "/" not in t and not re.match(r"^\d{4}/\d{2}", t):
                        host = t
                    elif len(t) > 20 or "Volume" in t or "farDepth" in t or "parans" in t or "spot-make" in t:
                        msg = t
                if not path and not msg:
                    return None
                is_error = ERROR_PATH_PATTERN.search(path) or ERROR_PATTERN.search(msg)
                if not is_error:
                    return None
                if ERROR_PATH_PATTERN.search(msg) and path:
                    msg, path = path, msg
                if not msg or ERROR_PATH_PATTERN.search(msg):
                    msg = "(该行消息未抓取到，请至平台查看)"
                ts = ""
                if msg:
                    m = re.match(r"^(\d{4}/\d{2}/\d{2}\s+\d{2}:\d{2}:\d{2})", msg)
                    if m:
                        ts = m.group(1)
                return {"message": msg[:500], "path": path[:200], "host": host[:100], "ts": ts}

            used_path = "3a euiDataGrid"
            # 3b) 若 euiDataGrid 未命中（Playwright 抓到的 DOM 可能不同）：按 docTable/role=row + 按内容识别 path/message
            if not rows:
                used_path = "3b fallback"
                for row_sel in [
                    "[data-test-subj='discoverDocTable'] [data-test-subj='docTableRow']",
                    "[data-test-subj='docTableRow']",
                    ".euiTableRow",
                    "table tbody tr",
                    "[data-test-subj='discoverDocTable'] [role='row']",
                    "[role='row']",
                ]:
                    doc_rows = page.locator(row_sel)
                    n = doc_rows.count()
                    if n < 1:
                        continue
                    logger.info("popfun_log: found %d rows with selector %s", n, row_sel[:50])
                    cell_sel = "td, [role='cell']"
                    for i in range(min(n, 100)):
                        try:
                            row = doc_rows.nth(i)
                            parsed = _parse_row_cells(row, cell_sel)
                            if parsed:
                                rows.append(parsed)
                        except Exception as e:
                            logger.debug("popfun_log row parse: %s", e)
                    if rows:
                        logger.info("popfun_log: extracted %d error rows (fallback)", len(rows))
                        break
            # 3c) 兜底：从整页正文中抓取含 -error.log 或 error 的连续行块
            if not rows:
                used_path = "3c body"
                body_text = page.locator("body").inner_text()
                for ln in body_text.split("\n"):
                    ln = ln.strip()
                    if ("-error.log" in ln or ERROR_PATTERN.search(ln)) and len(ln) > 15:
                        rows.append({"message": ln[:500], "path": "", "host": "", "ts": ""})
                rows = rows[:50]

            debug_lines.append(f"\n--- 最终 ---\n使用路径: {used_path}\n推送条数: {len(rows)}")
            capture_path = Path(__file__).resolve().parent / "popfun_log_capture.txt"
            try:
                with open(capture_path, "w", encoding="utf-8") as f:
                    f.write("\n".join(debug_lines))
                logger.info("popfun_log: 抓取数据已写入 %s", capture_path)
            except Exception as e:
                logger.warning("popfun_log: 写入 capture 文件失败: %s", e)

        finally:
            browser.close()

    return rows


def _build_log_card(rows: list[dict]) -> dict:
    """飞书卡片：每条展示 时间 + 消息，以及路径/主机。"""
    elements = [
        {
            "tag": "div",
            "text": {"tag": "lark_md", "content": f"**Popfun 日志平台** · 近 15 分钟 **error** 日志，共 **{len(rows)}** 条（最多展示 20 条）", "lines": 1},
        },
        {"tag": "hr"},
    ]
    for i, r in enumerate(rows[:20], 1):
        ts = (r.get("ts") or "").strip()
        msg = (r.get("message") or "").strip() or "-"
        path = (r.get("path") or "").strip()
        host = (r.get("host") or "").strip()
        line = f"**{i}. **"
        if ts:
            line += f"\n**时间：** {ts}"
        line += f"\n**消息：** {msg}"
        if host:
            line += f"\n主机: {host}"
        if path:
            line += f"\n路径: {path}"
        elements.append({"tag": "div", "text": {"tag": "lark_md", "content": line}})
    if len(rows) > 20:
        elements.append({"tag": "div", "text": {"tag": "plain_text", "content": f"... 共 {len(rows)} 条", "lines": 1}})
    elements.append({"tag": "hr"})
    elements.append({
        "tag": "div",
        "text": {"tag": "plain_text", "content": f"🕐 {datetime.now().strftime('%Y-%m-%d %H:%M')} · Popfun Discover", "lines": 1},
    })
    return {
        "config": {"wide_screen_mode": True},
        "header": {"title": {"tag": "plain_text", "content": "📋 Popfun Error 日志", "lines": 1}, "template": "red"},
        "elements": elements,
    }


def run_popfun_log_push(visible: bool = False) -> None:
    """定时/手动：登录 Popfun → 抓 error 日志 → 推到 FEISHU_POPFUN_LOG_CHAT_ID。
    visible=True 时用有界面浏览器，便于观察抓取过程。"""
    chat_id = (FEISHU_POPFUN_LOG_CHAT_ID or "").strip()
    if not chat_id:
        logger.debug("FEISHU_POPFUN_LOG_CHAT_ID not set, skip Popfun log push")
        return
    if not POPFUN_LOG_PASSWORD:
        logger.warning("POPFUN_LOG_PASSWORD not set, skip Popfun log push")
        send_text_message(chat_id, "Popfun 日志：未配置 POPFUN_LOG_PASSWORD（.env），无法登录。")
        return

    def _run():
        return _login_and_fetch_error_logs(headless=not visible)

    try:
        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(_run)
            rows = future.result(timeout=180)
        if not rows:
            send_text_message(
                chat_id,
                f"Popfun 日志：近 15 分钟未抓取到 error 日志（或页面结构变化）。\n🕐 {datetime.now().strftime('%Y-%m-%d %H:%M')}",
            )
            logger.info("Popfun log: 0 error rows, sent heartbeat to %s", chat_id[:20])
            return
        card = _build_log_card(rows)
        send_card_message(chat_id, card)
        logger.info("Popfun log: pushed %d error rows to %s", len(rows), chat_id[:20])
    except Exception as e:
        logger.exception("Popfun log push error: %s", e)
        send_text_message(
            chat_id,
            f"Popfun 日志抓取失败：{e}\n🕐 {datetime.now().strftime('%Y-%m-%d %H:%M')}",
        )


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    run_popfun_log_push(visible=True)
