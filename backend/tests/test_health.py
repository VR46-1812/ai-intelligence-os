"""Health endpoint contract tests."""

import asyncio

from httpx import ASGITransport, AsyncClient, Response

from app.main import app


async def request_health() -> Response:
    """Exercise the application through its ASGI boundary."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        return await client.get("/health")


def test_health_returns_stable_contract() -> None:
    """The scaffold exposes a healthy, typed response without dependencies."""
    response = asyncio.run(request_health())

    assert response.status_code == 200
    assert response.json() == {"service": "ai-intelligence-os", "status": "ok"}
