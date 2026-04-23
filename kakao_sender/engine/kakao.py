"""카카오톡 PC 창을 조작하는 저수준 primitive.

설계 원칙:
- UIA가 노출하는 컨트롤은 UIA로 (검증 가능, 다크모드/DPI 무관)
- 텍스트·이미지 전송은 클립보드 + 단축키 (카톡 내부 입력 위젯이 UIA SetValue를 무시하는 경우 많음)
- 이미지 업로드 완료는 시간 기반 추정 (Phase 2에서 UIA로 개선 여지)

각 primitive는 "성공 시 None, 실패 시 RuntimeError" 계약.
상태 검증은 `detection.py`의 순수 함수에 위임.
"""

from __future__ import annotations

import io
import time
from pathlib import Path
from typing import Literal

import pyautogui
import pyperclip
import uiautomation as auto
import win32clipboard
import win32con
import win32gui
from PIL import Image

from kakao_sender import config, logging_setup
from kakao_sender.engine import detection

_log = logging_setup.get_logger(__name__)

# pyautogui가 스크린 구석으로 마우스를 밀면 FailSafe 예외를 던짐 — 배치 발송 도중 중단 원인이 됨.
# 중단은 stop_flag / ESC 로 제어하므로 이 자동 feature는 꺼둔다.
pyautogui.FAILSAFE = False
pyautogui.PAUSE = 0.02


class KakaoError(RuntimeError):
    """카톡 조작 실패의 공통 예외."""


class KakaoNotFoundError(KakaoError):
    pass


class KakaoFocusError(KakaoError):
    pass


