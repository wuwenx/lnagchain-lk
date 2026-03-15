"""
飞书客户端：创建 HTTP 客户端、发送消息
WebSocket 事件处理在 main.py 中与 LangChain 桥接
"""
import json
import logging

import requests
import lark_oapi as lark
from lark_oapi.core.model import Config
from lark_oapi.core.token.manager import TokenManager
from lark_oapi.api.im.v1.model.create_message_request import CreateMessageRequest
from lark_oapi.api.im.v1.model.create_message_request_body import CreateMessageRequestBody
from lark_oapi.api.im.v1.model.create_message_reaction_request import CreateMessageReactionRequest
from lark_oapi.api.im.v1.model.create_message_reaction_request_body import CreateMessageReactionRequestBody
from lark_oapi.api.im.v1.model.emoji import Emoji
from lark_oapi.api.im.v1.model.update_message_request import UpdateMessageRequest
from lark_oapi.api.im.v1.model.update_message_request_body import UpdateMessageRequestBody
from lark_oapi.api.docx.v1.model.create_document_request import CreateDocumentRequest
from lark_oapi.api.docx.v1.model.create_document_request_body import CreateDocumentRequestBody
from lark_oapi.api.docx.v1.model.list_document_block_request import ListDocumentBlockRequest
from lark_oapi.api.docx.v1.model.create_document_block_children_request import CreateDocumentBlockChildrenRequest
from lark_oapi.api.docx.v1.model.create_document_block_children_request_body import (
    CreateDocumentBlockChildrenRequestBody,
)
from lark_oapi.api.docx.v1.model.block import Block
from lark_oapi.api.docx.v1.model.text import Text
from lark_oapi.api.docx.v1.model.text_element import TextElement
from lark_oapi.api.docx.v1.model.text_run import TextRun
from config import FEISHU_APP_ID, FEISHU_APP_SECRET, FEISHU_DOMAIN, FEISHU_DOC_BASE_URL

logger = logging.getLogger(__name__)

_client: lark.Client | None = None


def get_client() -> lark.Client:
    """获取飞书 HTTP 客户端（用于发消息等 API）。"""
    global _client
    if _client is None:
        _client = (
            lark.Client.builder()
            .app_id(FEISHU_APP_ID)
            .app_secret(FEISHU_APP_SECRET)
            .domain(FEISHU_DOMAIN)
            .build()
        )
    return _client


def send_text_message(chat_id: str, text: str) -> str | None:
    """
    向指定会话发送文本消息。
    :param chat_id: 会话 ID（chat_id）
    :param text: 文本内容
    :return: message_id，失败返回 None
    """
    try:
        body = (
            CreateMessageRequestBody.builder()
            .receive_id(chat_id)
            .msg_type("text")
            .content(json.dumps({"text": text}))
            .build()
        )
        req = (
            CreateMessageRequest.builder()
            .receive_id_type("chat_id")
            .request_body(body)
            .build()
        )
        resp = get_client().im.v1.message.create(req)
        if not resp.success():
            logger.error("send message failed: %s", resp.raw.content)
            return None
        return getattr(resp.data, "message_id", None)
    except Exception as e:
        logger.exception("send_text_message error: %s", e)
        return None


def add_message_reaction(message_id: str, emoji_type: str = "SMILE") -> bool:
    """
    给指定消息添加表情回应（如「正在处理」的提示）。
    :param message_id: 消息 ID（事件中的 message.message_id）
    :param emoji_type: 飞书 emoji 类型，须为接口支持的枚举，如 SMILE、THUMBSUP、LAUGH、OK（文档示例用 SMILE）
    :return: 是否添加成功
    """
    if not message_id or not message_id.strip():
        return False
    try:
        emoji = Emoji.builder().emoji_type(emoji_type.strip()).build()
        body = CreateMessageReactionRequestBody.builder().reaction_type(emoji).build()
        req = (
            CreateMessageReactionRequest.builder()
            .message_id(message_id.strip())
            .request_body(body)
            .build()
        )
        resp = get_client().im.v1.message_reaction.create(req)
        if not resp.success():
            logger.warning("add_message_reaction failed: %s", resp.raw.content)
            return False
        return True
    except Exception as e:
        logger.exception("add_message_reaction error: %s", e)
        return False


