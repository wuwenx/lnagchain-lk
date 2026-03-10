"""
/btc skill：获取当前 BTC 价格（使用 CoinGecko 公开 API，无需 key）。
"""
import json
import logging
import ssl
import urllib.request

import certifi

logger = logging.getLogger(__name__)

COINGECKO_URL = "https://api.coingecko.com/api/v3/simple/price?ids=bitcoin&vs_currencies=usd&include_24hr_change=true"


def _fetch_btc_price() -> dict | None:
    try:
        ctx = ssl.create_default_context(cafile=certifi.where())
        req = urllib.request.Request(COINGECKO_URL, headers={"User-Agent": "FeishuBot/1.0"})
        with urllib.request.urlopen(req, timeout=10, context=ctx) as resp:
            data = json.loads(resp.read().decode())
            return data.get("bitcoin")
    except Exception as e:
        logger.exception("fetch btc price error: %s", e)
        return None


def run_btc(
    user_message: str,
    *,
    document_context: str | None = None,
    chat_id: str = "",
    **kwargs,
) -> str:
    """执行 /btc：返回当前 BTC 价格（USD）及 24h 涨跌幅。"""
    raw = _fetch_btc_price()
    if not raw:
        return "暂时无法获取 BTC 价格，请稍后再试。"
    price = raw.get("usd")
    change_24h = raw.get("usd_24h_change")
    if price is None:
        return "暂时无法获取 BTC 价格，请稍后再试。"
    line = f"BTC 当前价格：**${price:,.2f}**（USD）"
    if change_24h is not None:
        sign = "📈" if change_24h >= 0 else "📉"
        line += f"\n24h 涨跌：{sign} {change_24h:+.2f}%"
    return line


class BtcSkill:
    id = "btc"
    name = "BTC 价格"
    description = "获取当前比特币（BTC）价格（USD）及 24h 涨跌"
    trigger_commands = ["/btc", "btc价格", "比特币价格"]

    def run(
        self,
        user_message: str,
        *,
        document_context: str | None = None,
        chat_id: str = "",
        **kwargs,
    ) -> str:
        return run_btc(user_message, document_context=document_context, chat_id=chat_id, **kwargs)


btc_skill = BtcSkill()
