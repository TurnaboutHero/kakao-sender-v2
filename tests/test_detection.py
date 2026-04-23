"""engine.detection 순수 함수 테스트."""

from __future__ import annotations

from pathlib import Path

from kakao_sender.engine.detection import (
    estimate_image_upload_wait,
    verify_target_title,
    wait_until,
)

# ── verify_target_title ────────────────────────


def test_verify_exact_match() -> None:
    m = verify_target_title("회원나", "회원나")
    assert m.ok
    assert m.actual == "회원나"
    assert m.expected == "회원나"


def test_verify_name_with_suffix() -> None:
    # 카톡이 제목에 " - 카카오톡" 같은 접미를 붙이는 경우도 OK
    m = verify_target_title("회원나 - 카카오톡", "회원나")
    assert m.ok

    m2 = verify_target_title("회원나 (2)", "회원나")
    assert m2.ok


def test_verify_wrong_person_fails() -> None:
    m = verify_target_title("회원다", "회원나")
    assert not m.ok
    assert "기대 이름 없음" in m.reason


def test_verify_empty_actual_or_expected() -> None:
    assert not verify_target_title("", "회원나").ok
    assert not verify_target_title("회원나", "").ok


def test_verify_strips_whitespace() -> None:
    assert verify_target_title("  회원나  ", "회원나").ok


# ── estimate_image_upload_wait ─────────────────


def test_estimate_wait_missing_file_falls_back() -> None:
    # 존재하지 않는 경로라도 예외 없이 기본값 반환
    wait = estimate_image_upload_wait("does_not_exist.jpg")
    assert wait > 0


def test_estimate_wait_scales_with_size(tmp_path: Path) -> None:
    small = tmp_path / "small.bin"
    small.write_bytes(b"x" * 1024)  # 1KB
    big = tmp_path / "big.bin"
    big.write_bytes(b"x" * (4 * 1024 * 1024))  # 4MB

    small_wait = estimate_image_upload_wait(small)
    big_wait = estimate_image_upload_wait(big)
    assert big_wait > small_wait


def test_estimate_wait_capped(tmp_path: Path) -> None:
    huge = tmp_path / "huge.bin"
    huge.write_bytes(b"x" * (200 * 1024 * 1024))  # 200MB
    # 상한 30초
    assert estimate_image_upload_wait(huge) <= 30.0


# ── wait_until ─────────────────────────────────


def test_wait_until_success_first_try() -> None:
    calls: list[int] = []

    def cond() -> bool:
        calls.append(1)
        return True

    sleeps: list[float] = []
    fake_time = _FakeTime()
    assert wait_until(cond, timeout=1.0, sleeper=sleeps.append, now=fake_time.now)
    assert len(calls) == 1
    assert sleeps == []


def test_wait_until_timeout() -> None:
    sleeps: list[float] = []
    fake_time = _FakeTime()

    def advance_sleep(d: float) -> None:
        sleeps.append(d)
        fake_time.advance(d)

    assert not wait_until(
        lambda: False,
        timeout=0.5,
        interval=0.1,
        sleeper=advance_sleep,
        now=fake_time.now,
    )
    # 5번 내외로 폴링 (0.1초 간격, 0.5초 타임아웃)
    assert 4 <= len(sleeps) <= 6


def test_wait_until_tolerates_condition_exceptions() -> None:
    """condition이 예외를 던져도 폴링 계속."""
    fake_time = _FakeTime()
    state = {"n": 0}

    def cond() -> bool:
        state["n"] += 1
        if state["n"] < 3:
            raise ValueError("아직 아님")
        return True

    def advance(d: float) -> None:
        fake_time.advance(d)

    assert wait_until(cond, timeout=1.0, interval=0.05, sleeper=advance, now=fake_time.now)
    assert state["n"] >= 3


class _FakeTime:
    """monotonic() 을 흉내내는 가짜 시계."""

    def __init__(self) -> None:
        self._t = 0.0

    def now(self) -> float:
        return self._t

    def advance(self, d: float) -> None:
        self._t += d
