"""
Apifox 接口查询 skill：通过开放 API 导出 OpenAPI，列出某模块或某目录下的全部接口。
用法见 run_apifox_api 与 /api 帮助文案。
"""
import logging
import re

from config import APIFOX_MODULE_ID, APIFOX_MODULE_MAP
from tools.apifox_client import (
    collect_operations,
    export_openapi,
    extract_openapi_path_tokens,
    filter_operations,
    format_operation_detail,
    get_path_operations,
    resolve_oas_path,
)

logger = logging.getLogger(__name__)

_TRIGGER_COMMANDS = ["/api", "/apifox"]
_AT_TAG_PATTERN = re.compile(r"<at[^>]*>[^<]*</at>\s*", re.IGNORECASE)
_AT_MENTION_ANY = re.compile(r"@\S+", re.IGNORECASE)
# 用户问「参数、返回类型」等时输出 OpenAPI 中的 parameters / requestBody / responses
_WANTS_DETAIL = re.compile(
    r"参数|请求|返回|响应|body|schema|类型|字段|说明|入参|出参",
    re.IGNORECASE,
)
_METHOD_WORD = re.compile(r"\b(GET|POST|PUT|PATCH|DELETE)\b", re.IGNORECASE)

_MAX_LINES = 400
_MAX_CHARS = 14000


def _strip_trigger(text: str) -> str:
    t = (text or "").strip()
    t = _AT_TAG_PATTERN.sub("", t)
    t = _AT_MENTION_ANY.sub("", t)
    t = re.sub(r"\s+", " ", t).strip()
    for cmd in _TRIGGER_COMMANDS:
        if not cmd:
            continue
        if t.lower().startswith(cmd.lower()):
            t = t[len(cmd) :].strip()
            break
    return t


def _parse_module_and_filter(rest: str) -> tuple[int | str | None, str | None]:
    """
    解析 `/api` 后的参数。
    返回 (module_id 或 None, tag 筛选关键词)。
    - 纯数字：视为 Apifox 模块 ID，不再做目录筛选。
    - 若在 APIFOX_MODULE_MAP 中：用对应模块 ID。
    - 若含 `/v1/...` 形式路径：用**第一个路径**作主筛选（避免整句中文导致 0 条）。
    - 否则整段作为目录/摘要/path 关键词筛选（在当前导出的模块内）。
    """
    rest = (rest or "").strip()
    if not rest:
        return None, None
    low = rest.lower()
    if low in ("help", "帮助", "?", "模块", "modules"):
        return None, "__help__"
    if rest.isdigit():
        return int(rest), None
    if rest in APIFOX_MODULE_MAP:
        return APIFOX_MODULE_MAP[rest], None
    path_tokens = extract_openapi_path_tokens(rest)
    if path_tokens:
        return None, path_tokens[0]
    return None, rest


def _pick_method_for_detail(path: str, available: list[str]) -> str:
    """在多个 HTTP 方法中猜一个（用于「问参数」时未写明方法）。"""
    if not available:
        return "GET"
    if len(available) == 1:
        return available[0]
    u = path.lower()
    order = [
        ("POST", lambda: any(x in u for x in ("add", "create", "save", "update", "delete"))),
        ("GET", lambda: any(x in u for x in ("list", "query", "get", "detail"))),
    ]
    for m, pred in order:
        if m in available and pred():
            return m
    return available[0]


