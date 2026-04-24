"""카톡 HWND 발견·분류·스냅샷 (Phase 3 실측 기반).

주 경로:
- `EnumWindows` / `EnumChildWindows` + `GetClassName` / `GetWindowText` / `GetWindowRect`
- UIA 는 쓰지 않음. 메인 창 UIA 는 빈 Pane 이고, 채팅창 내부는 win32 로 더 풍부하게 보인다.

용어:
- *카톡 창*: 클래스 `EVA_Window_Dblclk` + 관련 제목을 가진 top-level HWND. 메인/1:1 채팅/단톡방/참여자 패널이 모두 이 클래스.
- *채팅창*: 제목이 비어있지 않고, 메인 창("카카오톡") 이 아닌 `EVA_Window_Dblclk`. 1:1/단톡방을 제목만으로는 구분 불가.

여기서 제공하는 함수는 OS 호출 래퍼와 순수 로직의 얇은 결합이다. 테스트는 순수 로직을
가짜 enumerator 로 검증한다 (`tests/test_windows.py`).
"""

from __future__ import annotations

import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from typing import Literal

import win32con
import win32gui

from kakao_sender import config, logging_setup

_log = logging_setup.get_logger(__name__)

# win32 호출 래퍼 — 테스트에서 대체 가능하도록 모듈 수준 참조
_enum_windows: Callable[[Callable[[int, object], None], object], None] = win32gui.EnumWindows
_enum_child_windows: Callable[[int, Callable[[int, object], None], object], None] = (
    win32gui.EnumChildWindows
)
_get_class_name: Callable[[int], str] = win32gui.GetClassName
_get_window_text: Callable[[int], str] = win32gui.GetWindowText
_get_window_rect: Callable[[int], tuple[int, int, int, int]] = win32gui.GetWindowRect
_is_window_visible: Callable[[int], bool] = win32gui.IsWindowVisible


MainTab = Literal["친구", "채팅", "더보기", "잠금", "unknown"]


@dataclass(frozen=True, slots=True)
class ChatWindow:
    """1:1 또는 단톡방 채팅창.

    Phase 3 실측: 메인 창이 아닌 `EVA_Window_Dblclk` 는 내부에 RichEdit 와
    메시지 리스트를 자식 HWND 로 노출한다. 두 값을 함께 저장해 이후 조작에 활용.
    """

    hwnd: int
    title: str
    richedit_hwnd: int | None
    list_hwnd: int | None


# ── win32 enumeration 헬퍼 ─────────────────────────────


def _list_toplevel_windows() -> list[tuple[int, str, str]]:
    """Visible top-level HWND 목록: [(hwnd, class, title)]."""
    rows: list[tuple[int, str, str]] = []

    def cb(hwnd: int, _lparam: object) -> None:
        try:
            if not _is_window_visible(hwnd):
                return
            rows.append((hwnd, _get_class_name(hwnd), _get_window_text(hwnd)))
        except Exception as e:  # 진단 중 창이 죽을 수 있음
            _log.debug("top-level enum 실패 hwnd=%s: %s", hex(hwnd), e)

    _enum_windows(cb, None)
    return rows


def _list_children(hwnd: int) -> list[tuple[int, str, tuple[int, int, int, int]]]:
    """직접 자식 HWND: [(hwnd, class, rect)]. 자식의 자식은 재귀하지 않음."""
    rows: list[tuple[int, str, tuple[int, int, int, int]]] = []

    def cb(child: int, _lparam: object) -> None:
        try:
            rows.append((child, _get_class_name(child), _get_window_rect(child)))
        except Exception as e:
            _log.debug("child enum 실패 hwnd=%s: %s", hex(child), e)

    try:
        _enum_child_windows(hwnd, cb, None)
    except Exception as e:  # 창이 막 닫힌 경우
        _log.debug("EnumChildWindows 실패 hwnd=%s: %s", hex(hwnd), e)
    return rows


def _list_children_with_meta(
    hwnd: int,
) -> list[tuple[int, str, str, tuple[int, int, int, int], bool]]:
    """직접 자식 HWND + title + IsWindowVisible. get_active_main_tab 전용.

    Phase 3 실측: ContactListView 와 ChatRoomListView 는 rect 가 둘 다 비영( )인 채로
    공존한다 (활성/비활성에 관계없이). 활성 판정은 **IsWindowVisible** 가 유일한 신뢰 기준.
    """
    rows: list[tuple[int, str, str, tuple[int, int, int, int], bool]] = []

    def cb(child: int, _lparam: object) -> None:
        try:
            rows.append(
                (
                    child,
                    _get_class_name(child),
                    _get_window_text(child),
                    _get_window_rect(child),
                    bool(_is_window_visible(child)),
                )
            )
        except Exception as e:
            _log.debug("child enum 실패 hwnd=%s: %s", hex(child), e)

    try:
        _enum_child_windows(hwnd, cb, None)
    except Exception as e:
        _log.debug("EnumChildWindows 실패 hwnd=%s: %s", hex(hwnd), e)
    return rows


# ── 순수 분류 로직 (테스트 대상) ────────────────────────


def classify_toplevel(
    rows: Iterable[tuple[int, str, str]],
    *,
    main_window_title: str = config.UIA_NAMES.window_title,
    chat_class: str = config.HWND_CLASSES.chat_window,
) -> tuple[int | None, list[tuple[int, str]]]:
    """top-level 목록에서 (메인 창 HWND, [(채팅창 HWND, 제목)]) 반환.

    메인 창: class == chat_class AND title == main_window_title.
    채팅창: class == chat_class AND title != "" AND HWND != 메인.

    제목이 비어있는 `EVA_Window_Dblclk` (광고 팝업, 참여자 패널 등) 는 채팅창으로 치지 않는다.
    참여자 패널은 전용 조회 함수 (`find_participant_panel`) 로 찾는다.
    """
    main_hwnd: int | None = None
    chats: list[tuple[int, str]] = []
    for hwnd, cls, title in rows:
        if cls != chat_class:
            continue
        if title == main_window_title:
            main_hwnd = hwnd
        elif title:
            chats.append((hwnd, title))
    return main_hwnd, chats


