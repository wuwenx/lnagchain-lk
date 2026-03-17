"""
Binance 公告抓取：调用 Binance BAPI 公告列表接口（无需 Playwright），抓取指定页数的条目并推送到飞书。
支持定时任务：页数由配置 BINANCE_ANNOUNCEMENTS_PAGES 控制。
"""
import json
import logging
import re
from dataclasses import asdict, dataclass
from datetime import datetime

import requests

from config import (
    BINANCE_ANNOUNCEMENTS_PAGES,
    FEISHU_BINANCE_ANNOUNCEMENTS_CHAT_ID,
    OPENAI_API_BASE,
    OPENAI_API_KEY,
    OPENAI_MODEL,
)
from langchain_openai import ChatOpenAI
from lark_client import send_card_message, send_text_message

logger = logging.getLogger(__name__)

# BAPI 公告列表：catalogId=48 为公告分类（下架等），可改
BAPI_LIST_URL = "https://www.binance.com/bapi/composite/v1/public/cms/article/catalog/list/query"
# 单条公告详情页（格式：/detail/{code}）
ANNOUNCEMENT_DETAIL_BASE = "https://www.binance.com/zh-CN/support/announcement/detail"
# 每页条数
PAGE_SIZE = 15


@dataclass
class AnnouncementItem:
    """单条公告."""
    title: str
    date: str
    url: str
    snippet: str


def _fetch_one_page(catalog_id: int, page_no: int, page_size: int) -> list[dict]:
    """请求一页公告列表，返回 data.articles。"""
    params = {"catalogId": catalog_id, "pageNo": page_no, "pageSize": page_size}
    try:
        r = requests.get(BAPI_LIST_URL, params=params, timeout=15)
        r.raise_for_status()
        data = r.json()
    except Exception as e:
        logger.warning("binance_announcements BAPI request failed: %s", e)
        return []
    if not data.get("success") or data.get("code") != "000000":
        logger.warning("binance_announcements BAPI response: success=%s code=%s", data.get("success"), data.get("code"))
        return []
    inner = data.get("data") or {}
    return inner.get("articles") or []


def _format_publish_date(ts_ms: int | None) -> str:
    """将毫秒时间戳格式化为日期字符串。"""
    if ts_ms is None:
        return ""
    try:
        dt = datetime.fromtimestamp(ts_ms / 1000.0)
        return dt.strftime("%Y-%m-%d %H:%M")
    except Exception:
        return ""


def fetch_binance_announcements(
    max_pages: int = 2,
    catalog_id: int = 48,
) -> list[AnnouncementItem]:
    """
    抓取 Binance 公告列表（BAPI），最多 max_pages 页。
    :return: 按页顺序的 AnnouncementItem 列表
    """
    all_items: list[AnnouncementItem] = []
    for page_no in range(1, max_pages + 1):
        articles = _fetch_one_page(catalog_id, page_no, PAGE_SIZE)
        logger.info("binance_announcements: page %d got %d items", page_no, len(articles))
        for a in articles:
            code = (a.get("code") or "").strip()
            title = (a.get("title") or "").strip() or "(无标题)"
            ts = a.get("publishDate")
            date = _format_publish_date(ts) if isinstance(ts, (int, float)) else ""
            url = f"{ANNOUNCEMENT_DETAIL_BASE}/{code}" if code else ANNOUNCEMENT_DETAIL_BASE
            body = (a.get("body") or "").strip()
            snippet = body[:400] if body else ""
            all_items.append(
                AnnouncementItem(title=title, date=date, url=url, snippet=snippet)
            )
    seen = set()
    deduped = []
    for x in all_items:
        key = (x.title.strip(), x.url.strip())
        if key in seen:
            continue
        seen.add(key)
        deduped.append(x)
    if len(deduped) < len(all_items):
        logger.info("binance_announcements: deduped %d -> %d items", len(all_items), len(deduped))
    return deduped


def _translate_titles_to_chinese(titles: list[str], max_batch: int = 30) -> list[str]:
    """使用 LLM 将英文标题批量翻译为中文，返回与输入同序的中文列表。失败或缺失时用原文。"""
    if not titles:
        return []
    titles = titles[:max_batch]
    try:
        llm = ChatOpenAI(
            model=OPENAI_MODEL or "gpt-4o-mini",
            api_key=OPENAI_API_KEY,
            base_url=OPENAI_API_BASE or None,
            temperature=0,
        )
        numbered = "\n".join(f"{i+1}. {t}" for i, t in enumerate(titles))
        prompt = (
            "将以下 Binance 公告英文标题翻译成中文。仅输出翻译结果，每行一条，顺序与输入一致，不要编号、不要解释。\n\n"
            f"{numbered}"
        )
        resp = llm.invoke(prompt)
        text = (resp.content or "").strip()
        if not text:
            return list(titles)
        lines = [ln.strip() for ln in text.split("\n") if ln.strip()]
        result = []
        for i, ln in enumerate(lines):
            # 去掉行首 "1. " / "1．" / "1、" 等
            s = re.sub(r"^\s*\d+[\.\．\、]\s*", "", ln).strip()
            result.append(s[:200] if s else (titles[i] if i < len(titles) else ""))
        while len(result) < len(titles):
            result.append(titles[len(result)])
        return result[: len(titles)]
    except Exception as e:
        logger.warning("binance_announcements translate titles failed: %s", e)
        return list(titles)


