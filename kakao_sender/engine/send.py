"""발송 오케스트레이션 — 원본 `send_one` / `_run` 역할을 대체.

요구사항 정리:
- 오발송 방지: 채팅창 제목으로 대상 이름 검증, 불일치 시 즉시 실패
- 스팸 회피: 발송 간격 랜덤, N명마다 휴식, 문구 변주
- 중단 가능: `SendController.stop()` 즉시 반영
- 결과 추적: 성공/실패/중단 분류, 실패 사유 수집. 중단되면 남은 사람도 전부 STOPPED 로
  기록해 `retry_failed` 로 이어보낼 수 있게 한다 (이미 받은 사람에게는 다시 보내지 않음)
"""

from __future__ import annotations

import random
import time
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field, replace
from enum import StrEnum
from pathlib import Path
from threading import Event

from kakao_sender import config, logging_setup
from kakao_sender.engine.kakao import (
    KakaoClient,
    KakaoError,
    KakaoFocusError,
    KakaoTabError,
    KakaoTargetMismatchError,
)

_log = logging_setup.get_logger(__name__)


class SendOutcome(StrEnum):
    SUCCESS = "success"
    FAILED = "failed"
    SKIPPED = "skipped"
    STOPPED = "stopped"


@dataclass(slots=True)
class SendJob:
    """1건의 발송 요청."""

    target_name: str  # 카톡 저장명 (= 매핑에서 나온 값)
    legal_name: str = ""  # 공식 이름 (로그·UI용)
    text: str | None = None
    image_path: Path | None = None
    member_id: str = ""

    def __post_init__(self) -> None:
        self.target_name = self.target_name.strip()
        if not self.target_name:
            raise ValueError("SendJob.target_name is empty")
        if not (self.text or self.image_path):
            raise ValueError("text/image 모두 비어있음")


@dataclass(slots=True)
class SendResult:
    job: SendJob
    outcome: SendOutcome
    reason: str = ""
    duration_sec: float = 0.0
    attempt: int = 1
    image_sent: bool = False  # 실패했지만 이미지는 이미 나감 — 재시도 시 이미지 생략
    aborts_batch: bool = False  # 탭·포커스 문제 — 다음 건도 같은 이유로 위험하므로 배치 중단

    @property
    def retryable(self) -> bool:
        return self.outcome in (SendOutcome.FAILED, SendOutcome.STOPPED)


@dataclass(slots=True)
class SendController:
    """배치 중단 신호."""

    stop_event: Event = field(default_factory=Event)

    def stop(self) -> None:
        self.stop_event.set()

    @property
    def stopped(self) -> bool:
        return self.stop_event.is_set()


Callback = Callable[[SendResult], None]


def send_one(
    client: KakaoClient,
    job: SendJob,
    *,
    message_variants: Sequence[str] | None = None,
    attempt: int = 1,
) -> SendResult:
    """한 사람에게 전송. 예외를 잡아 SendResult 로 반환 (예외를 밖으로 던지지 않음).

    Phase 3 오발송 방지 파이프라인:
    1. `require_friend_tab()` — 채팅 탭의 단톡방 혼재 결과 제거 (확인만)
    2. `search(target_name)`
    3. `open_friend_chat(target_name)` — HWND diff + 제목 완전일치 내부 검증
    4. 이미지/텍스트 전송 (클립보드 + Ctrl+V, 입력 직전마다 맨 앞 창 확인)
    5. `close_current_chat()` — 대상 HWND 만 닫음. 전송 중 실패해도 닫는다.

    탭·포커스 오류는 `aborts_batch=True` 로 표시해 호출자가 배치를 멈추게 한다.
    """
    t0 = time.monotonic()
    image_sent = False

    def result(outcome: SendOutcome, reason: str = "", *, aborts: bool = False) -> SendResult:
        return SendResult(job, outcome, reason, time.monotonic() - t0, attempt, image_sent, aborts)

    try:
        client.require_friend_tab()
        client.search(job.target_name)
        client.open_friend_chat(job.target_name)
        try:
            if job.image_path is not None:
                client.send_image(job.image_path)
                image_sent = True

            if job.text:
                text_to_send = _pick_text(job.text, message_variants)
                client.send_text(text_to_send)
        finally:
            client.close_current_chat()
        return result(SendOutcome.SUCCESS)

    except KakaoTargetMismatchError as e:
        # 제목 불일치 — open_friend_chat 내부에서 이미 WM_CLOSE 전송 완료
        _log.warning("대상 검증 실패: %s", e)
        return result(SendOutcome.FAILED, f"대상 검증 실패: {e}")
    except (KakaoTabError, KakaoFocusError) as e:
        # 사용자가 탭을 바꿨거나 다른 창을 잡음 — 계속하면 엉뚱한 곳에 입력될 수 있음
        _log.error("배치 중단 사유: %s", e)
        return result(SendOutcome.FAILED, f"{type(e).__name__}: {e}", aborts=True)
    except KakaoError as e:
        return result(SendOutcome.FAILED, f"KakaoError: {e}")
    except Exception as e:  # 예기치 못한 예외도 배치 중단 없이 수집
        _log.exception("send_one 예외")
        return result(SendOutcome.FAILED, f"{type(e).__name__}: {e}")


