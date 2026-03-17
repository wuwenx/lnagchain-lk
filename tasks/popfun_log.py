"""
Popfun 日志平台：Playwright 登录 → 打开 Discover 页 → 抓取 error 相关日志 → 推送到飞书。
配置：POPFUN_LOG_BASE_URL / POPFUN_LOG_USERNAME / POPFUN_LOG_PASSWORD（.env）/ FEISHU_POPFUN_LOG_CHAT_ID
"""
import logging
import re
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path

from config import (
    FEISHU_POPFUN_LOG_CHAT_ID,
    OPENAI_API_BASE,
    OPENAI_API_KEY,
    OPENAI_MODEL,
    POPFUN_LOG_BASE_URL,
    POPFUN_LOG_PASSWORD,
    POPFUN_LOG_USERNAME,
)
from langchain_openai import ChatOpenAI
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
            # 等待实际要用的表格和滚动容器出现即可，避免固定长等
            try:
                page.wait_for_selector("[data-test-subj='euiDataGridBody'], .euiDataGrid__virtualized", timeout=15000)
            except Exception:
                pass
            page.wait_for_timeout(500)

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
                """只保留 error 行：path 含 -error.log 或 message 含 error（满足其一即保留），否则视为非 error 丢弃。"""
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
                # 实际带滚动条的是 .euiDataGrid__virtualized（overflow:auto），在其上小步 scrollTop 避免一次拉到底
                scroll_container_sel = ".euiDataGrid__virtualized"
                scroll_container = page.locator(scroll_container_sel).first
                if grid_body.count() == 0:
                    debug_lines.append("euiDataGridBody 未找到")
                elif scroll_container.count() == 0:
                    debug_lines.append("euiDataGrid__virtualized 未找到")
                else:
                    # 分页流程：第1页 → 当前页内下拉取数据 → 点「下一页」→ 取下一页数据 → 直到无下一页。去重 key=(ts, path, message前80字)。
                    seen_keys: set[tuple] = set()
                    raw_total = 0  # 去重前解析到的总条数（含重复）
                    pagination_next_sel = '[data-test-subj="pagination-button-next"]'
                    pagination_first_sel = '[data-test-subj="pagination-button-0"]'
                    max_pages = 25
                    only_first_page = False  # 抓全部页；改为 True 则只抓第 1 页
                    # 每步滚动高度（约 3 行），步长大一点滚动更快
                    scroll_step_px = 300
                    # 先把「Rows per page」改成 500，减少分页次数
                    try:
                        popover_btn = page.locator("[data-test-subj='tablePaginationPopoverButton']").first
                        if popover_btn.count() > 0:
                            popover_btn.click()
                            page.wait_for_timeout(500)
                            option_500 = page.get_by_role("button", name="500 rows").or_(page.locator("button:has-text('500 rows')")).first
                            if option_500.count() > 0:
                                option_500.click()
                                page.wait_for_timeout(2500)
                                logger.info("popfun_log: 已切换为每页 500 条")
                            else:
                                page.keyboard.press("Escape")
                        else:
                            page.wait_for_timeout(300)
                    except Exception as e:
                        logger.debug("popfun_log: 切换每页条数失败 %s，继续按当前设置抓取", e)
                        page.wait_for_timeout(300)
                    # 确保从第 1 页开始：点一下「1」按钮（若已在第1页则为 disabled，点击无妨）
                    try:
                        page.locator(pagination_first_sel).first.click()
                        page.wait_for_timeout(300)
                    except Exception:
                        pass
                    for page_num in range(1, max_pages + 1):
                        # 每页开始时：页面回顶，并把真正的滚动容器 .euiDataGrid__virtualized 的 scrollTop 置 0
                        page.evaluate("window.scrollTo(0, 0)")
                        page.wait_for_timeout(100)
                        scroll_container.evaluate("el => { el.scrollTop = 0; }")
                        page.wait_for_timeout(150)
                        grid_body.first.click()
                        page.wait_for_timeout(100)
                        if page_num == 1:
                            page.keyboard.press("Home")
                            page.wait_for_timeout(80)
                        # 当前页内：在 .euiDataGrid__virtualized 上小步增加 scrollTop，边滚边采（不触发分页）
                        no_new_count = 0
                        for step in range(120):
                            eui_rows = page.locator(primary_sel)
                            ne = eui_rows.count()
                            before = len(seen_keys)
                            for i in range(ne):
                                try:
                                    raw = _parse_eui_row_raw(eui_rows.nth(i))
                                    if raw.get("ts") or raw.get("path") or raw.get("message"):
                                        raw_total += 1
                                    key = (raw["ts"], raw["path"], (raw["message"] or "")[:80])
                                    if key in seen_keys:
                                        continue
                                    seen_keys.add(key)
                                    p = _parse_eui_row(eui_rows.nth(i))
                                    if p:
                                        rows.append(p)
                                except Exception:
                                    pass
                            # 小步下滚：只改 scrollTop += scroll_step_px，不拉到底
                            scroll_container.evaluate(
                                f"el => {{ const max = el.scrollHeight - el.clientHeight; if (max <= 0) return; el.scrollTop = Math.min(el.scrollTop + {scroll_step_px}, max); }}"
                            )
                            page.wait_for_timeout(400)
                            if len(seen_keys) == before:
                                no_new_count += 1
                                if no_new_count >= 6:
                                    break
                            else:
                                no_new_count = 0
                            # 若已滚到底则提前结束
                            at_bottom = scroll_container.evaluate(
                                "el => el.scrollHeight - el.clientHeight <= el.scrollTop + 2"
                            )
                            if at_bottom:
                                break
                        # 再采一次当前屏（兜底最后一屏）
                        eui_rows = page.locator(primary_sel)
                        for i in range(eui_rows.count()):
                            try:
                                raw = _parse_eui_row_raw(eui_rows.nth(i))
                                if raw.get("ts") or raw.get("path") or raw.get("message"):
                                    raw_total += 1
                                key = (raw["ts"], raw["path"], (raw["message"] or "")[:80])
                                if key not in seen_keys:
                                    seen_keys.add(key)
                                    p = _parse_eui_row(eui_rows.nth(i))
                                    if p:
                                        rows.append(p)
                            except Exception:
                                pass
                        ne = page.locator(primary_sel).count()
                        logger.info("popfun_log: 第 %d 页采集结束, 本页可见 %d 行, 累计去重 %d 行, error %d 条", page_num, ne, len(seen_keys), len(rows))
                        if only_first_page:
                            break
                        # 点「下一页」；若为 button 且 disabled 说明已是最后一页
                        next_el = page.locator(pagination_next_sel).first
                        if next_el.count() == 0:
                            break
                        is_disabled = next_el.evaluate("el => el.tagName === 'BUTTON' && el.hasAttribute('disabled')")
                        if is_disabled:
                            break
                        next_el.click()
                        page.wait_for_timeout(2500)
                        # 下一页：把 .euiDataGrid__virtualized 滚回顶部
                        scroll_container.evaluate("el => { el.scrollTop = 0; }")
                        page.wait_for_timeout(300)
                    captured_dedup = len(seen_keys)
                    filtered_out = captured_dedup - len(rows)
                    pushed = len(rows)
                    logger.info("popfun_log: 分页采集结束 | 抓取(去重前) %d 条, 抓取(去重后) %d 条, 过滤(非error) %d 条, 推送 %d 条", raw_total, captured_dedup, filtered_out, pushed)
                    # txt 仍存一份当前可见的原始 HTML（最后一屏）
                    raw_html = grid_body.first.evaluate("el => el.innerHTML")
                    debug_lines.append("=== 最原始数据：euiDataGridBody.innerHTML（最后一屏，未解析）===")
                    debug_lines.append(f"抓取(去重前): {raw_total} 条, 抓取(去重后): {captured_dedup} 条, 过滤(非error): {filtered_out} 条, 推送: {pushed} 条\n")
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

        finally:
            browser.close()

    return rows


