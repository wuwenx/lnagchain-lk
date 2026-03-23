"""
/metabase skill：按仓库固定流程（docs/metabase-add-page.md）在 CODE_WORKSPACE_ROOT 中新增 Metabase 嵌入页与侧栏导航。
仅白名单群可触发（与 /code 相同）。
用法示例：`/metabase data-hedge-profit 115 合约`、`/metabase id 115 my-board 现货`
"""
import logging
import re
from pathlib import Path

from config import CODE_WORKSPACE_ROOT, FEISHU_CODE_AGENT_CHAT_ID, METABASE_ADD_PAGE_DOC_PATH

logger = logging.getLogger(__name__)

_TRIGGER_COMMANDS = ["/metabase"]

_SLUG_RE = re.compile(r"^[a-z][a-z0-9]*(-[a-z0-9]+)*$")
_AT_TAG_PATTERN = re.compile(r"<at[^>]*>[^<]*</at>\s*", re.IGNORECASE)
_AT_MENTION_ANY = re.compile(r"@\S+", re.IGNORECASE)

_SPOT_LAST = frozenset({"现货", "现", "spot"})
_FUT_LAST = frozenset({"合约", "永续", "永续合约", "futures", "future", "contract"})


def _usage() -> str:
    return (
        "**Metabase 看板页（固定流程）**\n\n"
        "**用法**\n"
        "• `/metabase <slug> <DashboardID> <现货|合约>`\n"
        "• `/metabase id <DashboardID> <slug> <现货|合约>`\n\n"
        "• **现货**：导航项增加 `showOnlyInSpot: true`（仅现货侧可见）。\n"
        "• **合约**：导航项增加 `showOnlyInFuture: true`（仅合约侧可见）。\n\n"
        "可选：`标题「菜单中文名」`（须用中文直角引号），用于侧栏 `title`；不设则由代码助手参考 slug 起名。\n\n"
        "**示例**\n"
        "`/metabase data-hedge-profit 115 合约`\n"
        "`/metabase id 115 data-hedge-profit 现货`\n"
        "`/metabase id 110 my-dashboard 合约 标题「外盘盈亏分析」`"
    )


def _strip_mentions(text: str) -> str:
    t = _AT_TAG_PATTERN.sub("", text or "")
    t = _AT_MENTION_ANY.sub("", t)
    return re.sub(r"\s+", " ", t).strip()


def _extract_title_brackets(raw: str) -> tuple[str, str | None]:
    """从全文去掉 `标题「…」`，返回 (剩余文本, 标题或 None)。"""
    m = re.search(r"标题\s*「([^」]+)」", raw)
    if not m:
        return raw, None
    title = m.group(1).strip()
    rest = (raw[: m.start()] + raw[m.end() :]).strip()
    rest = re.sub(r"\s+", " ", rest)
    return rest, title or None


def parse_metabase_command(text: str) -> tuple[dict | None, str | None]:
    """
    解析 /metabase 参数。
    成功返回 ({"slug", "dashboard_id", "scope", "title"?}, None)；
    失败返回 (None, 错误说明)。
    """
    raw = _strip_mentions(text)
    for pfx in _TRIGGER_COMMANDS:
        if raw.lower().startswith(pfx.lower()):
            raw = raw[len(pfx) :].strip()
            break
    if not raw:
        return None, _usage()

    raw, title = _extract_title_brackets(raw)
    parts = raw.split()
    if not parts:
        return None, _usage()

    last = parts[-1]
    last_l = last.lower()
    if last in _SPOT_LAST or last_l == "spot":
        scope = "spot"
        parts = parts[:-1]
    elif last in _FUT_LAST or last_l in ("futures", "future", "contract"):
        scope = "futures"
        parts = parts[:-1]
    else:
        return None, "请在末尾指定 **现货** 或 **合约**（控制 `showOnlyInSpot` / `showOnlyInFuture`）。"

    if not parts:
        return None, _usage()

    slug: str | None = None
    dash_id: int | None = None

    if parts[0].lower() == "id":
        if len(parts) < 3:
            return None, "`id` 形式需：`/metabase id <数字ID> <slug> <现货|合约>`"
        try:
            dash_id = int(parts[1])
        except ValueError:
            return None, "`id` 后必须是数字 Dashboard ID。"
        cand = parts[2]
        if not _SLUG_RE.match(cand):
            return None, f"slug 须为 kebab-case（如 `data-hedge-profit`），当前：{cand!r}"
        slug = cand
    else:
        nums: list[int] = []
        slugs: list[str] = []
        for p in parts:
            if p.isdigit():
                nums.append(int(p))
            elif _SLUG_RE.match(p):
                slugs.append(p)
        if len(nums) != 1 or len(slugs) != 1:
            return (
                None,
                "请提供**一个** slug（kebab-case）与**一个** Dashboard 数字 ID。\n"
                "示例：`/metabase data-hedge-profit 115 合约`",
            )
        if _SLUG_RE.match(parts[0]):
            slug, dash_id = slugs[0], nums[0]
        elif parts[0].isdigit():
            dash_id, slug = nums[0], slugs[0]
        else:
            return None, "无法解析 slug 与 ID，请使用：`/metabase <slug> <id> <现货|合约>`"

    out: dict = {"slug": slug, "dashboard_id": dash_id, "scope": scope}
    if title:
        out["title"] = title
    return out, None


