"""
MEXC 下架公告抓取：使用 Playwright 打开下架公告列表页，抓取指定页数的条目（标题、日期、链接、摘要）。
支持定时任务：结果推送到飞书群，页数由配置 MEXC_DELISTINGS_PAGES 控制。
"""
import json
import logging
import re
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass
from datetime import datetime

from config import FEISHU_MEXC_DELISTINGS_CHAT_ID, MEXC_DELISTINGS_PAGES
from lark_client import send_card_message, send_text_message

logger = logging.getLogger(__name__)

BASE_URL = "https://www.mexc.com/zh-MY/announcements/delistings"
_DATE_LINE_RE = re.compile(r"^(\d{4}年\d{1,2}月\d{1,2}日|\d{1,2}\s*天前|\d+\s*小时前|\d+\s*分钟前)", re.MULTILINE)


@dataclass
class DelistingItem:
    """单条下架公告."""
    title: str
    date: str
    url: str
    snippet: str


def _parse_page_text(text: str, page_url: str) -> list[dict]:
    """从整页正文解析公告块：找日期行，其前一行作标题，后续作摘要。"""
    items = []
    text = (text or "").strip()
    if not text:
        return items
    lines = [ln.strip() for ln in text.split("\n") if ln.strip()]
    i = 0
    while i < len(lines):
        ln = lines[i]
        if not _DATE_LINE_RE.match(ln):
            i += 1
            continue
        date = ln[:80]
        title = ""
        if i > 0:
            prev = lines[i - 1]
            if (
                len(prev) >= 4
                and not prev.startswith("#")
                and not prev.startswith("http")
                and ("下架" in prev or "通知" in prev or "公告" in prev or "合约" in prev or "永续" in prev or "Meme" in prev or "闪兑" in prev or "代币" in prev)
            ):
                title = prev[:300]
        if not title:
            i += 1
            continue
        snippet_lines = [l for l in lines[i + 1 : i + 5] if l and not l.startswith("[#") and not l.startswith("http") and len(l) > 10][:3]
        snippet = " ".join(snippet_lines)[:400] if snippet_lines else ""
        items.append({"title": title, "date": date, "url": page_url, "snippet": snippet})
        i += 1
    return items


def _extract_page_items(page, page_url: str) -> list[dict]:
    """获取当前页面正文并按 ## 标题 + 日期 解析为公告列表。"""
    page.wait_for_selector("body", timeout=15000)
    page.wait_for_timeout(2500)
    try:
        body = page.locator("body")
        text = body.inner_text()
    except Exception as e:
        logger.warning("body inner_text failed: %s", e)
        return []
    return _parse_page_text(text, page_url)


def fetch_mexc_delistings(max_pages: int = 2, headless: bool = True) -> list[DelistingItem]:
    """
    抓取 MEXC 下架公告列表，最多 max_pages 页。
    :return: 按页顺序的 DelistingItem 列表
    """
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        raise RuntimeError("请安装 playwright: pip install playwright && playwright install chromium")

    all_items: list[DelistingItem] = []
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=headless)
        try:
            page = browser.new_page()
            page.set_default_timeout(20000)
            for page_num in range(1, max_pages + 1):
                url = BASE_URL if page_num == 1 else f"{BASE_URL}?page={page_num}"
                logger.info("mexc_delistings: fetching page %d url=%s", page_num, url)
                page.goto(url, wait_until="domcontentloaded", timeout=20000)
                raw = _extract_page_items(page, url)
                for r in raw:
                    all_items.append(
                        DelistingItem(
                            title=r.get("title") or "(无标题)",
                            date=r.get("date") or "",
                            url=r.get("url") or "",
                            snippet=r.get("snippet") or "",
                        )
                    )
                logger.info("mexc_delistings: page %d got %d items, total %d", page_num, len(raw), len(all_items))
        finally:
            browser.close()
    seen = set()
    deduped = []
    for x in all_items:
        key = (x.title.strip(), x.date.strip())
        if key in seen:
            continue
        seen.add(key)
        deduped.append(x)
    if len(deduped) < len(all_items):
        logger.info("mexc_delistings: deduped %d -> %d items", len(all_items), len(deduped))
    return deduped


