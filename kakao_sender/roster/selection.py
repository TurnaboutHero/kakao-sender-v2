"""발송 대상 범위 고르기 — 전체 / 그룹 / 선택한 사람만.

UI 상태와 분리된 순수 함수라 테스트로 고정한다. 제외 목록(생일자 등)은 어느 범위에서든
마지막에 한 번 더 빠진다 — "선택" 에 생일자를 실수로 넣어도 제외가 이긴다.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from enum import StrEnum

from kakao_sender.roster.store import Member


class TargetScope(StrEnum):
    ALL = "all"
    GROUP = "group"
    SELECTED = "selected"


@dataclass(frozen=True, slots=True)
class TargetSelection:
    picked: list[Member]  # 범위 안 + 제외 안 됨 (매핑 여부는 호출자가 판단)
    excluded: list[Member]  # 범위 안이지만 제외 목록에 있음
    out_of_scope: int  # 활성 회원 중 범위 밖이라 빠진 수


def select_targets(
    members: Sequence[Member],
    *,
    scope: TargetScope,
    group: str = "",
    selected_ids: Iterable[str] = (),
    excluded_ids: Iterable[str] = (),
) -> TargetSelection:
    """활성 회원 중 범위에 드는 사람을 명단 순서대로 고른다.

    - GROUP: `group` 과 그룹 칸이 정확히 같은 사람. `group` 이 비면 아무도 안 고름
      (실수로 전체 발송되는 걸 막기 위해 "그룹 미지정 = 전체" 로 해석하지 않는다).
    - SELECTED: `selected_ids` 에 든 사람만.
    """
    selected = set(selected_ids)
    excluded_set = set(excluded_ids)
    wanted_group = group.strip()

    picked: list[Member] = []
    excluded: list[Member] = []
    out_of_scope = 0
    for m in members:
        if not m.active:
            continue
        if scope is TargetScope.GROUP:
            in_scope = bool(wanted_group) and m.group == wanted_group
        elif scope is TargetScope.SELECTED:
            in_scope = m.id in selected
        else:
            in_scope = True
        if not in_scope:
            out_of_scope += 1
        elif m.id in excluded_set:
            excluded.append(m)
        else:
            picked.append(m)
    return TargetSelection(picked=picked, excluded=excluded, out_of_scope=out_of_scope)


def active_groups(members: Iterable[Member]) -> list[str]:
    """활성 회원에게 붙은 그룹 이름 (빈 값 제외, 가나다순)."""
    return sorted({m.group for m in members if m.active and m.group})
