import json
from collections.abc import AsyncIterator, Generator
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import aiohttp
import pytest
from homeassistant.components.todo import DOMAIN as TODO_DOMAIN
from homeassistant.config_entries import ConfigEntryState, ConfigSubentry
from homeassistant.const import CONF_TOKEN, STATE_UNAVAILABLE
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import issue_registry as ir
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker, AiohttpClientMockResponse
from pytest_homeassistant_custom_component.typing import WebSocketGenerator
from yarl import URL as YURL

from custom_components.anchor import _listen
from custom_components.anchor.api import AnchorAuthError, AnchorError
from custom_components.anchor.const import CONF_NOTE_ID, DOMAIN, SUBENTRY_NOTE
from tests.conftest import API, NOTE_ID, SHOPPING, SUBENTRY_ID, TOKEN, note

NOTE_URL = f"{API}/notes/{NOTE_ID}"


@pytest.fixture(autouse=True)
def no_event_stream() -> Generator[AsyncMock]:
    with patch("custom_components.anchor._listen", new_callable=AsyncMock) as listen:
        yield listen


@pytest.fixture
def server(aioclient_mock: AiohttpClientMocker) -> AiohttpClientMocker:
    aioclient_mock.get(f"{API}/health", json={"status": "ok", "app": "anchor", "version": "0.18.1"})
    aioclient_mock.get(NOTE_URL, json=note(SHOPPING, version=4))
    return aioclient_mock


def accept_patches(server: AiohttpClientMocker, conflicts: list[dict[str, Any]] | None = None) -> None:
    """Answer PATCHes like Anchor: 409 with the queued server notes first, then save and bump the version."""
    queued = list(conflicts or [])

    async def respond(method: str, url: YURL, data: dict[str, Any]) -> AiohttpClientMockResponse:
        if queued:
            return AiohttpClientMockResponse(method, url, status=409, json={"serverNote": queued.pop(0)})
        return AiohttpClientMockResponse(method, url, json=note(data["content"], version=data["baseVersion"] + 1))

    server.patch(NOTE_URL, side_effect=respond)


def sent_items(server: AiohttpClientMocker) -> list[tuple[str, str | None]]:
    """(text, checkbox) of every line in the last saved content."""
    ops = json.loads(server.mock_calls[-1][2]["content"])["ops"]
    lines, text = [], ""
    for op in ops:
        for i, part in enumerate(op["insert"].split("\n")):
            if i:
                lines.append((text, op.get("attributes", {}).get("list")))
                text = ""
            text += part
    return lines


async def setup(hass: HomeAssistant, config_entry: MockConfigEntry) -> str:
    config_entry.add_to_hass(hass)
    await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()
    entity_id = er.async_get(hass).async_get_entity_id(TODO_DOMAIN, DOMAIN, NOTE_ID)
    assert entity_id
    return entity_id


async def test_setup_creates_device_and_list(
    hass: HomeAssistant, config_entry: MockConfigEntry, server: AiohttpClientMocker, no_event_stream: AsyncMock
):
    entity_id = await setup(hass, config_entry)
    assert config_entry.state is ConfigEntryState.LOADED
    assert hass.states.get(entity_id).state == "1"  # one open item
    devices = dr.async_get(hass)
    server_device = devices.async_get_device_by_identifier((DOMAIN, config_entry.entry_id), config_entry.entry_id)
    assert server_device.sw_version == "0.18.1"
    assert server_device.name == "Anchor server"
    note_device = devices.async_get(er.async_get(hass).async_get(entity_id).device_id)
    assert note_device.name == "Groceries"
    assert note_device.via_device_id == server_device.id
    no_event_stream.assert_called_once()
    assert no_event_stream.call_args.kwargs["token"] == TOKEN

    assert await hass.config_entries.async_unload(config_entry.entry_id)
    assert config_entry.state is ConfigEntryState.NOT_LOADED


async def test_server_down(hass: HomeAssistant, config_entry: MockConfigEntry, aioclient_mock: AiohttpClientMocker):
    aioclient_mock.get(f"{API}/health", exc=aiohttp.ClientConnectionError())
    config_entry.add_to_hass(hass)
    await hass.config_entries.async_setup(config_entry.entry_id)
    assert config_entry.state is ConfigEntryState.SETUP_RETRY


async def test_missing_note_only_breaks_its_list(
    hass: HomeAssistant, config_entry: MockConfigEntry, aioclient_mock: AiohttpClientMocker
):
    aioclient_mock.get(f"{API}/health", json={"status": "ok", "app": "anchor"})
    aioclient_mock.get(NOTE_URL, status=404)
    entity_id = await setup(hass, config_entry)
    assert config_entry.state is ConfigEntryState.LOADED
    assert hass.states.get(entity_id).state == STATE_UNAVAILABLE


