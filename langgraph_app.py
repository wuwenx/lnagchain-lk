"""
LangGraph 薄封装：路由 → fetch / skill / agent → 统一 state 输出。
State: user_message, document_context, chat_id, route, skill_id, history, reply_text, reply_card.
"""
import logging
from typing import Literal, TypedDict

from langgraph.graph import END, StateGraph
from langchain_core.messages import BaseMessage

from skills import get_all_skills, resolve_skill, resolve_skill_by_keywords
from skills.fetch import fetch_skill, should_trigger_fetch
from langchain_agent import reply as langchain_reply

logger = logging.getLogger(__name__)


class ChatState(TypedDict, total=False):
    """图状态：入口注入 user_message/document_context/chat_id/history，节点写 route/reply_*。"""
    user_message: str
    document_context: str
    chat_id: str
    route: str  # "fetch" | "skill" | "agent"
    skill_id: str | None
    history: list[BaseMessage]
    reply_text: str
    reply_card: dict | None


def _get_skill_by_id(skill_id: str):
    for s in get_all_skills():
        if s.id == skill_id:
            return s
    return None


def _route_node(state: ChatState) -> ChatState:
    """路由：先 fetch 再 resolve_skill（前缀），再 resolve_skill_by_keywords（包含），最后 agent。"""
    text = (state.get("user_message") or "").strip()
    if should_trigger_fetch(text):
        return {**state, "route": "fetch"}
    skill = resolve_skill(text)
    if not skill:
        skill = resolve_skill_by_keywords(text)
    if skill:
        return {**state, "route": "skill", "skill_id": skill.id}
    return {**state, "route": "agent", "skill_id": None}


def _fetch_node(state: ChatState) -> ChatState:
    """执行 fetch_skill.run，写 reply_text。"""
    text = state.get("user_message") or ""
    doc = state.get("document_context") or ""
    chat_id = state.get("chat_id") or ""
    out = fetch_skill.run(text, document_context=doc or None, chat_id=chat_id)
    return {**state, "reply_text": out or "", "reply_card": None}


def _skill_node(state: ChatState) -> ChatState:
    """根据 skill_id 调对应 skill.run，写 reply_text、reply_card。支持 run 返回 (text, card)。"""
    skill_id = state.get("skill_id")
    skill = _get_skill_by_id(skill_id) if skill_id else None
    if not skill:
        return {**state, "reply_text": "未知技能", "reply_card": None}
    text = state.get("user_message") or ""
    doc = state.get("document_context") or ""
    chat_id = state.get("chat_id") or ""
    out = skill.run(text, document_context=doc or None, chat_id=chat_id)
    if isinstance(out, tuple):
        reply_text = (out[0] or "").strip() if out else ""
        reply_card = out[1] if len(out) > 1 else None
    else:
        reply_text = (out or "").strip()
        reply_card = None
    return {**state, "reply_text": reply_text, "reply_card": reply_card}


def _agent_node(state: ChatState) -> ChatState:
    """调用现有 LangChain Agent，写 reply_text、reply_card。"""
    text = state.get("user_message") or ""
    doc = state.get("document_context") or ""
    history = state.get("history") or []
    reply_text, reply_card = langchain_reply(
        text,
        history=history,
        document_context=doc or None,
    )
    return {
        **state,
        "reply_text": reply_text or "",
        "reply_card": reply_card,
    }


def _route_after_start(state: ChatState) -> Literal["fetch", "skill", "agent"]:
    r = (state.get("route") or "").strip()
    if r == "fetch":
        return "fetch"
    if r == "skill":
        return "skill"
    return "agent"


def build_graph() -> StateGraph:
    """构建图：START → route → fetch | skill | agent → END。"""
    graph = StateGraph(ChatState)
    graph.add_node("route", _route_node)
    graph.add_node("fetch", _fetch_node)
    graph.add_node("skill", _skill_node)
    graph.add_node("agent", _agent_node)

    graph.set_entry_point("route")
    graph.add_conditional_edges("route", _route_after_start, {
        "fetch": "fetch",
        "skill": "skill",
        "agent": "agent",
    })
    graph.add_edge("fetch", END)
    graph.add_edge("skill", END)
    graph.add_edge("agent", END)

    return graph.compile()


# 单例图
_graph = None


def get_graph():
    global _graph
    if _graph is None:
        _graph = build_graph()
    return _graph


def run(
    user_message: str,
    document_context: str = "",
    chat_id: str = "",
    history: list[BaseMessage] | None = None,
) -> tuple[str, dict | None]:
    """
    执行图，返回 (reply_text, reply_card)。
    与原有 handle_message 对接：传入 user_message、document_context、chat_id、history。
    """
    initial: ChatState = {
        "user_message": user_message,
        "document_context": document_context or "",
        "chat_id": chat_id,
        "history": list(history or []),
    }
    g = get_graph()
    final = g.invoke(initial)
    return (
        final.get("reply_text") or "",
        final.get("reply_card"),
    )
