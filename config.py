"""
配置：飞书等从 .env / 环境变量读取；**LLM 模型相关项仅从项目根目录 config.json 读取**（不走 .env）。
"""
import json
import os
from pathlib import Path

from dotenv import load_dotenv

# 项目根目录下的 .env
_env_path = Path(__file__).resolve().parent / ".env"
load_dotenv(dotenv_path=_env_path)

# 运行时 JSON（与 .env 并存；模型等可被管理页覆盖）
PROJECT_ROOT = Path(__file__).resolve().parent
CONFIG_JSON_PATH = PROJECT_ROOT / "config.json"


def _str(key: str, default: str = "") -> str:
    return os.environ.get(key, default).strip()


def _load_config_json() -> dict:
    try:
        if CONFIG_JSON_PATH.is_file():
            with open(CONFIG_JSON_PATH, encoding="utf-8") as f:
                data = json.load(f)
            return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError, TypeError, ValueError):
        pass
    return {}


_RUNTIME_CONFIG: dict = _load_config_json()


def _json_only_str(key: str) -> str:
    """仅从 config.json 读取（键不存在则为空字符串）。"""
    if key not in _RUNTIME_CONFIG:
        return ""
    rv = _RUNTIME_CONFIG[key]
    if rv is None:
        return ""
    return str(rv).strip()


def _json_only_bool(key: str, default: bool = False) -> bool:
    """仅从 config.json 读取布尔；键不存在用 default。"""
    if key not in _RUNTIME_CONFIG:
        return default
    v = _RUNTIME_CONFIG[key]
    if isinstance(v, bool):
        return v
    s = str(v).strip().lower()
    if s in ("1", "true", "yes", "on"):
        return True
    if s in ("0", "false", "no", "off"):
        return False
    return default


def _load_llm_from_runtime() -> tuple[str, str, str, str, str, str, bool]:
    """
    从 config.json 加载 LLM 配置（不使用 .env）：
    文本：OPENAI_API_BASE, OPENAI_API_KEY, OPENAI_MODEL
    多模态：VISION_OPENAI_API_BASE, VISION_OPENAI_API_KEY, VISION_OPENAI_MODEL
    兼容旧键 OPENAI_VISION_MODEL → 仅当 VISION_OPENAI_MODEL 为空时作为模型名回退
    """
    api_base = _json_only_str("OPENAI_API_BASE")
    api_key = _json_only_str("OPENAI_API_KEY")
    model = _json_only_str("OPENAI_MODEL")
    v_base = _json_only_str("VISION_OPENAI_API_BASE")
    v_key = _json_only_str("VISION_OPENAI_API_KEY")
    v_model = _json_only_str("VISION_OPENAI_MODEL")
    if not v_model:
        v_model = _json_only_str("OPENAI_VISION_MODEL")
    vision_mm = _json_only_bool("VISION_MULTIMODAL", False)
    return api_base, api_key, model, v_base, v_key, v_model, vision_mm


def llm_text_config_ready() -> bool:
    return bool(OPENAI_API_BASE and OPENAI_API_KEY and OPENAI_MODEL)


def llm_vision_config_ready() -> bool:
    return bool(
        VISION_MULTIMODAL
        and VISION_OPENAI_API_BASE
        and VISION_OPENAI_API_KEY
        and VISION_OPENAI_MODEL
    )


def format_llm_text_config_missing_message() -> str | None:
    if llm_text_config_ready():
        return None
    p = CONFIG_JSON_PATH.resolve()
    return (
        f"❌ LLM 文本模型未配置完整。请在项目根目录编辑 **{p}**（可复制 **config.example.json**），"
        f"填写 **OPENAI_API_BASE**、**OPENAI_API_KEY**、**OPENAI_MODEL**。\n"
        f"说明：**模型相关配置仅从该文件读取，不使用 .env**。"
    )


