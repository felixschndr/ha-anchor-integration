import aiohttp
from homeassistant.config_entries import SOURCE_RECONFIGURE, SOURCE_USER, ConfigSubentry
from homeassistant.const import CONF_TOKEN, CONF_URL
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from custom_components.anchor.const import CONF_NOTE_ID, DOMAIN, SUBENTRY_NOTE
from tests.conftest import API, NOTE_ID, SHOPPING, SUBENTRY_ID, TOKEN, URL, content, note

HEALTH = {"status": "ok", "app": "anchor", "version": "0.18.1"}


async def test_server_flow(hass: HomeAssistant, aioclient_mock: AiohttpClientMocker):
    aioclient_mock.get(f"{API}/health", json=HEALTH)
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})
    assert result["type"] is FlowResultType.FORM
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {CONF_URL: f" {URL}/ "})
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "anchor.test"
    assert result["data"] == {CONF_URL: URL}


async def test_server_flow_cannot_connect_then_recovers(hass: HomeAssistant, aioclient_mock: AiohttpClientMocker):
    aioclient_mock.get(f"{API}/health", exc=aiohttp.ClientConnectionError())
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {CONF_URL: URL})
    assert result["errors"] == {"base": "cannot_connect"}

    aioclient_mock.clear_requests()
    aioclient_mock.get(f"{API}/health", json=HEALTH)
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {CONF_URL: URL})
    assert result["type"] is FlowResultType.CREATE_ENTRY


async def test_server_already_configured(hass: HomeAssistant, config_entry: MockConfigEntry):
    config_entry.add_to_hass(hass)
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {CONF_URL: URL})
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"


async def _start_note_flow(hass: HomeAssistant) -> dict:
    if not (entries := hass.config_entries.async_entries(DOMAIN)):
        entries = [MockConfigEntry(domain=DOMAIN, data={CONF_URL: URL})]
        entries[0].add_to_hass(hass)
    return await hass.config_entries.subentries.async_init(
        (entries[0].entry_id, SUBENTRY_NOTE), context={"source": SOURCE_USER}
    )


async def test_note_flow(hass: HomeAssistant, aioclient_mock: AiohttpClientMocker):
    plain = {**note(content(("just text", None))), "id": "plain", "title": ""}
    shared_read_only = {**note(SHOPPING), "id": "readonly", "permission": "viewer"}
    aioclient_mock.get(f"{API}/notes", json=[note(SHOPPING), plain, shared_read_only])

    result = await _start_note_flow(hass)
    assert result["step_id"] == "user"
    result = await hass.config_entries.subentries.async_configure(result["flow_id"], {CONF_TOKEN: TOKEN})
    assert result["step_id"] == "note"
    options = result["data_schema"].schema[CONF_NOTE_ID].config["options"]
    assert [(option["value"], option["label"]) for option in options] == [(NOTE_ID, "Groceries"), ("plain", "plain")]

    result = await hass.config_entries.subentries.async_configure(result["flow_id"], {CONF_NOTE_ID: "plain"})
    assert result["errors"] == {"base": "no_checkboxes"}

    result = await hass.config_entries.subentries.async_configure(result["flow_id"], {CONF_NOTE_ID: NOTE_ID})
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "Groceries"
    assert result["data"] == {CONF_TOKEN: TOKEN, CONF_NOTE_ID: NOTE_ID}
    assert result["unique_id"] == NOTE_ID


async def test_note_flow_hides_added_notes(
    hass: HomeAssistant, config_entry: MockConfigEntry, aioclient_mock: AiohttpClientMocker
):
    config_entry.add_to_hass(hass)
    aioclient_mock.get(f"{API}/notes", json=[note(SHOPPING)])
    result = await _start_note_flow(hass)
    result = await hass.config_entries.subentries.async_configure(result["flow_id"], {CONF_TOKEN: TOKEN})
    assert result["errors"] == {"base": "no_notes"}

    aioclient_mock.clear_requests()
    aioclient_mock.get(f"{API}/notes", json=[note(SHOPPING), {**note(SHOPPING), "id": "other", "title": "Other"}])
    result = await hass.config_entries.subentries.async_configure(result["flow_id"], {CONF_TOKEN: TOKEN})
    assert [option["value"] for option in result["data_schema"].schema[CONF_NOTE_ID].config["options"]] == ["other"]


