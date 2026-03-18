"""
飞书 Webhook（请求地址）模式：HTTP 服务接收事件 → LangChain 回复 → 飞书 API 发回
在 Lark 后台「事件配置」里将「请求地址」设为：https://你的公网域名/ 或 /webhook 等（与 WEBHOOK_PATH 一致）
"""
import os
import certifi
os.environ.setdefault("SSL_CERT_FILE", certifi.where())
os.environ.setdefault("REQUESTS_CA_BUNDLE", certifi.where())

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, Response
from lark_oapi.core.exception import EventException
from lark_oapi.core.model import RawRequest, RawResponse
from apscheduler.schedulers.background import BackgroundScheduler

from config import (
    FEISHU_BINANCE_ANNOUNCEMENTS_CHAT_ID,
    FEISHU_BYBIT_ANNOUNCEMENTS_CHAT_ID,
    FEISHU_ENCRYPT_KEY,
    FEISHU_MEXC_DELISTINGS_CHAT_ID,
    FEISHU_NEEDLE_ALERT_CHAT_ID,
    FEISHU_OKX_ANNOUNCEMENTS_CHAT_ID,
    FEISHU_POPFUN_LOG_CHAT_ID,
    FEISHU_TOOBIT_24H_CHAT_ID,
    FEISHU_VERIFICATION_TOKEN,
    WEBHOOK_HOST,
    WEBHOOK_PATH,
    WEBHOOK_PORT,
    validate_webhook_config,
)
from gitlab_webhook import handle_gitlab_webhook
from handlers import build_event_handler
from tasks.binance_announcements import run_binance_announcements_push
from tasks.bybit_announcements import run_bybit_announcements_push
from tasks.mexc_delistings import run_mexc_delistings_push
from tasks.needle_scan import run_needle_scan_push
from tasks.okx_announcements import run_okx_announcements_push
from tasks.popfun_log import run_popfun_log_push
from tasks.toobit_24h import run_toobit_24h_push

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

_scheduler: BackgroundScheduler | None = None