def format_llm_vision_config_missing_message() -> str | None:
    """开启多模态但多模态三套未配齐时返回提示。"""
    if not VISION_MULTIMODAL:
        return None
    if llm_vision_config_ready():
        return None
    p = CONFIG_JSON_PATH.resolve()
    return (
        f"❌ 已开启 **VISION_MULTIMODAL**，但多模态接口未配置完整。请在 **{p}** 填写 "
        f"**VISION_OPENAI_API_BASE**、**VISION_OPENAI_API_KEY**、**VISION_OPENAI_MODEL**。\n"
        f"读取飞书/Lark 截图或图片时将**仅使用多模态配置**，**不再使用文本模型的 OPENAI_MODEL**。"
    )


def reload_runtime_config() -> None:
    """重新读取 config.json 并更新 LLM 相关模块变量与 os.environ（便于观测）。"""
    global _RUNTIME_CONFIG
    global OPENAI_API_BASE, OPENAI_API_KEY, OPENAI_MODEL
    global VISION_OPENAI_API_BASE, VISION_OPENAI_API_KEY, VISION_OPENAI_MODEL, VISION_MULTIMODAL
    _RUNTIME_CONFIG = _load_config_json()
    (
        OPENAI_API_BASE,
        OPENAI_API_KEY,
        OPENAI_MODEL,
        VISION_OPENAI_API_BASE,
        VISION_OPENAI_API_KEY,
        VISION_OPENAI_MODEL,
        VISION_MULTIMODAL,
    ) = _load_llm_from_runtime()
    os.environ["OPENAI_API_BASE"] = OPENAI_API_BASE
    os.environ["OPENAI_API_KEY"] = OPENAI_API_KEY
    os.environ["OPENAI_MODEL"] = OPENAI_MODEL
    os.environ["VISION_OPENAI_API_BASE"] = VISION_OPENAI_API_BASE
    os.environ["VISION_OPENAI_API_KEY"] = VISION_OPENAI_API_KEY
    os.environ["VISION_OPENAI_MODEL"] = VISION_OPENAI_MODEL
    os.environ["VISION_MULTIMODAL"] = "true" if VISION_MULTIMODAL else "false"
    os.environ.pop("OPENAI_VISION_MODEL", None)


def update_runtime_config_file(updates: dict) -> None:
    """合并写入 config.json 并 reload（供管理页等调用）。值可为 str / bool。"""
    data = _load_config_json()
    for k, v in updates.items():
        data[k] = v
    CONFIG_JSON_PATH.parent.mkdir(parents=True, exist_ok=True)
    CONFIG_JSON_PATH.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    reload_runtime_config()
    # 清空单例链，使下次 get_chain() 使用新模型
    try:
        import langchain_agent as _la

        _la._chain = None
    except Exception:
        pass


# 飞书
FEISHU_APP_ID = _str("FEISHU_APP_ID")
FEISHU_APP_SECRET = _str("FEISHU_APP_SECRET")
FEISHU_GROUP_ACCESS = _str("FEISHU_GROUP_ACCESS", "open")  # open | allowlist | disabled
# 国际版 Lark 请设为 https://open.larksuite.com，国内飞书默认 https://open.feishu.cn
FEISHU_DOMAIN = _str("FEISHU_DOMAIN", "https://open.feishu.cn")
# 创建文档后返回的链接根地址（可选），如 https://xxx.feishu.cn 或 https://xxx.larksuite.com，不设则只返回 document_id
FEISHU_DOC_BASE_URL = _str("FEISHU_DOC_BASE_URL", "").rstrip("/")

# Webhook 模式（请求地址）必填：事件订阅里的「Verification Token」；若开启加密需填「Encrypt Key」
FEISHU_VERIFICATION_TOKEN = _str("FEISHU_VERIFICATION_TOKEN")
FEISHU_ENCRYPT_KEY = _str("FEISHU_ENCRYPT_KEY")

# Webhook HTTP 服务
WEBHOOK_HOST = _str("WEBHOOK_HOST", "0.0.0.0")
WEBHOOK_PORT = _str("WEBHOOK_PORT", "9000")
WEBHOOK_PATH = _str("WEBHOOK_PATH", "/")
# 卡片内「打开链接」类按钮使用的基础 URL（如下载 Excel），需公网可访问，不设则不显示下载按钮
PUBLIC_BASE_URL = _str("PUBLIC_BASE_URL", "").rstrip("/")

