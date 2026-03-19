"""
Skills 注册与解析：根据用户消息中的命令/关键词触发对应 skill，未命中则走默认对话。
"""
import logging
from typing import Protocol

logger = logging.getLogger(__name__)


class Skill(Protocol):
    """Skill 协议：id、名称、描述、触发命令、run 方法。"""

    id: str
    name: str
    description: str
    trigger_commands: list[str]

    def run(
        self,
        user_message: str,
        *,
        document_context: str | None = None,
        chat_id: str = "",
        **kwargs,
    ) -> str:
        ...


# 注册表：所有已注册的 skill 实例
_REGISTRY: list[Skill] = []


def register(skill: Skill) -> None:
    """注册一个 skill。"""
    _REGISTRY.append(skill)
    logger.info("registered skill: %s (trigger: %s)", skill.id, skill.trigger_commands)


def resolve_skill(text: str) -> Skill | None:
    """
    根据用户消息解析是否命中某个 skill。
    规则：消息去掉首尾空白后，若以某 skill 的任一 trigger_command 开头（不区分大小写），则命中该 skill。
    """
    if not text or not text.strip():
        return None
    stripped = text.strip()
    for skill in _REGISTRY:
        for cmd in skill.trigger_commands:
            if not cmd:
                continue
            if stripped.lower().startswith(cmd.lower()):
                return skill
    return None


def resolve_skill_by_keywords(text: str) -> Skill | None:
    """
    按「消息包含关键词」匹配 skill，用于不依赖前缀的意图（如「对比 toobit 和 Binance 的 btc 流动性深度」）。
    仅对在 KEYWORD_SKILLS 中注册的 skill 做包含匹配，命中则返回该 skill，便于在 API 不支持 tool_calls 时仍能拿数据。
    """
    if not text or not text.strip():
        return None
    t = text.strip().lower()
    for skill, keywords in _KEYWORD_SKILLS:
        for kw in keywords:
            if kw and kw.lower() in t:
                return skill
    return None


# (skill, keywords) 列表：消息中包含任一词即命中该 skill
_KEYWORD_SKILLS: list[tuple[Skill, list[str]]] = []


def get_all_skills() -> list[Skill]:
    """返回所有已注册的 skills（可用于 /help 等）。"""
    return list(_REGISTRY)


def _register_builtin_skills() -> None:
    from skills.btc import btc_skill
    from skills.fetch import fetch_skill
    from skills.funding_compare import funding_compare_skill
    from skills.funding_rate import funding_rate_skill
    from skills.help import help_skill
    from skills.jks import jks_skill
    from skills.liquidity_depth import liquidity_depth_skill
    from skills.new_doc import new_doc_skill
    from skills.rank import rank_skill
    from skills.search_doc import search_doc_skill

    register(help_skill)
    register(search_doc_skill)
    register(btc_skill)
    register(rank_skill)
    register(fetch_skill)
    register(new_doc_skill)
    register(funding_compare_skill)  # 放在 funding_rate 前，使「资金费率监控」先命中
    register(funding_rate_skill)
    register(liquidity_depth_skill)
    register(jks_skill)
    # 流动性深度：消息中包含以下关键词时也走 skill，不依赖模型 tool_calls（兼容 jeniya 等中转）
    _KEYWORD_SKILLS.append((liquidity_depth_skill, ["流动性深度", "滑点", "深度对比", "订单簿"]))


# 导入时自动注册内置 skills
_register_builtin_skills()