def update_text_message(message_id: str, text: str) -> bool:
    """
    更新已有消息的文本内容（用于流式回复时逐步更新同一条消息）。
    :param message_id: 消息 ID（由 send_text_message 返回）
    :param text: 新的全文内容
    :return: 是否更新成功
    """
    try:
        body = (
            UpdateMessageRequestBody.builder()
            .msg_type("text")
            .content(json.dumps({"text": text}))
            .build()
        )
        req = (
            UpdateMessageRequest.builder()
            .message_id(message_id)
            .request_body(body)
            .build()
        )
        resp = get_client().im.v1.message.update(req)
        if not resp.success():
            logger.error("update message failed: %s", resp.raw.content)
            return False
        return True
    except Exception as e:
        logger.exception("update_text_message error: %s", e)
        return False


def send_card_message(chat_id: str, card: dict) -> str | None:
    """
    向指定会话发送交互式卡片消息。
    :param chat_id: 会话 ID（chat_id）
    :param card: 飞书卡片 JSON 对象（config/header/elements）
    :return: message_id，失败返回 None
    """
    try:
        body = (
            CreateMessageRequestBody.builder()
            .receive_id(chat_id)
            .msg_type("interactive")
            .content(json.dumps(card, ensure_ascii=False))
            .build()
        )
        req = (
            CreateMessageRequest.builder()
            .receive_id_type("chat_id")
            .request_body(body)
            .build()
        )
        resp = get_client().im.v1.message.create(req)
        if not resp.success():
            logger.error("send card failed: %s", resp.raw.content)
            return None
        return getattr(resp.data, "message_id", None)
    except Exception as e:
        logger.exception("send_card_message error: %s", e)
        return None


def build_funding_rate_card(lines: list[dict]) -> dict:
    """
    根据资金费率数据构建飞书卡片。lines 每项为 {"exchange": "Binance", "symbol": "BTC", "rate_pct": "-0.01190", "next_settlement": "UTC 2026-03-11 08:00"} 或错误信息 {"error": "..."}。
    """
    elements = []
    for i, row in enumerate(lines):
        if row.get("error"):
            elements.append({
                "tag": "div",
                "text": {"tag": "plain_text", "content": (row.get("error") or "")[:200], "lines": 2},
            })
        else:
            ex = row.get("exchange", "")
            sym = row.get("symbol", "BTC")
            rate = row.get("rate_pct", "")
            next_ts = row.get("next_settlement", "")
            content = f"**{ex}** {sym}\n费率: {rate}%"
            if next_ts:
                content += f"\n下一结算: {next_ts}"
            elements.append({
                "tag": "div",
                "text": {"tag": "lark_md", "content": content},
            })
        if i < len(lines) - 1:
            elements.append({"tag": "hr"})
    return {
        "config": {"wide_screen_mode": True},
        "header": {
            "title": {"tag": "plain_text", "content": "📊 永续合约资金费率", "lines": 1},
            "template": "blue",
        },
        "elements": elements,
    }


def parse_funding_rate_tool_output(text: str) -> list[dict]:
    """
    从 get_funding_rate / get_funding_rates_multi 的工具输出文本解析出结构化行，用于构建卡片。
    成功行格式: "BINANCE BTC 当前资金费率: -0.01190%（下一结算: UTC 2026-03-11 08:00）"
    """
    import re
    lines = []
    for line in (text or "").strip().split("\n"):
        line = line.strip()
        if not line:
            continue
        m = re.match(r"([A-Za-z0-9]+)\s+(\w+)\s+当前资金费率:\s*([^%（）]+)%(?:\s*（下一结算:\s*([^）]+)）)?", line)
        if m:
            lines.append({
                "exchange": m.group(1).upper(),
                "symbol": m.group(2),
                "rate_pct": m.group(3).strip(),
                "next_settlement": (m.group(4) or "").strip(),
            })
        else:
            if "资金费率" in line or "失败" in line or "错误" in line or "未找到" in line:
                lines.append({"error": line[:200]})
    return lines


