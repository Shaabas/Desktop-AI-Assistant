/*
 * Computer Use Helpers — GNOME Shell extension.
 *
 * Ambient Desktop Agent Interface with Fluid Wavy Aura.
 * - Screen-edge organic undulating wave in Google Gemini Blue (#4285F4).
 * - Continuous periodic harmonic wave field around perimeter with 0% CPU (GPU ease).
 * - Clockwise flow when Acting/Working, Anticlockwise flow when Thinking/Reasoning.
 * - Small bottom-anchored conversational surface on the primary monitor.
 * - One status line + text entry + microphone control.
 * - Input becomes click-through while the computer-use agent is operating.
 */

import cairo from 'gi://cairo';
import Clutter from 'gi://Clutter';
import GLib from 'gi://GLib';
import St from 'gi://St';

import * as Main from 'resource:///org/gnome/shell/ui/main.js';

const ORPHAN_NAME = 'wcu-indicator';
const POINTER_LABEL_NAME = 'jarvis-pointer-pill';

;

// Google Brand Color: Pure Gemini Blue (#4285F4)
const RGB_BLUE = { r: 66 / 255, g: 133 / 255, b: 244 / 255 };

// Spatial Wave and Flow Dynamics
const AURA_DEPTH = 140;          // Inward glow dissolve depth
const NOMINAL_WAVELENGTH = 480;  // Target wavelength in px (~4-5 gentle waves across monitor)
const WAVE_CYCLE_MS = 6000;      // 6.0s duration per wave period (~80px/s calm ambient flow)

export function sweepOrphanIndicators() {
    let removed = 0;
    const candidates = new Set(Main.layoutManager.uiGroup.get_children());
    // The pointer pill is a direct stage child (like Hati's cursor overlay),
    // not a LayoutManager chrome actor, so sweep its named orphan separately.
    for (const actor of global.stage.get_children()) {
        if (actor.get_name() === POINTER_LABEL_NAME)
            candidates.add(actor);
    }
    for (const actor of candidates) {
        let style = '';
        try {
            style = actor.get_style() ?? '';
        } catch (e) {
            style = '';
        }
        const looksLikeIndicator =
            actor.get_name() === ORPHAN_NAME ||
            actor.get_name() === POINTER_LABEL_NAME ||
            style.includes('gradient') ||
            style.includes('66, 133, 244') ||
            style.includes('234, 67, 53') ||
            style.includes('251, 188, 5') ||
            style.includes('52, 168, 83') ||
            style.includes('180, 220, 255') ||
            style.includes('36, 130, 255');
        if (looksLikeIndicator) {
            try {
                Main.layoutManager.removeChrome(actor);
            } catch (e) {
                // Not tracked as chrome; destroying it below is still correct.
            }
            actor.destroy();
            removed++;
        }
    }
    return removed;
}

export const AgentState = {
    IDLE: 'idle',
    OPENING: 'opening',
    LISTENING: 'listening',
    THINKING: 'thinking',
    WORKING: 'working',
    SPEAKING: 'speaking',
    WAITING_CONFIRMATION: 'waiting_confirmation',
    STOPPING: 'stopping',
    ERROR: 'error',
};

export class ActivityIndicator {
    constructor() {
        this._actors = [];
        this._edges = [[], [], [], []];
        this._monitorsChangedId = 0;
        this._conversationScroll = null;
        this._conversationBox = null;
        this._activityScroll = null;
        this._activityBox = null;
        this._activityToggle = null;
        this._activityLabel = null;
        this._statusLabel = null;
        this._stateDot = null;
        this._entry = null;
        this._micIcon = null;
        this._hudActor = null;
        this._pendingInstructions = [];
        this._pendingControlEvents = [];
        this._pendingConfirmationResponses = [];
        this._pendingWorkflowApprovalResponses = [];
        this._activeConfirmationRequestId = null;
        this._confirmationActor = null;
        this._workflowApprovalActor = null;
        this._hudBaseHeight = 92;
        this._hudBaseY = 0;
        this._hudMonitor = null;
        this._isRecording = false;
        this._recordProc = null;
        this._state = AgentState.IDLE;
        this._sessionOpen = false;
        this._activityExpanded = false;
        this._activityItems = [];
        this._conversationHistory = [];
        this._inputControls = [];
        this._micButton = null;
    }

