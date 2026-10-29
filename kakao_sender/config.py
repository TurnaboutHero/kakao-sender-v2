"""타이밍·경로·기본 상수 한 곳 모음.

원본 코드가 `time.sleep(0.1)`, 픽셀 좌표 등을 함수 본문에 흩뿌려놔서 튜닝이 어려웠음.
상수는 전부 여기로 모으고 필요 시 환경변수로 override.

모든 시간 단위는 초(seconds). 모든 경로는 `pathlib.Path`.
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass, field
from pathlib import Path


def _base_dir() -> Path:
    """exe 빌드 시 exe 옆 폴더, 소스 실행 시 프로젝트 루트.

    원본 코드의 `sys.frozen` 분기 로직을 계승하되 `sys` import를 확실히 한다.
    """
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent


BASE_DIR: Path = _base_dir()
DATA_DIR: Path = BASE_DIR / "data"
LOGS_DIR: Path = BASE_DIR / "logs"

ROSTER_CSV: Path = DATA_DIR / "members.csv"
MAPPING_JSON: Path = DATA_DIR / "mapping.json"


@dataclass(frozen=True, slots=True)
class Timing:
    """발송 파이프라인의 모든 지연값."""

    # 개별 조작 단위 지연
    focus_settle: float = 0.3
    after_search_input: float = 0.25
    after_enter: float = 0.15
    after_alt_enter: float = 0.3
    after_ctrl_v_text: float = 0.08
    after_ctrl_v_image: float = 0.4
    after_send_text: float = 0.6
    close_chat: float = 0.15
    wait_paste_confirm: float = 1.0  # 붙여넣기가 입력창에 반영될 때까지 폴링 상한
    wait_send_confirm: float = 1.5  # Enter 후 입력창이 비워질 때까지 폴링 상한

    # 조건 폴링 상한
    wait_chat_ready_timeout: float = 3.0
    wait_image_upload_extra: float = 1.5
    wait_image_per_mb: float = 2.5  # 1MB 당 대기
    wait_search_result_timeout: float = 1.5

    # Phase 3 신설 — HWND 스냅샷 diff / 탭 전환 검증
    wait_new_chat_window: float = 2.0
    wait_tab_switch: float = 1.0
    poll_interval: float = 0.05

    # 배치 템포 (스팸 회피)
    between_send_min: float = 5.0
    between_send_max: float = 8.0
    rest_every_n: int = 15
    rest_duration: float = 45.0


@dataclass(frozen=True, slots=True)
class HwndClasses:
    """win32 클래스명 — Phase 3 실측 (docs/investigation.md).

    카톡 top-level 창은 모두 같은 클래스 `EVA_Window_Dblclk` 를 쓰므로
    제목 + 자식 HWND 패턴으로 역할을 구분한다.
    """

    chat_window: str = "EVA_Window_Dblclk"
    richedit: str = "RICHEDIT50W"
    list_control: str = "EVA_VH_ListControl_Dblclk"
    edit_standard: str = "Edit"


@dataclass(frozen=True, slots=True)
class UIANames:
    """카톡 창에서 찾을 컨트롤 이름·속성.

    Phase 2/3 실측(`scripts/investigate.py`) 결과 반영.
    """

    window_title: str = "카카오톡"
    friends_tab_name: str = "친구"
    chats_tab_name: str = "채팅"
    search_edit_placeholders: tuple[str, ...] = ("검색", "이름, 전화번호", "친구 검색")
    # 빈 채팅 입력창(RICHEDIT50W)이 WM_GETTEXT 로 돌려주는 placeholder (Phase 3-2 ④ 실측)
    input_placeholder: str = "메시지 입력"

    # 메인 창 내부 뷰 이름 prefix (win32 GetWindowText startswith 매칭용).
    # 활성 여부는 GetWindowRect 의 너비·높이가 0 이 아닌지로 판정한다.
    view_prefix_friends: str = "ContactListView"
    view_prefix_chats: str = "ChatRoomListView"
    view_prefix_more: str = "MoreView"
    view_prefix_lock: str = "LockModeView"


@dataclass(frozen=True, slots=True)
class MessageVariants:
    """문구 변주 — 동일 텍스트 반복 패턴을 카톡 스팸 필터가 인식하는 걸 완화."""

    birthday_default: tuple[str, ...] = (
        "생일 축하해요 🎂",
        "생일 축하합니다 🎂",
        "🎂 생일 축하해 🎉",
        "오늘 생일 축하해요 🎂🎉",
    )


TIMING = Timing()
UIA_NAMES = UIANames()
HWND_CLASSES = HwndClasses()
MESSAGES = MessageVariants()


def ensure_dirs() -> None:
    """런타임 디렉토리가 존재하는지 보장."""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    LOGS_DIR.mkdir(parents=True, exist_ok=True)


def override_from_env() -> dict[str, str]:
    """`KAKAO_SENDER_*` 환경변수로 경로 override. 발견된 키만 반환."""
    overrides: dict[str, str] = {}
    for name in ("DATA_DIR", "LOGS_DIR"):
        key = f"KAKAO_SENDER_{name}"
        if key in os.environ:
            overrides[name] = os.environ[key]
    return overrides


@dataclass(frozen=True)
class _Frozen:
    """외부에서 상수 튜닝이 필요할 때 쓰는 스냅샷 헬퍼."""

    timing: Timing = field(default_factory=Timing)
    ui: UIANames = field(default_factory=UIANames)
    msg: MessageVariants = field(default_factory=MessageVariants)


DEFAULTS = _Frozen()
