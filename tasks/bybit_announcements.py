"""
Bybit 公告抓取：调用 Bybit 官方 API /v5/announcements/index，按 type 拉取「新币上线」与「代币下架」。
推送到飞书（上币 + 下币）一张卡片。
"""
import json
import logging
from dataclasses import asdict, dataclass
from datetime import datetime

import requests

from config import BYBIT_ANNOUNCEMENTS_PAGES, FEISHU_BYBIT_ANNOUNCEMENTS_CHAT_ID
from lark_client import send_card_message, send_text_message

logger = logging.getLogger(__name__)

BYBIT_API_BASE = "https://api.bybit.com"
ANNOUNCEMENTS_PATH = "/v5/announcements/index"
# 与网页一致：zh-MY 对应 https://announcements.bybit.com/zh-MY/?category=new_crypto
LOCALE = "zh-MY"
TYPE_NEW_CRYPTO = "new_crypto"
TYPE_DELISTINGS = "delistings"
LIMIT_PER_PAGE = 20


@dataclass
class BybitAnnouncementItem:
    """单条公告."""
    title: str
    date: str
    url: str
    category: str  # new_crypto | delistings


def _format_ts(ts_ms: int | None) -> str:
    if ts_ms is None:
        return ""
    try:
        dt = datetime.fromtimestamp(ts_ms / 1000.0)
        return dt.strftime("%Y-%m-%d")
    except Exception:
        return ""


def _fetch_page(announcement_type: str, page: int) -> list[dict]:
    """请求 API 一页，返回 result.list。"""
    url = f"{BYBIT_API_BASE}{ANNOUNCEMENTS_PATH}"
    params = {
        "locale": LOCALE,
        "type": announcement_type,
        "page": page,
        "limit": LIMIT_PER_PAGE,
    }
    try:
        r = requests.get(url, params=params, timeout=15)
        r.raise_for_status()
        data = r.json()
    except Exception as e:
        logger.warning("bybit_announcements fetch type=%s page=%s failed: %s", announcement_type, page, e)
        return []
    if data.get("retCode") != 0:
        logger.warning("bybit_announcements API retCode=%s retMsg=%s", data.get("retCode"), data.get("retMsg"))
        return []
    result = data.get("result") or {}
    return result.get("list") or []


def fetch_bybit_announcements(
    max_pages: int = 2,
) -> tuple[list[BybitAnnouncementItem], list[BybitAnnouncementItem]]:
    """
    抓取 Bybit 新币上线 + 代币下架，各 max_pages 页。
    :return: (new_crypto_items, delistings_items)
    """
    new_crypto: list[BybitAnnouncementItem] = []
    delistings: list[BybitAnnouncementItem] = []

    for page in range(1, max_pages + 1):
        for typ, out_list in [(TYPE_NEW_CRYPTO, new_crypto), (TYPE_DELISTINGS, delistings)]:
            raw = _fetch_page(typ, page)
            logger.info("bybit_announcements: type=%s page=%d got %d items", typ, page, len(raw))
            for a in raw:
                title = (a.get("title") or "").strip() or "(无标题)"
                url = (a.get("url") or "").strip()
                ts = a.get("publishTime") or a.get("dateTimestamp")
                date = _format_ts(ts) if isinstance(ts, (int, float)) else ""
                out_list.append(
                    BybitAnnouncementItem(
                        title=title,
                        date=date,
                        url=url,
                        category=typ,
                    )
                )
    return new_crypto, delistings


