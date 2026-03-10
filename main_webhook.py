"""
飞书 Webhook（请求地址）模式：HTTP 服务接收事件 → LangChain 回复 → 飞书 API 发回
在 Lark 后台「事件配置」里将「请求地址」设为：https://你的公网域名/ 或 /webhook 等（与 WEBHOOK_PATH 一致）
"""
import os
import certifi
os.environ.setdefault("SSL_CERT_FILE", certifi.where())
os.environ.setdefault("REQUESTS_CA_BUNDLE", certifi.where())

import logging

from fastapi import FastAPI, Request, Response
from lark_oapi.core.model import RawRequest, RawResponse

from config import (
    FEISHU_ENCRYPT_KEY,
    FEISHU_VERIFICATION_TOKEN,
    WEBHOOK_HOST,
    WEBHOOK_PATH,
    WEBHOOK_PORT,
    validate_webhook_config,
)
from handlers import build_event_handler

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

app = FastAPI(title="LangChain + Lark Webhook")
event_handler = None


def _get_handler():
    global event_handler
    if event_handler is None:
        event_handler = build_event_handler(
            FEISHU_ENCRYPT_KEY or "",
            FEISHU_VERIFICATION_TOKEN or "",
        )
    return event_handler


def _raw_request_from_http(request: Request, body: bytes) -> RawRequest:
    req = RawRequest()
    req.uri = str(request.url.path or "/")
    req.body = body
    req.headers = {}
    for k, v in request.headers.items():
        if k.lower().startswith("x-lark") or k.lower() in ("content-type",):
            req.headers[k] = v
    return req


def _response_to_http(resp: RawResponse) -> Response:
    ct = (resp.headers or {}).get("Content-Type", "application/json")
    return Response(
        content=resp.content or b"",
        status_code=resp.status_code or 200,
        headers=dict(resp.headers or {}),
        media_type=ct,
    )


@app.post(WEBHOOK_PATH)
@app.get(WEBHOOK_PATH)
async def lark_webhook(request: Request):
    """接收飞书事件：url_verification 或 im.message.receive_v1 等。"""
    body = await request.body()
    raw_req = _raw_request_from_http(request, body)
    handler = _get_handler()
    raw_resp = handler.do(raw_req)
    return _response_to_http(raw_resp)


def main():
    errors = validate_webhook_config()
    if errors:
        for e in errors:
            logger.error(e)
        raise SystemExit(1)
    try:
        port = int(WEBHOOK_PORT)
    except ValueError:
        port = 9000
    logger.info("Starting Webhook server at http://%s:%s path=%s", WEBHOOK_HOST, port, WEBHOOK_PATH or "/")
    import uvicorn
    uvicorn.run(app, host=WEBHOOK_HOST, port=port)


if __name__ == "__main__":
    main()