    destroy() {
        this.closeAssistant();
    }

    get active() {
        return this._sessionOpen;
    }

    getPendingInstructions() {
        const instructions = [...this._pendingInstructions];
        this._pendingInstructions = [];
        return instructions;
    }

    getPendingControlEvents() {
        const events = [...this._pendingControlEvents];
        this._pendingControlEvents = [];
        return events;
    }

    getPendingConfirmationResponses() {
        const responses = [...this._pendingConfirmationResponses];
        this._pendingConfirmationResponses = [];
        return responses;
    }

    getPendingWorkflowApprovalResponses() {
        const responses = [...this._pendingWorkflowApprovalResponses];
        this._pendingWorkflowApprovalResponses = [];
        return responses;
    }

    submitInstruction(text) {
        const trimmed = String(text || '').trim();
        if (!trimmed)
            return false;
        this._pendingInstructions.push(trimmed);
        return true;
    }

    requestClose() {
        this._pendingControlEvents.push('close');
        return true;
    }

    show(label, ttl) {
        // Legacy Computer Use clients can still call SetIndicator. Once the
        // assistant surface is open, render this as status/activity rather than
        // replacing the conversation.
        if (!this._sessionOpen)
            this.activateAssistant();
        const text = String(label || '').trim();
        if (text)
            this.setStateFromMessage(text);
        return this._sessionOpen;
    }

    clear() {
        return this.closeAssistant();
    }

    clearIndicator() {
        if (this._sessionOpen) {
            this.setActivity('');
            return true;
        }
        return this.closeAssistant();
    }

    activateAssistant() {
        if (!this._sessionOpen) {
            this._sessionOpen = true;
            this._state = AgentState.OPENING;
            this._build();
        }
        this.setAssistantState(AgentState.LISTENING, 'Listening…');
        GLib.idle_add(GLib.PRIORITY_DEFAULT_IDLE, () => {
            try {
                if (this._entry && this._entry.reactive)
                    this._entry.grab_key_focus();
            } catch (e) {
                logError(e, 'deskwright: failed to focus assistant input');
            }
            return GLib.SOURCE_REMOVE;
        });
        return true;
    }

    closeAssistant() {
        if (!this._sessionOpen)
            return true;

        this._stopRecordingIfNeeded();
        this._pendingInstructions = [];
        this._pendingControlEvents = [];
        this._pendingConfirmationResponses = [];
        this._pendingWorkflowApprovalResponses = [];
        this._confirmationActor = null;
        this._workflowApprovalActor = null;
        this._sessionOpen = false;
        this._state = AgentState.IDLE;
        this._activityExpanded = false;
        this._destroyActorsOnly();
        this._statusLabel = null;
        this._entry = null;
        this._micIcon = null;
        this._micButton = null;
        this._hudActor = null;
        this._inputControls = [];
        return true;
    }

    requestVoiceCapture() {
        if (!this._sessionOpen)
            return false;
        this._pendingControlEvents.push('voice');
        return true;
    }

    setAssistantState(state, detail = '') {
        if (!this._sessionOpen)
            return false;
        const normalized = String(state || AgentState.LISTENING).toLowerCase();
        this._state = normalized;
        if (this._statusLabel)
            this._statusLabel.text = detail || this._stateTitle(normalized);
        const interactive = normalized === AgentState.LISTENING || normalized === AgentState.WAITING_CONFIRMATION;
        this._setInteractionEnabled(interactive);
        if (normalized !== AgentState.LISTENING && normalized !== AgentState.SPEAKING && this._isRecording)
            this._stopRecordingIfNeeded();
        this._restartAuraFlow();
        return true;
    }

