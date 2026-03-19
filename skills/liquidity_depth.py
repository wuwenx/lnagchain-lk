"""
流动性深度 skill：当用户消息包含「流动性深度」「滑点」「深度对比」等关键词时，直接调用工具拿数据并返回卡片。
不依赖模型是否返回 tool_calls，解决部分 API（如 jeniya.cn）不兼容 function calling 时无法拿数据的问题。
"""
import logging
import re

from lark_client import build_liquidity_depth_card, parse_liquidity_depth_tool_output
from tools.liquidity_depth import get_liquidity_depth_multi_tool

logger = logging.getLogger(__name__)

# 命中后用于解析的交易所名（小写）
EXCHANGE_NAMES = ["toobit", "binance", "okx", "bybit", "gateio", "htx", "mexc", "gate", "huobi"]
ALIAS = {"gate": "gateio", "huobi": "htx"}


def _parse_exchanges(text: str) -> str:
    """从消息中解析交易所，返回逗号分隔的 exchange_ids。"""
    t = (text or "").lower()
    found = []
    for name in EXCHANGE_NAMES:
        if name in t:
            out = ALIAS.get(name, name)
            if out not in found:
                found.append(out)
    if len(found) >= 2:
        return ",".join(found)
    if "toobit" in t or "toobit" in found:
        return "toobit,binance"
    return "binance,okx"


def _parse_symbol(text: str) -> str:
    """从消息中解析标的，默认 BTC。"""
    t = (text or "").strip()
    # 常见标的（可扩展）
    for m in re.finditer(r"\b(btc|eth|1000pepe|pepe|op|arb|sol|bnb|doge|ton)\b", t, re.I):
        return (m.group(1) or "BTC").upper()
    if "btc" in t.lower() or "比特币" in t:
        return "BTC"
    if "eth" in t.lower() or "以太" in t:
        return "ETH"
    return "BTC"


def _parse_simulate_size(text: str) -> float:
    """解析「N个btc」「N 个 eth」等，返回数量。"""
    t = (text or "").strip()
    m = re.search(r"(\d+(?:\.\d+)?)\s*个\s*(?:btc|eth|个)", t, re.I)
    if m:
        try:
            return float(m.group(1))
        except ValueError:
            pass
    m = re.search(r"买入\s*(\d+(?:\.\d+)?)\s*(?:btc|eth)", t, re.I)
    if m:
        try:
            return float(m.group(1))
        except ValueError:
            pass
    return 100.0


def run_liquidity_depth(
    user_message: str,
    *,
    document_context: str | None = None,
    chat_id: str = "",
    **kwargs,
) -> tuple[str, dict | None]:
    """解析消息中的交易所、标的、数量，调用流动性深度工具，返回 (文本, 卡片)。"""
    exchanges = _parse_exchanges(user_message)
    symbol = _parse_symbol(user_message)
    size = _parse_simulate_size(user_message)
    try:
        out = get_liquidity_depth_multi_tool.invoke({
            "exchange_ids": exchanges,
            "symbol": symbol,
            "simulate_size": size,
        })
        content = out if isinstance(out, str) else str(out)
    except Exception as e:
        logger.exception("liquidity_depth skill error: %s", e)
        return (f"流动性深度查询失败: {e}", None)
    parsed = parse_liquidity_depth_tool_output(content)
    if parsed:
        card = build_liquidity_depth_card(parsed, conclusion_text="")
        return ("", card)
    return (content[:2000] or "未解析到深度数据。", None)


class LiquidityDepthSkill:
    id = "liquidity_depth"
    name = "流动性深度对比"
    description = "对比多交易所永续合约流动性深度与滑点（关键词：流动性深度、滑点、深度对比）"
    trigger_commands = ["流动性深度", "滑点", "深度对比", "订单簿"]  # 用于 keyword 包含匹配，非开头

    def run(
        self,
        user_message: str,
        *,
        document_context: str | None = None,
        chat_id: str = "",
        **kwargs,
    ) -> tuple[str, dict | None]:
        return run_liquidity_depth(
            user_message,
            document_context=document_context,
            chat_id=chat_id,
            **kwargs,
        )


liquidity_depth_skill = LiquidityDepthSkill()
