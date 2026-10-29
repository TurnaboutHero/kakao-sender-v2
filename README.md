# kakao-sender (v2)

카카오톡 PC 버전을 win32 창 핸들(HWND)과 클립보드·키 입력으로 제어해 다수의 친구에게 개인톡을 순차 발송하는 데스크톱 도구. 동아리 생일 축하 메시지 같은 "단톡에 공개할 수 없는 대량 개인톡" 용도.

## 이 저장소가 존재하는 이유

원본 `kakao_sender.py` (단일 파일 796줄, Claude 채팅으로 만든 v5)에서 드러난 구조적 결함을 고치기 위해 **처음부터 다시 설계**한다.

원본의 핵심 문제:

1. **픽셀 색 한 점으로 카톡 상태를 추정** — 이미지 업로드 완료 오판, 다크 모드/DPI 취약
2. **오발송 방지 부재** — 검색 첫 결과에 맹목 Enter, 탭 전제 미검증, 동명이인 미구분
3. **명단이 로컬 JSON** — 담당자 교체 시 처음부터, 카톡 저장명 동기화 수단 없음

상세 진단은 [`docs/original-analysis.md`](docs/original-analysis.md) 참조.

## 설계 결정 요약

| 항목 | 선택 | 근거 |
| --- | --- | --- |
| 창 제어·감지 | win32 HWND (`EnumWindows`/`EnumChildWindows`) + 클립보드·`Ctrl+V` | 카톡 UIA 트리는 비어 있어 쓸 수 없음 (Phase 2 실측) |
| 대상 검증 | 친구 탭 확인 + HWND 스냅샷 diff + 제목 완전일치, 키 입력 직전 맨 앞 창 확인 | 단톡방/동명이인/엉뚱한 창 오발송 차단 |
| 명단 | CSV 원본 + JSON 매핑 | 담당자별 카톡 저장명 변형 수용 |
| 스팸 회피 | 간격 랜덤 + 주기 휴식 + 문구 변주 | 161명 규모 계정 차단 예방 |
| 구조 | 패키지 분리 (app/engine/roster/config) | 테스트·재사용 가능 |

상세 설계는 [`docs/architecture.md`](docs/architecture.md).

## 빠른 시작

**필수:** Windows, Python 3.12+, 카카오톡 PC 버전 로그인 상태, [uv](https://docs.astral.sh/uv/).

```bash
# 의존성 설치 (개발 의존성 포함)
uv sync

# 테스트 실행
uv run pytest

# 린트
uv run ruff check
uv run ruff format --check

# 앱 실행
uv run python -m kakao_sender

# 실기기 dry-run (기본은 발송 안 함, --send 를 붙여야 실제 발송)
uv run python scripts/dryrun.py --list

# 카톡 창 구조 조사 (uiautomation 은 dev 의존성에만 포함)
uv run python scripts/investigate.py
```

## 디렉토리

```
kakao_sender/
├── __main__.py           python -m kakao_sender 진입점
├── app.py                Tk UI (4탭: 명단/매핑/발송/로그)
├── config.py             타이밍·문구·경로 상수
├── logging_setup.py      표준 logging 구성
├── engine/               카톡 조작 계층
│   ├── kakao.py          카톡 조작 primitive (검색·채팅창 열기·전송)
│   ├── windows.py        win32 HWND 탐색 (채팅창 스냅샷, 활성 탭 판정)
│   ├── detection.py      상태 판정 순수 함수 (제목 검증, 입력창 텍스트 비교)
│   └── send.py           오케스트레이션 (send_one, send_batch, 이어보내기)
└── roster/               명단 계층
    ├── store.py          CSV 로드/저장
    └── mapping.py        정식명 ↔ 카톡 저장명

scripts/
├── dryrun.py             UI 없이 발송 파이프라인만 실행 (실기기 검증용)
├── migrate.py            원본 members.json → members.csv 변환
└── investigate.py        카톡 창 구조 덤프 (조사용)
tests/                    pytest 단위 테스트
docs/                     설계·분석 문서
```

## 원본과의 관계

원본 v5 의 소스와 빌드 스펙은 [`legacy/`](legacy/) 에 보존되어 있다. 빌드 산출물과 실제 명단 파일은 공개 저장소에 포함하지 않는다. 상세는 [`legacy/README.md`](legacy/README.md).

## 주요 문서

- [`docs/USER_MANUAL.md`](docs/USER_MANUAL.md) — 담당자용 사용 설명서
- [`docs/original-analysis.md`](docs/original-analysis.md) — 원본 코드 진단 (이미지 잘림 원인 등)
- [`docs/architecture.md`](docs/architecture.md) — 설계 결정 D1~D7
- [`docs/investigation.md`](docs/investigation.md) — Phase 2 UIA 실측 결과

## 실측 요약 (Phase 2 + Phase 3)

- **Phase 2 (2026-04-23)**: 카톡 메인 창 UIA 가 빈 `PaneControl` 중첩뿐. 탭 버튼·검색창·친구 목록 모두 자체 렌더링.
- **Phase 3 (2026-04-24)**: win32 `EnumChildWindows` 경로 추가 조사. **채팅창 (1:1 / 단톡방) 은 별도 top-level HWND** 이며 내부에 표준 `RICHEDIT50W` 입력창과 메시지 `ListControl` 을 노출. 메인 창도 `ContactListView_*` / `ChatRoomListView_*` 등 자식 HWND 이름 + rect 로 활성 탭 판정 가능. 단톡방 참여자 패널도 별도 HWND (검색 `Edit` + 리스트).

**결과**: D1 이 "UIA 우선" → "win32 HWND + 핫키" (Phase 2) → **"win32 HWND + `EnumChildWindows` 주력, UIA 는 보조"** (Phase 3) 로 갱신. 오발송 방지는 **3중 방어** (친구 탭 강제 + HWND 스냅샷 diff + 제목 완전일치) 로 강화. 텍스트 전송은 `WM_SETTEXT` 가 카톡 반자동화 감지에 걸려 클립보드 + `Ctrl+V` + `Enter` 를 쓰고, 키 입력 직전마다 대상 창이 맨 앞인지 확인한다. 상세 내역은 [`docs/investigation.md`](docs/investigation.md).

## 라이선스

사적 사용. 외부 배포 시 별도 협의.
