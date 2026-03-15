"""
飞书事件处理：消息解析、是否回复、LangChain 回复并发回
供 WebSocket (main.py) 与 Webhook (main_webhook.py) 共用
支持按 chat_id 维护多轮对话历史。
"""
import json
import logging
import re
import threading

import lark_oapi as lark
from langchain_core.messages import AIMessage, HumanMessage

from config import (
    FEISHU_GROUP_ACCESS,
    FEISHU_BOT_OPEN_ID,
    FEISHU_PIPELINE_STAGE_A_CHAT_ID,
    FEISHU_PIPELINE_STAGE_B_CHAT_ID,
    FEISHU_PIPELINE_STAGE_C_CHAT_ID,
    FEISHU_REACTION_EMOJI,
)
from feishu_doc import extract_document_ids, extract_wiki_node_tokens, fetch_documents_content
from langgraph_app import run as graph_run
from lark_client import send_text_message, send_card_message, update_text_message, add_message_reaction

logger = logging.getLogger(__name__)

# 飞书消息里 @ 的格式可能是：
# 1) <at user_id="ou_xxx">名字</at>
# 2) 群聊里为 @_user_1 或 @ou_xxx 等
_AT_TAG_PATTERN = re.compile(r"<at[^>]*>[^<]*</at>\s*", re.IGNORECASE)
# 开头的 @ 提及（如 @_user_1、@OpenClaw、@ou_xxx），直到第一个空格或结尾
_AT_MENTION_LEADING = re.compile(r"^@\S+\s*", re.IGNORECASE)

# 按 chat_id 维护多轮对话历史，格式：list of [HumanMessage, AIMessage, ...]
_chat_histories: dict[str, list] = {}
_history_lock = threading.Lock()
# 每个会话保留最近 N 轮（每轮 1 条用户 + 1 条助手），避免无限增长
MAX_HISTORY_TURNS = 10
MAX_HISTORY_LEN = MAX_HISTORY_TURNS * 2


def _get_history(chat_id: str) -> list:
    """获取该会话的对话历史（HumanMessage/AIMessage 列表）。"""
    with _history_lock:
        return list(_chat_histories.get(chat_id, []))


def _append_to_history(chat_id: str, user_text: str, assistant_text: str) -> None:
    """将本轮用户消息与助手回复追加到历史，并截断到最多 MAX_HISTORY_LEN 条。"""
    with _history_lock:
        hist = _chat_histories.setdefault(chat_id, [])
        hist.append(HumanMessage(content=user_text))
        hist.append(AIMessage(content=assistant_text))
        if len(hist) > MAX_HISTORY_LEN:
            _chat_histories[chat_id] = hist[-MAX_HISTORY_LEN:]


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


def _strip_mention_tags(text: str) -> str:
    """去掉飞书 @ 标签，避免「@OpenClaw /jks」无法命中 skill。"""
    if not text:
        return text
    # 先去掉 <at user_id="xxx">名字</at>
    t = _AT_TAG_PATTERN.sub("", text)
    # 再去掉开头的 @xxx（如 @_user_1、@OpenClaw）
    t = _AT_MENTION_LEADING.sub("", t)
    return t.strip()


def _get_message(data):
    """兼容 WebSocket（data.message）与 Webhook（data.event.message）。"""
    if hasattr(data, "message") and data.message is not None:
        return data.message
    if hasattr(data, "event") and data.event is not None and getattr(data.event, "message", None) is not None:
        return data.event.message
    return None


def _get_message_id(message) -> str | None:
    """从事件中的 message 取出 message_id，兼容 dict 与对象。"""
    if not message:
        return None
    if isinstance(message, dict):
        return message.get("message_id") or message.get("id")
    return getattr(message, "message_id", None) or getattr(message, "id", None)


