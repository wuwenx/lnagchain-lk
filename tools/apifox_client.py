"""
Apifox 开放 API 客户端：导出项目的 OpenAPI 规范，供「生成前端」等流程使用。
文档：https://apifox-openapi.apifox.cn
"""
import json
import logging
import re
from typing import Any

import requests

from config import APIFOX_ACCESS_TOKEN, APIFOX_API_BASE, APIFOX_MODULE_ID, APIFOX_PROJECT_ID

logger = logging.getLogger(__name__)

EXPORT_OPENAPI_VERSION = "2024-03-28"


def export_openapi(
    project_id: str | None = None,
    module_id: int | str | None = None,
    oas_version: str = "3.1",
    export_format: str = "JSON",
    scope_type: str = "ALL",
    include_apifox_ext: bool = False,
    add_folders_to_tags: bool = False,
) -> dict[str, Any] | None:
    """
    调用 Apifox 开放 API 导出项目的 OpenAPI 规范。

    :param project_id: 项目 ID，不传则用 config.APIFOX_PROJECT_ID
    :param module_id: 模块 ID，不传则用 config.APIFOX_MODULE_ID 或导出默认模块
    :param oas_version: 2.0 | 3.0 | 3.1
    :param export_format: JSON | YAML
    :param scope_type: ALL | SELECTED_TAGS | SELECTED_FOLDERS | SELECTED_ENDPOINTS
    :param include_apifox_ext: 是否包含 x-apifox 扩展字段
    :param add_folders_to_tags: 是否将目录写入 tags，便于按「文件夹/业务」筛选接口
    :return: OpenAPI 规范 dict，失败返回 None
    """
    pid = (project_id or APIFOX_PROJECT_ID or "").strip()
    if not pid:
        logger.warning("Apifox export_openapi: project_id 未配置")
        return None
    token = (APIFOX_ACCESS_TOKEN or "").strip()
    if not token:
        logger.warning("Apifox export_openapi: APIFOX_ACCESS_TOKEN 未配置")
        return None

    url = f"{APIFOX_API_BASE or 'https://api.apifox.com'}/v1/projects/{pid}/export-openapi"
    headers = {
        "X-Apifox-Api-Version": EXPORT_OPENAPI_VERSION,
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }
    body = {
        "scope": {"type": scope_type},
        "options": {
            "includeApifoxExtensionProperties": include_apifox_ext,
            "addFoldersToTags": add_folders_to_tags,
        },
        "oasVersion": oas_version,
        "exportFormat": export_format,
    }
    mid = module_id if module_id is not None else APIFOX_MODULE_ID
    if mid is not None:
        try:
            body["moduleId"] = int(mid)
        except (TypeError, ValueError):
            pass

    try:
        resp = requests.post(url, headers=headers, json=body, timeout=60)
        resp.raise_for_status()
        data = resp.json()
        if isinstance(data, dict) and ("paths" in data or "openapi" in data):
            return data
        logger.warning("Apifox export_openapi: 响应格式异常 %s", type(data))
        return data if isinstance(data, dict) else None
    except requests.RequestException as e:
        logger.exception("Apifox export_openapi 请求失败: %s", e)
        return None
    except json.JSONDecodeError as e:
        logger.exception("Apifox export_openapi 响应非 JSON: %s", e)
        return None


_HTTP_METHODS = frozenset({"get", "post", "put", "patch", "delete", "head", "options"})


def collect_operations(oas: dict[str, Any]) -> list[dict[str, Any]]:
    """
    从 OpenAPI 文档中收集所有接口（路径 + 方法 + 摘要 + tags）。
    """
    out: list[dict[str, Any]] = []
    paths = oas.get("paths") or {}
    if not isinstance(paths, dict):
        return out
    for path, item in paths.items():
        if not isinstance(item, dict):
            continue
        for method, op in item.items():
            lm = method.lower()
            if lm.startswith("x-") or lm == "parameters":
                continue
            if lm not in _HTTP_METHODS:
                continue
            if not isinstance(op, dict):
                continue
            tags = op.get("tags")
            if not isinstance(tags, list):
                tags = []
            else:
                tags = [str(t) for t in tags if t is not None]
            out.append(
                {
                    "method": lm.upper(),
                    "path": path,
                    "summary": (op.get("summary") or "").strip() if isinstance(op.get("summary"), str) else "",
                    "tags": tags,
                }
            )
    return out


