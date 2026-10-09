# JARVIS

**JARVIS is a desktop AI assistant.** Gemini Computer Use is the primary autonomous execution engine, while reusable YAML workflows provide a deterministic, local fast path for repeated tasks.

## Overview

- **Autonomous desktop execution:** Send tasks to Gemini Computer Use.
- **Reusable workflows:** JARVIS checks YAML workflows before invoking Gemini.
- **Workflow adaptation:** A local Ollama model can adapt existing workflows.
- **GNOME integration:** A GNOME Shell extension provides the Linux-facing assistant surface.
- **Protected actions:** Confirmation controls are available for sensitive actions.
- **Voice input:** Wake-word detection and speech-to-text are handled by the Python runtime.

## Contents

- [Run modes](#run-modes)
- [Configuration](#configuration)
- [GNOME integration](#gnome-integration)
- [Custom keyboard shortcut](#custom-keyboard-shortcut)
- [Linux user service](#linux-user-service)
- [Workflow learning](#workflow-learning)
- [Windows](#windows)
- [License](#license)

## Run modes

Run commands from the project directory, using the Python environment in which JARVIS's dependencies are installed.

```bash
# Linux: start the persistent runtime used by the GNOME assistant surface
python main.py

# Terminal-only mode
python main.py --cli

# GNOME custom shortcut target: open/close the assistant or stop a running task
python main.py --hotkey

# Install the Linux user service
python main.py --install-linux-service

# Direct Gemini Computer Use
python main.py --query "open Firefox and search for Fedora news" --env desktop
python main.py --query "search Google for Fedora" --env playwright

# One-shot JARVIS request
python main.py --prompt "open Firefox"

# Inspect and adapt workflows
python main.py --list-workflows
python main.py --workflow-adapt open_firefox --request "adapt this for Chromium"

# Legacy Windows HUD source is retained under legacy/hud
python main.py --hud
```

## Configuration

### API credentials

Store credentials locally in `config/api_keys.json`. This file is intended to be ignored by Git; **never commit API keys or other secrets**. Keep a sanitized example configuration in the repository if you want to document the required fields.

### Local model

JARVIS uses Ollama for its local model. The default model configuration is in `config/settings.json`. Make sure Ollama is installed and the configured model is available before using features that depend on it.

## GNOME integration

The supplied GNOME extension is the Linux-facing JARVIS assistant surface. Install it with:

```bash
./gnome-extension/install.sh
```

Then enable `deskwright@zeticle.com` in GNOME Extensions.

The extension provides:

- A compact, single-line status surface
- Text commands
- Python-managed wake-word and speech-to-text microphone input
- Live thinking, working, speaking, and listening states
- Confirmation controls for protected actions
- Existing compositor-side Deskwright capabilities

The extension does **not** register its own keyboard shortcut. Configure a custom shortcut in GNOME Settings instead.

## Custom keyboard shortcut

Open **GNOME Settings → Keyboard → View and Customize Shortcuts → Custom Shortcuts** and add a shortcut that runs JARVIS with `--hotkey`.

Use absolute paths. For example, if your project is in `/home/your-user/Projects/Desktop-AI-Assistant` and you use a project virtual environment:

```bash
/home/your-user/Projects/Desktop-AI-Assistant/.venv/bin/python /home/your-user/Projects/Desktop-AI-Assistant/main.py --hotkey
```

Replace both paths with your actual checkout and Python interpreter paths. The interpreter must have the project's dependencies installed.

The hotkey has the following behavior:

| Assistant state | Hotkey action |
|---|---|
| Closed | Open the assistant |
| Open | Close the assistant |
| Busy | Stop/cancel the task and close the assistant |

A running task keeps the compositor halt barrier engaged until the task has actually unwound, preventing post-cancellation desktop input.

## Linux user service

JARVIS is designed to run as a persistent user service so the shortcut can signal a live runtime. Install the service with:

```bash
python main.py --install-linux-service
```

The daemon stores its PID at `$XDG_RUNTIME_DIR/jarvis/jarvis.pid`.

## Workflow learning

JARVIS checks YAML workflows before invoking Gemini. A successful Gemini Computer Use trace can be compiled into a reusable workflow under `workflows/learned/`. The local Ollama model can then adapt workflows, reducing the need to use Gemini for every repeated task.

The `workflows/learned/` directory may contain personal or machine-specific data. Keep private learned workflows out of public commits; use sanitized examples for documentation and tests.

## Windows

The legacy PySide HUD source is retained under `legacy/hud`. The intended Windows hotkey design uses a small hidden Qt host with the native Windows `RegisterHotKey` / `WM_HOTKEY` mechanism, mapped to the same JARVIS state machine. Treat this as planned behavior unless it has been implemented and tested in your checkout.

## License

This project is intended to use the Apache License 2.0. See [`LICENSE`](LICENSE) for the license text. Third-party components may have separate terms; see [`third_party_licenses/`](third_party_licenses/) and preserve the relevant notices.