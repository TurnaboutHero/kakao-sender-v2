# legacy/ — v5 원본 아카이브

동아리 선배가 Claude 채팅(무료)으로 만든 **원본 `kakao_sender.py` v5**. 구조적 결함
때문에 v2 (상위 디렉토리) 로 재작성됐지만, 역사·비교·재현 목적으로 소스만 보존한다.

## 포함물

| 파일 | 크기 | 내용 |
| --- | --- | --- |
| `kakao_sender.py` | 33 KB | 단일 파일 (Tkinter UI + 카톡 자동화, 796 줄) |
| `kakao_sender.spec` | 1 KB | PyInstaller 빌드 스펙 |

제외한 항목:

- `dist/`, `build/`: 재생성 가능한 PyInstaller 산출물
- `members.json`, `members.csv`, `mapping.json`: 사용자 명단·매핑 데이터

## 왜 v2 로 다시 썼는가

원본의 핵심 결함 3가지:

1. **픽셀 한 점으로 카톡 상태를 추정** — 이미지 업로드 도중 "입력창 색이 흰색"이라
   업로드 완료로 오판, 채팅창을 조기 종료하는 증상의 원인.
2. **오발송 방지 부재** — 검색 첫 결과에 맹목 Enter, 동명이인·단톡방 구분 없음.
3. **명단이 로컬 JSON** — 담당자 교체 시 처음부터 재작성, 카톡 저장명 동기화 수단 없음.

상세 진단은 `../docs/original-analysis.md`, 설계 교체 근거는 `../docs/architecture.md`.

## 실행 권장하지 않음

- 보관 목적. 실제 발송에는 v2 (`kakao_sender/`) 를 쓴다.
- 실행해도 위 3가지 결함은 그대로. 특히 150명 이상 배치에서 이미지 전송 누락 발생.
- 어차피 의존성(`pyautogui`, `pyperclip`, `pywin32`, `Pillow`) 은 v2 `pyproject.toml`
  에 이미 들어 있음. 꼭 실행해야 한다면: `uv run python legacy/kakao_sender.py`.

## 공개 저장소 기준

실제 명단과 담당자별 카톡 저장명은 개인정보이므로 이 디렉토리에도 커밋하지 않는다.
