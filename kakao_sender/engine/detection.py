"""카톡 창 상태 감지.

원본의 유일한 감지 수단이었던 "하단 한 점의 픽셀 색" 을 대체한다.
우선순위:
1. UIA (uiautomation) — 창 핸들·컨트롤·포커스·텍스트 조회
2. win32 API — 창 제목, 활성 창
3. 시간 기반 대기 — UIA가 제공하지 않는 업로드 완료 등

테스트 가능한 순수 함수들을 위주로 구성하고, 외부 의존 호출은 주입형으로 분리한다.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from kakao_sender import config, logging_setup

_log = logging_setup.get_logger(__name__)


@dataclass(frozen=True, slots=True)
class TargetMatch:
    ok: bool
    actual: str
    expected: str
    reason: str = ""


def verify_target_title(actual_title: str, expected_name: str) -> TargetMatch:
    """열린 채팅창 제목이 기대한 상대와 **완전일치** 하는지.

    Phase 3 실측 결과 (docs/investigation.md):
    - 카톡은 1:1 채팅창 제목에 접미사를 붙이지 않음. 상대 이름 그대로.
    - 단톡방과 1:1 채팅창은 클래스·자식 구조가 동일해 제목만으로 구분 불가.
    - 따라서 부분일치는 위험: 단톡방 이름이 회원 이름을 포함할 수 있다 (예: "회원나 팬클럽").

    strip() 후 정확히 같아야 성공. 그 외 모두 실패.
    """
    actual = (actual_title or "").strip()
    expected = (expected_name or "").strip()

    if not actual:
        return TargetMatch(False, actual, expected, "창 제목을 읽을 수 없음")
    if not expected:
        return TargetMatch(False, actual, expected, "기대 이름이 비어있음")

    if actual == expected:
        return TargetMatch(True, actual, expected)

    return TargetMatch(False, actual, expected, f"제목 불일치 (기대={expected!r})")


def estimate_image_upload_wait(image_path: Path | str) -> float:
    """이미지 업로드 대기 시간 추정.

    파일 크기에 선형 비례 + 최소 바닥값.
    네트워크 속도를 모르니 보수적으로.
    """
    t = config.TIMING
    try:
        size_bytes = Path(image_path).stat().st_size
    except OSError as e:
        _log.warning("이미지 크기 확인 실패: %s (%s)", image_path, e)
        return t.wait_image_upload_extra

    size_mb = size_bytes / (1024 * 1024)
    wait = t.wait_image_upload_extra + size_mb * t.wait_image_per_mb
    # 비정상적으로 큰 이미지 방지 상한 (30초)
    return min(wait, 30.0)


def wait_until(
    condition: Callable[[], bool],
    *,
    timeout: float,
    interval: float = 0.1,
    sleeper: Callable[[float], None] = time.sleep,
    now: Callable[[], float] = time.monotonic,
) -> bool:
    """condition()이 True 반환할 때까지 timeout 초 폴링.

    `sleeper`, `now` 주입으로 테스트 가능.
    """
    deadline = now() + timeout
    while True:
        try:
            if condition():
                return True
        except Exception as e:  # 감지 함수가 예외를 올려도 폴링 유지
            _log.debug("condition 예외: %s", e)
        if now() >= deadline:
            return False
        sleeper(interval)
