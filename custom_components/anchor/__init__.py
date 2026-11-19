"""Anchor notes as Home Assistant to-do lists."""

import asyncio
import logging
from collections import defaultdict

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_URL, Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryNotReady
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import AnchorAuthError, AnchorClient, AnchorError
from .const import DOMAIN
from .coordinator import AnchorNoteCoordinator

_LOGGER = logging.getLogger(__name__)

PLATFORMS = [Platform.TODO]
MAX_RECONNECT_DELAY = 300

type AnchorConfigEntry = ConfigEntry[dict[str, AnchorNoteCoordinator]]


async def async_setup_entry(hass: HomeAssistant, entry: AnchorConfigEntry) -> bool:
    client = AnchorClient(session=async_get_clientsession(hass), url=entry.data[CONF_URL])
    try:
        health = await client.health()
    except AnchorError as err:
        raise ConfigEntryNotReady(str(err)) from err

    dr.async_get(hass).async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, entry.entry_id)},
        name=entry.title,
        manufacturer="Anchor",
        sw_version=health.get("version"),
        configuration_url=entry.data[CONF_URL],
    )

    entry.runtime_data = {
        subentry_id: AnchorNoteCoordinator(hass=hass, entry=entry, client=client, subentry=subentry)
        for subentry_id, subentry in entry.subentries.items()
    }
    # A failing note only makes its own entity unavailable instead of blocking the whole server.
    await asyncio.gather(*(coordinator.async_refresh() for coordinator in entry.runtime_data.values()))

    # One event stream per user: Anchor caps the streams per user, and every stream reports all of the user's notes.
    by_token: dict[str, list[AnchorNoteCoordinator]] = defaultdict(list)
    for coordinator in entry.runtime_data.values():
        by_token[coordinator.token].append(coordinator)
    for token, coordinators in by_token.items():
        entry.async_create_background_task(
            hass=hass,
            target=_listen(client=client, token=token, coordinators=coordinators),
            name=f"{DOMAIN} event stream",
        )

    entry.async_on_unload(entry.add_update_listener(_async_reload))
    await hass.config_entries.async_forward_entry_setups(entry=entry, platforms=PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: AnchorConfigEntry) -> bool:
    # Drops issues of removed notes; the first refresh after a reload recreates the ones still valid.
    for coordinator in entry.runtime_data.values():
        coordinator.delete_token_issue()
    return await hass.config_entries.async_unload_platforms(entry=entry, platforms=PLATFORMS)


async def _async_reload(hass: HomeAssistant, entry: AnchorConfigEntry) -> None:
    """Pick up added or removed notes."""
    await hass.config_entries.async_reload(entry.entry_id)


async def _refresh(coordinators: list[AnchorNoteCoordinator]) -> None:
    for coordinator in coordinators:
        await coordinator.async_request_refresh()


async def _listen(client: AnchorClient, token: str, coordinators: list[AnchorNoteCoordinator]) -> None:
    """Refresh the notes whenever the server reports a change; runs until the entry is unloaded."""
    delay = 1
    while True:
        try:
            async for event in client.events(token):
                delay = 1
                if event in ("open", "sync"):
                    await _refresh(coordinators)
        except AnchorAuthError:
            _LOGGER.warning("Anchor rejected the API token, live updates are disabled until the next reload")
            return
        except AnchorError as err:
            _LOGGER.debug("Anchor event stream dropped: %s", err)
        # The server closes every stream after an hour.
        await asyncio.sleep(delay)
        delay = min(delay * 2, MAX_RECONNECT_DELAY)
