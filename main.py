"""Single JARVIS entrypoint.

Default Linux behavior is a headless daemon driven by the GNOME extension and
voice stack. Windows exposes the TUI by default. The old PySide HUD is retained
as inactive legacy source and is intentionally not part of the normal path.
"""
from __future__ import annotations

import faulthandler

try:
    # Include Python frames from all threads in systemd's journal if a native
    # extension crashes. This is diagnostic only and does not mask SIGSEGV.
    faulthandler.enable(all_threads=True)
except Exception:
    pass

import argparse
import platform
import sys

from src.jarvis.config import bootstrap_environment


def _query_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="JARVIS — Gemini Computer Use + local workflows")
    parser.add_argument("--query", required=True, help="Computer-use request")
    parser.add_argument("--env", choices=("desktop", "playwright", "browserbase"), default=None)
    parser.add_argument("--initial_url", default=None)
    parser.add_argument("--highlight_mouse", action="store_true")
    parser.add_argument("--annotate_grid", action="store_true")
    parser.add_argument("--no_indicator", action="store_true")
    parser.add_argument("--model", default=None)
    return parser


def run_computer_use(args: argparse.Namespace) -> int:
    settings = bootstrap_environment()
    from src.jarvis.agent.controller import JarvisController

    environment = args.env or ("desktop" if platform.system().lower() != "windows" else "playwright")
    model = args.model or settings.get("cloud_computer_use_model") or "gemini-3.5-flash-lite"
    controller = JarvisController(status_callback=lambda message: print(f"[JARVIS] {message}"))

    # Standalone CUA deliberately bypasses the normal intent/workflow router:
    # this flag means "run Computer Use now".
    from src.jarvis.computer_use.computers import BrowserbaseComputer, DeskwrightComputer, PlaywrightComputer
    from src.jarvis.workflows.secure import SecureComputerProxy
    from src.jarvis.workflows.trace import TracingComputer
    from src.jarvis.agent.computer_agent import JarvisComputerUseAgent

    if environment == "desktop":
        if platform.system().lower() == "windows":
            print("[JARVIS] --env desktop requires a Linux Wayland/GNOME session with Deskwright.")
            return 2
        computer = DeskwrightComputer(
            initial_url=args.initial_url,
            highlight_mouse=args.highlight_mouse,
            annotate_grid=args.annotate_grid,
            enable_indicator=not args.no_indicator,
        )
    elif environment == "playwright":
        computer = PlaywrightComputer(
            screen_size=(1440, 900),
            initial_url=args.initial_url or "https://www.google.com",
            highlight_mouse=args.highlight_mouse,
        )
    else:
        if BrowserbaseComputer is None:
            print("[JARVIS] Browserbase support is not installed. Install the browserbase extra.")
            return 2
        computer = BrowserbaseComputer(
            screen_size=(1440, 900),
            initial_url=args.initial_url or "https://www.google.com",
        )

    with computer as active:
        tracing = TracingComputer(active)
        secure = SecureComputerProxy(tracing, controller.security, controller.confirmation_callback)
        agent = JarvisComputerUseAgent(
            browser_computer=secure,
            query=args.query,
            model_name=model,
            verbose=True,
            confirmation_callback=controller.confirmation_callback,
        )
        agent.agent_loop()
        if getattr(agent, "security_aborted", False):
            print("[JARVIS] Sensitive action blocked; pending actions were not executed.")
            return 2
        if getattr(agent, "loop_guard_aborted", False):
            print(f"[JARVIS] Computer-use stopped safely: {agent.final_reasoning}")
            return 3
    return 0


def run_cli() -> int:
    bootstrap_environment()
    from src.jarvis.agent.controller import JarvisController

    controller = JarvisController()
    print("\nJARVIS TUI — type 'exit' to quit, '/workflows' to list workflows.\n")
    while True:
        try:
            query = input("JARVIS ❯ ").strip()
        except (KeyboardInterrupt, EOFError):
            print()
            return 0
        if not query:
            continue
        if query.lower() in {"exit", "quit", "q"}:
            return 0
        if query == "/workflows":
            for name in sorted(controller.workflow_registry.names()):
                print(f"  {name}")
            continue
        try:
            print(controller.process_request(query))
        except Exception as exc:
            print(f"[JARVIS] Error: {exc}")


def run_prompt(prompt: str) -> int:
    bootstrap_environment()
    from src.jarvis.agent.controller import JarvisController
    controller = JarvisController()
    try:
        print(controller.process_request(prompt))
        return 0
    except Exception as exc:
        print(f"[JARVIS] Error: {exc}")
        return 1


def run_hotkey() -> int:
    """Signal the already-running JARVIS daemon. Never start another instance."""
    from src.jarvis.input.hotkey import send_hotkey_trigger

    return 0 if send_hotkey_trigger() else 1


