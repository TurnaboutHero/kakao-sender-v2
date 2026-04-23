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

### D1. 상태 감지는 UIA 우선, 시간 기반 fallback

픽셀 감지는 완전 폐기. `uiautomation` 라이브러리로 `WindowControl`, `ButtonControl`, `EditControl` 을 찾아 조작한다. 노출되지 않는 영역(메시지 영역, 업로드 완료)은 시간 기반 대기로 보완. Phase 2 조사 결과에 따라 UIA 적용 범위를 점진 확대.

### D2. 대상 검증 계층 분리

오발송 방지는 v2의 **존재 이유**다. `detection.verify_target_title(actual, expected)` 는 순수 함수로 테스트 가능하게 분리했다. 엔진(`send_one`)은 채팅창을 연 직후 제목을 읽어 이 함수에 위임하고, 불일치 시 즉시 닫고 실패로 기록.

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

`SendResult` 를 보존하고 `retry_failed()` 로 **실패자만** 다시 돌린다. 원본은 실패자를 로그에만 남기고 담당자가 수기로 재발송해야 했다. 100명 규모 배치에서 3명 누락은 항상 있고, 수기 재발송은 실수 원인.

### D7. 데이터는 커밋하지 않는다

`.gitignore` 에 `data/`, `logs/`, `members.*`, `mapping.*` 포함. 동아리 회원 명단·카톡 저장명은 개인정보. public-safe repo라도 최소화.

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
4. 원본 `카카오톡_자동발송기.exe` 는 한동안 백업으로 유지

## 미해결 / 확장 여지

- [ ] Phase 2: UIA 실측 완료 후 `switch_to_tab`, `select_friend_in_results` 강화
- [ ] Phase 2: 이미지 업로드 완료 UIA 감지 (현재 시간 기반)
- [ ] 단톡 참여자 리스트 자동 추출 (복사 차단 회피) — OCR or UIA
- [ ] Google Sheets 연동으로 명단 공유
- [ ] 자정 스케줄러 (스팸 리스크 평가 후)
- [ ] 발송 전 본인 계정 테스트 발송
- [ ] 키보드/마우스 잠금 모드
