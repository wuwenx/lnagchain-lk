"""
飞书文档读取：从消息中解析飞书文档/知识库链接，拉取正文供 LangChain 使用
支持：docx 文档链接、Wiki（知识库）链接
"""
import re
import logging

from lark_oapi.api.docx.v1.model.raw_content_document_request import RawContentDocumentRequest
from lark_oapi.api.wiki.v2.model.get_node_space_request import GetNodeSpaceRequest

from lark_client import get_client

logger = logging.getLogger(__name__)

# 飞书/ Lark 文档链接：/docx/DocumentId 或 /docs/DocumentId
DOC_LINK_PATTERN = re.compile(
    r"https?://[^\s]+?(?:feishu\.cn|larksuite\.com)/(?:docx|docs)/([A-Za-z0-9_-]{20,})",
    re.IGNORECASE,
)

# 飞书/ Lark 知识库（Wiki）链接：/wiki/NodeToken（含子域名如 xxx.sg.larksuite.com）
WIKI_LINK_PATTERN = re.compile(
    r"https?://[^\s]+?(?:feishu\.cn|larksuite\.com)/wiki/([A-Za-z0-9_-]{20,})",
    re.IGNORECASE,
)


def extract_document_ids(text: str) -> list[str]:
    """从文本中提取飞书文档链接的 document_id 列表（去重）。"""
    if not text or not text.strip():
        return []
    seen = set()
    ids = []
    for m in DOC_LINK_PATTERN.finditer(text):
        doc_id = m.group(1).strip()
        if doc_id and doc_id not in seen:
            seen.add(doc_id)
            ids.append(doc_id)
    return ids


def extract_wiki_node_tokens(text: str) -> list[str]:
    """从文本中提取飞书知识库（Wiki）链接的 node_token 列表（去重）。"""
    if not text or not text.strip():
        return []
    seen = set()
    tokens = []
    for m in WIKI_LINK_PATTERN.finditer(text):
        token = m.group(1).strip()
        if token and token not in seen:
            seen.add(token)
            tokens.append(token)
    return tokens


def fetch_wiki_node_content(node_token: str) -> str | None:
    """
    根据知识库节点 token 拉取节点对应文档的纯文本内容。
    若节点类型为 doc/docx，则用 docx raw_content 拉取；其他类型（如 sheet、mindnote）暂不支持，返回 None。
    """
    try:
        req = GetNodeSpaceRequest.builder().token(node_token).build()
        resp = get_client().wiki.v2.space.get_node(req)
        if not resp.success():
            logger.warning(
                "wiki get_node failed: node_token=%s code=%s msg=%s",
                node_token,
                resp.code,
                resp.msg,
            )
            return None
        if not resp.data or not resp.data.node:
            return None
        node = resp.data.node
        obj_token = getattr(node, "obj_token", None)
        obj_type = (getattr(node, "obj_type", None) or "").lower()
        if not obj_token:
            return None
        # 仅支持文档类型，用 docx 接口拉取正文
        if obj_type in ("doc", "docx", "1", "2"):
            return fetch_document_content(obj_token)
        logger.info("wiki node type %s not supported for content, node_token=%s", obj_type, node_token)
        return None
    except Exception as e:
        logger.exception("fetch_wiki_node_content error: node_token=%s err=%s", node_token, e)
        return None


def fetch_document_content(document_id: str) -> str | None:
    """
    根据 document_id 拉取文档纯文本内容（新版 docx）。
    无权限或非 docx 文档时返回 None。
    """
    try:
        req = (
            RawContentDocumentRequest.builder()
            .document_id(document_id)
            .build()
        )
        resp = get_client().docx.v1.document.raw_content(req)
        if not resp.success():
            logger.warning("doc raw_content failed: document_id=%s code=%s msg=%s", document_id, resp.code, resp.msg)
            return None
        content = getattr(resp.data, "content", None) if resp.data else None
        return (content or "").strip() or None
    except Exception as e:
        logger.exception("fetch_document_content error: document_id=%s err=%s", document_id, e)
        return None


def fetch_documents_content(document_ids: list[str], wiki_tokens: list[str] | None = None, max_chars: int = 50000) -> str:
    """
    批量拉取文档/知识库正文，拼成一段文本（用于作为上下文传给 LLM）。
    支持直接文档 ID 与 Wiki node_token；单篇超过 max_chars 会截断；多篇之间用分隔符。
    """
    if not document_ids and not wiki_tokens:
        return ""
    parts = []
    total = 0
    for doc_id in document_ids or []:
        if total >= max_chars:
            break
        raw = fetch_document_content(doc_id)
        if not raw:
            continue
        if len(raw) + total > max_chars:
            raw = raw[: max_chars - total]
        parts.append(f"【文档 {doc_id}】\n{raw}")
        total += len(raw)
    for node_token in wiki_tokens or []:
        if total >= max_chars:
            break
        raw = fetch_wiki_node_content(node_token)
        if not raw:
            continue
        if len(raw) + total > max_chars:
            raw = raw[: max_chars - total]
        parts.append(f"【知识库文档 {node_token}】\n{raw}")
        total += len(raw)
    return "\n\n---\n\n".join(parts) if parts else ""