def parse_liquidity_depth_tool_output(text: str) -> dict | None:
    """
    从 get_liquidity_depth_multi 的工具输出解析出结构化数据，用于构建深度对比卡片。
    支持档位价格区间、买/卖盘、滑点与均价。返回 {"symbol", "exchanges": [{"name", "mid", "num_bands", "levels": {label: {"bid","ask","low","high"}}, "slippage": {}}], "level_labels"}。
    """
    import re
    if not (text or "").strip():
        return None
    exchanges = []
    level_labels = []
    current_ex = None
    for line in (text or "").split("\n"):
        line = line.strip()
        if not line:
            continue
        # 交易所行: "BINANCE ETH 中间价≈2031.61 USDT  共分析 5 档"
        m = re.match(r"([A-Za-z0-9]+)\s+(\w+)\s+中间价[≈=]\s*([\d.]+)\s*USDT(?:\s+共分析\s+(\d+)\s+档)?", line)
        if m:
            if current_ex and current_ex.get("levels"):
                exchanges.append(current_ex)
            current_ex = {
                "name": m.group(1).upper(),
                "symbol": m.group(2),
                "mid": float(m.group(3)),
                "num_bands": int(m.group(4)) if m.group(4) else None,
                "levels": {},
                "slippage": {},
            }
            continue
        # 档位行（带价格区间）: "  万1(0.01%) 价格区间[2028.0,2029.0] 买盘: 0.15M USDT  卖盘: 0.14M USDT"
        m2 = re.match(r"(.+?)\s+价格区间\[([\d.]+),([\d.]+)\]\s+买盘:\s*([\d.]+)M\s*USDT\s+卖盘:\s*([\d.]+)M\s*USDT", line)
        if m2 and current_ex:
            label, low, high = m2.group(1).strip(), m2.group(2), m2.group(3)
            bid_val, ask_val = m2.group(4), m2.group(5)
            current_ex["levels"][label] = {"bid": bid_val, "ask": ask_val, "low": low, "high": high}
            if label not in level_labels:
                level_labels.append(label)
            continue
        # 档位行（旧格式无价格区间）: "  万1(0.01%) 买盘: 0.15M USDT  卖盘: 0.14M USDT"
        m2b = re.match(r"(.+?)\s+买盘:\s*([\d.]+)M\s*USDT\s+卖盘:\s*([\d.]+)M\s*USDT", line)
        if m2b and current_ex and line.startswith("  "):
            label, bid_val, ask_val = m2b.group(1).strip(), m2b.group(2), m2b.group(3)
            current_ex["levels"][label] = {"bid": bid_val, "ask": ask_val, "low": None, "high": None}
            if label not in level_labels:
                level_labels.append(label)
            continue
        # 滑点与均价: "  滑点与均价: 买入1000ETH 滑点: 0.05% 买入均价: 2032.5 USDT | 卖出1000ETH 滑点: 0.04% 卖出均价: 2030.2 USDT"
        m3 = re.search(
            r"滑点与均价:\s*买入([\d.]+)(\w+)\s+滑点:\s*([\d.]+)%\s+买入均价:\s*([\d.]+)\s*USDT\s*\|\s*卖出\1\2\s+滑点:\s*([\d.]+)%\s+卖出均价:\s*([\d.]+)\s*USDT",
            line,
        )
        if m3 and current_ex:
            current_ex["slippage"] = {
                "simulate_size": float(m3.group(1)),
                "asset": m3.group(2),
                "buy_slip_pct": m3.group(3),
                "buy_avg": m3.group(4),
                "sell_slip_pct": m3.group(5),
                "sell_avg": m3.group(6),
            }
            continue
        # 滑点行含深度不足（卖出有数据）
        m3b = re.search(
            r"滑点与均价:\s*买入([\d.]+)(\w+)\s+深度不足\s*\|\s*卖出\1\2\s+滑点:\s*([\d.]+)%\s+卖出均价:\s*([\d.]+)\s*USDT",
            line,
        )
        if m3b and current_ex:
            current_ex["slippage"] = {"simulate_size": float(m3b.group(1)), "asset": m3b.group(2), "buy_slip_pct": None, "buy_avg": None, "sell_slip_pct": m3b.group(3), "sell_avg": m3b.group(4)}
            continue
        # 滑点行：买入有数据、卖出深度不足
        m3c = re.search(
            r"滑点与均价:\s*买入([\d.]+)(\w+)\s+滑点:\s*([\d.]+)%\s+买入均价:\s*([\d.]+)\s*USDT\s*\|\s*卖出\1\2\s+深度不足",
            line,
        )
        if m3c and current_ex:
            current_ex["slippage"] = {"simulate_size": float(m3c.group(1)), "asset": m3c.group(2), "buy_slip_pct": m3c.group(3), "buy_avg": m3c.group(4), "sell_slip_pct": None, "sell_avg": None}
            continue
        # 滑点行：双深度不足
        m3d = re.search(r"滑点与均价:\s*买入([\d.]+)(\w+)\s+深度不足\s*\|\s*卖出\1\2\s+深度不足", line)
        if m3d and current_ex:
            current_ex["slippage"] = {"simulate_size": float(m3d.group(1)), "asset": m3d.group(2), "buy_slip_pct": None, "buy_avg": None, "sell_slip_pct": None, "sell_avg": None}
            continue
        # 错误行
        if "订单簿为空" in line or "获取深度失败" in line or "未找到" in line:
            if current_ex and current_ex.get("levels"):
                exchanges.append(current_ex)
            current_ex = {"name": line.split()[0] if line.split() else "?", "mid": None, "num_bands": None, "levels": {}, "slippage": {}, "error": line[:150]}
    if current_ex:
        exchanges.append(current_ex)
    if not exchanges or not level_labels:
        return None
    symbol = exchanges[0].get("symbol", "ETH") if exchanges else "ETH"
    return {"symbol": symbol, "exchanges": exchanges, "level_labels": level_labels}