async def test_adding_a_note_reloads(hass: HomeAssistant, config_entry: MockConfigEntry, server: AiohttpClientMocker):
    await setup(hass, config_entry)
    server.get(f"{API}/notes/other", json={**note(SHOPPING), "id": "other"})
    hass.config_entries.async_add_subentry(
        config_entry,
        ConfigSubentry(
            data={CONF_TOKEN: TOKEN, CONF_NOTE_ID: "other"},
            subentry_type=SUBENTRY_NOTE,
            title="Other",
            unique_id="other",
        ),
    )
    await hass.async_block_till_done()
    assert er.async_get(hass).async_get_entity_id(TODO_DOMAIN, DOMAIN, "other")


async def test_add_check_rename_and_remove(
    hass: HomeAssistant, config_entry: MockConfigEntry, server: AiohttpClientMocker
):
    entity_id = await setup(hass, config_entry)
    accept_patches(server)

    async def call(service: str, **data: Any) -> None:
        await hass.services.async_call(TODO_DOMAIN, service, {"entity_id": entity_id, **data}, blocking=True)

    await call("add_item", item="Eggs")
    assert sent_items(server) == [
        ("Groceries", None),
        ("Milk", "unchecked"),
        ("Bread", "checked"),
        ("Eggs", "unchecked"),
        ("Some text", None),
    ]
    assert server.mock_calls[-1][2]["baseVersion"] == 4

    await call("update_item", item="Milk", status="completed", rename="Oat milk")
    assert sent_items(server)[1] == ("Oat milk", "checked")
    assert server.mock_calls[-1][2]["baseVersion"] == 5

    await call("remove_item", item=["Bread", "Eggs"])
    assert sent_items(server) == [("Groceries", None), ("Oat milk", "checked"), ("Some text", None)]
    assert hass.states.get(entity_id).state == "0"


async def test_move(
    hass: HomeAssistant, config_entry: MockConfigEntry, server: AiohttpClientMocker, hass_ws_client: WebSocketGenerator
):
    entity_id = await setup(hass, config_entry)
    accept_patches(server)
    client = await hass_ws_client(hass)
    await client.send_json_auto_id({"type": "todo/item/list", "entity_id": entity_id})
    milk, bread = (item["uid"] for item in (await client.receive_json())["result"]["items"])

    await client.send_json_auto_id(
        {"type": "todo/item/move", "entity_id": entity_id, "uid": milk, "previous_uid": bread}
    )
    assert (await client.receive_json())["success"]
    assert [text for text, _ in sent_items(server)] == ["Groceries", "Bread", "Milk", "Some text"]


async def test_edit_reapplies_on_top_of_concurrent_change(
    hass: HomeAssistant, config_entry: MockConfigEntry, server: AiohttpClientMocker
):
    entity_id = await setup(hass, config_entry)
    changed_meanwhile = note(SHOPPING.replace('"Bread"', '"Butter"'), version=9)
    accept_patches(server, conflicts=[changed_meanwhile])

    await hass.services.async_call(
        TODO_DOMAIN, "update_item", {"entity_id": entity_id, "item": "Milk", "status": "completed"}, blocking=True
    )
    assert sent_items(server)[1:3] == [("Milk", "checked"), ("Butter", "checked")]
    assert server.mock_calls[-1][2]["baseVersion"] == 9


async def test_edit_gives_up_when_note_keeps_changing(
    hass: HomeAssistant, config_entry: MockConfigEntry, server: AiohttpClientMocker
):
    entity_id = await setup(hass, config_entry)
    accept_patches(server, conflicts=[note(SHOPPING, version=v) for v in (5, 6, 7)])
    with pytest.raises(HomeAssistantError, match="kept changing"):
        await hass.services.async_call(TODO_DOMAIN, "add_item", {"entity_id": entity_id, "item": "Eggs"}, blocking=True)


async def test_edit_of_vanished_item(hass: HomeAssistant, config_entry: MockConfigEntry, server: AiohttpClientMocker):
    entity_id = await setup(hass, config_entry)
    accept_patches(server, conflicts=[note(SHOPPING.replace('"Milk"', '"Cheese"'), version=5)])
    with pytest.raises(HomeAssistantError, match="no longer exists"):
        await hass.services.async_call(
            TODO_DOMAIN, "update_item", {"entity_id": entity_id, "item": "Milk", "status": "completed"}, blocking=True
        )


