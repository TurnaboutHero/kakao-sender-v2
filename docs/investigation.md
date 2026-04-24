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
| `0xa00dd4` | `모임 단톡방 (예시)` | 단톡방 | RichEdit + 메시지 ListControl (1:1 과 동일) |
| `0xac0e2a` | (공백) | 단톡방 참여자 패널 | 참여자 ListControl + 검색 Edit |
| `0x10a82` | (공백) | MOMENT 광고 팝업 | Chrome_WidgetWin_* 자식 뭉치 |

**오발송 관점 경고**: 1:1 채팅창과 단톡방이 **클래스·자식 패턴·제목 형식까지 구별 불가**. 제목만 보면 "모임 단톡방 (예시)" 같이 긴 단톡방명과 "회원가" 같은 개인톡이 섞여 있어, **창이 열린 뒤 제목만으로 단톡방인지 아닌지 알 수 없다.**

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
[PaneControl] name='모임 단톡방 (예시)' id=''
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

## Phase 3-2 실기기 dry-run 에서 드러난 실패들 (2026-04-24)

4명(소규모 모임) 대상 실기기 dry-run 에서 **정적 분석으로 잡히지 않은 카톡 PC 의 실제 동작**들이 드러남. 각각 설계 전제를 뒤집었고, 아래는 **실패한 시도와 정정한 이유** 순서대로 기록. 나중에 같은 가정을 반복하지 않기 위한 방지책. (개인정보성 문자열은 회원A~D / 모임 단톡방 등으로 익명화.)

### ① 자동 탭 전환: **카톡 PC 에 친구/채팅 탭 단축키 자체가 없음**

