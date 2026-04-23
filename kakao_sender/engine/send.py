"""발송 오케스트레이션 — 원본 `send_one` / `_run` 역할을 대체.

요구사항 정리:
- 오발송 방지: 채팅창 제목으로 대상 이름 검증, 불일치 시 즉시 실패
- 스팸 회피: 발송 간격 랜덤, N명마다 휴식, 문구 변주
- 중단 가능: `SendController.stop()` 즉시 반영
- 결과 추적: 성공/실패/스킵 분류, 실패 사유 수집, 재시도용 실패자 목록 추출
"""

from __future__ import annotations

import random
import time
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from threading import Event

from kakao_sender import config, logging_setup
from kakao_sender.engine import detection
from kakao_sender.engine.kakao import KakaoClient, KakaoError

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
    """한 사람에게 전송. 예외를 잡아 SendResult로 반환."""
    t0 = time.monotonic()
    try:
        client.search(job.target_name)
        title = client.open_friend_chat()

        match = detection.verify_target_title(title, job.target_name)
        if not match.ok:
            # 엉뚱한 채팅창이 열렸음 — 닫고 실패 반환
            client.close_active_chat()
            reason = f"대상 검증 실패: 창 제목={match.actual!r}, 기대={match.expected!r} ({match.reason})"
            _log.warning("%s", reason)
            return SendResult(job, SendOutcome.FAILED, reason, time.monotonic() - t0, attempt)

        if job.image_path is not None:
            client.send_image(job.image_path)

        if job.text:
            text_to_send = _pick_text(job.text, message_variants)
            client.send_text(text_to_send)

        client.close_active_chat()
        return SendResult(job, SendOutcome.SUCCESS, "", time.monotonic() - t0, attempt)

    except KakaoError as e:
        return SendResult(
            job, SendOutcome.FAILED, f"KakaoError: {e}", time.monotonic() - t0, attempt
        )
    except Exception as e:  # 예기치 못한 예외도 배치 중단 없이 수집
        _log.exception("send_one 예외")
        return SendResult(
            job, SendOutcome.FAILED, f"{type(e).__name__}: {e}", time.monotonic() - t0, attempt
        )


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
    """명단 전체를 순회하며 발송.

    - 각 발송 후 `on_result(result)` 콜백 호출 (UI 업데이트용)
    - `controller.stop()`이 걸리면 현재 건 완료 후 중단
    - 간격 랜덤 + N명마다 휴식으로 스팸 필터 회피
    """
    ctrl = controller or SendController()
    rate = rate or config.TIMING
    results: list[SendResult] = []

    _log.info("배치 발송 시작: %d명", len(jobs))
    client.open_search()

    try:
        for idx, job in enumerate(jobs, 1):
            if ctrl.stopped:
                _log.info("사용자 중단 요청 감지")
                results.append(SendResult(job, SendOutcome.STOPPED, "사용자 중단"))
                if on_result:
                    on_result(results[-1])
                break

            _log.info(
                "[%d/%d] %s (카톡명: %s)",
                idx,
                len(jobs),
                job.legal_name or job.target_name,
                job.target_name,
            )
            result = send_one(client, job, message_variants=message_variants)
            results.append(result)
            if on_result:
                on_result(result)

            if idx < len(jobs):
                _pace(idx, rate, sleeper, ctrl)
    finally:
        client.close_search()

    succeeded = sum(1 for r in results if r.outcome == SendOutcome.SUCCESS)
    failed = sum(1 for r in results if r.outcome == SendOutcome.FAILED)
    _log.info("배치 완료: 성공 %d, 실패 %d, 총 %d", succeeded, failed, len(results))
    return results


def retry_failed(
    client: KakaoClient,
    prior_results: Iterable[SendResult],
    **kwargs,
) -> list[SendResult]:
    """직전 배치의 실패자만 다시 발송."""
    failed_jobs = [r.job for r in prior_results if r.outcome == SendOutcome.FAILED]
    if not failed_jobs:
        _log.info("재시도할 실패자 없음")
        return []
    _log.info("실패자 %d명 재시도", len(failed_jobs))
    return send_batch(client, failed_jobs, **kwargs)


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
