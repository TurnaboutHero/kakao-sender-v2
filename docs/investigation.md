# Phase 2 조사 — 카카오톡 UIA 노출 실측

**조사일**: 2026-04-23
**카카오톡 버전**: 실행 중 (버전 문자열 미확인)
**OS**: Windows 11 Pro 26200
**조사 도구**: `scripts/investigate.py` + `uiautomation==2.0.29`

## 결론 (Executive Summary)

> **카카오톡 PC는 UIA 에 거의 아무것도 노출하지 않는다.**
> 버튼·입력창·리스트·트리뷰 어떤 의미 있는 컨트롤도 없음. 최상위 Window 아래는 전부 빈 `PaneControl` 들의 중첩이거나 내부 해시 ID를 이름으로 쓰는 Pane들뿐.

결과적으로 v2 설계의 **D1 (상태 감지는 UIA 우선)** 전제는 현실에서 성립하지 않는다. UIA로 얻을 수 있는 의미 있는 정보는 **최상위 창 발견** 과 **win32 HWND 추출** 이 사실상 전부.

## 원시 관측

### depth 8 덤프 (카톡 메인 창, 채팅 탭 상태)

```
[WindowControl] Name='카카오톡' (1528,0)-(1920,642)
  [PaneControl] Name='' (1534,545)-(1914,636)
    [PaneControl] Name='' ...
      [PaneControl] Name='' ...
        [PaneControl] Name='' ...
          [PaneControl] Name='' ...
            [PaneControl] Name='MOMENT 광고' ...            ← 사용자 텍스트 유일
              [PaneControl] Name='' ...
              [PaneControl] Name='MOMENT 광고 - 웹 콘텐츠' ...
                [PaneControl] Name='' ...
  [PaneControl] Name='OnlineMainView_0x000105a8' ...         ← 내부 해시
    [PaneControl] Name='ChatRoomListView_0x000105bc' ...
      [PaneControl] Name='ChatRoomListCtrl_0x000105be' ...
        [PaneControl] Name='' ...
  [PaneControl] Name='' ...
```

### 체크리스트 결과

| # | 항목 | 결과 | 비고 |
|---|---|---|---|
| 1 | 친구 탭 버튼 | ❌ | ButtonControl 자체가 존재하지 않음 |
| 2 | 채팅 탭 버튼 | ❌ | 동일 |
| 3 | 친구 목록 트리/리스트 | ❌ | 자체 렌더링으로 추정 |
| 3a | 친구 이름 텍스트 | ❌ | |
| 4 | 상단 검색 EditControl | ❌ | |
| 5 | 채팅창 제목 (win32 GetWindowText) | ⚠️ | 이번 조사 범위 밖 — 별도 필요 |
| 6 | 채팅창이 별도 Window? | ⚠️ | 미확인 |
| 7 | 입력창 EditControl | ❌ | 메인 창 기준 |
| 8 | 전송 상태 말풍선 | ❌ | |
| 9 | 단톡방 참여자 리스트 | ❌ | |
| 10 | 포커스/포그라운드 | ⚠️ | |

## 설계 함의

### 버려지는 것

- `KakaoClient.switch_to_tab` 의 UIA 경로 — 작동하지 않음. 핫키 fallback만 남음.
- `scripts/investigate.py` 가 조사 중 "친구 목록을 UIA 로 긁어오기" 로 매핑 마법사를 자동화할 수 있다는 기대 — 포기.
- 오발송 검증을 메인 창 UIA 로 하기 — 포기. 대신 **열린 채팅창의 `win32gui.GetWindowText`** 가 유일한 검증 수단 (별도 실측 필요).

### 살아남는 것 / 강해지는 것

- `win32gui.FindWindow`, `GetForegroundWindow`, `GetWindowText` — 유일한 상태 창구.
- `detection.verify_target_title` — 여전히 유효 (실측 검증 필요).
- 핫키 기반 입력 (`Ctrl+F`, `Enter`, `Alt+Enter`, `Ctrl+W`, `Ctrl+V`) — 원본과 동일.
- 이미지 업로드 시간 기반 대기 — 다른 선택지 없음.

### 새로 필요한 것

- **채팅창 전용 조사**: 1:1 채팅창을 열었을 때 창 제목·Window 구조·EditControl 노출 여부 실측.
  - 수동 재조사: `uv run python scripts/investigate.py --foreground` 를 카톡 1:1 채팅창이 활성일 때 실행.
- **스크린 기반 상태 감지 검토**: 정 아쉬우면 OCR(tesseract) 로 채팅창 상단 이름을 읽어 대상 검증.

## 코드 반영

- `docs/architecture.md` D1 업데이트: "UIA 우선" → "win32 HWND + 핫키 + UIA는 창 탐지만"
- `engine/kakao.py.switch_to_tab` 의 UIA 경로는 **방어적 재시도** 정도로 유지 (카톡 향후 업데이트 대비) + 로그로 실제 경로 추적

## 재현 명령

```bash
uv run python scripts/investigate.py --depth 4
uv run python scripts/investigate.py --depth 8
uv run python scripts/investigate.py --find 친구    # 역시 0건 기대
uv run python scripts/investigate.py --foreground   # 채팅창 활성 시 재시도 필요
```

원시 로그: `data/phase2-depth4.txt`, `data/phase2-depth8.txt` (repo 에 올리지 않음 — `.gitignore` 로 제외됨).
