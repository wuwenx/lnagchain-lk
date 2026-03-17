"""
配置：从环境变量加载飞书与 LLM 配置
"""
import os
from pathlib import Path

from dotenv import load_dotenv

# 项目根目录下的 .env
_env_path = Path(__file__).resolve().parent / ".env"
load_dotenv(dotenv_path=_env_path)


def _str(key: str, default: str = "") -> str:
    return os.environ.get(key, default).strip()


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

# LLM（OpenAI 兼容：OpenAI / DeepSeek / 国内中转等）
OPENAI_API_KEY = _str("OPENAI_API_KEY")
OPENAI_API_BASE = _str("OPENAI_API_BASE", "https://api.openai.com/v1")
# 模型名，如 gpt-4o-mini、deepseek-chat、deepseek-reasoner
OPENAI_MODEL = _str("OPENAI_MODEL", "gpt-4o-mini")

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

# Toobit 24h 成交量 Top20 定时推送：接收卡片的飞书群 chat_id，不填则不定时推送 oc_70f3a7c325ba36ca6b22282e346ecfce
FEISHU_TOOBIT_24H_CHAT_ID = _str("FEISHU_TOOBIT_24H_CHAT_ID", "")

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


def validate_config() -> list[str]:
    """校验必填配置，返回错误信息列表。"""
    errors = []
    if not FEISHU_APP_ID or not FEISHU_APP_SECRET:
        errors.append("请设置 FEISHU_APP_ID 和 FEISHU_APP_SECRET（.env 或环境变量）")
    if not OPENAI_API_KEY:
        errors.append("请设置 OPENAI_API_KEY")
    return errors


def validate_webhook_config() -> list[str]:
    """Webhook 模式专用：校验 VERIFICATION_TOKEN 等。"""
    errors = validate_config()
    if not FEISHU_VERIFICATION_TOKEN:
        errors.append("Webhook 模式请设置 FEISHU_VERIFICATION_TOKEN（事件订阅里的 Verification Token）")
    return errors
