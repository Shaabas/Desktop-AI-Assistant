from pathlib import Path

from src.jarvis.agent.security import SecurityGuard
from src.jarvis.workflows.compiler import WorkflowCompiler
from src.jarvis.workflows.executor import WorkflowExecutor
from src.jarvis.workflows.matcher import WorkflowMatcher
from src.jarvis.workflows.registry import WorkflowRegistry


class FakeComputer:
    def __init__(self):
        self.calls = []
    def screen_size(self): return (1000, 500)
    def type_text(self, text, press_enter=False): self.calls.append(("type", text, press_enter)); return None
    def type_text_at(self, x, y, text, press_enter=False, clear_before_typing=True): self.calls.append(("type_at", x, y, text, press_enter, clear_before_typing)); return None
    def click_at(self, x, y): self.calls.append(("click", x, y)); return None
    def double_click_at(self, x, y): self.calls.append(("double", x, y)); return None
    def triple_click_at(self, x, y): self.calls.append(("triple", x, y)); return None
    def middle_click_at(self, x, y): self.calls.append(("middle", x, y)); return None
    def right_click_at(self, x, y): self.calls.append(("right", x, y)); return None
    def mouse_down(self, x, y): self.calls.append(("down", x, y)); return None
    def mouse_up(self, x, y): self.calls.append(("up", x, y)); return None
    def hover_at(self, x, y): self.calls.append(("hover", x, y)); return None
    def key_combination(self, keys): self.calls.append(("keys", keys)); return None
    def press_key(self, key): self.calls.append(("key", key)); return None
    def key_down(self, key): self.calls.append(("downkey", key)); return None
    def key_up(self, key): self.calls.append(("upkey", key)); return None
    def scroll_at(self, x, y, direction, magnitude=800): self.calls.append(("scroll", x, y, direction, magnitude)); return None
    def wait(self, seconds): self.calls.append(("wait", seconds)); return None
    def wait_5_seconds(self): self.calls.append(("wait5",)); return None
    def go_back(self): self.calls.append(("back",)); return None
    def go_forward(self): self.calls.append(("forward",)); return None
    def navigate(self, url): self.calls.append(("navigate", url)); return None
    def open_web_browser(self): self.calls.append(("browser",)); return None
    def search(self): self.calls.append(("search",)); return None
    def take_screenshot(self): self.calls.append(("shot",)); return None
    def drag_and_drop(self, x, y, destination_x, destination_y): self.calls.append(("drag", x, y, destination_x, destination_y)); return None


def test_registry_matcher_and_parameterized_execution(tmp_path: Path):
    root = tmp_path / "workflows"
    root.mkdir()
    (root / "demo.yml").write_text(
        """
name: open_demo

description: Open demo website and type query

environment: desktop
platform: any
tags: [demo, browser]
steps:
  - action: navigate
    args:
      url: https://example.com
  - action: type
    args:
      text: "{{query}}"
""",
        encoding="utf-8",
    )
    registry = WorkflowRegistry(root)
    match = WorkflowMatcher(registry).find("open demo browser", environment="desktop", platform="linux")
    assert match is not None
    fake = FakeComputer()
    WorkflowExecutor(SecurityGuard(), lambda _: True).run(match.workflow, fake, {"query": "hello"})
    assert fake.calls == [("navigate", "https://example.com"), ("type", "hello", False)]


def test_compiler_normalizes_coordinates():
    workflow = WorkflowCompiler().compile_from_trace(
        "click demo",
        [{"method": "click_at", "args": [250, 100], "kwargs": {}}],
        (1000, 500),
        environment="desktop",
        platform="linux",
    )
    assert workflow.steps[0].args == {"x": 250, "y": 200}


def test_security_text_boundary():
    guard = SecurityGuard()
    risky, reason = guard.evaluate_text_input("sudo rm -rf ~/data")
    assert risky is True
    assert reason


def test_compiler_preserves_positional_arguments_for_non_coordinate_actions():
    workflow = WorkflowCompiler().compile_from_trace(
        "search for relaxing music",
        [
            {"method": "navigate", "args": ["https://example.com"], "kwargs": {}},
            {"method": "type_text", "args": ["relaxing music", True], "kwargs": {}},
            {"method": "press_key", "args": ["ENTER"], "kwargs": {}},
            {"method": "wait", "args": [2], "kwargs": {}},
        ],
        (1000, 500),
        environment="desktop",
        platform="linux",
    )
    assert [step.args for step in workflow.steps] == [
        {"url": "https://example.com"},
        {"text": "relaxing music", "press_enter": True},
        {"key": "ENTER"},
        {"seconds": 2},
    ]
    assert workflow.metadata["trace_warnings"] == []


