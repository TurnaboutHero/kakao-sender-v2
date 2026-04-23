"""원본 v5 의 `members.json` → v2 `members.csv` 마이그레이션.

사용 예:
    uv run python scripts/migrate.py ../kakao_sender/dist/members.json
    uv run python scripts/migrate.py ../kakao_sender/dist/members.json --out data/members.csv

각 이름을 `Member` 로 승격하면서 UUID 발급. `group`·`birthday` 등은 빈 값으로 두고
이후 UI 또는 수작업으로 채운다.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from kakao_sender import config
from kakao_sender.roster.store import load_members_from_legacy_json, save_members


def main() -> int:
    # Windows 기본 cp949 출력으로 이모지·특수문자 깨짐 방지
    if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
        try:
            sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
            sys.stderr.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
        except Exception:
            pass

    parser = argparse.ArgumentParser(description="legacy members.json → members.csv")
    parser.add_argument("input", type=Path, help="원본 members.json 경로")
    parser.add_argument(
        "--out",
        type=Path,
        default=None,
        help=f"출력 CSV 경로 (기본 {config.ROSTER_CSV})",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="출력 파일이 이미 있어도 덮어씀",
    )
    args = parser.parse_args()

    if not args.input.exists():
        print(f"입력 파일 없음: {args.input}", file=sys.stderr)
        return 2

    out = args.out or config.ROSTER_CSV
    if out.exists() and not args.force:
        print(f"{out} 가 이미 존재함. 덮어쓰려면 --force", file=sys.stderr)
        return 3

    members = load_members_from_legacy_json(args.input)
    save_members(members, out)
    print(f"✓ {len(members)}명 변환 완료 → {out}")
    print()
    print("다음 단계:")
    print("  1. UI 실행: uv run python -m kakao_sender")
    print("  2. '🔗 매핑' 탭에서 각 회원의 카톡 저장명 입력 (또는 '동명 자동 채우기')")
    print("  3. '📨 발송' 탭에서 발송")
    return 0


if __name__ == "__main__":
    sys.exit(main())
