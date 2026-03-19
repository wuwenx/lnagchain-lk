"""
/code skill：在 Lark 群内触发本地代码修改助手。
仅当 chat_id 等于配置中的 FEISHU_CODE_AGENT_CHAT_ID 时执行，其他群提示未开放。
"""
import logging

from config import FEISHU_CODE_AGENT_CHAT_ID

logger = logging.getLogger(__name__)

# 触发词（用于 strip 时匹配）
_TRIGGER_COMMANDS = ["/code", "代码修改", "改代码"]
import re

# @ 提及格式：<at ...>...</at> 或任意位置的 @xxx
_AT_TAG_PATTERN = re.compile(r"<at[^>]*>[^<]*</at>\s*", re.IGNORECASE)
_AT_MENTION_ANY = re.compile(r"@\S+", re.IGNORECASE)


def _strip_trigger(text: str) -> str:
    """去掉触发词（可在句中）和 @ 提及，返回作为代码助手任务的文案。"""
    t = (text or "").strip()
    # 去掉 <at> 标签与任意 @xxx
    t = _AT_TAG_PATTERN.sub("", t)
    t = _AT_MENTION_ANY.sub("", t)
    t = re.sub(r"\s+", " ", t).strip()
    # 去掉任一处出现的触发词（保留前后内容）
    for cmd in _TRIGGER_COMMANDS:
        if not cmd:
            continue
        idx = t.lower().find(cmd.lower())
        if idx >= 0:
            t = (t[:idx] + t[idx + len(cmd) :]).strip()
    return re.sub(r"\s+", " ", t).strip()


def run_code_agent(
    user_message: str,
    *,
    document_context: str | None = None,
    chat_id: str = "",
    **kwargs,
) -> str:
    """仅白名单群可执行代码修改，其余群返回提示。"""
    if not FEISHU_CODE_AGENT_CHAT_ID:
        return "代码修改功能未配置白名单群（FEISHU_CODE_AGENT_CHAT_ID），暂不可用。"
    if (chat_id or "").strip() != FEISHU_CODE_AGENT_CHAT_ID.strip():
        return "仅支持在指定群使用代码修改功能，当前群未开放。"
    task = _strip_trigger(user_message)
    if not task:
        return (
            "请说明要执行的代码操作，例如：\n"
            "• 请读取 config.py 并告诉我 OPENAI_MODEL 的默认值\n"
            "• 在 hello.py 里添加一个斐波那契函数\n"
            "• 运行 pytest 并告诉我结果"
        )
    try:
        from code_agent import run as code_agent_run
        return code_agent_run(task) or "代码助手未返回内容，请重试。"
    except Exception as e:
        logger.exception("code_agent run error")
        return f"执行代码修改时出错: {str(e)}"


class CodeAgentSkill:
    id = "code_agent"
    name = "代码修改"
    description = "在指定群内通过自然语言查看/修改本地项目代码（仅白名单群可用）"
    trigger_commands = _TRIGGER_COMMANDS.copy()

    def run(
        self,
        user_message: str,
        *,
        document_context: str | None = None,
        chat_id: str = "",
        **kwargs,
    ) -> str:
        return run_code_agent(
            user_message,
            document_context=document_context,
            chat_id=chat_id,
            **kwargs,
        )


code_agent_skill = CodeAgentSkill()
