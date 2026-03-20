"""
LangChain 对话链：接收用户消息，返回 AI 回复文本
支持 Agent + Tools（如 get_funding_rate），自然语言问「Binance 今日 BTC 资金费率」会先调工具再整理回复。
支持多模态：飞书用户发送的图片经下载后以 image_url 传入支持 vision 的模型。
"""
import base64
import logging

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder
from langchain_core.runnables import RunnablePassthrough
from langchain_openai import ChatOpenAI

from config import OPENAI_API_BASE, OPENAI_API_KEY, OPENAI_MODEL, OPENAI_VISION_MODEL, VISION_MULTIMODAL
from tools import get_funding_rate_tool, get_funding_rates_multi_tool, get_liquidity_depth_multi_tool
from lark_client import build_funding_rate_card, parse_funding_rate_tool_output, parse_liquidity_depth_tool_output, build_liquidity_depth_card

logger = logging.getLogger(__name__)


def _mime_and_b64_image(data: bytes) -> tuple[str, str]:
    """根据魔数判断 MIME，返回 (mime, base64 字符串)。"""
    if len(data) >= 8 and data[:8] == b"\x89PNG\r\n\x1a\n":
        mime = "image/png"
    elif len(data) >= 6 and data[:6] in (b"GIF87a", b"GIF89a"):
        mime = "image/gif"
    elif len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        mime = "image/webp"
    else:
        mime = "image/jpeg"
    return mime, base64.b64encode(data).decode("ascii")


def _human_message_content(text: str, image_bytes_list: list[bytes] | None) -> str | list:
    """纯文本或多模态 content 列表（OpenAI 兼容）。"""
    if not image_bytes_list:
        return text
    parts: list[dict] = [{"type": "text", "text": text}]
    for raw in image_bytes_list:
        mime, b64 = _mime_and_b64_image(raw)
        parts.append({"type": "image_url", "image_url": {"url": f"data:{mime};base64,{b64}"}})
    return parts


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
AGENT_TOOLS = [get_funding_rate_tool, get_funding_rates_multi_tool, get_liquidity_depth_multi_tool]
MAX_AGENT_ITERATIONS = 5

AGENT_SYSTEM = """你是一个通用、有帮助的 AI 助手，在飞书中与用户对话。回复简洁、友好，使用中文。

你可以进行日常对话、回答问题、闲聊、知识问答等；不限于某单一领域。当用户问到你无法获取实时数据或无法访问外部系统的问题（如天气、他人私有文档等）时，可礼貌说明并建议替代方式，或根据你的知识做一般性回答。

当用户消息中带有飞书文档/知识库链接时，你会收到【文档内容】作为上下文，请结合该内容回答用户问题。若上下文中说明还附有「文档内嵌图片」，你会在同一轮对话中收到这些图片，请结合文字与图片理解需求（含截图、表格图、流程图等）。

此外，当用户询问**交易所相关数据**时，你可以使用以下工具：

**资金费率**
1. get_funding_rates_multi_tool：一次查询多个交易所的资金费率。参数 exchange_ids 逗号分隔（如 "binance,toobit,bybit"），symbol 如 BTC。用户问多所资金费率时优先用此工具。
2. get_funding_rate_tool：查询单个交易所资金费率。参数 exchange_id、symbol。

**流动性深度对比**
3. get_liquidity_depth_multi_tool：一次查询多个交易所的永续合约订单簿深度，用于对比流动性。当用户问「对比 OKX 和 Binance 的 ETH 流动性深度」「多交易所深度对比」时，用本工具一次传入所有交易所（exchange_ids 逗号分隔，如 "okx,binance"），symbol 如 ETH。**用户说的标的符号必须原样传入，不得改写或“纠正”：用户说「1000PEPE」就传 symbol="1000PEPE」，不要改成 PEPE；用户说「对比 toobit 和 Binance 的 1000PEPE 流动性深度」则 symbol 为 1000PEPE。** 工具按订单簿数据多档（默认 0.01%,0.02%,0.03%,0.05%,0.07%,0.1%,0.15%,0.2%,0.3%,0.5%,0.7%,1%）返回各所的**买盘深度**与**卖盘深度**（单位 M USDT，分开列出），并默认按 100 个标的模拟买入/卖出滑点与均价。若用户提到具体数量（如「计算买入 10000000 个 pepe 的滑点」），将该数字作为 simulate_size 传入，标的仍按用户说的流动性对比对象（如 1000PEPE）对应 symbol。
请基于档位与滑点数据分别分析买盘、卖盘流动性差异（哪所更厚、买卖是否均衡、适合大单的档位等）。
回复时覆盖用户问到的每个交易所；若有失败也说明。"""


