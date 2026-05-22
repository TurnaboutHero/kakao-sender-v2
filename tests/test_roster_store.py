"""roster.store 단위 테스트."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from kakao_sender.roster.store import (
    Member,
    load_members,
    load_members_from_legacy_json,
    save_members,
    sorted_by_name,
)


def test_member_strips_whitespace_and_generates_id() -> None:
    m = Member(name="  회원나  ", group=" A조 ")
    assert m.name == "회원나"
    assert m.group == "A조"
    assert len(m.id) == 12
    assert m.active is True


def test_member_rejects_empty_name() -> None:
    with pytest.raises(ValueError, match=r"Member\.name"):
        Member(name="   ")


def test_roundtrip_save_load(tmp_path: Path) -> None:
    csv_path = tmp_path / "members.csv"
    original = [
        Member(name="회원가", group="A요회", birthday="03-15"),
        Member(name="회원나", group="B요회", birthday="11-02", phone_last4="1234", active=False),
        Member(name="회원다", note="해외 거주"),
    ]
    save_members(original, csv_path)

    loaded = load_members(csv_path)
    assert len(loaded) == 3
    names = {m.name for m in loaded}
    assert names == {"회원가", "회원나", "회원다"}

    jinseo = next(m for m in loaded if m.name == "회원나")
    assert jinseo.group == "B요회"
    assert jinseo.birthday == "11-02"
    assert jinseo.phone_last4 == "1234"
    assert jinseo.active is False

    member_c = next(m for m in loaded if m.name == "회원다")
    assert member_c.note == "해외 거주"
    assert member_c.active is True


def test_load_missing_file_returns_empty(tmp_path: Path) -> None:
    assert load_members(tmp_path / "nope.csv") == []


def test_save_is_atomic(tmp_path: Path) -> None:
    """tmp 파일 경유 확인: 저장 후 .tmp가 남아있지 않아야 함."""
    csv_path = tmp_path / "members.csv"
    save_members([Member(name="A")], csv_path)
    assert csv_path.exists()
    assert not csv_path.with_suffix(".csv.tmp").exists()


def test_legacy_json_import_list_of_strings(tmp_path: Path) -> None:
    legacy = tmp_path / "members.json"
    legacy.write_text(
        json.dumps(["회원가", "회원나", "회원다"], ensure_ascii=False),
        encoding="utf-8",
    )
    loaded = load_members_from_legacy_json(legacy)
    assert [m.name for m in loaded] == ["회원가", "회원나", "회원다"]
    assert all(m.active and not m.group and not m.birthday for m in loaded)
    assert len({m.id for m in loaded}) == 3  # id는 모두 다름


def test_legacy_json_import_handles_duplicates_and_empties(tmp_path: Path) -> None:
    legacy = tmp_path / "members.json"
    legacy.write_text(
        json.dumps(["  ", "회원가", "회원가", ""], ensure_ascii=False),
        encoding="utf-8",
    )
    loaded = load_members_from_legacy_json(legacy)
    # 빈 이름 제외하되, 중복은 경고만 하고 둘 다 유지 (사용자 판단)
    assert [m.name for m in loaded] == ["회원가", "회원가"]


def test_legacy_json_wrong_shape(tmp_path: Path) -> None:
    legacy = tmp_path / "members.json"
    legacy.write_text(json.dumps({"not": "a list"}), encoding="utf-8")
    with pytest.raises(ValueError, match="legacy JSON"):
        load_members_from_legacy_json(legacy)


def test_sorted_by_name_is_stable_ko() -> None:
    members = [Member(name=n) for n in ["다회원", "가회원", "나회원"]]
    sorted_names = [m.name for m in sorted_by_name(members)]
    # 한글 기본 정렬: 가나다 순
    assert sorted_names == ["가회원", "나회원", "다회원"]
