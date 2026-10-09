# Merge Notes

Source projects:

- Desktop-AI-Assistant: JARVIS product shell, voice stack, local Ollama support,
  TTS, security boundary, legacy HUD.
- Google Computer Use Preview: authoritative Gemini Computer Use engine and its
  working Deskwright / Playwright / Browserbase computers.
- GNOME extension: compositor-side interface used by Deskwright.
- UI-Automata: architectural reference for the YAML workflow subsystem.

Google Computer Use upstream snapshot used for integration:
`77c9797e943aad63bbc963b7fd092a9e51c07863`

The integrated Computer Use source was copied from the uploaded working tree;
only namespace imports were changed so it lives inside `src/jarvis/computer_use`.
Its internal action protocol and computer implementations were otherwise kept
intact.
