"""정식명(Member) ↔ 담당자 카톡 저장명 매핑.

담당자마다 카톡에 저장한 이름이 다르고, 시간 지나며 바뀐다.
공용 명단과 분리해서 담당자 PC 로컬에만 저장.

키: Member.id (UUID) — 이름 바뀌어도 살아남도록
값: MappingEntry { kakao_name, source, verified_at }
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from collections.abc import Mapping as _ABCMapping
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Literal

from kakao_sender import config, logging_setup

_log = logging_setup.get_logger(__name__)

Source = Literal["manual", "auto_match", "imported"]

SCHEMA_VERSION = 1


@dataclass(slots=True)
class MappingEntry:
    """한 Member에 대한 카톡 저장명 정보."""

    kakao_name: str
    source: Source = "manual"
    verified_at: str = ""  # ISO 8601, 비어있으면 미검증

    def __post_init__(self) -> None:
        self.kakao_name = self.kakao_name.strip()
        if not self.kakao_name:
            raise ValueError("MappingEntry.kakao_name is empty")

    def touch(self) -> None:
        self.verified_at = datetime.now().astimezone().isoformat(timespec="seconds")


@dataclass(slots=True)
class Mapping:
    """담당자 1명의 전체 매핑 + 메타."""

    entries: dict[str, MappingEntry] = field(default_factory=dict)
    owner: str = ""
    updated_at: str = ""

    def set(self, member_id: str, kakao_name: str, source: Source = "manual") -> None:
        entry = MappingEntry(kakao_name=kakao_name, source=source)
        entry.touch()
        self.entries[member_id] = entry
        self._touch()

    def get(self, member_id: str) -> MappingEntry | None:
        return self.entries.get(member_id)

    def remove(self, member_id: str) -> bool:
        existed = self.entries.pop(member_id, None) is not None
        if existed:
            self._touch()
        return existed

    def __contains__(self, member_id: object) -> bool:
        return member_id in self.entries

    def __iter__(self) -> Iterator[tuple[str, MappingEntry]]:
        return iter(self.entries.items())

    def __len__(self) -> int:
        return len(self.entries)

    def missing(self, member_ids: _ABCMapping[str, object] | list[str]) -> list[str]:
        """주어진 id들 중 매핑 없는 것들."""
        ids = list(member_ids) if isinstance(member_ids, list) else list(member_ids.keys())
        return [mid for mid in ids if mid not in self.entries]

    def _touch(self) -> None:
        self.updated_at = datetime.now().astimezone().isoformat(timespec="seconds")


def load_mapping(path: Path | None = None) -> Mapping:
    target = path or config.MAPPING_JSON
    if not target.exists():
        _log.info("mapping 파일 없음, 빈 매핑으로 시작: %s", target)
        return Mapping()
    with target.open("r", encoding="utf-8") as f:
        raw = json.load(f)
    version = int(raw.get("version", 1))
    if version > SCHEMA_VERSION:
        _log.warning(
            "mapping 스키마 버전 %d가 현재 %d보다 높음 — 호환성 문제 가능", version, SCHEMA_VERSION
        )

    entries_raw = raw.get("entries", {})
    entries: dict[str, MappingEntry] = {}
    for mid, val in entries_raw.items():
        if isinstance(val, str):
            entries[mid] = MappingEntry(kakao_name=val, source="imported")
        elif isinstance(val, dict) and "kakao_name" in val:
            entries[mid] = MappingEntry(
                kakao_name=val["kakao_name"],
                source=val.get("source", "manual"),
                verified_at=val.get("verified_at", ""),
            )
        else:
            _log.warning("mapping entry 스킵 (알 수 없는 형식) %s: %r", mid, val)

    return Mapping(
        entries=entries,
        owner=str(raw.get("owner", "")),
        updated_at=str(raw.get("updated_at", "")),
    )


def save_mapping(mapping: Mapping, path: Path | None = None) -> None:
    target = path or config.MAPPING_JSON
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "version": SCHEMA_VERSION,
        "owner": mapping.owner,
        "updated_at": mapping.updated_at,
        "entries": {mid: asdict(e) for mid, e in mapping.entries.items()},
    }
    tmp = target.with_suffix(target.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    tmp.replace(target)
    _log.debug("mapping 저장 완료: %s (%d건)", target, len(mapping))
