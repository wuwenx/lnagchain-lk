"""
飞书客户端：创建 HTTP 客户端、发送消息
WebSocket 事件处理在 main.py 中与 LangChain 桥接
"""
import json
import logging

import lark_oapi as lark
from lark_oapi.api.im.v1.model.create_message_request import CreateMessageRequest
from lark_oapi.api.im.v1.model.create_message_request_body import CreateMessageRequestBody
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
