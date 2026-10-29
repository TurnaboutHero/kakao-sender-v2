"""명단과 카톡 저장명 매핑 계층.

- `store`: 원본 회원 명단(이름 + 그룹·생일 등 선택 항목). CSV.
- `mapping`: 담당자의 카톡에 실제로 저장된 이름 (담당자마다 다름). JSON.
"""

from kakao_sender.roster.mapping import (
    Mapping,
    MappingEntry,
    load_mapping,
    save_mapping,
)
from kakao_sender.roster.store import (
    Member,
    load_members,
    load_members_from_legacy_json,
    save_members,
)

__all__ = [
    "Mapping",
    "MappingEntry",
    "Member",
    "load_mapping",
    "load_members",
    "load_members_from_legacy_json",
    "save_mapping",
    "save_members",
]
