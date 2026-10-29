"""engine.send 오케스트레이션 테스트 — KakaoClient 를 가짜로 대체."""

from __future__ import annotations

from pathlib import Path

from kakao_sender.engine.kakao import (
    KakaoError,
    KakaoFocusError,
    KakaoTabError,
    KakaoTargetMismatchError,
)
from kakao_sender.engine.send import (
    SendController,
    SendJob,
    SendOutcome,
    retry_failed,
    retry_jobs,
    send_batch,
    send_one,
)

IMAGE = Path("card.png")


class FakeClient:
    """호출 기록 + 대상별로 지정한 단계에서 예외를 던지는 가짜 KakaoClient."""

    def __init__(self, failures: dict[tuple[str, str], Exception] | None = None) -> None:
        self.failures = failures or {}
        self.calls: list[tuple[str, str]] = []
        self.sent: list[tuple[str, str]] = []  # (대상, "image" | 텍스트)
        self.open_chat: str | None = None
        self.target = ""
        self.closed_search = 0

    def _step(self, step: str) -> None:
        self.calls.append((step, self.target))
        exc = self.failures.get((self.target, step))
        if exc is not None:
            raise exc

    def require_friend_tab(self) -> None:
        self._step("tab")

    def search(self, query: str) -> None:
        self.target = query
        self._step("search")

    def open_friend_chat(self, expected_name: str) -> None:
        self._step("open")
        self.open_chat = expected_name

    def send_image(self, image_path: Path) -> None:
        self._step("image")
        self.sent.append((self.target, "image"))

    def send_text(self, text: str) -> None:
        self._step("text")
        self.sent.append((self.target, text))

    def close_current_chat(self) -> None:
        self.calls.append(("close", self.target))
        self.open_chat = None

    def close_search(self) -> None:
        self.closed_search += 1


def _jobs(*names: str, image: bool = False) -> list[SendJob]:
    return [
        SendJob(target_name=n, text="축하해요", image_path=IMAGE if image else None) for n in names
    ]


def _batch(client: FakeClient, jobs: list[SendJob], **kwargs):
    return send_batch(client, jobs, sleeper=lambda _s: None, **kwargs)


# ── send_one ───────────────────────────────────


def test_send_one_success_closes_chat() -> None:
    client = FakeClient()
    r = send_one(client, _jobs("회원가")[0])
    assert r.outcome == SendOutcome.SUCCESS
    assert client.sent == [("회원가", "축하해요")]
    assert client.open_chat is None


def test_send_one_closes_chat_when_send_fails() -> None:
    """전송 중 실패해도 열린 채팅창을 남기지 않는다."""
    client = FakeClient({("회원가", "text"): KakaoError("붙여넣기 확인 실패")})
    r = send_one(client, _jobs("회원가")[0])
    assert r.outcome == SendOutcome.FAILED
    assert not r.aborts_batch
    assert client.open_chat is None
    assert ("close", "회원가") in client.calls


def test_send_one_target_mismatch_is_plain_failure() -> None:
    client = FakeClient({("회원가", "open"): KakaoTargetMismatchError("제목 불일치")})
    r = send_one(client, _jobs("회원가")[0])
    assert r.outcome == SendOutcome.FAILED
    assert not r.aborts_batch
    assert client.sent == []


def test_send_one_focus_error_aborts_batch_without_raising() -> None:
    client = FakeClient({("회원가", "text"): KakaoFocusError("맨 앞 창이 대상이 아님")})
    r = send_one(client, _jobs("회원가")[0])
    assert r.outcome == SendOutcome.FAILED
    assert r.aborts_batch


def test_send_one_records_image_sent_before_text_failure() -> None:
    client = FakeClient({("회원가", "text"): KakaoError("전송 실패")})
    r = send_one(client, _jobs("회원가", image=True)[0])
    assert r.outcome == SendOutcome.FAILED
    assert r.image_sent


# ── send_batch ─────────────────────────────────


