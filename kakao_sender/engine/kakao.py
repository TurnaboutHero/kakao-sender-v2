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

import ctypes
import pyautogui
import win32api
import win32clipboard
import win32con
import win32gui
import win32process
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
        """메인 창을 포그라운드로. 최소화면 복원 + 포커스 훔김 회피 트릭.

        `SetForegroundWindow` 는 백그라운드 프로세스가 호출하면 Windows 가 소리 없이
        거부한다 (반환만 False). 이를 우회하려면 현재 포그라운드 창의 입력 큐에
        `AttachThreadInput` 으로 일시적으로 붙어야 한다. 사용자가 직접 터미널에서
        돌릴 때는 불필요하지만, GUI/다른 프로세스에서 호출될 때 안정성 상승.

        전환 후 실제로 포그라운드가 바뀌었는지 검증하고, 실패 시 명확한 에러.
        """
        hwnd = self.main_hwnd
        if win32gui.IsIconic(hwnd):
            win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)

        if win32gui.GetForegroundWindow() == hwnd:
            time.sleep(config.TIMING.focus_settle)
            return

        _force_foreground(hwnd)
        time.sleep(config.TIMING.focus_settle)

        if win32gui.GetForegroundWindow() != hwnd:
            raise KakaoFocusError(
                "카톡 메인 창을 포그라운드로 올릴 수 없음. "
                "카톡 창을 한 번 클릭해 foreground 로 올려두고 다시 시도하세요."
            )

    # ── 사이드바 탭 전환 ─────────────────────────

    def require_friend_tab(self) -> None:
        """친구 탭인지 **확인만** 한다. 자동 전환 시도 없음.

        Phase 3 오발송 방지 1차 방어선. 카톡 PC 공식 단축키 목록(2026-04 기준,
        https://cs.kakao.com/helps_html/1073209659)에 사이드바 탭 전환 단축키가 없음.
        따라서 자동 전환을 시도하지 않고 사용자가 발송 전에 직접 친구 탭으로
        전환해 두도록 요구한다. 검증은 `windows.get_active_main_tab` 이 맡음.

        채팅 탭에서 검색하면 단톡방이 결과에 섞여 나오므로 친구 탭 고정 필수.
        """
        current = windows.get_active_main_tab(self.main_hwnd)
        if current == "친구":
            _log.debug("친구 탭 확인 완료")
            return
        raise KakaoTabError(
            f"현재 탭={current!r} — 발송은 '친구' 탭에서만 가능. "
            "카톡 사이드바에서 '친구' 탭 아이콘을 클릭한 뒤 다시 시도해 주세요."
        )

    # 하위 호환을 위한 별칭 (이전 이름을 외부에서 쓰고 있을 수 있음)
    switch_to_friend_tab = require_friend_tab

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

    def search(self, query: str) -> None:
        """친구 탭의 검색창에 query 입력. `require_friend_tab` 선행 필수.

        pyautogui 키보드 경로(End/Shift+Home/Ctrl+V)는 포커스가 예측 불가하게
        이동해 덧붙기·엉뚱한 창 입력 문제가 발생 (실측 확인, 2026-04-24).
        → 검색 Edit HWND 직접 조작 + EM_SETSEL/WM_CLEAR 표준 Edit 메시지로 우회:

        1. Ctrl+F 로 kakao 검색 UI 활성화 (숨김 Edit 을 visible 상태로)
        2. 친구 탭 검색 Edit HWND 찾기 + 현재 내용 로깅 (진단용)
        3. EM_SETSEL(0, -1) 로 전체 선택 + WM_CLEAR 로 선택 삭제 (표준 Edit 의 확실한 경로)
        4. WM_SETTEXT "" 추가 방어적 clear
        5. SetFocus + 클립보드 + Ctrl+V → kakao live search 트리거
        """
        self.focus_main()

        # 1) 검색 UI 활성화 — 필드가 hidden 상태면 visible 로 전환
        pyautogui.hotkey("ctrl", "f")
        time.sleep(config.TIMING.after_enter)

        # 2) 검색 Edit 찾기 + 진단 로그
        search_hwnd = windows.find_friend_tab_search_edit(self.main_hwnd)
        if search_hwnd is None:
            raise KakaoError(
                "친구 탭 검색 Edit 을 찾지 못함 — 친구 탭 상태 확인 필요"
            )
        current = _get_richedit_text(search_hwnd)
        _log.info("search HWND=%s, 기존 내용=%r", hex(search_hwnd), current[:40])

        # 3) 표준 Edit 명령: 전체 선택 후 삭제
        _EM_SETSEL = 0x00B1
        try:
            win32gui.SendMessage(search_hwnd, _EM_SETSEL, 0, -1)
            win32gui.SendMessage(search_hwnd, win32con.WM_CLEAR, 0, 0)
        except Exception as e:
            _log.debug("EM_SETSEL/WM_CLEAR 실패: %s", e)

        # 4) 방어적 WM_SETTEXT ""
        try:
            win32gui.SendMessage(search_hwnd, win32con.WM_SETTEXT, 0, "")
        except Exception as e:
            _log.debug("WM_SETTEXT \"\" 실패: %s", e)

        after_clear = _get_richedit_text(search_hwnd)
        _log.info("search HWND=%s, clear 후=%r", hex(search_hwnd), after_clear[:40])

        # 5) 포커스 + 붙여넣기
        try:
            win32gui.SetFocus(search_hwnd)
        except Exception as e:
            _log.debug("SetFocus 실패: %s", e)

        _set_clipboard_text(query)
        time.sleep(0.05)
        pyautogui.hotkey("ctrl", "v")
        time.sleep(config.TIMING.after_search_input)

        final = _get_richedit_text(search_hwnd)
        _log.info("search HWND=%s, 붙여넣기 후=%r", hex(search_hwnd), final[:40])

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
        """현재 채팅창에 텍스트 전송 + **전송 확인 검증**.

        실측 결과 (2026-04-24): `WM_SETTEXT` 로 텍스트를 주입하면 카톡의
        반자동화 감지가 작동해 **전송 버튼이 비활성 상태로 유지됨** (Enter 도 무시됨).
        수동 타이핑·클립보드 붙여넣기 같은 "실제 입력 이벤트" 만이 전송 버튼을 활성화.

        따라서 텍스트도 이미지와 같은 **클립보드 + Ctrl+V** 경로로 통일한다.
        단 RichEdit HWND 에 `SetFocus` 로 수신 대상 명시.

        전송 후 RichEdit 가 비었는지 검증해 false positive (전송 실패를 성공으로
        보고) 를 방지.
        """
        if not text:
            return
        chat = self._require_current_chat()
        if chat.richedit_hwnd is None:
            raise KakaoError("입력창(RichEdit) HWND 없음 — open_friend_chat 먼저 호출")

        # 1) 채팅창 포그라운드 + RichEdit 포커스 (Ctrl+V 수신 대상 명확화)
        _force_foreground(chat.hwnd)
        time.sleep(config.TIMING.focus_settle)
        try:
            win32gui.SendMessage(chat.richedit_hwnd, win32con.WM_SETFOCUS, 0, 0)
        except Exception:
            pass

        # 2) 클립보드에 텍스트 넣기
        _set_clipboard_text(text)
        time.sleep(0.05)

        # 3) Ctrl+V → 입력 이벤트로 감지됨 → 전송 버튼 활성화
        pyautogui.hotkey("ctrl", "v")
        time.sleep(config.TIMING.after_ctrl_v_text)

        # 4) Enter 로 전송
        pyautogui.press("enter")
        time.sleep(config.TIMING.after_send_text)

        # 5) 전송 확인 — 우리가 보낸 텍스트가 RichEdit 에 남아있는지.
        # "비었는지" 로 판정하면 카톡이 빈 입력창에 보여주는 placeholder
        # ("메시지 입력") 를 내용으로 오판함. 보낸 텍스트 자체가 남아있는지만 본다.
        remaining = _get_richedit_text(chat.richedit_hwnd)
        sent = text.strip()
        if sent and sent in remaining:
            raise KakaoError(
                f"텍스트 전송 실패 — 보낸 내용이 아직 입력창에 남아있음: {remaining[:80]!r}. "
                "카톡 반자동화 감지 또는 '엔터 키로 메시지 전송' 설정 확인 필요."
            )

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


