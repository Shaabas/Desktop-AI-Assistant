"""Authoritative Gemini Computer Use engine used by JARVIS.

This package contains the working Google Computer Use Preview implementation.
Imports are intentionally lazy at package level so the JARVIS workflow and
configuration layers can operate without importing the optional Gemini SDK.
"""

__all__ = [
    "BrowserAgent",
    "ComputerAgent",
    "Computer",
    "EnvState",
    "BrowserbaseComputer",
    "DeskwrightComputer",
    "PlaywrightComputer",
]


def __getattr__(name: str):
    if name in {"BrowserAgent", "ComputerAgent"}:
        from .agent import BrowserAgent, ComputerAgent
        return {"BrowserAgent": BrowserAgent, "ComputerAgent": ComputerAgent}[name]
    if name in {"Computer", "EnvState", "BrowserbaseComputer", "DeskwrightComputer", "PlaywrightComputer"}:
        from .computers import (
            BrowserbaseComputer,
            Computer,
            DeskwrightComputer,
            EnvState,
            PlaywrightComputer,
        )
        return {
            "Computer": Computer,
            "EnvState": EnvState,
            "BrowserbaseComputer": BrowserbaseComputer,
            "DeskwrightComputer": DeskwrightComputer,
            "PlaywrightComputer": PlaywrightComputer,
        }[name]
    raise AttributeError(name)
