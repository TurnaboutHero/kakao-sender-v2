"""roster.mapping 단위 테스트."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from kakao_sender.roster.mapping import (
    Mapping,
    MappingEntry,
    load_mapping,
    save_mapping,
)


def test_mapping_entry_strips_and_rejects_empty() -> None:
    e = MappingEntry(kakao_name="  별칭가 🎂  ")
    assert e.kakao_name == "별칭가 🎂"
    with pytest.raises(ValueError, match="kakao_name"):
        MappingEntry(kakao_name="   ")


def test_mapping_set_get_remove_contains() -> None:
    m = Mapping()
    assert "abc" not in m
    assert m.get("abc") is None

    m.set("abc", "별칭나")
    assert "abc" in m
    e = m.get("abc")
    assert e is not None
    assert e.kakao_name == "별칭나"
    assert e.source == "manual"
    assert e.verified_at != ""
    assert m.updated_at != ""

    assert m.remove("abc") is True
    assert "abc" not in m
    assert m.remove("abc") is False


def test_missing_with_dict_and_list() -> None:
    m = Mapping()
    m.set("a", "A형")
    m.set("b", "B형")
    assert sorted(m.missing({"a": None, "b": None, "c": None})) == ["c"]
    assert sorted(m.missing(["a", "b", "c", "d"])) == ["c", "d"]


def test_roundtrip_save_load(tmp_path: Path) -> None:
    path = tmp_path / "mapping.json"
    m = Mapping(owner="담당자A")
    m.set("id1", "별칭가")
    m.set("id2", "별칭다🎂", source="auto_match")
    save_mapping(m, path)

    loaded = load_mapping(path)
    assert loaded.owner == "담당자A"
    assert len(loaded) == 2
    assert loaded.get("id1").kakao_name == "별칭가"
    assert loaded.get("id2").source == "auto_match"


def test_load_missing_file_returns_empty(tmp_path: Path) -> None:
    m = load_mapping(tmp_path / "nope.json")
    assert isinstance(m, Mapping)
    assert len(m) == 0


def test_load_backward_compat_string_values(tmp_path: Path) -> None:
    """value가 문자열인 구형 파일도 수용 (source=imported)."""
    path = tmp_path / "mapping.json"
    path.write_text(
        json.dumps(
            {
                "version": 1,
                "owner": "",
                "updated_at": "",
                "entries": {"id1": "별칭나", "id2": "별칭다"},
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    loaded = load_mapping(path)
    assert len(loaded) == 2
    assert loaded.get("id1").source == "imported"


def test_load_tolerates_future_version(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    path = tmp_path / "mapping.json"
    path.write_text(
        json.dumps(
            {
                "version": 99,
                "entries": {"id1": {"kakao_name": "X"}},
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    loaded = load_mapping(path)
    assert len(loaded) == 1


def test_save_is_atomic(tmp_path: Path) -> None:
    path = tmp_path / "mapping.json"
    m = Mapping()
    m.set("id1", "X")
    save_mapping(m, path)
    assert path.exists()
    assert not path.with_suffix(".json.tmp").exists()
