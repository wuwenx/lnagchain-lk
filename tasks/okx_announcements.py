"""
OKX 公告抓取：请求帮助中心 section 页，从 SSR 内嵌的 appState JSON 解析公告列表。
同时抓取「新币种上线」与「币对下线」两类，推送到飞书（上币 + 下币）。
"""
import json
import logging
import re
from dataclasses import asdict, dataclass
from datetime import datetime

import requests

from config import FEISHU_OKX_ANNOUNCEMENTS_CHAT_ID, OKX_ANNOUNCEMENTS_PAGES
from lark_client import send_card_message, send_text_message

logger = logging.getLogger(__name__)

# 简体中文帮助中心 base，section 后缀为 announcements-new-listings / announcements-delistings
OKX_HELP_BASE = "https://www.okx.com/zh-hans/help/section"
# 单条公告详情（相对路径 /zh-hans/help/{slug}）
OKX_HELP_ARTICLE_BASE = "https://www.okx.com/zh-hans/help"
# 每页条数（OKX 默认 15）
PAGE_SIZE = 15

SECTION_NEW_LISTINGS = "announcements-new-listings"
SECTION_DELISTINGS = "announcements-delistings"


@dataclass
class OkxAnnouncementItem:
    """单条公告."""
    title: str
    date: str
    url: str
    slug: str
    section: str  # new_listings | delistings


def _format_publish_date(ts_ms: int | None) -> str:
    if ts_ms is None:
        return ""
    try:
        dt = datetime.fromtimestamp(ts_ms / 1000.0)
        return dt.strftime("%Y-%m-%d")
    except Exception:
        return ""


def _extract_app_state(html: str) -> dict | None:
    """从 OKX 帮助中心 HTML 中提取 __app_data_for_ssr__ 的 JSON。"""
    if not html:
        return None
    # 页面内 <script ... id="appState">...JSON...</script>
    m = re.search(r'id="appState"[^>]*>\s*([\s\S]*?)\s*</script>', html)
    if not m:
        return None
    try:
        return json.loads(m.group(1))
    except json.JSONDecodeError as e:
        logger.warning("okx_announcements parse appState JSON failed: %s", e)
        return None


def _fetch_section_page(section_slug: str, page_num: int) -> list[dict]:
    """请求某一 section 的某一页，返回该页 articleList.list。"""
    if page_num <= 1:
        url = f"{OKX_HELP_BASE}/{section_slug}"
    else:
        url = f"{OKX_HELP_BASE}/{section_slug}/page/{page_num}"
    try:
        r = requests.get(url, timeout=15)
        r.raise_for_status()
        data = _extract_app_state(r.text)
    except Exception as e:
        logger.warning("okx_announcements fetch %s page %s failed: %s", section_slug, page_num, e)
        return []
    if not data:
        return []
    try:
        il = (
            (data.get("appContext") or {})
            .get("initialProps") or {}
        ).get("sectionData") or {}
        al = il.get("articleList") or {}
        return al.get("list") or []
    except Exception as e:
        logger.warning("okx_announcements extract articleList failed: %s", e)
        return []


def fetch_okx_announcements(
    max_pages: int = 2,
) -> tuple[list[OkxAnnouncementItem], list[OkxAnnouncementItem]]:
    """
    抓取 OKX 新币上线 + 币对下线，各 max_pages 页。
    :return: (new_listings_items, delistings_items)
    """
    new_listings: list[OkxAnnouncementItem] = []
    delistings: list[OkxAnnouncementItem] = []

    for page_num in range(1, max_pages + 1):
        for section_slug, out_list, section_label in [
            (SECTION_NEW_LISTINGS, new_listings, "new_listings"),
            (SECTION_DELISTINGS, delistings, "delistings"),
        ]:
            articles = _fetch_section_page(section_slug, page_num)
            logger.info("okx_announcements: %s page %d got %d items", section_slug, page_num, len(articles))
            for a in articles:
                slug = (a.get("slug") or a.get("id") or "").strip()
                title = (a.get("title") or "").strip() or "(无标题)"
                ts = a.get("publishTime")
                date = _format_publish_date(ts) if isinstance(ts, (int, float)) else ""
                url = f"{OKX_HELP_ARTICLE_BASE}/{slug}" if slug else OKX_HELP_ARTICLE_BASE
                out_list.append(
                    OkxAnnouncementItem(
                        title=title,
                        date=date,
                        url=url,
                        slug=slug,
                        section=section_label,
                    )
                )
    # 去重（同 slug 只保留一条）
    def dedup(items: list[OkxAnnouncementItem]) -> list[OkxAnnouncementItem]:
        seen = set()
        out = []
        for x in items:
            if x.slug in seen:
                continue
            seen.add(x.slug)
            out.append(x)
        return out

    return dedup(new_listings), dedup(delistings)