def classify_active_main_tab(
    children: Iterable[tuple[int, str, str, tuple[int, int, int, int], bool]],
    *,
    names: config.UIANames = config.UIA_NAMES,
) -> MainTab:
    """자식 목록 (hwnd, class, title, rect, visible) 에서 활성 탭 라벨 반환.

    Phase 3 실측: ContactListView / ChatRoomListView 는 둘 다 rect 가 정상이라
    **`IsWindowVisible`** 가 유일한 활성 판별 기준. MoreView / LockModeView 는 비활성 시
    rect 도 0 으로 쭈그러들지만, 표준화를 위해 동일하게 visible 만 체크.

    우선순위: 잠금 > 더보기 > 친구 > 채팅. (잠금/더보기가 켜져 있으면 friend/chat 뷰도
    동시에 visible 일 수 있으므로 순서가 중요.)
    """
    prefix_order: tuple[tuple[str, MainTab], ...] = (
        (names.view_prefix_lock, "잠금"),
        (names.view_prefix_more, "더보기"),
        (names.view_prefix_friends, "친구"),
        (names.view_prefix_chats, "채팅"),
    )
    for prefix, label in prefix_order:
        for _hwnd, _cls, title, _rect, visible in children:
            if visible and title.startswith(prefix):
                return label
    return "unknown"


def find_child_by_class(
    children: Iterable[tuple[int, str, tuple[int, int, int, int]]],
    *,
    target_class: str,
) -> int | None:
    """주어진 클래스의 첫 번째 자식 HWND. 없으면 None.

    rect 0 여부는 무시 — 채팅창의 RichEdit 는 생성 직후 rect 가 비정상일 수 있음.
    """
    for hwnd, cls, _rect in children:
        if cls == target_class:
            return hwnd
    return None


# ── 고수준 조회 API (실 호출, OS 경유) ────────────────


def find_main_window() -> int | None:
    """카톡 메인 창 HWND. 없으면 None."""
    main, _ = classify_toplevel(_list_toplevel_windows())
    return main


def enumerate_chat_windows() -> list[ChatWindow]:
    """현재 열린 모든 1:1/단톡 채팅창 목록.

    각 채팅창의 RichEdit 와 메시지 ListControl HWND 도 같이 기록한다.
    """
    _, chats = classify_toplevel(_list_toplevel_windows())
    result: list[ChatWindow] = []
    for hwnd, title in chats:
        children = _list_children(hwnd)
        result.append(
            ChatWindow(
                hwnd=hwnd,
                title=title,
                richedit_hwnd=find_child_by_class(
                    children, target_class=config.HWND_CLASSES.richedit
                ),
                list_hwnd=find_child_by_class(
                    children, target_class=config.HWND_CLASSES.list_control
                ),
            )
        )
    return result


def snapshot_chat_hwnds() -> set[int]:
    """현재 모든 채팅창 HWND 집합 (diff 계산용)."""
    return {c.hwnd for c in enumerate_chat_windows()}


def wait_for_new_chat_window(
    prior: set[int],
    *,
    timeout: float = config.TIMING.wait_new_chat_window,
    poll: float = config.TIMING.poll_interval,
    now: Callable[[], float] = time.monotonic,
    sleeper: Callable[[float], None] = time.sleep,
) -> ChatWindow | None:
    """prior 집합에 없던 새 채팅창이 나타날 때까지 대기.

    여러 개가 동시에 생기는 경우는 실질적으로 없으나, 그럴 때는 임의의 하나를 반환.
    """
    deadline = now() + timeout
    while True:
        for chat in enumerate_chat_windows():
            if chat.hwnd not in prior:
                return chat
        if now() >= deadline:
            return None
        sleeper(poll)


def get_active_main_tab(main_hwnd: int) -> MainTab:
    """메인 창의 활성 탭 라벨."""
    return classify_active_main_tab(_list_children_with_meta(main_hwnd))


def find_friend_tab_search_edit(main_hwnd: int) -> int | None:
    """친구 탭의 검색 Edit HWND 찾기.

    메인 창 자식 중 title 이 `view_prefix_friends` 로 시작하고 visible 인 View 를 찾고,
    그 아래 첫 번째 `Edit` 클래스 자식 HWND 를 반환. 친구 탭이 비활성이면 None.
    """
    # ContactListView_* (visible)
    contact_view: int | None = None
    for hwnd, _cls, title, _rect, visible in _list_children_with_meta(main_hwnd):
        if visible and title.startswith(config.UIA_NAMES.view_prefix_friends):
            contact_view = hwnd
            break
    if contact_view is None:
        return None

    # 그 안의 Edit (첫 번째)
    for hwnd, cls, _rect in _list_children(contact_view):
        if cls == config.HWND_CLASSES.edit_standard:
            return hwnd
    return None


def post_close(hwnd: int) -> None:
    """특정 HWND 에 WM_CLOSE 전송 (포그라운드 무관)."""
    try:
        win32gui.PostMessage(hwnd, win32con.WM_CLOSE, 0, 0)
    except Exception as e:
        _log.debug("PostMessage WM_CLOSE 실패 hwnd=%s: %s", hex(hwnd), e)
