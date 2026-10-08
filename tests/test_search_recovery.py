"""限流恢复离线测试：注入等待，不访问 API，不实际睡眠。"""

from copy import deepcopy
from datetime import datetime, timedelta, timezone
from email.utils import format_datetime
import unittest
from unittest.mock import AsyncMock, Mock

from search_recovery import parse_retry_after, recover_rate_limit


def limited(retryable=True):
    return {"result": None, "error": {"code": "RATE_LIMITED", "retryable": retryable},
            "diagnostics": {"requestId": "same-request", "metrics": {"attempts": 1, "durationMs": 1}}}


def success():
    return {"result": {"items": [{"kind": "paper", "data": {}}]}, "error": None,
            "diagnostics": {"requestId": "same-request", "metrics": {"attempts": 1, "durationMs": 1}}}


class RecoveryTests(unittest.IsolatedAsyncioTestCase):
    async def test_success_no_wait_or_retry(self):
        call, sleep = AsyncMock(return_value=success()), AsyncMock()
        out = await recover_rate_limit(call, sleep=sleep)
        call.assert_awaited_once(); sleep.assert_not_awaited()
        self.assertEqual(out["diagnostics"]["rateLimitRecovery"]["calls"], 1)

    async def test_rate_limited_then_success(self):
        call, sleep, progress = AsyncMock(side_effect=[limited(), success()]), AsyncMock(), Mock()
        out = await recover_rate_limit(call, sleep=sleep, progress=progress, jitter=lambda: 0)
        self.assertIsNone(out["error"])
        sleep.assert_awaited_once_with(2)
        self.assertEqual(call.await_count, 2)
        self.assertEqual(out["diagnostics"]["rateLimitRecovery"]["totalWaitSeconds"], 2)
        self.assertIn("不再调用模型", progress.call_args.args[0])

    async def test_persistent_limits_stop_at_four_calls(self):
        call, sleep = AsyncMock(return_value=limited()), AsyncMock()
        out = await recover_rate_limit(call, sleep=sleep, jitter=lambda: 0)
        self.assertEqual(call.await_count, 4)
        self.assertEqual([c.args[0] for c in sleep.await_args_list], [2, 4, 8])
        stats = out["diagnostics"]["rateLimitRecovery"]
        self.assertTrue(stats["exhausted"])
        self.assertEqual(stats["totalWaitSeconds"], 14)
        self.assertEqual(stats["retries"], 3)
        self.assertEqual(out["error"]["code"], "RATE_LIMITED")

    async def test_last_retry_can_succeed(self):
        call, sleep = AsyncMock(side_effect=[limited(), limited(), limited(), success()]), AsyncMock()
        out = await recover_rate_limit(call, sleep=sleep)
        self.assertEqual(call.await_count, 4)
        self.assertFalse(out["diagnostics"]["rateLimitRecovery"]["exhausted"])

    async def test_non_retryable_limit_not_retried(self):
        call, sleep = AsyncMock(return_value=limited(False)), AsyncMock()
        await recover_rate_limit(call, sleep=sleep)
        call.assert_awaited_once(); sleep.assert_not_awaited()

    async def test_other_error_not_retried(self):
        for code in ("INVALID_REQUEST", "PROVIDER_UNAVAILABLE", "UNKNOWN_PROVIDER_ERROR"):
            data = limited(); data["error"]["code"] = code
            call, sleep = AsyncMock(return_value=data), AsyncMock()
            await recover_rate_limit(call, sleep=sleep)
            call.assert_awaited_once(); sleep.assert_not_awaited()

    async def test_empty_search_is_not_retried(self):
        data = success(); data["result"]["items"] = []
        call, sleep = AsyncMock(return_value=data), AsyncMock()
        out = await recover_rate_limit(call, sleep=sleep)
        call.assert_awaited_once(); sleep.assert_not_awaited()
        self.assertEqual(out["result"]["items"], [])

    async def test_original_outcome_is_not_mutated(self):
        raw = success(); before = deepcopy(raw)
        await recover_rate_limit(AsyncMock(return_value=raw), sleep=AsyncMock())
        self.assertEqual(raw, before)

    async def test_invalid_policy_no_request(self):
        for delays in ((0,), (-1,), (61,), (True,), (float("nan"),), (1, 2, 3, 4)):
            call = AsyncMock()
            with self.assertRaises(ValueError):
                await recover_rate_limit(call, delays=delays, sleep=AsyncMock())
            call.assert_not_awaited()

    async def test_zero_retries_allowed(self):
        call, sleep = AsyncMock(return_value=limited()), AsyncMock()
        out = await recover_rate_limit(call, delays=(), sleep=sleep)
        call.assert_awaited_once(); sleep.assert_not_awaited()
        self.assertTrue(out["diagnostics"]["rateLimitRecovery"]["exhausted"])

    async def test_server_retry_after_takes_priority_and_adds_jitter(self):
        first = limited()
        first["diagnostics"].update(retryAfterHeader="3", httpRequests=[{"httpStatus": 429}])
        call, sleep = AsyncMock(side_effect=[first, success()]), AsyncMock()
        result = await recover_rate_limit(call, sleep=sleep, jitter=lambda: .2)
        sleep.assert_awaited_once_with(3.2)
        event = result["diagnostics"]["rateLimitRecovery"]["retryEvents"][0]
        self.assertEqual(event["waitSource"], "retry-after")
        self.assertEqual(event["retryAfterHeader"], "3")
        self.assertEqual(event["httpStatus"], 429)
        self.assertEqual(event["selectedWaitSeconds"], 3.2)

    async def test_invalid_header_uses_fallback(self):
        first = limited(); first["diagnostics"]["retryAfterHeader"] = "invalid"
        call, sleep = AsyncMock(side_effect=[first, success()]), AsyncMock()
        result = await recover_rate_limit(call, sleep=sleep, jitter=lambda: 0)
        sleep.assert_awaited_once_with(2)
        self.assertEqual(result["diagnostics"]["rateLimitRecovery"]["retryEvents"][0]["waitSource"], "fallback")

    async def test_very_long_server_wait_stops_instead_of_retrying_too_early(self):
        first = limited(); first["diagnostics"]["retryAfterHeader"] = "3600"
        call, sleep = AsyncMock(return_value=first), AsyncMock()
        result = await recover_rate_limit(call, sleep=sleep)
        call.assert_awaited_once(); sleep.assert_not_awaited()
        self.assertTrue(result["diagnostics"]["rateLimitRecovery"]["stoppedForServerWaitBudget"])

    async def test_large_server_delay_is_cancelable_in_short_chunks(self):
        first = limited(); first["diagnostics"]["retryAfterHeader"] = "65"
        call, sleep = AsyncMock(side_effect=[first, success()]), AsyncMock()
        await recover_rate_limit(call, sleep=sleep, jitter=lambda: 0)
        self.assertEqual([c.args[0] for c in sleep.await_args_list], [30, 30, 5])

    def test_http_date_and_missing_header(self):
        at = datetime(2026, 10, 8, tzinfo=timezone.utc)
        self.assertEqual(parse_retry_after(format_datetime(at + timedelta(seconds=5)), at=at), 5)
        self.assertEqual(parse_retry_after("0"), 0)
        self.assertIsNone(parse_retry_after(None))
        self.assertIsNone(parse_retry_after("-1"))


if __name__ == "__main__":
    unittest.main()
