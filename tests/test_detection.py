"""engine.detection 순수 함수 테스트."""

from __future__ import annotations

from pathlib import Path

from kakao_sender.engine.detection import (
    estimate_image_upload_wait,
    normalize_input_text,
    pasted_text_present,
    text_left_in_input,
    verify_target_title,
    wait_until,
)

PLACEHOLDER = "메시지 입력"

# ── 입력창 텍스트 비교 ─────────────────────────


def test_normalize_drops_whitespace_emoji_and_object_char() -> None:
    assert normalize_input_text("생일 축하해요 🎂\r\n") == "생일축하해요"
    assert normalize_input_text("🎂 생일￼ 축하해 🎉") == "생일축하해"
    assert normalize_input_text("❤️ 사랑해") == "사랑해"  # BMP 기호 + variation selector


def test_pasted_text_present_on_placeholder_box() -> None:
    assert pasted_text_present("생일 축하해요 🎂", PLACEHOLDER, "생일 축하해요 🎂")


def test_pasted_text_present_tolerates_richedit_newlines_and_emoji_objects() -> None:
    assert pasted_text_present("첫 줄\n둘째 줄 🎉", PLACEHOLDER, "첫 줄\r둘째 줄 ￼")


def test_pasted_text_missing_when_paste_went_elsewhere() -> None:
    """붙여넣기가 다른 창으로 새면 입력창은 그대로 — Enter 를 누르면 안 됨."""
    assert not pasted_text_present("생일 축하해요", PLACEHOLDER, PLACEHOLDER)
    assert not pasted_text_present("생일 축하해요", "", "")


def test_pasted_text_wrong_content_rejected() -> None:
    assert not pasted_text_present("생일 축하해요", PLACEHOLDER, "다른 내용")


def test_pasted_emoji_only_message_judged_by_change() -> None:
    assert pasted_text_present("🎂🎉", PLACEHOLDER, "🎂🎉")
    assert not pasted_text_present("🎂🎉", PLACEHOLDER, PLACEHOLDER)
    # before 를 "" 로 읽었다가 placeholder 가 나타난 건 "내용 변화" 가 아님
    assert not pasted_text_present("🎂🎉", "", PLACEHOLDER)


def test_text_left_in_input() -> None:
    assert text_left_in_input("생일 축하해요 🎂", "생일 축하해요 🎂")
    assert text_left_in_input("첫 줄\n둘째 줄", "첫 줄\r둘째 줄")  # 원문 비교로는 놓치던 경우
    assert not text_left_in_input("생일 축하해요", PLACEHOLDER)
    assert not text_left_in_input("🎂", "🎂")  # 판정 불가 → 전송된 것으로 간주
    # placeholder "메시지 입력" 의 일부인 짧은 메시지도 빈 입력창이면 전송된 것
    assert not text_left_in_input("입력", PLACEHOLDER)


# ── verify_target_title ────────────────────────


def test_verify_exact_match() -> None:
    m = verify_target_title("회원나", "회원나")
    assert m.ok
    assert m.actual == "회원나"
    assert m.expected == "회원나"


def test_verify_partial_match_rejected() -> None:
    """Phase 3: 부분일치는 단톡방 오발송 위험 — 실패해야 함.

    예: 단톡방 이름 "회원나 팬클럽" 이 회원명 "회원나" 를 포함.
    완전일치만 통과시켜 오발송을 구조적으로 차단.
    """
    m = verify_target_title("회원나 - 카카오톡", "회원나")
    assert not m.ok

    m2 = verify_target_title("회원나 (2)", "회원나")
    assert not m2.ok

    m3 = verify_target_title("회원나 팬클럽", "회원나")
    assert not m3.ok


def test_verify_wrong_person_fails() -> None:
    m = verify_target_title("회원다", "회원나")
    assert not m.ok
    assert "제목 불일치" in m.reason


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