def main() -> int:
    argv = sys.argv[1:]

    if "--help" in argv or "-h" in argv:
        print(
            """JARVIS

Normal modes:
  python main.py                 Start the headless JARVIS service on Linux; TUI on Windows.
  python main.py --cli           Start the interactive TUI.
  python main.py --hotkey        Toggle the assistant surface; if a task is active, stop it and close the surface.
  python main.py --install-linux-service
                                 Install and start the Linux user-level JARVIS service.
  python main.py --hud           Reserved legacy PySide HUD path; retained for the Windows adapter phase.

Computer Use:
  python main.py --query TEXT [--env desktop|playwright|browserbase]
  python main.py --prompt TEXT   Route a request through JARVIS.

Workflows:
  python main.py --list-workflows
  python main.py --workflow-review NAME       Show YAML and validation errors before approval
  python main.py --workflow-approve NAME      Explicitly approve a reviewed, schema-valid workflow
  python main.py --workflow-adapt NAME --request TEXT
"""
        )
        return 0

    if "--hotkey" in argv:
        return run_hotkey()
    if "--install-linux-service" in argv:
        if platform.system().lower() == "windows":
            print("[JARVIS] --install-linux-service is only available on Linux.")
            return 2
        from pathlib import Path
        import os
        import subprocess
        project_root = Path(__file__).resolve().parent
        template = project_root / "systemd" / "user" / "jarvis.service.in"
        service_dir = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "systemd" / "user"
        service_dir.mkdir(parents=True, exist_ok=True)
        service_path = service_dir / "jarvis.service"
        unit_text = template.read_text(encoding="utf-8")
        unit_text = unit_text.replace("__PROJECT_ROOT__", str(project_root))
        unit_text = unit_text.replace("__PYTHON_EXECUTABLE__", sys.executable)
        service_path.write_text(unit_text, encoding="utf-8")
        subprocess.run(["systemctl", "--user", "daemon-reload"], check=True)
        subprocess.run(["systemctl", "--user", "enable", "--now", "jarvis.service"], check=True)
        print(f"[JARVIS] Installed and started {service_path}")
        return 0

    # Normal JARVIS modes initialize the application environment only after
    # the lightweight control commands above have been handled.
    bootstrap_environment()

    if "--query" in argv or any(a.startswith("--query=") for a in argv):
        parser = _query_parser()
        return run_computer_use(parser.parse_args(argv))
    if "--cli" in argv:
        return run_cli()
    if "--prompt" in argv:
        parser = argparse.ArgumentParser()
        parser.add_argument("--prompt", required=True)
        return run_prompt(parser.parse_args(argv).prompt)
    if "--workflow-review" in argv:
        parser = argparse.ArgumentParser()
        parser.add_argument("--workflow-review", dest="workflow", required=True)
        args = parser.parse_args(argv)
        from pathlib import Path
        from src.jarvis.agent.controller import JarvisController
        controller = JarvisController()
        workflow = controller.workflow_registry.get(args.workflow)
        if workflow is None:
            print(f"[JARVIS] Unknown workflow: {args.workflow}")
            return 2
        print(f"Name: {workflow.name}")
        print(f"Description: {workflow.description}")
        print(f"Status: {workflow.status or 'unapproved'}")
        print(f"Runnable: {'yes' if workflow.is_runnable else 'no'}")
        errors = controller.workflow_registry.validation_errors(workflow.name)
        if errors:
            print("Validation: FAILED")
            for error in errors:
                print(f"  - {error}")
        else:
            print("Validation: passed (schema only; behavior is not proven)")
        print("\n--- Workflow YAML ---")
        if workflow.path and Path(workflow.path).is_file():
            print(Path(workflow.path).read_text(encoding="utf-8"))
        else:
            print("[JARVIS] Workflow source file is unavailable.")
            return 2
        print("\nReview coordinate assumptions and possible side effects before enabling this workflow.")
        return 0

    if "--workflow-approve" in argv:
        parser = argparse.ArgumentParser()
        parser.add_argument("--workflow-approve", dest="workflow", required=True)
        args = parser.parse_args(argv)
        from src.jarvis.agent.controller import JarvisController
        from src.jarvis.workflows.registry import WorkflowFormatError
        controller = JarvisController()
        try:
            workflow = controller.workflow_registry.approve(args.workflow)
        except (KeyError, WorkflowFormatError) as exc:
            print(f"[JARVIS] Approval failed: {exc}")
            return 2
        print(f"[JARVIS] Approved workflow '{workflow.name}'. It is now eligible for automatic matching.")
        print("[JARVIS] Schema validation passed; test the workflow behavior in a safe context.")
        return 0

    if "--workflow-adapt" in argv:
        parser = argparse.ArgumentParser()
        parser.add_argument("--workflow-adapt", dest="workflow", required=True)
        parser.add_argument("--request", required=True)
        args = parser.parse_args(argv)
        from src.jarvis.agent.controller import JarvisController
        controller = JarvisController()
        print(controller.adapt_workflow(args.workflow, args.request))
        return 0
    if "--list-workflows" in argv:
        from src.jarvis.agent.controller import JarvisController
        controller = JarvisController()
        for workflow in sorted(controller.workflow_registry.all(), key=lambda x: x.name):
            errors = controller.workflow_registry.validation_errors(workflow.name)
            status = workflow.status or ("runnable" if workflow.is_runnable else "unapproved")
            validation = "invalid" if errors else "schema-ok"
            print(f"{workflow.name}\tstatus={status}\t{validation}\t{workflow.description}")
        print("\nReview candidates with: python main.py --workflow-review NAME")
        print("Approve a reviewed workflow with: python main.py --workflow-approve NAME")
        return 0
    if "--hud" in argv:
        print(
            "The legacy PySide HUD is intentionally inactive in the merged architecture. "
            "Its sources are retained under legacy/hud/ for later reactivation."
        )
        return 0

    # Windows receives a TUI by default. Linux starts the headless service used
    # by the GNOME Shell extension.
    if platform.system().lower() == "windows":
        return run_cli()

    # Acquire the singleton before constructing JarvisDaemon, because its
    # constructor loads the speech and TTS models and can consume substantial
    # memory. This also prevents `python main.py` from duplicating systemd's
    # resident service and loading every model a second time.
    from src.jarvis.input.hotkey import acquire_daemon_lock, release_daemon_lock

    if not acquire_daemon_lock():
        print("[JARVIS] Another daemon instance is already running; use the configured hotkey to control it.")
        return 1
    try:
        from src.jarvis.app.daemon import JarvisDaemon
        JarvisDaemon().start()
        return 0
    finally:
        release_daemon_lock()


if __name__ == "__main__":
    raise SystemExit(main())
