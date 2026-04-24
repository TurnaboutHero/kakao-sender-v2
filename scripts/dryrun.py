"""실기기 dry-run 스크립트 — Phase A/B/C 시나리오 전용.

목적: 전체 Tk UI 없이 `engine.send.send_batch` 파이프라인만 격리 실행해 오발송 방지 3중
방어를 검증한다.

사용법:

    python scripts/dryrun.py --list            # 명단/매핑 확인만 (발송 안 함)
    python scripts/dryrun.py --who "회원명" --send  # 한 명만 발송 (카톡 저장명 또는 정식명)
    python scripts/dryrun.py --trap --send     # 함정 테스트 (단톡방명을 대상으로)
    python scripts/dryrun.py --all --send      # 명단 전원 발송

`--send` 없이는 **발송 안 함** (플래그 없으면 "-dry" 모드로 jobs 와 타이밍만 출력).

기본 메시지: "🧪 v2 dry-run 테스트" (환경변수 DRYRUN_MSG 로 override).
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
import time
from pathlib import Path

# 프로젝트 루트를 sys.path 에 삽입
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from kakao_sender import config, logging_setup  # noqa: E402
from kakao_sender.engine.kakao import KakaoClient, KakaoError  # noqa: E402
from kakao_sender.engine.send import SendJob, SendOutcome, send_batch  # noqa: E402
from kakao_sender.roster import load_mapping, load_members  # noqa: E402

DEFAULT_MSG = os.environ.get("DRYRUN_MSG", "🧪 v2 dry-run 테스트")
# 함정 테스트에 사용할 "단톡방 제목 비슷하게 위장된" 가짜 저장명
# 함정 검사용 저장명 — 실존 친구와 매치되지 않는 고유 문자열이면 뭐든 OK.
# 목적: 친구 탭 검색 0건 → HWND diff 타임아웃 → FAILED 경로 검증.
TRAP_NAME = "__DRYRUN_TRAP_ZZZ__단톡방함정__"


def _print_header(title: str) -> None:
    print()
    print("=" * 70)
    print(f"  {title}")
    print("=" * 70)


def _build_jobs_from_roster(text: str, image: Path | None = None) -> list[SendJob]:
    members = load_members()
    mapping = load_mapping()
    jobs: list[SendJob] = []
    skipped: list[str] = []
    for m in members:
        if not m.active:
            continue
        entry = mapping.get(m.id)
        if entry is None or not entry.kakao_name:
            skipped.append(m.name)
            continue
        jobs.append(
            SendJob(
                target_name=entry.kakao_name,
                legal_name=m.name,
                text=text or None,
                image_path=image,
                member_id=m.id,
            )
        )
    if skipped:
        print(f"[WARN] 매핑 없는 회원 스킵: {skipped}")
    return jobs


def _filter_who(jobs: list[SendJob], who: str) -> list[SendJob]:
    who = who.strip()
    return [j for j in jobs if j.target_name == who or j.legal_name == who]


def _trap_job(text: str, image: Path | None = None) -> SendJob:
    return SendJob(
        target_name=TRAP_NAME,
        legal_name="(함정) 단톡방 이름으로 위장",
        text=text or None,
        image_path=image,
        member_id="trap-target",
    )


def _print_jobs(jobs: list[SendJob]) -> None:
    if not jobs:
        print("  (대상 없음)")
        return
    for i, j in enumerate(jobs, 1):
        img = f", image={j.image_path}" if j.image_path else ""
        print(f"  {i}. {j.legal_name!r:35} → 카톡명 {j.target_name!r}{img}")


def _run_send(jobs: list[SendJob]) -> int:
    try:
        with KakaoClient() as client:
            print(f"[INFO] 카톡 메인 창 attach 성공: hwnd={hex(client.main_hwnd)}")
            results = send_batch(client, jobs)
    except KakaoError as e:
        print(f"[FATAL] KakaoError: {e}")
        return 2
    except Exception as e:
        print(f"[FATAL] {type(e).__name__}: {e}")
        return 3

    _print_header("결과 요약")
    ok = fail = stop = 0
    for r in results:
        icon = {
            SendOutcome.SUCCESS: "✅",
            SendOutcome.FAILED: "❌",
            SendOutcome.STOPPED: "🛑",
            SendOutcome.SKIPPED: "⏭",
        }.get(r.outcome, "•")
        display = r.job.legal_name or r.job.target_name
        print(f"  {icon} {display:35} ({r.duration_sec:.1f}s) {r.reason}")
        if r.outcome == SendOutcome.SUCCESS:
            ok += 1
        elif r.outcome == SendOutcome.FAILED:
            fail += 1
        else:
            stop += 1

    print()
    print(f"  합계: 성공 {ok} / 실패 {fail} / 기타 {stop}")
    return 0 if fail == 0 else 1


def main() -> int:
    if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
        try:
            sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
            sys.stderr.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
        except Exception:
            pass

    parser = argparse.ArgumentParser(description="v2 실기기 dry-run")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--list", action="store_true", help="명단·매핑만 출력 (발송 안 함)")
    mode.add_argument("--who", type=str, help="한 명만 (카톡 저장명 또는 정식명)")
    mode.add_argument("--trap", action="store_true", help="함정 테스트 — 단톡방명을 대상으로 시도")
    mode.add_argument("--all", action="store_true", help="명단 전원")
    parser.add_argument(
        "--send",
        action="store_true",
        help="실제로 발송 (없으면 준비만 하고 중단)",
    )
    parser.add_argument(
        "--text",
        default=DEFAULT_MSG,
        help=f"보낼 메시지 (기본: {DEFAULT_MSG!r}). 빈 문자열이면 이미지만 전송.",
    )
    parser.add_argument(
        "--image",
        type=Path,
        default=None,
        help="첨부 이미지 경로 (jpg/png/bmp). 지정되면 텍스트보다 먼저 전송.",
    )
    parser.add_argument(
        "--debug", action="store_true", help="DEBUG 로그 on"
    )
    args = parser.parse_args()

    logging_setup.configure(level=logging.DEBUG if args.debug else logging.INFO)
    config.ensure_dirs()

    # ── 모드 분기 ────────────────────────────────

    _print_header("v2 dry-run — 환경")
    print(f"  ROSTER_CSV  = {config.ROSTER_CSV}")
    print(f"  MAPPING_JSON= {config.MAPPING_JSON}")
    print(f"  MESSAGE     = {args.text!r}")
    if args.image:
        img_size = args.image.stat().st_size if args.image.exists() else -1
        print(f"  IMAGE       = {args.image} ({img_size:,} bytes)")
    print(f"  SEND 모드   = {args.send}")

    if args.image and not args.image.exists():
        print(f"[ERROR] 이미지 파일 없음: {args.image}")
        return 6

    if args.list:
        _print_header("명단 + 매핑")
        jobs = _build_jobs_from_roster(args.text, args.image)
        _print_jobs(jobs)
        return 0

    if args.trap:
        _print_header("함정 테스트 — 단톡방 제목을 '저장명' 으로 속여 투입")
        jobs = [_trap_job(args.text, args.image)]
        print("  기대 동작: open_friend_chat 단계에서 제목 완전일치 실패 → FAILED")
    elif args.who:
        jobs = _filter_who(_build_jobs_from_roster(args.text, args.image), args.who)
        _print_header(f"단일 대상 — {args.who!r}")
        if not jobs:
            print("  매칭되는 대상 없음. --list 로 명단 확인 후 정식명/저장명으로 재시도.")
            return 4
    else:  # args.all
        jobs = _build_jobs_from_roster(args.text, args.image)
        _print_header(f"전원 발송 ({len(jobs)}명)")

    _print_jobs(jobs)

    if not jobs:
        print("대상 없음 — 중단.")
        return 5

    if not args.send:
        print()
        print("⚠️ --send 플래그 없음 — 준비만 하고 종료.")
        print("   실제 발송은 `--send` 추가.")
        return 0

    # 마지막 안전장치
    print()
    print(f"3초 후 발송 시작. Ctrl+C 로 중단 가능.")
    time.sleep(3)

    return _run_send(jobs)


if __name__ == "__main__":
    sys.exit(main())
