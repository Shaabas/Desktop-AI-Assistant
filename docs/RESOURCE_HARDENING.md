# JARVIS resource hardening

This patch addresses the resource-management paths identified during the Fedora service investigation. It is intentionally conservative: it bounds queues and retry frequency rather than imposing a broad process memory cap.

## Changes

- **Bounded microphone queues.** Wake-word and speech capture each use an eight-chunk queue. When full, the callback discards the oldest audio block and keeps the newest one. The callback never waits for queue space. At 16 kHz this limits queued audio to roughly 640 ms for wake-word capture and 800 ms for speech capture.
- **Audio stream health checks.** Five consecutive PortAudio input-overflow callbacks cause the consumer to stop using that stream. A capture loop also treats more than three seconds without a callback as a stalled stream. The stream context is then closed and the failure is reported to the recovery policy.
- **Bounded retry backoff.** Repeated stream-open, callback-stall, overflow, or capture errors are retried after 0.5, 1, 2, 4, 8, 16 and then at most 30 seconds. A healthy stream resets the failure counter. Waits are checked in short intervals so shutdown/cancellation remains responsive.
- **Explicit stream cancellation.** `VoiceEngine.cancel_recording()` signals the consumer and attempts to abort an active PortAudio stream. The stream context still owns final cleanup.
- **Safe ALSA callback lifetime.** The `ctypes` callback passed to `snd_lib_error_set_handler` is kept in a module-global reference, avoiding a dangling C callback pointer after garbage collection.
- **TTS subprocess cleanup.** External TTS playback processes are terminated and waited for; processes that do not exit within the timeout are killed, then waited for again. The active-process reference is cleared conditionally so a finishing child cannot overwrite a newer process reference.
- **Bounded command backlog.** JARVIS retains at most eight queued user requests and rejects a request longer than 32,768 characters. A full queue rejects the newest request instead of growing without bound.
- **Worker shutdown.** The daemon tracks its worker threads and joins them for a bounded period on shutdown. It reports any workers that fail to stop within that deadline.
- **Daemon singleton.** The default Linux daemon path obtains an advisory `flock` before constructing the daemon and loading Whisper/TTS models. A second manual invocation exits instead of loading another copy of the models. Use the configured hotkey to control the already-running service. The zero-byte lock file may remain after exit; the lock itself is released by closing the descriptor.
- **Service installer fix.** `scripts/install-user-service.sh` now substitutes both `__PROJECT_ROOT__` and `__PYTHON_EXECUTABLE__`, and fails if a placeholder remains unresolved.

## Validation

Run the project's tests with:

```bash
python -m pytest -q
python -m py_compile main.py src/jarvis/input/audio_resources.py src/jarvis/input/speech.py src/jarvis/app/daemon.py src/jarvis/models/tts.py src/jarvis/models/process_utils.py src/jarvis/input/hotkey.py
bash -n scripts/install-user-service.sh
```

The automated tests cover queue bounds/latest-audio behavior, overflow streak detection, exponential retry caps, subprocess reaping, singleton-lock behavior and service-template substitution.

## Fedora rollout and verification

After applying the patch, restart the existing service:

```bash
systemctl --user restart jarvis.service
systemctl --user status jarvis.service
journalctl --user -u jarvis.service -f
```

Test normal wake-word recognition and a few spoken commands. The patch does not prove that it resolves the historical 9.5 GB OOM event because that event's precise allocation path was not captured. Monitor these values during activity:

```bash
PID=$(systemctl --user show --property=MainPID --value jarvis.service)
CGROUP=$(awk -F: '$1 == "0" {print $3}' /proc/"$PID"/cgroup)
cat "/sys/fs/cgroup${CGROUP}/memory.current"
grep -E '^(anon|file|kernel) ' "/sys/fs/cgroup${CGROUP}/memory.stat"
```

The prior idle samples showed stable anonymous memory; the next validation must include active voice capture and recovery from an audio-device failure. If memory begins climbing sharply again, stop the service and preserve the journal and kernel OOM report before restarting it.

Live ALSA/PortAudio recovery has not been exercised in this build environment because it has no microphone device. Unit tests validate the bounded policy and lifecycle helpers, not the hardware-specific behavior of Fedora's current audio stack.