def test_compiler_does_not_turn_missing_arguments_into_empty_actions():
    workflow = WorkflowCompiler().compile_from_trace(
        "open firefox",
        [
            {"method": "press_key", "args": [], "kwargs": {}},
            {"method": "type_text", "args": [], "kwargs": {"press_enter": True}},
            {"method": "click_at", "args": [10], "kwargs": {}},
        ],
        (1000, 500),
        environment="desktop",
        platform="linux",
    )
    assert workflow.steps == []
    assert len(workflow.metadata["trace_warnings"]) == 3


def test_unvalidated_learned_workflows_are_not_auto_matched(tmp_path: Path):
    root = tmp_path / "workflows"
    learned = root / "learned"
    learned.mkdir(parents=True)
    (learned / "send_email.yml").write_text(
        """
name: send_email
description: Send an email
metadata:
  status: unvalidated
steps:
  - action: click
    args: {x: 500, y: 500}
""",
        encoding="utf-8",
    )
    registry = WorkflowRegistry(root)
    assert registry.get("send_email") is not None
    assert WorkflowMatcher(registry, minimum_score=0.1).find("send email") is None


def test_missing_workflow_parameters_fail_before_any_action(tmp_path: Path):
    root = tmp_path / "workflows"
    root.mkdir()
    (root / "type_query.yml").write_text(
        """
name: type_query
description: Type query
parameters:
  query:
    required: true
steps:
  - action: type_text
    args:
      text: "{{query}}"
""",
        encoding="utf-8",
    )
    workflow = WorkflowRegistry(root).get("type_query")
    fake = FakeComputer()
    try:
        WorkflowExecutor(SecurityGuard(), lambda _: True).run(workflow, fake)
    except Exception as exc:
        assert "Missing required workflow parameter" in str(exc) or "Missing value" in str(exc)
    else:
        raise AssertionError("workflow without required query should not execute")
    assert fake.calls == []


def test_url_postcondition_is_reported_as_verified(tmp_path: Path):
    root = tmp_path / "workflows"
    root.mkdir()
    (root / "open_example.yml").write_text(
        """
name: open_example
description: Open example
steps:
  - action: navigate
    args:
      url: https://example.com
    expect:
      type: url_contains
      value: example.com
      timeout_seconds: 0
""",
        encoding="utf-8",
    )
    workflow = WorkflowRegistry(root).get("open_example")
    fake = FakeComputer()
    fake.current_state = lambda: type("State", (), {"url": "https://example.com"})()
    report = WorkflowExecutor(SecurityGuard(), lambda _: True).run_with_report(workflow, fake)
    assert report.outcome == "succeeded_verified"
    assert report.steps[0].outcome == "succeeded_verified"


def test_expectation_timeout_stops_workflow_instead_of_silently_continuing(tmp_path: Path):
    root = tmp_path / "workflows"
    root.mkdir()
    (root / "wrong_url.yml").write_text(
        """
name: wrong_url
description: Navigate and verify
steps:
  - action: navigate
    args:
      url: https://example.com
    expect:
      type: url_contains
      value: definitely-not-this-domain
      timeout_seconds: 0
""",
        encoding="utf-8",
    )
    workflow = WorkflowRegistry(root).get("wrong_url")
    fake = FakeComputer()
    fake.current_state = lambda: type("State", (), {"url": "https://example.com"})()
    try:
        WorkflowExecutor(SecurityGuard(), lambda _: True).run(workflow, fake)
    except Exception as exc:
        assert "postcondition not met" in str(exc)
    else:
        raise AssertionError("failed postcondition must stop the workflow")


def test_registry_approval_requires_valid_workflow(tmp_path: Path):
    root = tmp_path / "workflows"
    learned = root / "learned"
    learned.mkdir(parents=True)
    (learned / "broken.yml").write_text(
        """
name: broken
description: Broken workflow
metadata:
  status: unvalidated
steps:
  - action: press_key
    args: {}
""",
        encoding="utf-8",
    )
    registry = WorkflowRegistry(root)
    try:
        registry.approve("broken")
    except Exception as exc:
        assert "missing required argument" in str(exc)
    else:
        raise AssertionError("invalid workflow should not be approved")