# LLM（OpenAI 兼容）：**仅从 config.json 读取**，见 _load_llm_from_runtime
(
    OPENAI_API_BASE,
    OPENAI_API_KEY,
    OPENAI_MODEL,
    VISION_OPENAI_API_BASE,
    VISION_OPENAI_API_KEY,
    VISION_OPENAI_MODEL,
    VISION_MULTIMODAL,
) = _load_llm_from_runtime()
os.environ["OPENAI_API_BASE"] = OPENAI_API_BASE
os.environ["OPENAI_API_KEY"] = OPENAI_API_KEY
os.environ["OPENAI_MODEL"] = OPENAI_MODEL
os.environ["VISION_OPENAI_API_BASE"] = VISION_OPENAI_API_BASE
os.environ["VISION_OPENAI_API_KEY"] = VISION_OPENAI_API_KEY
os.environ["VISION_OPENAI_MODEL"] = VISION_OPENAI_MODEL
os.environ["VISION_MULTIMODAL"] = "true" if VISION_MULTIMODAL else "false"
os.environ.pop("OPENAI_VISION_MODEL", None)
# 每条飞书消息最多处理几张图、单张最大字节（避免爆 token）
try:
    _vim = int(os.environ.get("VISION_MAX_IMAGES", "3") or "3")
except (TypeError, ValueError):
    _vim = 3
VISION_MAX_IMAGES = max(1, min(8, _vim))
try:
    _vmb = int(os.environ.get("VISION_MAX_IMAGE_BYTES", str(4 * 1024 * 1024)) or str(4 * 1024 * 1024))
except (TypeError, ValueError):
    _vmb = 4 * 1024 * 1024
VISION_MAX_IMAGE_BYTES = max(512 * 1024, min(20 * 1024 * 1024, _vmb))
# 拉取飞书 docx 内嵌图片并随上下文送入多模态模型（需 docs:document.media:download 等权限）
FEISHU_DOC_FETCH_IMAGES = _str("FEISHU_DOC_FETCH_IMAGES", "true").lower() in ("1", "true", "yes", "on")
try:
    _dm = int(os.environ.get("DOCX_MAX_IMAGES", "12") or "12")
except (TypeError, ValueError):
    _dm = 12
DOCX_MAX_IMAGES = max(0, min(30, _dm))
# CoinMarketCap API（/rank skill 交易所排名，可选）
CMC_API_KEY = _str("CMC_API_KEY")

# JKS（Jenkins）发板：触发构建
JKS_URL = _str("JKS_URL", "").rstrip("/")
JKS_USERNAME = _str("JKS_USERNAME", "")
JKS_TOKEN = _str("JKS_TOKEN", "")
JKS_JOB_NAME = _str("JKS_JOB_NAME", "")

# GitLab Webhook → 飞书 MR 卡片
# 在 GitLab 项目/群组 Webhook 里填的 Secret token，可选
GITLAB_WEBHOOK_SECRET = _str("GITLAB_WEBHOOK_SECRET", "")
# 接收 MR/Push 通知的飞书群 chat_id
FEISHU_MR_CHAT_ID = _str("FEISHU_MR_CHAT_ID", "")

# 群聊 @ 机器人 判定：若设置，则仅当 mentions 中包含该 open_id 时才回复（严格走「群聊中@机器人」事件）
# 不设则沿用原逻辑：群聊有任意 @ 或 content 含 <at 即回复
FEISHU_BOT_OPEN_ID = _str("FEISHU_BOT_OPEN_ID", "ou_621c9a4fa6b4b678699c72e05649964c")

# Push 卡片：回滚 tag 默认值（commit message 中未写回滚tag 时使用）
GITLAB_PUSH_ROLLBACK_TAG = _str("GITLAB_PUSH_ROLLBACK_TAG", "PROD-20260205-maintainOuerDepth")

# Toobit 24h 成交量 Top20 定时推送：接收卡片的飞书群 chat_id，不填则不定时推送 
FEISHU_TOOBIT_24H_CHAT_ID = _str("FEISHU_TOOBIT_24H_CHAT_ID", "oc_70f3a7c325ba36ca6b22282e346ecfce")

