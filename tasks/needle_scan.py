"""
插针监听：拉取 Toobit 永续合约全市场 24h ticker，按影线比例判断插针，推送到飞书群。
数据量：一次 fetch_tickers() 约 500+ 交易对，仅做浮点运算，压力很小。
"""
import logging
from datetime import datetime
from typing import Any

import ccxt

from config import FEISHU_NEEDLE_ALERT_CHAT_ID
from lark_client import send_card_message, send_text_message

logger = logging.getLogger(__name__)

# 影线占整根 K 线比例阈值，超过视为插针
NEEDLE_WICK_RATIO_MIN = 0.8
# 影线相对实体倍数
NEEDLE_WICK_VS_BODY_MIN = 2.0
# 最小 K 线幅度（避免极低价币噪音），占 close 的比例
MIN_RANGE_PCT = 0.001
# 插针推送门槛：整根 K 线振幅至少为该比例才推送（避免 0.9% 这种肉眼难见的“数学插针”）
MIN_RANGE_PCT_FOR_ALERT = 0.01
# 实体占整根 K 线比例下限：实体过小（如 O=H=C）视为噪音，不推送
MIN_BODY_RATIO = 0.02


def _to_str(v: Any) -> str:
    if v is None:
        return ""
    if isinstance(v, (int, float)):
        return f"{v:.8f}".rstrip("0").rstrip(".")
    return str(v)


def _detect_needles(tickers: dict[str, Any]) -> list[dict]:
    """
    对 24h ticker 做插针检测。ticker 为 ccxt 统一封装（交易所原始在 info 内），
    使用 ccxt 顶层字段：open/high/low/close, percentage, change, timestamp, bid, ask 等。
    返回插针列表，每项含 t,a,b,s,c,o,h,l,v,qv,pc,pcp 及 needle_type, upper_ratio, lower_ratio。
    """
    needles = []
    for symbol, t in tickers.items():
        if not t:
            continue
        try:
            o = float(t.get("open") or 0)
            h = float(t.get("high") or 0)
            l_val = float(t.get("low") or 0)
            c = float(t.get("close") or t.get("last") or 0)
        except (TypeError, ValueError):
            continue
        if c <= 0 or h <= l_val:
            continue
        range_ = h - l_val
        if range_ < c * MIN_RANGE_PCT:
            continue
        # 振幅过小（如 24h 仅 0.9%）在 K 线上几乎看不出针，不推送
        if range_ < c * MIN_RANGE_PCT_FOR_ALERT:
            continue
        body = abs(c - o)
        # 实体过小（如 O=H=C）：整根都是影线，多为噪音或单笔异常成交，不推送
        if range_ > 0 and body / range_ < MIN_BODY_RATIO:
            continue
        upper_wick = h - max(o, c)
        lower_wick = min(o, c) - l_val
        upper_ratio = upper_wick / range_ if range_ > 0 else 0
        lower_ratio = lower_wick / range_ if range_ > 0 else 0
        is_upper = upper_ratio >= NEEDLE_WICK_RATIO_MIN and upper_wick >= NEEDLE_WICK_VS_BODY_MIN * body
        is_lower = lower_ratio >= NEEDLE_WICK_RATIO_MIN and lower_wick >= NEEDLE_WICK_VS_BODY_MIN * body
        if not is_upper and not is_lower:
            continue
        v = t.get("baseVolume") or t.get("volume") or 0
        qv = t.get("quoteVolume") or 0
        # ccxt 统一封装：percentage=24h涨跌%，change=24h价格变动，timestamp=时间
        pct = t.get("percentage")
        if pct is not None:
            try:
                pct = float(pct)
            except (TypeError, ValueError):
                pct = (c - o) / o * 100 if o and o != 0 else None
        else:
            pct = (c - o) / o * 100 if o and o != 0 else None
        pc = t.get("change")
        if pc is not None:
            try:
                pc = float(pc)
            except (TypeError, ValueError):
                pc = c - o if o else None
        else:
            pc = c - o if o else None
        # 买一/卖一：ccxt 顶层 bid/ask
        a_val = t.get("ask")
        b_val = t.get("bid")
        needle_type = "上插针" if is_upper else "下插针"
        if is_upper and is_lower:
            needle_type = "上+下插针"
        ts_raw = t.get("timestamp")
        if ts_raw is not None:
            try:
                ts = int(float(ts_raw))
                if ts < 1e12:
                    ts = ts * 1000
            except (TypeError, ValueError):
                ts = int(datetime.now().timestamp() * 1000)
        else:
            ts = int(datetime.now().timestamp() * 1000)
        needles.append({
            "t": ts,
            "a": _to_str(a_val) if a_val is not None else "",
            "b": _to_str(b_val) if b_val is not None else "",
            "s": symbol,
            "c": _to_str(c),
            "o": _to_str(o),
            "h": _to_str(h),
            "l": _to_str(l_val),
            "v": _to_str(v),
            "qv": _to_str(qv),
            "pc": _to_str(pc) if pc is not None else "",
            "pcp": _to_str(pct) if pct is not None else "",
            "needle_type": needle_type,
            "upper_ratio": round(upper_ratio, 4),
            "lower_ratio": round(lower_ratio, 4),
        })
    return needles


