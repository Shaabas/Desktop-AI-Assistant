"""
src/jarvis/agent/security.py — Security Boundary Guard for Desktop AI Assistant.
Evaluates TaskSteps and direct terminal/motor inputs for security implications,
blocking deadly/destructive commands and requiring confirmation for risky operations.
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Tuple



class SecurityGuard:
    """Security Boundary Guard evaluating task safety and enforcing deadly command blocks."""

    # Absolute hard blocks: commands that cause catastrophic, irreversible system/data damage
    DEADLY_PATTERNS: List[Tuple[str, str]] = [
        # 1. Fork bombs
        (r":\(\)\s*\{\s*:(\|:)?&\s*\};:", "Fork bomb pattern detected."),
        # 2. Catastrophic filesystem deletion (rm -rf /, rm -rf /*, rm -rf ~, rm -rf $HOME, rm -rf ./*, rm -rf *)
        (r"\brm\s+(-[a-zA-Z0-9]*r[a-zA-Z0-9]*f*|-rf|-fr)\s+((--no-preserve-root\s+)?(/|/\*|~|~/\*|\$HOME(/\S*)?|/home(/\S*)?|/root(/\S*)?|/etc(/\S*)?|/usr(/\S*)?|/var(/\S*)?|/boot(/\S*)?|\.\.|\./\*|\*))(\s+|$)", "Catastrophic recursive deletion targeting root, home, or critical system directories."),
        (r"\brm\s+.*--no-preserve-root", "Destructive deletion with --no-preserve-root flag."),
        # 3. Disk & block device formatting / raw wiping
        (r"\b(mkfs(\.[a-zA-Z0-9]+)?|wipefs|blkdiscard|gdisk|fdisk|parted|sfdisk)\b", "Direct disk partition or filesystem formatting command."),
        (r"\bdd\s+.*\bof=/dev/(sd[a-z]|nvme[0-9]|vd[a-z]|hd[a-z]|mapper/|loop[0-9])", "Raw block device overwrite via dd."),
        (r"\bshred\s+.*(/dev/(sd[a-z]|nvme[0-9]|vd[a-z]|hd[a-z]))", "Raw disk shredding command."),
        (r">\s*/dev/(sd[a-z]|nvme[0-9]|vd[a-z]|hd[a-z]|mapper/|mem|kmem|port)", "Direct overwrite redirection to system device or kernel memory."),
        # 4. Kernel and critical system configuration tampering
        (r">\s*/boot/", "Direct overwrite redirection targeting /boot."),
        (r">\s*/etc/(passwd|shadow|fstab|sudoers|pam\.d)", "Direct overwrite targeting critical authentication files."),
        (r">\s*/proc/sysrq-trigger", "Kernel SysRq trigger tampering."),
        (r"\bmv\s+(/|/\*|~|\$HOME)(\s+|$)", "Attempting to move root or home directory."),
        # 5. Remote shell piping without verification
        (r"\b(curl|wget)\s+.*\|\s*(sudo\s+)?(ba)?sh\b", "Blindly piping remote network payload into shell."),
        # 6. Catastrophic permission destruction
        (r"\bchmod\s+(-[a-zA-Z0-9]*R[a-zA-Z0-9]*\s+)?(777|000)\s+((/|/\*|~|~/\*|\$HOME(/\S*)?|/home(/\S*)?|/root(/\S*)?|/etc(/\S*)?))(\s+|$)", "Recursive permission destruction on root or home."),
        (r"\bchown\s+-[a-zA-Z0-9]*R[a-zA-Z0-9]*\s+.*\s+((/|/\*|~|~/\*|\$HOME(/\S*)?|/home(/\S*)?|/root(/\S*)?|/etc(/\S*)?))(\s+|$)", "Recursive ownership stripping on root or home."),
        # 7. Hostile system shutdown / reboot / halt
        (r"\b(shutdown|poweroff|reboot|halt)\b", "System power state modification."),
        (r"\binit\s+[06]\b", "System init runlevel change."),
        (r"\bsystemctl\s+(poweroff|reboot|halt)\b", "Systemd power state alteration."),
        # 8. Killing init or compositor
        (r"\bkill\s+(-9|-KILL)\s+1\b", "Attempting to kill init process (PID 1)."),
        (r"\bpkill\s+(-9|-KILL)\s+(systemd|init)\b", "Attempting to kill systemd or init."),
    ]

    # Operations requiring explicit confirmation
    DANGEROUS_TERMINAL_PATTERNS = [
        r"\brm\s+-rf\b",
        r"\bsudo\b",
        r"\bdd\b",
        r"\bmkfs\b",
        r"\bshutdown\b",
        r"\breboot\b",
        r"\bchmod\s+777\b",
        r"\bchown\b",
        r">\s*/dev/sd",
    ]

    def is_deadly_command(self, command: str) -> Tuple[bool, str]:
        """
        Evaluates whether a command contains catastrophic, irreversible system/data threats.
        Returns (is_deadly, reason).
        """
        if not command or not command.strip():
            return False, ""

        clean_cmd = command.strip()

        for pattern, reason in self.DEADLY_PATTERNS:
            if re.search(pattern, clean_cmd, re.IGNORECASE):
                return True, reason

        return False, ""

    def evaluate_text_input(self, text: str) -> Tuple[bool, str]:
        """Detect risky shell-like text before a computer agent types it.

        This is intentionally narrower than terminal execution policy: it is an
        additional boundary for visual computer-use agents that may type into a
        terminal emulator.
        """
        if not text or not text.strip():
            return False, ""
        deadly, reason = self.is_deadly_command(text)
        if deadly:
            return True, reason
        lower = text.lower()
        for pattern in self.DANGEROUS_TERMINAL_PATTERNS:
            if re.search(pattern, lower):
                return True, f"Potentially dangerous command text matched '{pattern}'."
        return False, ""

    def evaluate_step(self, step: Any) -> Dict[str, Any]:
        tool_name = step.tool_name
        params = step.parameters

        if tool_name == "terminal_control":
            cmd = str(params.get("command", "")).strip()

            is_deadly, deadly_reason = self.is_deadly_command(cmd)
            if is_deadly:
                return {
                    "requires_confirmation": True,
                    "is_deadly": True,
                    "reason": f"DEADLY COMMAND BLOCKED: {deadly_reason}",
                }

            cmd_lower = cmd.lower()
            for pattern in self.DANGEROUS_TERMINAL_PATTERNS:
                if re.search(pattern, cmd_lower):
                    return {
                        "requires_confirmation": True,
                        "is_deadly": False,
                        "reason": f"Terminal command '{cmd}' contains potentially dangerous pattern '{pattern}'.",
                    }
            if step.parameters.get("requires_confirmation", True):
                return {
                    "requires_confirmation": True,
                    "is_deadly": False,
                    "reason": "Terminal execution requires explicit user authorization.",
                }

        elif tool_name == "os_controls":
            action = str(params.get("action", "")).lower()
            if action in {"power_off", "shutdown", "reboot"}:
                return {
                    "requires_confirmation": True,
                    "is_deadly": True,
                    "reason": f"System action '{action}' will change system power state.",
                }

        elif tool_name == "file_controller":
            action = str(params.get("action", "")).lower()
            if action in {"delete_file", "remove_directory", "format"}:
                return {
                    "requires_confirmation": True,
                    "is_deadly": False,
                    "reason": f"File action '{action}' performs destructive file deletion.",
                }

        return {"requires_confirmation": False, "is_deadly": False, "reason": "Action is safe."}


_SECURITY_GUARD_INSTANCE: Optional[SecurityGuard] = None


def get_security_guard() -> SecurityGuard:
    global _SECURITY_GUARD_INSTANCE
    if _SECURITY_GUARD_INSTANCE is None:
        _SECURITY_GUARD_INSTANCE = SecurityGuard()
    return _SECURITY_GUARD_INSTANCE
