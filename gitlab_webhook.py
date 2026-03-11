"""
GitLab Webhook 处理：接收 Merge Request / Push / Tag Push 事件，组装飞书卡片并发送到指定群。
GitLab 配置：Settings → Webhooks → URL 填 https://你的域名/webhook/gitlab，
勾选 Merge request events、Push events、Tag push events 等。
"""
import json
import logging
import re

from config import (
    FEISHU_MR_CHAT_ID,
    GITLAB_WEBHOOK_SECRET,
    GITLAB_PUSH_ROLLBACK_TAG,
)
from lark_client import send_card_message

logger = logging.getLogger(__name__)

# 飞书 lark_md 中需要转义的特殊字符
_LARK_MD_ESCAPE = re.compile(r"([*_\[\]()`#\\])")
# 从 commit message 中解析回滚 tag：回滚tag xxx / 回滚 tag: xxx / ROLLBACK_TAG: xxx
_ROLLBACK_TAG_PATTERNS = re.compile(
    r"(?:回滚\s*tag\s*[：:]\s*|回滚tag\s+|ROLLBACK_TAG\s*[：:]\s*|回滚\s*[：:]\s*)([A-Za-z0-9_\-\.]+)",
    re.IGNORECASE,
)


def _escape_lark_md(text: str) -> str:
    """简单转义，避免 * _ 等破坏 lark_md 解析。"""
    if not text:
        return ""
    return _LARK_MD_ESCAPE.sub(r"\\\1", text)


def build_mr_card(payload: dict) -> dict | None:
    """
    根据 GitLab Merge Request Webhook payload 构建飞书卡片。
    payload 见：https://docs.gitlab.com/ee/user/project/integrations/webhook_events.html#merge-request-events
    返回飞书 interactive 卡片 dict，解析失败返回 None。
    """
    try:
        oa = payload.get("object_attributes") or {}
        kind = payload.get("object_kind")
        if kind != "merge_request":
            return None
        title = (oa.get("title") or "Merge Request").strip()
        url = oa.get("url") or ""
        state = (oa.get("state") or "opened").lower()
        action = (oa.get("action") or "open").lower()
        description = (oa.get("description") or "").strip()
        source = oa.get("source_branch") or ""
        target = oa.get("target_branch") or ""

        user = payload.get("user") or {}
        author = user.get("name") or user.get("username") or "Unknown"

        project = payload.get("project") or {}
        project_path = project.get("path_with_namespace") or ""

        # 状态文案
        if state == "merged":
            state_text = "已合并"
            template = "green"
        elif state == "closed":
            state_text = "已关闭"
            template = "grey"
        else:
            state_text = "打开" if action == "open" else "更新"
            template = "blue"

        desc_preview = ""
        if description:
            # 截断并做简单换行处理，避免卡片过长
            desc_one_line = description.replace("\n", " ").strip()[:200]
            if len(description) > 200:
                desc_one_line += "..."
            desc_preview = _escape_lark_md(desc_one_line)

        elements = [
            {
                "tag": "div",
                "text": {
                    "tag": "lark_md",
                    "content": f"**作者**：{_escape_lark_md(author)}",
                },
            },
        ]
        if project_path:
            elements.append({
                "tag": "div",
                "text": {
                    "tag": "lark_md",
                    "content": f"**项目**：{_escape_lark_md(project_path)}",
                },
            })
        if source or target:
            elements.append({
                "tag": "div",
                "text": {
                    "tag": "lark_md",
                    "content": f"**分支**：{_escape_lark_md(source)} → {_escape_lark_md(target)}",
                },
            })
        if desc_preview:
            elements.append({"tag": "hr"})
            elements.append({
                "tag": "div",
                "text": {"tag": "lark_md", "content": "**描述摘要**\n" + desc_preview},
            })
        if url:
            elements.append({"tag": "hr"})
            elements.append({
                "tag": "div",
                "text": {"tag": "lark_md", "content": f"[打开 MR]({url})"},
            })

        header_title = f"Merge Request · {state_text}"
        return {
            "config": {"wide_screen_mode": True},
            "header": {
                "title": {"tag": "plain_text", "content": header_title[:30], "lines": 1},
                "template": template,
            },
            "elements": [
                {
                    "tag": "div",
                    "text": {"tag": "lark_md", "content": f"**{_escape_lark_md(title)}**"},
                },
                {"tag": "hr"},
                *elements,
            ],
        }
    except Exception as e:
        logger.exception("build_mr_card error: %s", e)
        return None


