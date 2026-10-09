# JARVIS Architecture

```text
                  JARVIS daemon
                       │
        ┌──────────────┼──────────────┐
        │              │              │
     workflows      Gemini CUA      voice/TTS
        │              │              │
        └──────────────┼──────────────┘
                       │
                platform UI adapter
                       │
          ┌────────────┴────────────┐
          │                         │
       GNOME/Linux               Windows
          │                         │
    GNOME Shell surface       retained PySide HUD
```

## Linux surface boundary

The GNOME extension owns only the compositor-facing surface and D-Bus bridge. Python owns all intelligence and voice processing.

The active Linux surface is deliberately compact. It exposes a status line, a text entry field, a microphone request, and security confirmations. It does not render a conversation history or activity drawer.

## Voice ownership

`VoiceEngine` is the single STT/wake-word implementation. The GNOME extension does not record or transcribe audio itself.

## Computer-use isolation

The GNOME assistant surface is not forcibly hidden around screenshots; Deskwright uses the native GNOME Shell screenshot path. During computer-use execution the surface becomes non-reactive so injected pointer events pass through it.

## Hotkey ownership

GNOME Settings owns the Linux keyboard binding. `main.py --hotkey` only signals the already-running JARVIS process when one exists; otherwise it directly toggles the GNOME surface through the extension D-Bus contract. The daemon remains the owner of active-task cancellation.
