from datetime import timedelta

DOMAIN = "anchor"

CONF_NOTE_ID = "note_id"

SUBENTRY_NOTE = "note"

# Server-sent events deliver changes instantly. Polling only catches what a dropped stream missed.
FALLBACK_POLL_INTERVAL = timedelta(minutes=5)