def test_local_planner_selects_only_approved_candidates_and_binds_parameters(tmp_path: Path):
    import json

    from src.jarvis.workflows.local_planner import WorkflowLocalPlanner

    root = tmp_path / "workflows"
    root.mkdir()
    (root / "search.yml").write_text(
        """
name: search_web
description: Search the web for a query
tags: [browser, search]
parameters:
  query:
    description: Search query
    required: true
steps:
  - action: navigate
    args:
      url: https://www.google.com
  - action: type_text
    args:
      text: "{{query}}"
      press_enter: true
""",
        encoding="utf-8",
    )

    class FakeOllama:
        prompt = ""
        system = ""
        def available(self):
            return True
        def generate(self, prompt, system="", **kwargs):
            self.prompt = prompt
            self.system = system
            return json.dumps({
                "workflow": "search_web",
                "parameters": {"query": "relaxing music"},
                "confidence": 0.93,
                "reason": "The user requested a web search.",
            })

    registry = WorkflowRegistry(root)
    ollama = FakeOllama()
    planner = WorkflowLocalPlanner(ollama, registry)  # type: ignore[arg-type]
    match = planner.choose("search the web for relaxing music", environment="desktop", platform="linux")
    assert match is not None
    assert match.workflow.name == "search_web"
    assert match.parameters == {"query": "relaxing music"}
    assert "screenshot" not in ollama.prompt.lower()
    assert "accessibility tree" not in ollama.prompt.lower()
    assert "no screenshot" in ollama.system.lower()
    assert "accessibility tree" in ollama.system.lower()


def test_adapter_never_inherits_approved_status(tmp_path: Path):
    import yaml

    from src.jarvis.workflows.adapter import WorkflowAdapter

    root = tmp_path / "workflows"
    root.mkdir()
    source_path = root / "source.yml"
    source_path.write_text(
        """
name: source
description: Open an example
metadata:
  status: approved
steps:
  - action: navigate
    args:
      url: https://example.com
""",
        encoding="utf-8",
    )
    registry = WorkflowRegistry(root)
    workflow = registry.get("source")

    class FakeOllama:
        def generate(self, prompt, system="", **kwargs):
            data = workflow.to_dict()
            data["name"] = "source_adapted"
            data["description"] = "Open a different example"
            data["steps"][0]["args"]["url"] = "https://example.org"
            return yaml.safe_dump(data, sort_keys=False)

    adapted = WorkflowAdapter(FakeOllama(), registry).adapt(workflow, "Use example.org instead")  # type: ignore[arg-type]
    assert adapted.metadata["status"] == "unvalidated"
    assert adapted.metadata["source"] == "local_llm_adapter"
    assert adapted.is_runnable is False


def test_unapproved_candidate_cannot_be_directly_executed(tmp_path: Path):
    root = tmp_path / "workflows"
    learned = root / "learned"
    learned.mkdir(parents=True)
    (learned / "candidate.yml").write_text(
        """
name: candidate
description: Candidate workflow
metadata:
  status: unvalidated
steps:
  - action: press_key
    args: {key: ENTER}
""",
        encoding="utf-8",
    )
    workflow = WorkflowRegistry(root).get("candidate")
    try:
        WorkflowExecutor(SecurityGuard(), lambda _: True).run(workflow, FakeComputer())
    except Exception as exc:
        assert "not approved" in str(exc)
    else:
        raise AssertionError("unapproved workflow should not execute")


def test_registry_approval_enables_only_valid_candidate(tmp_path: Path):
    root = tmp_path / "workflows"
    learned = root / "learned"
    learned.mkdir(parents=True)
    (learned / "candidate.yml").write_text(
        """
name: candidate
description: Candidate workflow
metadata:
  status: unvalidated
steps:
  - action: press_key
    args: {key: ENTER}
""",
        encoding="utf-8",
    )
    registry = WorkflowRegistry(root)
    assert registry.approve("candidate").is_runnable is True
    assert registry.get("candidate").metadata["status"] == "approved"


