"""
多交易所流动性深度对比工具：一次请求多个交易所**永续合约**订单簿，按 万1(0.01%)/万5(0.05%)/微观(0.1%)/紧密(0.5%)/核心(1%) 五档计算深度，供 Agent 分析。
不走 skill，仅作为 Agent 工具。
"""
import logging

import ccxt
from langchain_core.tools import tool

logger = logging.getLogger(__name__)

# 深度档位：与截图一致 万1、万5、微观、紧密、核心
DEPTH_LEVELS_PCT = [0.01, 0.05, 0.1, 0.5, 1.0]
DEPTH_LABELS = ["万1(0.01%)", "万5(0.05%)", "微观(0.1%)", "紧密(0.5%)", "核心(1%)"]

ALIAS = {"gate": "gateio", "huobi": "htx"}

# Toobit 深度需除以 1000 后才与其他交易所单位一致
DEPTH_SCALE = {"toobit": 1 / 1000}


def _normalize_symbol_swap(symbol: str) -> str:
    """永续合约订单簿符号：ETH -> ETH/USDT:USDT（USDT 本位永续）。"""
    s = (symbol or "").strip().upper()
    if not s:
        return "ETH/USDT:USDT"
    if ":" in s and "/" in s:
        return s
    if "/" in s:
        return f"{s}:USDT" if ":USDT" not in s else s
    return f"{s}/USDT:USDT"


def _get_exchange_swap(exchange_id: str):
    """创建 ccxt 永续合约交易所实例（defaultType=swap，用于订单簿）。"""
    eid = (exchange_id or "").strip().lower()
    eid = ALIAS.get(eid, eid)
    if eid not in ccxt.exchanges:
        raise ValueError(f"不支持的交易所: {exchange_id}")
    return getattr(ccxt, eid)({
        "enableRateLimit": True,
        "timeout": 15000,
        "options": {"defaultType": "swap"},
    })


def _depth_in_band_usdt(bids: list, asks: list, depth_pct: float) -> tuple[float, float, float, float]:
    """
    计算在 mid 价 ±depth_pct% 范围内买盘、卖盘深度（USDT）及该档最低价、最高价。
    返回 (买盘USDT, 卖盘USDT, low, high)。
    """
    if not bids or not asks:
        return 0.0, 0.0, 0.0, 0.0
    mid = (float(bids[0][0]) + float(asks[0][0])) / 2
    low = mid * (1 - depth_pct / 100)
    high = mid * (1 + depth_pct / 100)
    bid_total = 0.0
    for row in bids:
        p, s = float(row[0]), float(row[1])
        if p < low:
            break
        bid_total += p * s
    ask_total = 0.0
    for row in asks:
        p, s = float(row[0]), float(row[1])
        if p > high:
            break
        ask_total += p * s
    return bid_total, ask_total, low, high


def _avg_price_and_slippage(bids: list, asks: list, size: float, mid: float) -> tuple[float | None, float | None, float | None, float | None]:
    """
    模拟吃单：买入/卖出 size 个标的的均价与滑点。
    返回 (买入均价, 买入滑点%, 卖出均价, 卖出滑点%)；深度不足时对应项为 None。
    滑点定义：买入滑点 = (买入均价 - mid)/mid*100，卖出滑点 = (mid - 卖出均价)/mid*100。
    """
    if not bids or not asks or size <= 0:
        return None, None, None, None
    buy_avg = None
    buy_slip_pct = None
    remaining = size
    cost = 0.0
    for row in asks:
        p, s = float(row[0]), float(row[1])
        take = min(remaining, s)
        cost += p * take
        remaining -= take
        if remaining <= 0:
            buy_avg = cost / size
            buy_slip_pct = (buy_avg - mid) / mid * 100 if mid else None
            break
    sell_avg = None
    sell_slip_pct = None
    remaining = size
    revenue = 0.0
    for row in bids:
        p, s = float(row[0]), float(row[1])
        take = min(remaining, s)
        revenue += p * take
        remaining -= take
        if remaining <= 0:
            sell_avg = revenue / size
            sell_slip_pct = (mid - sell_avg) / mid * 100 if mid else None
            break
    return buy_avg, buy_slip_pct, sell_avg, sell_slip_pct


