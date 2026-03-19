"""
按 chat_id 缓存「上一条」资费对比等数据，便于用户回复「分析结果」时带入上下文。
"""
import threading
import time

# chat_id -> { "text": str, "at": float }
_funding_compare_cache: dict[str, dict] = {}
_cache_lock = threading.Lock()
DEFAULT_TTL = 600  # 10 分钟


def set_funding_compare_data(chat_id: str, rows: list[dict]) -> None:
    """发送资费对比卡片后调用，缓存该会话的表格摘要，供后续「分析」时注入。"""
    if not chat_id or not rows:
        return
    lines = ["标的\tToobit(%)\t币安(%)\t差值(%)"]
    for r in rows[:200]:
        sym = r.get("symbol_short", "-")
        t_pct = r.get("toobit_rate_pct", 0)
        b_pct = r.get("binance_rate_pct", 0)
        diff = r.get("diff_pct", 0)
        lines.append(f"{sym}\t{t_pct:+.4f}\t{b_pct:+.4f}\t{diff:+.4f}")
    text = "\n".join(lines)
    with _cache_lock:
        _funding_compare_cache[chat_id] = {"text": text, "at": time.time()}


def get_funding_compare_data(chat_id: str, max_age_seconds: int = DEFAULT_TTL) -> str | None:
    """若该会话在 TTL 内发过资费对比卡片，返回缓存的表格文本，否则返回 None。"""
    if not chat_id:
        return None
    with _cache_lock:
        entry = _funding_compare_cache.get(chat_id)
    if not entry or (time.time() - entry["at"]) > max_age_seconds:
        return None
    return entry.get("text")
