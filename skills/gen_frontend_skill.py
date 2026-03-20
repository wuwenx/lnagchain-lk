"""
「生成前端」skill：从 Lark 需求文档 + Apifox 接口文档生成前端页面、路由、菜单。
仅白名单群可触发（与 code_agent 相同）；需在消息中提供飞书文档链接，并已配置 APIFOX_ACCESS_TOKEN、APIFOX_PROJECT_ID。
"""
import json
import logging
import re

from config import FEISHU_CODE_AGENT_CHAT_ID
from feishu_doc import extract_document_ids, extract_wiki_node_tokens, fetch_documents_content
from tools.apifox_client import export_openapi

logger = logging.getLogger(__name__)

_TRIGGER_COMMANDS = ["/生成前端", "生成前端", "根据文档生成前端"]
_AT_TAG_PATTERN = re.compile(r"<at[^>]*>[^<]*</at>\s*", re.IGNORECASE)
_AT_MENTION_ANY = re.compile(r"@\S+", re.IGNORECASE)
# 需求+OAS 总长度上限，避免超出模型上下文
_MAX_CONTEXT_CHARS = 80000
_OAS_MAX_CHARS = 50000


def _strip_trigger_and_mention(text: str) -> str:
    t = (text or "").strip()
    t = _AT_TAG_PATTERN.sub("", t)
    t = _AT_MENTION_ANY.sub("", t)
    t = re.sub(r"\s+", " ", t).strip()
    for cmd in _TRIGGER_COMMANDS:
        if not cmd:
            continue
        idx = t.lower().find(cmd.lower())
        if idx >= 0:
            t = (t[:idx] + t[idx + len(cmd) :]).strip()
    return re.sub(r"\s+", " ", t).strip()


def run_gen_frontend(
    user_message: str,
    *,
    document_context: str | None = None,
    chat_id: str = "",
    **kwargs,
) -> str:
    """拉取 Lark 文档 + Apifox OAS，组装上下文后调用 code_agent 生成前端。"""
    if not FEISHU_CODE_AGENT_CHAT_ID:
        return "生成前端功能未配置白名单群（FEISHU_CODE_AGENT_CHAT_ID），暂不可用。"
    if (chat_id or "").strip() != FEISHU_CODE_AGENT_CHAT_ID.strip():
        return "仅支持在指定群使用「生成前端」功能，当前群未开放。"

    doc_ids = extract_document_ids(user_message)
    wiki_tokens = extract_wiki_node_tokens(user_message)
    if not doc_ids and not wiki_tokens:
        # 若已有 document_context（如 handler 里根据链接预填的），可直接用
        if document_context and document_context.strip():
            lark_content = document_context.strip()
        else:
            return (
                "请在本条消息中附带**飞书文档链接**（需求文档）。\n"
                "例如：生成前端 https://xxx.feishu.cn/docx/xxxxx\n"
                "并确保已配置 APIFOX_ACCESS_TOKEN、APIFOX_PROJECT_ID（.env）。"
            )
    else:
        lark_content = fetch_documents_content(doc_ids, wiki_tokens, max_chars=_MAX_CONTEXT_CHARS - _OAS_MAX_CHARS)
        if not lark_content or not lark_content.strip():
            return "未能拉取到飞书文档内容，请检查链接与应用权限。"

    oas = export_openapi()
    if not oas:
        return (
            "未能从 Apifox 拉取接口文档。请确认已配置 APIFOX_ACCESS_TOKEN、APIFOX_PROJECT_ID，"
            "且项目 ID 正确、令牌有效。"
        )

    oas_str = json.dumps(oas, ensure_ascii=False, indent=0)
    if len(oas_str) > _OAS_MAX_CHARS:
        oas_str = oas_str[:_OAS_MAX_CHARS] + "\n... (已截断)"

    extra = _strip_trigger_and_mention(user_message)
    if extra and extra != user_message:
        extra_hint = f"\n\n用户补充说明：{extra}"
    else:
        extra_hint = ""

    prompt = f"""请根据以下**需求文档**和**接口文档（OpenAPI）**，在当前工作区中生成或补充前端：路由、菜单、页面组件。

要求：
1. 先用 read_local_file 查看项目现有路由、菜单、页面结构（如 router 配置、menu 配置、views 目录）。
2. 根据需求与接口按现有规范生成：新增路由、菜单项、页面组件（列表/表单/详情等），并调用接口。
3. 使用 write_local_file 或 replace_code_block 写入或修改文件，不要遗漏已有重要逻辑。
4. 路径、组件命名与项目现有风格保持一致。

【需求文档】
{lark_content}
【接口文档 OpenAPI】
{oas_str}
{extra_hint}

请开始执行：先读取相关现有文件，再生成并写入。"""

    try:
        from code_agent import run as code_agent_run
        return code_agent_run(prompt) or "生成前端已完成，未返回总结。请查看工作区文件变更。"
    except Exception as e:
        logger.exception("gen_frontend code_agent run error")
        return f"生成前端时出错: {str(e)}"


class GenFrontendSkill:
    id = "gen_frontend"
    name = "生成前端"
    description = "根据飞书需求文档 + Apifox 接口文档生成前端页面、路由、菜单（仅白名单群，需提供文档链接与 Apifox 配置）"
    trigger_commands = _TRIGGER_COMMANDS.copy()

    def run(
        self,
        user_message: str,
        *,
        document_context: str | None = None,
        chat_id: str = "",
        **kwargs,
    ) -> str:
        return run_gen_frontend(
            user_message,
            document_context=document_context,
            chat_id=chat_id,
            **kwargs,
        )


gen_frontend_skill = GenFrontendSkill()
