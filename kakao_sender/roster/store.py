"""회원 명단 — CSV로 영속화.

원본은 `members.json`에 이름 문자열 리스트만 저장했다. 이 버전은:

- 안정적 `id` (UUID) — 이름이 바뀌어도 매핑이 유지되도록
- `group`(그룹 구분), `birthday`, `phone_last4`, `note` 등 선택 메타 — 이름 외엔 비워도 됨
- CSV로 저장 → Excel/Google Sheets와 호환

`load_members_from_legacy_json()` 로 원본 `members.json` 이름 리스트를 흡수 가능.
"""

from __future__ import annotations

import csv
import json
import re
import uuid
from collections.abc import Iterable, Sequence
from dataclasses import asdict, dataclass, field
from datetime import date
from pathlib import Path

from kakao_sender import config, logging_setup

_log = logging_setup.get_logger(__name__)

CSV_FIELDS = (
    "id",
    "name",
    "group",
    "birthday",
    "phone_last4",
    "active",
    "note",
)


@dataclass(slots=True)
class Member:
    """명단 1행."""

    name: str
    group: str = ""  # 그룹 구분 (기수·팀·캠퍼스 등 자유 텍스트)
    birthday: str = ""  # "MM-DD" 권장, "YYYY-MM-DD" 허용
    phone_last4: str = ""
    active: bool = True
    note: str = ""
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])

    def __post_init__(self) -> None:
        self.name = self.name.strip()
        self.group = self.group.strip()
        self.birthday = self.birthday.strip()
        self.phone_last4 = self.phone_last4.strip()
        self.note = self.note.strip()
        if not self.name:
            raise ValueError("Member.name is empty")


def normalize_birthday(text: str) -> str:
    """생일 입력을 `MM-DD` / `YYYY-MM-DD` 로 정규화. 빈 입력은 "" (선택 항목).

    `3-15`, `03/15`, `1999.3.15` 처럼 구분자·자릿수가 섞여도 받아들인다.
    존재하지 않는 날짜는 ValueError.
    """
    raw = text.strip()
    if not raw:
        return ""
    parts = re.split(r"[-/.]", raw)
    if not all(p.isdigit() for p in parts) or len(parts) not in (2, 3):
        raise ValueError(f"생일 형식 오류: {raw!r} (예: 03-15 또는 1999-03-15)")
    nums = [int(p) for p in parts]
    year = nums[0] if len(nums) == 3 else 2000  # MM-DD 는 윤년 기준으로 검사 (02-29 허용)
    month, day = nums[-2:]
    if len(nums) == 3 and len(parts[0]) != 4:
        raise ValueError(f"생일 연도는 4자리: {raw!r}")
    try:
        date(year, month, day)
    except ValueError:
        raise ValueError(f"존재하지 않는 날짜: {raw!r}") from None
    md = f"{month:02d}-{day:02d}"
    return f"{year:04d}-{md}" if len(nums) == 3 else md


def normalize_phone_last4(text: str) -> str:
    """연락처 끝 4자리. 빈 입력은 "" (선택 항목), 그 외는 숫자 4개만 허용."""
    raw = text.strip()
    if raw and not (len(raw) == 4 and raw.isdigit()):
        raise ValueError(f"연락처 끝 4자리는 숫자 4개: {raw!r}")
    return raw


def _to_row(m: Member) -> dict[str, str]:
    d = asdict(m)
    d["active"] = "yes" if m.active else "no"
    return {k: str(d.get(k, "")) for k in CSV_FIELDS}


def _from_row(row: dict[str, str]) -> Member:
    return Member(
        id=(row.get("id") or "").strip() or uuid.uuid4().hex[:12],
        name=row.get("name", ""),
        group=row.get("group", ""),
        birthday=row.get("birthday", ""),
        phone_last4=row.get("phone_last4", ""),
        active=(row.get("active", "yes").strip().lower() not in {"no", "false", "0", ""}),
        note=row.get("note", ""),
    )


def load_members(path: Path | None = None) -> list[Member]:
    """CSV 파일에서 명단 로드. 파일 없으면 빈 리스트."""
    target = path or config.ROSTER_CSV
    if not target.exists():
        _log.info("roster CSV 없음, 빈 명단으로 시작: %s", target)
        return []
    with target.open("r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        return [_from_row(row) for row in reader]


def save_members(members: Iterable[Member], path: Path | None = None) -> None:
    """CSV로 원자적 저장 (tmp → rename)."""
    target = path or config.ROSTER_CSV
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_suffix(target.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_FIELDS)
        writer.writeheader()
        for m in members:
            writer.writerow(_to_row(m))
    tmp.replace(target)
    _log.debug("roster 저장 완료: %s", target)


def load_members_from_legacy_json(path: Path) -> list[Member]:
    """원본 v5의 `members.json` (이름 문자열 리스트) → Member 리스트 승격.

    누락 필드는 기본값, `id`는 새로 발급. 같은 이름이 여러 번 나오면 경고하고 유지.
    """
    with path.open("r", encoding="utf-8") as f:
        raw = json.load(f)
    if not isinstance(raw, list):
        raise ValueError(f"legacy JSON 형식 오류 (list 기대): {path}")

    members: list[Member] = []
    seen: set[str] = set()
    for entry in raw:
        if isinstance(entry, str):
            name = entry.strip()
        elif isinstance(entry, dict) and "name" in entry:
            name = str(entry["name"]).strip()
        else:
            _log.warning("legacy entry 스킵 (알 수 없는 형식): %r", entry)
            continue
        if not name:
            continue
        if name in seen:
            _log.warning("legacy에 동일 이름 중복: %s", name)
        seen.add(name)
        members.append(Member(name=name))
    return members


def sorted_by_name(members: Sequence[Member]) -> list[Member]:
    """한글 포함 이름을 자연스럽게 가나다순 정렬."""
    return sorted(members, key=lambda m: m.name)
