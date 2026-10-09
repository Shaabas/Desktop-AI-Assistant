from pathlib import Path
from types import SimpleNamespace

from src.jarvis.computer_use.no_progress_guard import (
    RepeatedActionGuard,
    action_signature,
)


def call(name, **args):
    return SimpleNamespace(name=name, args=args)


def test_rephrased_intent_and_small_coordinate_jitter_are_same_action():
    guard = RepeatedActionGuard(repeat_limit=3)
    assert guard.observe([call("click", x=442, y=955, intent="Click message input box")]) is None
    assert guard.observe([call("click", x=454, y=955, intent="Click actual message input")]) is None
    reason = guard.observe([call("click", x=450, y=955, intent="Focus message field")])
    assert reason is not None
    assert "3 consecutive times" in reason
    assert "Stopped before executing" in reason


def test_new_target_resets_streak():
    guard = RepeatedActionGuard(repeat_limit=3)
    a = call("click", x=440, y=950, intent="first")
    b = call("click", x=800, y=400, intent="other target")
    assert guard.observe([a]) is None
    assert guard.observe([a]) is None
    assert guard.observe([b]) is None
    assert guard.streak == 1


def test_observation_action_does_not_count_or_reset_the_streak():
    guard = RepeatedActionGuard(repeat_limit=3)
    click = call("click", x=450, y=950)
    assert guard.observe([click]) is None
    assert guard.observe([click]) is None
    assert guard.observe([call("wait", seconds=1)]) is None
    assert guard.streak == 2
    reason = guard.observe([click])
    assert reason is not None


def test_action_aliases_normalize_to_same_signature():
    assert action_signature(call("click", x=450, y=950)) == action_signature(
        call("click_at", x=450, y=950, intent="alternate wording")
    )


def test_agent_checks_confirmation_before_guard_and_guard_before_dispatch():
    source = Path("src/jarvis/computer_use/agent.py").read_text(encoding="utf-8")
    method = source[source.index("    def run_one_iteration("):]
    preflight = method.index("self._preflight_safety_confirmations(function_calls)")
    guard = method.index("self._no_progress_guard.observe(function_calls)")
    dispatch = method.index("fc_result = self.handle_action(")
    assert preflight < guard < dispatch


def test_route_reports_stall_and_does_not_learn_a_looping_trace():
    source = Path("src/jarvis/workflows/router.py").read_text(encoding="utf-8")
    assert source.count('getattr(agent, "loop_guard_aborted", False)') == 2
    assert 'outcome="stalled"' in source