async def test_note_flow_rejects_content_that_is_not_delta(hass: HomeAssistant, aioclient_mock: AiohttpClientMocker):
    aioclient_mock.get(f"{API}/notes", json=[note("plain text from an old client")])
    result = await _start_note_flow(hass)
    result = await hass.config_entries.subentries.async_configure(result["flow_id"], {CONF_TOKEN: TOKEN})
    result = await hass.config_entries.subentries.async_configure(result["flow_id"], {CONF_NOTE_ID: NOTE_ID})
    assert result["errors"] == {"base": "no_checkboxes"}


async def test_note_flow_errors(hass: HomeAssistant, aioclient_mock: AiohttpClientMocker):
    result = await _start_note_flow(hass)
    for response, error in (
        ({"status": 401}, "invalid_auth"),
        ({"exc": aiohttp.ClientConnectionError()}, "cannot_connect"),
        ({"json": [{**note(SHOPPING), "permission": "viewer"}]}, "no_notes"),
    ):
        aioclient_mock.clear_requests()
        aioclient_mock.get(f"{API}/notes", **response)
        result = await hass.config_entries.subentries.async_configure(result["flow_id"], {CONF_TOKEN: TOKEN})
        assert result["errors"] == {"base": error}


async def _start_reconfigure(hass: HomeAssistant, config_entry: MockConfigEntry) -> dict:
    config_entry.add_to_hass(hass)
    return await hass.config_entries.subentries.async_init(
        (config_entry.entry_id, SUBENTRY_NOTE), context={"source": SOURCE_RECONFIGURE, "subentry_id": SUBENTRY_ID}
    )


async def test_reconfigure_replaces_token(
    hass: HomeAssistant, config_entry: MockConfigEntry, aioclient_mock: AiohttpClientMocker
):
    aioclient_mock.get(f"{API}/notes", json=[note(SHOPPING)])
    result = await _start_reconfigure(hass, config_entry)
    assert result["step_id"] == "reconfigure"
    assert result["description_placeholders"] == {"note": "Groceries"}

    result = await hass.config_entries.subentries.async_configure(result["flow_id"], {CONF_TOKEN: "new-token"})
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reconfigure_successful"
    assert config_entry.subentries[SUBENTRY_ID].data == {CONF_TOKEN: "new-token", CONF_NOTE_ID: NOTE_ID}
    assert aioclient_mock.mock_calls[-1][3] == {"Authorization": "Bearer new-token"}


async def test_reconfigure_errors(
    hass: HomeAssistant, config_entry: MockConfigEntry, aioclient_mock: AiohttpClientMocker
):
    result = await _start_reconfigure(hass, config_entry)
    for response, error in (
        ({"status": 401}, "invalid_auth"),
        ({"exc": aiohttp.ClientConnectionError()}, "cannot_connect"),
        ({"json": [{**note(SHOPPING), "id": "someone-elses"}]}, "note_not_accessible"),
        ({"json": [{**note(SHOPPING), "permission": "viewer"}]}, "note_not_accessible"),
    ):
        aioclient_mock.clear_requests()
        aioclient_mock.get(f"{API}/notes", **response)
        result = await hass.config_entries.subentries.async_configure(result["flow_id"], {CONF_TOKEN: "new-token"})
        assert result["errors"] == {"base": error}
    assert config_entry.subentries[SUBENTRY_ID].data[CONF_TOKEN] == TOKEN


async def test_reconfigure_updates_all_notes_of_the_same_user(
    hass: HomeAssistant, config_entry: MockConfigEntry, aioclient_mock: AiohttpClientMocker
):
    config_entry.add_to_hass(hass)
    for note_id, token in (("sibling", TOKEN), ("bobs", "token-of-bob"), ("gone", TOKEN)):
        hass.config_entries.async_add_subentry(
            config_entry,
            ConfigSubentry(
                data={CONF_TOKEN: token, CONF_NOTE_ID: note_id},
                subentry_type=SUBENTRY_NOTE,
                title=note_id,
                unique_id=note_id,
            ),
        )
    # "gone" was deleted in Anchor meanwhile, so the new token can't be checked against it
    aioclient_mock.get(f"{API}/notes", json=[note(SHOPPING), {**note(SHOPPING), "id": "sibling"}])
    result = await _start_reconfigure(hass, config_entry)
    result = await hass.config_entries.subentries.async_configure(result["flow_id"], {CONF_TOKEN: "new-token"})
    assert result["reason"] == "reconfigure_successful"
    tokens = {subentry.data[CONF_NOTE_ID]: subentry.data[CONF_TOKEN] for subentry in config_entry.subentries.values()}
    assert tokens == {NOTE_ID: "new-token", "sibling": "new-token", "bobs": "token-of-bob", "gone": TOKEN}
