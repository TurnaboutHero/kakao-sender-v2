# 아키텍처

## 한 줄 요약

**원본은 카톡 상태를 "픽셀 한 점"으로 추정했다. v2는 UI Automation으로 "정확히" 관찰한다.**

## 계층

```
┌──────────────────────────────────────────────┐
│ app.py          Tk UI (명단/매핑/발송/로그 탭)  │
├──────────────────────────────────────────────┤
│ engine.send     SendJob, send_one, send_batch │
│                 오케스트레이션, 재시도, 레이트   │
├──────────────────────────────────────────────┤
│ engine.kakao    KakaoClient                   │
│                 카톡 창 primitive (UIA + 핫키) │
├──────────────────────────────────────────────┤
│ engine.detection  상태 감지 (순수 함수 + 폴링)  │
├──────────────────────────────────────────────┤
│ roster          Member, Mapping, 저장·로드    │
│ config          타이밍·경로·문구 상수         │
│ logging_setup   표준 logging 구성            │
└──────────────────────────────────────────────┘
```

### 의존 방향

- `app` → `engine`, `roster`, `config`
- `engine.*` → `config`, `logging_setup` (cyclic 금지)
- `roster` → `config`, `logging_setup`
- `config` / `logging_setup` 은 누구에게도 의존하지 않음

## 핵심 결정

### D1. 상태 감지는 win32 HWND + `EnumChildWindows` 주력, UIA 는 보조 (Phase 3 재수정)

**Phase 2 관찰**: 메인 창 UIA 는 빈 `PaneControl` 중첩 + 내부 해시 ID 뿐.
**Phase 3 반전** (`docs/investigation.md` 참조):

- 채팅창 (1:1 / 단톡방) 은 **별도 top-level `EVA_Window_Dblclk` HWND**. 내부에 **표준 `RICHEDIT50W` 입력창**, `EVA_VH_ListControl_Dblclk` 메시지 리스트를 HWND 로 노출.
- 메인 창도 `EnumChildWindows` 로 훑으면 `ContactListView_*`, `ChatRoomListView_*`, `MoreView_*`, `LockModeView_*` 등 의미 있는 뷰 이름 + 활성 여부 (rect 0) 를 얻을 수 있음.
- 단톡방 참여자 패널도 별도 top-level HWND. 검색 `Edit` + 리스트 `EVA_VH_ListControl_Dblclk` 구조.

**현실 전략** (Phase 3-2 dry-run 반영):

- 창 탐색: `EnumWindows` + 클래스 `EVA_Window_Dblclk` + 제목 매칭
- **탭 전환은 안 함** — 공식 단축키 목록에 친구/채팅 탭 전환 단축키 자체가 없음. `require_friend_tab` 은 *확인만* 하고 아니면 에러 → 사용자 수동 전환 요구.
- 채팅창 열기: **HWND 스냅샷 diff** (`snapshot → Enter+Alt+Enter → wait_for_new_hwnd`). `GetForegroundWindow` 의존 제거 — 포커스 훔김 내성 확보.
- 검색 입력: 친구 탭 `ContactListView` 의 **검색 Edit HWND 직접 조작** (`EM_SETSEL`+`WM_CLEAR`+`WM_SETTEXT ""`) 으로 필드 초기화 → `SetFocus` → 클립보드 + `Ctrl+V` 로 query 붙여넣기 (kakao live search 트리거). pyautogui 키보드 경로는 배치 반복 시 포커스 예측 불가로 실패.
- 텍스트 전송: **클립보드 + `Ctrl+V` + `Enter`** (실측상 `WM_SETTEXT` 로 주입하면 **전송 버튼이 비활성 유지**되는 반자동화 감지가 있음). 전송 후 `WM_GETTEXT` 로 보낸 텍스트가 남아있는지 확인해 false positive 차단.
- 채팅창 닫기: `PostMessage(chat_hwnd, WM_CLOSE)` — HWND 직접 지정, 엉뚱한 창 닫음 방지.
- 이미지 전송: 클립보드 DIB + Ctrl+V. 포커스는 RichEdit HWND 에 `SetFocus` 로 명시.
- 이미지 업로드 완료: 여전히 파일 크기 비례 고정 대기. 메시지 ListControl 자식 수 변화 감지는 미검증 — Phase C 과제.
- 포커스 확보: `_force_foreground` (`AttachThreadInput` 트릭) 으로 Windows 의 "백그라운드 프로세스 포커스 훔김 방지" 우회.

UIA 는 **입력창 `AutomationId='1006'`** 발견 등 보조 경로로 존재하지만, 주력 아님.

### D2. 오발송 방지 3중 방어 (Phase 3 강화)

v2 의 **존재 이유**. 아래 3층 모두 통과해야 실제 발송:

