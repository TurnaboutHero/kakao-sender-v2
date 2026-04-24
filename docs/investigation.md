# Phase 2 조사 — 카카오톡 UIA 노출 실측

**조사일**: 2026-04-23 (Phase 2), 2026-04-24 (Phase 3 재조사)
**카카오톡 버전**: 실행 중 (버전 문자열 미확인)
**OS**: Windows 11 Pro 26200
**조사 도구**: `scripts/investigate.py` + `uiautomation==2.0.29` + `pywin32`

## 최종 결론 (Executive Summary, Phase 3 기준)

Phase 2의 "UIA에 아무것도 없다" 결론은 **절반만 맞았다**. Phase 3 재조사에서 **win32 `EnumChildWindows` 경로**를 파보니 그림이 완전히 달라졌다.

> **메인 창 UIA는 여전히 장님이다. 하지만 채팅창(1:1·단톡) 과 참여자 패널은 별도 top-level HWND이며, 그 내부에 `RICHEDIT50W`, `Edit`, `EVA_VH_ListControl_Dblclk` 같은 표준/준표준 컨트롤을 **HWND로 노출**한다.**
>
> 또한 메인 창도 UIA로는 비어있지만 `EnumChildWindows` 로 훑으면 `ContactListView_*`, `ChatRoomListView_*`, `MoreView_*`, `LockModeView_*` 등 의미 있는 뷰 이름과 활성 여부 (`IsWindowVisible`) 를 얻을 수 있다.

결과적으로 **D1 재수정**: "UIA 우선" → Phase 2 때 "UIA는 창 탐지만" → **Phase 3 "win32 HWND + EnumChildWindows 주력, UIA는 보조"**.