def _force_foreground(hwnd: int) -> None:
    """AttachThreadInput 트릭으로 SetForegroundWindow 의 background-process 제약 우회.

    Windows 는 백그라운드 프로세스가 포커스를 훔치는 것을 막는다 (ALTTAB_DENIED).
    현재 포그라운드 스레드에 일시적으로 붙으면 같은 입력 큐에서 요청하는 셈이라 허용됨.
    """
    foreground = win32gui.GetForegroundWindow()
    current_thread = ctypes.windll.kernel32.GetCurrentThreadId()
    fg_thread = 0
    if foreground:
        fg_thread, _ = win32process.GetWindowThreadProcessId(foreground)

    attached = False
    if fg_thread and fg_thread != current_thread:
        try:
            win32process.AttachThreadInput(current_thread, fg_thread, True)
            attached = True
        except Exception as e:
            _log.debug("AttachThreadInput 실패 (계속 진행): %s", e)

    try:
        try:
            win32gui.BringWindowToTop(hwnd)
        except Exception:
            pass
        try:
            win32gui.SetForegroundWindow(hwnd)
        except Exception as e:
            raise KakaoFocusError(f"SetForegroundWindow 실패: {e}") from e
    finally:
        if attached:
            try:
                win32process.AttachThreadInput(current_thread, fg_thread, False)
            except Exception:
                pass


def _post_key(hwnd: int, vk: int) -> None:
    """특정 HWND 에 KEYDOWN/KEYUP 쌍을 Post. 캐릭터 키가 아닌 제어 키용."""
    win32api.PostMessage(hwnd, win32con.WM_KEYDOWN, vk, 0)
    time.sleep(0.02)
    win32api.PostMessage(hwnd, win32con.WM_KEYUP, vk, 0)


def _get_richedit_text(hwnd: int) -> str:
    """RichEdit 컨트롤의 현재 텍스트. 전송 성공 검증에 사용.

    WM_GETTEXTLENGTH 로 길이 받고 WM_GETTEXT 로 내용 읽음. ctypes 기반.
    """
    try:
        length = ctypes.windll.user32.SendMessageW(hwnd, win32con.WM_GETTEXTLENGTH, 0, 0)
        if length <= 0:
            return ""
        buf = ctypes.create_unicode_buffer(length + 1)
        ctypes.windll.user32.SendMessageW(hwnd, win32con.WM_GETTEXT, length + 1, buf)
        return buf.value
    except Exception as e:
        _log.debug("_get_richedit_text 실패 hwnd=%s: %s", hex(hwnd), e)
        return ""


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
