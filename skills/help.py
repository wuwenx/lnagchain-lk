"""
/help skill：列出所有可用技能及触发词，方便用户发现能力。
"""
import logging

logger = logging.getLogger(__name__)


def run_help(
    user_message: str,
    *,
    document_context: str | None = None,
    chat_id: str = "",
    **kwargs,
) -> str:
    """生成帮助文案：所有已注册 skill 的名称、描述与触发命令。"""
    from skills import get_all_skills

    skills = get_all_skills()
    if not skills:
        return "当前没有已注册的技能。"
    lines = ["**可用命令与技能**\n"]
    for s in skills:
        triggers = "、".join(s.trigger_commands)
        lines.append(f"• **{s.name}**：{s.description}")
        lines.append(f"  触发：{triggers}\n")
    return "\n".join(lines).strip()


class HelpSkill:
    id = "help"
    name = "帮助"
    description = "查看本帮助，列出所有可用命令与技能"
    trigger_commands = ["/help", "帮助", "help"]

    def run(
        self,
        user_message: str,
        *,
        document_context: str | None = None,
        chat_id: str = "",
        **kwargs,
    ) -> str:
        return run_help(
            user_message,
            document_context=document_context,
            chat_id=chat_id,
            **kwargs,
        )


help_skill = HelpSkill()
