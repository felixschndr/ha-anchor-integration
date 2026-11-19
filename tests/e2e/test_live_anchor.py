"""The integration in a real Home Assistant core against a real Anchor server.

Start Anchor (see the e2e job in .github/workflows/tests.yml) and set ANCHOR_E2E_URL, e.g.
    docker run --rm -p 127.0.0.1:3999:3000 -e PG_USER=anchor -e PG_PASSWORD=password -e PG_DATABASE=anchor \
        ghcr.io/zhfahim/anchor:latest
    ANCHOR_E2E_URL=http://127.0.0.1:3999 poetry run pytest tests/e2e
"""

import asyncio
import os
from typing import Any
from uuid import uuid4

import pytest
from homeassistant.components.todo import DOMAIN as TODO_DOMAIN
from homeassistant.config_entries import SOURCE_RECONFIGURE, SOURCE_USER, ConfigEntryState
from homeassistant.const import CONF_TOKEN, CONF_URL
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from custom_components.anchor import delta
from custom_components.anchor.const import CONF_NOTE_ID, DOMAIN, SUBENTRY_NOTE
from tests.conftest import SHOPPING, content

URL = os.environ.get("ANCHOR_E2E_URL", "")
pytestmark = pytest.mark.skipif(not URL, reason="set ANCHOR_E2E_URL to test against a real Anchor server")


class Anchor:
    """Talks to the server directly, the way the Anchor web app would."""

    def __init__(self, hass: HomeAssistant) -> None:
        self._session = async_get_clientsession(hass)
        self.token = ""  # nosec B105 - set by sign_up
        self._jwt = ""

    async def call(self, method: str, path: str, token: str | None = None, **kwargs: Any) -> Any:
        headers = {"Authorization": f"Bearer {token or self.token}"}
        async with self._session.request(method=method, url=f"{URL}/api{path}", headers=headers, **kwargs) as response:
            response.raise_for_status()
            return await response.json()

    async def sign_up(self) -> None:
        account = {"email": f"{uuid4().hex}@example.com", "password": "e2e-password", "name": "E2E"}  # nosec B105
        async with self._session.post(url=f"{URL}/api/auth/register", json=account) as response:
            response.raise_for_status()
            self._jwt = (await response.json())["access_token"]
        await self.regenerate_token()

    async def regenerate_token(self) -> None:
        """Like clicking "regenerate" in Anchor's settings: the previous token stops working."""
        self.token = (await self.call(method="POST", path="/auth/api-token/regenerate", token=self._jwt))["apiToken"]

    async def create_note(self, title: str, body: str) -> str:
        return (await self.call(method="POST", path="/notes", json={"title": title, "content": body}))["id"]

    async def lines(self, note_id: str) -> list[tuple[str, str | None]]:
        note = await self.call(method="GET", path=f"/notes/{note_id}")
        return [(line.text, line.checkbox) for line in delta.parse(note["content"])]


async def wait_for(condition: Any, timeout: float = 20) -> None:
    async with asyncio.timeout(timeout):
        while not condition():
            await asyncio.sleep(0.1)


# socket_enabled lifts the network block of Home Assistant's test harness.
async def test_live(hass: HomeAssistant, socket_enabled: None):
    anchor = Anchor(hass)
    await anchor.sign_up()
    note_id = await anchor.create_note(title="Groceries", body=SHOPPING)
    plain_id = await anchor.create_note(title="Plain", body=content(("no checkboxes", None)))

    # Server
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {CONF_URL: URL})
    entry = result["result"]
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.LOADED

    # Note: token → pick a note without checkboxes (rejected) → pick the shopping list
    result = await hass.config_entries.subentries.async_init(
        (entry.entry_id, SUBENTRY_NOTE), context={"source": SOURCE_USER}
    )
    result = await hass.config_entries.subentries.async_configure(result["flow_id"], {CONF_TOKEN: anchor.token})
    result = await hass.config_entries.subentries.async_configure(result["flow_id"], {CONF_NOTE_ID: plain_id})
    assert result["errors"] == {"base": "no_checkboxes"}
    result = await hass.config_entries.subentries.async_configure(result["flow_id"], {CONF_NOTE_ID: note_id})
    assert result["title"] == "Groceries"
    await hass.async_block_till_done()

    entity_id = er.async_get(hass).async_get_entity_id(TODO_DOMAIN, DOMAIN, note_id)
    assert entity_id
    assert hass.states.get(entity_id).state == "1"

    async def call(service: str, **data: Any) -> None:
        await hass.services.async_call(TODO_DOMAIN, service, {"entity_id": entity_id, **data}, blocking=True)

    # Home Assistant → Anchor
    await call("add_item", item="Eggs")
    await call("update_item", item="Milk", status="completed")
    await call("remove_item", item=["Bread"])
    assert await anchor.lines(note_id) == [
        ("Groceries", None),
        ("Milk", "checked"),
        ("Eggs", "unchecked"),
        ("Some text", None),
    ]

    # Anchor → Home Assistant, pushed through the event stream
    note = await anchor.call(method="GET", path=f"/notes/{note_id}")
    edited = delta.add(lines=delta.parse(note["content"]), summary="Butter")
    await anchor.call(
        method="PATCH", path=f"/notes/{note_id}", json={"content": delta.dump(edited), "baseVersion": note["version"]}
    )
    await wait_for(lambda: hass.states.get(entity_id).state == "2")

    # The token gets regenerated in Anchor → reconfigure the note with the new one
    await anchor.regenerate_token()
    subentry_id = next(iter(entry.subentries))
    result = await hass.config_entries.subentries.async_init(
        (entry.entry_id, SUBENTRY_NOTE), context={"source": SOURCE_RECONFIGURE, "subentry_id": subentry_id}
    )
    result = await hass.config_entries.subentries.async_configure(result["flow_id"], {CONF_TOKEN: anchor.token})
    assert result["reason"] == "reconfigure_successful"
    await hass.async_block_till_done()
    await call("add_item", item="Jam")
    assert ("Jam", "unchecked") in await anchor.lines(note_id)

    assert await hass.config_entries.async_unload(entry.entry_id)