def _mentions_include_our_bot(message) -> bool:
    """事件里 mentions 是否包含本机器人（群聊@机器人 判定）。"""
    if not FEISHU_BOT_OPEN_ID:
        return True
    mentions = getattr(message, "mentions", None) or []
    for m in mentions:
        if not m:
            continue
        # 兼容 dict 或对象：id 可能为 open_id 字符串，或 id.open_id
        mid = m.get("id") if isinstance(m, dict) else getattr(m, "id", None)
        if mid is None:
            continue
        if isinstance(mid, str) and mid == FEISHU_BOT_OPEN_ID:
            return True
        if hasattr(mid, "open_id") and getattr(mid, "open_id", None) == FEISHU_BOT_OPEN_ID:
            return True
        if isinstance(mid, dict) and mid.get("open_id") == FEISHU_BOT_OPEN_ID:
            return True
    return False


def _should_reply_to_chat(data) -> bool:
    """群聊时仅在被 @ 本机器人时回复；私聊直接回复。走「群聊中@机器人」事件判定。"""
    try:
        message = _get_message(data)
        if not message:
            return False
        chat_type = getattr(message, "chat_type", "") or ""
        if chat_type != "group":
            return True
        if FEISHU_GROUP_ACCESS == "disabled":
            return False
        if FEISHU_BOT_OPEN_ID:
            if not _mentions_include_our_bot(message):
                logger.debug("group message: FEISHU_BOT_OPEN_ID set but our bot not in mentions, skip")
                return False
            return True
        mentions = getattr(message, "mentions", None) or []
        if len(mentions) > 0:
            return True
        content = getattr(message, "content", "") or ""
        if "<at" in content.lower():
            return True
        return False
    except Exception:
        return True


def _run_pipeline(user_text: str, document_context: str, chat_id_a: str) -> None:
    """
    多群流水线：A 需求分析 → B 方案生成 → C 总结输出。
    每阶段结果发到对应群，并作为下一阶段输入；最终结果发到 C 群。
    """
    chat_id_b = (FEISHU_PIPELINE_STAGE_B_CHAT_ID or "").strip()
    chat_id_c = (FEISHU_PIPELINE_STAGE_C_CHAT_ID or "").strip()
    if not chat_id_b or not chat_id_c:
        logger.warning("pipeline B/C chat_id not set, fallback to single reply in A")
        reply_text, reply_card = graph_run(
            user_message=user_text,
            document_context=document_context or "",
            chat_id=chat_id_a,
            history=[],
        )
        if reply_card:
            send_card_message(chat_id_a, reply_card)
        elif reply_text:
            send_text_message(chat_id_a, reply_text)
        else:
            send_text_message(chat_id_a, "抱歉，流水线未完整配置或执行失败。")
        return
    # 阶段 1：需求分析
    prompt_1 = "请对以下内容进行需求分析，输出结构化的需求说明（可包含背景、目标、约束等）：\n\n" + user_text
    if document_context:
        prompt_1 = "【附：文档/网页上下文】\n" + document_context + "\n\n---\n\n" + prompt_1
    reply_1, card_1 = graph_run(
        user_message=prompt_1,
        document_context="",
        chat_id=chat_id_a,
        history=[],
    )
    result_1 = reply_1 or (("✅ 见下方卡片" if card_1 else "") or "需求分析无文本输出")
    if card_1:
        send_card_message(chat_id_a, card_1)
    else:
        send_text_message(chat_id_a, result_1)
    send_text_message(chat_id_b, "【需求分析结果】\n" + (reply_1 or "（见上条卡片）"))
    logger.info("pipeline stage 1 done, result len=%d", len(result_1))
    # 阶段 2：方案生成
    prompt_2 = "请根据以下需求分析结果，生成具体方案（步骤、资源、时间等）：\n\n" + result_1
    reply_2, card_2 = graph_run(
        user_message=prompt_2,
        document_context="",
        chat_id=chat_id_b,
        history=[],
    )
    result_2 = reply_2 or (("✅ 见下方卡片" if card_2 else "") or "方案生成无文本输出")
    if card_2:
        send_card_message(chat_id_b, card_2)
    else:
        send_text_message(chat_id_b, result_2)
    send_text_message(chat_id_c, "【方案】\n" + (reply_2 or "（见上条卡片）"))
    logger.info("pipeline stage 2 done, result len=%d", len(result_2))
    # 阶段 3：总结输出
    prompt_3 = "请对以下方案进行总结输出，给出可执行的结论与要点：\n\n" + result_2
    reply_3, card_3 = graph_run(
        user_message=prompt_3,
        document_context="",
        chat_id=chat_id_c,
        history=[],
    )
    if card_3:
        send_card_message(chat_id_c, card_3)
    elif reply_3:
        send_text_message(chat_id_c, reply_3)
    else:
        send_text_message(chat_id_c, "总结输出无内容。")
    logger.info("pipeline stage 3 done, replied to C=%s", chat_id_c[:20] + "...")


