"""
资金费率 skill：命令式查询某交易所某标的的资金费率。
触发：/资金费率、资金费率
示例：/资金费率 binance BTC、资金费率 okx ETH
底层使用 tools.funding_rate.get_funding_rate（ccxt），与 Agent 工具一致。
"""
import logging

from tools.funding_rate import get_funding_rate

logger = logging.getLogger(__name__)


def run_funding_rate(
    user_message: str,
    *,
    document_context: str | None = None,
    chat_id: str = "",
    **kwargs,
) -> str:
    """解析「资金费率 exchange symbol」并返回资金费率文本。"""
    t = (user_message or "").strip()
    for prefix in ("/资金费率", "资金费率", "/funding", "funding"):
        if t.lower().startswith(prefix):
            t = t[len(prefix) :].strip()
            break
    if not t:
        return (
            "用法：/资金费率 <交易所> <标的>\n"
            "示例：/资金费率 binance BTC、资金费率 okx ETH\n"
            "交易所支持：binance, okx, bybit, gateio, htx, bitget, mexc 等（小写）。"
        )
    parts = t.split()
    exchange_id = (parts[0] or "binance").strip().lower()
    symbol = (parts[1] if len(parts) > 1 else "BTC").strip().upper() or "BTC"
    return get_funding_rate(exchange_id=exchange_id, symbol=symbol)


class FundingRateSkill:
    id = "funding_rate"
    name = "资金费率"
    description = "查询某交易所永续合约资金费率（如 Binance BTC、OKX ETH）"
    trigger_commands = ["/资金费率", "资金费率", "/funding", "funding"]

    def run(
        self,
        user_message: str,
        *,
        document_context: str | None = None,
        chat_id: str = "",
        **kwargs,
    ) -> str:
        return run_funding_rate(
            user_message,
            document_context=document_context,
            chat_id=chat_id,
            **kwargs,
        )


funding_rate_skill = FundingRateSkill()
