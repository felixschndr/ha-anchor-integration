from homeassistant.components.todo.const import TodoItemStatus, TodoListEntityFeature
from homeassistant.components.todo.entity import TodoItem, TodoListEntity
from homeassistant.config_entries import ConfigSubentry
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from . import AnchorConfigEntry, delta
from .const import DOMAIN
from .coordinator import AnchorNoteCoordinator


async def async_setup_entry(
    hass: HomeAssistant, entry: AnchorConfigEntry, async_add_entities: AddConfigEntryEntitiesCallback
) -> None:
    server = dr.async_get(hass).async_get_device_by_identifier(
        identifier=(DOMAIN, entry.entry_id), config_entry_id=entry.entry_id
    )
    assert server is not None  # registered in async_setup_entry
    for subentry_id, coordinator in entry.runtime_data.items():
        async_add_entities(
            [AnchorTodoList(coordinator=coordinator, subentry=entry.subentries[subentry_id], server_id=server.id)],
            config_subentry_id=subentry_id,
        )


# Both bases define `available`; Home Assistant resolves that at runtime, pyright can't see it.
class AnchorTodoList(CoordinatorEntity[AnchorNoteCoordinator], TodoListEntity):  # pyright: ignore

    _attr_has_entity_name = True
    _attr_name = None
    _attr_supported_features = (
        TodoListEntityFeature.CREATE_TODO_ITEM
        | TodoListEntityFeature.UPDATE_TODO_ITEM
        | TodoListEntityFeature.DELETE_TODO_ITEM
        | TodoListEntityFeature.MOVE_TODO_ITEM
    )

    def __init__(self, coordinator: AnchorNoteCoordinator, subentry: ConfigSubentry, server_id: str) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = coordinator.note_id
        # A device belongs to one subentry, so every note gets its own, connected via the server's device.
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, coordinator.note_id)},
            name=subentry.title,
            manufacturer="Anchor",
            via_device_id=server_id,
        )

    @property
    def todo_items(self) -> list[TodoItem] | None:  # pyright: ignore[reportIncompatibleVariableOverride]
        if self.coordinator.data is None:
            return None
        return [
            TodoItem(
                uid=item.uid,
                summary=item.summary,
                status=TodoItemStatus.COMPLETED if item.checked else TodoItemStatus.NEEDS_ACTION,
            )
            for item in delta.items(delta.parse(self.coordinator.data["content"]))
        ]

    async def async_create_todo_item(self, item: TodoItem) -> None:
        await self.coordinator.async_edit(lambda lines: delta.add(lines=lines, summary=item.summary or ""))

    async def async_update_todo_item(self, item: TodoItem) -> None:
        checked = None if item.status is None else item.status == TodoItemStatus.COMPLETED
        await self.coordinator.async_edit(
            lambda lines: delta.update(lines=lines, uid=item.uid or "", summary=item.summary, checked=checked)
        )

    async def async_delete_todo_items(self, uids: list[str]) -> None:
        await self.coordinator.async_edit(lambda lines: delta.delete(lines=lines, uids=uids))

    async def async_move_todo_item(self, uid: str, previous_uid: str | None = None) -> None:
        await self.coordinator.async_edit(lambda lines: delta.move(lines=lines, uid=uid, previous_uid=previous_uid))