def test_batch_all_success_one_result_per_job() -> None:
    client = FakeClient()
    results = _batch(client, _jobs("가", "나", "다"))
    assert [r.outcome for r in results] == [SendOutcome.SUCCESS] * 3
    assert client.closed_search == 1


def test_batch_not_on_friend_tab_sends_nothing() -> None:
    client = FakeClient({("", "tab"): KakaoTabError("채팅 탭")})
    results = _batch(client, _jobs("가", "나"))
    assert [r.outcome for r in results] == [SendOutcome.STOPPED] * 2
    assert client.sent == []


def test_batch_mid_tab_error_stops_and_keeps_results() -> None:
    """배치 도중 탭이 바뀌면 예외로 결과를 잃지 않고, 남은 사람은 STOPPED 로 남는다."""
    client = FakeClient({("나", "open"): KakaoTabError("탭 바뀜")})
    results = _batch(client, _jobs("가", "나", "다", "라"))
    assert [r.outcome for r in results] == [
        SendOutcome.SUCCESS,
        SendOutcome.FAILED,
        SendOutcome.STOPPED,
        SendOutcome.STOPPED,
    ]
    assert client.sent == [("가", "축하해요")]


def test_batch_focus_error_stops_remaining() -> None:
    client = FakeClient({("가", "text"): KakaoFocusError("다른 창 클릭")})
    results = _batch(client, _jobs("가", "나"))
    assert [r.outcome for r in results] == [SendOutcome.FAILED, SendOutcome.STOPPED]
    assert [c for c in client.calls if c[1] == "나"] == []


def test_batch_ordinary_failure_continues() -> None:
    client = FakeClient({("나", "open"): KakaoError("채팅창이 열리지 않음")})
    results = _batch(client, _jobs("가", "나", "다"))
    assert [r.outcome for r in results] == [
        SendOutcome.SUCCESS,
        SendOutcome.FAILED,
        SendOutcome.SUCCESS,
    ]


def test_batch_user_stop_records_every_remaining_job() -> None:
    ctrl = SendController()
    client = FakeClient()
    seen: list[str] = []

    def on_result(r) -> None:
        seen.append(r.job.target_name)
        if r.job.target_name == "가":
            ctrl.stop()

    results = _batch(client, _jobs("가", "나", "다"), controller=ctrl, on_result=on_result)
    assert [r.outcome for r in results] == [
        SendOutcome.SUCCESS,
        SendOutcome.STOPPED,
        SendOutcome.STOPPED,
    ]
    assert seen == ["가", "나", "다"]


def test_batch_close_search_failure_does_not_lose_results() -> None:
    client = FakeClient()

    def broken_close_search() -> None:
        raise KakaoFocusError("포커스 없음")

    client.close_search = broken_close_search  # type: ignore[method-assign]
    results = _batch(client, _jobs("가"))
    assert [r.outcome for r in results] == [SendOutcome.SUCCESS]


# ── 이어보내기 ─────────────────────────────────


def test_retry_jobs_skips_success_and_includes_stopped() -> None:
    client = FakeClient({("나", "open"): KakaoTabError("탭 바뀜")})
    results = _batch(client, _jobs("가", "나", "다"))
    assert [j.target_name for j in retry_jobs(results)] == ["나", "다"]


def test_retry_drops_image_already_sent() -> None:
    client = FakeClient({("가", "text"): KakaoError("전송 실패")})
    results = _batch(client, _jobs("가", image=True))
    [job] = retry_jobs(results)
    assert job.image_path is None
    assert job.text == "축하해요"


def test_retry_failed_never_resends_to_successful_targets() -> None:
    first = FakeClient({("나", "open"): KakaoFocusError("다른 창 클릭")})
    results = _batch(first, _jobs("가", "나", "다"))

    second = FakeClient()
    retried = retry_failed(second, results, sleeper=lambda _s: None)
    assert [r.outcome for r in retried] == [SendOutcome.SUCCESS] * 2
    assert {t for t, _ in second.sent} == {"나", "다"}
