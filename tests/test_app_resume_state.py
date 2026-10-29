"""SendTab 이어보내기 상태 복원 — Tk 위젯 없이 메서드만 호출."""

from __future__ import annotations

from types import SimpleNamespace

from kakao_sender import app
from kakao_sender.engine.send import SendJob, SendOutcome, SendResult


def _r(name: str, outcome: SendOutcome) -> SendResult:
    return SendResult(SendJob(target_name=name, text="축하해요"), outcome)


def _stub(prev_results: list[SendResult], prev_sent: list[SendResult]) -> SimpleNamespace:
    return SimpleNamespace(
        state=app._SendUIState(is_running=True, last_results=[], sent_before=[*prev_sent]),
        _prev_state=(prev_results, prev_sent),
        _set_running_ui=lambda _running: None,
        status_lbl=SimpleNamespace(config=lambda **_kw: None),
        _update_retry_button=lambda: None,
    )


def test_worker_failed_before_any_result_restores_pending(monkeypatch) -> None:
    """이어보내기가 카톡 창을 못 찾아 바로 실패해도 남은 사람 목록이 사라지면 안 됨.

    사라지면 '발송 시작' 의 중복 경고가 안 떠서 이미 받은 사람에게 다시 간다.
    """
    monkeypatch.setattr(app.messagebox, "showerror", lambda *_a, **_k: None)
    prior = [_r("가", SendOutcome.SUCCESS), _r("나", SendOutcome.STOPPED)]
    stub = _stub(prior, [])

    app.SendTab._worker_failed(stub, "카톡 메인 창을 찾을 수 없음")

    assert stub.state.last_results == prior
    assert not stub.state.is_running


def test_worker_failed_keeps_partial_results(monkeypatch) -> None:
    monkeypatch.setattr(app.messagebox, "showerror", lambda *_a, **_k: None)
    stub = _stub([_r("가", SendOutcome.STOPPED)], [])
    partial = [_r("가", SendOutcome.SUCCESS)]
    stub.state.last_results = partial

    app.SendTab._worker_failed(stub, "예기치 못한 오류")

    assert stub.state.last_results == partial