def build_liquidity_depth_card(parsed: dict, conclusion_text: str = "") -> dict:
    """
    根据解析后的流动性深度数据构建飞书卡片：头部标题、价格水平模块、各档位深度表格、结论、时间戳。
    conclusion_text: 大模型给出的结论摘要，可为空。
    """
    from datetime import datetime
    symbol = parsed.get("symbol", "ETH")
    exchanges = parsed.get("exchanges", [])
    level_labels = parsed.get("level_labels", [])
    if not exchanges:
        return {"config": {"wide_screen_mode": True}, "header": {"title": {"tag": "plain_text", "content": "流动性深度"}}, "elements": []}
    exchange_names = [e.get("name", "?") for e in exchanges]
    title = " vs ".join(exchange_names) + f" {symbol} 流动性深度对比"
    elements = []

    # 1. 价格水平模块（含各所分析档位数）
    price_lines = []
    mids = []
    for e in exchanges:
        if e.get("error"):
            price_lines.append(f"- **{e['name']}**：{e.get('error', '')[:80]}")
        else:
            mid = e.get("mid")
            nb = e.get("num_bands")
            if mid is not None:
                band_info = f" 共分析 {nb} 档" if nb is not None else ""
                price_lines.append(f"- **{e['name']}** 中间价：≈{mid:.2f} USDT{band_info}")
                mids.append(mid)
    if mids:
        spread = max(mids) - min(mids) if len(mids) > 1 else 0
        if spread < 1:
            price_lines.append("\n> ✅ 价差极小，市场高度同步")
        else:
            price_lines.append(f"\n> 价差约 {spread:.2f} USDT")
    elements.append({
        "tag": "div",
        "text": {"tag": "lark_md", "content": "**💰 价格水平（" + symbol + "/USDT）**\n" + "\n".join(price_lines)},
    })
    elements.append({"tag": "hr"})

    # 2. 各档位深度对比：用 lark_md 表格（原生 table 在部分飞书环境不展示，改用 Markdown 表格保证可见）
    elements.append({
        "tag": "div",
        "text": {"tag": "lark_md", "content": "**📈 各档位流动性深度对比（单位：M USDT，买盘/卖盘分开；档位列为该档最低价～最高价）**"},
    })
    col_headers = ["档位"]
    for e in exchanges:
        col_headers.append(e.get("name", "?") + "买")
        col_headers.append(e.get("name", "?") + "卖")
    table_rows = [col_headers]
    for label in level_labels:
        low_high = ""
        for e in exchanges:
            lev = e.get("levels", {}).get(label)
            if isinstance(lev, dict) and lev.get("low") is not None and lev.get("high") is not None:
                low_high = f" [{lev['low']},{lev['high']}]"
                break
        row = [label + low_high]
        for e in exchanges:
            lev = e.get("levels", {}).get(label)
            if isinstance(lev, dict):
                row.append((lev.get("bid") or "-") + ("M" if lev.get("bid") else ""))
                row.append((lev.get("ask") or "-") + ("M" if lev.get("ask") else ""))
            else:
                row.append("-")
                row.append("-")
        table_rows.append(row)
    md_lines = ["| " + " | ".join(col_headers) + " |", "| " + " | ".join(["---"] * len(col_headers)) + " |"]
    for r in table_rows[1:]:
        md_lines.append("| " + " | ".join(str(x) for x in r) + " |")
    elements.append({
        "tag": "div",
        "text": {"tag": "lark_md", "content": "\n".join(md_lines)},
    })
    elements.append({"tag": "hr"})

    # 2b. 滑点与均价：用 lark_md 表格（保证卡片在飞书内正常展示）
    slip_exchanges = [e for e in exchanges if e.get("slippage") and isinstance(e["slippage"], dict) and (e["slippage"].get("simulate_size") is not None or e["slippage"].get("asset"))]
    if slip_exchanges:
        s0 = slip_exchanges[0]["slippage"]
        size_str = f"{s0.get('simulate_size', '')}{s0.get('asset', symbol)}"
        elements.append({
            "tag": "div",
            "text": {"tag": "lark_md", "content": f"**📉 滑点与均价（模拟买入/卖出 {size_str}）**"},
        })
        slip_headers = ["交易所", "买入滑点%", "买入均价", "卖出滑点%", "卖出均价"]
        slip_rows = [slip_headers]
        for e in slip_exchanges:
            s = e["slippage"]
            slip_rows.append([
                e.get("name", "?"),
                str(s.get("buy_slip_pct")) + "%" if s.get("buy_slip_pct") is not None else "深度不足",
                str(s.get("buy_avg")) if s.get("buy_avg") is not None else "深度不足",
                str(s.get("sell_slip_pct")) + "%" if s.get("sell_slip_pct") is not None else "深度不足",
                str(s.get("sell_avg")) if s.get("sell_avg") is not None else "深度不足",
            ])
        slip_md = ["| " + " | ".join(slip_headers) + " |", "| " + " | ".join(["---"] * 5) + " |"]
        for r in slip_rows[1:]:
            slip_md.append("| " + " | ".join(str(x) for x in r) + " |")
        elements.append({"tag": "div", "text": {"tag": "lark_md", "content": "\n".join(slip_md)}})
        elements.append({"tag": "hr"})

    # 3. 结论模块
    conclusion = (conclusion_text or "").strip()
    if not conclusion:
        conclusion = "请结合上方各档位数据对比各所流动性差异，按各档位分别说明买盘/卖盘深度与价差。"
    elements.append({
        "tag": "div",
        "text": {"tag": "lark_md", "content": "**💡 核心结论**\n" + conclusion},
    })
    # 4. 时间戳
    elements.append({
        "tag": "note",
        "elements": [{"tag": "plain_text", "content": "数据更新时间：{}".format(datetime.now().strftime("%Y-%m-%d %H:%M"))}],
    })

    return {
        "config": {"wide_screen_mode": True},
        "header": {
            "title": {"tag": "plain_text", "content": "📊 " + title},
            "template": "blue",
        },
        "elements": elements,
    }


