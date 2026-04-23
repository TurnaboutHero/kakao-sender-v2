# Phase 2 조사 — 카카오톡 UIA 노출 실측

`scripts/investigate.py` 를 카톡 PC 실행 상태에서 돌려 기록한다. 결과에 따라
`engine.kakao` 의 UIA 경로 vs 핫키 fallback 비중이 결정됨.

실행:

```bash
uv run python scripts/investigate.py --depth 3          # 창 최상위
uv run python scripts/investigate.py --depth 6          # 깊게 내려가기
uv run python scripts/investigate.py --find 검색        # 검색창 관련만
uv run python scripts/investigate.py --find 친구        # 친구 탭 관련만
```

## 체크리스트

**카톡 창 뼈대**

- [ ] 1. 좌측 사이드바의 **친구 탭 버튼** (`ButtonControl Name="친구"` 같은 것)
- [ ] 2. 좌측 사이드바의 **채팅 탭 버튼** (`Name="채팅"`)
- [ ] 3. 친구 탭에 있을 때 **친구 목록** 이 `TreeControl`/`ListControl` 로 노출되는가
  - [ ] 3a. 각 친구 이름이 `TreeItem Name="..."` 로 읽히는가
  - [ ] 3b. 스크롤 가능한가 (가상화 대응 필요?)
- [ ] 4. 상단 검색 `EditControl` (`Name` 또는 `ClassName` 조회 가능한가)

**채팅창**

- [ ] 5. 1:1 채팅창을 열었을 때 **창 제목(상대 이름)** 이 `win32gui.GetWindowText` 로 읽히는가
- [ ] 6. 채팅창이 별도 Window인가, 메인 창 안의 Pane 교체인가
- [ ] 7. 입력창이 `EditControl` 로 노출되고 `.Value`/`.GetValuePattern()` 로 비어있는지 확인 가능한가
- [ ] 8. 마지막 말풍선의 **전송 상태**(전송중/완료) 가 UIA 에 반영되는가

**단톡방 참여자**

- [ ] 9. 단톡방 우측 참여자 패널을 열었을 때 멤버 리스트가 UIA 에 나오는가
- [ ] 9a. 이름 텍스트를 긁어올 수 있는가 (복사 차단 우회)

**포커스 / 포그라운드**

- [ ] 10. 자동화 중 다른 창이 포커스를 뺏는 경우 복구 전략 가능한가

---

## 기록 양식

각 체크 항목 옆에 조사 결과를 붙인다. 예:

```
## 1. 친구 탭 버튼
검사일: 2026-04-23
카톡 버전: X.X.X

출력:
  [Button] Name="친구" Rect=(18,124,88,180)

결론: UIA Name 검색 가능. `engine.kakao.switch_to_tab` UIA 경로 사용 확정.
```

```
## 5. 채팅창 제목
출력: win32gui.GetWindowText -> "회원나 - 카카오톡"
결론: 포함 비교로 충분. `detection.verify_target_title` 그대로 사용.
```

---

## 조사 결과

<!-- 여기에 실측 결과를 추가. 커밋으로 남기면 향후 카톡 버전 업데이트 시 diff 비교 가능. -->

### (아직 기록 없음)