1. **친구 탭 강제** — `switch_to_friend_tab()` 후 `get_active_main_tab(main_hwnd)` 로 visibility 검증. 실패 시 즉시 예외. 채팅 탭 검색은 단톡방/1:1/친구 혼재 결과를 내므로 금지.
2. **HWND 스냅샷 diff** — 검색 + Enter + Alt+Enter 전후의 `EVA_Window_Dblclk` 집합 차이로 새 채팅창 HWND 를 식별. 포커스가 다른 창으로 가도 타겟 특정 가능.
3. **제목 완전일치** — `detection.verify_target_title` 은 `a.strip() == e.strip()` 만 성공. 부분일치 금지 — 단톡방 제목이 회원 이름을 포함할 수 있어 오발송 위험.

**입력 단계 방어 (3중 방어 이후)**: 위 3층은 "올바른 창을 열었는지" 만 보장한다. 실제 입력(`Ctrl+V`·`Enter`)은 pyautogui 라 HWND 지정이 안 되고 **맨 앞 창으로 간다**. 그래서:

- 검색어 붙여넣기 · 검색 결과 Enter · 메시지/이미지 붙여넣기 · 메시지 Enter 직전마다 `_require_foreground(hwnd)` — 맨 앞 창이 대상이 아니면 키를 보내지 않고 `KakaoFocusError`
- 메시지는 붙여넣은 뒤 `WM_GETTEXT` 로 **이 채팅창 입력창에 들어갔는지** 확인한 후에만 Enter (`detection.pasted_text_present`, 공백·이모지 정규화 비교)
- 입력창에 이전 시도의 텍스트가 남아있으면 붙여넣기 전에 비운다 (이어 붙은 메시지 방지)
- 탭·포커스 오류는 배치 전체를 멈춘다 (`SendResult.aborts_batch`) — 사용자가 PC 를 쓰는 중이므로 계속하면 위험

불일치 시 `PostMessage(new_chat_hwnd, WM_CLOSE)` 로 HWND 를 직접 지정해 닫고 실패로 기록. `Ctrl+W` (포그라운드 창 닫기) 는 **쓰지 않음** — 엉뚱한 창을 닫을 여지 있음.

### D3. 정식명 / 카톡명 분리

공용 명단은 `정식명`(예: "회원나")만 가진다. 담당자의 카톡에 저장된 이름("별칭나")은 담당자별 로컬 매핑에 둔다. 담당자 교체 시 매핑만 재작성. 키는 Member 의 UUID 여서 이름이 바뀌어도 살아남는다.

### D4. 스팸 회피를 코드 수준에서 강제

```python
# config.TIMING
between_send_min: float = 5.0
between_send_max: float = 8.0
rest_every_n: int = 15
rest_duration: float = 45.0
```

+ `MessageVariants.birthday_default` 의 4가지 문구 랜덤 선택. 160명 배치 기준 예상 소요 13~15분. **고정 간격 3초 + 동일 문구 = 계정 차단 위험**이라 이 수치는 양보 불가.

### D5. 워커 스레드 / UI 스레드 마샬링

발송은 `threading.Thread(daemon=True)` 에서 돈다. UI 갱신은 **반드시** `root.after(0, ...)` 로 메인 스레드 큐에 넣는다. 로그 핸들러(`_TkTextHandler`) 도 같은 규칙. 원본의 `self._log()` 직접 호출은 간헐적 UI 글리치의 주 원인이었음.

### D6. 재시도는 1급 기능

`SendResult` 를 보존하고 `retry_failed()` 로 **실패(FAILED)·미발송(STOPPED)자만** 이어보낸다. 원본은 실패자를 로그에만 남기고 담당자가 수기로 재발송해야 했다. 100명 규모 배치에서 3명 누락은 항상 있고, 수기 재발송은 실수 원인.

- `send_batch` 는 중단(사용자 중지·탭/포커스 오류)돼도 **모든 job 에 결과를 하나씩** 남긴다. 남은 사람은 STOPPED. 예외로 결과를 잃지 않는다.
- 이미지는 나가고 텍스트만 실패한 건은 `image_sent=True` — 이어보낼 때 이미지를 빼고 텍스트만 보낸다.
- UI 는 끝나지 않은 배치가 있는데 "발송 시작" 을 누르면 중복 발송 경고를 띄운다.

### D7. 데이터는 커밋하지 않는다

`.gitignore` 에 `data/`, `logs/`, `members.*`, `mapping.*` 포함. 동아리 회원 명단·카톡 저장명은 개인정보이므로 공개 저장소에는 커밋하지 않는다.

### D8. 카톡 반자동화 감지 대응 — 입력은 Ctrl+V, 검증은 HWND (Phase 3-2 정정)

