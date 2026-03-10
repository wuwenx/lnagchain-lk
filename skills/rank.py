"""
/rank skill：获取 CMC 交易所排名，突出显示 Toobit 的排名。
使用 CoinMarketCap Pro API：/v1/exchange/listings/latest
需在 .env 中配置 CMC_API_KEY。
"""
import json
import logging
import ssl
import urllib.parse
import urllib.request

import certifi

from config import CMC_API_KEY

logger = logging.getLogger(__name__)

CMC_BASE = "https://pro-api.coinmarketcap.com"
LISTINGS_URL = f"{CMC_BASE}/v1/exchange/listings/latest"
MAP_URL = f"{CMC_BASE}/v1/exchange/map"  # 备用：Basic 可能仅开放 map
TOOBIT_NAME = "toobit"  # 匹配 name/slug 中含 toobit 的交易所
TOP_N = 15  # 展示前 N 名
FETCH_LIMIT = 200  # listings 拉取数量
MAP_LIMIT = 500    # map 备用时拉取数量（尽量包含 Toobit）


def _request_cmc(url: str) -> list[dict] | None:
    """请求 CMC API，返回 data 数组；失败返回 None。"""
    if not CMC_API_KEY:
        return None
    try:
        ctx = ssl.create_default_context(cafile=certifi.where())
        req = urllib.request.Request(
            url,
            headers={
                "X-CMC_PRO_API_KEY": CMC_API_KEY,
                "Accept": "application/json",
                "User-Agent": "FeishuBot/1.0",
            },
        )
        with urllib.request.urlopen(req, timeout=15, context=ctx) as resp:
            data = json.loads(resp.read().decode())
            if not data.get("data"):
                return None
            return data["data"]
    except Exception as e:
        logger.warning("CMC request %s: %s", url, e)
        return None


def _fetch_exchange_listings() -> tuple[list[dict] | None, bool]:
    """
    优先用 listings（官网现货排名），403 时回退到 map（列表顺序，非排名）。
    返回 (data, is_real_rank)：is_real_rank 表示是否为官网排名顺序。
    """
    params = urllib.parse.urlencode({"start": 1, "limit": FETCH_LIMIT})
    listings_url = f"{LISTINGS_URL}?{params}"
    out = _request_cmc(listings_url)
    if out is not None:
        return out, True
    map_url = f"{MAP_URL}?limit={MAP_LIMIT}&listing_status=active"
    out = _request_cmc(map_url)
    return (out, False) if out else (None, False)


def _find_toobit(exchanges: list[dict]) -> dict | None:
    for ex in exchanges:
        name = (ex.get("name") or "").lower()
        slug = (ex.get("slug") or "").lower()
        if TOOBIT_NAME in name or TOOBIT_NAME in slug:
            return ex
    return None


def run_rank(
    user_message: str,
    *,
    document_context: str | None = None,
    chat_id: str = "",
    **kwargs,
) -> str:
    """执行 /rank：返回 CMC 交易所排名，突出 Toobit。"""
    if not CMC_API_KEY:
        return "请先在 .env 中配置 CMC_API_KEY 后使用 /rank。"
    result = _fetch_exchange_listings()
    if result[0] is None:
        return "暂时无法获取 CMC 交易所排名，请稍后再试。"
    exchanges, is_real_rank = result
    toobit = _find_toobit(exchanges)
    if is_real_rank:
        title = "**CMC 现货交易所排名（前 %d 名）**\n" % TOP_N
        rank_note = ""
    else:
        title = "**CMC 交易所列表（前 %d，API 列表顺序）**\n" % TOP_N
        rank_note = "\n（当前 API 计划未开放排名接口，上表为列表顺序非官网排名；官网现货排名 Toobit 约 **第 25 名**）"
    lines = [title]
    for i, ex in enumerate(exchanges[:TOP_N], start=1):
        name = ex.get("name") or ex.get("slug") or "—"
        is_toobit = toobit and ex.get("id") == toobit.get("id")
        if is_toobit:
            lines.append(f"> **#{i} 🎯 Toobit**（当前排名）")
        else:
            lines.append(f"#{i} {name}")
    if toobit:
        toobit_pos = next(
            (i for i, ex in enumerate(exchanges, start=1) if ex.get("id") == toobit.get("id")),
            None,
        )
        if toobit_pos and toobit_pos > TOP_N:
            if is_real_rank:
                lines.append(f"> **#{toobit_pos} 🎯 Toobit**（当前排名）")
            else:
                lines.append(f"> 列表中第 {toobit_pos} 条 🎯 **Toobit** — 官网现货排名约 **第 25 名**")
        lines.append("")
        lines.append("— Toobit 已用 🎯 标出")
        if rank_note:
            lines.append(rank_note)
    else:
        lines.append("")
        lines.append("（未在本次列表中找到 Toobit）")
    return "\n".join(lines)


class RankSkill:
    id = "rank"
    name = "CMC 交易所排名"
    description = "获取 CoinMarketCap 交易所排名，突出显示 Toobit"
    trigger_commands = ["/rank", "交易所排名", "cmc排名"]

    def run(
        self,
        user_message: str,
        *,
        document_context: str | None = None,
        chat_id: str = "",
        **kwargs,
    ) -> str:
        return run_rank(user_message, document_context=document_context, chat_id=chat_id, **kwargs)


rank_skill = RankSkill()