    setStateFromMessage(message) {
        const lower = String(message || '').toLowerCase();
        let state = AgentState.WORKING;
        if (lower.includes('error') || lower.includes('failed'))
            state = AgentState.ERROR;
        else if (lower.includes('thinking') || lower.includes('reasoning') || lower.includes('planning') || lower.includes('analyzing'))
            state = AgentState.THINKING;
        else if (lower.includes('speak'))
            state = AgentState.SPEAKING;
        else if (lower.includes('listen') || lower.includes('ready'))
            state = AgentState.LISTENING;
        this.setAssistantState(state, message);
        return true;
    }

    appendConversationMessage(role, text) {
        if (!this._sessionOpen || !String(text || '').trim())
            return false;
        if (this._statusLabel)
            this._statusLabel.text = String(text).trim();
        this._restartAuraFlow();
        return true;
    }

    setActivity(text) {
        if (!this._sessionOpen)
            return false;
        const value = String(text || '').trim();
        if (this._statusLabel)
            this._statusLabel.text = value || this._stateTitle(this._state);
        return true;
    }

    showConfirmation(reason, requestId) {
        const confirmationId = String(requestId || '').trim();
        if (!this._sessionOpen || !this._hudActor || !confirmationId)
            return false;
        this.clearWorkflowApproval();
        this.clearConfirmation();
        this._activeConfirmationRequestId = confirmationId;

        // Keep the confirmation content and decisions unchanged, but present
        // them in the same prominent vertical card treatment as workflow
        // approval. In particular, do not add a new title or explanatory copy:
        // the reason string remains the only body content.
        const card = new St.BoxLayout({
            vertical: true,
            x_expand: true,
            style:
                'background-color: rgba(255,255,255,0.045);' +
                'border: 1px solid rgba(245,158,11,0.38);' +
                'border-radius: 12px;' +
                'padding: 9px 11px 9px 11px;' +
                'spacing: 6px;',
        });
        const body = new St.Label({
            text: String(reason || 'JARVIS needs your confirmation.'),
            x_expand: true,
            style: 'color: #cbd5e1; font-size: 11px;',
        });
        try { body.clutter_text.set_line_wrap(true); } catch (e) {}

        const buttons = new St.BoxLayout({
            vertical: false,
            x_expand: true,
            style: 'spacing: 8px;',
        });
        const deny = this._makeActionButton('Deny', () => {
            if (this._activeConfirmationRequestId !== confirmationId)
                return;
            this._pendingConfirmationResponses.push(JSON.stringify({
                request_id: confirmationId,
                decision: 'deny',
            }));
            this.clearConfirmation();
        });
        const allow = this._makeActionButton('Allow', () => {
            if (this._activeConfirmationRequestId !== confirmationId)
                return;
            this._pendingConfirmationResponses.push(JSON.stringify({
                request_id: confirmationId,
                decision: 'allow',
            }));
            this.clearConfirmation();
        });
        buttons.add_child(deny);
        buttons.add_child(allow);
        card.add_child(body);
        card.add_child(buttons);
        this._confirmationActor = card;
        this._hudActor.add_child(card);

        // The original single-row prompt fit into the 92px HUD. The vertical
        // card needs its own space; keep the HUD bottom-anchored just like the
        // workflow approval card and restore its original geometry on close.
        if (this._hudMonitor) {
            const baseHeight = this._hudBaseHeight || 92;
            const availableHeight = Math.max(baseHeight, this._hudMonitor.height - 52);
            // Measure the full content after attaching the confirmation card.
            // The previous fixed 205px height left a large unused gap below the
            // bordered panel. Natural sizing keeps only the HUD's CSS bottom
            // padding, while still expanding for longer wrapped explanations.
            const [, naturalHeight] = this._hudActor.get_preferred_height(this._hudActor.width);
            const expandedHeight = Math.min(Math.max(baseHeight, Math.ceil(naturalHeight)), availableHeight);
            this._hudActor.height = expandedHeight;
            this._hudActor.y = Math.round(this._hudMonitor.y + this._hudMonitor.height - expandedHeight - 26);
        }
        this.setAssistantState(AgentState.WAITING_CONFIRMATION, 'Confirmation required');
        return true;
    }

