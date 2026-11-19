import json
from typing import Any

import pytest
from homeassistant.config_entries import ConfigSubentryDataWithId
from homeassistant.const import CONF_TOKEN, CONF_URL
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.anchor.const import CONF_NOTE_ID, DOMAIN, SUBENTRY_NOTE

URL = "http://anchor.test:3000"
API = f"{URL}/api"
TOKEN = "token-of-alice"  # nosec B105 - test value
NOTE_ID = "11111111-1111-1111-1111-111111111111"
SUBENTRY_ID = "subentry-1"


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations: None) -> None:
    return


def content(*lines: tuple[str, dict[str, Any] | None]) -> str:
    """Build Delta content from (text, newline attributes) pairs."""
    ops: list[dict[str, Any]] = []
    for text, attributes in lines:
        if text:
            ops.append({"insert": text})
        ops.append({"insert": "\n", **({"attributes": attributes} if attributes else {})})
    return json.dumps({"ops": ops})


def note(body: str, version: int = 1, **extra: Any) -> dict[str, Any]:
    return {"id": NOTE_ID, "title": "Groceries", "content": body, "version": version, "permission": "owner", **extra}


SHOPPING = content(
    ("Groceries", {"header": 2}),
    ("Milk", {"list": "unchecked"}),
    ("Bread", {"list": "checked"}),
    ("Some text", None),
)


@pytest.fixture
def config_entry() -> MockConfigEntry:
    return MockConfigEntry(
        domain=DOMAIN,
        title="anchor.test",
        data={CONF_URL: URL},
        subentries_data=[
            ConfigSubentryDataWithId(
                subentry_id=SUBENTRY_ID,
                subentry_type=SUBENTRY_NOTE,
                title="Groceries",
                unique_id=NOTE_ID,
                data={CONF_TOKEN: TOKEN, CONF_NOTE_ID: NOTE_ID},
            )
        ],
    )
