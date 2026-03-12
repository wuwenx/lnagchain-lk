"""
Toobit 24h 涨跌定时任务：ccxt 拉取全市场 24h ticker，按成交量取 Top20，推送飞书群。
"""
import logging

import ccxt

from config import FEISHU_TOOBIT_24H_CHAT_ID
from lark_client import send_card_message

logger = logging.getLogger(__name__)

TOOBIT_TOP_N = 20


def _get_toobit_tickers_top_by_volume() -> list[dict]:
    """
    拉取 Toobit 全市场 24h ticker，按 quoteVolume（USDT 成交量）排序取前 TOOBIT_TOP_N。
    每项: {"symbol": "BTCUSDT", "last": 97000, "percentage": 2.5, "quoteVolume": 123456789, "baseVolume": 1200}
    """
    ex = ccxt.toobit({"enableRateLimit": True, "timeout": 15000})
    ex.load_markets()
    tickers = ex.fetch_tickers()
    rows = []
    for sym, t in tickers.items():
        if not t:
            continue
        quote_vol = float(t.get("quoteVolume") or 0)
        if quote_vol <= 0:
            continue
        last = float(t.get("last") or t.get("close") or 0)
        pct = t.get("percentage")
        if pct is not None:
            pct = float(pct)
        else:
            pct = None
        base_vol = float(t.get("baseVolume") or 0)
        rows.append({
            "symbol": sym,
            "last": last,
            "percentage": pct,
            "quoteVolume": quote_vol,
            "baseVolume": base_vol,
        })
    rows.sort(key=lambda x: x["quoteVolume"], reverse=True)
    return rows[:TOOBIT_TOP_N]


def _build_toobit_24h_card(rows: list[dict]) -> dict:
    """根据 Top20 列表构建飞书卡片：分行展示、涨跌上色、分组更清晰。"""
    from datetime import datetime

    def _format_row(i: int, r: dict) -> str:
        sym_raw = (r.get("symbol") or "").strip()
        if sym_raw.endswith("USDT"):
            sym = sym_raw[:-4].replace("/", "").strip() or sym_raw
        else:
            sym = sym_raw.replace("/USDT", "").replace(":USDT", "").strip() or sym_raw
        last = r.get("last") or 0
        pct = r.get("percentage")
        if pct is not None:
            pct_val = float(pct)
            # 保留最多6位小数，去掉末尾无用的0；绿涨红跌（font 双引号+颜色名，部分环境需 markdown 才生效）
            raw = f"{pct_val:+.6f}".rstrip("0").rstrip(".") + "%"
            if pct_val >= 0:
                pct_str = "📈 <font color=\"green\">" + raw + "</font>"
            else:
                pct_str = "📉 <font color=\"red\">" + raw + "</font>"
        else:
            pct_str = "-"
        vol = r.get("quoteVolume") or 0
        if vol >= 1e9:
            vol_str = f"{vol / 1e9:.2f}B"
        elif vol >= 1e6:
            vol_str = f"{vol / 1e6:.2f}M"
        elif vol >= 1e3:
            vol_str = f"{vol / 1e3:.2f}K"
        else:
            vol_str = f"{vol:.0f}"
        # 价格按大小决定小数位
        if last >= 1000:
            price_str = f"{last:,.2f}"
        elif last >= 1:
            price_str = f"{last:,.4f}"
        else:
            price_str = f"{last:.6f}"
        return f"**#{i}** {sym}  ·  **{price_str}**  ·  {pct_str}  ·  {vol_str}"

    elements = [
        {
            "tag": "div",
            "text": {
                "tag": "lark_md",
                "content": "按 **24h 成交量(USDT)** 排序 · 数据来源 **Toobit**",
            },
        },
        {"tag": "hr"},
    ]
    # 分两组：Top 1-10 与 Top 11-20，每组一个小标题
    for group_name, start, end in [("🔝 Top 1-10", 0, 10), ("📊 Top 11-20", 10, 20)]:
        if start >= len(rows):
            break
        elements.append({
            "tag": "div",
            "text": {"tag": "lark_md", "content": f"**{group_name}**"},
        })
        for idx in range(start, min(end, len(rows))):
            r = rows[idx]
            i = idx + 1
            elements.append({
                "tag": "div",
                "text": {"tag": "lark_md", "content": _format_row(i, r)},
            })
        elements.append({"tag": "hr"})
    elements.append({
        "tag": "div",
        "text": {
            "tag": "plain_text",
            "content": f"🕐 {datetime.now().strftime('%Y-%m-%d %H:%M')} · 每 5 分钟更新",
            "lines": 1,
        },
    })
    return {
        "config": {"wide_screen_mode": True},
        "header": {
            "title": {"tag": "plain_text", "content": "📈 Toobit 24h 成交量 Top20", "lines": 1},
            "template": "blue",
        },
        "elements": elements,
    }


def run_toobit_24h_push() -> None:
    """执行一次：拉取 Toobit 24h Top20，推送到配置的飞书群。"""
    chat_id = (FEISHU_TOOBIT_24H_CHAT_ID or "").strip()
    if not chat_id:
        logger.debug("TOOBIT_24H_CHAT_ID not set, skip push")
        return
    try:
        rows = _get_toobit_tickers_top_by_volume()
        if not rows:
            logger.warning("Toobit 24h: no tickers returned")
            return
        card = _build_toobit_24h_card(rows)
        mid = send_card_message(chat_id, card)
        if mid:
            logger.info("Toobit 24h Top20 pushed to %s, message_id=%s", chat_id[:20], mid)
        else:
            logger.warning("Toobit 24h push failed (send_card_message returned None)")
    except Exception as e:
        logger.exception("Toobit 24h push error: %s", e)