def test_failed_workflow_does_not_automatically_replay_task(tmp_path: Path):
    from src.jarvis.workflows.executor import WorkflowExecutionError
    from src.jarvis.workflows.matcher import WorkflowMatch
    from src.jarvis.workflows.router import ExecutionRouter

    root = tmp_path / "workflows"
    root.mkdir()
    (root / "demo.yml").write_text(
        """
name: demo
description: open demo
steps:
  - action: press_key
    args: {key: ENTER}
""",
        encoding="utf-8",
    )
    registry = WorkflowRegistry(root)
    router = ExecutionRouter(registry, SecurityGuard(), lambda _: True)
    match = WorkflowMatch(registry.get("demo"), 1.0)
    router.matcher.find = lambda *args, **kwargs: match
    router.run_workflow = lambda *args, **kwargs: (_ for _ in ()).throw(WorkflowExecutionError("postcondition uncertain"))
    router._computer_for_environment = lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("fallback must not run"))

    result = router.execute(
        "open demo",
        environment="desktop",
        model_name="not-used",
        platform="linux",
    )
    assert result.success is False
    assert result.outcome == "failed_or_unknown"
    assert "did not automatically replay" in result.message


def test_local_step_controller_must_authorize_each_next_step(tmp_path: Path):
    root = tmp_path / "workflows"
    root.mkdir()
    (root / "two_steps.yml").write_text(
        """
name: two_steps
description: Execute two key actions
steps:
  - action: press_key
    intent: Focus the application launcher
    args: {key: SUPER}
  - action: press_key
    intent: Confirm launch
    args: {key: ENTER}
""",
        encoding="utf-8",
    )
    workflow = WorkflowRegistry(root).get("two_steps")

    class Controller:
        calls = []
        def authorize_next_step(self, workflow, **kwargs):
            self.calls.append(kwargs)
            if kwargs["step_index"] == 1:
                return True, "first step authorized"
            return False, "task state is uncertain"
        def choose_recovery(self, workflow, **kwargs):
            return None

    controller = Controller()
    fake = FakeComputer()
    executor = WorkflowExecutor(SecurityGuard(), lambda _: True, step_controller=controller)
    try:
        executor.run_with_report(workflow, fake, task_query="launch the application", use_local_llm=True)
    except Exception as exc:
        assert "Local LLM stopped before step 2" in str(exc)
    else:
        raise AssertionError("controller denial must halt execution")
    assert [call["step_index"] for call in controller.calls] == [1, 2]
    assert fake.calls == [("key", "SUPER")]
    assert "latest_url" in controller.calls[1]
    assert "previous_steps" in controller.calls[1]


def test_local_controller_is_not_required_when_disabled(tmp_path: Path):
    root = tmp_path / "workflows"
    root.mkdir()
    (root / "one_step.yml").write_text(
        """
name: one_step
description: Execute one key action
steps:
  - action: press_key
    args: {key: ENTER}
""",
        encoding="utf-8",
    )
    workflow = WorkflowRegistry(root).get("one_step")

    class Controller:
        def authorize_next_step(self, workflow, **kwargs):
            raise AssertionError("controller should be disabled")

    fake = FakeComputer()
    report = WorkflowExecutor(SecurityGuard(), lambda _: True, step_controller=Controller()).run_with_report(
        workflow, fake, use_local_llm=False
    )
    assert report.outcome == "completed_unverified"
    assert fake.calls == [("key", "ENTER")]


def test_real_local_planner_step_gate_receives_text_only_context(tmp_path: Path):
    import json

    from src.jarvis.workflows.local_planner import WorkflowLocalPlanner
    from src.jarvis.workflows.schema import Workflow, WorkflowStep

    class FakeOllama:
        prompt = ""
        system = ""
        def generate(self, prompt, system="", **kwargs):
            self.prompt = prompt
            self.system = system
            return json.dumps({
                "decision": "execute_next",
                "step_index": 1,
                "confidence": 0.9,
                "reason": "The approved first step matches the task.",
            })

    workflow = Workflow(
        name="open_example",
        description="Open the example website",
        steps=[WorkflowStep(action="navigate", args={"url": "https://example.com"}, intent="Open example")],
    )
    ollama = FakeOllama()
    planner = WorkflowLocalPlanner(ollama, WorkflowRegistry(tmp_path / "empty"))  # type: ignore[arg-type]
    approved, reason = planner.authorize_next_step(
        workflow,
        step_index=1,
        total_steps=1,
        task_query="open example",
        previous_steps=[],
        latest_url="",
    )
    assert approved is True
    assert "Open example" in ollama.prompt
    assert "screenshot" not in ollama.prompt.lower()
    assert "accessibility tree" not in ollama.prompt.lower()
    assert "no screenshot" in ollama.system.lower()
    assert "accessibility tree" in ollama.system.lower()


