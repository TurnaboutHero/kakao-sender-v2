"""engine/windows.py 순수 로직 테스트.

OS 호출은 없다. 분류·매칭·diff 로직만 검증.
"""

from __future__ import annotations

import pytest

from kakao_sender.config import HWND_CLASSES, UIA_NAMES
from kakao_sender.engine import windows as w


# ── classify_toplevel ──────────────────────────────────


def test_classify_toplevel_picks_main_and_chats():
    rows = [
        (0x10610, HWND_CLASSES.chat_window, "카카오톡"),
        (0x6B0ED4, HWND_CLASSES.chat_window, "회원가"),
        (0xA00DD4, HWND_CLASSES.chat_window, "모임 단톡방 (예시)"),
        (0xAC0E2A, HWND_CLASSES.chat_window, ""),  # 참여자 패널 — 제목 빈문자열
        (0x10A82, HWND_CLASSES.chat_window, ""),  # 광고
        (0x12345, "SomeOther", "노이즈"),
    ]
    main, chats = w.classify_toplevel(rows)
    assert main == 0x10610
    # 제목이 비어있는 EVA_Window_Dblclk 는 채팅창에서 제외
    assert (0x6B0ED4, "회원가") in chats
    assert (0xA00DD4, "모임 단톡방 (예시)") in chats
    assert len(chats) == 2


def test_classify_toplevel_no_main_returns_none():
    rows = [
        (0x6B0ED4, HWND_CLASSES.chat_window, "회원가"),
    ]
    main, chats = w.classify_toplevel(rows)
    assert main is None
    assert chats == [(0x6B0ED4, "회원가")]


def test_classify_toplevel_custom_main_title():
    rows = [
        (0x1, HWND_CLASSES.chat_window, "KAKAO"),
    ]
    main, _ = w.classify_toplevel(rows, main_window_title="KAKAO")
    assert main == 0x1


# ── classify_active_main_tab ──────────────────────────


def _children(*specs: tuple[str, bool]):
    """(title, visible) 만 들어있는 간이 fixture.

    Phase 3: 활성 판별은 IsWindowVisible 로 하므로 rect/hwnd/class 는 매칭에 안 쓰임.
    """
    default_rect = (10, 30, 500, 500)
    return [(i, "any", title, default_rect, visible) for i, (title, visible) in enumerate(specs)]


def test_classify_active_main_tab_friend_when_contactlist_visible():
    """Phase 3 실측: ContactListView 가 visible 일 때 '친구'.

    중요: ChatRoomListView 의 rect 는 거의 항상 정상 크기다 (비활성 시에도).
    visible 플래그만이 유효한 구별자.
    """
    children = _children(
        ("ContactListView_0x10614", True),
        ("ChatRoomListView_0x10626", False),
        ("MoreView_0x1063e", False),
        ("LockModeView_0x10646", False),
    )
    assert w.classify_active_main_tab(children) == "친구"


def test_classify_active_main_tab_chat_when_chatroomlist_visible():
    children = _children(
        ("ContactListView_0x1", False),
        ("ChatRoomListView_0x2", True),
    )
    assert w.classify_active_main_tab(children) == "채팅"


def test_classify_active_main_tab_lock_takes_priority():
    """잠금 뷰가 활성이면 친구/채팅 뷰가 뒤에 남아있어도 잠금으로 분류."""
    children = _children(
        ("ContactListView_0x1", True),  # 뒤에서 살아있음
        ("LockModeView_0x2", True),
    )
    assert w.classify_active_main_tab(children) == "잠금"


def test_classify_active_main_tab_more_before_friend():
    children = _children(
        ("ContactListView_0x1", True),
        ("MoreView_0x2", True),
    )
    assert w.classify_active_main_tab(children) == "더보기"


def test_classify_active_main_tab_unknown_when_all_invisible():
    children = _children(
        ("ContactListView_0x1", False),
        ("ChatRoomListView_0x2", False),
    )
    assert w.classify_active_main_tab(children) == "unknown"


def test_classify_active_main_tab_unknown_when_empty():
    assert w.classify_active_main_tab([]) == "unknown"


def test_classify_active_main_tab_ignores_unrelated_titles():
    children = _children(
        ("RandomThing_0x1", True),
        ("", True),
    )
    assert w.classify_active_main_tab(children) == "unknown"


