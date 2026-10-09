# JARVIS pointer label

JARVIS displays a small pill containing only `JARVIS` next to the mouse pointer while a `DeskwrightComputer` desktop-control context is active.

## Design

- Implemented as a non-reactive child of `global.stage`, following the rendering pattern used by Hati. GNOME Shell composites it above Wayland windows.
- The pill is non-reactive so it should not intercept mouse input.
- The pill uses a fully rounded (`border-radius: 999px`) dark background with a subtle outline and light text.
- It follows `global.get_pointer()` with an offset, moving to the opposite side near the stage edges. The label tracks Mutter’s `Meta.CursorTracker` visibility and cursor-sprite signals; it hides when the native pointer is hidden and reappears when the pointer becomes visible again.
- It remains visible while the agent is thinking, waiting or carrying out actions inside the computer-control session.

## Lifecycle and failure handling

Python activates the actor through `com.zeticle.deskwright.SetPointerLabelActive(true, ttlSeconds)` when the computer context is ready. A background heartbeat renews the 45-second lease every 20 seconds. Context exit stops the heartbeat and explicitly deactivates the pill. If JARVIS crashes or gets killed before cleanup, GNOME Shell removes the actor when the lease expires. The actor also destroys itself when its lease expires, and the GNOME extension destroys it when disabled. Activating the pill is refused while the emergency halt is engaged.

The label is optional. If the running GNOME Shell extension has not yet reloaded and does not expose the new D-Bus method, JARVIS logs at debug level and continues without the pill. The D-Bus `Ping` build value is bumped to `2026-10-09.2` to make the running build easy to identify.

## Install / reload on Wayland

From the JARVIS project root:

```bash
gnome-extension/install.sh
```

After editing a GNOME Shell extension on Wayland, log out of GNOME and log back in so GNOME Shell loads the new JavaScript module. Then verify the active service with:

```bash
gdbus call --session --dest com.zeticle.deskwright \
  --object-path /com/zeticle/deskwright \
  --method com.zeticle.deskwright.Ping
```

The result should include build `2026-10-09.2` and the `SetPointerLabelActive` method. A brief manual check can show and clear the label:

```bash
gdbus call --session --dest com.zeticle.deskwright \
  --object-path /com/zeticle/deskwright \
  --method com.zeticle.deskwright.SetPointerLabelActive true 45.0

# Clear it again:
gdbus call --session --dest com.zeticle.deskwright \
  --object-path /com/zeticle/deskwright \
  --method com.zeticle.deskwright.SetPointerLabelActive false 45.0
```

## Validation scope

Unit tests validate the D-Bus method signature, uppercase text-only styling, non-reactive actor configuration, pointer positioning, cursor-visibility tracking and Python session cleanup wiring. Live rendering, exact placement on multi-monitor layouts, and click-through behavior must still be verified in the target GNOME Shell session.