Phase 2 원문은 아래 섹션 [`Phase 2 조사 (원문)`](#phase-2-조사-원문) 에 보존한다.

---

## Phase 3 재조사 (2026-04-24)

### 계기

Phase 2 에서 "채팅창 전용 조사 필요 — 수동 재조사" 로 남겨뒀던 TODO 를 실제로 수행. 1:1 채팅창·단톡방·참여자 패널이 열린 상태에서 `EnumWindows`, `EnumChildWindows`, UIA 트리 덤프를 병렬 실행.

### 전체 HWND 지형

모든 카톡 top-level 창은 클래스 `EVA_Window_Dblclk` 로 동일. **제목과 내부 구조로만 역할 구분 가능.**

| HWND (예시) | 제목 | 역할 | 내부 구조 요약 |
|---|---|---|---|
| `0x10610` | `카카오톡` | 메인 창 | 메인 뷰들 (ContactListView, ChatRoomListView, MoreView, LockModeView 등) + 광고 자식 창 |
| `0x6b0ed4` | `회원가` | 1:1 채팅창 | RichEdit + 메시지 ListControl |
| `0xa00dd4` | `샘플 광주지구 전체톡방` | 단톡방 | RichEdit + 메시지 ListControl (1:1 과 동일) |
| `0xac0e2a` | (공백) | 단톡방 참여자 패널 | 참여자 ListControl + 검색 Edit |
| `0x10a82` | (공백) | MOMENT 광고 팝업 | Chrome_WidgetWin_* 자식 뭉치 |

**오발송 관점 경고**: 1:1 채팅창과 단톡방이 **클래스·자식 패턴·제목 형식까지 구별 불가**. 제목만 보면 "샘플 광주지구 전체톡방" 같이 긴 단톡방명과 "회원가" 같은 개인톡이 섞여 있어, **창이 열린 뒤 제목만으로 단톡방인지 아닌지 알 수 없다.**

### 채팅창 내부 (1:1·단톡방 공통)

```
0x6b0ed4 [EVA_Window_Dblclk] '회원가' rect=(1150,0)-(1530,640)
├─ 0x880ec4 [RICHEDIT50W]                     ← 입력창 ⭐ WM_SETTEXT 가능
├─ 0x2f0f8a [EVA_VH_ListControl_Dblclk]       ← 메시지 리스트 (자체 렌더링)
│  └─ 0x810980 [_EVA_CustomScrollCtrl]        ← 스크롤바
└─ 0x860c40 [Edit]                            ← rect 0, 역할 미상
```

UIA 관점 동일 창:
```
[PaneControl] name='회원가' id=''
├─ [DocumentControl] name='RichEdit Control' id='1006'   ← 입력창 UIA
└─ [PaneControl] id='100'                                ← 메시지 리스트
   └─ [PaneControl] id='202'                             ← 스크롤바
```

- 입력창은 **표준 Microsoft RichEdit** → `SendMessage(WM_SETTEXT)` / `PostMessage(WM_KEYDOWN, VK_RETURN)` 으로 직접 조작 가능. 클립보드·pyautogui 불필요.
- 메시지 리스트는 카톡 자체 렌더링. UIA·MSAA·win32 모두 내부 텍스트 미노출. 발신 메시지 검증은 불가.

### 메인 창 내부 구조 (win32 `EnumChildWindows` 로만 보임)

UIA 트리는 빈 Pane + 해시 이름만 주지만, win32 경로는 의미 있는 이름을 준다:

```
0x10610 [EVA_Window_Dblclk] '카카오톡'
├─ 0x10612 [EVA_ChildWindow] 'OnlineMainView_0x00010612'
│  ├─ 0x10614 [EVA_Window] 'ContactListView_0x00010614'   ← 친구 탭
│  │  ├─ 0x10616 [EVA_VH_ListControl_Dblclk] 'SearchListCtrl_...'
│  │  ├─ 0x1061c [EVA_VH_ListControl_Dblclk] 'ContactListCtrl_...'
│  │  └─ 0x10624 [Edit] (친구 탭 검색창, 탭 비활성 시 vis=0)
│  ├─ 0x10626 [EVA_Window] 'ChatRoomListView_0x00010626'  ← 채팅 탭
│  │  ├─ 0x10628 [EVA_VH_ListControl_Dblclk] 'ChatRoomListCtrl_...'
│  │  ├─ 0x1062e [EVA_VH_ListControl_Dblclk] 'SearchListCtrl_...'
│  │  └─ 0x1063c [Edit] (채팅 탭 검색창)
│  ├─ 0x1063e [EVA_Window] 'MoreView_0x0001063e' (비활성 시 rect 0)
│  └─ 0x10646 [EVA_ChildWindow_Dblclk] 'LockModeView_0x00010646' (비활성 시 rect 0)
│     └─ 0x1064e~0x10654 [Edit] ×4                         ← 잠금모드 PIN 4자리
```

**핵심 패턴 (라이브 재확인, 2026-04-24)**:

- `ContactListView_*`, `ChatRoomListView_*` — **둘 다 항상 rect 가 정상 크기** (탭 활성/비활성 무관). 이 두 뷰는 z-order 로만 전환되고 HWND 자체는 유지됨.
- `MoreView_*`, `LockModeView_*` — 비활성 시 rect 가 0 으로 쭈그러드는 동시에 `IsWindowVisible` 도 0.
- 따라서 **활성 탭 판별의 유일한 안정 기준은 `IsWindowVisible`** (rect 아님).
- 각 뷰의 자식 `Edit` 검색창도 동일한 패턴: 활성 탭의 Edit 만 `rect` 가 정상, 다만 이것도 `IsWindowVisible` 로 판별하는 편이 일관성 있음.

→ `get_active_main_tab(main_hwnd)` 는 **`IsWindowVisible` 우선순위** 기반:
- `LockModeView_*` 가 visible → **잠금**
- `MoreView_*` 가 visible → **더보기**
- `ContactListView_*` 가 visible → **친구**
- `ChatRoomListView_*` 가 visible → **채팅**
- (잠금/더보기가 켜져 있으면 friend/chat 뷰도 동시에 visible=1 일 수 있으므로 우선순위 순서 중요)

### 단톡방 참여자 패널

단톡방 우상단 `≡` 메뉴 클릭 시 **별도 top-level `EVA_Window_Dblclk`** 로 열림 (단톡방의 win32 자식이 아님).

```
0xac0e2a [EVA_Window_Dblclk] '' rect=(1098,89)-(1368,464)
├─ 0xc100a  [EVA_VH_ListControl_Dblclk]         ← 참여자 리스트 (자체 렌더링)
│  └─ 0x381090 [_EVA_CustomScrollCtrl]          ← 스크롤바
├─ 0x2608ee [EVA_VH_ListControl_Dblclk] (vis=0) ← 검색 결과 리스트 (미입력 시 숨김)
└─ 0x58096a [Edit] rect=(1153,109)-(1339,132)   ← 참여자 검색창 ⭐
```

UIA 는 단톡방 창의 **UIA 자식**으로 이 패널을 보여줌 (win32 부모-자식 관계 없음에도 불구하고):
```
[PaneControl] name='샘플 광주지구 전체톡방' id=''
├─ [PaneControl] id=''                                        ← 참여자 패널
│  ├─ [PaneControl] id='1003'                                 ← 참여자 리스트
│  │  └─ [PaneControl] id='202'                               ← 스크롤바
│  └─ [EditControl] id='100'                                  ← 참여자 검색창
├─ [DocumentControl] name='RichEdit Control' id='1006'       ← 원래 입력창
└─ [PaneControl] id='100'                                     ← 메시지 리스트
```

**참여자 이름 텍스트는 노출되지 않음.** UIA depth 20 + `LegacyIAccessiblePattern` 까지 파도 빈 Pane 만 나옴. 메시지 리스트와 동일하게 카톡 자체 렌더링.

### 단톡방 참여자 리스트 추출 — 가능성 정리

| 방법 | 가능성 | 비고 |
|---|---|---|
| UIA/MSAA 로 이름 긁기 | ❌ 불가 | Phase 3 실측 |
| **검색창에 이름 주입 → 존재 확인** | ⭕ 가능 | 161명 루프 필요, 완전 리스트 대신 "누가 단톡방에 있는지" 검증 |
| OCR (`PrintWindow` + tesseract) | △ 가능하지만 고비용 | 리스트 스크롤 필요, 이모지 포함 저장명 정확도 낮음 |
| 메시지 로그에서 발화자 수집 | ❌ 부분만 | 조용한 회원은 영원히 못 잡음 |

→ **실용 해법**: 완전 리스트 뽑기 포기, "내 명단 N명이 이 단톡방에 있는지" 로 문제를 뒤집는다.

### 친구 탭 검색 vs 채팅 탭 검색

- 친구 탭 검색창 (`0x10624`): 결과로 **친구(개인)만** 나옴
- 채팅 탭 검색창 (`0x1063c`): 결과에 **1:1 대화방 + 단톡방 + 친구** 섞여 나옴

원본 v5는 채팅 탭 / 친구 탭 어느 쪽인지 검증하지 않고 검색 → `Enter` → 첫 결과 맹목 선택. **단톡방이 첫 결과로 올라오면 단톡방 오발송**. 이 경로가 실제로 터졌는지는 불명이나 구조상 가능.

→ v2 필수 방어: `switch_to_friend_tab()` 후 **`IsWindowVisible` 로 성공 검증** → 실패 시 즉시 예외. 배치 시작 전·각 건 앞 모두 강제.

### 오발송 방지 다층 방어 (최종 설계)

```
send_one(job):
  1. main_hwnd 확인 (EnumWindows)
  2. switch_to_friend_tab() + IsWindowVisible 검증 ← 1차: 단톡방 결과 제거
  3. existing_chats = snapshot_chat_hwnds()      ← 스냅샷
  4. search(name) + Enter + Alt+Enter
  5. new_chat = wait_for_new_hwnd(existing, 2s)  ← 2차: HWND 추적
     not new_chat → fail, skip
  6. if new_chat.title.strip() != expected:      ← 3차: 완전일치
         PostMessage(new_chat.hwnd, WM_CLOSE)    ← HWND 직접 지정해 닫기
         fail
  7. SendMessage(richedit, WM_SETTEXT, text)     ← 포커스 독립
  8. PostMessage(richedit, WM_KEYDOWN, VK_RETURN)
  9. PostMessage(new_chat.hwnd, WM_CLOSE)
```

### 코드 반영 체크리스트 (Phase 3)

- [ ] `config.HwndClasses` 추가 (`EVA_Window_Dblclk`, `RICHEDIT50W`, `EVA_VH_ListControl_Dblclk`)
- [ ] `engine/windows.py` 신설: `find_main_window`, `enumerate_chat_windows`, `snapshot_chat_hwnds`, `wait_for_new_chat_window`, `find_richedit_child`, `get_active_main_tab`
- [ ] `engine/kakao.py`:
  - `switch_to_friend_tab()` + `IsWindowVisible` 검증 루프
  - `open_friend_chat(expected_name)` HWND 스냅샷 diff + 완전일치 + 즉시 WM_CLOSE
  - `send_text()` WM_SETTEXT + PostMessage VK_RETURN
  - `close_current_chat()` HWND 직접 지정
  - `_get_foreground_window_title` 제거
  - UIA 경로 축소 (창 제목으로 발견, 그 외 win32)
- [ ] `engine/detection.verify_target_title`: 부분일치 → **완전일치** (`a == e`)
  - 기존 "부분일치 성공" 테스트 케이스는 **실패 기대값**으로 교체
- [ ] `engine/send.send_one`: `switch_to_friend_tab` 선행, `open_friend_chat` 반환 HWND 사용
- [ ] `scripts/investigate.py` 옵션 추가: `--win32-tree`, `--tab-state`, `--chats`, `--panel`
- [ ] `docs/architecture.md` D1 / D2 / D8(새) / D9(새) 업데이트

### 재현 명령 (Phase 3)

```bash
# 1:1 채팅창 하나 + 단톡방 하나 + 참여자 패널 열어둔 상태에서:
python scripts/investigate.py --chats      # EVA_Window_Dblclk 전수 + 자식 트리
python scripts/investigate.py --tab-state  # 현재 활성 탭 (친구/채팅/잠금)
python scripts/investigate.py --win32-tree # 메인 창 win32 자식 (UIA 대비)
python scripts/investigate.py --panel      # 참여자 패널 구조
```

---

## Phase 2 조사 (원문)

아래는 2026-04-23 최초 조사 시점 기록. Phase 3 에서 "UIA 외에도 win32 경로가 있다"는 관점이 추가됐지만, 당시 관측 자체는 유효하다 (메인 창 UIA 가 빈약하다는 것).

### 원시 관측 (Phase 2)

#### depth 8 덤프 (카톡 메인 창, 채팅 탭 상태)

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

#### 체크리스트 결과 (Phase 2)

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

### 설계 함의 (Phase 2 시점)

> 아래 소결론들은 Phase 3 에서 부분적으로 뒤집혔다. 위 [Phase 3 재조사](#phase-3-재조사-2026-04-24) 참조.

#### 버려지는 것

- `KakaoClient.switch_to_tab` 의 UIA 경로 — 작동하지 않음. 핫키 fallback만 남음.
- `scripts/investigate.py` 가 조사 중 "친구 목록을 UIA 로 긁어오기" 로 매핑 마법사를 자동화할 수 있다는 기대 — 포기.
- 오발송 검증을 메인 창 UIA 로 하기 — 포기. 대신 **열린 채팅창의 `win32gui.GetWindowText`** 가 유일한 검증 수단 (별도 실측 필요).

#### 살아남는 것 / 강해지는 것

- `win32gui.FindWindow`, `GetForegroundWindow`, `GetWindowText` — 유일한 상태 창구.
- `detection.verify_target_title` — 여전히 유효 (실측 검증 필요).
- 핫키 기반 입력 (`Ctrl+F`, `Enter`, `Alt+Enter`, `Ctrl+W`, `Ctrl+V`) — 원본과 동일.
- 이미지 업로드 시간 기반 대기 — 다른 선택지 없음.

#### 새로 필요한 것 (Phase 2 TODO, Phase 3 에서 해소)

- **채팅창 전용 조사**: 1:1 채팅창을 열었을 때 창 제목·Window 구조·EditControl 노출 여부 실측. ✅ Phase 3 완료.
  - 수동 재조사: `uv run python scripts/investigate.py --foreground` 를 카톡 1:1 채팅창이 활성일 때 실행.
- **스크린 기반 상태 감지 검토**: 정 아쉬우면 OCR(tesseract) 로 채팅창 상단 이름을 읽어 대상 검증.

### 코드 반영 (Phase 2 시점)

- `docs/architecture.md` D1 업데이트: "UIA 우선" → "win32 HWND + 핫키 + UIA는 창 탐지만" (Phase 3 에서 다시 갱신됨)
- `engine/kakao.py.switch_to_tab` 의 UIA 경로는 **방어적 재시도** 정도로 유지 (카톡 향후 업데이트 대비) + 로그로 실제 경로 추적

### 재현 명령 (Phase 2)

```bash
uv run python scripts/investigate.py --depth 4
uv run python scripts/investigate.py --depth 8
uv run python scripts/investigate.py --find 친구    # 역시 0건 기대
uv run python scripts/investigate.py --foreground   # 채팅창 활성 시 재시도 필요
```

원시 로그: `data/phase2-depth4.txt`, `data/phase2-depth8.txt` (repo 에 올리지 않음 — `.gitignore` 로 제외됨).