def test_real_local_planner_rejects_attempt_to_reorder_steps(tmp_path: Path):
    import json

    from src.jarvis.workflows.local_planner import WorkflowLocalPlanner
    from src.jarvis.workflows.schema import Workflow, WorkflowStep

    class FakeOllama:
        def generate(self, prompt, system="", **kwargs):
            return json.dumps({
                "decision": "execute_next",
                "step_index": 99,
                "confidence": 1.0,
                "reason": "Attempted to skip ahead.",
            })

    workflow = Workflow(
        name="two_steps",
        description="Two steps",
        steps=[WorkflowStep(action="press_key", args={"key": "ENTER"})],
    )
    planner = WorkflowLocalPlanner(FakeOllama(), WorkflowRegistry(tmp_path / "empty"))  # type: ignore[arg-type]
    approved, reason = planner.authorize_next_step(
        workflow, step_index=1, total_steps=1, task_query="two steps", previous_steps=[]
    )
    assert approved is False
    assert "only step 1 is permitted" in reason


def test_unavailable_local_planner_does_not_bypass_with_lexical_workflow(tmp_path: Path):
    from src.jarvis.workflows.router import ExecutionRouter

    root = tmp_path / "workflows"
    root.mkdir()
    (root / "demo.yml").write_text(
        """
name: demo
description: Open demo page
steps:
  - action: press_key
    args: {key: ENTER}
""",
        encoding="utf-8",
    )
    registry = WorkflowRegistry(root)

    class UnavailablePlanner:
        last_reason = "local model unavailable"
        last_error = ""

        def choose(self, *args, **kwargs):
            return None

    router = ExecutionRouter(
        registry, SecurityGuard(), lambda _: True, local_planner=UnavailablePlanner()
    )
    router.matcher.find = lambda *args, **kwargs: (_ for _ in ()).throw(
        AssertionError("local-planner mode must not bypass to lexical workflow matching")
    )
    router.run_workflow = lambda *args, **kwargs: (_ for _ in ()).throw(
        AssertionError("workflow must not run without local planner authorization")
    )
    router._computer_for_environment = lambda *args, **kwargs: (_ for _ in ()).throw(
        RuntimeError("general computer-use path reached")
    )

    try:
        router.execute(
            "open demo",
            environment="desktop",
            model_name="not-used",
            platform="linux",
        )
    except RuntimeError as exc:
        assert str(exc) == "general computer-use path reached"
    else:
        raise AssertionError("expected the router to avoid workflow execution")


def test_local_planner_skips_slow_inference_for_weak_unparameterized_match(tmp_path: Path):
    import json

    from src.jarvis.workflows.local_planner import WorkflowLocalPlanner

    root = tmp_path / "workflows"
    (root / "examples" / "linux").mkdir(parents=True)
    (root / "examples" / "linux" / "open_firefox.yml").write_text(
        """
name: open_firefox
description: Open Firefox from the GNOME application overview
environment: desktop
platform: linux
tags: [firefox, browser, launch, application]
steps:
  - action: press_key
    args: {key: Super_L}
  - action: type
    args: {text: Firefox}
  - action: press_key
    args: {key: Return}
""",
        encoding="utf-8",
    )

    class FakeOllama:
        model = "qwen3.5:2b"
        generate_calls = 0

        def available(self):
            return True

        def generate(self, prompt, system="", **kwargs):
            self.generate_calls += 1
            return json.dumps({"workflow": None, "parameters": {}, "confidence": 0.0, "reason": "No match"})

    statuses = []
    ollama = FakeOllama()
    planner = WorkflowLocalPlanner(ollama, WorkflowRegistry(root), status_callback=statuses.append)  # type: ignore[arg-type]
    match = planner.choose("play a song on Firefox", environment="desktop", platform="linux")

    assert match is None
    assert ollama.generate_calls == 0
    assert "No close workflow match" in " ".join(statuses)