    clearConfirmation() {
        if (this._confirmationActor) {
            try { this._confirmationActor.destroy(); } catch (e) {}
            this._confirmationActor = null;
        }
        this._activeConfirmationRequestId = null;
        // Don't collapse the HUD if another prompt has already taken over.
        if (!this._workflowApprovalActor && this._hudActor && this._hudMonitor) {
            this._hudActor.height = this._hudBaseHeight || 92;
            this._hudActor.y = this._hudBaseY;
        }
        return true;
    }

    showWorkflowApproval(name, description, stepsSummary, stepCount) {
        if (!this._sessionOpen || !this._hudActor)
            return false;

        this.clearConfirmation();
        this.clearWorkflowApproval();
        this._pendingWorkflowApprovalResponses = [];

        const workflowName = String(name || 'unnamed workflow').slice(0, 100);
        const workflowDescription = String(description || 'No description was provided.').slice(0, 240);
        const summary = String(stepsSummary || 'No step summary is available.').slice(0, 360);
        const count = Math.max(0, Number(stepCount) || 0);

        const card = new St.BoxLayout({
            vertical: true,
            x_expand: true,
            style:
                'background-color: rgba(255,255,255,0.045);' +
                'border: 1px solid rgba(245,158,11,0.38);' +
                'border-radius: 12px;' +
                'padding: 9px 11px 9px 11px;' +
                'spacing: 6px;',
        });
        const title = new St.Label({
            text: 'Approve learned workflow?',
            x_expand: true,
            style: 'color: #fbbf24; font-size: 13px; font-weight: bold;',
        });
        const detail = new St.Label({
            text: `${workflowName} — ${count} recorded steps\n${workflowDescription}\n${summary}\n\nApproving enables future automatic execution. Recorded coordinates and waits may be fragile.`,
            x_expand: true,
            style: 'color: #cbd5e1; font-size: 11px;',
        });
        try { detail.clutter_text.set_line_wrap(true); } catch (e) {}

        const buttons = new St.BoxLayout({
            vertical: false,
            x_expand: true,
            style: 'spacing: 8px;',
        });
        const decline = this._makeActionButton('Decline', () => {
            this._pendingWorkflowApprovalResponses.push(JSON.stringify({workflow: workflowName, decision: 'decline'}));
            this.clearWorkflowApproval();
            this.setAssistantState(AgentState.THINKING, 'Workflow declined');
        });
        const approve = this._makeActionButton('Approve workflow', () => {
            this._pendingWorkflowApprovalResponses.push(JSON.stringify({workflow: workflowName, decision: 'approve'}));
            this.clearWorkflowApproval();
            this.setAssistantState(AgentState.THINKING, 'Workflow approval received');
        });
        buttons.add_child(decline);
        buttons.add_child(approve);
        card.add_child(title);
        card.add_child(detail);
        card.add_child(buttons);

        this._workflowApprovalActor = card;
        this._hudActor.add_child(card);
        if (this._hudMonitor) {
            const expandedHeight = 285;
            this._hudActor.height = expandedHeight;
            this._hudActor.y = Math.round(this._hudMonitor.y + this._hudMonitor.height - expandedHeight - 26);
        }
        this.setAssistantState(AgentState.WAITING_CONFIRMATION, `Review workflow: ${workflowName}`);
        return true;
    }

    clearWorkflowApproval() {
        if (this._workflowApprovalActor) {
            try { this._workflowApprovalActor.destroy(); } catch (e) {}
            this._workflowApprovalActor = null;
        }
        if (this._hudActor && this._hudMonitor) {
            this._hudActor.height = this._hudBaseHeight || 92;
            this._hudActor.y = this._hudBaseY;
        }
        return true;
    }



