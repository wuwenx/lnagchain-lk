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