async def test_edit_save_fails(hass: HomeAssistant, config_entry: MockConfigEntry, server: AiohttpClientMocker):
    entity_id = await setup(hass, config_entry)
    server.patch(NOTE_URL, status=500)
    with pytest.raises(HomeAssistantError, match="Could not save"):
        await hass.services.async_call(TODO_DOMAIN, "add_item", {"entity_id": entity_id, "item": "Eggs"}, blocking=True)


def fake_client(*streams: list[str] | Exception) -> MagicMock:
    remaining = list(streams)

    async def events(token: str) -> AsyncIterator[str]:
        stream = remaining.pop(0)
        if isinstance(stream, Exception):
            raise stream
        for event in stream:
            yield event

    return MagicMock(events=events)


async def test_listen_refreshes_on_sync_and_after_reconnect():
    coordinator = MagicMock(async_request_refresh=AsyncMock())
    client = fake_client(["open", "ping", "sync", "reconnect"], AnchorError("dropped"), ["open"], AnchorAuthError())
    with patch("custom_components.anchor.asyncio.sleep", new_callable=AsyncMock) as sleep:
        await _listen(client=client, token=TOKEN, coordinators=[coordinator])
    # backs off while failing, starts over once a stream got through
    assert [c.args[0] for c in sleep.call_args_list] == [1, 2, 1]
    # on every (re)connect to catch up, and on "sync"
    assert coordinator.async_request_refresh.await_count == 3


async def test_rejected_token_raises_repair_issue(
    hass: HomeAssistant, config_entry: MockConfigEntry, aioclient_mock: AiohttpClientMocker
):
    aioclient_mock.get(f"{API}/health", json={"status": "ok", "app": "anchor"})
    aioclient_mock.get(NOTE_URL, status=401)
    entity_id = await setup(hass, config_entry)
    assert hass.states.get(entity_id).state == STATE_UNAVAILABLE
    issue_id = f"invalid_token_{SUBENTRY_ID}"
    issue = ir.async_get(hass).async_get_issue(DOMAIN, issue_id)
    assert issue.translation_placeholders == {"note": "Groceries"}

    aioclient_mock.clear_requests()
    aioclient_mock.get(NOTE_URL, json=note(SHOPPING))
    await config_entry.runtime_data[SUBENTRY_ID].async_refresh()
    assert ir.async_get(hass).async_get_issue(DOMAIN, issue_id) is None
    assert hass.states.get(entity_id).state == "1"


async def test_unload_drops_repair_issue(
    hass: HomeAssistant, config_entry: MockConfigEntry, aioclient_mock: AiohttpClientMocker
):
    aioclient_mock.get(f"{API}/health", json={"status": "ok", "app": "anchor"})
    aioclient_mock.get(NOTE_URL, status=401)
    await setup(hass, config_entry)
    assert await hass.config_entries.async_unload(config_entry.entry_id)
    assert ir.async_get(hass).async_get_issue(DOMAIN, f"invalid_token_{SUBENTRY_ID}") is None


async def test_content_that_is_not_delta_makes_list_unavailable(
    hass: HomeAssistant, config_entry: MockConfigEntry, aioclient_mock: AiohttpClientMocker
):
    aioclient_mock.get(f"{API}/health", json={"status": "ok", "app": "anchor"})
    aioclient_mock.get(NOTE_URL, json=note("plain text"))
    entity_id = await setup(hass, config_entry)
    assert hass.states.get(entity_id).state == STATE_UNAVAILABLE


async def test_move_on_unloaded_note(
    hass: HomeAssistant,
    config_entry: MockConfigEntry,
    aioclient_mock: AiohttpClientMocker,
    hass_ws_client: WebSocketGenerator,
):
    aioclient_mock.get(f"{API}/health", json={"status": "ok", "app": "anchor"})
    aioclient_mock.get(NOTE_URL, status=500)
    entity_id = await setup(hass, config_entry)
    client = await hass_ws_client(hass)
    await client.send_json_auto_id({"type": "todo/item/move", "entity_id": entity_id, "uid": "x"})
    response = await client.receive_json()
    assert not response["success"]
    assert "could not be loaded" in response["error"]["message"]


async def test_edit_when_note_turned_into_something_else(
    hass: HomeAssistant, config_entry: MockConfigEntry, server: AiohttpClientMocker
):
    entity_id = await setup(hass, config_entry)
    accept_patches(server, conflicts=[note("plain text", version=5)])
    with pytest.raises(HomeAssistantError, match="not Quill Delta"):
        await hass.services.async_call(TODO_DOMAIN, "add_item", {"entity_id": entity_id, "item": "Eggs"}, blocking=True)
