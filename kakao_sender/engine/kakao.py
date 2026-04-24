"""카카오톡 PC 창을 조작하는 저수준 primitive (Phase 3 기반).

설계 원칙:
- win32 `EnumWindows` / `EnumChildWindows` 주력. UIA 거의 안 씀.
- 채팅창은 **HWND 스냅샷 diff** 로 추적. `GetForegroundWindow` 의존 제거.
- 텍스트는 `SendMessage(WM_SETTEXT)` + `PostMessage(WM_KEYDOWN, VK_RETURN)` 로 RichEdit 에 직접 주입. 클립보드·pyautogui 불필요.
- 이미지는 클립보드 DIB 경로 유지 (포맷 복잡도). `SetFocus(richedit_hwnd)` 로 수신 창 명시.
- 탭 전환은 `Ctrl+1/2` 핫키 + `get_active_main_tab` rect 검증.
- 채팅창 닫기는 `PostMessage(chat_hwnd, WM_CLOSE)` — 포그라운드 의존 없음.

각 primitive 는 "성공 시 None, 실패 시 KakaoError" 계약.
"""

from __future__ import annotations

import io
import time
from pathlib import Path
from typing import Literal

import pyautogui
import win32api
import win32clipboard
import win32con
import win32gui
from PIL import Image

from kakao_sender import config, logging_setup
from kakao_sender.engine import detection, windows

_log = logging_setup.get_logger(__name__)

# pyautogui FailSafe 는 마우스가 구석으로 갈 때 중단시켜 배치 중 끊김 원인이 됨.
# 중단은 stop_flag / ESC 로 제어.
pyautogui.FAILSAFE = False
pyautogui.PAUSE = 0.02


class KakaoError(RuntimeError):
    """카톡 조작 실패의 공통 예외."""


class KakaoNotFoundError(KakaoError):
    pass


class KakaoFocusError(KakaoError):
    pass


class KakaoTabError(KakaoError):
    pass


class KakaoTargetMismatchError(KakaoError):
    """채팅창이 열렸으나 제목이 기대와 다름 (단톡방 오발송 방지용)."""


