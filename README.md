# kakao-sender (v2)

카카오톡 PC 버전을 UI Automation으로 제어해 다수의 친구에게 개인톡을 순차 발송하는 데스크톱 도구. 동아리 생일 축하 메시지 같은 "단톡에 공개할 수 없는 대량 개인톡" 용도.

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
| 상태 감지 | `uiautomation` (UIA) 우선 + 픽셀 fallback | 다크모드/DPI/버전 업데이트에 강함 |
| 대상 검증 | 채팅창 제목과 목표 이름 비교 | 단톡방/동명이인 오발송 0 |
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

# 카톡 UIA 트리 조사 (Phase 2: 실측)
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
│   ├── kakao.py          UIA 기반 primitive
│   ├── detection.py      상태 감지 (UIA + 시간 기반 fallback)
│   └── send.py           오케스트레이션 (send_one, send_batch)
└── roster/               명단 계층
    ├── store.py          CSV 로드/저장
    └── mapping.py        정식명 ↔ 카톡 저장명

scripts/investigate.py    카톡 창 UIA 트리 덤프 (조사용)
tests/                    pytest 단위 테스트
docs/                     설계·분석 문서
```

## 원본과의 관계

원본 v5 는 [`legacy/`](legacy/) 에 통째로 보존되어 있다 (`kakao_sender.py`, `kakao_sender.spec`, `dist/*.exe`, `build/*`). 참조·비교용이며 수정하지 않는다. 상세는 [`legacy/README.md`](legacy/README.md).

## 주요 문서

- [`docs/USER_MANUAL.md`](docs/USER_MANUAL.md) — 담당자용 사용 설명서
- [`docs/original-analysis.md`](docs/original-analysis.md) — 원본 코드 진단 (이미지 잘림 원인 등)
- [`docs/architecture.md`](docs/architecture.md) — 설계 결정 D1~D7
- [`docs/investigation.md`](docs/investigation.md) — Phase 2 UIA 실측 결과

## Phase 2 실측 요약

2026-04-23 조사 결과 **카카오톡 PC 는 UIA 에 의미 있는 컨트롤을 거의 노출하지 않는다.** 탭 버튼·검색창·친구 목록 모두 자체 렌더링. 그래서 설계 결정 D1 이 "UIA 우선" → **"win32 HWND + 핫키 중심, UIA 는 창 탐지만"** 으로 조정됨. 상세 내역은 `docs/investigation.md`.

## 라이선스

사적 사용. 외부 배포 시 별도 협의.
