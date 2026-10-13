"""Client HTTP pour interagir avec l'API Todo."""

from typing import Any

import httpx
from mcp.server.mcpserver.exceptions import ToolError

from mcp_server.config import Settings


def _error_detail(response: httpx.Response) -> str:
    """Readable message from an API error response."""
    try:
        detail = response.json()["detail"]
    except (ValueError, KeyError):
        return response.text

    if isinstance(detail, list):
        return "; ".join(f"{'.'.join(map(str, e['loc']))}: {e['msg']}" for e in detail)
    return str(detail)


class TodoApiClient:
    """HTTP client for the Todo API."""

    def __init__(
        self, settings: Settings, transport: httpx.AsyncBaseTransport | None = None
    ) -> None:
        self._client = httpx.AsyncClient(
            base_url=settings.TODO_API_URL,
            headers={"Authorization": f"Bearer {settings.TODO_API_TOKEN.get_secret_value()}"},
            transport=transport,
            timeout=10,
        )

    async def request(
        self,
        method: str,
        path: str,
        *,
        params: dict | None = None,
        json: dict | None = None,
        idempotency_key: str | None = None,
    ) -> Any:
        """Send a request to the Todo API and return the response."""
        headers = {}
        if idempotency_key is not None:
            headers["Idempotency-Key"] = idempotency_key

        try:
            response = await self._client.request(
                method, path, params=params, json=json, headers=headers
            )
        except httpx.HTTPError as exc:
            raise ToolError(f"API unreachable ({type(exc).__name__})") from exc

        if response.is_error:
            raise ToolError(f"API error {response.status_code}: {_error_detail(response)}")

        return None if response.status_code == 204 else response.json()

    async def aclose(self) -> None:
        """Close the HTTP client."""
        await self._client.aclose()