def _get_tenant_access_token() -> str | None:
    """使用与 SDK 相同的逻辑获取 tenant_access_token，供直接 HTTP 调用使用。"""
    if not FEISHU_APP_ID or not FEISHU_APP_SECRET:
        return None
    try:
        config = Config()
        config.app_id = FEISHU_APP_ID
        config.app_secret = FEISHU_APP_SECRET
        config.domain = FEISHU_DOMAIN or "https://open.feishu.cn"
        return TokenManager.get_self_tenant_token(config)
    except Exception as e:
        logger.debug("_get_tenant_access_token failed: %s", e)
        return None


def search_doc_wiki(query: str, page_size: int = 10) -> list[dict]:
    """
    调用飞书开放平台「搜索文档与知识库」接口（search v2 doc_wiki/search），
    返回匹配的文档/知识库条目列表，每项含 title_highlighted、summary_highlighted、url（若有）。
    需应用具备 docx:document 与 wiki 相关读权限。使用 tenant_access_token 直连接口，避免 SDK 鉴权问题。
    :param query: 搜索关键词
    :param page_size: 返回条数上限，默认 10
    :return: [{"title": str, "summary": str, "url": str | None}, ...]，失败或无权时返回 []
    """
    if not query or not query.strip():
        return []
    query = query.strip()
    logger.info("search_doc_wiki request: query=%r page_size=%s", query, page_size)
    try:
        token = _get_tenant_access_token()
        if not token:
            logger.warning("search_doc_wiki: no tenant_access_token (check FEISHU_APP_ID/FEISHU_APP_SECRET)")
            return []
        url = (FEISHU_DOMAIN or "https://open.feishu.cn").rstrip("/") + "/open-apis/search/v2/doc_wiki/search"
        payload = {"query": query, "page_size": min(max(1, page_size), 20)}
        headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json; charset=utf-8"}
        r = requests.post(url, json=payload, headers=headers, timeout=30)
        body = r.json() if r.text else {}
        if r.status_code != 200 or body.get("code") != 0:
            logger.warning(
                "search_doc_wiki API failed: query=%r status=%s body=%s",
                query,
                r.status_code,
                (r.text or "")[:800],
            )
            return []
        data = body.get("data") or {}
        units = data.get("res_units") or []
        total = data.get("total")
        has_more = data.get("has_more")
        logger.info(
            "search_doc_wiki response: query=%r total=%s has_more=%s res_units_len=%s",
            query,
            total,
            has_more,
            len(units),
        )
        if not units:
            logger.info("search_doc_wiki: no res_units (total=%s), query=%r", total, query)
        out = []
        for u in units:
            title = (u.get("title_highlighted") or "").strip() or "(无标题)"
            summary = (u.get("summary_highlighted") or "").strip() or ""
            meta = u.get("result_meta") or {}
            url_val = (meta.get("url") or "").strip() or None
            out.append({"title": title, "summary": summary, "url": url_val})
        return out
    except Exception as e:
        logger.exception("search_doc_wiki error: %s", e)
        return []