    _build() {
        this._actors = [];
        this._edges = [[], [], [], []];
        this._statusLabel = null;
        this._entry = null;
        this._micIcon = null;
        this._hudActor = null;
        this._hudContent = null;
        this._workflowApprovalActor = null;
        this._confirmationActor = null;
        this._conversationScroll = null;
        this._conversationBox = null;
        this._activityScroll = null;
        this._activityBox = null;

        this._buildWavyAura();
        this._startWavyAuraFlow();
        this._buildHud();

        this._monitorsChangedId = Main.layoutManager.connect('monitors-changed', () => {
            if (!this._sessionOpen)
                return;
            // Rebuild the surface for the new primary monitor while preserving
            // the current session state. Conversation history is intentionally
            // kept in the Python daemon, so this surface can be rebuilt safely.
            const state = this._state;
            this._destroyActorsOnly();
            this._sessionOpen = true;
            this._state = state;
            this._build();
        });
    }

    _destroyActorsOnly() {
        if (this._monitorsChangedId) {
            try { Main.layoutManager.disconnect(this._monitorsChangedId); } catch (e) {}
            this._monitorsChangedId = 0;
        }
        for (let e = 0; e < 4; e++) {
            for (const item of this._edges[e] || []) {
                try { item.actor.remove_all_transitions?.(); } catch (err) {}
            }
        }
        for (const actor of this._actors) {
            try { Main.layoutManager.removeChrome(actor); } catch (e) {}
            try { actor.destroy(); } catch (e) {}
        }
        this._actors = [];
        this._edges = [[], [], [], []];
    }

    _buildWavyAura() {
        for (const monitor of Main.layoutManager.monitors) {
            const W = monitor.width;
            const H = monitor.height;
            const perimeter = 2 * (W + H);
            // Integer harmonic frequency ensures exact 0-seam perimeter closure
            const numWaves = Math.max(Math.round(perimeter / NOMINAL_WAVELENGTH), 4);
            const wavelength = perimeter / numWaves;

            for (let edgeIndex = 0; edgeIndex < 4; edgeIndex++) {
                const isHoriz = (edgeIndex === 0 || edgeIndex === 2);
                const containerWidth = isHoriz ? W : AURA_DEPTH;
                const containerHeight = isHoriz ? AURA_DEPTH : H;
                let containerX = monitor.x;
                let containerY = monitor.y;

                if (edgeIndex === 1) // Right
                    containerX = monitor.x + W - AURA_DEPTH;
                else if (edgeIndex === 2) // Bottom
                    containerY = monitor.y + H - AURA_DEPTH;

                // Container clips child texture exactly to the monitor edge boundary
                const container = new Clutter.Actor({
                    reactive: false,
                    x: containerX,
                    y: containerY,
                    width: containerWidth,
                    height: containerHeight,
                    clip_to_allocation: true,
                });
                container.set_name(ORPHAN_NAME);

                // Child surface is extended by 2 wavelengths to allow seamless continuous translation
                const childWidth = isHoriz ? Math.ceil(W + 2 * wavelength) : AURA_DEPTH;
                const childHeight = isHoriz ? AURA_DEPTH : Math.ceil(H + 2 * wavelength);

                const waveChild = new St.DrawingArea({
                    reactive: false,
                    x: isHoriz ? -wavelength : 0,
                    y: isHoriz ? 0 : -wavelength,
                    width: childWidth,
                    height: childHeight,
                });

                waveChild.connect('repaint', (area) => {
                    const [cw, ch] = area.get_surface_size();
                    if (cw <= 0 || ch <= 0)
                        return;
                    const cr = area.get_context();
                    try {
                        this._paintEdgeWave(cr, edgeIndex, cw, ch, W, H, wavelength);
                    } catch (e) {
                        logError(e, `deskwright: cairo paint wavy edge ${edgeIndex}`);
                    } finally {
                        cr.$dispose();
                    }
                });

                container.add_child(waveChild);
                Main.layoutManager.addChrome(container, {
                    affectsStruts: false,
                    trackFullscreen: false,
                });

                this._actors.push(container);
                this._edges[edgeIndex].push({
                    actor: waveChild,
                    isHorizontal: isHoriz,
                    wavelength,
                });
            }
        }
    }

