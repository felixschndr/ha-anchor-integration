"""Minimal client for the Anchor REST API (https://github.com/zhfahim/anchor)."""

from collections.abc import AsyncIterator
from typing import Any

import aiohttp

REQUEST_TIMEOUT = aiohttp.ClientTimeout(total=15)
# The server pings every 25 s, so a much longer silence means the stream is dead.
EVENTS_TIMEOUT = aiohttp.ClientTimeout(total=None, sock_connect=15, sock_read=90)


class AnchorError(Exception):
    pass


class AnchorAuthError(AnchorError):
    pass


class AnchorConflictError(AnchorError):
    """The note changed since the version the edit was based on."""

    def __init__(self, server_note: dict[str, Any]) -> None:
        super().__init__("Note was changed by someone else")
        self.server_note = server_note


class AnchorClient:
    def __init__(self, session: aiohttp.ClientSession, url: str) -> None:
        self._session = session
        self._api = f"{url.rstrip('/')}/api"

    @staticmethod
    def _headers(token: str) -> dict[str, str]:
        return {"Authorization": f"Bearer {token}"}

    async def _request(self, method: str, path: str, token: str | None = None, json: Any = None) -> Any:
        try:
            async with self._session.request(
                method=method,
                url=f"{self._api}{path}",
                headers=self._headers(token) if token else None,
                json=json,
                timeout=REQUEST_TIMEOUT,
            ) as response:
                if response.status == 401:
                    raise AnchorAuthError("The API token was rejected")
                if response.status == 409:
                    raise AnchorConflictError((await response.json())["serverNote"])
                response.raise_for_status()
                return await response.json()
        except (aiohttp.ClientError, TimeoutError, ValueError, KeyError) as err:
            raise AnchorError(f"Request to {path} failed: {err!r}") from err

    async def health(self) -> dict[str, Any]:
        data = await self._request(method="GET", path="/health")
        if not isinstance(data, dict) or data.get("app") != "anchor":
            raise AnchorError("This is not an Anchor server")
        return data

    async def list_notes(self, token: str) -> list[dict[str, Any]]:
        return await self._request(method="GET", path="/notes", token=token)

    async def get_note(self, token: str, note_id: str) -> dict[str, Any]:
        return await self._request(method="GET", path=f"/notes/{note_id}", token=token)

    async def update_content(self, token: str, note_id: str, content: str, base_version: int) -> dict[str, Any]:
        """Raises AnchorConflictError when the note is no longer at ``base_version``."""
        return await self._request(
            method="PATCH",
            path=f"/notes/{note_id}",
            token=token,
            json={"content": content, "baseVersion": base_version},
        )

    async def events(self, token: str) -> AsyncIterator[str]:
        """Yield the type of every server-sent event until the server closes the stream (at the latest after 1 h)."""
        try:
            async with self._session.get(
                f"{self._api}/sync/events",
                headers={**self._headers(token), "Accept": "text/event-stream"},
                timeout=EVENTS_TIMEOUT,
            ) as response:
                if response.status == 401:
                    raise AnchorAuthError("The API token was rejected")
                response.raise_for_status()
                # Lets the caller catch up on changes made while it wasn't connected.
                yield "open"
                event = "message"
                async for raw in response.content:
                    line = raw.decode(errors="replace").rstrip("\r\n")
                    if line.startswith("event:"):
                        event = line.removeprefix("event:").strip()
                    elif not line:
                        yield event
                        event = "message"
        except (aiohttp.ClientError, TimeoutError) as err:
            raise AnchorError(f"Event stream failed: {err}") from err
