"""
飞书事件处理：消息解析、是否回复、LangChain 回复并发回
供 WebSocket (main.py) 与 Webhook (main_webhook.py) 共用
"""
import json
import logging
import threading

import lark_oapi as lark

from config import FEISHU_GROUP_ACCESS
from feishu_doc import extract_document_ids, extract_wiki_node_tokens, fetch_documents_content
from langchain_agent import reply as langchain_reply
from lark_client import send_text_message

logger = logging.getLogger(__name__)


def _extract_text_from_content(content: str, message_type: str) -> str:
    """从飞书消息 content（JSON 字符串）中解析文本。"""
    if not content:
        return ""
    try:
        obj = json.loads(content)
        if message_type == "text":
            return (obj.get("text") or "").strip()
        return ""
    except json.JSONDecodeError:
        return content.strip()


def _get_message(data):
    """兼容 WebSocket（data.message）与 Webhook（data.event.message）。"""
    if hasattr(data, "message") and data.message is not None:
        return data.message
    if hasattr(data, "event") and data.event is not None and getattr(data.event, "message", None) is not None:
        return data.event.message
    return None


def _should_reply_to_chat(data) -> bool:
    """群聊时仅在被 @ 时回复；私聊直接回复。"""
    try:
        message = _get_message(data)
        if not message:
            return False
        chat_type = getattr(message, "chat_type", "") or ""
        if chat_type != "group":
            return True
        if FEISHU_GROUP_ACCESS == "disabled":
            return False
        mentions = getattr(message, "mentions", None) or []
        return len(mentions) > 0
    except Exception:
        return True


def handle_message(data) -> None:
    """处理单条消息：取文本 → LangChain 回复 → 发回飞书。"""
    try:
        message = _get_message(data)
        if not message:
            logger.warning("no message in event, skip")
            return
        chat_id = getattr(message, "chat_id", None)
        content = getattr(message, "content", "") or ""
        message_type = getattr(message, "message_type", "text")
        if not chat_id:
            logger.warning("no chat_id in message, skip")
            return
        if not _should_reply_to_chat(data):
            return
        text = _extract_text_from_content(content, message_type)
        if not text:
            logger.info("empty text or non-text message, skip")
            return
        # 若消息中含飞书文档或知识库链接，拉取正文作为上下文
        doc_ids = extract_document_ids(text)
        wiki_tokens = extract_wiki_node_tokens(text)
        document_context = fetch_documents_content(doc_ids, wiki_tokens=wiki_tokens) if (doc_ids or wiki_tokens) else ""
        if doc_ids or wiki_tokens:
            logger.info(
                "found %d doc link(s), %d wiki link(s), fetched context len=%d",
                len(doc_ids),
                len(wiki_tokens),
                len(document_context or ""),
            )
        logger.info("user message: %s", text[:200])
        answer = langchain_reply(text, document_context=document_context)
        if answer:
            send_text_message(chat_id, answer)
            logger.info("replied to chat_id=%s", chat_id)
        else:
            send_text_message(chat_id, "抱歉，我暂时无法生成回复。")
    except Exception as e:
        logger.exception("handle_message error: %s", e)


def _do_p2_im_message_receive_v1(data) -> None:
    """P2 接收消息事件：后台线程执行避免阻塞。"""
    threading.Thread(target=handle_message, args=(data,), daemon=True).start()


def _noop_message_read(_data) -> None:
    """消息已读事件：无需处理，仅避免 500。"""
    pass


def _noop(_data) -> None:
    """其他 IM 事件：无需处理，仅避免 500。"""
    pass


def build_event_handler(encrypt_key: str, verification_token: str):
    """构建事件处理器（解密 + 验签 + 消息回调）。"""
    return (
        lark.EventDispatcherHandler.builder(encrypt_key, verification_token)
        .register_p2_im_message_receive_v1(_do_p2_im_message_receive_v1)
        .register_p2_im_message_message_read_v1(_noop_message_read)
        .register_p2_im_message_recalled_v1(_noop)
        .register_p2_im_message_reaction_created_v1(_noop)
        .register_p2_im_message_reaction_deleted_v1(_noop)
        .build()
    )
