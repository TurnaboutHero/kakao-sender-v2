"""KakaoClient.send_text 의 키 입력 차단 테스트 — win32·pyautogui 호출을 가짜로 대체.

실제 카톡 동작(WM_GETTEXT 반환값 등)은 검증하지 않는다. 여기서는 "맨 앞 창이 대상이
아니거나 붙여넣기가 확인되지 않으면 Enter 를 누르지 않는다" 는 제어 흐름만 본다.
"""

from __future__ import annotations

import pytest

from kakao_sender.engine import kakao
from kakao_sender.engine.kakao import KakaoClient, KakaoError, KakaoFocusError
from kakao_sender.engine.windows import ChatWindow

CHAT = 0x100
RICHEDIT = 0x101
OTHER_CHAT = 0x200


class FakeDesktop:
    def __init__(self) -> None:
        self.foreground = CHAT
        self.box = "메시지 입력"
        self.keys: list[str] = []
        self.clipboard = ""
        self.paste_lands = True  # False 면 Ctrl+V 가 다른 창으로 샌 상황
        self.enter_sends = True
        self.cleared = 0
        self.clear_works = True  # False 면 _clear_edit 이 조용히 실패한 상황
        self.on_paste = None  # 붙여넣기 직후 실행할 동작 (예: 포커스 뺏김)

    def hotkey(self, *keys: str) -> None:
        self.keys.append("+".join(keys))
        if keys == ("ctrl", "v"):
            if self.paste_lands and self.foreground == CHAT:
                self.box = (
                    self.clipboard if self.box == "메시지 입력" else self.box + self.clipboard
                )
            if self.on_paste:
                self.on_paste()

    def press(self, key: str) -> None:
        self.keys.append(key)
        if key == "enter" and self.foreground == CHAT and self.enter_sends:
            self.box = "메시지 입력"

    def clear(self, _hwnd: int) -> None:
        self.cleared += 1
        if self.clear_works:
            self.box = "메시지 입력"


@pytest.fixture
def desk(monkeypatch: pytest.MonkeyPatch) -> FakeDesktop:
    d = FakeDesktop()
    monkeypatch.setattr(kakao.time, "sleep", lambda _s: None)
    monkeypatch.setattr(kakao, "_force_foreground", lambda _h: None)
    monkeypatch.setattr(kakao, "_set_clipboard_text", lambda t: setattr(d, "clipboard", t))
    monkeypatch.setattr(kakao, "_get_richedit_text", lambda _h: d.box)
    monkeypatch.setattr(kakao, "_clear_edit", d.clear)
    monkeypatch.setattr(kakao.win32gui, "GetForegroundWindow", lambda: d.foreground)
    monkeypatch.setattr(kakao.win32gui, "SendMessage", lambda *a: 0)
    monkeypatch.setattr(kakao.pyautogui, "hotkey", d.hotkey)
    monkeypatch.setattr(kakao.pyautogui, "press", d.press)
    monkeypatch.setattr(
        kakao.detection, "wait_until", lambda cond, **_kw: cond()
    )  # 폴링 없이 1회 판정
    return d


def _client() -> KakaoClient:
    c = KakaoClient()
    c._current_chat = ChatWindow(hwnd=CHAT, title="회원가", richedit_hwnd=RICHEDIT, list_hwnd=None)
    return c


def test_send_text_happy_path(desk: FakeDesktop) -> None:
    _client().send_text("생일 축하해요 🎂")
    assert desk.keys == ["ctrl+v", "enter"]


def test_no_keys_when_other_window_in_front(desk: FakeDesktop) -> None:
    desk.foreground = OTHER_CHAT
    with pytest.raises(KakaoFocusError):
        _client().send_text("생일 축하해요")
    assert desk.keys == []


def test_no_enter_when_paste_not_confirmed(desk: FakeDesktop) -> None:
    """붙여넣기가 안 들어갔으면 포커스 이상 — 배치를 멈추도록 KakaoFocusError."""
    desk.paste_lands = False
    with pytest.raises(KakaoFocusError, match="붙여넣기 확인 실패"):
        _client().send_text("생일 축하해요")
    assert "enter" not in desk.keys


def test_no_enter_when_focus_stolen_after_paste(desk: FakeDesktop) -> None:
    desk.on_paste = lambda: setattr(desk, "foreground", OTHER_CHAT)
    with pytest.raises(KakaoFocusError):
        _client().send_text("생일 축하해요")
    assert "enter" not in desk.keys


def test_leftover_text_cleared_before_paste(desk: FakeDesktop) -> None:
    """이전 시도에서 남은 텍스트 뒤에 붙여넣어 두 번 이어진 메시지가 나가면 안 됨."""
    desk.box = "생일 축하해요"
    _client().send_text("생일 축하해요")
    assert desk.cleared == 1
    assert desk.keys == ["ctrl+v", "enter"]


def test_no_keys_when_leftover_cannot_be_cleared(desk: FakeDesktop) -> None:
    desk.box = "생일 축하해요"
    desk.clear_works = False
    with pytest.raises(KakaoError, match="남은 내용을 지우지 못함"):
        _client().send_text("생일 축하해요")
    assert desk.keys == []


def test_placeholder_is_not_cleared(desk: FakeDesktop) -> None:
    _client().send_text("생일 축하해요")
    assert desk.cleared == 0


def test_failure_reported_when_text_remains_after_enter(desk: FakeDesktop) -> None:
    desk.enter_sends = False
    with pytest.raises(KakaoError, match="텍스트 전송 실패"):
        _client().send_text("첫 줄\n둘째 줄")


# ── 이미지 미리보기 확인 Enter 대상 ────────────────

KAKAO_PID = 10
PREVIEW = 0x300


@pytest.fixture
def fg(monkeypatch: pytest.MonkeyPatch) -> dict[str, int]:
    state = {"hwnd": PREVIEW}
    pids = {CHAT: KAKAO_PID, PREVIEW: KAKAO_PID, OTHER_CHAT: KAKAO_PID, 0x999: 77}
    monkeypatch.setattr(kakao.win32gui, "GetForegroundWindow", lambda: state["hwnd"])
    monkeypatch.setattr(kakao, "_window_pid", lambda h: pids.get(h, 0))
    return state


def test_image_enter_allowed_on_new_kakao_dialog(fg: dict[str, int]) -> None:
    kakao._require_image_confirm_target(CHAT, {CHAT, OTHER_CHAT})


def test_image_enter_allowed_on_chat_itself(fg: dict[str, int]) -> None:
    fg["hwnd"] = CHAT
    kakao._require_image_confirm_target(CHAT, {CHAT, OTHER_CHAT})


def test_image_enter_blocked_on_other_existing_chat(fg: dict[str, int]) -> None:
    fg["hwnd"] = OTHER_CHAT
    with pytest.raises(KakaoFocusError, match="다른 채팅창"):
        kakao._require_image_confirm_target(CHAT, {CHAT, OTHER_CHAT})


def test_image_enter_blocked_outside_kakao(fg: dict[str, int]) -> None:
    fg["hwnd"] = 0x999
    with pytest.raises(KakaoFocusError, match="카톡이 아님"):
        kakao._require_image_confirm_target(CHAT, {CHAT, OTHER_CHAT})