def _build_bybit_card(
    new_crypto: list[BybitAnnouncementItem],
    delistings: list[BybitAnnouncementItem],
    pages: int,
) -> dict:
    """构建飞书卡片：上币 + 下币 两块。"""
    elements = [
        {
            "tag": "div",
            "text": {
                "tag": "lark_md",
                "content": f"抓取 **新币上线** 与 **代币下架** 各 **{pages} 页**（API 每页 20 条），仅展示前 15 条/类。",
            },
        },
        {"tag": "hr"},
        {
            "tag": "div",
            "text": {"tag": "lark_md", "content": "**新币上线**", "lines": 1},
        },
    ]
    for i, x in enumerate(new_crypto[:15], 1):
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
        "text": {"tag": "lark_md", "content": "**代币下架**", "lines": 1},
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
            "content": f"🕐 {datetime.now().strftime('%Y-%m-%d %H:%M')} · Bybit 公告 · 上币 {len(new_crypto)} 条 / 下币 {len(delistings)} 条",
            "lines": 1,
        },
    })
    return {
        "config": {"wide_screen_mode": True},
        "header": {
            "title": {"tag": "plain_text", "content": "📋 Bybit 公告（上币 + 下币）", "lines": 1},
            "template": "blue",
        },
        "elements": elements,
    }


def run_bybit_announcements_push() -> None:
    """定时任务：抓取 Bybit 上币 + 下币，推送到 FEISHU_BYBIT_ANNOUNCEMENTS_CHAT_ID。"""
    chat_id = (FEISHU_BYBIT_ANNOUNCEMENTS_CHAT_ID or "").strip()
    if not chat_id:
        logger.debug("FEISHU_BYBIT_ANNOUNCEMENTS_CHAT_ID not set, skip Bybit announcements push")
        return
    pages = BYBIT_ANNOUNCEMENTS_PAGES
    try:
        new_crypto, delistings = fetch_bybit_announcements(max_pages=pages)
        if not new_crypto and not delistings:
            send_text_message(
                chat_id,
                f"Bybit 公告：本周期抓取上币/下币各 {pages} 页，未获取到条目。\n🕐 {datetime.now().strftime('%Y-%m-%d %H:%M')}",
            )
            logger.info("Bybit announcements: 0 items, sent heartbeat to %s", chat_id[:20])
            return
        card = _build_bybit_card(new_crypto, delistings, pages)
        send_card_message(chat_id, card)
        logger.info(
            "Bybit announcements: pushed 上币 %d / 下币 %d to %s",
            len(new_crypto),
            len(delistings),
            chat_id[:20],
        )
    except Exception as e:
        logger.exception("Bybit announcements push error: %s", e)
        send_text_message(
            chat_id,
            f"Bybit 公告抓取失败：{e}\n🕐 {datetime.now().strftime('%Y-%m-%d %H:%M')}",
        )


def run(
    max_pages: int | None = None,
    output_json: str | None = None,
) -> tuple[list[BybitAnnouncementItem], list[BybitAnnouncementItem]]:
    """
    执行抓取，可选写入 JSON。
    :return: (new_crypto, delistings)
    """
    pages = max_pages if max_pages is not None else BYBIT_ANNOUNCEMENTS_PAGES
    new_crypto, delistings = fetch_bybit_announcements(max_pages=pages)
    if output_json:
        with open(output_json, "w", encoding="utf-8") as f:
            json.dump(
                {
                    "new_crypto": [asdict(x) for x in new_crypto],
                    "delistings": [asdict(x) for x in delistings],
                },
                f,
                ensure_ascii=False,
                indent=2,
            )
        logger.info("bybit_announcements: wrote 上币 %d / 下币 %d to %s", len(new_crypto), len(delistings), output_json)
    return new_crypto, delistings


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    new_l, del_l = run(output_json="tasks/bybit_announcements_2pages.json")
    print(f"上币 {len(new_l)} 条，下币 {len(del_l)} 条（页数={BYBIT_ANNOUNCEMENTS_PAGES}）")
    for i, x in enumerate(new_l[:5], 1):
        print(f"  上币 {i}. {x.title[:50]}... | {x.date} | {x.url[:50]}...")
    for i, x in enumerate(del_l[:5], 1):
        print(f"  下币 {i}. {x.title[:50]}... | {x.date} | {x.url[:50]}...")
