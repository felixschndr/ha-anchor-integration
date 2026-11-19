import logging
from collections.abc import Callable
from typing import Any

from homeassistant.config_entries import ConfigEntry, ConfigSubentry
from homeassistant.const import CONF_TOKEN
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.debounce import Debouncer
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from . import delta
from .api import AnchorAuthError, AnchorClient, AnchorConflictError, AnchorError
from .const import CONF_NOTE_ID, DOMAIN, FALLBACK_POLL_INTERVAL

_LOGGER = logging.getLogger(__name__)

MAX_EDIT_ATTEMPTS = 3


class AnchorNoteCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    """Holds one note, as returned by the Anchor API."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry, client: AnchorClient, subentry: ConfigSubentry) -> None:
        super().__init__(
            hass=hass,
            logger=_LOGGER,
            config_entry=entry,
            name=f"{DOMAIN} {subentry.title}",
            update_interval=FALLBACK_POLL_INTERVAL,
            # Event-triggered refreshes should show up right away, not after the default 10 s cooldown.
            request_refresh_debouncer=Debouncer(hass=hass, logger=_LOGGER, cooldown=1, immediate=True),
            always_update=False,
        )
        self.client = client
        self.token: str = subentry.data[CONF_TOKEN]
        self.note_id: str = subentry.data[CONF_NOTE_ID]
        self.note_title = subentry.title
        self._issue_id = f"invalid_token_{subentry.subentry_id}"

    async def _async_update_data(self) -> dict[str, Any]:
        try:
            note = await self.client.get_note(token=self.token, note_id=self.note_id)
        except AnchorAuthError as err:
            # Subentries have no reauth flow, so point the user to "Change API token" (the reconfigure flow).
            ir.async_create_issue(
                hass=self.hass,
                domain=DOMAIN,
                issue_id=self._issue_id,
                is_fixable=False,
                severity=ir.IssueSeverity.ERROR,
                translation_key="invalid_token",
                translation_placeholders={"note": self.note_title},
            )
            raise UpdateFailed(str(err)) from err
        except AnchorError as err:
            raise UpdateFailed(str(err)) from err
        self.delete_token_issue()
        try:
            delta.parse(note["content"])
        except ValueError as err:
            raise UpdateFailed(str(err)) from err
        return note

    def delete_token_issue(self) -> None:
        ir.async_delete_issue(hass=self.hass, domain=DOMAIN, issue_id=self._issue_id)

    async def async_edit(self, edit: Callable[[list[delta.Line]], list[delta.Line]]) -> None:
        """Apply ``edit`` to the note's lines and save it, re-applying it on top of concurrent changes."""
        note = self.data
        if note is None:  # the websocket move command doesn't check availability
            raise HomeAssistantError("The note could not be loaded yet")
        for _ in range(MAX_EDIT_ATTEMPTS):
            try:
                lines = edit(delta.parse(note["content"]))
            except KeyError as err:
                raise HomeAssistantError(f"The item {err} no longer exists in the note") from err
            except ValueError as err:
                raise HomeAssistantError(str(err)) from err
            try:
                note = await self.client.update_content(
                    token=self.token, note_id=self.note_id, content=delta.dump(lines), base_version=note["version"]
                )
            except AnchorConflictError as err:
                note = err.server_note
                continue
            except AnchorError as err:
                raise HomeAssistantError(f"Could not save the note: {err}") from err
            self.async_set_updated_data(note)
            return
        raise HomeAssistantError("The note kept changing while saving, please try again")
