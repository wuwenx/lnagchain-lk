"""
LangChain 对话链：接收用户消息，返回 AI 回复文本
支持 Agent + Tools（如 get_funding_rate），自然语言问「Binance 今日 BTC 资金费率」会先调工具再整理回复。
"""
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder
from langchain_core.runnables import RunnablePassthrough
from langchain_openai import ChatOpenAI

from config import OPENAI_API_BASE, OPENAI_API_KEY, OPENAI_MODEL
from tools import get_funding_rate_tool, get_funding_rates_multi_tool
from lark_client import build_funding_rate_card, parse_funding_rate_tool_output


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


# Agent 使用的工具列表（可扩展更多交易所/数据类工具）
AGENT_TOOLS = [get_funding_rate_tool, get_funding_rates_multi_tool]
MAX_AGENT_ITERATIONS = 5

AGENT_SYSTEM = """你是一个有帮助的 AI 助手，在飞书中与用户对话。回复简洁、友好，使用中文。
你可以使用以下工具查询交易所资金费率：
1. get_funding_rates_multi_tool：一次查询**多个**交易所的资金费率。参数 exchange_ids 为逗号分隔的交易所 id（如 "binance,toobit,bybit"），symbol 如 BTC。当用户问「A、B、C 三个/多个交易所的 BTC 资金费率」时，**必须优先使用本工具**，一次性传入所有交易所，确保不遗漏。
2. get_funding_rate_tool：查询**单个**交易所的资金费率。参数 exchange_id（如 binance, toobit, bybit）、symbol（如 BTC）。
回复时请覆盖用户问到的每一个交易所的结果；若某交易所查询失败，也要在回复中说明该交易所暂不可用或报错。"""


def _get_agent_llm():
    """带 tools 的 LLM，供 Agent 循环使用。"""
    llm = ChatOpenAI(
        model=OPENAI_MODEL or "gpt-4o-mini",
        api_key=OPENAI_API_KEY,
        base_url=OPENAI_API_BASE or None,
        temperature=0.7,
    )
    return llm.bind_tools(AGENT_TOOLS)


def _run_agent(input_text: str, history: list) -> tuple[str, dict | None]:
    """
    运行 Agent：带工具调用的对话，返回 (最终回复文本, 卡片dict或None)。
    若本轮调用了资金费率类工具且解析成功，会返回卡片供飞书以 interactive 消息发送。
    """
    llm = _get_agent_llm()
    messages = [SystemMessage(content=AGENT_SYSTEM)]
    messages.extend(history or [])
    messages.append(HumanMessage(content=input_text))
    tool_map = {t.name: t for t in AGENT_TOOLS}
    funding_tool_names = {get_funding_rate_tool.name, get_funding_rates_multi_tool.name}
    all_funding_lines = []
    for _ in range(MAX_AGENT_ITERATIONS):
        response = llm.invoke(messages)
        if not getattr(response, "tool_calls", None):
            card = None
            if all_funding_lines:
                card = build_funding_rate_card(all_funding_lines)
            return (response.content or "").strip() or "抱歉，我暂时无法生成回复。", card
        tool_messages = []
        for tc in response.tool_calls:
            name = tc.get("name") or (getattr(tc, "name", None))
            args = tc.get("args") or getattr(tc, "args", {}) or {}
            tid = tc.get("id") or getattr(tc, "id", "")
            tool = tool_map.get(name) if name else None
            if tool:
                try:
                    out = tool.invoke(args)
                    content = out if isinstance(out, str) else str(out)
                except Exception as e:
                    content = f"工具执行错误: {e}"
            else:
                content = f"未知工具: {name}"
            if name in funding_tool_names:
                parsed = parse_funding_rate_tool_output(content)
                all_funding_lines.extend(parsed)
            tool_messages.append(ToolMessage(content=content, tool_call_id=tid))
        messages.append(response)
        messages.extend(tool_messages)
    card = build_funding_rate_card(all_funding_lines) if all_funding_lines else None
    return "查询步骤过多，请简化问题后重试。", card


def reply(user_message: str, history: list | None = None, document_context: str | None = None) -> tuple[str, dict | None]:
    """
    根据用户消息与可选历史生成回复。返回 (回复文本, 卡片dict或None)。
    """
    input_text = user_message
    if document_context and document_context.strip():
        input_text = (
            f"以下是与用户问题相关的文档内容，请结合文档内容回答用户问题。\n\n"
            f"【文档内容】\n{document_context.strip()}\n\n"
            f"【用户问题】\n{user_message}"
        )
    return _run_agent(input_text, history or [])


def reply_stream(user_message: str, history: list | None = None, document_context: str | None = None):
    """
    流式生成回复；当前实现为运行 Agent 后 yield (最终文本, 卡片或None)。
    """
    input_text = user_message
    if document_context and document_context.strip():
        input_text = (
            f"以下是与用户问题相关的文档内容，请结合文档内容回答用户问题。\n\n"
            f"【文档内容】\n{document_context.strip()}\n\n"
            f"【用户问题】\n{user_message}"
        )
    final, card = _run_agent(input_text, history or [])
    if final:
        yield final, card
