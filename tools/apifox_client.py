"""
Apifox 开放 API 客户端：导出项目的 OpenAPI 规范，供「生成前端」等流程使用。
文档：https://apifox-openapi.apifox.cn
"""
import json
import logging
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
) -> dict[str, Any] | None:
    """
    调用 Apifox 开放 API 导出项目的 OpenAPI 规范。

    :param project_id: 项目 ID，不传则用 config.APIFOX_PROJECT_ID
    :param module_id: 模块 ID，不传则用 config.APIFOX_MODULE_ID 或导出默认模块
    :param oas_version: 2.0 | 3.0 | 3.1
    :param export_format: JSON | YAML
    :param scope_type: ALL | SELECTED_TAGS | SELECTED_FOLDERS | SELECTED_ENDPOINTS
    :param include_apifox_ext: 是否包含 x-apifox 扩展字段
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
            "addFoldersToTags": False,
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
