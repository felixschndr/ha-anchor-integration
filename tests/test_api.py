import aiohttp
import pytest
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from custom_components.anchor.api import AnchorAuthError, AnchorClient, AnchorConflictError, AnchorError
from tests.conftest import API, NOTE_ID, TOKEN, URL, note


@pytest.fixture
async def client(hass: HomeAssistant, aioclient_mock: AiohttpClientMocker) -> AnchorClient:
    return AnchorClient(session=async_get_clientsession(hass), url=f"{URL}/")


async def test_health(client: AnchorClient, aioclient_mock: AiohttpClientMocker):
    aioclient_mock.get(f"{API}/health", json={"status": "ok", "app": "anchor", "version": "0.18.1"})
    assert (await client.health())["version"] == "0.18.1"
    assert aioclient_mock.mock_calls[0][3] is None  # no token sent


async def test_health_of_other_app(client: AnchorClient, aioclient_mock: AiohttpClientMocker):
    aioclient_mock.get(f"{API}/health", json={"status": "ok"})
    with pytest.raises(AnchorError, match="not an Anchor server"):
        await client.health()


async def test_connection_error(client: AnchorClient, aioclient_mock: AiohttpClientMocker):
    aioclient_mock.get(f"{API}/health", exc=aiohttp.ClientConnectionError("refused"))
    with pytest.raises(AnchorError, match="refused"):
        await client.health()


async def test_http_error(client: AnchorClient, aioclient_mock: AiohttpClientMocker):
    aioclient_mock.get(f"{API}/notes/{NOTE_ID}", status=404)
    with pytest.raises(AnchorError):
        await client.get_note(token=TOKEN, note_id=NOTE_ID)


async def test_notes_with_token(client: AnchorClient, aioclient_mock: AiohttpClientMocker):
    aioclient_mock.get(f"{API}/notes", json=[note("")])
    assert await client.list_notes(token=TOKEN) == [note("")]
    assert aioclient_mock.mock_calls[0][3] == {"Authorization": f"Bearer {TOKEN}"}


async def test_auth_error(client: AnchorClient, aioclient_mock: AiohttpClientMocker):
    aioclient_mock.get(f"{API}/notes", status=401)
    with pytest.raises(AnchorAuthError):
        await client.list_notes(token=TOKEN)


async def test_update_sends_base_version(client: AnchorClient, aioclient_mock: AiohttpClientMocker):
    aioclient_mock.patch(f"{API}/notes/{NOTE_ID}", json=note("x", version=3))
    assert (await client.update_content(token=TOKEN, note_id=NOTE_ID, content="x", base_version=2))["version"] == 3
    assert aioclient_mock.mock_calls[0][2] == {"content": "x", "baseVersion": 2}


async def test_update_conflict(client: AnchorClient, aioclient_mock: AiohttpClientMocker):
    aioclient_mock.patch(f"{API}/notes/{NOTE_ID}", status=409, json={"message": "x", "serverNote": note("y", 5)})
    with pytest.raises(AnchorConflictError) as err:
        await client.update_content(token=TOKEN, note_id=NOTE_ID, content="x", base_version=2)
    assert err.value.server_note["version"] == 5


async def test_events(client: AnchorClient, aioclient_mock: AiohttpClientMocker):
    stream = b'event: ping\ndata: \n\nevent: sync\r\ndata: {"seq":"7"}\r\n\r\ndata: no type\n\nevent: reconnect\n\n'
    aioclient_mock.get(f"{API}/sync/events", content=stream)
    assert [event async for event in client.events(TOKEN)] == ["open", "ping", "sync", "message", "reconnect"]


async def test_events_auth_error(client: AnchorClient, aioclient_mock: AiohttpClientMocker):
    aioclient_mock.get(f"{API}/sync/events", status=401)
    with pytest.raises(AnchorAuthError):
        [event async for event in client.events(TOKEN)]


async def test_events_connection_error(client: AnchorClient, aioclient_mock: AiohttpClientMocker):
    aioclient_mock.get(f"{API}/sync/events", exc=TimeoutError())
    with pytest.raises(AnchorError):
        [event async for event in client.events(TOKEN)]


async def test_unexpected_responses(client: AnchorClient, aioclient_mock: AiohttpClientMocker):
    aioclient_mock.get(f"{API}/notes", text="<html>proxy error</html>")
    with pytest.raises(AnchorError):
        await client.list_notes(token=TOKEN)
    aioclient_mock.patch(f"{API}/notes/{NOTE_ID}", status=409, json={"message": "no server note"})
    with pytest.raises(AnchorError):
        await client.update_content(token=TOKEN, note_id=NOTE_ID, content="x", base_version=1)