def create_lark_document(title: str, folder_token: str = "") -> tuple[str | None, str | None]:
    """
    在飞书云文档中创建一篇新文档（仅标题，正文为空）。
    :param title: 文档标题
    :param folder_token: 可选，文件夹 token，空表示根目录（需应用有对应权限）
    :return: (document_id, url)，失败返回 (None, None)；若未配置 FEISHU_DOC_BASE_URL 则 url 为 None
    """
    try:
        body = (
            CreateDocumentRequestBody.builder()
            .title(title)
            .folder_token(folder_token or "")
            .build()
        )
        req = CreateDocumentRequest.builder().request_body(body).build()
        resp = get_client().docx.v1.document.create(req)
        if not resp.success():
            logger.error("create document failed: %s", getattr(resp, "raw", resp))
            return None, None
        doc = getattr(resp.data, "document", None)
        if not doc:
            return None, None
        doc_id = getattr(doc, "document_id", None)
        if not doc_id:
            return None, None
        url = None
        if FEISHU_DOC_BASE_URL:
            url = f"{FEISHU_DOC_BASE_URL}/docx/{doc_id}"
        return doc_id, url
    except Exception as e:
        logger.exception("create_lark_document error: %s", e)
        return None, None


# 飞书 docx 正文段落 block_type：1=页面(根)，2=正文段落
_BLOCK_TYPE_PAGE = 1
_BLOCK_TYPE_TEXT = 2


def _make_paragraph_block(line: str) -> Block:
    """构造一个正文段落 Block。"""
    text_run = TextRun.builder().content(line or " ").build()
    element = TextElement.builder().text_run(text_run).build()
    text = Text.builder().elements([element]).build()
    return Block.builder().block_type(_BLOCK_TYPE_TEXT).text(text).build()


def _get_document_root_block_id(document_id: str) -> str | None:
    """获取文档根节点（page）的 block_id。"""
    try:
        req = ListDocumentBlockRequest.builder().document_id(document_id).page_size(1).build()
        resp = get_client().docx.v1.document_block.list(req)
        if not resp.success() or not getattr(resp.data, "items", None):
            return None
        items = resp.data.items
        if not items:
            return None
        return getattr(items[0], "block_id", None)
    except Exception as e:
        logger.exception("list document blocks error: %s", e)
        return None


def append_document_body(document_id: str, body_text: str) -> bool:
    """
    向已存在的文档追加正文（在根 block 下插入段落）。
    body_text 按行拆成多个段落写入；单次请求最多 50 段，超出会分批。
    """
    if not body_text or not body_text.strip():
        return True
    root_id = _get_document_root_block_id(document_id)
    if not root_id:
        logger.warning("append_document_body: no root block for doc %s", document_id)
        return False
    lines = [ln for ln in body_text.strip().split("\n")]
    chunk_size = 50
    insert_index = 0
    for i in range(0, len(lines), chunk_size):
        chunk = lines[i : i + chunk_size]
        blocks = [_make_paragraph_block(ln) for ln in chunk]
        try:
            req_body = (
                CreateDocumentBlockChildrenRequestBody.builder()
                .children(blocks)
                .index(insert_index)
                .build()
            )
            req = (
                CreateDocumentBlockChildrenRequest.builder()
                .document_id(document_id)
                .block_id(root_id)
                .request_body(req_body)
                .build()
            )
            resp = get_client().docx.v1.document_block_children.create(req)
            if not resp.success():
                logger.error("create block children failed: %s", getattr(resp, "raw", resp))
                return False
            insert_index += len(blocks)
        except Exception as e:
            logger.exception("append_document_body error: %s", e)
            return False
    return True