# 插针监听：检测到插针后推送的飞书群 chat_id，不填则不推送
FEISHU_NEEDLE_ALERT_CHAT_ID = _str("FEISHU_NEEDLE_ALERT_CHAT_ID", "oc_1fdd521a8a86fc0413ca8ef20364e3f2")

# MEXC 下架公告定时推送：接收卡片的飞书群 chat_id，不填则不定时推送
FEISHU_MEXC_DELISTINGS_CHAT_ID = _str("FEISHU_MEXC_DELISTINGS_CHAT_ID", "oc_a3df7d8f7b728d12c7e6a4af98fd5eae")
# 每次抓取的页数（变量，可在 .env 用 MEXC_DELISTINGS_PAGES=3 覆盖）
try:
    _mexc_pages = int(os.environ.get("MEXC_DELISTINGS_PAGES", "2") or "2")
except (TypeError, ValueError):
    _mexc_pages = 2
MEXC_DELISTINGS_PAGES = max(1, min(10, _mexc_pages))

# Binance 公告定时推送：接收卡片的飞书群 chat_id（默认与 MEXC 下架同一群）
FEISHU_BINANCE_ANNOUNCEMENTS_CHAT_ID = _str("FEISHU_BINANCE_ANNOUNCEMENTS_CHAT_ID", "oc_a3df7d8f7b728d12c7e6a4af98fd5eae")
# 每次抓取的页数，可在 .env 用 BINANCE_ANNOUNCEMENTS_PAGES=3 覆盖
try:
    _bn_pages = int(os.environ.get("BINANCE_ANNOUNCEMENTS_PAGES", "2") or "2")
except (TypeError, ValueError):
    _bn_pages = 2
BINANCE_ANNOUNCEMENTS_PAGES = max(1, min(10, _bn_pages))

# OKX 公告定时推送：上币 + 下币，接收卡片的飞书群 chat_id
FEISHU_OKX_ANNOUNCEMENTS_CHAT_ID = _str("FEISHU_OKX_ANNOUNCEMENTS_CHAT_ID", "oc_a3df7d8f7b728d12c7e6a4af98fd5eae")
# 上币/下币各抓取的页数
try:
    _okx_pages = int(os.environ.get("OKX_ANNOUNCEMENTS_PAGES", "2") or "2")
except (TypeError, ValueError):
    _okx_pages = 2
OKX_ANNOUNCEMENTS_PAGES = max(1, min(5, _okx_pages))

# Bybit 公告定时推送：上币 + 下币，接收卡片的飞书群 chat_id
FEISHU_BYBIT_ANNOUNCEMENTS_CHAT_ID = _str("FEISHU_BYBIT_ANNOUNCEMENTS_CHAT_ID", "oc_a3df7d8f7b728d12c7e6a4af98fd5eae")
# 上币/下币各抓取的页数（API 每页默认 20 条）
try:
    _bybit_pages = int(os.environ.get("BYBIT_ANNOUNCEMENTS_PAGES", "2") or "2")
except (TypeError, ValueError):
    _bybit_pages = 2
BYBIT_ANNOUNCEMENTS_PAGES = max(1, min(5, _bybit_pages))

# 收到消息并确定会回复时，在用户该条消息上添加的表情回应。须为飞书支持的 emoji_type，如 SMILE、THUMBSUP、LAUGH。不填或空则不添加
FEISHU_REACTION_EMOJI = _str("FEISHU_REACTION_EMOJI", "SMILE")

# 多群流水线：A=需求分析 → B=方案生成 → C=总结输出。仅在 A 群 @ 机器人时触发整条流水线
FEISHU_PIPELINE_STAGE_A_CHAT_ID = _str("FEISHU_PIPELINE_STAGE_A_CHAT_ID", "oc_c582c841a1ff3e5979d7d45d8bfc7a9f")
FEISHU_PIPELINE_STAGE_B_CHAT_ID = _str("FEISHU_PIPELINE_STAGE_B_CHAT_ID", "oc_84bb99852bb8fbcf145375eed1e3d784")
FEISHU_PIPELINE_STAGE_C_CHAT_ID = _str("FEISHU_PIPELINE_STAGE_C_CHAT_ID", "oc_8b3d5e5932f49263e67d3c09612912cd")