def _build_okx_card(
    new_listings: list[OkxAnnouncementItem],
    delistings: list[OkxAnnouncementItem],
    pages: int,
) -> dict:
    """构建飞书卡片：上币 + 下币 两块。"""
    elements = [
        {
            "tag": "div",
            "text": {
                "tag": "lark_md",
                "content": f"抓取 **新币上线** 与 **币对下线** 各 **{pages} 页**，仅展示前 15 条/类。",
            },
        },
        {"tag": "hr"},
        {
            "tag": "div",
            "text": {"tag": "lark_md", "content": "**新币种上线**", "lines": 1},
        },
    ]
    for i, x in enumerate(new_listings[:15], 1):
        link = f"[详情]({x.url})" if x.url else ""
        elements.append({
            "tag": "div",
            "text": {
                "tag": "lark_md",
                "content": f"{i}. {x.title[:80]}\n日期：{x.date or '-'} · {link}",
            },
        })
    elements.append({"tag": "hr"})
    elements.append({
        "tag": "div",
        "text": {"tag": "lark_md", "content": "**币对下线**", "lines": 1},
    })
    for i, x in enumerate(delistings[:15], 1):
        link = f"[详情]({x.url})" if x.url else ""
        elements.append({
            "tag": "div",
            "text": {
                "tag": "lark_md",
                "content": f"{i}. {x.title[:80]}\n日期：{x.date or '-'} · {link}",
            },
        })
    elements.append({"tag": "hr"})
    elements.append({
        "tag": "div",
        "text": {
            "tag": "plain_text",
            "content": f"🕐 {datetime.now().strftime('%Y-%m-%d %H:%M')} · OKX 公告 · 上币 {len(new_listings)} 条 / 下币 {len(delistings)} 条",
            "lines": 1,
        },
    })
    return {
        "config": {"wide_screen_mode": True},
        "header": {
            "title": {"tag": "plain_text", "content": "📋 OKX 公告（上币 + 下币）", "lines": 1},
            "template": "blue",
        },
        "elements": elements,
    }


def run_okx_announcements_push() -> None:
    """定时任务：抓取 OKX 上币 + 下币，推送到 FEISHU_OKX_ANNOUNCEMENTS_CHAT_ID。"""
    chat_id = (FEISHU_OKX_ANNOUNCEMENTS_CHAT_ID or "").strip()
    if not chat_id:
        logger.debug("FEISHU_OKX_ANNOUNCEMENTS_CHAT_ID not set, skip OKX announcements push")
        return
    pages = OKX_ANNOUNCEMENTS_PAGES
    try:
        new_listings, delistings = fetch_okx_announcements(max_pages=pages)
        if not new_listings and not delistings:
            send_text_message(
                chat_id,
                f"OKX 公告：本周期抓取上币/下币各 {pages} 页，未获取到条目。\n🕐 {datetime.now().strftime('%Y-%m-%d %H:%M')}",
            )
            logger.info("OKX announcements: 0 items, sent heartbeat to %s", chat_id[:20])
            return
        card = _build_okx_card(new_listings, delistings, pages)
        send_card_message(chat_id, card)
        logger.info(
            "OKX announcements: pushed 上币 %d / 下币 %d to %s",
            len(new_listings),
            len(delistings),
            chat_id[:20],
        )
    except Exception as e:
        logger.exception("OKX announcements push error: %s", e)
        send_text_message(
            chat_id,
            f"OKX 公告抓取失败：{e}\n🕐 {datetime.now().strftime('%Y-%m-%d %H:%M')}",
        )


def run(
    max_pages: int | None = None,
    output_json: str | None = None,
) -> tuple[list[OkxAnnouncementItem], list[OkxAnnouncementItem]]:
    """
    执行抓取，可选写入 JSON。
    :return: (new_listings, delistings)
    """
    pages = max_pages if max_pages is not None else OKX_ANNOUNCEMENTS_PAGES
    new_listings, delistings = fetch_okx_announcements(max_pages=pages)
    if output_json:
        with open(output_json, "w", encoding="utf-8") as f:
            json.dump(
                {
                    "new_listings": [asdict(x) for x in new_listings],
                    "delistings": [asdict(x) for x in delistings],
                },
                f,
                ensure_ascii=False,
                indent=2,
            )
        logger.info("okx_announcements: wrote 上币 %d / 下币 %d to %s", len(new_listings), len(delistings), output_json)
    return new_listings, delistings


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    new_l, del_l = run(output_json="tasks/okx_announcements_2pages.json")
    print(f"上币 {len(new_l)} 条，下币 {len(del_l)} 条（页数={OKX_ANNOUNCEMENTS_PAGES}）")
    for i, x in enumerate(new_l[:5], 1):
        print(f"  上币 {i}. {x.title[:50]}... | {x.date} | {x.url[:45]}...")
    for i, x in enumerate(del_l[:5], 1):
        print(f"  下币 {i}. {x.title[:50]}... | {x.date} | {x.url[:45]}...")