    _paintEdgeWave(cr, edgeIndex, w, h, W, H, wavelength) {
        const isHoriz = (edgeIndex === 0 || edgeIndex === 2);
        const L = isHoriz ? w : h;

        // 1. Source wave along edge propagation dimension
        const src = isHoriz
            ? new cairo.LinearGradient(0, 0, w, 0)
            : new cairo.LinearGradient(0, 0, 0, h);

        const stepPx = 30;
        const steps = Math.ceil(L / stepPx);
        for (let i = 0; i <= steps; i++) {
            const u = Math.min(i * stepPx, L);
            let s = 0;
            if (edgeIndex === 0)
                s = u;
            else if (edgeIndex === 1)
                s = W + u;
            else if (edgeIndex === 2)
                s = 2 * W + H - u;
            else if (edgeIndex === 3)
                s = 2 * W + 2 * H - u;

            const frac = (((s % wavelength) + wavelength) % wavelength) / wavelength;
            // Smooth organic cosine swell from trough (0.18) to crest (0.54)
            const waveWeight = 0.5 * (1.0 - Math.cos(2 * Math.PI * frac));
            const alpha = 0.18 + 0.36 * waveWeight;
            src.addColorStopRGBA(u / L, RGB_BLUE.r, RGB_BLUE.g, RGB_BLUE.b, alpha);
        }
        cr.setSource(src);

        // 2. Depth mask inward from screen border
        let mask;
        if (edgeIndex === 0) {
            // Top: starts at y = 0
            mask = new cairo.LinearGradient(0, 0, 0, h);
            mask.addColorStopRGBA(0.0, 1, 1, 1, 1.0);
            mask.addColorStopRGBA(0.25, 1, 1, 1, 0.65);
            mask.addColorStopRGBA(0.60, 1, 1, 1, 0.25);
            mask.addColorStopRGBA(1.0, 1, 1, 1, 0.0);
        } else if (edgeIndex === 1) {
            // Right: starts at x = w
            mask = new cairo.LinearGradient(0, 0, w, 0);
            mask.addColorStopRGBA(0.0, 1, 1, 1, 0.0);
            mask.addColorStopRGBA(0.40, 1, 1, 1, 0.25);
            mask.addColorStopRGBA(0.75, 1, 1, 1, 0.65);
            mask.addColorStopRGBA(1.0, 1, 1, 1, 1.0);
        } else if (edgeIndex === 2) {
            // Bottom: starts at y = h
            mask = new cairo.LinearGradient(0, 0, 0, h);
            mask.addColorStopRGBA(0.0, 1, 1, 1, 0.0);
            mask.addColorStopRGBA(0.40, 1, 1, 1, 0.25);
            mask.addColorStopRGBA(0.75, 1, 1, 1, 0.65);
            mask.addColorStopRGBA(1.0, 1, 1, 1, 1.0);
        } else if (edgeIndex === 3) {
            // Left: starts at x = 0
            mask = new cairo.LinearGradient(0, 0, w, 0);
            mask.addColorStopRGBA(0.0, 1, 1, 1, 1.0);
            mask.addColorStopRGBA(0.25, 1, 1, 1, 0.65);
            mask.addColorStopRGBA(0.60, 1, 1, 1, 0.25);
            mask.addColorStopRGBA(1.0, 1, 1, 1, 0.0);
        }

        cr.mask(mask);
    }

    _startWavyAuraFlow() {
        for (let edgeIndex = 0; edgeIndex < 4; edgeIndex++) {
            for (const item of this._edges[edgeIndex]) {
                this._runEdgeWave(edgeIndex, item, false);
            }
        }
    }