def _analyze_error_logs_with_llm(rows: list[dict], max_rows: int = 300, max_chars: int = 200000) -> str:
    """将 error 日志交给大模型做全面分析，返回分析报告文本。未配置 API 或失败时返回空字符串。
    默认最多取 300 条、约 200KB 字符，避免输入过大导致模型崩溃。"""
    if not rows or not OPENAI_API_KEY:
        return ""
    lines: list[str] = []
    total_chars = 0
    for i, r in enumerate(rows[:max_rows], 1):
        ts = (r.get("ts") or "").strip()
        host = (r.get("host") or "").strip()
        path = (r.get("path") or "").strip()
        msg = (r.get("message") or "").strip() or "-"
        line = f"[{i}] {ts} | host:{host} | path:{path} | message:{msg}"
        lines.append(line)
        total_chars += len(line) + 1
        if total_chars >= max_chars:
            break
    log_sample = "\n".join(lines)
    total = len(rows)
    sample_n = len(lines)
    if sample_n >= total:
        data_desc = f"**全量共 {total} 条**，已全部提供，请基于全量数据做分析，输出时说明「基于全量 {total} 条」即可。"
    else:
        data_desc = f"**总条数 {total} 条**，这里提供前 {sample_n} 条作为样本（约 {100 * sample_n // max(total, 1)}%），分析时请说明「基于前 {sample_n} 条样本（共 {total} 条）」."
    prompt = f"""你是一名运维/后端专家。下面是一批来自 Popfun 日志平台近 15 分钟的 error 日志（每条包含时间、主机、路径、消息）。{data_desc}

请做**全面 error 日志分析**，按以下结构用中文输出（无需代码块、无需重复原始日志，可充分展开、不限制字数）：

1. **分类统计**：按错误类型、模块或路径归纳条数与占比（如：某类错误 N 条、占比 X%）。
2. **高频错误**：出现次数最多或最典型的几条，写出典型 message 摘要。
3. **可能根因**：结合 path、host、message 推断可能原因（网络、配置、依赖、业务逻辑等）。
4. **处理建议**：按优先级给出建议操作（立即处理 / 观察 / 优化等）。
5. **总结**：一两句话概括当前整体健康度与最需关注的点。

日志样本：
---
{log_sample}
---"""

    try:
        llm = ChatOpenAI(
            model=OPENAI_MODEL or "gpt-4o-mini",
            api_key=OPENAI_API_KEY,
            base_url=OPENAI_API_BASE or None,
            temperature=0,
        )
        resp = llm.invoke(prompt)
        return (resp.content or "").strip()
    except Exception as e:
        logger.warning("popfun_log LLM 分析失败: %s", e)
        return ""


