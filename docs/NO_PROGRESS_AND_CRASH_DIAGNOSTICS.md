# Computer Use no-progress guard and crash diagnostics

## Repeated actions

The Computer Use agent now stops before dispatching an action when the first actionable call reaches its third attempt with the same normalized signature. The signature ignores natural-language intent/reasoning fields and groups nearby normalized coordinates into 25-point buckets, so tiny coordinate jitter does not bypass the guard. Wait, move, hover, and screenshot-only calls neither increment nor reset the streak, so inserting a wait between repeated clicks cannot evade the guard.

The check runs **after** the existing sensitive-action confirmation preflight and **before** dispatching any action in that response. Therefore it cannot cause a sensitive action to run before its confirmation. If it trips, the agent terminates that attempt, returns an explicit `stalled` result, and does not save the looping action trace as a workflow candidate. The tool-response code also now sends an explicit error response when a computer adapter returns an unsupported/empty result instead of silently omitting the tool response from Gemini's conversation history.

This is a fail-safe. It deliberately stops rather than guessing what alternative action would be correct.

## Native segmentation faults

`main.py` enables Python's `faulthandler` for all threads so Python stacks are written to the systemd journal when a native extension crashes. This improves the next crash report; it does **not** itself fix a native `SIGSEGV`.

To collect the native backtrace for a core dump on Fedora:

```bash
coredumpctl debug <PID>
```

At the GDB prompt, run:

```gdb
set pagination off
thread apply all bt full
```

The native crash should be considered unresolved until the backtrace identifies the faulting library/frame. Do not treat the ONNX Runtime provider warning or a resource-tracker warning alone as proof of the cause.