def test_classify_active_main_tab_rect_is_ignored_for_activity():
    """명시적 회귀 방어: rect 가 전부 0 이어도 visible 이면 활성으로 인정.

    Phase 3 초안에서 rect 0 을 '비활성' 으로 썼다가 틀린 것을 기록.
    """
    # rect 은 0, but visible=True
    rect_zero = (0, 0, 0, 0)
    children = [(1, "any", "ChatRoomListView_0x2", rect_zero, True)]
    assert w.classify_active_main_tab(children) == "채팅"


# ── find_child_by_class ────────────────────────────────


def test_find_child_by_class_returns_first_match():
    children = [
        (0x1, "OtherClass", (0, 0, 10, 10)),
        (0x2, HWND_CLASSES.richedit, (0, 0, 10, 10)),
        (0x3, HWND_CLASSES.richedit, (0, 0, 10, 10)),
    ]
    assert w.find_child_by_class(children, target_class=HWND_CLASSES.richedit) == 0x2


def test_find_child_by_class_returns_none_when_missing():
    children = [(0x1, "OtherClass", (0, 0, 10, 10))]
    assert w.find_child_by_class(children, target_class=HWND_CLASSES.richedit) is None


def test_find_child_by_class_accepts_zero_rect():
    """생성 직후 rect 0 인 자식도 반환 — 채팅창 Edit 가 그럴 수 있음."""
    children = [(0x1, HWND_CLASSES.richedit, (0, 0, 0, 0))]
    assert w.find_child_by_class(children, target_class=HWND_CLASSES.richedit) == 0x1


# ── ChatWindow dataclass ───────────────────────────────


def test_chatwindow_is_frozen():
    cw = w.ChatWindow(hwnd=1, title="x", richedit_hwnd=2, list_hwnd=3)
    with pytest.raises(AttributeError):
        cw.hwnd = 99  # type: ignore[misc]


# ── wait_for_new_chat_window (주입형) ──────────────────


class _FakeClock:
    def __init__(self) -> None:
        self.t = 0.0

    def now(self) -> float:
        return self.t

    def sleep(self, s: float) -> None:
        self.t += s


def test_wait_for_new_chat_window_returns_new_hwnd(monkeypatch):
    """enumerate_chat_windows 첫 호출은 prior 동일, 두 번째 호출에 새 창 등장."""
    clock = _FakeClock()
    state = {"calls": 0}

    def fake_enumerate() -> list[w.ChatWindow]:
        state["calls"] += 1
        if state["calls"] == 1:
            return [w.ChatWindow(hwnd=0x100, title="기존", richedit_hwnd=None, list_hwnd=None)]
        return [
            w.ChatWindow(hwnd=0x100, title="기존", richedit_hwnd=None, list_hwnd=None),
            w.ChatWindow(hwnd=0x200, title="새창", richedit_hwnd=0x300, list_hwnd=0x400),
        ]

    monkeypatch.setattr(w, "enumerate_chat_windows", fake_enumerate)
    result = w.wait_for_new_chat_window(
        prior={0x100}, timeout=5.0, poll=0.1, now=clock.now, sleeper=clock.sleep
    )
    assert result is not None
    assert result.hwnd == 0x200
    assert result.title == "새창"


def test_wait_for_new_chat_window_timeout_returns_none(monkeypatch):
    clock = _FakeClock()
    monkeypatch.setattr(
        w,
        "enumerate_chat_windows",
        lambda: [w.ChatWindow(hwnd=0x100, title="기존", richedit_hwnd=None, list_hwnd=None)],
    )
    result = w.wait_for_new_chat_window(
        prior={0x100}, timeout=0.3, poll=0.1, now=clock.now, sleeper=clock.sleep
    )
    assert result is None
    # 대기 중 폴링이 여러 번 일어났는지 확인 (탈출이 즉시가 아님)
    assert clock.t >= 0.3


# ── UIA_NAMES 연동 확인 ────────────────────────────────


def test_classify_uses_uia_names_prefixes():
    """UIA_NAMES 의 prefix 가 실제로 매칭에 쓰이는지."""
    assert UIA_NAMES.view_prefix_friends == "ContactListView"
    assert UIA_NAMES.view_prefix_chats == "ChatRoomListView"