def _build_log_card(rows: list[dict], analysis: str = "") -> dict:
    """飞书卡片：上方仅展示 error 总数，主体为大模型分析结果。"""
    elements: list[dict] = []
    n = len(rows)
    elements.append({
        "tag": "div",
        "text": {"tag": "lark_md", "content": f"**Popfun 日志平台** · 近 15 分钟 error 日志\n**错误条数：{n} 条**", "lines": 2},
    })
    elements.append({"tag": "hr"})
    if analysis:
        # 演示阶段：不截断，展示完整 AI 分析（飞书卡片单条有约 30KB 上限，超长时需拆多块或后端需再处理）
        elements.append({
            "tag": "div",
            "text": {"tag": "lark_md", "content": f"**🤖 AI 分析**\n{analysis}", "lines": 100},
        })
    else:
        elements.append({
            "tag": "div",
            "text": {"tag": "plain_text", "content": "（未生成 AI 分析，请检查 OPENAI_API_KEY 或稍后重试）", "lines": 1},
        })
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
            rows = future.result(timeout=900)
        if not rows:
            send_text_message(
                chat_id,
                f"Popfun 日志：近 15 分钟未抓取到 error 日志（或页面结构变化）。\n🕐 {datetime.now().strftime('%Y-%m-%d %H:%M')}",
            )
            logger.info("Popfun log: 0 error rows, sent heartbeat to %s", chat_id[:20])
            return
        analysis = _analyze_error_logs_with_llm(rows)
        if analysis:
            logger.info("popfun_log: AI 分析完成，%d 字", len(analysis))
        card = _build_log_card(rows, analysis=analysis)
        send_card_message(chat_id, card)
        logger.info("Popfun log: pushed %d error rows (with AI analysis) to %s", len(rows), chat_id[:20])
    except Exception as e:
        logger.exception("Popfun log push error: %s", e)
        send_text_message(
            chat_id,
            f"Popfun 日志抓取失败：{e}\n🕐 {datetime.now().strftime('%Y-%m-%d %H:%M')}",
        )


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    # 直接运行脚本时使用有界面浏览器，便于观察抓取过程（登录 → Discover → PageDown 翻页）
    run_popfun_log_push(visible=True)
