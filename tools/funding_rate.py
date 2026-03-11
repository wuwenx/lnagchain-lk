"""
资金费率工具：通过 ccxt 请求交易所 API 获取永续合约资金费率。
供 LangChain Agent 调用（如「Binance 今日 BTC 资金费率是多少」）。
"""
import logging

import ccxt
from langchain_core.tools import tool

logger = logging.getLogger(__name__)

# 常见交易所 id（ccxt 要求小写）
EXCHANGE_IDS = {"binance", "okx", "bybit", "gateio", "gate", "htx", "huobi", "kucoin", "bitget", "mexc", "toobit"}


def _normalize_symbol(symbol: str) -> str:
    """将 BTC、ETH 或 BTC/USDT 规范为永续合约符号 BTC/USDT:USDT。"""
    s = (symbol or "").strip().upper()
    if not s:
        return "BTC/USDT:USDT"
    if "/" in s and ":" in s:
        return s  # 已是 BTC/USDT:USDT 形式
    if "/" in s:
        # BTC/USDT -> BTC/USDT:USDT
        parts = s.split("/")
        if len(parts) >= 2:
            return f"{parts[0]}/{parts[1]}:{parts[1]}"
    # BTC / ETH
    return f"{s}/USDT:USDT"


def _get_exchange(exchange_id: str):
    """创建 ccxt 交易所实例，启用限速与超时。"""
    eid = (exchange_id or "").strip().lower()
    if eid not in ccxt.exchanges:
        # 常见别名
        alias = {"gate": "gateio", "huobi": "htx"}
        eid = alias.get(eid, eid)
    if eid not in ccxt.exchanges:
        raise ValueError(f"不支持的交易所: {exchange_id}，可选: {', '.join(sorted(EXCHANGE_IDS))}")
    config = {"enableRateLimit": True, "timeout": 15000}
    if eid == "binance":
        config["options"] = {"defaultType": "future"}
    return getattr(ccxt, eid)(config)


def get_funding_rate(exchange_id: str, symbol: str) -> str:
    """
    获取指定交易所在某永续合约上的当前资金费率（下一期或当前期）。
    :param exchange_id: 交易所 id，如 binance, okx, bybit（小写）
    :param symbol: 标的，如 BTC, ETH 或 BTC/USDT:USDT
    :return: 人类可读的字符串，失败时返回错误说明
    """
    exchange_id = (exchange_id or "").strip() or "binance"
    symbol = _normalize_symbol(symbol)
    try:
        exchange = _get_exchange(exchange_id)
        data = exchange.fetch_funding_rate(symbol)
        rate = data.get("fundingRate")
        next_ts = data.get("fundingTimestamp") or data.get("nextFundingTimestamp")
        symbol_short = symbol.split("/")[0] if "/" in symbol else symbol
        if rate is None:
            return f"{exchange_id} {symbol_short} 暂未获取到资金费率。"
        rate_pct = float(rate) * 100
        line = f"{exchange_id.upper()} {symbol_short} 当前资金费率: {rate_pct:.5f}%"
        if next_ts:
            from datetime import datetime
            try:
                dt = datetime.utcfromtimestamp(int(next_ts) / 1000 if next_ts > 1e12 else int(next_ts))
                line += f"（下一结算: UTC {dt.strftime('%Y-%m-%d %H:%M')}）"
            except Exception:
                pass
        return line
    except ccxt.BadSymbol as e:
        logger.warning("get_funding_rate BadSymbol: %s", e)
        return f"{exchange_id} 上未找到合约 {symbol}，请检查交易所是否支持该永续合约。"
    except Exception as e:
        logger.exception("get_funding_rate error: %s", e)
        return f"获取资金费率失败: {e}"


@tool
def get_funding_rate_tool(exchange_id: str, symbol: str = "BTC") -> str:
    """
    查询某交易所永续合约的当前资金费率。用于回答「Binance 今日 BTC 资金费率」「OKX 的 ETH 资金费率」等问题。
    exchange_id: 交易所英文名小写，如 binance, okx, bybit。
    symbol: 标的，如 BTC 或 ETH，默认 BTC。
    """
    return get_funding_rate(exchange_id=exchange_id, symbol=symbol)


@tool
def get_funding_rates_multi_tool(exchange_ids: str, symbol: str = "BTC") -> str:
    """
    一次性查询多个交易所的永续合约资金费率。当用户问「A、B、C 三个交易所的 BTC 资金费率」时，请用本工具一次传入所有交易所，避免漏掉。
    exchange_ids: 多个交易所 id，用英文逗号分隔，如 "binance,toobit,bybit"。
    symbol: 标的，如 BTC 或 ETH，默认 BTC。
    """
    if not (exchange_ids or "").strip():
        return "请提供至少一个交易所 id，多个用逗号分隔，如 binance,toobit,bybit。"
    parts = [p.strip().lower() for p in exchange_ids.split(",") if p.strip()]
    if not parts:
        return "未解析到有效交易所，请用逗号分隔，如 binance,toobit,bybit。"
    lines = []
    for eid in parts:
        lines.append(get_funding_rate(exchange_id=eid, symbol=symbol))
    return "\n".join(lines)
