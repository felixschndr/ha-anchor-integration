import json

import pytest

from custom_components.anchor import delta
from tests.conftest import SHOPPING, content


def texts(lines: list[delta.Line]) -> list[tuple[str, str | None]]:
    return [(line.text, line.checkbox) for line in lines]


def test_parse_splits_multiline_inserts_and_keeps_attributes():
    raw = json.dumps(
        {
            "ops": [
                {"insert": "a\nb"},
                {"insert": "\n", "attributes": {"list": "checked"}},
                {"insert": "bold", "attributes": {"bold": True}},
                {"insert": {"image": "x"}},
                {"insert": "\n\n", "attributes": {"list": "unchecked", "indent": 1}},
                {"insert": "tail"},
            ]
        }
    )
    lines = delta.parse(raw)
    assert texts(lines) == [("a", None), ("b", "checked"), ("bold", "unchecked"), ("", "unchecked"), ("tail", None)]
    assert lines[2].ops == [{"insert": "bold", "attributes": {"bold": True}}, {"insert": {"image": "x"}}]


@pytest.mark.parametrize("raw", [None, ""])
def test_parse_empty(raw: str | None):
    assert delta.parse(raw) == []


def test_dump_roundtrip_merges_text():
    raw = json.dumps(
        {"ops": [{"insert": "a\nb\n"}, {"insert": "c"}, {"insert": "\n", "attributes": {"list": "checked"}}]}
    )
    assert json.loads(delta.dump(delta.parse(raw))) == json.loads(raw) | {
        "ops": [{"insert": "a\nb\nc"}, {"insert": "\n", "attributes": {"list": "checked"}}]
    }


def test_items_only_checkbox_lines_with_stable_uids():
    lines = delta.parse(SHOPPING)
    items = delta.items(lines)
    assert [(item.summary, item.checked) for item in items] == [("Milk", False), ("Bread", True)]
    checked = delta.update(lines=lines, uid=items[0].uid, checked=True)
    assert [item.uid for item in delta.items(checked)] == [item.uid for item in items]


def test_duplicate_texts_get_distinct_uids():
    lines = delta.parse(content(("Milk", {"list": "unchecked"}), ("Milk", {"list": "checked"})))
    first, second = delta.items(lines)
    assert first.uid != second.uid
    assert [item.checked for item in delta.items(delta.update(lines=lines, uid=second.uid, checked=False))] == [
        False,
        False,
    ]


def test_update_summary_and_keep_formatting_when_unchanged():
    lines = delta.parse(
        json.dumps(
            {
                "ops": [
                    {"insert": "Milk", "attributes": {"bold": True}},
                    {"insert": "\n", "attributes": {"list": "unchecked"}},
                ]
            }
        )
    )
    uid = delta.items(lines)[0].uid
    assert delta.update(lines=lines, uid=uid, summary="Milk")[0].ops == lines[0].ops
    renamed = delta.update(lines=lines, uid=uid, summary="Oat milk", checked=None)
    assert renamed[0].ops == [{"insert": "Oat milk"}]
    assert renamed[0].checkbox == "unchecked"


def test_update_unknown_uid():
    with pytest.raises(KeyError):
        delta.update(lines=delta.parse(SHOPPING), uid="nope", checked=True)


def test_add_after_last_checkbox_and_to_empty_note():
    lines = delta.add(lines=delta.parse(SHOPPING), summary="Eggs")
    assert texts(lines) == [
        ("Groceries", None),
        ("Milk", "unchecked"),
        ("Bread", "checked"),
        ("Eggs", "unchecked"),
        ("Some text", None),
    ]
    assert texts(delta.add(lines=[], summary="Eggs")) == [("Eggs", "unchecked")]


def test_delete_keeps_other_lines():
    lines = delta.parse(SHOPPING)
    uids = [item.uid for item in delta.items(lines)]
    assert texts(delta.delete(lines=lines, uids=uids)) == [("Groceries", None), ("Some text", None)]


@pytest.mark.parametrize(
    ("move", "after", "expected"),
    [
        ("C", "A", ["A", "C", "B"]),
        ("A", "C", ["B", "C", "A"]),
        ("C", None, ["C", "A", "B"]),
        ("B", "B", ["A", "B", "C"]),
    ],
)
def test_move(move: str, after: str | None, expected: list[str]):
    lines = delta.parse(
        content(("Title", None), ("A", {"list": "unchecked"}), ("B", {"list": "unchecked"}), ("C", {"list": "checked"}))
    )
    uid = {item.summary: item.uid for item in delta.items(lines)}
    moved = delta.move(lines=lines, uid=uid[move], previous_uid=uid[after] if after else None)
    assert [line.text for line in moved] == ["Title", *expected]


def test_move_between_duplicates():
    lines = delta.parse(content(("A", {"list": "unchecked"}), ("A", {"list": "checked"}), ("B", {"list": "unchecked"})))
    first, second, _ = delta.items(lines)
    moved = delta.move(lines=lines, uid=first.uid, previous_uid=second.uid)
    assert texts(moved) == [("A", "checked"), ("A", "unchecked"), ("B", "unchecked")]


@pytest.mark.parametrize("raw", ["plain text", "{}", '{"ops": "x"}', '{"ops": [1]}', "[]"])
def test_parse_rejects_content_that_is_not_delta(raw: str):
    with pytest.raises(ValueError, match="not Quill Delta"):
        delta.parse(raw)


def test_newlines_in_summaries_stay_on_one_line():
    lines = delta.add(lines=delta.parse(SHOPPING), summary="Eggs\nand ham")
    renamed = delta.update(lines=lines, uid=delta.items(lines)[0].uid, summary="Oat\r\nmilk")
    assert [item.summary for item in delta.items(renamed)] == ["Oat milk", "Bread", "Eggs and ham"]