class KakaoClient:
    """카카오톡 창 하나에 대한 조작 세션.

    라이프사이클: `attach()` → (여러 작업) → `detach()`.
    `with KakaoClient() as kk:` 패턴 지원.
    """

    def __init__(self, *, window_title: str | None = None) -> None:
        self.window_title: str = window_title or config.UIA_NAMES.window_title
        self._hwnd: int | None = None
        self._window: auto.WindowControl | None = None

    # ── 라이프사이클 ──────────────────────────────

    def __enter__(self) -> KakaoClient:
        self.attach()
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.detach()

    def attach(self) -> None:
        hwnd = win32gui.FindWindow(None, self.window_title)
        if not hwnd:
            raise KakaoNotFoundError(f"창을 찾을 수 없음: {self.window_title!r}")
        self._hwnd = hwnd
        # UIA 창 참조: 검색 깊이 1~2로 제한해 속도 확보
        self._window = auto.WindowControl(
            searchFromControl=auto.GetRootControl(),
            Name=self.window_title,
            searchDepth=2,
        )
        if not self._window.Exists(maxSearchSeconds=2.0):
            _log.warning("UIA로 창을 못 찾음 — win32 핸들만 사용")
            self._window = None
        _log.debug("attach 완료: hwnd=%s, uia=%s", hex(hwnd), bool(self._window))

    def detach(self) -> None:
        self._hwnd = None
        self._window = None

    # ── 창 조작 ───────────────────────────────────

    @property
    def hwnd(self) -> int:
        if self._hwnd is None:
            raise KakaoError("attach() 호출 전")
        return self._hwnd

    def focus(self) -> None:
        """창을 포그라운드로. 최소화면 복원."""
        hwnd = self.hwnd
        if win32gui.IsIconic(hwnd):
            win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
        try:
            win32gui.SetForegroundWindow(hwnd)
        except Exception as e:
            raise KakaoFocusError(f"포커스 실패: {e}") from e
        time.sleep(config.TIMING.focus_settle)

    # ── 사이드바 탭 전환 ─────────────────────────

    def switch_to_tab(self, tab: Literal["친구", "채팅"]) -> None:
        """좌측 사이드바에서 지정 탭 클릭.

        UIA가 탭 버튼을 노출하면 이름으로 클릭, 실패하면 단축키 fallback.
        카톡 PC 단축키 (2025~2026 기준): Ctrl+1=친구, Ctrl+2=채팅 (버전별 실측 필요).
        """
        if self._window is not None:
            try:
                btn = self._window.ButtonControl(Name=tab, searchDepth=8)
                if btn.Exists(maxSearchSeconds=1.0):
                    btn.Click(simulateMove=False)
                    time.sleep(config.TIMING.after_enter)
                    _log.debug("UIA 탭 클릭 성공: %s", tab)
                    return
            except Exception as e:
                _log.debug("UIA 탭 클릭 실패 (fallback 진행): %s", e)

        # Fallback: 단축키 (카톡 버전에 따라 안 먹힐 수 있음)
        self.focus()
        shortcut = {"친구": "1", "채팅": "2"}[tab]
        pyautogui.hotkey("ctrl", shortcut)
        time.sleep(config.TIMING.after_enter)
        _log.debug("단축키 탭 전환 시도: Ctrl+%s", shortcut)

    # ── 검색 ─────────────────────────────────────

    def search(self, query: str, *, clear_first: bool = True) -> None:
        """현재 탭(친구 또는 채팅)의 검색창에 query 입력.

        검색창은 `Ctrl+F` 로 열고, 이미 열려있다면 내용만 교체.
        """
        self.focus()
        if clear_first:
            # 기존 내용 전체 선택 후 삭제 (Shift+Home 은 검색창에서만 유효)
            pyautogui.press("end")
            time.sleep(0.03)
            pyautogui.hotkey("shift", "home")
            time.sleep(0.03)
        else:
            pyautogui.hotkey("ctrl", "f")
            time.sleep(config.TIMING.after_enter)

        pyperclip.copy(query)
        pyautogui.hotkey("ctrl", "v")
        time.sleep(config.TIMING.after_search_input)

    def open_search(self) -> None:
        """Ctrl+F로 검색창 열기 (이미 열려있어도 무해)."""
        self.focus()
        pyautogui.hotkey("ctrl", "f")
        time.sleep(config.TIMING.after_enter)

    def close_search(self) -> None:
        pyautogui.press("escape")
        time.sleep(config.TIMING.after_enter)

    # ── 채팅창 열기 / 닫기 ───────────────────────

    def open_friend_chat(self) -> str:
        """검색창에 친구 이름이 입력된 상태에서 1:1 채팅창 오픈.

        Returns:
            열린 채팅창의 제목 (상대 이름 검증에 사용)
        """
        # 1) Enter: 첫 검색 결과 선택 (친구 탭이면 친구 프로필 포커스)
        pyautogui.press("enter")
        time.sleep(config.TIMING.after_enter)
        # 2) Alt+Enter: 1:1 채팅창 열기
        pyautogui.hotkey("alt", "enter")
        time.sleep(config.TIMING.after_alt_enter)

        title = self._get_foreground_window_title()
        _log.debug("채팅창 개방, 포그라운드 제목: %s", title)
        return title

    def close_active_chat(self) -> None:
        pyautogui.hotkey("ctrl", "w")
        time.sleep(config.TIMING.close_chat)

    def _get_foreground_window_title(self) -> str:
        try:
            hwnd = win32gui.GetForegroundWindow()
            return win32gui.GetWindowText(hwnd) or ""
        except Exception as e:
            _log.warning("포그라운드 창 제목 읽기 실패: %s", e)
            return ""

    # ── 전송 ─────────────────────────────────────

    def send_text(self, text: str) -> None:
        if not text:
            return
        pyperclip.copy(text)
        pyautogui.hotkey("ctrl", "v")
        time.sleep(config.TIMING.after_ctrl_v_text)
        pyautogui.press("enter")
        time.sleep(config.TIMING.after_send_text)

    def send_image(self, image_path: Path | str) -> None:
        path = Path(image_path)
        if not path.exists():
            raise KakaoError(f"이미지 파일 없음: {path}")

        _copy_image_to_clipboard(path)
        time.sleep(0.15)
        pyautogui.hotkey("ctrl", "v")
        # 카톡은 이미지 붙여넣기 시 미리보기 다이얼로그를 먼저 띄움
        time.sleep(config.TIMING.after_ctrl_v_image)
        pyautogui.press("enter")
        # 업로드 대기 — 크기 비례
        wait_sec = detection.estimate_image_upload_wait(path)
        _log.debug(
            "이미지 업로드 대기 %.2f초 (%s, %d bytes)", wait_sec, path.name, path.stat().st_size
        )
        time.sleep(wait_sec)


def _copy_image_to_clipboard(path: Path) -> None:
    """이미지 파일을 DIB 포맷으로 Windows 클립보드에 주입.

    원본 코드의 로직을 그대로 계승 (`BMP` 헤더 14바이트 제거 후 `CF_DIB`).
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