def filter_operations(
    ops: list[dict[str, Any]],
    *,
    tag_query: str | None = None,
) -> list[dict[str, Any]]:
    """
    按关键词筛选：匹配任一 tag、summary 或 path（不区分大小写）。
    支持「整句里含路径」：若 tag_query 很长，只要文档 path 是该串的子串即命中（例如用户粘贴了 `/v1/foo 请问参数`）。
    """
    if not tag_query or not tag_query.strip():
        return ops
    q = tag_query.strip().lower()
    result: list[dict[str, Any]] = []
    for o in ops:
        path = (o.get("path") or "").lower()
        if not path:
            continue
        if q in path or path in q:
            result.append(o)
            continue
        if q in (o.get("summary") or "").lower():
            result.append(o)
            continue
        tags = o.get("tags") or []
        if any(q in (t or "").lower() for t in tags):
            result.append(o)
            continue
    return result


def extract_openapi_path_tokens(text: str) -> list[str]:
    """
    从用户消息中提取疑似 HTTP 路径的片段（以 / 开头，不含空白）。
    用于 `/api /v1/foo/bar 请问参数` 这类输入。
    """
    if not text or not text.strip():
        return []
    # 连续 /xxx/yyy，不含空白与中文；允许 {id}、连字符
    return re.findall(r"/[^\s\u4e00-\u9fff]+", text)


def resolve_oas_path(paths_obj: dict[str, Any], hint: str) -> str | None:
    """
    在 OAS paths 中解析用户给出的路径：精确匹配优先，其次去尾斜杠、后缀匹配。
    """
    if not isinstance(paths_obj, dict) or not hint:
        return None
    h = hint.strip()
    if not h.startswith("/"):
        h = "/" + h
    if h in paths_obj:
        return h
    h_norm = h.rstrip("/") or "/"
    for p in paths_obj:
        if not isinstance(p, str):
            continue
        p_norm = p.rstrip("/") or "/"
        if p_norm.lower() == h_norm.lower():
            return p
        if p_norm.lower().endswith(h_norm.lower()) or h_norm.lower().endswith(p_norm.lower()):
            return p
    return None


def get_path_operations(oas: dict[str, Any], path_key: str) -> list[tuple[str, dict[str, Any]]]:
    """返回某 path 下所有 HTTP 方法及其 operation 对象。"""
    paths = oas.get("paths") or {}
    if not isinstance(paths, dict):
        return []
    item = paths.get(path_key)
    if not isinstance(item, dict):
        return []
    out: list[tuple[str, dict[str, Any]]] = []
    for method, op in item.items():
        lm = method.lower()
        if lm.startswith("x-") or lm == "parameters":
            continue
        if lm not in _HTTP_METHODS:
            continue
        if isinstance(op, dict):
            out.append((lm.upper(), op))
    return out


def format_operation_detail(
    oas: dict[str, Any],
    path_key: str,
    method: str,
    *,
    max_chars: int = 12000,
) -> str:
    """
    将单个 operation 格式化为可读文本：参数、请求体、响应（JSON 摘要）。
    """
    ops = get_path_operations(oas, path_key)
    method_u = method.upper()
    op_obj: dict[str, Any] | None = None
    for m, o in ops:
        if m.upper() == method_u:
            op_obj = o
            break
    if op_obj is None:
        if len(ops) == 1:
            method_u, op_obj = ops[0][0], ops[0][1]
        else:
            avail = ", ".join(m for m, _ in ops)
            return f"路径 `{path_key}` 下未找到方法 `{method}`。可用方法：{avail or '无'}"

    chunk: dict[str, Any] = {
        "summary": op_obj.get("summary"),
        "operationId": op_obj.get("operationId"),
        "parameters": op_obj.get("parameters"),
        "requestBody": op_obj.get("requestBody"),
        "responses": op_obj.get("responses"),
    }
    # 去掉全空键
    chunk = {k: v for k, v in chunk.items() if v is not None}
    try:
        text = json.dumps(chunk, ensure_ascii=False, indent=2)
    except (TypeError, ValueError):
        text = str(chunk)
    if len(text) > max_chars:
        text = text[:max_chars] + "\n… (已截断)"
    return f"**{method_u}** `{path_key}`\n\n```json\n{text}\n```"
