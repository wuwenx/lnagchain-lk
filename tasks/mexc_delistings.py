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

BASE_URL_DELISTINGS = "https://www.mexc.com/zh-MY/announcements/delistings"
BASE_URL_NEW_LISTINGS = "https://www.mexc.com/zh-MY/announcements/new-listings"
_DATE_LINE_RE = re.compile(r"^(\d{4}年\d{1,2}月\d{1,2}日|\d{1,2}\s*天前|\d+\s*小时前|\d+\s*分钟前)", re.MULTILINE)
_DATE_LINE_RE_NEW = re.compile(r"^((?:大约\s*)?\d{1,2}\s*天前|(?:大约\s*)?\d+\s*小时前|\d+\s*分钟前|\d{4}年\d{1,2}月\d{1,2}日)", re.MULTILINE)
_HEADING_RE = re.compile(r"^##\s+(.+)$")


@dataclass
class DelistingItem:
    """单条公告（下架或上架通用）."""
    title: str
    date: str
    url: str
    snippet: str


def _parse_page_text_new_listings(text: str, page_url: str) -> list[dict]:
    """上架页解析：## 标题 + 下一行日期（大约 N 小时前 / N 分钟前 / N 天前）。"""
    items = []
    text = (text or "").strip()
    if not text:
        return items
    lines = [ln.strip() for ln in text.split("\n") if ln.strip()]
    i = 0
    while i < len(lines):
        m = _HEADING_RE.match(lines[i])
        if not m:
            i += 1
            continue
        title = m.group(1).strip()[:300]
        i += 1
        date = ""
        if i < len(lines) and _DATE_LINE_RE_NEW.match(lines[i]):
            date = lines[i][:80]
            i += 1
        snippet_lines = [l for l in lines[i : i + 4] if l and not l.startswith("[#") and not l.startswith("http") and len(l) > 10][:3]
        snippet = " ".join(snippet_lines)[:400] if snippet_lines else ""
        items.append({"title": title, "date": date, "url": page_url, "snippet": snippet})
        i += 1
    return items


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


def _extract_page_items(page, page_url: str, parser=_parse_page_text) -> list[dict]:
    """获取当前页面正文并用指定 parser 解析为公告列表。"""
    page.wait_for_selector("body", timeout=15000)
    page.wait_for_timeout(2500)
    try:
        body = page.locator("body")
        text = body.inner_text()
    except Exception as e:
        logger.warning("body inner_text failed: %s", e)
        return []
    return parser(text, page_url)


def _fetch_mexc_announcements_one_type(
    base_url: str,
    max_pages: int,
    headless: bool,
    parser,
    log_label: str,
) -> list[DelistingItem]:
    """通用：抓取某一类 MEXC 公告（上架或下架）。"""
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
                url = base_url if page_num == 1 else f"{base_url}?page={page_num}"
                logger.info("mexc_announcements: %s page %d url=%s", log_label, page_num, url)
                page.goto(url, wait_until="domcontentloaded", timeout=20000)
                raw = _extract_page_items(page, url, parser=parser)
                for r in raw:
                    all_items.append(
                        DelistingItem(
                            title=r.get("title") or "(无标题)",
                            date=r.get("date") or "",
                            url=r.get("url") or "",
                            snippet=r.get("snippet") or "",
                        )
                    )
                logger.info("mexc_announcements: %s page %d got %d items, total %d", log_label, page_num, len(raw), len(all_items))
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
        logger.info("mexc_announcements: %s deduped %d -> %d", log_label, len(all_items), len(deduped))
    return deduped


def fetch_mexc_delistings(max_pages: int = 2, headless: bool = True) -> list[DelistingItem]:
    """抓取 MEXC 下架公告列表。"""
    return _fetch_mexc_announcements_one_type(
        BASE_URL_DELISTINGS, max_pages, headless, _parse_page_text, "下架",
    )


def fetch_mexc_new_listings(max_pages: int = 2, headless: bool = True) -> list[DelistingItem]:
    """抓取 MEXC 上架公告列表（new-listings）。"""
    return _fetch_mexc_announcements_one_type(
        BASE_URL_NEW_LISTINGS, max_pages, headless, _parse_page_text_new_listings, "上架",
    )


def _build_delistings_card(items: list[DelistingItem], pages: int) -> dict:
    """构建飞书卡片：仅 MEXC 下架（兼容旧用法）。"""
    return _build_mexc_two_sections_card([], items, pages, only_delistings=True)


