"""
飞书文档读取：从消息中解析飞书文档/知识库链接，拉取正文供 LangChain 使用
支持：docx 文档链接、Wiki（知识库）链接
- 纯文本：document.raw_content（与历史行为一致）
- 多模态：列出 docx 块，提取图片块 token，经 drive 素材接口下载图片字节，供与正文一并传入 vision 模型
"""
import re
import logging

from lark_oapi.api.docx.v1.model.list_document_block_request import ListDocumentBlockRequest
from lark_oapi.api.docx.v1.model.raw_content_document_request import RawContentDocumentRequest
from lark_oapi.api.wiki.v2.model.get_node_space_request import GetNodeSpaceRequest

from config import (
    DOCX_MAX_IMAGES,
    FEISHU_DOC_FETCH_IMAGES,
    VISION_MAX_IMAGE_BYTES,
    VISION_MAX_IMAGES,
)
from lark_client import (
    batch_get_media_tmp_download_urls,
    download_http_bytes,
    get_client,
)

logger = logging.getLogger(__name__)

# 飞书 docx：图片 Block，见开放平台 block 数据结构
BLOCK_TYPE_IMAGE = 27

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


def _get_block_type(block) -> int:
    try:
        if isinstance(block, dict):
            return int(block.get("block_type") or 0)
        return int(getattr(block, "block_type", None) or 0)
    except (TypeError, ValueError):
        return 0


def _get_image_token(block) -> str | None:
    im = block.get("image") if isinstance(block, dict) else getattr(block, "image", None)
    if im is None:
        return None
    if isinstance(im, dict):
        t = im.get("token")
    else:
        t = getattr(im, "token", None)
    return str(t).strip() if t else None


def list_all_document_blocks(document_id: str) -> list:
    """分页拉取文档下全部块（扁平列表）。"""
    client = get_client()
    all_items: list = []
    page_token: str | None = None
    for _ in range(200):
        b = ListDocumentBlockRequest.builder().document_id(document_id).page_size(500)
        if page_token:
            b = b.page_token(page_token)
        req = b.build()
        resp = client.docx.v1.document_block.list(req)
        if not resp.success():
            logger.warning(
                "list document blocks failed: document_id=%s code=%s msg=%s",
                document_id,
                getattr(resp, "code", None),
                getattr(resp, "msg", None),
            )
            break
        data = resp.data
        if not data:
            break
        items = getattr(data, "items", None) or []
        all_items.extend(items)
        next_token = getattr(data, "page_token", None)
        if isinstance(next_token, str) and next_token.strip():
            page_token = next_token
            continue
        break
    return all_items


def collect_image_tokens_from_blocks(blocks: list) -> list[str]:
    """从块列表中按文档顺序收集图片素材 token（去重）。"""
    seen: set[str] = set()
    out: list[str] = []
    for block in blocks:
        if _get_block_type(block) != BLOCK_TYPE_IMAGE:
            continue
        tok = _get_image_token(block)
        if not tok or tok in seen:
            continue
        seen.add(tok)
        out.append(tok)
    return out


def fetch_document_image_bytes(document_id: str, max_images: int) -> list[bytes]:
    """
    拉取某 docx 文档内嵌图片的二进制（最多 max_images 张）。
    依赖 list blocks + medias/batch_get_tmp_download_url。
    """
    if max_images <= 0 or not FEISHU_DOC_FETCH_IMAGES:
        return []
    blocks = list_all_document_blocks(document_id)
    tokens = collect_image_tokens_from_blocks(blocks)[:max_images]
    if not tokens:
        return []
    result: list[bytes] = []
    for i in range(0, len(tokens), 5):
        batch = tokens[i : i + 5]
        url_map = batch_get_media_tmp_download_urls(batch)
        for tok in batch:
            url = url_map.get(tok)
            if not url:
                logger.warning("no tmp url for image token %s", tok[:40])
                continue
            data = download_http_bytes(url)
            if not data:
                continue
            if len(data) > VISION_MAX_IMAGE_BYTES:
                logger.warning("skip doc image too large: %s bytes", len(data))
                continue
            result.append(data)
            if len(result) >= max_images:
                return result
    return result


def resolve_wiki_object_token_for_doc_node(node_token: str) -> str | None:
    """知识库节点指向 doc/docx 时返回 obj_token，否则 None。"""
    try:
        req = GetNodeSpaceRequest.builder().token(node_token).build()
        resp = get_client().wiki.v2.space.get_node(req)
        if not resp.success() or not resp.data or not resp.data.node:
            return None
        node = resp.data.node
        obj_token = getattr(node, "obj_token", None)
        obj_type = (getattr(node, "obj_type", None) or "").lower()
        if not obj_token:
            return None
        if obj_type in ("doc", "docx", "1", "2"):
            return str(obj_token).strip()
        return None
    except Exception as e:
        logger.exception("resolve_wiki_object_token error: %s", e)
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


def fetch_wiki_node_content(node_token: str) -> str | None:
    """
    根据知识库节点 token 拉取节点对应文档的纯文本内容。
    若节点类型为 doc/docx，则用 docx raw_content 拉取；其他类型（如 sheet、mindnote）暂不支持，返回 None。
    """
    oid = resolve_wiki_object_token_for_doc_node(node_token)
    if not oid:
        return None
    return fetch_document_content(oid)


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


def fetch_documents_images_bytes(
    document_ids: list[str],
    wiki_tokens: list[str] | None = None,
    *,
    max_total: int | None = None,
) -> list[bytes]:
    """
    拉取多篇文档中的内嵌图片（顺序：按 document_ids 顺序，再 wiki 各篇）。
    总数不超过 max_total（默认 min(DOCX_MAX_IMAGES, VISION_MAX_IMAGES)）。
    """
    if not FEISHU_DOC_FETCH_IMAGES:
        return []
    cap = max_total if max_total is not None else min(DOCX_MAX_IMAGES, VISION_MAX_IMAGES)
    if cap <= 0:
        return []
    out: list[bytes] = []
    for doc_id in document_ids or []:
        if len(out) >= cap:
            break
        need = cap - len(out)
        out.extend(fetch_document_image_bytes(doc_id, need))
    for node_token in wiki_tokens or []:
        if len(out) >= cap:
            break
        oid = resolve_wiki_object_token_for_doc_node(node_token)
        if not oid:
            continue
        need = cap - len(out)
        out.extend(fetch_document_image_bytes(oid, need))
    return out[:cap]


def fetch_documents_content_and_images(
    document_ids: list[str],
    wiki_tokens: list[str] | None = None,
    max_chars: int = 50000,
    *,
    max_images: int | None = None,
) -> tuple[str, list[bytes]]:
    """
    同时拉取正文（与 fetch_documents_content 一致）与文档内图片字节。
    返回 (纯文本上下文, 图片列表)。
    """
    text = fetch_documents_content(document_ids, wiki_tokens=wiki_tokens, max_chars=max_chars)
    cap = max_images if max_images is not None else min(DOCX_MAX_IMAGES, VISION_MAX_IMAGES)
    images = fetch_documents_images_bytes(document_ids, wiki_tokens, max_total=cap)
    return text, images
