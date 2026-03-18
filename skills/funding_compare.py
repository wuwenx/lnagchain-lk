"""
资金费率监控 skill：获取 Toobit 与 Binance 全市场资金费率，对比差值并以卡片形式回复。
触发：获取资金费率情况、资金费率监控、/funding_compare
返回 (reply_text, reply_card)，卡片由 LangGraph 统一发送。
"""
import logging

from lark_client import build_funding_compare_card
from tools.funding_rate import get_funding_compare_toobit_binance

logger = logging.getLogger(__name__)


def run_funding_compare(
    user_message: str,
    *,
    document_context: str | None = None,
    chat_id: str = "",
    **kwargs,
) -> tuple[str, dict | None]:
    """
    并行拉取 Toobit / Binance 全市场资金费率，对比后返回 (空文案, 卡片)。
    失败时返回 (错误说明, None)。
    """
    try:
        rows = get_funding_compare_toobit_binance()
        if not rows:
            return ("Toobit 或 Binance 未拉取到共同标的资金费率，请稍后重试。", None)
        card = build_funding_compare_card(rows)
        return ("", card)
    except Exception as e:
        logger.exception("funding_compare error: %s", e)
        return (f"资金费率对比失败: {e}", None)


class FundingCompareSkill:
    id = "funding_compare"
    name = "资金费率监控"
    description = "获取 Toobit 与 Binance 全市场资金费率并对比差值，以卡片形式回复"
    trigger_commands = [
        "获取资金费率情况",
        "获取资金费率监控",
        "资金费率监控",
        "/funding_compare",
        "funding_compare",
    ]

    def run(
        self,
        user_message: str,
        *,
        document_context: str | None = None,
        chat_id: str = "",
        **kwargs,
    ) -> tuple[str, dict | None]:
        return run_funding_compare(
            user_message,
            document_context=document_context,
            chat_id=chat_id,
            **kwargs,
        )


funding_compare_skill = FundingCompareSkill()