원본 v5 는 모든 입력을 `pyperclip` 으로 클립보드에 복사 + `pyautogui.hotkey('ctrl', 'v')` + `pyautogui.press('enter')` 로 발사했다. 문제:

- 발송 중 사용자가 다른 앱에서 복사하면 클립보드 덮어쓰기 → 이전 텍스트 발송
- 발송 중 포커스가 다른 창으로 가면 키가 엉뚱한 곳으로 감
- IME 상태가 한글이면 간헐적 깨짐

Phase 3 초안은 `WM_SETTEXT` 로 RichEdit 에 직접 주입하면 위 문제를 피하면서 빠르다고 봤지만, Phase 3-2 실기기 dry-run (`docs/investigation.md` Phase 3-2) 에서 **카톡의 반자동화 감지가 WM_SETTEXT 경로를 차단**하는 것이 확인됐다:

- RichEdit 에 WM_SETTEXT 로 텍스트를 넣으면 표시는 되지만 **전송 버튼이 회색 비활성**
- Enter (물리·가상) 모두 무시
- 수동 타이핑·클립보드 Ctrl+V 는 정상 작동

따라서 **메시지 입력은 클립보드+Ctrl+V 로 돌리고**, 대신 세 가지를 보강한다:

1. `_force_foreground` 로 채팅창 HWND 를 **반드시 포그라운드** 로 올려 paste 가 해당 창에 감 (AttachThreadInput 우회)
2. 클립보드 접근은 자체 구현 (`_set_clipboard_text` with `CF_UNICODETEXT`) — pyperclip 의존 제거
3. 전송 직후 `WM_GETTEXT` 로 richedit 내용 확인해 **보낸 텍스트가 남아있으면 실패 raise** (placeholder `메시지 입력` 과 구분해 `text in remaining` 로 판정)

검색창 입력은 **반자동화 감지 대상이 아니라서** (전송 버튼 상태머신이 없으므로) `EM_SETSEL`+`WM_CLEAR`+`WM_SETTEXT ""` 로 HWND 직접 초기화 후 `Ctrl+V` 로 붙여넣는다. 덕분에 배치 반복 시 포커스 이동에 무관한 안정성을 확보.

### D9. 단톡방 참여자 리스트 자동 추출 불가 (Phase 3 신설)

단톡방 참여자 패널의 리스트 컨트롤은 카톡 자체 렌더링. UIA / MSAA / win32 자식 어느 경로로도 **이름 텍스트 미노출**. OCR 외에는 완전 리스트 추출 불가.

**대안**: 검색창(`Edit` HWND)에 이름을 순차 주입하고 결과 리스트 rect / vis 변화로 **개별 존재 여부만 판정**. 161명 루프로 "단톡방에 없는 N 명" 리포트 가능. "단톡방에 있지만 명단에 없는 사람" 은 이 방법으로 못 잡음 — 담당자 수동 확인.

## 테스트 전략

- `roster`, `config`, `detection` 은 pytest 로 100% 커버
- `engine.kakao`, `engine.send` 의 실제 카톡 상호작용은 **수동 테스트 영역** — CI에서 skip
- `send.send_one` 내부 로직은 `KakaoClient` 를 mock 해 단위 테스트 가능 (추후 확장)
- `test_detection.py` 의 `wait_until` 테스트는 `sleeper`/`now` 를 주입해 실시간 0초 완료

## 타겟 플랫폼

- Windows 10/11 + 카카오톡 PC 버전
- macOS/Linux 는 지원 안 함 (pywin32, uiautomation, KakaoTalk 자체가 Windows 전용)

## 마이그레이션 경로

기존 사용자 (원본 v5) 에서 v2로:

1. `data/` 폴더 생성
2. `data/members.csv` 는 UI의 "legacy .json 불러오기" 로 자동 생성
3. 매핑은 담당자가 "동명 자동 채우기" 후 검토·수정
4. 원본 빌드 산출물은 공개 저장소 밖에서만 백업으로 유지

## 미해결 / 확장 여지

- [x] ~~Phase 2: UIA 실측 완료 후 `switch_to_tab`, `select_friend_in_results` 강화~~ → Phase 3 에서 win32 경로로 해소
- [ ] Phase 3 B: 이미지 업로드 완료를 메시지 ListControl 자식 수 변화로 감지 (현재 시간 기반 유지)
- [x] ~~단톡 참여자 리스트 자동 추출~~ → **불가 확정** (D9). 대신 "존재 검증" 루트로 전환 가능
- [ ] 단톡방 참여자 존재 검증 도구 (검색창 주입 루프, Phase 3 선택 구현)
- [ ] Google Sheets 연동으로 명단 공유
- [ ] 자정 스케줄러 (스팸 리스크 평가 후)
- [ ] 발송 전 본인 계정 테스트 발송
- [ ] 키보드/마우스 잠금 모드
