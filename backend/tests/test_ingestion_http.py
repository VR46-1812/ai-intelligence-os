"""Deterministic bounded HTTP transport tests for M2.1."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable

import httpx
import pytest

from app.ingestion.contracts import ConnectorErrorCode, ConnectorException
from app.ingestion.http import BoundedHttpClient


def _client(
    handler: Callable[[httpx.Request], httpx.Response],
    *,
    retries: int = 3,
    maximum_bytes: int = 1024,
    concurrency: int = 3,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    monotonic: Callable[[], float] | None = None,
) -> tuple[httpx.AsyncClient, BoundedHttpClient]:
    async_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    bounded = BoundedHttpClient(
        async_client,
        maximum_retries=retries,
        initial_backoff_seconds=1,
        maximum_backoff_seconds=10,
        maximum_response_bytes=maximum_bytes,
        concurrency=concurrency,
        sleep=sleep,
        monotonic=monotonic or (lambda: 0.0),
        jitter=lambda: 0.0,
    )
    return async_client, bounded


def test_retries_429_and_5xx_with_bounded_backoff_then_returns_typed_response() -> None:
    attempts = 0
    delays: list[float] = []

    def handler(_: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            return httpx.Response(429, headers={"Retry-After": "2"})
        if attempts == 2:
            return httpx.Response(503)
        return httpx.Response(
            200,
            content=b"fixture-response",
            headers={"Content-Type": "application/atom+xml", "ETag": "fixture-etag"},
        )

    async def sleep(delay: float) -> None:
        delays.append(delay)

    async def exercise() -> None:
        async_client, bounded = _client(handler, sleep=sleep)
        try:
            response = await bounded.get(
                "https://source.test/feed",
                source_key="fixture",
                minimum_request_interval_ms=0,
                expected_media_types=("application/atom+xml",),
            )
        finally:
            await async_client.aclose()
        assert response.content == b"fixture-response"
        assert response.response_metadata["etag"] == "fixture-etag"

    asyncio.run(exercise())
    assert attempts == 3
    assert delays == [1.0, 1.0]


def test_auth_failure_is_not_retried_and_credentials_are_not_exposed(
    caplog: pytest.LogCaptureFixture,
) -> None:
    attempts = 0
    secret = "super-secret-token"

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        assert request.headers["Authorization"] == f"Bearer {secret}"
        return httpx.Response(401)

    async def exercise() -> ConnectorException:
        async_client, bounded = _client(handler)
        try:
            with pytest.raises(ConnectorException) as captured:
                await bounded.get(
                    "https://source.test/private",
                    source_key="fixture",
                    minimum_request_interval_ms=0,
                    expected_media_types=("application/json",),
                    headers={"Authorization": f"Bearer {secret}"},
                )
            return captured.value
        finally:
            await async_client.aclose()

    error = asyncio.run(exercise())
    assert error.failure.code is ConnectorErrorCode.AUTH_REQUIRED
    assert error.failure.retryable is False
    assert attempts == 1
    assert secret not in str(error)
    assert secret not in error.failure.model_dump_json()
    assert secret not in caplog.text


def test_network_timeout_stops_at_hard_three_attempt_limit() -> None:
    attempts = 0
    delays: list[float] = []

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        raise httpx.ReadTimeout("fixture timeout", request=request)

    async def sleep(delay: float) -> None:
        delays.append(delay)

    async def exercise() -> ConnectorException:
        async_client, bounded = _client(handler, retries=5, sleep=sleep)
        try:
            with pytest.raises(ConnectorException) as captured:
                await bounded.get(
                    "https://source.test/feed",
                    source_key="fixture",
                    minimum_request_interval_ms=0,
                    expected_media_types=("application/json",),
                )
            return captured.value
        finally:
            await async_client.aclose()

    error = asyncio.run(exercise())
    assert error.failure.code is ConnectorErrorCode.NETWORK_TIMEOUT
    assert error.failure.attempts == 3
    assert attempts == 3
    assert len(delays) == 2


@pytest.mark.parametrize(
    ("headers", "content", "expected_code"),
    [
        (
            {"Content-Type": "application/json", "Content-Length": "100"},
            b"x",
            ConnectorErrorCode.CONTENT_TOO_LARGE,
        ),
        ({"Content-Type": "application/json"}, b"0123456789", ConnectorErrorCode.CONTENT_TOO_LARGE),
        ({"Content-Type": "text/html"}, b"small", ConnectorErrorCode.UNSUPPORTED_MEDIA),
    ],
)
def test_size_and_media_limits_fail_without_retry(
    headers: dict[str, str], content: bytes, expected_code: ConnectorErrorCode
) -> None:
    attempts = 0

    def handler(_: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        return httpx.Response(200, headers=headers, content=content)

    async def exercise() -> ConnectorException:
        async_client, bounded = _client(handler, maximum_bytes=5)
        try:
            with pytest.raises(ConnectorException) as captured:
                await bounded.get(
                    "https://source.test/feed",
                    source_key="fixture",
                    minimum_request_interval_ms=0,
                    expected_media_types=("application/json",),
                )
            return captured.value
        finally:
            await async_client.aclose()

    error = asyncio.run(exercise())
    assert error.failure.code is expected_code
    assert attempts == 1


def test_rate_limit_spacing_skips_first_delay_and_spaces_following_request() -> None:
    now = 10.0
    delays: list[float] = []

    def monotonic() -> float:
        return now

    async def sleep(delay: float) -> None:
        nonlocal now
        delays.append(delay)
        now += delay

    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(200, headers={"Content-Type": "application/json"}, content=b"{}")

    async def exercise() -> None:
        async_client, bounded = _client(handler, sleep=sleep, monotonic=monotonic)
        try:
            for _ in range(2):
                await bounded.get(
                    "https://source.test/feed",
                    source_key="fixture",
                    minimum_request_interval_ms=3000,
                    expected_media_types=("application/json",),
                )
        finally:
            await async_client.aclose()

    asyncio.run(exercise())
    assert delays == [3.0]


def test_concurrency_never_exceeds_configured_source_limit() -> None:
    active = 0
    maximum_active = 0

    async def handler(_: httpx.Request) -> httpx.Response:
        nonlocal active, maximum_active
        active += 1
        maximum_active = max(maximum_active, active)
        await asyncio.sleep(0.01)
        active -= 1
        return httpx.Response(200, headers={"Content-Type": "application/json"}, content=b"{}")

    async def exercise() -> None:
        async_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        bounded = BoundedHttpClient(
            async_client,
            maximum_retries=3,
            initial_backoff_seconds=1,
            maximum_backoff_seconds=10,
            maximum_response_bytes=1024,
            concurrency=2,
        )
        try:
            await asyncio.gather(
                *(
                    bounded.get(
                        f"https://source.test/feed/{index}",
                        source_key=f"fixture-{index}",
                        minimum_request_interval_ms=0,
                        expected_media_types=("application/json",),
                    )
                    for index in range(5)
                )
            )
        finally:
            await async_client.aclose()

    asyncio.run(exercise())
    assert maximum_active == 2