- **최초 가정**: Ctrl+1=친구, Ctrl+2=채팅 (원본 v5 가정을 그대로 계승)
- **실측**: `pyautogui.hotkey("ctrl", "1")` 을 쏴도 탭이 안 바뀜. 공식 단축키 목록 (https://cs.kakao.com/helps_html/1073209659) 확인 결과 **사이드바 탭 전환 단축키 자체가 존재하지 않음**
- **정정**: `switch_to_friend_tab` 을 자동 전환 대신 **"확인만"** 하는 `require_friend_tab` 으로 교체. 친구 탭이 아니면 명확한 에러 + 사용자 수동 전환 요청.
- **설계 교훈**: 가정한 단축키가 있는지는 공식 문서를 먼저 확인할 것. 원본 v5 는 "담당자가 친구 탭에 맞춰놓고 실행" 을 전제했지 자동 전환을 안 했다 — 이걸 놓침.

### ② `Ctrl+A` = **친구 추가** (select-all 아님)

- **최초 가정**: 검색창 내용 전체 선택에 `Ctrl+A` 를 쓰면 간결함
- **실측**: `Ctrl+A` 를 누르면 **친구 추가 다이얼로그가 뜸**. 공식 단축키 목록에 "친구 추가: Ctrl+A" 명시. 이후 덤프로 검색창에 "회원C (그룹 회원B (그룹" 같은 **커서 위치 삽입 결과** 관측됨.
- **정정**: 표준 `Ctrl+A` 가정을 폐기. `End`+`Shift+Home` 시도했으나 포커스가 검색창 밖으로 새면 실패 → 결국 **HWND 직접 조작** 으로 대체 (아래 ④).
- **설계 교훈**: OS 표준 단축키라고 앱 내에서도 같은 의미라는 보장 없음.

### ③ `WM_SETTEXT` → RichEdit 에 **메시지 전송 버튼이 활성화되지 않음** (반자동화 감지)

- **최초 가정**: `SendMessage(richedit, WM_SETTEXT, text)` + `PostMessage(WM_KEYDOWN, VK_RETURN)` 으로 **클립보드·포커스·IME 의존 0** 달성 가능
- **실측**: WM_SETTEXT 로 텍스트를 주입하면 텍스트는 보이지만 **전송 버튼이 회색으로 비활성 유지**. Enter 로도 전송 안 됨. 사용자 증언: "수동 타이핑은 되는데 너가 넣은 건 안 먹힘". 카톡의 명시적 반자동화 감지.
- **정정**: 텍스트도 이미지와 동일한 **클립보드 + Ctrl+V** 경로로 통일. 붙여넣기 이벤트는 "실제 입력" 으로 인식되어 전송 버튼 활성화. 원본 v5 가 이 경로를 쓴 이유가 이것이었음.
- **설계 교훈**: 카톡은 "얼마나 사용자처럼 보이는 입력인가" 로 자동화를 탐지. WM_SETTEXT 같은 우회는 입력 이벤트 stream 이 없어 걸림. Ctrl+V 는 실제 시스템 레벨 paste 이벤트라 통과.

### ④ 검증 로직이 **placeholder 텍스트를 내용으로 오판**

- **최초 가정**: 전송 성공 후 RichEdit 가 비면 (`remaining.strip() == ""`) 성공
- **실측**: 비어있는 RichEdit 는 `WM_GETTEXT` 에 `'메시지 입력'` (카톡 placeholder) 을 반환. 이를 내용으로 오판해 **정상 전송을 실패로 잘못 분류**.
- **정정**: "비어있나" 대신 **"우리가 보낸 텍스트가 남아있나"** 로 검증 기준 전환. `text.strip() in remaining` 이 참일 때만 실패 처리.
- **설계 교훈**: 입력창의 placeholder 존재를 염두에 두지 않음. 현대 앱의 Edit 컨트롤은 WM_GETTEXT 로 placeholder 를 반환하는 경우가 흔함.

### ⑤ 검색창 연속 검색이 **이전 검색어 위에 덧붙음**

- **최초 가정**: `Ctrl+F` (검색창 포커스) → `End`+`Shift+Home` (전체 선택) → `Ctrl+V` (교체) 로 각 반복마다 깨끗한 검색 가능
- **실측**: 첫 번째는 OK, 이후는 **이전 검색어와 커서 위치 상태가 예측 불가능**. "회원B (그룹)" + "회원C (그룹)" 가 덧붙어 `"회원C (그룹 회원B (그룹)그룹)"` 같은 상태 발생. 원인: 채팅창 전송·close 후 포커스가 메인 창 안에서 검색창 아닌 곳으로 이동.
- **정정 1차**: `Ctrl+A` 추가 → ② 때문에 실패
- **정정 2차**: `Esc` + `Ctrl+F` 재오픈으로 상태 리셋 → 여전히 불안정
- **정정 3차 (최종)**: **검색 Edit HWND 직접 조작**. `find_friend_tab_search_edit(main_hwnd)` 로 HWND 획득 → `SendMessage(EM_SETSEL, 0, -1)` 전체 선택 → `WM_CLEAR` 삭제 → `WM_SETTEXT "" ` 방어적 clear → `SetFocus` → `Ctrl+V`. 배치 3건 연속 모두 "기존 내용='회원B (그룹)' → clear 후=''" 로그로 검증 통과.
- **설계 교훈**: pyautogui 같은 **물리 키보드 시뮬레이션은 포커스라는 글로벌 상태에 의존**해 배치 반복에 취약. Edit HWND 가 노출된 경우 `EM_SETSEL`/`WM_CLEAR` 같은 **표준 Edit 메시지**가 포커스 무관하게 작동.

### ⑥ `SetForegroundWindow` 가 **백그라운드 프로세스에서 조용히 거부됨**

- **최초 가정**: `win32gui.SetForegroundWindow(kakao_hwnd)` 한 번이면 포그라운드로 옴
- **실측**: 다른 프로세스(예: 자동화 러너) 에서 호출 시 Windows 가 보안상 **silently reject** (반환은 False 지만 예외 없음). 결과: 핫키가 엉뚱한 창으로 감.
- **정정**: `_force_foreground` 헬퍼 신설 — 현재 포그라운드 스레드에 `AttachThreadInput` 으로 일시 attach → `BringWindowToTop` + `SetForegroundWindow` → detach. 이후 `GetForegroundWindow() == hwnd` 확인해 실패 시 명확한 에러.
- **설계 교훈**: Windows 는 의도적으로 포커스 하이재킹을 막음. 정당한 사용자 자동화도 `AttachThreadInput` 트릭 없이는 안 됨.

### ⑦ false positive 로 인한 **전송 성공 오보** (1회 발생)

- **증상**: 드라이런 첫 시도에서 스크립트가 `✅ 회원A (2.1s)` 로 성공 보고했으나 **실제 메시지는 전송 안 됨** (사용자가 육안 확인).
- **원인**: ③ (WM_SETTEXT + PostMessage VK_RETURN 이 안 먹힘) + ④ (placeholder 를 내용으로 오판) 가 조합된 상태에서, 검증 로직이 아예 없었음. 전송을 시도하고 바로 "성공" 기록.
- **정정**: ③ + ④ + **전송 후 검증 단계를 필수로 삽입**. 검증 실패 시 `KakaoError` raise → `send_one` 이 `FAILED` 로 기록. 이로써 "보고된 성공" 과 "실제 전송" 이 일치.
- **설계 교훈**: 자동화 결과는 **반드시 독립적 관찰로 검증**. 스크립트의 자기보고를 믿지 말 것. 실기기에서 한 번 false positive 가 나면 162명 배치에서 돌이킬 수 없음.

### 최종 파이프라인 (검증 완료)

```
send_one(job):
  require_friend_tab()                                      ← 1차 방어 (수동 전환 요구)
  search(target_name):
    Ctrl+F 로 검색 UI 활성화
    find_friend_tab_search_edit(main_hwnd)                  ← HWND 획득
    EM_SETSEL(0,-1) + WM_CLEAR + WM_SETTEXT "" 로 필드 초기화 ← 포커스 무관
    SetFocus(search_edit)
    Ctrl+V 로 query 붙여넣기                                 ← live search 트리거
  existing = snapshot_chat_hwnds()
  Enter + Alt+Enter
  new_chat = wait_for_new_chat_window(existing, 2s)         ← 2차 방어 (HWND diff)
  if not new_chat: FAILED
  verify_target_title(new_chat.title, target_name) 완전일치  ← 3차 방어
  if not match:
    PostMessage(new_chat.hwnd, WM_CLOSE)
    FAILED
  send_text(text):
    force_foreground(chat.hwnd)
    _set_clipboard_text(text)
    Ctrl+V                                                  ← 반자동화 우회
    Enter
    verify: text not in WM_GETTEXT(richedit)                ← false positive 차단
  close_current_chat()  # PostMessage(WM_CLOSE) on chat HWND
```

### 실기기 dry-run 결과 (4명 실제 발송)

- Phase A 개별: ✅ 회원A, ✅ 회원C, ✅ 회원D (각 단건 2.1~2.7s)
- Phase B 함정: ✅ 단톡방 이름 (모임 단톡방 예시) 을 저장명으로 위장 → **채팅창 안 열림** 단계에서 차단 (3.2s, no message sent)
- Phase C 배치: ✅ 회원B/회원C/회원D 3연속 (각 2.6s, 간격 7s) — 검색 필드 `"회원B (그룹)" → '' → "회원C (그룹)"` 전환 로그로 검증

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
