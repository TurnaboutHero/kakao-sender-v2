"""카카오톡 창의 UIA·win32 트리를 덤프·조사.

Phase 2 (UIA 중심) 와 Phase 3 (win32 HWND 중심) 양쪽 조사 경로 모두 제공.

사용 예:
    uv run python scripts/investigate.py                 # 기본: 메인 창 UIA 3 depth
    uv run python scripts/investigate.py --depth 6       # 깊이
    uv run python scripts/investigate.py --find 검색     # Name 부분 매칭 필터
    uv run python scripts/investigate.py --foreground    # 포그라운드 창부터
    uv run python scripts/investigate.py --win32-tree    # 메인 창 win32 자식 재귀
    uv run python scripts/investigate.py --chats         # 모든 EVA_Window_Dblclk 채팅창 + 자식
    uv run python scripts/investigate.py --tab-state     # 메인 창 활성 탭 판정
    uv run python scripts/investigate.py --panel         # 단톡방 참여자 패널 (패널 열린 상태에서)

결과를 `docs/investigation.md` 에 반영해 설계 결정 근거로 남긴다.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import TYPE_CHECKING

# `uv run python scripts/investigate.py` 로 직접 실행 시 kakao_sender 패키지를 찾도록
# 프로젝트 루트를 sys.path 에 추가.
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

if TYPE_CHECKING:
    import uiautomation as auto


WINDOW_NAME = "카카오톡"
CHAT_WINDOW_CLASS = "EVA_Window_Dblclk"


# ── UIA 경로 ────────────────────────────────────────


def dump_tree(
    ctrl: auto.Control,
    *,
    max_depth: int,
    depth: int = 0,
    filter_name: str | None = None,
) -> None:
    if depth > max_depth:
        return
    try:
        name = ctrl.Name or ""
        cls = ctrl.ControlTypeName
        rect = ctrl.BoundingRectangle
        aid = ctrl.AutomationId or ""
        if not filter_name or filter_name.lower() in name.lower():
            indent = "  " * depth
            rect_str = f"({rect.left},{rect.top})-({rect.right},{rect.bottom})" if rect else ""
            id_str = f" id={aid!r}" if aid else ""
            print(f"{indent}[{cls}] Name={name!r}{id_str} {rect_str}")
    except Exception as e:
        print(f"{'  ' * depth}[ERROR] {e}")
        return
    for child in ctrl.GetChildren():
        dump_tree(child, max_depth=max_depth, depth=depth + 1, filter_name=filter_name)


# ── win32 경로 (Phase 3) ────────────────────────────


def dump_win32_tree(hwnd: int, *, depth: int = 0, max_depth: int = 10) -> None:
    """HWND 자식 재귀. 빈 이름/숨김도 전부 표시."""
    import win32gui

    if depth > max_depth:
        return
    try:
        cls = win32gui.GetClassName(hwnd)
        txt = win32gui.GetWindowText(hwnd)
        rect = win32gui.GetWindowRect(hwnd)
        vis = win32gui.IsWindowVisible(hwnd)
        indent = "  " * depth
        print(f"{indent}{hex(hwnd)} [{cls}] vis={vis} {txt!r} rect={rect}")
    except Exception as e:
        print(f"{'  ' * depth}[ERROR] {e}")
        return
    try:
        win32gui.EnumChildWindows(
            hwnd, lambda ch, _: dump_win32_tree(ch, depth=depth + 1, max_depth=max_depth), None
        )
    except Exception:
        pass


def list_eva_windows() -> list[tuple[int, str, str, tuple[int, int, int, int]]]:
    """모든 visible top-level EVA_Window_Dblclk 창."""
    import win32gui

    rows: list[tuple[int, str, str, tuple[int, int, int, int]]] = []

    def cb(hwnd: int, _lparam: object) -> None:
        if not win32gui.IsWindowVisible(hwnd):
            return
        cls = win32gui.GetClassName(hwnd)
        if cls != CHAT_WINDOW_CLASS:
            return
        rows.append((hwnd, cls, win32gui.GetWindowText(hwnd), win32gui.GetWindowRect(hwnd)))

    win32gui.EnumWindows(cb, None)
    return rows


def cmd_chats(max_depth: int) -> int:
    """모든 EVA_Window_Dblclk 창 목록 + 각 창의 win32 자식 트리."""
    rows = list_eva_windows()
    print(f"# EVA_Window_Dblclk top-level 창 {len(rows)}개")
    for hwnd, cls, title, rect in rows:
        print(f"  {hex(hwnd)} {title!r:40} rect={rect}")

    for hwnd, _cls, title, _rect in rows:
        print()
        print(f"# --- {title!r} ({hex(hwnd)}) 자식 트리 ---")
        dump_win32_tree(hwnd, max_depth=max_depth)
    return 0


def cmd_win32_tree(window_name: str, max_depth: int) -> int:
    """지정 창 (기본: 메인 창) 의 win32 자식 재귀 덤프."""
    import win32gui

    hwnd = win32gui.FindWindow(None, window_name)
    if not hwnd:
        print(f"창을 찾지 못함: {window_name!r}", file=sys.stderr)
        return 2
    dump_win32_tree(hwnd, max_depth=max_depth)
    return 0


def cmd_tab_state(window_name: str) -> int:
    """메인 창의 활성 탭 판정 — engine.windows 의 실전 로직 직접 호출."""
    import win32gui

    from kakao_sender.engine import windows as kw

    hwnd = win32gui.FindWindow(None, window_name)
    if not hwnd:
        print(f"창을 찾지 못함: {window_name!r}", file=sys.stderr)
        return 2

    active = kw.get_active_main_tab(hwnd)
    print(f"# 메인 창: {hex(hwnd)} {window_name!r}")
    print(f"# 활성 탭: {active!r}")
    print("#")
    print("# 근거 — 메인 창 직접 자식 (IsWindowVisible 가 유일한 활성 판별 기준):")
    from kakao_sender.config import UIA_NAMES

    prefixes = (
        UIA_NAMES.view_prefix_lock,
        UIA_NAMES.view_prefix_more,
        UIA_NAMES.view_prefix_friends,
        UIA_NAMES.view_prefix_chats,
    )

    def cb(child: int, _lp: object) -> None:
        cls = win32gui.GetClassName(child)
        txt = win32gui.GetWindowText(child)
        rect = win32gui.GetWindowRect(child)
        vis = win32gui.IsWindowVisible(child)
        if any(txt.startswith(p) for p in prefixes):
            w = rect[2] - rect[0]
            h = rect[3] - rect[1]
            active_mark = " ← 활성" if vis else ""
            print(f"  {hex(child)} [{cls}] vis={vis} {txt!r} rect={rect} ({w}x{h}){active_mark}")

    win32gui.EnumChildWindows(hwnd, cb, None)
    return 0


def cmd_panel() -> int:
    """단톡방 참여자 패널 조사. 사전 조건: 참여자 패널이 열린 상태.

    휴리스틱: top-level `EVA_Window_Dblclk` 중 제목이 비어있고 크기가 중간 (160~600 폭,
    200~900 높이) 인 창을 후보로 보고 자식 트리·UIA 트리를 모두 출력.
    """
    candidates: list[tuple[int, str, tuple[int, int, int, int]]] = []
    for hwnd, _cls, title, rect in list_eva_windows():
        if title:
            continue
        w = rect[2] - rect[0]
        h = rect[3] - rect[1]
        if 160 <= w <= 600 and 200 <= h <= 900:
            candidates.append((hwnd, title, rect))

    if not candidates:
        print("후보 패널을 찾지 못함 — 참여자 패널이 열려 있는지 확인.", file=sys.stderr)
        return 3

    import uiautomation as auto

    for hwnd, _title, rect in candidates:
        print(f"# --- 참여자 패널 후보 {hex(hwnd)} rect={rect} ---")
        dump_win32_tree(hwnd, max_depth=4)
        print("# UIA depth 10")
        ctrl = auto.ControlFromHandle(hwnd)
        if ctrl:
            dump_tree(ctrl, max_depth=10)
        print()
    return 0


# ── 엔트리 ──────────────────────────────────────────


def main() -> int:
    # Windows 기본 cp949 출력으로 한글 깨짐 방지
    if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
        try:
            sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
            sys.stderr.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
        except Exception:
            pass

    parser = argparse.ArgumentParser(description="카톡 UIA/win32 창 조사")
    parser.add_argument("--depth", type=int, default=3, help="트리 깊이 (기본 3)")
    parser.add_argument(
        "--find", type=str, default=None, help="UIA Name 필터 (부분일치, 대소문자 무시)"
    )
    parser.add_argument(
        "--foreground",
        action="store_true",
        help="카톡 창 이름 검색 대신 현재 포그라운드 창에서 시작 (UIA)",
    )
    parser.add_argument(
        "--window",
        type=str,
        default=WINDOW_NAME,
        help=f"창 이름 (기본 {WINDOW_NAME!r})",
    )

    # Phase 3 옵션 (상호 배타)
    exclusive = parser.add_mutually_exclusive_group()
    exclusive.add_argument(
        "--win32-tree",
        action="store_true",
        help="메인 창 win32 자식 재귀 (UIA 대비)",
    )
    exclusive.add_argument(
        "--chats",
        action="store_true",
        help="모든 EVA_Window_Dblclk 창 + 각 창 자식 트리",
    )
    exclusive.add_argument(
        "--tab-state",
        action="store_true",
        help="메인 창의 활성 탭 판정 (친구/채팅/더보기/잠금)",
    )
    exclusive.add_argument(
        "--panel",
        action="store_true",
        help="단톡방 참여자 패널 조사 (패널 열린 상태에서 실행)",
    )
    args = parser.parse_args()

    # Phase 3 옵션 분기
    if args.win32_tree:
        return cmd_win32_tree(args.window, args.depth)
    if args.chats:
        return cmd_chats(args.depth)
    if args.tab_state:
        return cmd_tab_state(args.window)
    if args.panel:
        return cmd_panel()

    # Phase 2 기본 (UIA 트리)
    try:
        import uiautomation as auto
    except ImportError:
        print("uiautomation 모듈이 없습니다. `uv sync` 후 다시 실행하세요.", file=sys.stderr)
        return 1

    if args.foreground:
        ctrl = auto.GetForegroundControl()
        print(f"# 포그라운드: {ctrl.ControlTypeName} Name={ctrl.Name!r}")
    else:
        root = auto.GetRootControl()
        ctrl = root.WindowControl(Name=args.window, searchDepth=3)
        if not ctrl.Exists(maxSearchSeconds=2.0):
            print(f"창을 찾지 못함: {args.window!r}", file=sys.stderr)
            print("카카오톡 PC를 실행하고 로그인 상태인지 확인하세요.", file=sys.stderr)
            return 2
        print(f"# 창: {ctrl.Name!r}")

    print(f"# depth <= {args.depth}, filter={args.find!r}")
    print("-" * 70)
    dump_tree(ctrl, max_depth=args.depth, filter_name=args.find)
    return 0


if __name__ == "__main__":
    sys.exit(main())
