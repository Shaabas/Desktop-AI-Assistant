/*
 * Minimal, click-through pointer pill for JARVIS desktop-control sessions.
 * The pill deliberately contains only "JARVIS" and follows the GNOME Shell
 * pointer in compositor coordinates, so it works for native Wayland windows.
 */

import GLib from 'gi://GLib';
import Meta from 'gi://Meta';
import St from 'gi://St';

const DEFAULT_TTL_SECONDS = 45;
const MIN_TTL_SECONDS = 5;
const POINTER_POLL_MS = 16;
const POINTER_OFFSET_X = 18;
const POINTER_OFFSET_Y = 18;
const EDGE_PADDING = 8;

export class PointerLabel {
    constructor() {
        this._actor = null;
        this._tickId = 0;
        this._expiresAt = 0;
        this._lastX = null;
        this._lastY = null;
        this._lastWidth = null;
        this._lastHeight = null;
        this._cursorTracker = null;
        this._visibilityChangedId = 0;
        this._cursorChangedId = 0;
        this._positionInvalidatedId = 0;
        this._lastCursorVisible = null;
    }

    show(ttlSeconds = DEFAULT_TTL_SECONDS) {
        const ttl = Number.isFinite(Number(ttlSeconds))
            ? Math.max(MIN_TTL_SECONDS, Number(ttlSeconds))
            : DEFAULT_TTL_SECONDS;
        this._expiresAt = GLib.get_monotonic_time() + ttl * 1000000;

        if (!this._actor)
            this._createActor();

        this._connectCursorTracker();
        this._syncCursorVisibility();
        this._updatePosition();
        if (!this._tickId) {
            this._tickId = GLib.timeout_add(
                GLib.PRIORITY_DEFAULT,
                POINTER_POLL_MS,
                () => this._onTick()
            );
        }
        return true;
    }

    hide() {
        this._disconnectCursorTracker();
        if (this._tickId) {
            GLib.source_remove(this._tickId);
            this._tickId = 0;
        }
        this._destroyActor();
        this._expiresAt = 0;
        return true;
    }

    destroy() {
        return this.hide();
    }

    _createActor() {
        const label = new St.Label({
            text: 'JARVIS',
            style: 'color: #F8FAFC; font-size: 11px; font-weight: 650;',
            reactive: false,
            can_focus: false,
        });
        this._actor = new St.BoxLayout({
            vertical: false,
            reactive: false,
            can_focus: false,
            style:
                'background-color: rgba(27, 33, 44, 0.96);' +
                'border: 1px solid rgba(148, 163, 184, 0.48);' +
                'border-radius: 999px;' +
                'padding: 4px 11px;' +
                'spacing: 0px;',
        });
        this._actor.set_name('jarvis-pointer-pill');
        this._actor.add_child(label);
        this._actor.set_reactive(false);
        // Hati uses the same stage-overlay pattern for its cursor highlight.
        // A non-reactive stage actor follows the pointer above Wayland windows
        // without becoming a surface that could consume the user's clicks.
        global.stage.add_child(this._actor);
    }

    _destroyActor() {
        if (!this._actor)
            return;
        try {
            global.stage.remove_child(this._actor);
        } catch (e) {
            // The actor may already have been detached during Shell teardown.
        }
        try {
            this._actor.destroy();
        } catch (e) {
            // Teardown should remain idempotent.
        }
        this._actor = null;
        this._lastX = null;
        this._lastY = null;
        this._lastWidth = null;
        this._lastHeight = null;
    }

    _onTick() {
        if (!this._actor) {
            this._tickId = 0;
            return GLib.SOURCE_REMOVE;
        }

        if (GLib.get_monotonic_time() >= this._expiresAt) {
            this._disconnectCursorTracker();
            this._destroyActor();
            this._tickId = 0;
            this._expiresAt = 0;
            return GLib.SOURCE_REMOVE;
        }

        // Some applications and idle-cursor utilities hide the native cursor
        // without moving it. Keep the pill in sync with Mutter's actual cursor
        // visibility state rather than leaving a detached label on screen.
        this._syncCursorVisibility();
        this._updatePosition();
        return GLib.SOURCE_CONTINUE;
    }

