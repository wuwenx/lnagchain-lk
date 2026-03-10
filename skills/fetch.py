"""
/抓取 skill：用 Playwright 打开用户提供的网址，抓取页面正文后交给大模型分析并输出到 Lark。
当消息中同时包含「获取」或「抓取」且包含一个 http(s) 链接时触发。
"""
import re
import logging

from langchain_agent import reply as langchain_reply

logger = logging.getLogger(__name__)

# 消息中需包含的触发词（任一）
TRIGGER_WORDS = ("获取", "抓取")
# 匹配 http(s) URL（简单，不含空格）
URL_PATTERN = re.compile(r"https?://[^\s]+", re.IGNORECASE)
# 抓取正文最大字符数，避免超出模型上下文
MAX_PAGE_CHARS = 35000


def _extract_first_url(text: str) -> str | None:
    m = URL_PATTERN.search(text)
    return m.group(0).strip() if m else None


def _fetch_page_text(url: str) -> str | None:
    """用 Playwright 打开 URL，返回页面 body 文本。"""
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        logger.error("playwright not installed, run: pip install playwright && playwright install chromium")
        return None
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=False)
            try:
                page = browser.new_page()
                page.goto(url, wait_until="domcontentloaded", timeout=15000)
                page.wait_for_timeout(2000)  # 给简单 JS 一点渲染时间
                body = page.locator("body")
                text = body.inner_text() if body.count() else ""
                return (text or "").strip()
            finally:
                browser.close()
    except Exception as e:
        logger.exception("playwright fetch error url=%s: %s", url, e)
        return None


def _should_trigger_fetch(text: str) -> bool:
    """消息是否应触发抓取：包含触发词且包含 URL。"""
    if not text or not text.strip():
        return False
    t = text.strip()
    has_trigger = any(w in t for w in TRIGGER_WORDS)
    has_url = URL_PATTERN.search(t) is not None
    return has_trigger and has_url


def run_fetch(
    user_message: str,
    *,
    document_context: str | None = None,
    chat_id: str = "",
    **kwargs,
) -> str:
    """执行抓取：从消息中取 URL → Playwright 抓正文 → 大模型分析 → 返回结果。"""
    url = _extract_first_url(user_message)
    if not url:
        return "请提供要抓取的网址（消息中需包含以 http 或 https 开头的链接）。"
    page_text = _fetch_page_text(url)
    if not page_text:
        return f"抓取失败：无法获取该页面内容，请检查链接是否可访问或稍后重试。\n链接：{url}"
    if len(page_text) > MAX_PAGE_CHARS:
        page_text = page_text[:MAX_PAGE_CHARS] + "\n\n[内容已截断]"
    # 将网页内容作为上下文，用户原消息作为问题交给大模型
    prompt = (
        "以下是一则网页的正文内容，请根据用户的问题对网页内容进行总结或分析，回复简洁清晰。\n\n"
        "【网页内容】\n"
        f"{page_text}\n\n"
        "【用户问题】\n"
        f"{user_message}"
    )
    return langchain_reply(prompt, document_context=None)


class FetchSkill:
    id = "fetch"
    name = "网页抓取"
    description = "输入网址并说「获取」或「抓取」时，用 Playwright 抓取页面并由大模型分析"
    trigger_commands = ["/抓取", "/获取"]

    def run(
        self,
        user_message: str,
        *,
        document_context: str | None = None,
        chat_id: str = "",
        **kwargs,
    ) -> str:
        return run_fetch(user_message, document_context=document_context, chat_id=chat_id, **kwargs)


fetch_skill = FetchSkill()


def should_trigger_fetch(text: str) -> bool:
    """供 handlers 判断：是否应走抓取流程（含 URL + 获取/抓取）。"""
    return _should_trigger_fetch(text)
