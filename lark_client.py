"""
飞书客户端：创建 HTTP 客户端、发送消息
WebSocket 事件处理在 main.py 中与 LangChain 桥接
"""
import json
import logging

import lark_oapi as lark
from lark_oapi.api.im.v1.model.create_message_request import CreateMessageRequest
from lark_oapi.api.im.v1.model.create_message_request_body import CreateMessageRequestBody

from config import FEISHU_APP_ID, FEISHU_APP_SECRET, FEISHU_DOMAIN

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
