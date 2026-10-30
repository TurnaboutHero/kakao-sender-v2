"""roster.selection 단위 테스트 — 발송 범위가 넓어지는 쪽으로 새지 않는지가 핵심."""

from __future__ import annotations

from kakao_sender.roster.selection import TargetScope, active_groups, select_targets
from kakao_sender.roster.store import Member

A1 = Member(name="회원가", group="1기", id="a1")
A2 = Member(name="회원나", group="1기", id="a2")
B1 = Member(name="회원다", group="2기", id="b1")
NG = Member(name="회원라", id="ng")  # 그룹 없음
OFF = Member(name="회원마", group="1기", active=False, id="off")
ROSTER = [A1, A2, B1, NG, OFF]


def _names(members: list[Member]) -> list[str]:
    return [m.name for m in members]


def test_all_scope_skips_inactive_and_applies_exclusion() -> None:
    sel = select_targets(ROSTER, scope=TargetScope.ALL, excluded_ids={"a2"})
    assert _names(sel.picked) == ["회원가", "회원다", "회원라"]
    assert _names(sel.excluded) == ["회원나"]
    assert sel.out_of_scope == 0


def test_group_scope_exact_match_only() -> None:
    sel = select_targets(ROSTER, scope=TargetScope.GROUP, group=" 1기 ")
    assert _names(sel.picked) == ["회원가", "회원나"]  # 비활성 회원마는 빠짐
    assert sel.out_of_scope == 2


def test_group_scope_without_group_picks_nobody() -> None:
    """그룹을 안 골랐다고 전체로 퍼지면 안 됨."""
    sel = select_targets(ROSTER, scope=TargetScope.GROUP, group="")
    assert sel.picked == []
    assert sel.out_of_scope == 4


def test_selected_scope_only_checked_people() -> None:
    sel = select_targets(ROSTER, scope=TargetScope.SELECTED, selected_ids={"b1", "ng"})
    assert _names(sel.picked) == ["회원다", "회원라"]
    assert sel.out_of_scope == 2


def test_selected_scope_empty_picks_nobody() -> None:
    assert select_targets(ROSTER, scope=TargetScope.SELECTED).picked == []


def test_exclusion_beats_selection() -> None:
    """생일자를 선택 목록에 실수로 넣어도 제외 목록이 우선."""
    sel = select_targets(
        ROSTER, scope=TargetScope.SELECTED, selected_ids={"a1", "b1"}, excluded_ids={"a1"}
    )
    assert _names(sel.picked) == ["회원다"]
    assert _names(sel.excluded) == ["회원가"]


def test_selected_inactive_member_not_sent() -> None:
    sel = select_targets(ROSTER, scope=TargetScope.SELECTED, selected_ids={"off"})
    assert sel.picked == []


def test_stale_ids_ignored() -> None:
    """삭제된 회원 id 가 선택 목록에 남아 있어도 무해."""
    sel = select_targets(ROSTER, scope=TargetScope.SELECTED, selected_ids={"deleted"})
    assert sel.picked == []


def test_active_groups() -> None:
    assert active_groups(ROSTER) == ["1기", "2기"]