    _runEdgeWave(edgeIndex, item, fromCurrentPos = false) {
        if (!this.active)
            return;
        const { actor, isHorizontal, wavelength } = item;
        if (!actor.get_parent())
            return;

        const isThinking = (this._state === AgentState.THINKING);

        // Clockwise directions (Acting / Default):
        // Top (0): moves L -> R (-wavelength to 0)
        // Right (1): moves T -> B (-wavelength to 0)
        // Bottom (2): moves R -> L (0 to -wavelength)
        // Left (3): moves B -> T (0 to -wavelength)
        //
        // Thinking reverses the flow (Anticlockwise):
        let moveForward = (edgeIndex === 0 || edgeIndex === 1);
        if (isThinking)
            moveForward = !moveForward;

        const targetPos = moveForward ? 0 : -wavelength;
        let startPos = moveForward ? -wavelength : 0;
        let duration = WAVE_CYCLE_MS;

        if (fromCurrentPos) {
            const currentPos = isHorizontal ? actor.x : actor.y;
            const remainingDist = Math.abs(currentPos - targetPos);
            duration = Math.max(Math.round(WAVE_CYCLE_MS * (remainingDist / wavelength)), 100);
            startPos = currentPos;
        }

        // Apply starting position
        if (isHorizontal)
            actor.x = startPos;
        else
            actor.y = startPos;

        const easeParams = {
            duration,
            mode: Clutter.AnimationMode.LINEAR,
            onComplete: () => {
                if (this.active && actor.get_parent())
                    this._runEdgeWave(edgeIndex, item, false);
            },
        };

        if (isHorizontal)
            easeParams.x = targetPos;
        else
            easeParams.y = targetPos;

        try {
            actor.ease(easeParams);
        } catch (e) {
            logError(e, `deskwright: error easing wave edge ${edgeIndex}`);
        }
    }


    _makeActionButton(label, callback) {
        const button = new St.Button({
            label,
            x_expand: true,
            style:
                'background-color: rgba(255,255,255,0.07);' +
                'border: 1px solid rgba(255,255,255,0.10);' +
                'border-radius: 10px;' +
                'padding: 8px 14px;' +
                'color: #e2e8f0; font-size: 12px;',
        });
        button.connect('clicked', callback);
        return button;
    }

    _buildHud() {
        const monitor = Main.layoutManager.primaryMonitor || Main.layoutManager.monitors[0];
        if (!monitor)
            return;

        const HUD_WIDTH = Math.min(560, Math.max(420, monitor.width - 48));
        const HUD_HEIGHT = 92;
        this._hudMonitor = monitor;
        this._hudBaseHeight = HUD_HEIGHT;
        const hudX = Math.round(monitor.x + (monitor.width - HUD_WIDTH) / 2);
        const hudY = Math.round(monitor.y + monitor.height - HUD_HEIGHT - 26);
        this._hudBaseY = hudY;

        const hud = new St.BoxLayout({
            vertical: true,
            reactive: false,
            can_focus: false,
            track_hover: false,
            x: hudX,
            y: hudY,
            width: HUD_WIDTH,
            height: HUD_HEIGHT,
            style:
                'background-color: rgba(8,12,20,0.88);' +
                'border: 1px solid rgba(255,255,255,0.10);' +
                'border-radius: 16px;' +
                'padding: 8px 12px 10px 14px;' +
                'box-shadow: 0 10px 32px rgba(0,0,0,0.30);' +
                'spacing: 6px;',
        });
        hud.set_name(ORPHAN_NAME);
        Main.layoutManager.addChrome(hud, { affectsStruts: false, trackFullscreen: false });
        this._hudActor = hud;
        this._actors.push(hud);

        const statusRow = new St.BoxLayout({ vertical: false, x_expand: true });
        this._statusLabel = new St.Label({
            text: 'Listening…',
            x_expand: true,
            style: 'color: #e2e8f0; font-size: 12px; padding: 1px 0;',
            y_align: Clutter.ActorAlign.CENTER,
        });
        try { this._statusLabel.clutter_text.set_single_line_mode(true); } catch (e) {}
        statusRow.add_child(this._statusLabel);
        hud.add_child(statusRow);

        this._buildInput(hud);
        this._setInteractionEnabled(true);
    }