def _get_agent_llm(*, use_vision: bool = False):
    """带 tools 的 LLM，供 Agent 循环使用。含图片时使用支持多模态的模型名。"""
    model = (OPENAI_VISION_MODEL or OPENAI_MODEL or "gpt-4o-mini") if use_vision else (OPENAI_MODEL or "gpt-4o-mini")
    llm = ChatOpenAI(
        model=model,
        api_key=OPENAI_API_KEY,
        base_url=OPENAI_API_BASE or None,
        temperature=0.7,
    )
    return llm.bind_tools(AGENT_TOOLS)


def _run_agent(
    input_text: str,
    history: list,
    image_bytes_list: list[bytes] | None = None,
) -> tuple[str, dict | None]:
    """
    运行 Agent：带工具调用的对话，返回 (最终回复文本, 卡片dict或None)。
    若本轮调用了资金费率类工具且解析成功，会返回卡片供飞书以 interactive 消息发送。
    """
    system_content = AGENT_SYSTEM
    if image_bytes_list and not VISION_MULTIMODAL:
        n = len(image_bytes_list)
        logger.info(
            "VISION_MULTIMODAL disabled or unsupported API base: dropping %s image(s), text-only request",
            n,
        )
        # 说明放在 SystemMessage，避免写入 HumanMessage 导致模型误以为「用户提到了系统提示」
        system_content = (
            AGENT_SYSTEM
            + "\n\n【本轮能力约束】当前连接的 LLM 接口（如 DeepSeek）**不支持多模态**，你**无法看到**用户发送的截图/图片（本回合已丢弃 "
            + str(n)
            + " 张图，可能含聊天图片或飞书文档内嵌图）。若用户只发了「读取」「看图」等简短文字并依赖截图，请明确说明你**看不到图**，并建议：① 用文字描述或粘贴接口路径/字段名；② 或将 OPENAI_API_BASE 换为支持 OpenAI 多模态 vision 的端点（如官方 gpt-4o 等），并设置 VISION_MULTIMODAL=true。若下方【文档内容】里有文字，仍可仅基于文字回答。"
        )
        image_bytes_list = None
    use_vision = bool(image_bytes_list)
    llm = _get_agent_llm(use_vision=use_vision)
    messages = [SystemMessage(content=system_content)]
    messages.extend(history or [])
    human_content = _human_message_content(input_text, image_bytes_list)
    messages.append(HumanMessage(content=human_content))
    tool_map = {t.name: t for t in AGENT_TOOLS}
    funding_tool_names = {get_funding_rate_tool.name, get_funding_rates_multi_tool.name}
    depth_tool_name = get_liquidity_depth_multi_tool.name
    all_funding_lines = []
    all_depth_content = []  # 深度工具输出的文本，取最后一次用于构建深度卡片
    for _ in range(MAX_AGENT_ITERATIONS):
        response = llm.invoke(messages)
        if not getattr(response, "tool_calls", None):
            card = None
            if all_depth_content:
                parsed = parse_liquidity_depth_tool_output(all_depth_content[-1])
                if parsed:
                    card = build_liquidity_depth_card(parsed, conclusion_text=response.content or "")
            if card is None and all_funding_lines:
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
            if name == depth_tool_name:
                all_depth_content.append(content)
            tool_messages.append(ToolMessage(content=content, tool_call_id=tid))
        messages.append(response)
        messages.extend(tool_messages)
    # 达到最大迭代：优先返回深度卡片，否则资金费率卡片
    card = None
    if all_depth_content:
        parsed = parse_liquidity_depth_tool_output(all_depth_content[-1])
        if parsed:
            card = build_liquidity_depth_card(parsed, conclusion_text="")
    if card is None and all_funding_lines:
        card = build_funding_rate_card(all_funding_lines)
    return "查询步骤过多，请简化问题后重试。", card


def reply(
    user_message: str,
    history: list | None = None,
    document_context: str | None = None,
    image_bytes_list: list[bytes] | None = None,
) -> tuple[str, dict | None]:
    """
    根据用户消息与可选历史生成回复。返回 (回复文本, 卡片dict或None)。
    image_bytes_list：飞书下载的图片二进制，将走多模态模型（需 API 支持 vision）。
    """
    input_text = user_message
    if document_context and document_context.strip():
        input_text = (
            f"以下是与用户问题相关的文档内容，请结合文档内容回答用户问题。\n\n"
            f"【文档内容】\n{document_context.strip()}\n\n"
            f"【用户问题】\n{user_message}"
        )
    return _run_agent(input_text, history or [], image_bytes_list=image_bytes_list)


def reply_stream(
    user_message: str,
    history: list | None = None,
    document_context: str | None = None,
    image_bytes_list: list[bytes] | None = None,
):
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
    final, card = _run_agent(input_text, history or [], image_bytes_list=image_bytes_list)
    if final:
        yield final, card