def build_push_card(payload: dict) -> dict | None:
    """
    根据 GitLab Push Hook 构建「发版/配置变更」模版卡片。
    从 Git 提交记录获取：
    - 需求内容：最近一条 commit 的首行
    - 发布tag/发布分支：webhook 的 ref（refs/tags/xxx 或 refs/heads/xxx）
    - 回滚tag：最近一条 commit 的 message 中若包含「回滚tag xxx」「回滚 tag: xxx」「ROLLBACK_TAG: xxx」则解析，否则用配置 GITLAB_PUSH_ROLLBACK_TAG
    """
    try:
        kind = payload.get("object_kind")
        if kind != "push":
            return None
        ref = (payload.get("ref") or "").strip()
        is_tag = ref.startswith("refs/tags/")
        ref_short = ref.replace("refs/tags/", "").replace("refs/heads/", "") if ref else ""
        project = payload.get("project") or {}
        project_path = project.get("path_with_namespace") or ""
        project_url = project.get("web_url") or project.get("git_http_url") or ""
        commits = payload.get("commits") or []
        user_name = (payload.get("user_name") or payload.get("user_username") or "").strip() or "Unknown"

        # 需求内容：最近一条 commit 的首行；发布 tag/分支：来自 ref
        demand_content = ref_short
        last_msg_full = ""
        last_url = ""
        if commits:
            last = commits[-1]
            last_msg_full = (last.get("message") or "").strip()
            first_line = last_msg_full.split("\n")[0][:80].strip()
            if first_line:
                demand_content = first_line
            last_url = last.get("url") or ""

        # 回滚 tag：优先从最近一条 commit 的完整 message 中解析，否则用配置
        rollback = (GITLAB_PUSH_ROLLBACK_TAG or "").strip()
        if last_msg_full:
            m = _ROLLBACK_TAG_PATTERNS.search(last_msg_full)
            if m:
                rollback = m.group(1).strip()

        # 操作内容：发布分支 / 发布 tag + 回滚 tag
        if is_tag:
            op_content = f"发布tag {ref_short}"
        else:
            op_content = f"发布分支 {ref_short}"
        if rollback:
            op_content += f"\n回滚tag {rollback}"

        e = _escape_lark_md
        lines = [
            f"**需求内容**：{e(demand_content)}",
            f"**开发负责人**：@{e(user_name)}",
            "**操作内容**：",
            op_content,
        ]
        body_md = "\n".join(lines)
        elements = [
            {"tag": "div", "text": {"tag": "lark_md", "content": body_md}},
        ]
        if project_path:
            elements.append({
                "tag": "div",
                "text": {"tag": "lark_md", "content": f"**项目**：{e(project_path)}"},
            })
        link_parts = []
        if project_url:
            link_parts.append(f"[查看项目]({project_url})")
        if last_url:
            link_parts.append(f"[查看提交]({last_url})")
        if link_parts:
            elements.append({"tag": "hr"})
            elements.append({"tag": "div", "text": {"tag": "lark_md", "content": " ".join(link_parts)}})

        title_text = f"发版 · {ref_short}" if ref_short else "Push 发版"
        return {
            "config": {"wide_screen_mode": True},
            "header": {
                "title": {"tag": "plain_text", "content": title_text[:30], "lines": 1},
                "template": "blue",
            },
            "elements": elements,
        }
    except Exception as e:
        logger.exception("build_push_card error: %s", e)
        return None