    _buildInput(hud) {
        const inputBox = new St.BoxLayout({
            vertical: false,
            x_expand: true,
            reactive: false,
            style:
                'background-color: rgba(255,255,255,0.045);' +
                'border: 1px solid rgba(255,255,255,0.09);' +
                'border-radius: 12px;' +
                'padding: 2px 4px 2px 11px;' +
                'spacing: 3px;',
        });

        const entry = new St.Entry({
            hint_text: 'Tell me what to do…',
            can_focus: true,
            reactive: true,
            x_expand: true,
            style: 'background: transparent; border: none; color: #ffffff; font-size: 12px; padding: 6px 0;',
        });
        this._entry = entry;
        inputBox.add_child(entry);

        const micBtn = new St.Button({
            can_focus: true,
            reactive: true,
            style: 'background: transparent; border: none; padding: 4px 6px;',
        });
        const micIcon = new St.Icon({ icon_name: 'audio-input-microphone-symbolic', icon_size: 16, style: 'color: #cbd5e1;' });
        micBtn.set_child(micIcon);
        this._micIcon = micIcon;
        this._micButton = micBtn;
        inputBox.add_child(micBtn);
        this._inputControls = [entry, micBtn];
        hud.add_child(inputBox);

        entry.clutter_text.connect('activate', () => this._submitCurrentText());
        micBtn.connect('clicked', () => this.requestVoiceCapture());
    }

    _setInteractionEnabled(enabled) {
        for (const actor of this._inputControls || []) {
            try { actor.reactive = !!enabled; } catch (e) {}
            try { actor.can_focus = !!enabled; } catch (e) {}
        }
        if (this._micIcon)
            this._micIcon.style = enabled ? 'color: #cbd5e1;' : 'color: #64748b;';
    }

    _submitCurrentText() {
        if (!this._entry || !this._entry.reactive)
            return;
        const text = this._entry.get_text().trim();
        if (!text)
            return;
        this._entry.set_text('');
        this._pendingInstructions.push(text);
        if (this._statusLabel)
            this._statusLabel.text = text;
        this.setAssistantState(AgentState.THINKING, 'Thinking…');
    }

    _stopRecordingIfNeeded() {
        this._isRecording = false;
    }

    _stateTitle(state) {
        return {
            [AgentState.IDLE]: 'Idle',
            [AgentState.OPENING]: 'Opening…',
            [AgentState.LISTENING]: 'Ready',
            [AgentState.THINKING]: 'Thinking…',
            [AgentState.WORKING]: 'Working…',
            [AgentState.SPEAKING]: 'Speaking…',
            [AgentState.WAITING_CONFIRMATION]: 'Waiting for confirmation',
            [AgentState.STOPPING]: 'Stopping…',
            [AgentState.ERROR]: 'Something went wrong',
        }[state] || 'Ready';
    }

    _stateColor(state) {
        return {
            [AgentState.LISTENING]: '#60a5fa',
            [AgentState.THINKING]: '#a78bfa',
            [AgentState.WORKING]: '#38bdf8',
            [AgentState.SPEAKING]: '#34d399',
            [AgentState.WAITING_CONFIRMATION]: '#f59e0b',
            [AgentState.STOPPING]: '#fb7185',
            [AgentState.ERROR]: '#f87171',
        }[state] || '#64748b';
    }

    _restartAuraFlow() {
        for (let edgeIndex = 0; edgeIndex < 4; edgeIndex++) {
            for (const item of this._edges[edgeIndex] || []) {
                try { item.actor.remove_all_transitions?.(); } catch (e) {}
                this._runEdgeWave(edgeIndex, item, true);
            }
        }
    }


}