    _connectCursorTracker() {
        if (this._cursorTracker)
            return;

        try {
            // GNOME Shell 49+ exposes the cursor tracker through the backend.
            // The Meta constructor is retained as a compatibility fallback.
            if (global.backend && typeof global.backend.get_cursor_tracker === 'function')
                this._cursorTracker = global.backend.get_cursor_tracker();
        } catch (e) {
            this._cursorTracker = null;
        }

        if (!this._cursorTracker) {
            try {
                this._cursorTracker = Meta.CursorTracker.get_for_display(global.display);
            } catch (e) {
                this._cursorTracker = null;
            }
        }

        if (!this._cursorTracker)
            return;

        try {
            this._visibilityChangedId = this._cursorTracker.connect(
                'visibility-changed',
                () => this._syncCursorVisibility()
            );
        } catch (e) {
            this._visibilityChangedId = 0;
        }

        // Some applications hide the pointer by changing its cursor sprite to
        // NULL rather than toggling the tracker visibility flag.
        try {
            this._cursorChangedId = this._cursorTracker.connect(
                'cursor-changed',
                () => this._syncCursorVisibility()
            );
        } catch (e) {
            this._cursorChangedId = 0;
        }

        try {
            this._positionInvalidatedId = this._cursorTracker.connect(
                'position-invalidated',
                () => {
                    this._syncCursorVisibility();
                    this._updatePosition();
                }
            );
        } catch (e) {
            this._positionInvalidatedId = 0;
        }
    }

    _disconnectCursorTracker() {
        if (this._cursorTracker) {
            for (const signalId of [
                this._visibilityChangedId,
                this._cursorChangedId,
                this._positionInvalidatedId,
            ]) {
                if (!signalId)
                    continue;
                try {
                    this._cursorTracker.disconnect(signalId);
                } catch (e) {
                    // The tracker may already be tearing down with GNOME Shell.
                }
            }
        }

        this._cursorTracker = null;
        this._visibilityChangedId = 0;
        this._cursorChangedId = 0;
        this._positionInvalidatedId = 0;
        this._lastCursorVisible = null;
    }

    _isNativeCursorVisible() {
        if (!this._cursorTracker)
            return true;

        let visible = true;
        try {
            if (typeof this._cursorTracker.get_pointer_visible === 'function')
                visible = Boolean(this._cursorTracker.get_pointer_visible());
        } catch (e) {
            // Use the cursor sprite as a secondary signal if available.
        }

        try {
            if (typeof this._cursorTracker.get_sprite === 'function' &&
                !this._cursorTracker.get_sprite()) {
                visible = false;
            }
        } catch (e) {
            // Older Mutter versions may not expose the cursor sprite through GI.
        }

        return visible;
    }

    _syncCursorVisibility() {
        if (!this._actor)
            return;

        const cursorVisible = this._isNativeCursorVisible();
        if (cursorVisible === this._lastCursorVisible)
            return;

        this._lastCursorVisible = cursorVisible;
        if (cursorVisible)
            this._actor.show();
        else
            this._actor.hide();
    }

    _updatePosition() {
        if (!this._actor)
            return;

        try {
            const [pointerX, pointerY] = global.get_pointer();
            // Actor dimensions may be zero until the first layout pass. Re-run
            // edge placement after those dimensions become available, even if
            // the pointer itself has not moved.
            const measuredWidth = this._actor.get_width() || 0;
            const measuredHeight = this._actor.get_height() || 0;
            if (pointerX === this._lastX && pointerY === this._lastY &&
                measuredWidth === this._lastWidth && measuredHeight === this._lastHeight)
                return;

            // JARVIS is wider than the original lowercase label; keep a safe
            // fallback until GNOME Shell has completed the actor's layout.
            const width = Math.max(74, measuredWidth);
            const height = Math.max(27, measuredHeight);
            const stageWidth = global.stage.width;
            const stageHeight = global.stage.height;

            let x = pointerX + POINTER_OFFSET_X;
            let y = pointerY + POINTER_OFFSET_Y;
            if (x + width > stageWidth - EDGE_PADDING)
                x = pointerX - width - POINTER_OFFSET_X;
            if (y + height > stageHeight - EDGE_PADDING)
                y = pointerY - height - POINTER_OFFSET_Y;

            x = Math.max(EDGE_PADDING, Math.min(x, stageWidth - width - EDGE_PADDING));
            y = Math.max(EDGE_PADDING, Math.min(y, stageHeight - height - EDGE_PADDING));

            this._actor.set_position(Math.round(x), Math.round(y));
            this._lastX = pointerX;
            this._lastY = pointerY;
            this._lastWidth = measuredWidth;
            this._lastHeight = measuredHeight;
        } catch (e) {
            // Keep the session alive; the next tick can retry pointer placement.
        }
    }
}