def get_liquidity_depth_multi(
    exchange_ids: str,
    symbol: str = "ETH",
    depth_levels: str = "",
    simulate_size: float | None = None,
) -> str:
    """
    一次请求多个交易所的永续合约订单簿，计算各档深度（含档位价格区间）、可选模拟大单滑点与均价。
    depth_levels 逗号分隔的百分比；simulate_size 若传入则计算买入/卖出该数量时的滑点与均价（单位：标的币个数）。
    """
    symbol = _normalize_symbol_swap(symbol)
    if not (exchange_ids or "").strip():
        return "请提供至少一个交易所 id，多个用逗号分隔，如 okx,binance。"
    eids = [p.strip().lower() for p in exchange_ids.split(",") if p.strip()]
    if not eids:
        return "未解析到有效交易所。"
    if not depth_levels or not depth_levels.strip():
        pcts = DEPTH_LEVELS_PCT
        labels = DEPTH_LABELS
    else:
        try:
            pcts = [float(x.strip()) for x in depth_levels.split(",") if x.strip()]
            labels = [f"{p}%" for p in pcts]
        except ValueError:
            pcts = DEPTH_LEVELS_PCT
            labels = DEPTH_LABELS
    asset = symbol.split("/")[0]
    lines = []
    for eid in eids:
        try:
            ex = _get_exchange_swap(eid)
            ob = ex.fetch_order_book(symbol, limit=500)
            bids = ob.get("bids") or []
            asks = ob.get("asks") or []
            if not bids or not asks:
                lines.append(f"{eid.upper()} {symbol}: 订单簿为空或暂无数据。")
                continue
            mid = (float(bids[0][0]) + float(asks[0][0])) / 2
            scale = DEPTH_SCALE.get(eid, 1.0)
            num_levels = len(pcts)
            parts = [f"{eid.upper()} {asset} 中间价≈{mid:.2f} USDT  共分析 {num_levels} 档"]
            band_ranges = []
            for i, pct in enumerate(pcts):
                bid_usdt, ask_usdt, low, high = _depth_in_band_usdt(bids, asks, pct)
                bid_usdt, ask_usdt = bid_usdt * scale, ask_usdt * scale
                label = labels[i] if i < len(labels) else f"{pct}%"
                band_ranges.append(f"{label}[{low:.2f},{high:.2f}]")
                parts.append(f"  {label} 价格区间[{low:.2f},{high:.2f}] 买盘: {bid_usdt/1e6:.2f}M USDT  卖盘: {ask_usdt/1e6:.2f}M USDT")
            parts.append("  档位价格区间: " + "  ".join(band_ranges))
            if simulate_size is not None and simulate_size > 0:
                buy_avg, buy_slip, sell_avg, sell_slip = _avg_price_and_slippage(bids, asks, simulate_size, mid)
                slip_parts = []
                if buy_avg is not None and buy_slip is not None:
                    slip_parts.append(f"买入{simulate_size:.0f}{asset} 滑点: {buy_slip:.4f}% 买入均价: {buy_avg:.2f} USDT")
                else:
                    slip_parts.append(f"买入{simulate_size:.0f}{asset} 深度不足")
                if sell_avg is not None and sell_slip is not None:
                    slip_parts.append(f"卖出{simulate_size:.0f}{asset} 滑点: {sell_slip:.4f}% 卖出均价: {sell_avg:.2f} USDT")
                else:
                    slip_parts.append(f"卖出{simulate_size:.0f}{asset} 深度不足")
                parts.append("  滑点与均价: " + " | ".join(slip_parts))
            lines.append("\n".join(parts))
        except ccxt.BadSymbol:
            lines.append(f"{eid.upper()} {symbol}: 未找到该交易对。")
        except Exception as e:
            logger.exception("get_liquidity_depth %s: %s", eid, e)
            lines.append(f"{eid.upper()}: 获取深度失败 - {e}")
    return "\n\n".join(lines)


@tool
def get_liquidity_depth_multi_tool(
    exchange_ids: str,
    symbol: str = "ETH",
    depth_levels: str = "0.01,0.05,0.1,0.5,1",
    simulate_size: float = 100,
) -> str:
    """
    一次性查询多个交易所的**永续合约**流动性深度，用于对比分析。当用户问「对比 A 和 B 的 ETH 流动性深度」「多交易所深度对比」时，请用本工具一次传入所有交易所。
    深度档位固定为：万1(0.01%)、万5(0.05%)、微观(0.1%)、紧密(0.5%)、核心(1%)，分别表示中间价±该百分比范围内的订单簿深度（USDT）。买盘与卖盘单独计算、单独输出；每档会输出该档最低价、最高价及本所分析的档位数。默认按 simulate_size 模拟买入/卖出该数量标的的滑点与均价（默认 100 个标的）。**若用户提到具体数量（如「买入10个btc的滑点」「算100个eth的滑点」），请将该数字作为 simulate_size 传入，并与 symbol 对应。**
    exchange_ids: 多个交易所 id，英文逗号分隔，如 "okx,binance" 或 "okx,binance,bybit"。
    symbol: 标的，如 ETH、BTC，默认 ETH（对应永续 ETH/USDT:USDT）。**必须按用户原话传入：用户说 1000PEPE 就传 "1000PEPE"，不要改成 PEPE 或其他。**
    depth_levels: 可选，逗号分隔的百分比，默认 "0.01,0.05,0.1,0.5,1" 对应万1/万5/微观/紧密/核心。
    simulate_size: 模拟数量（标的币个数），用于计算买入/卖出该数量时的滑点与均价，默认 100。用户若说「10个btc的滑点」则传 symbol=BTC、simulate_size=10。
    """
    return get_liquidity_depth_multi(
        exchange_ids=exchange_ids,
        symbol=symbol,
        depth_levels=depth_levels,
        simulate_size=simulate_size,
    )