def _build_mexc_two_sections_card(
    new_listings: list[DelistingItem],
    delistings: list[DelistingItem],
    pages: int,
    only_delistings: bool = False,
) -> dict:
    """构建飞书卡片：上架 + 下架 两块。若 only_delistings 则仅下架一块。"""
    elements = []
    if only_delistings:
        elements.append({
            "tag": "div",
            "text": {"tag": "lark_md", "content": f"抓取 **{pages} 页** 去重后共 **{len(delistings)}** 条，仅展示前 30 条。", "lines": 1},
        })
    else:
        elements.append({
            "tag": "div",
            "text": {"tag": "lark_md", "content": f"抓取 **上架** 与 **下架** 各 **{pages} 页**，仅展示前 15 条/类。", "lines": 1},
        })
    elements.append({"tag": "hr"})

    if not only_delistings and new_listings:
        elements.append({"tag": "div", "text": {"tag": "lark_md", "content": "**新币上线**", "lines": 1}})
        for i, x in enumerate(new_listings[:15], 1):
            snippet = (x.snippet or "").strip()
            if len(snippet) > 80:
                snippet = snippet[:80] + "..."
            elements.append({
                "tag": "div",
                "text": {"tag": "lark_md", "content": f"{i}. **{x.title[:70]}**\n日期：{x.date or '-'}\n{snippet or '-'}"},
            })
        elements.append({"tag": "hr"})

    elements.append({"tag": "div", "text": {"tag": "lark_md", "content": "**币种下架**", "lines": 1}})
    del_display = delistings[:30] if only_delistings else delistings[:15]
    for i, x in enumerate(del_display, 1):
        snippet = (x.snippet or "").strip()
        if len(snippet) > 80:
            snippet = snippet[:80] + "..."
        elements.append({
            "tag": "div",
            "text": {"tag": "lark_md", "content": f"{i}. **{x.title[:70]}**\n日期：{x.date or '-'}\n{snippet or '-'}"},
        })
    if (only_delistings and len(delistings) > 30) or (not only_delistings and len(delistings) > 15):
        elements.append({"tag": "div", "text": {"tag": "plain_text", "content": f"... 下架共 {len(delistings)} 条", "lines": 1}})
    elements.append({"tag": "hr"})
    if only_delistings:
        elements.append({
            "tag": "div",
            "text": {"tag": "plain_text", "content": f"🕐 {datetime.now().strftime('%Y-%m-%d %H:%M')} · MEXC 下架公告 · 抓取 {pages} 页", "lines": 1},
        })
    else:
        elements.append({
            "tag": "div",
            "text": {"tag": "plain_text", "content": f"🕐 {datetime.now().strftime('%Y-%m-%d %H:%M')} · MEXC 公告 · 上架 {len(new_listings)} 条 / 下架 {len(delistings)} 条", "lines": 1},
        })
    return {
        "config": {"wide_screen_mode": True},
        "header": {
            "title": {"tag": "plain_text", "content": "📋 MEXC 下架公告" if only_delistings else "📋 MEXC 公告（上架 + 下架）", "lines": 1},
            "template": "blue",
        },
        "elements": elements,
    }


def run_mexc_delistings_push() -> None:
    """定时任务：抓取 MEXC 上架 + 下架公告，推送到 FEISHU_MEXC_DELISTINGS_CHAT_ID。在单独线程中执行。"""
    chat_id = (FEISHU_MEXC_DELISTINGS_CHAT_ID or "").strip()
    if not chat_id:
        logger.debug("FEISHU_MEXC_DELISTINGS_CHAT_ID not set, skip MEXC announcements push")
        return
    pages = MEXC_DELISTINGS_PAGES

    def _fetch_both():
        new_l = fetch_mexc_new_listings(max_pages=pages, headless=True)
        del_l = fetch_mexc_delistings(max_pages=pages, headless=True)
        return new_l, del_l

    try:
        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(_fetch_both)
            new_listings, delistings = future.result(timeout=180)
        if not new_listings and not delistings:
            send_text_message(
                chat_id,
                f"MEXC 公告：本周期抓取上架/下架各 {pages} 页，未解析到条目。\n🕐 {datetime.now().strftime('%Y-%m-%d %H:%M')}",
            )
            logger.info("MEXC announcements: 0 items, sent heartbeat to %s", chat_id[:20])
            return
        card = _build_mexc_two_sections_card(new_listings, delistings, pages, only_delistings=False)
        send_card_message(chat_id, card)
        logger.info(
            "MEXC announcements: pushed 上架 %d / 下架 %d (pages=%s) to %s",
            len(new_listings), len(delistings), pages, chat_id[:20],
        )
    except Exception as e:
        logger.exception("MEXC announcements push error: %s", e)
        send_text_message(
            chat_id,
            f"MEXC 公告抓取失败：{e}\n🕐 {datetime.now().strftime('%Y-%m-%d %H:%M')}",
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