@asynccontextmanager
async def _lifespan(app: FastAPI):
    """应用生命周期：启动时启动定时任务，关闭时停止。"""
    global _scheduler
    has_toobit = bool((FEISHU_TOOBIT_24H_CHAT_ID or "").strip())
    has_needle = bool((FEISHU_NEEDLE_ALERT_CHAT_ID or "").strip())
    has_mexc = bool((FEISHU_MEXC_DELISTINGS_CHAT_ID or "").strip())
    has_binance = bool((FEISHU_BINANCE_ANNOUNCEMENTS_CHAT_ID or "").strip())
    has_okx = bool((FEISHU_OKX_ANNOUNCEMENTS_CHAT_ID or "").strip())
    has_bybit = bool((FEISHU_BYBIT_ANNOUNCEMENTS_CHAT_ID or "").strip())
    has_popfun = bool((FEISHU_POPFUN_LOG_CHAT_ID or "").strip())
    if has_toobit or has_needle or has_mexc or has_binance or has_okx or has_bybit or has_popfun:
        _scheduler = BackgroundScheduler()
        if has_toobit:
            _scheduler.add_job(run_toobit_24h_push, "interval", minutes=5, id="toobit_24h")
            logger.info("Toobit 24h scheduler (every 5 min -> %s)", (FEISHU_TOOBIT_24H_CHAT_ID or "")[:20] + "...")
            try:
                run_toobit_24h_push()
            except Exception as e:
                logger.exception("Toobit 24h first run error: %s", e)
        if has_needle:
            _scheduler.add_job(run_needle_scan_push, "interval", minutes=5, id="needle_scan")
            logger.info("Needle scan scheduler (every 5 min -> %s)", (FEISHU_NEEDLE_ALERT_CHAT_ID or "")[:20] + "...")
            try:
                run_needle_scan_push()
            except Exception as e:
                logger.exception("Needle scan first run error: %s", e)
        # if has_mexc:
        #     _scheduler.add_job(run_mexc_delistings_push, "interval", minutes=5, id="mexc_delistings")
        #     logger.info("MEXC delistings scheduler (every 5 min -> %s)", (FEISHU_MEXC_DELISTINGS_CHAT_ID or "")[:20] + "...")
        #     try:
        #         run_mexc_delistings_push()
        #     except Exception as e:
        #         logger.exception("MEXC delistings first run error: %s", e)
        # if has_binance:
        #     _scheduler.add_job(run_binance_announcements_push, "interval", minutes=5, id="binance_announcements")
        #     logger.info("Binance announcements scheduler (every 5 min -> %s)", (FEISHU_BINANCE_ANNOUNCEMENTS_CHAT_ID or "")[:20] + "...")
        #     try:
        #         run_binance_announcements_push()
        #     except Exception as e:
        #         logger.exception("Binance announcements first run error: %s", e)
        # if has_okx:
        #     _scheduler.add_job(run_okx_announcements_push, "interval", minutes=5, id="okx_announcements")
        #     logger.info("OKX announcements scheduler (every 5 min -> %s)", (FEISHU_OKX_ANNOUNCEMENTS_CHAT_ID or "")[:20] + "...")
        #     try:
        #         run_okx_announcements_push()
        #     except Exception as e:
        #         logger.exception("OKX announcements first run error: %s", e)
        # if has_bybit:
        #     _scheduler.add_job(run_bybit_announcements_push, "interval", minutes=5, id="bybit_announcements")
        #     logger.info("Bybit announcements scheduler (every 5 min -> %s)", (FEISHU_BYBIT_ANNOUNCEMENTS_CHAT_ID or "")[:20] + "...")
        #     try:
        #         run_bybit_announcements_push()
        #     except Exception as e:
        #         logger.exception("Bybit announcements first run error: %s", e)
        # if has_popfun:
        #     _scheduler.add_job(run_popfun_log_push, "interval", minutes=15, id="popfun_log")
        #     logger.info("Popfun log scheduler (every 15 min -> %s)", (FEISHU_POPFUN_LOG_CHAT_ID or "")[:20] + "...")
        #     try:
        #         run_popfun_log_push()
        #     except Exception as e:
        #         logger.exception("Popfun log first run error: %s", e)
        _scheduler.start()
    else:
        logger.debug("No FEISHU_*_CHAT_ID set, schedulers disabled")
    yield
    if _scheduler:
        _scheduler.shutdown(wait=False)
        _scheduler = None
        logger.info("Scheduler stopped")


app = FastAPI(title="LangChain + Lark Webhook", lifespan=_lifespan)
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
    try:
        raw_resp = handler.do(raw_req)
    except EventException as e:
        if "processor not found" in str(e):
            logger.debug("unhandled event type, return 200: %s", e)
            return Response(content=b'{"msg":"success"}', status_code=200, media_type="application/json")
        raise
    return _response_to_http(raw_resp)


GITLAB_WEBHOOK_PATH = "/webhook/gitlab"


@app.post(GITLAB_WEBHOOK_PATH)
async def gitlab_webhook(request: Request):
    """
    接收 GitLab Webhook（如 Merge request events）。
    GitLab 配置：Settings → Webhooks → URL = https://你的域名/webhook/gitlab，勾选 Merge request events。
    若配置了 GITLAB_WEBHOOK_SECRET，请在同一页填写相同的 Secret token。
    """
    body = await request.body()
    event = request.headers.get("X-Gitlab-Event", "").strip()
    token = request.headers.get("X-Gitlab-Token", "").strip()
    logger.info("GitLab webhook received: X-Gitlab-Event=%s, body_len=%d", event, len(body))
    handled, err = handle_gitlab_webhook(body, event, token or None)
    if err:
        logger.warning("GitLab webhook rejected: %s", err)
        return Response(content=err, status_code=403)
    return Response(content=b'{"ok":true}', status_code=200, media_type="application/json")


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
    logger.info("GitLab MR Webhook URL: http://%s:%s%s", WEBHOOK_HOST, port, GITLAB_WEBHOOK_PATH)
    import uvicorn
    uvicorn.run(app, host=WEBHOOK_HOST, port=port)


if __name__ == "__main__":
    main()
