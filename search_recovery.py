"""有界恢复检索429和明确的临时网络错误；不重跑Planner/Reader或叠加SDK重试。"""

import asyncio
from copy import deepcopy
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
import math
import random

DEFAULT_DELAYS = (2, 4, 8)
TRANSIENT_TRANSPORT_ERRORS = {"CONNECT_TIMEOUT", "READ_TIMEOUT", "WRITE_TIMEOUT", "POOL_TIMEOUT",
                              "DNS_ERROR", "CONNECT_ERROR", "CONNECTION_INTERRUPTED"}


def parse_retry_after(value, *, at=None):
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        # HTTP Retry-After 支持非负整数秒或 HTTP-date，不解释其他自定义限流头。
        if value.strip().isdigit():
            seconds = float(value.strip())
        else:
            date = parsedate_to_datetime(value)
            if date.tzinfo is None:
                date = date.replace(tzinfo=timezone.utc)
            seconds = max(0, (date - (at or datetime.now(timezone.utc))).total_seconds())
        return seconds if math.isfinite(seconds) and seconds >= 0 else None
    except (ValueError, OverflowError, TypeError):
        return None


async def recover_rate_limit(call, *, delays=DEFAULT_DELAYS, sleep=asyncio.sleep, progress=None,
                             jitter=None):
    """首次调用+最多3次总重试；其中临时网络重试最多2次。

    Only retry a transport error with a matching latest observed request, no HTTP
    response, and an explicit retryable classification. Never infer a network
    failure from the broad PROVIDER_UNAVAILABLE code alone. TLS/proxy/config
    failures and unknown states fail closed. No inner SDK/Provider retries.
    """
    delays = tuple(delays)
    if len(delays) > 3 or any(type(delay) not in (int, float) or not 0 < delay <= 60 for delay in delays):
        raise ValueError("限流恢复只允许最多三次、每次 1—60 秒的有界等待")
    history = []
    retry_events = []
    waited = 0
    network_retries = 0
    previous_sequence = None
    jitter = jitter if jitter is not None else lambda: random.uniform(0.1, 0.3)
    for index in range(len(delays) + 1):
        outcome = await call()
        error = outcome.get("error")
        limited = bool(error and error.get("code") == "RATE_LIMITED" and error.get("retryable") is True)
        requests = outcome.get("diagnostics", {}).get("httpRequests", [])
        latest = requests[-1] if requests else {}
        sequence = latest.get("requestSequence")
        network = bool(error and error.get("code") == "PROVIDER_UNAVAILABLE" and error.get("retryable") is True
                       and latest.get("httpStatus") is None and latest.get("transportRetryable") is True
                       and latest.get("transportError") in TRANSIENT_TRANSPORT_ERRORS
                       and (sequence is None or sequence != previous_sequence))
        previous_sequence = sequence
        retryable = limited or (network and network_retries < 2)
        # 不保存原始提供商消息或完整请求，避免在重试日志中传播凭据。
        code = error.get("code") if error else None
        allowed = {"RATE_LIMITED", "INVALID_REQUEST", "PROVIDER_UNAVAILABLE", "UNKNOWN_PROVIDER_ERROR"}
        history.append({"call": index + 1, "errorCode": code if code in allowed else ("OTHER_ERROR" if error else None),
                        "metrics": deepcopy(outcome.get("diagnostics", {}).get("metrics", {}))})
        header = outcome.get("diagnostics", {}).get("retryAfterHeader")
        server_delay = parse_retry_after(header)
        budget_exceeded = limited and server_delay is not None and server_delay > 120
        if not retryable or index == len(delays) or budget_exceeded:
            final = deepcopy(outcome)
            final.setdefault("diagnostics", {})["rateLimitRecovery"] = {
                "calls": len(history), "retries": len(history) - 1,
                "totalWaitSeconds": round(waited, 3), "exhausted": limited,
                "attempts": history, "retryEvents": retry_events,
                "stoppedForServerWaitBudget": budget_exceeded,
            }
            final["diagnostics"]["transientNetworkRecovery"] = {
                "retries": network_retries, "maxRetries": min(2, len(delays)),
                "exhausted": network, "lastReason": latest.get("transportError") if network else None}
            return final
        extra = jitter()
        if not isinstance(extra, (int, float)) or not math.isfinite(extra) or not 0 <= extra <= 0.5:
            raise ValueError("jitter 必须是 0—0.5 秒")
        if network:
            delay = round((1, 2)[network_retries] + extra, 3)
            network_retries += 1
            source, reason = "bounded-network-backoff", "transient_network"
        else:
            delay = round((server_delay if server_delay is not None else delays[index]) + extra, 3)
            source, reason = "retry-after" if server_delay is not None else "fallback", "rate_limit"
        retry_events.append({"retryNumber": index + 1, "reason": reason,
                             "httpStatus": latest.get("httpStatus"),
                             "transportError": latest.get("transportError") if network else None,
                             "retryAfterHeader": header if limited else None, "selectedWaitSeconds": delay,
                             "waitSource": source, "jitterSeconds": round(extra, 3)})
        if progress:
            if network:
                progress(f"检索网络暂时失败（{latest['transportError']}），等待 {delay} 秒后重试（{network_retries}/2）；只重试检索，不再调用模型。")
            else:
                progress(f"检索被限流，等待 {delay} 秒后自动重试（{index + 1}/{len(delays)}）；不再调用模型。")
        # 长服务端等待分段，保持可取消且可给用户进度；不会提前于 Retry-After 重试。
        remaining = delay
        while remaining > 0:
            chunk = min(30, remaining)
            await sleep(chunk)
            remaining = round(remaining - chunk, 3)
        waited += delay
