"""Set up the server first, then add notes (each with its user's API token) as subentries."""

from typing import Any
from urllib.parse import urlparse

import probatio
from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    ConfigSubentryFlow,
    SubentryFlowResult,
)
from homeassistant.const import CONF_TOKEN, CONF_URL
from homeassistant.core import callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.selector import (
    SelectOptionDict,
    SelectSelector,
    SelectSelectorConfig,
    TextSelector,
    TextSelectorConfig,
    TextSelectorType,
)

from . import delta
from .api import AnchorAuthError, AnchorClient, AnchorError
from .const import CONF_NOTE_ID, DOMAIN, SUBENTRY_NOTE


class AnchorConfigFlow(ConfigFlow, domain=DOMAIN):
    VERSION = 1

    @classmethod
    @callback
    def async_get_supported_subentry_types(cls, config_entry: ConfigEntry) -> dict[str, type[ConfigSubentryFlow]]:
        return {SUBENTRY_NOTE: NoteSubentryFlow}

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            url = user_input[CONF_URL].strip().rstrip("/")
            self._async_abort_entries_match({CONF_URL: url})
            try:
                await AnchorClient(session=async_get_clientsession(self.hass), url=url).health()
            except AnchorError:
                errors["base"] = "cannot_connect"
            else:
                return self.async_create_entry(title=urlparse(url).hostname or url, data={CONF_URL: url})
        return self.async_show_form(
            step_id="user",
            data_schema=self.add_suggested_values_to_schema(
                data_schema=probatio.Schema(
                    {probatio.Required(CONF_URL): TextSelector(TextSelectorConfig(type=TextSelectorType.URL))}
                ),
                suggested_values=user_input,
            ),
            errors=errors,
            description_placeholders={"example_url": "http://anchor.local:3000"},
        )


TOKEN_SCHEMA = probatio.Schema(
    {probatio.Required(CONF_TOKEN): TextSelector(TextSelectorConfig(type=TextSelectorType.PASSWORD))}
)


class NoteSubentryFlow(ConfigSubentryFlow):
    """Token → list the user's notes → pick one that has checkboxes. Reconfiguring replaces the token."""

    def __init__(self) -> None:
        self._token: str | None = None
        self._notes: dict[str, dict[str, Any]] = {}

    async def _load_notes(self, token: str) -> str | None:
        """Load the notes the token's user may edit; returns an error key on failure."""
        client = AnchorClient(session=async_get_clientsession(self.hass), url=self._get_entry().data[CONF_URL])
        try:
            notes = await client.list_notes(token=token)
        except AnchorAuthError:
            return "invalid_auth"
        except AnchorError:
            return "cannot_connect"
        # Viewers can't edit the content, so their notes can't be to-do lists.
        self._notes = {note["id"]: note for note in notes if note.get("permission") != "viewer"}
        self._token = token
        return None

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> SubentryFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            if error := await self._load_notes(user_input[CONF_TOKEN]):
                errors["base"] = error
            else:
                added = {subentry.unique_id for subentry in self._get_entry().subentries.values()}
                self._notes = {note_id: note for note_id, note in self._notes.items() if note_id not in added}
                if self._notes:
                    return await self.async_step_note()
                errors["base"] = "no_notes"
        return self.async_show_form(step_id="user", data_schema=TOKEN_SCHEMA, errors=errors)

    async def async_step_reconfigure(self, user_input: dict[str, Any] | None = None) -> SubentryFlowResult:
        subentry = self._get_reconfigure_subentry()
        errors: dict[str, str] = {}
        if user_input is not None:
            if error := await self._load_notes(user_input[CONF_TOKEN]):
                errors["base"] = error
            elif subentry.data[CONF_NOTE_ID] not in self._notes:
                errors["base"] = "note_not_accessible"
            else:
                # A token regenerated in Anchor breaks every note of that user, so fix them all at once.
                entry = self._get_entry()
                for other in entry.subentries.values():
                    same_user = other.data[CONF_TOKEN] == subentry.data[CONF_TOKEN]
                    if other is not subentry and same_user and other.data[CONF_NOTE_ID] in self._notes:
                        self.hass.config_entries.async_update_subentry(
                            entry=entry, subentry=other, data={**other.data, CONF_TOKEN: user_input[CONF_TOKEN]}
                        )
                # The entry's update listener reloads it with the new token.
                return self.async_update_and_abort(
                    entry=entry, subentry=subentry, data_updates={CONF_TOKEN: user_input[CONF_TOKEN]}
                )
        return self.async_show_form(
            step_id="reconfigure",
            data_schema=TOKEN_SCHEMA,
            errors=errors,
            description_placeholders={"note": subentry.title},
        )

    async def async_step_note(self, user_input: dict[str, Any] | None = None) -> SubentryFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            note = self._notes[user_input[CONF_NOTE_ID]]
            if _has_checkboxes(note):
                return self.async_create_entry(
                    title=_title(note),
                    data={CONF_TOKEN: self._token, CONF_NOTE_ID: note["id"]},
                    unique_id=note["id"],
                )
            errors["base"] = "no_checkboxes"
        options = [SelectOptionDict(value=note_id, label=_title(note)) for note_id, note in self._notes.items()]
        return self.async_show_form(
            step_id="note",
            data_schema=probatio.Schema(
                {probatio.Required(CONF_NOTE_ID): SelectSelector(SelectSelectorConfig(options=options))}
            ),
            errors=errors,
        )


def _has_checkboxes(note: dict[str, Any]) -> bool:
    try:
        return bool(delta.items(delta.parse(note.get("content"))))
    except ValueError:
        return False


def _title(note: dict[str, Any]) -> str:
    return note.get("title") or note["id"]