class KakaoClient:
    """카카오톡 세션. `attach()` → 여러 작업 → `detach()`.

    with 문 지원.
    """

    def __init__(self, *, window_title: str | None = None) -> None:
        self.window_title: str = window_title or config.UIA_NAMES.window_title
        self._main_hwnd: int | None = None
        self._current_chat: windows.ChatWindow | None = None

    # ── 라이프사이클 ──────────────────────────────

    def __enter__(self) -> KakaoClient:
        self.attach()
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.detach()

    def attach(self) -> None:
        hwnd = windows.find_main_window()
        if not hwnd:
            raise KakaoNotFoundError(f"카톡 메인 창을 찾을 수 없음: {self.window_title!r}")
        self._main_hwnd = hwnd
        _log.debug("attach 완료: main_hwnd=%s", hex(hwnd))

    def detach(self) -> None:
        self._main_hwnd = None
        self._current_chat = None

    @property
    def main_hwnd(self) -> int:
        if self._main_hwnd is None:
            raise KakaoError("attach() 호출 전")
        return self._main_hwnd

    # ── 창 포커스 ─────────────────────────────────

    def focus_main(self) -> None:
        """메인 창을 포그라운드로. 최소화면 복원."""
        hwnd = self.main_hwnd
        if win32gui.IsIconic(hwnd):
            win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
        try:
            win32gui.SetForegroundWindow(hwnd)
        except Exception as e:
            raise KakaoFocusError(f"메인 창 포커스 실패: {e}") from e
        time.sleep(config.TIMING.focus_settle)

    # ── 사이드바 탭 전환 ─────────────────────────

    def switch_to_friend_tab(self) -> None:
        """친구 탭으로 전환 + rect 로 성공 검증.

        Phase 3 오발송 방지 1차 방어선. 채팅 탭에서 검색하면 단톡방이 결과에
        섞여 나오므로 친구 탭 고정 필수. 전환 실패를 감지해 즉시 예외를 던진다.
        """
        self._switch_tab("친구", hotkey="1")

    def switch_to_chats_tab(self) -> None:
        """채팅 탭 (진단·UI 기능용)."""
        self._switch_tab("채팅", hotkey="2")

    def _switch_tab(self, target: Literal["친구", "채팅"], *, hotkey: str) -> None:
        self.focus_main()
        pyautogui.hotkey("ctrl", hotkey)

        deadline = time.monotonic() + config.TIMING.wait_tab_switch
        last_seen: windows.MainTab = "unknown"
        while time.monotonic() < deadline:
            last_seen = windows.get_active_main_tab(self.main_hwnd)
            if last_seen == target:
                _log.debug("탭 전환 성공: %s", target)
                return
            time.sleep(config.TIMING.poll_interval)

        raise KakaoTabError(
            f"{target} 탭 전환 실패 (마지막 관찰 탭={last_seen!r}) — 발송 중단"
        )

    # ── 검색 ─────────────────────────────────────

    def open_search(self) -> None:
        """Ctrl+F 로 검색창 열기 (이미 열려있어도 무해)."""
        self.focus_main()
        pyautogui.hotkey("ctrl", "f")
        time.sleep(config.TIMING.after_enter)

    def close_search(self) -> None:
        self.focus_main()
        pyautogui.press("escape")
        time.sleep(config.TIMING.after_enter)

    def search(self, query: str, *, clear_first: bool = True) -> None:
        """현재 탭의 검색창에 query 입력. 실행 전 `switch_to_friend_tab` 호출 필수."""
        self.focus_main()
        if clear_first:
            # 기존 내용 전체 선택 후 삭제
            pyautogui.press("end")
            time.sleep(0.03)
            pyautogui.hotkey("shift", "home")
            time.sleep(0.03)

        # 한글·이모지 포함 문자열은 pyautogui.typewrite 가 못 보내므로 클립보드 경유.
        # 이 경로만 클립보드를 쓰고, 본 메시지 전송은 WM_SETTEXT 를 쓴다.
        _set_clipboard_text(query)
        pyautogui.hotkey("ctrl", "v")
        time.sleep(config.TIMING.after_search_input)

    # ── 채팅창 열기 / 닫기 ───────────────────────

    def open_friend_chat(self, expected_name: str) -> windows.ChatWindow:
        """검색창에 이름이 입력된 상태에서 1:1 채팅창 오픈 + 제목 완전일치 검증.

        Phase 3 오발송 방지 2·3차 방어선:
        - HWND 스냅샷 diff 로 새 채팅창 HWND 식별 (포커스 훔김 내성)
        - 제목이 `expected_name` 과 완전일치 안 하면 해당 HWND 만 `WM_CLOSE` 후 예외

        Args:
            expected_name: 검증 대상 이름 (카톡 저장명). 검색어와 동일해야 안전.

        Returns:
            ChatWindow (hwnd, title, richedit_hwnd, list_hwnd).
        """
        before = windows.snapshot_chat_hwnds()

        pyautogui.press("enter")
        time.sleep(config.TIMING.after_enter)
        pyautogui.hotkey("alt", "enter")
        time.sleep(config.TIMING.after_alt_enter)

        new_chat = windows.wait_for_new_chat_window(
            before, timeout=config.TIMING.wait_new_chat_window
        )
        if new_chat is None:
            raise KakaoError(
                f"채팅창이 열리지 않음: {expected_name!r} "
                f"(검색 결과가 없거나 친구가 아닐 수 있음)"
            )

        match = detection.verify_target_title(new_chat.title, expected_name)
        if not match.ok:
            # 엉뚱한 창을 열었음 — HWND 직접 지정해 닫는다 (포그라운드 무관).
            windows.post_close(new_chat.hwnd)
            raise KakaoTargetMismatchError(
                f"제목 불일치 — 실제={new_chat.title!r}, 기대={expected_name!r}, "
                f"이유={match.reason}"
            )

        if new_chat.richedit_hwnd is None:
            windows.post_close(new_chat.hwnd)
            raise KakaoError(
                f"채팅창은 열렸으나 입력창(RichEdit) HWND 를 못 찾음: {expected_name!r}"
            )

        self._current_chat = new_chat
        _log.debug(
            "채팅창 개방 확인: hwnd=%s, title=%r, richedit=%s",
            hex(new_chat.hwnd),
            new_chat.title,
            hex(new_chat.richedit_hwnd),
        )
        return new_chat

    def close_current_chat(self) -> None:
        """현재 추적 중인 채팅창만 닫음. HWND 직접 지정."""
        chat = self._current_chat
        if chat is None:
            return
        windows.post_close(chat.hwnd)
        self._current_chat = None
        time.sleep(config.TIMING.close_chat)

    # ── 전송 ─────────────────────────────────────

    def send_text(self, text: str) -> None:
        """현재 채팅창의 RichEdit 에 텍스트 주입 + Enter.

        `SendMessage(WM_SETTEXT)` 는 기존 내용을 대체한다. `PostMessage(WM_KEYDOWN, VK_RETURN)`
        으로 전송. 클립보드·포커스·IME 의존 없음.
        """
        if not text:
            return
        chat = self._require_current_chat()
        if chat.richedit_hwnd is None:
            raise KakaoError("입력창(RichEdit) HWND 없음 — open_friend_chat 먼저 호출")

        win32gui.SendMessage(chat.richedit_hwnd, win32con.WM_SETTEXT, 0, text)
        time.sleep(config.TIMING.after_ctrl_v_text)
        _post_key(chat.richedit_hwnd, win32con.VK_RETURN)
        time.sleep(config.TIMING.after_send_text)

    def send_image(self, image_path: Path | str) -> None:
        """이미지 전송.

        이미지는 카톡 업로드 플로우를 거쳐야 해 클립보드 DIB + Ctrl+V 경로 유지.
        다만 수신 HWND 를 `SetFocus` 로 명시해 포커스 훔김에 덜 취약하게 만든다.
        """
        path = Path(image_path)
        if not path.exists():
            raise KakaoError(f"이미지 파일 없음: {path}")
        chat = self._require_current_chat()
        if chat.richedit_hwnd is None:
            raise KakaoError("입력창 HWND 없음")

        _copy_image_to_clipboard(path)
        time.sleep(0.15)

        # RichEdit 에 포커스 지정 후 Ctrl+V.
        try:
            win32gui.SetForegroundWindow(chat.hwnd)
        except Exception as e:
            _log.debug("채팅창 포그라운드 실패 (계속 진행): %s", e)
        try:
            win32gui.SendMessage(chat.richedit_hwnd, win32con.WM_SETFOCUS, 0, 0)
        except Exception:
            pass

        pyautogui.hotkey("ctrl", "v")
        time.sleep(config.TIMING.after_ctrl_v_image)
        pyautogui.press("enter")

        wait_sec = detection.estimate_image_upload_wait(path)
        _log.debug(
            "이미지 업로드 대기 %.2f초 (%s, %d bytes)",
            wait_sec,
            path.name,
            path.stat().st_size,
        )
        time.sleep(wait_sec)

    # ── 내부 헬퍼 ────────────────────────────────

    def _require_current_chat(self) -> windows.ChatWindow:
        if self._current_chat is None:
            raise KakaoError("활성 채팅창 없음 — open_friend_chat 먼저 호출")
        return self._current_chat


