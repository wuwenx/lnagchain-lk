"""
JKS（Jenkins）发板 skill：通过飞书指令触发指定 Jenkins 任务构建。
触发：/发板、发板、/jks、jks
配置：config 中的 JKS_URL、JKS_USERNAME、JKS_TOKEN、JKS_JOB_NAME（.env）
参考前端逻辑：Crumb、参数化构建 buildWithParameters、develop 分支默认参数。
"""
import base64
import json
import logging
import urllib.error
import urllib.parse
import urllib.request

from config import JKS_JOB_NAME, JKS_TOKEN, JKS_URL, JKS_USERNAME

logger = logging.getLogger(__name__)

# 参数化构建默认参数（构建 develop 分支）
DEFAULT_BUILD_PARAMS = {
    "TAG": "origin/develop",
    "APP_ENV": "test1",
    "APP_BUILDFORCE": "no",
    "APP_BUILDCMD": "pnpm install && npm run build",
    "APP_BUILDFILE": "dist",
    "APP_NAME": "web-mm-admin-new",
    "APP_SHORTNAME": "mm-admin-new",
    "APP_CLASS": "fe",
    "APP_HOSTNAME": "test-ex-openresty",
}


def _get_crumb(base_url: str, auth_b64: str) -> tuple[str | None, str | None]:
    """获取 Jenkins Crumb，返回 (crumb_request_field, crumb) 或 (None, None)。"""
    url = f"{base_url.rstrip('/')}/crumbIssuer/api/json"
    req = urllib.request.Request(url, headers={"Authorization": f"Basic {auth_b64}", "Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            data = json.loads(r.read().decode())
            return data.get("crumbRequestField"), data.get("crumb")
    except Exception as e:
        logger.debug("get crumb failed: %s", e)
        return None, None


def _job_has_parameters(base_url: str, job_path: str, auth_b64: str) -> bool:
    """判断任务是否为参数化构建。"""
    url = f"{base_url.rstrip('/')}/job/{job_path}/api/json"
    req = urllib.request.Request(url, headers={"Authorization": f"Basic {auth_b64}", "Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            data = json.loads(r.read().decode())
    except Exception as e:
        logger.debug("get job info failed: %s", e)
        return False
    props = data.get("property") or []
    return any(p.get("_class") == "hudson.model.ParametersDefinitionProperty" for p in props)


def _trigger_build() -> tuple[bool, str]:
    """触发 Jenkins 构建，返回 (成功?, 消息)。"""
    if not JKS_URL or not JKS_USERNAME or not JKS_TOKEN or not JKS_JOB_NAME:
        return False, "JKS 未配置：请在 .env 中设置 JKS_URL、JKS_USERNAME、JKS_TOKEN、JKS_JOB_NAME"

    # jobName 如 test/web/web-mm-admin-new -> test/job/web/job/web-mm-admin-new（与前端 replace(/\//g,'/job/') 一致）
    job_path = JKS_JOB_NAME.strip("/").replace("/", "/job/")
    if not job_path:
        return False, "JKS_JOB_NAME 不能为空"

    auth_str = f"{JKS_USERNAME}:{JKS_TOKEN}"
    auth_b64 = base64.b64encode(auth_str.encode()).decode()

    build_url = f"{JKS_URL.rstrip('/')}/job/{job_path}/build"
    body: bytes | None = None
    headers: dict = {}

    # 检测是否参数化构建，是则用 buildWithParameters 并传 develop 等默认参数
    if _job_has_parameters(JKS_URL, job_path, auth_b64):
        logger.info("jks: 检测到参数化构建，使用 buildWithParameters")
        build_url = f"{JKS_URL.rstrip('/')}/job/{job_path}/buildWithParameters"
        body = urllib.parse.urlencode(DEFAULT_BUILD_PARAMS).encode()
        headers["Content-Type"] = "application/x-www-form-urlencoded"

    # 获取 CSRF Crumb
    crumb_field, crumb_value = _get_crumb(JKS_URL, auth_b64)
    if crumb_field and crumb_value:
        headers[crumb_field] = crumb_value

    headers["Authorization"] = f"Basic {auth_b64}"
    headers["Accept"] = "application/json"
    req = urllib.request.Request(build_url, data=body, method="POST", headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            return True, f"已触发 JKS 构建（develop 分支）：{JKS_JOB_NAME}\n构建页：{JKS_URL.rstrip('/')}/job/{job_path}"
    except urllib.error.HTTPError as e:
        err_body = ""
        try:
            err_body = e.read().decode()[:300]
        except Exception:
            pass
        logger.warning("jks build HTTP error: %s %s %s", e.code, e.reason, err_body)
        return False, f"发板失败：HTTP {e.code} {e.reason}"
    except Exception as e:
        logger.exception("jks trigger error")
        return False, f"发板失败：{e}"


class JksSkill:
    id = "jks"
    name = "发板"
    description = "触发 Jenkins 构建（JKS）"
    trigger_commands = ["/发板", "发板", "/jks", "jks"]

    def run(
        self,
        user_message: str,
        *,
        document_context: str | None = None,
        chat_id: str = "",
        **kwargs,
    ) -> str:
        ok, msg = _trigger_build()
        return msg


jks_skill = JksSkill()
