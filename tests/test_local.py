import json
from copy import deepcopy

import pytest

from balatro_ai.local_coach import LocalCoach, action_schema, local_url
from balatro_ai.local_runtime import RunControl
from balatro_ai.runner import Limits, run_game, validate_response
from tests.test_runner import Coach, Game


@pytest.mark.parametrize(
    "url",
    [
        "https://example.com/v1",
        "http://10.0.0.2/v1",
        "http://localhost@evil.com/v1",
        "http://127.0.0.1/v1?x=1",
    ],
)
def test_local_transport_cannot_send_game_state_to_remote(url):
    with pytest.raises(ValueError):
        local_url(url)


def test_qwen_backend_uses_nonthinking_completion_and_preserves_packet(monkeypatch):
    calls = []
    packet = {
        "request_id": "abcd1234",
        "instructions": "Public instructions",
        "plan": "Keep Kings.",
        "analysis": {"next_blind": 600},
        "observation": {"money": 10},
    }
    before = deepcopy(packet)
    response = dict(
        request_id="abcd1234", action_json='{"type":"select_blind"}', plan="Keep Kings."
    )

    def transport(url, body, timeout):
        calls.append((url, body, timeout))
        return {"choices": [{"text": json.dumps(response)}], "usage": {"completion_tokens": 30}}

    monkeypatch.setattr("balatro_ai.local_coach.http_json", transport)
    coach = LocalCoach(model_key="qwen3.6-35b-a3b")
    assert coach.choose(packet, 60) == response
    assert packet == before
    url, body, timeout = calls[0]
    assert url.endswith("/completions") and timeout == 60
    assert "</think>" in body["prompt"] and "Keep Kings." in body["prompt"]
    assert body["response_format"]["type"] == "json_schema"
    assert not coach.last_timings["hedged"]


def test_chat_backend_and_stale_reply_remain_under_upstream_validation(monkeypatch):
    response = dict(request_id="stale", action_json='{"type":"select_blind"}', plan="Grow.")
    calls = []

    def transport(url, body, timeout):
        calls.append(body)
        return {"choices": [{"message": {"content": json.dumps(response)}}]}

    monkeypatch.setattr("balatro_ai.local_coach.http_json", transport)
    coach = LocalCoach(model_key="gemma-4-12b-it-qat")
    actual = coach.choose({"instructions": "Play", "request_id": "live"}, 60)
    with pytest.raises(ValueError, match="stale"):
        validate_response(actual, "live", None)
    assert calls[0]["chat_template_kwargs"] == {"enable_thinking": False}
    assert calls[0]["response_format"]["json_schema"]["schema"]["properties"]["request_id"][
        "enum"
    ] == ["live"]


def test_typed_followup_is_encoded_without_choosing_or_changing_the_action(monkeypatch):
    response = {
        "request_id": "live",
        "action_json": {"type": "skip_blind"},
        "plan": "Grow.",
        "then": [{"action_json": {"type": "select_blind"}, "repeat": None, "until": None}],
    }
    monkeypatch.setattr(
        "balatro_ai.local_coach.http_json",
        lambda *a: {"choices": [{"message": {"content": json.dumps(response)}}]},
    )
    actual = LocalCoach(template="chat").choose({"instructions": "Play", "request_id": "live"}, 60)
    assert json.loads(actual["action_json"]) == response["action_json"]
    assert json.loads(actual["then"][0]["action_json"]) == response["then"][0]["action_json"]
    schema = action_schema([], followup=True)
    assert all(
        "play_cards" not in branch["properties"]["type"]["enum"] for branch in schema["anyOf"]
    )
    pick = next(
        b for b in schema["anyOf"] if b["properties"]["type"]["enum"] == ["choose_pack_card"]
    )
    assert pick["properties"]["card"]["type"] == "object"
    assert pick["properties"]["targets"]["maxItems"] == 0


def test_stop_while_model_waits_never_executes_late_response(tmp_path, monkeypatch):
    monkeypatch.setattr("balatro_ai.runner.fcntl.flock", lambda *_: None)
    monkeypatch.setattr("balatro_ai.runner.time.sleep", lambda _: None)
    control = RunControl()

    class StopCoach(Coach):
        def choose(self, packet, timeout):
            reply = super().choose(packet, timeout)
            control.set("stop")
            return reply

    game = Game()
    result = run_game(
        game, StopCoach(), tmp_path / "run", control=control, limits=Limits(450, 750, 7200, 60)
    )
    assert result["status"] == "stopped" and result["decisions"] == 0
    assert "select" not in game.calls


def test_pause_resume_discards_inflight_response_and_reasks(tmp_path, monkeypatch):
    monkeypatch.setattr("balatro_ai.runner.fcntl.flock", lambda *_: None)
    monkeypatch.setattr("balatro_ai.runner.time.sleep", lambda _: None)
    control = RunControl()

    class PauseCoach(Coach):
        def choose(self, packet, timeout):
            reply = super().choose(packet, timeout)
            if len(self.packets) == 1:
                control.set("pause")
                control.set("resume")
            return reply

    coach, game = PauseCoach(), Game()
    result = run_game(game, coach, tmp_path / "run", control=control)
    assert result["status"] == "won" and len(coach.packets) == 2
    assert game.calls.count("select") == 1
    assert "coach_discarded" in (tmp_path / "run/trajectory.jsonl").read_text()
