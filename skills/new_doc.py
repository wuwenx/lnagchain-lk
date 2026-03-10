"""
新建 Lark 文档 skill：根据用户描述直接创建飞书云文档并返回链接。
触发：/新建文档、新建lark文档、创建飞书文档、帮我新建...文档 等
"""
import logging

from langchain_agent import reply as langchain_reply
from lark_client import append_document_body, create_lark_document

logger = logging.getLogger(__name__)


def _extract_title_and_body(user_message: str) -> tuple[str, str]:
    """用 LLM 从用户消息中生成文档标题和可选正文，返回 (title, body)。"""
    prompt = (
        "用户请求：\n"
        f"{user_message}\n\n"
        "请直接给出两段内容，不要其他解释：\n"
        "第一行：文档标题（仅一行，用于新建飞书文档）。\n"
        "第二行开始：文档正文（可多行，用于复制到文档；若不需要正文可写「无」）。"
    )
    out = langchain_reply(prompt, document_context=None)
    if not out or not out.strip():
        return "未命名文档", ""
    lines = out.strip().split("\n")
    title = lines[0].strip() or "未命名文档"
    body = "\n".join(lines[1:]).strip() if len(lines) > 1 else ""
    if body.lower() in ("无", "无。", "无。"):
        body = ""
    return title, body


def run_new_doc(
    user_message: str,
    *,
    document_context: str | None = None,
    chat_id: str = "",
    **kwargs,
) -> str:
    """执行新建文档：LLM 生成标题与正文 → 创建文档 → 将正文写入文档 → 返回链接。"""
    title, body = _extract_title_and_body(user_message)
    doc_id, url = create_lark_document(title)
    if not doc_id:
        return "创建文档失败，请检查应用是否有云文档创建权限（如 docx:document 的写权限）或 folder_token 配置。"
    if url:
        msg = f"已创建文档 **《{title}》**\n链接：{url}"
    else:
        msg = f"已创建文档 **《{title}》**\ndocument_id：`{doc_id}`\n请在飞书云文档中搜索该标题打开。"
    if body:
        if append_document_body(doc_id, body):
            msg += "\n\n正文已写入文档，可直接打开查看。"
        else:
            msg += "\n\n正文写入失败，可将以下内容手动复制到文档中：\n\n" + body
    return msg


class NewDocSkill:
    id = "new_doc"
    name = "新建 Lark 文档"
    description = "根据描述直接创建飞书云文档并返回链接"
    trigger_commands = [
        "/新建文档",
        "新建lark文档",
        "新建飞书文档",
        "创建lark文档",
        "创建飞书文档",
        "帮我新建一个",
        "帮我创建一份",
    ]

    def run(
        self,
        user_message: str,
        *,
        document_context: str | None = None,
        chat_id: str = "",
        **kwargs,
    ) -> str:
        return run_new_doc(
            user_message,
            document_context=document_context,
            chat_id=chat_id,
            **kwargs,
        )


new_doc_skill = NewDocSkill()
