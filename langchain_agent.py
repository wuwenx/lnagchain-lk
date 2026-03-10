"""
LangChain 对话链：接收用户消息，返回 AI 回复文本
"""
from langchain_core.messages import HumanMessage, SystemMessage
from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder
from langchain_core.runnables import RunnablePassthrough
from langchain_openai import ChatOpenAI

from config import OPENAI_API_BASE, OPENAI_API_KEY, OPENAI_MODEL


def build_chain():
    """构建带简单上下文的对话链（可扩展为 Agent / RAG）。"""
    llm = ChatOpenAI(
        model=OPENAI_MODEL or "gpt-4o-mini",
        api_key=OPENAI_API_KEY,
        base_url=OPENAI_API_BASE or None,
        temperature=0.7,
    )
    prompt = ChatPromptTemplate.from_messages(
        [
            SystemMessage(
                content="你是一个有帮助的 AI 助手，在飞书中与用户对话。回复简洁、友好，使用中文。"
            ),
            MessagesPlaceholder(variable_name="history"),
            ("human", "{input}"),
        ]
    )
    # 若无历史可传空列表
    chain = (
        RunnablePassthrough.assign(
            history=lambda x: x.get("history") or [],
        )
        | prompt
        | llm
    )
    return chain


# 单例链，main 中调用
_chain = None


def get_chain():
    global _chain
    if _chain is None:
        _chain = build_chain()
    return _chain


def reply(user_message: str, history: list | None = None, document_context: str | None = None) -> str:
    """
    根据用户消息与可选历史生成回复。
    history:  list of [HumanMessage, AIMessage]（可选，后续可做多轮）
    document_context: 可选，飞书文档正文等上下文，会一并提供给模型
    """
    chain = get_chain()
    input_text = user_message
    if document_context and document_context.strip():
        input_text = (
            f"以下是与用户问题相关的文档内容，请结合文档内容回答用户问题。\n\n"
            f"【文档内容】\n{document_context.strip()}\n\n"
            f"【用户问题】\n{user_message}"
        )
    result = chain.invoke(
        {"input": input_text, "history": history or []}
    )
    return result.content if hasattr(result, "content") else str(result)


def reply_stream(user_message: str, history: list | None = None, document_context: str | None = None):
    """
    流式生成回复，每次 yield 当前已累积的完整文本，供调用方更新同一条飞书消息。
    """
    chain = get_chain()
    input_text = user_message
    if document_context and document_context.strip():
        input_text = (
            f"以下是与用户问题相关的文档内容，请结合文档内容回答用户问题。\n\n"
            f"【文档内容】\n{document_context.strip()}\n\n"
            f"【用户问题】\n{user_message}"
        )
    accumulated = ""
    try:
        for chunk in chain.stream({"input": input_text, "history": history or []}):
            part = getattr(chunk, "content", None) or ""
            if isinstance(part, str) and part:
                accumulated += part
                yield accumulated
    except Exception:
        pass
    if accumulated:
        yield accumulated