def _build_announcements_card(
    items: list[AnnouncementItem],
    pages: int,
    titles_zh: list[str] | None = None,
) -> dict:
    """构建飞书卡片：Binance 公告列表，展示中文标题并带详情链接。"""
    elements = [
        {
            "tag": "div",
            "text": {
                "tag": "lark_md",
                "content": f"抓取 **{pages} 页** 去重后共 **{len(items)}** 条，仅展示前 30 条（标题已译中文）。",
            },
        },
        {"tag": "hr"},
    ]
    display_items = items[:30]
    if titles_zh is None:
        titles_zh = [x.title for x in display_items]
    for i, x in enumerate(display_items):
        title_zh = (titles_zh[i] if i < len(titles_zh) else x.title)[:100]
        snippet = (x.snippet or "").strip()
        if len(snippet) > 120:
            snippet = snippet[:120] + "..."
        # 飞书 lark_md 支持 [文字](url)
        link_line = f"[查看详情]({x.url})" if x.url else ""
        line = f"**{i+1}. {title_zh}**\n链接：{link_line}\n日期：{x.date or '-'}\n{snippet or '-'}"
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
            "content": f"🕐 {datetime.now().strftime('%Y-%m-%d %H:%M')} · Binance 公告 · 抓取 {pages} 页（BAPI）",
            "lines": 1,
        },
    })
    return {
        "config": {"wide_screen_mode": True},
        "header": {
            "title": {"tag": "plain_text", "content": "📋 Binance 公告", "lines": 1},
            "template": "blue",
        },
        "elements": elements,
    }


def run_binance_announcements_push() -> None:
    """定时任务：抓取 Binance 公告（页数取配置 BINANCE_ANNOUNCEMENTS_PAGES），推送到 FEISHU_BINANCE_ANNOUNCEMENTS_CHAT_ID。"""
    chat_id = (FEISHU_BINANCE_ANNOUNCEMENTS_CHAT_ID or "").strip()
    if not chat_id:
        logger.debug("FEISHU_BINANCE_ANNOUNCEMENTS_CHAT_ID not set, skip Binance announcements push")
        return
    pages = BINANCE_ANNOUNCEMENTS_PAGES
    try:
        items = fetch_binance_announcements(max_pages=pages)
        if not items:
            send_text_message(
                chat_id,
                f"Binance 公告：本周期抓取 {pages} 页，未获取到条目。\n🕐 {datetime.now().strftime('%Y-%m-%d %H:%M')}",
            )
            logger.info("Binance announcements: 0 items, sent heartbeat to %s", chat_id[:20])
            return
        titles_zh = _translate_titles_to_chinese([x.title for x in items[:30]])
        card = _build_announcements_card(items, pages, titles_zh=titles_zh)
        send_card_message(chat_id, card)
        logger.info(
            "Binance announcements: pushed %d items (pages=%s) to %s",
            len(items),
            pages,
            chat_id[:20],
        )
    except Exception as e:
        logger.exception("Binance announcements push error: %s", e)
        send_text_message(
            chat_id,
            f"Binance 公告抓取失败：{e}\n🕐 {datetime.now().strftime('%Y-%m-%d %H:%M')}",
        )


def run(
    max_pages: int | None = None,
    output_json: str | None = None,
) -> list[AnnouncementItem]:
    """
    执行抓取并可选写入 JSON。
    :param max_pages: 抓取页数，默认使用配置 BINANCE_ANNOUNCEMENTS_PAGES
    :param output_json: 若提供则把结果写入该路径
    """
    pages = max_pages if max_pages is not None else BINANCE_ANNOUNCEMENTS_PAGES
    items = fetch_binance_announcements(max_pages=pages)
    if output_json:
        with open(output_json, "w", encoding="utf-8") as f:
            json.dump([asdict(x) for x in items], f, ensure_ascii=False, indent=2)
        logger.info("binance_announcements: wrote %d items to %s", len(items), output_json)
    return items


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    result = run(output_json="tasks/binance_announcements_2pages.json")
    print(f"共抓取 {len(result)} 条（页数={BINANCE_ANNOUNCEMENTS_PAGES}）")
    for i, x in enumerate(result[:15], 1):
        print(f"{i}. {x.title[:60]}... | {x.date} | {x.url[:50]}...")
    if len(result) > 15:
        print(f"... 其余 {len(result) - 15} 条见 tasks/binance_announcements_2pages.json")