def send_batch(
    client: KakaoClient,
    jobs: Sequence[SendJob],
    *,
    controller: SendController | None = None,
    on_result: Callback | None = None,
    message_variants: Sequence[str] | None = None,
    rate: config.Timing | None = None,
    sleeper: Callable[[float], None] = time.sleep,
) -> list[SendResult]:
    """명단 전체를 순회하며 발송. 모든 job 에 대해 정확히 하나의 결과를 반환한다.

    - 각 발송 후 `on_result(result)` 콜백 호출 (UI 업데이트용)
    - `controller.stop()`이 걸리면 현재 건 완료 후 중단, 남은 job 은 STOPPED
    - `aborts_batch` 결과가 나오면 즉시 중단, 남은 job 은 STOPPED
    - 간격 랜덤 + N명마다 휴식으로 스팸 필터 회피
    """
    ctrl = controller or SendController()
    rate = rate or config.TIMING
    results: list[SendResult] = []

    def record(result: SendResult) -> None:
        results.append(result)
        if on_result:
            on_result(result)

    def stop_remaining(remaining: Sequence[SendJob], reason: str) -> None:
        for job in remaining:
            record(SendResult(job, SendOutcome.STOPPED, reason))

    _log.info("배치 발송 시작: %d명", len(jobs))
    # 배치 시작 시 친구 탭 확인 — 첫 건에서 단톡방 결과가 섞이는 것을 막는다.
    try:
        client.require_friend_tab()
    except KakaoTabError as e:
        _log.error("배치 시작 시 친구 탭 아님 — 전체 중단: %s", e)
        stop_remaining(jobs, f"친구 탭 아님: {e}")
        return results

    try:
        for idx, job in enumerate(jobs, 1):
            if ctrl.stopped:
                _log.info("사용자 중단 요청 감지")
                stop_remaining(jobs[idx - 1 :], "사용자 중단")
                break

            _log.info(
                "[%d/%d] %s (카톡명: %s)",
                idx,
                len(jobs),
                job.legal_name or job.target_name,
                job.target_name,
            )
            result = send_one(client, job, message_variants=message_variants)
            record(result)

            if result.aborts_batch:
                _log.error("배치 중단 — 남은 %d명은 미발송", len(jobs) - idx)
                stop_remaining(jobs[idx:], f"배치 중단 ({result.reason})")
                break

            if idx < len(jobs):
                _pace(idx, rate, sleeper, ctrl)
    finally:
        # 포커스를 잃은 상태면 close_search 도 실패한다 — 결과를 잃지 않도록 삼킨다.
        try:
            client.close_search()
        except KakaoError as e:
            _log.warning("검색창 닫기 실패 (무시): %s", e)

    counts = {o: sum(1 for r in results if r.outcome == o) for o in SendOutcome}
    _log.info(
        "배치 완료: 성공 %d, 실패 %d, 중단 %d, 총 %d",
        counts[SendOutcome.SUCCESS],
        counts[SendOutcome.FAILED],
        counts[SendOutcome.STOPPED],
        len(results),
    )
    return results


def retry_jobs(results: Iterable[SendResult]) -> list[SendJob]:
    """이어보낼 job 목록 — 실패(FAILED)와 미발송(STOPPED)만. 성공한 사람은 제외.

    이미지는 나가고 텍스트만 실패한 건은 이미지를 빼고 텍스트만 다시 보낸다.
    """
    jobs: list[SendJob] = []
    for r in results:
        if not r.retryable:
            continue
        job = r.job
        if r.image_sent:
            if not job.text:
                continue
            job = replace(job, image_path=None)
        jobs.append(job)
    return jobs


def retry_failed(
    client: KakaoClient,
    prior_results: Iterable[SendResult],
    **kwargs,
) -> list[SendResult]:
    """직전 배치의 실패·미발송자만 다시 발송."""
    jobs = retry_jobs(prior_results)
    if not jobs:
        _log.info("이어보낼 대상 없음")
        return []
    _log.info("실패·미발송 %d명 이어보내기", len(jobs))
    return send_batch(client, jobs, **kwargs)


def _pick_text(base: str, variants: Sequence[str] | None) -> str:
    """base 가 비어있지 않으면 그대로, variants가 주어지면 랜덤 선택.

    UI에서 사용자가 텍스트를 직접 입력한 경우는 그대로 보낸다.
    variants 는 "생일 축하" 같은 동일 의미 문구 변주를 위한 것.
    """
    if variants:
        return random.choice(variants)
    return base


def _pace(
    idx: int, rate: config.Timing, sleeper: Callable[[float], None], ctrl: SendController
) -> None:
    """간격 랜덤 대기 + rest_every_n마다 추가 휴식."""
    if ctrl.stopped:
        return
    if idx % rate.rest_every_n == 0:
        _log.info("%d명마다 휴식: %.1f초", rate.rest_every_n, rate.rest_duration)
        _interruptible_sleep(rate.rest_duration, sleeper, ctrl)
    else:
        delay = random.uniform(rate.between_send_min, rate.between_send_max)
        _interruptible_sleep(delay, sleeper, ctrl)


def _interruptible_sleep(
    total: float, sleeper: Callable[[float], None], ctrl: SendController
) -> None:
    """긴 대기를 짧게 잘라 stop 체크."""
    remaining = total
    step = 0.5
    while remaining > 0 and not ctrl.stopped:
        sleeper(min(step, remaining))
        remaining -= step