def build_tag_push_card(payload: dict) -> dict | None:
    """
    Tag push 事件专用：提示「最新推送的 tag 是 xxx」，避免多人上线覆盖。
    GitLab 勾选 Tag push events 时，X-Gitlab-Event 为 Tag Push Hook，object_kind 为 tag_push。
    """
    try:
        kind = payload.get("object_kind")
        if kind != "tag_push":
            return None
        ref = (payload.get("ref") or "").strip()
        tag_name = ref.replace("refs/tags/", "") if ref.startswith("refs/tags/") else ref
        if not tag_name:
            tag_name = payload.get("ref") or "未知"
        project = payload.get("project") or {}
        project_path = project.get("path_with_namespace") or ""
        project_url = (project.get("web_url") or project.get("git_http_url") or "").rstrip("/")
        user_name = (payload.get("user_name") or payload.get("user_username") or "").strip() or "Unknown"
        tag_url = f"{project_url}/-/tags/{tag_name}" if project_url and tag_name else project_url

        e = _escape_lark_md
        tip = "请注意避免多人上线覆盖。"
        lines = [
            f"**最新推送的 tag 是**：{e(tag_name)}",
            f"{tip}",
            "",
            f"**推送人**：@{e(user_name)}",
        ]
        if project_path:
            lines.append(f"**项目**：{e(project_path)}")
        body_md = "\n".join(lines)
        elements = [{"tag": "div", "text": {"tag": "lark_md", "content": body_md}}]
        if tag_url:
            elements.append({"tag": "hr"})
            elements.append({
                "tag": "div",
                "text": {"tag": "lark_md", "content": f"[查看 Tag]({tag_url})"},
            })

        return {
            "config": {"wide_screen_mode": True},
            "header": {
                "title": {"tag": "plain_text", "content": f"Tag 推送 · {tag_name}"[:30], "lines": 1},
                "template": "orange",
            },
            "elements": elements,
        }
    except Exception as e:
        logger.exception("build_tag_push_card error: %s", e)
        return None


def handle_gitlab_webhook(body: bytes, x_gitlab_event: str | None, x_gitlab_token: str | None) -> tuple[bool, str]:
    """
    处理 GitLab Webhook 请求体。
    :return: (是否已处理并发送卡片, 错误信息，空表示成功)
    """
    if GITLAB_WEBHOOK_SECRET and x_gitlab_token != GITLAB_WEBHOOK_SECRET:
        return False, "GITLAB_WEBHOOK_SECRET 与请求头 X-Gitlab-Token 不一致"

    event = (x_gitlab_event or "").strip()
    if event not in ("Merge Request Hook", "Push Hook", "Tag Push Hook"):
        return False, ""

    if not FEISHU_MR_CHAT_ID:
        logger.warning("FEISHU_MR_CHAT_ID 未配置，无法发送 GitLab 卡片到飞书。请在 .env 中设置 FEISHU_MR_CHAT_ID 为要接收通知的群 chat_id")
        return True, ""

    try:
        payload = json.loads(body.decode("utf-8"))
    except Exception as e:
        logger.warning("gitlab webhook body json error: %s", e)
        return False, ""

    if event == "Merge Request Hook":
        card = build_mr_card(payload)
    elif event == "Tag Push Hook":
        card = build_tag_push_card(payload)
    else:
        card = build_push_card(payload)

    if not card:
        return True, ""

    mid = send_card_message(FEISHU_MR_CHAT_ID, card)
    if mid:
        logger.info("gitlab %s card sent to chat_id=%s message_id=%s", event, FEISHU_MR_CHAT_ID, mid)
    else:
        logger.error("gitlab card send failed for chat_id=%s", FEISHU_MR_CHAT_ID)
    return True, ""
