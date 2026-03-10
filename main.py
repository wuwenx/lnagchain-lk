"""
飞书 WebSocket 长连接 + LangChain 桥接
收到用户消息 → 调用 LangChain 生成回复 → 通过飞书 API 发回
"""
# 在导入任何使用 SSL 的库之前设置证书路径，避免 macOS 上 certificate verify failed
import os
import certifi
os.environ.setdefault("SSL_CERT_FILE", certifi.where())
os.environ.setdefault("REQUESTS_CA_BUNDLE", certifi.where())

import logging

import lark_oapi as lark

from config import FEISHU_APP_ID, FEISHU_APP_SECRET, FEISHU_DOMAIN, validate_config
from handlers import build_event_handler

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


def main() -> None:
    errors = validate_config()
    if errors:
        for e in errors:
            logger.error(e)
        raise SystemExit(1)

    event_handler = build_event_handler("", "")  # WebSocket 无需 encrypt/verification
    ws_client = lark.ws.Client(
        FEISHU_APP_ID,
        FEISHU_APP_SECRET,
        event_handler=event_handler,
        log_level=lark.LogLevel.INFO,
        domain=FEISHU_DOMAIN,
    )
    logger.info("Starting Feishu WebSocket client (LangChain bridge)...")
    ws_client.start()


if __name__ == "__main__":
    main()