def handle_message(data) -> None:
    """处理单条消息：取文本 → LangChain 回复 → 发回飞书。若来自流水线 A 群则走多群流水线。"""
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
        text_before_strip = text
        text = _strip_mention_tags(text)
        # strip 与 FEISHU_BOT_OPEN_ID 无关：前者用于「匹配指令」（content 去 @ 后得到 /jks），后者用于「是否回复」（mentions 含本 bot 才回）
        logger.info("message text: raw=%r, after_strip=%r", text_before_strip[:150], text[:150])
        if not text:
            logger.info("message is only @ mention, skip")
            return
        # 在用户消息上添加「处理中」表情回应（如 🔥），需应用有 im:message:reaction 权限
        reaction_emoji = (FEISHU_REACTION_EMOJI or "").strip()
        if reaction_emoji:
            msg_id = _get_message_id(message)
            if msg_id:
                ok = add_message_reaction(msg_id, reaction_emoji)
                if not ok:
                    logger.debug("add_message_reaction failed (message_id=%s), check im:message.reaction permission", msg_id[:20] + "...")
            else:
                _keys = list(message.keys()) if isinstance(message, dict) else [k for k in dir(message) if not k.startswith("_")]
                logger.debug("no message_id in event message, skip reaction (message keys: %s)", _keys[:20])
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
        # 多群流水线：仅当消息来自 A 群且已配置 A 的 chat_id 时触发
        pipeline_a = (FEISHU_PIPELINE_STAGE_A_CHAT_ID or "").strip()
        if pipeline_a and chat_id == pipeline_a:
            _run_pipeline(text, document_context or "", chat_id)
            logger.info("pipeline completed for chat_id=%s", chat_id)
            return
        # 默认：单群单次回复
        history = _get_history(chat_id)
        reply_text, reply_card = graph_run(
            user_message=text,
            document_context=document_context or "",
            chat_id=chat_id,
            history=history,
        )
        # 确保 reply_text 为字符串（若某节点误返回 tuple 则取首元素）
        if isinstance(reply_text, tuple):
            reply_text = (reply_text[0] if reply_text else "") or ""
        else:
            reply_text = (reply_text or "").strip() if reply_text else ""
        if reply_card:
            send_card_message(chat_id, reply_card)
            _append_to_history(chat_id, text, "✅ 见下方卡片")
        elif reply_text:
            send_text_message(chat_id, reply_text)
            _append_to_history(chat_id, text, reply_text)
        else:
            send_text_message(chat_id, "抱歉，我暂时无法生成回复。")
        logger.info("replied to chat_id=%s", chat_id)
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
        .register_p2_im_chat_updated_v1(_noop)
        .register_p2_im_chat_access_event_bot_p2p_chat_entered_v1(_noop)
        .register_p2_customized_event("im.message.updated_v1", _noop)
        .register_p2_vc_meeting_all_meeting_started_v1(_noop)
        .register_p2_vc_meeting_all_meeting_ended_v1(_noop)
        .build()
    )