# ── 모듈 레벨 헬퍼 ──────────────────────────────────


def _post_key(hwnd: int, vk: int) -> None:
    """특정 HWND 에 KEYDOWN/KEYUP 쌍을 Post. 캐릭터 키가 아닌 제어 키용."""
    win32api.PostMessage(hwnd, win32con.WM_KEYDOWN, vk, 0)
    time.sleep(0.02)
    win32api.PostMessage(hwnd, win32con.WM_KEYUP, vk, 0)


def _set_clipboard_text(text: str) -> None:
    """유니코드 텍스트를 Windows 클립보드에 넣음 (pyperclip 대체)."""
    win32clipboard.OpenClipboard()
    try:
        win32clipboard.EmptyClipboard()
        win32clipboard.SetClipboardData(win32con.CF_UNICODETEXT, text)
    finally:
        win32clipboard.CloseClipboard()


def _copy_image_to_clipboard(path: Path) -> None:
    """이미지 파일을 DIB 포맷으로 Windows 클립보드에 주입.

    원본 v5 의 로직을 계승 (`BMP` 헤더 14바이트 제거 후 `CF_DIB`).
    """
    with Image.open(path) as img:
        buffer = io.BytesIO()
        img.convert("RGB").save(buffer, "BMP")
        data = buffer.getvalue()[14:]
    win32clipboard.OpenClipboard()
    try:
        win32clipboard.EmptyClipboard()
        win32clipboard.SetClipboardData(win32clipboard.CF_DIB, data)
    finally:
        win32clipboard.CloseClipboard()
