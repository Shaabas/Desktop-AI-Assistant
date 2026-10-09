"""JARVIS product orchestration and security.

Heavy computer-use dependencies are intentionally loaded lazily so local-only
commands such as workflow inspection and security tests do not require the
Gemini SDK to be installed.
"""

from .security import SecurityGuard

__all__ = ["JarvisController", "JarvisComputerUseAgent", "SecurityGuard"]


def __getattr__(name: str):
    if name == "JarvisController":
        from .controller import JarvisController
        return JarvisController
    if name == "JarvisComputerUseAgent":
        from .computer_agent import JarvisComputerUseAgent
        return JarvisComputerUseAgent
    raise AttributeError(name)
