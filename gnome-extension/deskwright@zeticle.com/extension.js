/*
 * Computer Use Helpers — GNOME Shell extension.
 *
 * The compositor-side half of deskwright. Everything an agent needs
 * that GNOME on Wayland refuses to give an ordinary client -- screenshots,
 * window geometry and control, the pointer position, a trustworthy clipboard,
 * an on-screen activity border -- lives in dbus.js behind com.zeticle.deskwright.
 *
 * This file only wires up the D-Bus service. The global JARVIS shortcut is
 * intentionally owned by GNOME Settings rather than hard-coded in the
 * extension.
 */

import {Extension} from 'resource:///org/gnome/shell/extensions/extension.js';

import {DBusService} from './dbus.js';
import {sweepOrphanIndicators} from './indicator.js';

export default class ComputerUseHelpersExtension extends Extension {
    enable() {
        // Clear any border stranded by an earlier broken build before arming.
        try {
            sweepOrphanIndicators();
        } catch (e) {
            logError(e, 'deskwright: orphan indicator sweep');
        }

        this._dbus = new DBusService();
        this._dbus.enable();
    }

    disable() {
        if (this._dbus) {
            this._dbus.disable();
            this._dbus = null;
        }

    }
}