# Popfun 日志平台：抓取 Discover 的 error 日志并推送到飞书
POPFUN_LOG_BASE_URL = _str("POPFUN_LOG_BASE_URL", "https://log.popfun.xyz").rstrip("/")
POPFUN_LOG_USERNAME = _str("POPFUN_LOG_USERNAME", "read")
# 密码请务必放在 .env：POPFUN_LOG_PASSWORD=xxx，不要提交到代码库
POPFUN_LOG_PASSWORD = _str("POPFUN_LOG_PASSWORD", "")
FEISHU_POPFUN_LOG_CHAT_ID = _str("FEISHU_POPFUN_LOG_CHAT_ID", "oc_0ef90b86531f087ef931863bb64121fd")

# 代码修改助手（/code）：仅在此群内可触发，用于通过 Lark 修改本地代码
FEISHU_CODE_AGENT_CHAT_ID = _str("FEISHU_CODE_AGENT_CHAT_ID", "oc_26bb8b21a07f1bc01d79169013ef973a")
# 代码助手操作目录：读/写/替换/执行命令均基于此目录；不设则使用本项目根目录
CODE_WORKSPACE_ROOT = _str("CODE_WORKSPACE_ROOT", "/Users/wuwenxiang/wuwx/mm-admin").strip() or None
# /metabase skill：固定流程文档路径；不设则依次尝试 CODE_WORKSPACE_ROOT/docs/metabase-add-page.md、本仓库 docs/metabase-add-page.md
METABASE_ADD_PAGE_DOC_PATH = _str("METABASE_ADD_PAGE_DOC_PATH", "").strip() or None

# Apifox 开放 API：用于「生成前端」时拉取接口文档（OpenAPI）
APIFOX_ACCESS_TOKEN = _str("APIFOX_ACCESS_TOKEN", "")
APIFOX_PROJECT_ID = _str("APIFOX_PROJECT_ID", "")
APIFOX_MODULE_ID = _str("APIFOX_MODULE_ID", "").strip() or None  # 可选，不设则导出默认模块
APIFOX_API_BASE = _str("APIFOX_API_BASE", "https://api.apifox.com").rstrip("/")
# 可选：模块名 → Apifox 模块 ID 的 JSON，供 /api 技能按名称切换模块，如 {"订单":123,"用户":456}
_APIFOX_MODULE_MAP_RAW = _str("APIFOX_MODULE_MAP", "")
try:
    _m = json.loads(_APIFOX_MODULE_MAP_RAW) if _APIFOX_MODULE_MAP_RAW else {}
    APIFOX_MODULE_MAP: dict[str, int] = (
        {str(k): int(v) for k, v in _m.items()} if isinstance(_m, dict) else {}
    )
except (TypeError, ValueError, json.JSONDecodeError):
    APIFOX_MODULE_MAP = {}


def validate_config() -> list[str]:
    """校验必填配置，返回错误信息列表。"""
    errors = []
    if not FEISHU_APP_ID or not FEISHU_APP_SECRET:
        errors.append("请设置 FEISHU_APP_ID 和 FEISHU_APP_SECRET（.env 或环境变量）")
    if not llm_text_config_ready():
        errors.append(
            f"请在 {CONFIG_JSON_PATH.resolve()} 配置 OPENAI_API_BASE、OPENAI_API_KEY、OPENAI_MODEL"
            f"（模型相关仅从该文件读取，不使用 .env）。可复制 config.example.json。"
        )
    return errors


def validate_webhook_config() -> list[str]:
    """Webhook 模式专用：校验 VERIFICATION_TOKEN 等。"""
    errors = validate_config()
    if not FEISHU_VERIFICATION_TOKEN:
        errors.append("Webhook 模式请设置 FEISHU_VERIFICATION_TOKEN（事件订阅里的 Verification Token）")
    return errors
