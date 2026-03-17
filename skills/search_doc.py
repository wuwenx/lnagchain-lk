"""
/search skill：基于飞书开放平台「搜索文档与知识库」接口搜索企业内文档/知识库，再交由大模型总结汇总。
触发：/search 关键词、搜索 xxx
"""
import logging

from langchain_agent import reply as langchain_reply
from lark_client import search_doc_wiki

logger = logging.getLogger(__name__)

# 搜索条数上限（供 LLM 总结）
DEFAULT_PAGE_SIZE = 10


def _format_search_results(items: list[dict]) -> str:
    """将搜索结果格式化为一段文本，便于 LLM 阅读与汇总。"""
    if not items:
        return "（未找到匹配的文档或知识库条目）"
    lines = []
    for i, it in enumerate(items, 1):
        title = it.get("title") or "(无标题)"
        summary = (it.get("summary") or "").strip()
        url = (it.get("url") or "").strip()
        block = f"{i}. **{title}**\n   {summary or '（无摘要）'}"
        if url:
            block += f"\n   链接：{url}"
        lines.append(block)
    return "\n\n".join(lines)


def run_search_doc(
    user_message: str,
    *,
    document_context: str | None = None,
    chat_id: str = "",
    page_size: int = DEFAULT_PAGE_SIZE,
    **kwargs,
) -> str:
    """
    执行文档/知识库搜索：调用飞书 search v2 doc_wiki → 格式化为文本 → 大模型总结汇总。
    user_message 应为去掉触发词后的查询关键词（如「/search 产品需求」→ 「产品需求」）。
    """
    query = (user_message or "").strip()
    if not query:
        return "请提供搜索关键词，例如：/search 产品需求"
    items, api_error = search_doc_wiki(query, page_size=page_size)
    if api_error:
        logger.info("search_doc skill: search_doc_wiki API error for query=%r: %s", query, api_error)
        return (
            f"文档/知识库搜索**接口调用失败**：{api_error}\n\n"
            "请按下列项排查：\n"
            "1. **飞书开放平台** → 应用 → **权限管理**：为应用开启「搜索」或「云文档/知识库」相关权限（如：查看云空间文件、查看文档、查看知识库等），并让管理员重新审批/发布。\n"
            "2. **国际版 Lark**：在 Developer Console → 应用 → **Permissions & Scopes** 中勾选 docx、wiki 相关只读/搜索权限并重新发布。\n"
            "3. 查看服务端日志中的 `search_doc_wiki API failed` 的 code/msg，对照[飞书错误码](https://open.feishu.cn/document/ukTMukTMukTM/ugjM14COyUjL4ITN)排查。"
        )
    if not items:
        logger.info("search_doc skill: search_doc_wiki returned 0 items (API 成功) for query=%r", query)
        return (
            f"未找到与「{query}」相关的文档或知识库内容。\n\n"
            "可能原因：企业内暂无标题/正文匹配该关键词的文档或知识库；或应用暂无权限搜索到这些资源（需在开放平台为应用开启文档与知识库的读/搜索权限）。可尝试其他关键词或确认权限后重试。"
        )
    results_text = _format_search_results(items)
    prompt = (
        "以下是对企业内飞书文档与知识库的搜索结果（标题、摘要与链接）。"
        "请根据用户的问题或关键词，对搜索结果做简洁的总结与汇总：提炼要点、可注明来源序号或链接，便于用户快速了解。"
        "若结果与问题关联不强，可简要说明并建议更精确的关键词。\n\n"
        "【搜索结果】\n"
        f"{results_text}\n\n"
        "【用户搜索/问题】\n"
        f"{query}"
    )
    reply_text, _ = langchain_reply(prompt, document_context=None)
    return reply_text or "未能生成汇总，请稍后重试。"


class SearchDocSkill:
    id = "search_doc"
    name = "文档/知识库搜索"
    description = "搜索企业内飞书文档与知识库并总结汇总（/search 关键词）"
    trigger_commands = ["/search", "/serch", "搜索"]

    def run(
        self,
        user_message: str,
        *,
        document_context: str | None = None,
        chat_id: str = "",
        **kwargs,
    ) -> str:
        # 去掉触发词，取关键词
        text = (user_message or "").strip()
        for cmd in self.trigger_commands:
            if text.lower().startswith(cmd.lower()):
                text = text[len(cmd) :].strip()
                break
        return run_search_doc(
            text,
            document_context=document_context,
            chat_id=chat_id,
            **kwargs,
        )


search_doc_skill = SearchDocSkill()