def _build_delistings_card(items: list[DelistingItem], pages: int) -> dict:
    """构建飞书卡片：MEXC 下架公告列表。"""
    elements = [
        {
            "tag": "div",
            "text": {
                "tag": "lark_md",
                "content": f"抓取 **{pages} 页** 去重后共 **{len(items)}** 条，仅展示前 30 条。",
            },
        },
        {"tag": "hr"},
    ]
    for i, x in enumerate(items[:30], 1):
        snippet = (x.snippet or "").strip()
        if len(snippet) > 120:
            snippet = snippet[:120] + "..."
        line = f"**{i}. {x.title[:80]}**\n日期：{x.date or '-'}\n{snippet or '-'}"
        elements.append({
            "tag": "div",
            "text": {"tag": "lark_md", "content": line},
        })
    if len(items) > 30:
        elements.append({
            "tag": "div",
            "text": {"tag": "plain_text", "content": f"... 共 {len(items)} 条", "lines": 1},
        })
    elements.append({"tag": "hr"})
    elements.append({
        "tag": "div",
        "text": {
            "tag": "plain_text",
            "content": f"🕐 {datetime.now().strftime('%Y-%m-%d %H:%M')} · MEXC 下架公告 · 抓取 {pages} 页",
            "lines": 1,
        },
    })
    return {
        "config": {"wide_screen_mode": True},
        "header": {
            "title": {"tag": "plain_text", "content": "📋 MEXC 下架公告", "lines": 1},
            "template": "blue",
        },
        "elements": elements,
    }


def run_mexc_delistings_push() -> None:
    """定时任务：抓取 MEXC 下架公告（页数取配置 MEXC_DELISTINGS_PAGES），推送到 FEISHU_MEXC_DELISTINGS_CHAT_ID。
    抓取在单独线程中执行，避免 Playwright Sync API 与 asyncio 冲突。
    """
    chat_id = (FEISHU_MEXC_DELISTINGS_CHAT_ID or "").strip()
    if not chat_id:
        logger.debug("FEISHU_MEXC_DELISTINGS_CHAT_ID not set, skip MEXC delistings push")
        return
    pages = MEXC_DELISTINGS_PAGES
    try:
        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(fetch_mexc_delistings, max_pages=pages, headless=True)
            items = future.result(timeout=120)
        if not items:
            send_text_message(
                chat_id,
                f"MEXC 下架公告：本周期抓取 {pages} 页，未解析到条目。\n🕐 {datetime.now().strftime('%Y-%m-%d %H:%M')}",
            )
            logger.info("MEXC delistings: 0 items, sent heartbeat to %s", chat_id[:20])
            return
        card = _build_delistings_card(items, pages)
        send_card_message(chat_id, card)
        logger.info("MEXC delistings: pushed %d items (pages=%s) to %s", len(items), pages, chat_id[:20])
    except Exception as e:
        logger.exception("MEXC delistings push error: %s", e)
        send_text_message(
            chat_id,
            f"MEXC 下架公告抓取失败：{e}\n🕐 {datetime.now().strftime('%Y-%m-%d %H:%M')}",
        )


def run(max_pages: int | None = None, headless: bool = True, output_json: str | None = None) -> list[DelistingItem]:
    """
    执行抓取并可选写入 JSON。
    :param max_pages: 抓取页数，默认使用配置 MEXC_DELISTINGS_PAGES
    :param output_json: 若提供则把结果写入该路径
    """
    pages = max_pages if max_pages is not None else MEXC_DELISTINGS_PAGES
    items = fetch_mexc_delistings(max_pages=pages, headless=headless)
    if output_json:
        with open(output_json, "w", encoding="utf-8") as f:
            json.dump([asdict(x) for x in items], f, ensure_ascii=False, indent=2)
        logger.info("mexc_delistings: wrote %d items to %s", len(items), output_json)
    return items


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    result = run(headless=True, output_json="tasks/mexc_delistings_2pages.json")
    print(f"共抓取 {len(result)} 条（页数={MEXC_DELISTINGS_PAGES}）")
    for i, x in enumerate(result[:15], 1):
        print(f"{i}. {x.title[:60]}... | {x.date} | {x.url[:50]}...")
    if len(result) > 15:
        print(f"... 其余 {len(result) - 15} 条见 tasks/mexc_delistings_2pages.json")