def run_apifox_api(user_message: str, **kwargs) -> str:
    """列出 Apifox 项目中的接口；需配置 APIFOX_ACCESS_TOKEN、APIFOX_PROJECT_ID。"""
    rest = _strip_trigger(user_message)
    mod, tag_q = _parse_module_and_filter(rest)

    if tag_q == "__help__":
        map_hint = ""
        if APIFOX_MODULE_MAP:
            pairs = "、".join(f"{k}→{v}" for k, v in APIFOX_MODULE_MAP.items())
            map_hint = f"\n• 已配置模块别名（APIFOX_MODULE_MAP）：{pairs}"
        return (
            "**Apifox 接口查询**\n\n"
            "• `/api` — 列出当前默认模块（或 .env 中 APIFOX_MODULE_ID）下全部接口\n"
            "• `/api <模块ID>` — 指定 Apifox **模块数字 ID**（在项目设置或模块设置中查看）\n"
            "• `/api <别名>` — 若在 .env 配置了 `APIFOX_MODULE_MAP` JSON，可按名称切换模块\n"
            "• `/api <关键词>` — 在已导出模块内按**目录/tag、路径、摘要**筛选（需导出时带目录信息）\n"
            "• `/api /v1/foo/bar 参数与返回类型` — 可写**完整路径**；若同时问到参数/返回，会输出 OpenAPI 中的 parameters、requestBody、responses\n"
            f"{map_hint}\n\n"
            "说明：接口列表来自 Apifox「导出 OpenAPI」；关键词筛选依赖导出选项中的目录写入 tags。"
        )

    module_id: int | str | None
    if mod is not None:
        module_id = mod
    elif APIFOX_MODULE_ID:
        try:
            module_id = int(APIFOX_MODULE_ID)
        except (TypeError, ValueError):
            module_id = None
    else:
        module_id = None

    oas = export_openapi(
        module_id=module_id,
        add_folders_to_tags=True,
    )
    if not oas:
        return (
            "未能从 Apifox 拉取接口文档。请确认已配置 APIFOX_ACCESS_TOKEN、APIFOX_PROJECT_ID，"
            "且模块 ID（若填写）正确。"
        )

    info = oas.get("info") if isinstance(oas.get("info"), dict) else {}
    title = (info.get("title") or "未命名模块").strip()
    wants_detail = bool(_WANTS_DETAIL.search(rest))
    path_hints = extract_openapi_path_tokens(rest)
    explicit_method: str | None = None
    m_m = _METHOD_WORD.search(rest)
    if m_m:
        explicit_method = m_m.group(1).upper()

    ops = collect_operations(oas)
    if tag_q:
        ops = filter_operations(ops, tag_query=tag_q)
    if not ops:
        return (
            f"模块「{title}」下没有匹配的接口（共 0 条）。可调整关键词、检查模块 ID，"
            f"或确认路径是否写对（例如 `/v1/chain/add`，勿写成 `api/v1/...` 除非文档里确实如此）。"
        )

    # 问到「参数/返回」且能对应到单一路径：输出 OpenAPI 中的 parameters / requestBody / responses
    paths_obj = oas.get("paths") if isinstance(oas.get("paths"), dict) else {}
    if wants_detail:
        pk: str | None = None
        if path_hints:
            pk = resolve_oas_path(paths_obj, path_hints[0])
        if not pk and len(ops) == 1:
            pk = (ops[0].get("path") or "").strip() or None
        if pk:
            pm_list = [m for m, _ in get_path_operations(oas, pk)]
            if explicit_method and explicit_method in pm_list:
                method_use = explicit_method
            elif len(ops) == 1 and (ops[0].get("path") or "") == pk:
                method_use = (ops[0].get("method") or "GET").upper()
            else:
                method_use = _pick_method_for_detail(pk, pm_list)
            detail_text = format_operation_detail(oas, pk, method_use, max_chars=_MAX_CHARS)
            return f"**模块**：{title}\n\n{detail_text}"

    lines: list[str] = [
        f"**{title}** 共 **{len(ops)}** 条接口",
    ]
    # 按首 tag（通常为 Apifox 目录）分组
    by_tag: dict[str, list[dict]] = {}
    for o in ops:
        tags = o.get("tags") or []
        key = tags[0] if tags else "(未分类)"
        by_tag.setdefault(key, []).append(o)

    truncated = False
    for tag_name in sorted(by_tag.keys()):
        lines.append(f"\n**{tag_name}**")
        for o in by_tag[tag_name]:
            sm = o.get("summary") or ""
            sm = f" — {sm}" if sm else ""
            lines.append(f"- `{o['method']}` `{o['path']}`{sm}")
            if len(lines) >= _MAX_LINES:
                truncated = True
                break
        if truncated:
            break

    out = "\n".join(lines)
    if len(out) > _MAX_CHARS:
        out = out[:_MAX_CHARS] + "\n\n… (输出过长已截断，可缩小关键词或指定更具体的目录名)"
    elif truncated:
        out += "\n\n… (条数过多已截断，请使用 `/api 关键词` 缩小范围)"
    return out


class ApifoxApiSkill:
    id = "apifox_api"
    name = "Apifox 接口查询"
    description = "从 Apifox 导出 OpenAPI，列出模块或目录下的接口（/api，需配置令牌与项目 ID）"
    trigger_commands = _TRIGGER_COMMANDS.copy()

    def run(self, user_message: str, **kwargs) -> str:
        return run_apifox_api(user_message, **kwargs)


apifox_api_skill = ApifoxApiSkill()