def _load_workflow_doc() -> str:
    candidates: list[Path] = []
    if METABASE_ADD_PAGE_DOC_PATH:
        candidates.append(Path(METABASE_ADD_PAGE_DOC_PATH).expanduser())
    ws = CODE_WORKSPACE_ROOT
    if ws:
        candidates.append(Path(ws) / "docs" / "metabase-add-page.md")
    candidates.append(Path(__file__).resolve().parent.parent / "docs" / "metabase-add-page.md")
    for p in candidates:
        try:
            if p.is_file():
                return p.read_text(encoding="utf-8")
        except OSError as e:
            logger.warning("read metabase workflow doc %s: %s", p, e)
    return ""


def run_metabase_page(
    user_message: str,
    *,
    document_context: str | None = None,
    chat_id: str = "",
    **kwargs,
) -> str:
    if not FEISHU_CODE_AGENT_CHAT_ID:
        return "Metabase 流程未配置白名单群（FEISHU_CODE_AGENT_CHAT_ID），暂不可用。"
    if (chat_id or "").strip() != FEISHU_CODE_AGENT_CHAT_ID.strip():
        return "仅支持在指定群使用「/metabase」功能，当前群未开放。"
    if not CODE_WORKSPACE_ROOT:
        return "请配置 CODE_WORKSPACE_ROOT 为 mm-admin 仓库根目录。"

    params, err = parse_metabase_command(user_message)
    if err is not None:
        return err
    if not params:
        return _usage()

    doc = _load_workflow_doc()
    if not doc.strip():
        return (
            "未找到流程文档 `metabase-add-page.md`。请在 `CODE_WORKSPACE_ROOT/docs/` 下放置该文件，"
            "或设置环境变量 `METABASE_ADD_PAGE_DOC_PATH` 指向完整路径。"
        )

    slug = params["slug"]
    dash_id = params["dashboard_id"]
    scope = params["scope"]
    title = params.get("title")

    if scope == "spot":
        scope_desc = (
            "菜单可见范围：**仅现货** — 在 `vertical/index.js` 的 MetaBase children 新项上设置 `showOnlyInSpot: true`，"
            "不要设置 `showOnlyInFuture`。"
        )
        scope_note = "showOnlyInSpot: true"
    else:
        scope_desc = (
            "菜单可见范围：**仅合约** — 在 `vertical/index.js` 的 MetaBase children 新项上设置 `showOnlyInFuture: true`，"
            "不要设置 `showOnlyInSpot`。"
        )
        scope_note = "showOnlyInFuture: true"

    title_line = ""
    if title:
        title_line = (
            f"\n- 侧栏菜单 **title**：优先使用用户指定文案「{title}」"
            f"（按项目现有写法用 `getI18n().global.t(...)` 或写入 i18n 资源，与同组 MetaBase 子项风格一致）。\n"
        )
    else:
        title_line = (
            "\n- 侧栏菜单 **title**：请根据 slug 语义化中文，与同组 MetaBase 子项风格一致；必要时补充 i18n。\n"
        )

    prompt = f"""请在本仓库（工作区根目录即 mm-admin）中，**严格按下列《固定流程》文档**完成「新增 Metabase 看板页」的全部修改，使用 read_local_file / replace_code_block / write_local_file 执行，**不要臆造路由命名规则**。

## 本次任务参数（必须遵守）
- **slug**：`{slug}`（kebab-case，文件名与 `dashboardIds` 的 key 必须与此一致）
- **Metabase Dashboard 数字 ID**：`{dash_id}`（单一看板：在 `dashboardIds` **追加** `'{slug}': {dash_id},`，勿删改已有键）
- {scope_desc}
- 路由 **name**：`metabase-{slug}`；页面文件：`src/pages/metabase/{slug}.vue`；导航 **to** / **auth_name** 与同组现有 MetaBase 页一致（通常为 `metabase-{slug}`）。
{title_line}
## 你必须完成的文件（与流程文档一致）
**重要：全部为「追加 / 新建」，禁止删除或覆盖已有配置。**

1. `src/config/metabase.js` — 在 `dashboardIds` **对象末尾追加**一行 `'{slug}': {dash_id},`，**保留**所有原有键值对（不要删掉或改名别人的 slug）。
2. `src/pages/metabase/{slug}.vue` — **新建文件**；以 `data-asset.vue` 等为模板复制，仅将读取的 `dashboardIds` 键改为 `'{slug}'`。
3. `src/navigation/vertical/index.js` — 在 MetaBase 分组 `children` **数组末尾（或合适位置）追加**一个新对象：`to`、`auth_name` 与 `slug` 对应；`id` 为**新的、全项目唯一**字符串（勿替换已有菜单项）；并加上 **{scope_note}**。**禁止**删除或覆盖已有 `{ ... }` 菜单项。
4. **不要**默认改 `horizontal/index.js`，除非流程或用户明确要求顶栏入口。


## 自检
完成前对照流程文档中的自检清单逐项核对。

---

## 《固定流程》全文（命名与步骤以下文为准）

{doc}

---

请开始：先读 `src/config/metabase.js`、`src/navigation/vertical/index.js` 与模板页，再按参数修改与新增。"""

    # 代码助手可能运行数分钟、多轮工具调用；先推一条提示，避免用户误以为「没反应」
    cid = (chat_id or "").strip()
    if cid:
        try:
            from lark_client import send_text_message

            send_text_message(
                cid,
                "⏳ 已收到 **/metabase**，正在按固定流程执行代码助手（读取/写入工作区，可能需要 **数分钟**），完成后会再发一条结果…\n"
                f"参数：slug=`{slug}`，Dashboard ID=`{dash_id}`，"
                + ("现货" if scope == "spot" else "合约")
                + "。",
            )
        except Exception as ack_e:
            logger.warning("metabase 即时提示发送失败（可忽略）: %s", ack_e)

    try:
        from code_agent import run as code_agent_run

        logger.info(
            "metabase_page: invoking code_agent slug=%s dashboard_id=%s scope=%s workspace=%s",
            slug,
            dash_id,
            scope,
            CODE_WORKSPACE_ROOT,
        )
        result = code_agent_run(prompt)
        if not (result or "").strip():
            return (
                "Metabase 流程已结束，但模型未返回文字总结（接口可能返回空 content）。"
                "请在本机查看 `CODE_WORKSPACE_ROOT` 下文件是否已变更；若完全无变更，请查服务端日志中的 `metabase_page` / `code_agent`。"
            )
        return result
    except Exception as e:
        logger.exception("metabase_page code_agent run error")
        return f"执行 /metabase 时出错: {e}"


class MetabasePageSkill:
    id = "metabase_page"
    name = "Metabase 看板页"
    description = "按固定流程新增 Metabase 嵌入页与导航（/metabase，仅白名单群，需 CODE_WORKSPACE_ROOT 指向 mm-admin）"
    trigger_commands = _TRIGGER_COMMANDS.copy()

    def run(
        self,
        user_message: str,
        *,
        document_context: str | None = None,
        chat_id: str = "",
        **kwargs,
    ) -> str:
        return run_metabase_page(
            user_message,
            document_context=document_context,
            chat_id=chat_id,
            **kwargs,
        )


metabase_page_skill = MetabasePageSkill()
