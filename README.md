# Anchor for Home Assistant

Use notes from [Anchor](https://github.com/zhfahim/anchor), the self-hosted notes app, as to-do lists in Home Assistant.

Every checkbox line of a note becomes a to-do item. Checking, renaming, adding, deleting and reordering items in Home Assistant writes straight back to the note, and changes made in Anchor show up in Home Assistant instantly.

**Table of contents**

<!--TOC-->

- [Installation](#installation)
- [Setup](#setup)
- [How a note maps to a to-do list](#how-a-note-maps-to-a-to-do-list)
- [Development](#development)

<!--TOC-->

## Installation

[![Open your Home Assistant instance and open this repository in the Marketplace.](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=felixschndr&repository=ha-anchor-integration&category=integration)

Or add it by hand: **Settings** > **Marketplace** > **Add from link** > `felixschndr/ha-anchor-integration`.

## Setup

[![Open your Home Assistant instance and start setting up Anchor.](https://my.home-assistant.io/badges/config_flow_start.svg)](https://my.home-assistant.io/redirect/config_flow_start/?domain=anchor)

1. Add the integration and enter the URL of your Anchor server (the one you open in the browser, e.g. `http://anchor.local:3000`). The server shows up as a device.
2. On the integration page, select **Add note**.
3. Paste an API token of the Anchor user whose note you want. Each user creates their token in Anchor under **Settings**.
4. Pick a note. It needs at least one checkbox line.

Repeat steps 2 to 4 for more notes, also from other users.

## How a note maps to a to-do list

- Only checkbox lines are to-do items. Headings, text and other lists stay untouched and are not shown.
- New items are added below the last checkbox line.
- Indented checkboxes appear as normal items; their indentation is kept in Anchor.
- Renaming an item drops its inline formatting (bold, links, …).
- Notes shared with you as *viewer* can't be used, since they are read-only.
- Anchor lines have no ids, so an item is identified by its text (and which occurrence of that text it is). If someone adds an item with the same text above it at the very moment Home Assistant edits it, the edit can hit the other one.

## Development

```bash
poetry install
poetry run pytest --cov=custom_components/anchor
pre-commit run --all-files
```

The end-to-end tests run the integration in a real Home Assistant core against a real Anchor server. They are skipped unless `ANCHOR_E2E_URL` is set:

```bash
docker run --rm -d --name anchor-e2e -p 127.0.0.1:3999:3000 -e PG_USER=anchor -e PG_PASSWORD=password -e PG_DATABASE=anchor ghcr.io/zhfahim/anchor:latest
ANCHOR_E2E_URL=http://127.0.0.1:3999 poetry run pytest tests/e2e
```
