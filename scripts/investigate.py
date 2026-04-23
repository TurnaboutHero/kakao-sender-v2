"""카카오톡 창의 UIA 트리를 덤프·조사.

Phase 2 조사 (README 참조) 수행용. 카카오톡 PC 버전을 켜둔 상태에서 실행:

    uv run python scripts/investigate.py                 # 기본: 상단 3 depth 트리
    uv run python scripts/investigate.py --depth 6       # 깊게
    uv run python scripts/investigate.py --find 검색     # Name에 '검색' 포함 컨트롤만
    uv run python scripts/investigate.py --foreground    # 포그라운드 창부터 역추적

출력 예시:
    [Pane] Name="사이드바" Rect=(10,50,90,800)
      [Button] Name="친구" Rect=(10,60,90,100)
      [Button] Name="채팅" Rect=(10,100,90,140)
      ...

결과를 `docs/investigation.md` 에 복사·기록해서 설계 결정 근거로 남긴다.
"""

from __future__ import annotations

import argparse
import sys
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import uiautomation as auto


WINDOW_NAME = "카카오톡"


def dump_tree(
    ctrl: auto.Control, *, max_depth: int, depth: int = 0, filter_name: str | None = None
) -> None:
    if depth > max_depth:
        return
    try:
        name = ctrl.Name or ""
        cls = ctrl.ControlTypeName
        rect = ctrl.BoundingRectangle
        if not filter_name or filter_name.lower() in name.lower():
            indent = "  " * depth
            rect_str = f"({rect.left},{rect.top})-({rect.right},{rect.bottom})" if rect else ""
            print(f"{indent}[{cls}] Name={name!r} {rect_str}")
    except Exception as e:
        print(f"{'  ' * depth}[ERROR] {e}")
        return
    for child in ctrl.GetChildren():
        dump_tree(child, max_depth=max_depth, depth=depth + 1, filter_name=filter_name)


def main() -> int:
    parser = argparse.ArgumentParser(description="카톡 UIA 트리 조사")
    parser.add_argument("--depth", type=int, default=3, help="트리 깊이 (기본 3)")
    parser.add_argument(
        "--find", type=str, default=None, help="Name 필터 (부분일치, 대소문자 무시)"
    )
    parser.add_argument(
        "--foreground",
        action="store_true",
        help="카톡 창 이름 검색 대신 현재 포그라운드 창에서 시작",
    )
    parser.add_argument(
        "--window",
        type=str,
        default=WINDOW_NAME,
        help=f"창 이름 (기본 {WINDOW_NAME!r})",
    )
    args = parser.parse_args()

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