def _build_needle_card(needles: list[dict]) -> dict:
    """构建飞书卡片：列出本周期检测到的插针。"""
    elements = [
        {
            "tag": "div",
            "text": {
                "tag": "lark_md",
                "content": f"基于 **Toobit 永续合约 24h** 一根 K 线影线比例判断（上/下影线占比 ≥ {NEEDLE_WICK_RATIO_MIN * 100:.0f}% 且影线 ≥ {NEEDLE_WICK_VS_BODY_MIN:.1f}×实体）",
            },
        },
        {"tag": "hr"},
    ]
    for n in needles[:50]:
        s = n.get("s", "")
        nt = n.get("needle_type", "")
        o, h, lv, c = n.get("o", ""), n.get("h", ""), n.get("l", ""), n.get("c", "")
        pcp = n.get("pcp", "")
        ur, lr = n.get("upper_ratio"), n.get("lower_ratio")
        line = f"**{s}** {nt} · O:{o} H:{h} L:{lv} C:{c} · 24h涨跌:{pcp}% · 上影比:{ur} 下影比:{lr}"
        elements.append({
            "tag": "div",
            "text": {"tag": "lark_md", "content": line},
        })
    if len(needles) > 50:
        elements.append({
            "tag": "div",
            "text": {"tag": "plain_text", "content": f"... 共 {len(needles)} 条，仅展示前 50 条", "lines": 1},
        })
    elements.append({"tag": "hr"})
    elements.append({
        "tag": "div",
        "text": {
            "tag": "plain_text",
            "content": f"🕐 {datetime.now().strftime('%Y-%m-%d %H:%M')} · Toobit 永续合约 24h 全市场扫描",
            "lines": 1,
        },
    })
    return {
        "config": {"wide_screen_mode": True},
        "header": {
            "title": {"tag": "plain_text", "content": "⚠️ 插针监听", "lines": 1},
            "template": "red",
        },
        "elements": elements,
    }


def _get_toobit_swap():
    """Toobit 永续合约交易所实例（defaultType=swap）。"""
    return ccxt.toobit({
        "enableRateLimit": True,
        "timeout": 20000,
        "options": {"defaultType": "swap"},
    })


def fetch_needle_results() -> list[dict]:
    """
    拉取 Toobit 永续合约 24h ticker，检测插针，返回符合约定格式的列表（仅插针标的）。
    返回格式：[{"t", "a", "b", "s", "c", "o", "h", "l", "v", "qv", "pc", "pcp"}, ...]
    """
    ex = _get_toobit_swap()
    ex.load_markets()
    tickers = ex.fetch_tickers()
    needles = _detect_needles(tickers)
    return [
        {
            "t": n["t"],
            "a": n["a"],
            "b": n["b"],
            "s": n["s"],
            "c": n["c"],
            "o": n["o"],
            "h": n["h"],
            "l": n["l"],
            "v": n["v"],
            "qv": n["qv"],
            "pc": n["pc"],
            "pcp": n["pcp"],
        }
        for n in needles
    ]


def run_needle_scan_push() -> None:
    """定时任务：扫描 Toobit 永续合约 24h 插针，有则推送到 FEISHU_NEEDLE_ALERT_CHAT_ID。"""
    chat_id = (FEISHU_NEEDLE_ALERT_CHAT_ID or "").strip()
    if not chat_id:
        logger.debug("FEISHU_NEEDLE_ALERT_CHAT_ID not set, skip needle push")
        return
    try:
        ex = _get_toobit_swap()
        ex.load_markets()
        tickers = ex.fetch_tickers()
        full_needles = _detect_needles(tickers)
        if not full_needles:
            logger.info("Needle scan: no needles detected this run, sending heartbeat")
            msg = f"插针扫描完成，本周期未检测到插针。\n🕐 {datetime.now().strftime('%Y-%m-%d %H:%M')}"
            send_text_message(chat_id, msg)
            return
        logger.info("Needle scan: detected %d needles, pushing to %s", len(full_needles), chat_id[:20])
        card = _build_needle_card(full_needles)
        mid = send_card_message(chat_id, card)
        if mid:
            logger.info("Needle scan pushed to %s, message_id=%s", chat_id[:20], mid)
        else:
            logger.warning("Needle scan push failed")
    except Exception as e:
        logger.exception("Needle scan error: %s", e)
