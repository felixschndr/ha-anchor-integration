"""Checkbox lines of an Anchor note.

Anchor stores note content as Quill Delta JSON. A checkbox is a line whose closing newline op carries
``{"list": "checked"|"unchecked"}``. Only those lines become to-do items; every other line is kept as is.
"""

import hashlib
import json
from dataclasses import dataclass
from typing import Any

type Op = dict[str, Any]

CHECKED = "checked"
UNCHECKED = "unchecked"


@dataclass
class Line:
    ops: list[Op]  # text ops before the newline
    newline: Op  # the newline op, carrying block attributes like list or indent

    @property
    def text(self) -> str:
        return "".join(op["insert"] for op in self.ops if isinstance(op.get("insert"), str))

    @property
    def checkbox(self) -> str | None:
        value = self.newline.get("attributes", {}).get("list")
        return value if value in (CHECKED, UNCHECKED) else None


@dataclass(frozen=True)
class Item:
    uid: str
    summary: str
    checked: bool


def parse(content: str | None) -> list[Line]:
    """Split the stored content into lines, mirroring Anchor's deltaToLines."""
    if not content:
        return []
    try:
        ops = json.loads(content)["ops"]
    except (ValueError, KeyError, TypeError) as err:
        raise ValueError("The note's content is not Quill Delta") from err
    if not isinstance(ops, list) or not all(isinstance(op, dict) for op in ops):
        raise ValueError("The note's content is not Quill Delta")
    lines: list[Line] = []
    current: list[Op] = []
    for op in ops:
        insert = op.get("insert")
        if not (isinstance(insert, str) and "\n" in insert):
            current.append(op)
            continue
        attrs = {"attributes": op["attributes"]} if "attributes" in op else {}
        *parts, rest = insert.split("\n")
        for part in parts:
            if part:
                current.append({"insert": part, **attrs})
            lines.append(Line(ops=current, newline={"insert": "\n", **attrs}))
            current = []
        if rest:
            current.append({"insert": rest, **attrs})
    if current:
        lines.append(Line(ops=current, newline={"insert": "\n"}))
    return lines


def dump(lines: list[Line]) -> str:
    """Serialize the lines, merging neighbouring text ops with equal attributes like Quill does."""
    ops: list[Op] = []
    for op in (op for line in lines for op in (*line.ops, line.newline)):
        previous = ops[-1] if ops else None
        if (
            previous is not None
            and isinstance(previous.get("insert"), str)
            and isinstance(op.get("insert"), str)
            and previous.get("attributes") == op.get("attributes")
        ):
            ops[-1] = {**previous, "insert": previous["insert"] + op["insert"]}
        else:
            ops.append(op)
    return json.dumps({"ops": ops}, ensure_ascii=False, separators=(",", ":"))


def items(lines: list[Line]) -> list[Item]:
    """The checkbox lines as items.

    Lines have no ids, so the uid is derived from the text plus its occurrence among equal texts. It survives
    checking, reordering and edits of other lines, which is what Home Assistant needs between two calls.
    """
    seen: dict[str, int] = {}
    result = []
    for line in lines:
        if line.checkbox is None:
            continue
        text = line.text
        seen[text] = seen.get(text, -1) + 1
        digest = hashlib.sha1(text.encode(), usedforsecurity=False).hexdigest()[:12]
        result.append(Item(uid=f"{digest}-{seen[text]}", summary=text, checked=line.checkbox == CHECKED))
    return result


def _index(lines: list[Line], uid: str) -> int:
    checkbox_indices = [i for i, line in enumerate(lines) if line.checkbox is not None]
    for i, item in zip(checkbox_indices, items(lines), strict=True):
        if item.uid == uid:
            return i
    raise KeyError(uid)


def _single_line(summary: str) -> str:
    """A newline would split the item into several lines."""
    return " ".join(summary.splitlines())


def update(lines: list[Line], uid: str, summary: str | None = None, checked: bool | None = None) -> list[Line]:
    result = list(lines)
    i = _index(lines=result, uid=uid)
    line = result[i]
    ops = line.ops if summary is None or summary == line.text else [{"insert": _single_line(summary)}]
    newline = line.newline
    if checked is not None:
        newline = {
            **newline,
            "attributes": {**newline.get("attributes", {}), "list": CHECKED if checked else UNCHECKED},
        }
    result[i] = Line(ops=ops, newline=newline)
    return result


def add(lines: list[Line], summary: str) -> list[Line]:
    """Insert a new unchecked line right after the last checkbox line (or at the end)."""
    checkbox_indices = [i for i, line in enumerate(lines) if line.checkbox is not None]
    position = checkbox_indices[-1] + 1 if checkbox_indices else len(lines)
    new = Line(ops=[{"insert": _single_line(summary)}], newline={"insert": "\n", "attributes": {"list": UNCHECKED}})
    return [*lines[:position], new, *lines[position:]]


def delete(lines: list[Line], uids: list[str]) -> list[Line]:
    drop = {_index(lines=lines, uid=uid) for uid in uids}
    return [line for i, line in enumerate(lines) if i not in drop]


def move(lines: list[Line], uid: str, previous_uid: str | None) -> list[Line]:
    """Move the item right after ``previous_uid``, or before the first checkbox line when it is None."""
    source = _index(lines=lines, uid=uid)
    if previous_uid is None:
        target = next(i for i, line in enumerate(lines) if line.checkbox is not None)
    else:
        target = _index(lines=lines, uid=previous_uid) + 1
    result = list(lines)
    line = result.pop(source)
    result.insert(target - 1 if target > source else target, line)
    return result
