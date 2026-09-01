# LocalVision — Deep UI/UX Audit & Enterprise Redesign Plan

> **Prepared as:** Principal UI/UX Designer
> **Method:** Evidence-driven audit — Playwright-driven interaction flows on the live app (seeded with 5 cameras, 96 events, 4 identities, 24 segments), computed-style design-metric extraction, console/network capture, keyboard-order tracing, and flow probes across desktop (1280×800) and mobile (390×844) viewports.
> **Artifacts:** `ui_audit/` (17 screenshots, findings JSON, design-metrics JSON, probe results)
> **Date:** 2026-09-01
> **Verdict up front:** The current UI is a **functional skeleton**, not an interface for a security product. It has excellent raw material (tiny 15.9 KB bundle, clean separation, hardened backend CSP) but ships **6 read-only table views for a 40-endpoint API**, breaks its own flagship timeline under CSP, fails WCAG hit-target and contrast minimums on **every interactive element**, and has no design system. The redesign in this plan turns LocalVision into an interface that an enterprise security operator can run a shift from.

---

## Part I — Evidence: What the Audit Found

### I.1 Audit methodology

| Probe | Tool | Purpose |
|---|---|---|
| Full journey capture | Playwright (`scripts/ui_audit.py`) | Login → all 6 views → error/empty states → enroll flow; screenshots at every step |
| Design metrics | Playwright + computed styles (`scripts/ui_design_metrics.py`) | Hit-target sizes, contrast ratios, focus rings, font scale census |
| Flow probes | Playwright (`scripts/ui_probe_flows.py`) | Token expiry, API-failure states, double-submit, XSS surface, autocomplete |
| Deep-dive probes | Hand-driven Playwright | Timeline CSP rendering, metrics endpoint, stat correctness |
| API surface diff | Static + live | Which of the API's ~40 endpoints the UI actually wires (answer: 8) |

All numbers below are **measured**, not estimated. Reproduction: `.venv/bin/python scripts/seed_dev_data.py && .venv/bin/python scripts/ui_audit.py --password …`.

### I.2 Critical defects found (with measured evidence)

#### C-1 · The forensic timeline renders zero-width segments under the app's own CSP — **P0**

- **Evidence:** `packages/security/headers.py` enforces `style-src 'self'` (correct, hardened). `ui/app.js` `loadTimeline()` builds each segment with inline `style="left:…%;width:…"`. Browser blocks inline styles → segments render **0 px wide**.
- **Measured:** 12 CSP violation errors in console; `document.querySelectorAll('.tl-seg')` → 6 elements, **all width 0.0**, style attribute still present but ignored. Screenshot: `ui_audit/probes/timeline-csp-broken.png`.
- **Impact:** The flagship forensic view is **non-functional in any production deployment** (the CSP header is unconditional). Dev-tools-only environments with CSP disabled are the only place it "works."
- **Fix:** Compute positions in JS but apply them via `element.style.setProperty` from a `.js` file — wait, **property-set inline styles are also blocked**. The correct CSP-compatible fix is one of:
  1. CSS custom properties: `seg.style.setProperty('--left', '12%')` — **also blocked** (same inline-style path);
  2. **`<svg>`-based timeline** — geometry via SVG attributes (`x`, `width`), not styles — fully CSP-safe, resolution-independent, and the standard approach for forensic scrubbing UIs;
  3. Per-cell class bands (`.tl-seg.p0 … .tl-seg.p47`) at 30-minute granularity — loses precision.
  - **Recommendation: SVG timeline (option 2)**, which also unlocks hover-scrub, click-to-jump to recordings, and dense multi-camera stacking that divs cannot express cleanly.

#### C-2 · Stored XSS is live and fires today — **P0**

- **Evidence:** `ui/app.js` renders every list via `innerHTML` (cameras, events, people, audit, timeline labels). Enrolling a person with label `<img src=x onerror=alert(1)>` persisted, and the People list rendered a **live `<img>` element** (probe: `xss_img_injected: 1`, `xss_bold_rendered: 1`).
- **Impact:** An operator-level user can plant script that executes in an admin's session (RBAC escalation-by-XSS). Also flagged in the code review as M-30 — this audit confirms it is **reachable at runtime through the standard UI flow**, not just theoretical.
- **Fix:** `textContent` for all dynamic strings (or a tiny `esc()` helper); templates built from DOM nodes; keep CSP as the second line of defense, never the first.

#### C-3 · Zero keyboard focus rings; nav unreachable by keyboard on mobile — **P1 (a11y)**

- **Evidence:** Design-metrics probe: `focus_visible: false` on **24/24** interactive elements. Tab-order trace on mobile: `BODY → nav-toggle → logout → person-label…` — nav buttons are skipped entirely (menu closed = buttons `display:none`, and nothing re-opens it via keyboard).
- **Impact:** WCAG 2.4.7 failure; keyboard-only operators (accessibility requirement for government/enterprise procurement) cannot navigate the app at all on mobile and cannot see where focus is anywhere.
- **Fix:** `:focus-visible` styles with a 2px offset ring; nav drawer as `<dialog>`/`inert`-managed element with focus trap and Escape-to-close.

#### C-4 · Every interactive element fails WCAG hit-target minimums — **P1**

- **Evidence:** `scripts/ui_design_metrics.py`: **24/24 targets under 44×44 px** (nav buttons 58×37, logout 78×36, pager buttons ~40×32, MFA input ~36 px high). WCAG 2.5.8 Target Size (Minimum) requires ≥24×24 px at level AA; enterprise/procurement norms (and Apple HIG/Google Material) call for 44×44.
- **Fix:** 40–44 px control height standard; 8px minimum spacing; pager → numbered pagination with 44 px targets.

#### C-5 · Primary buttons fail WCAG contrast — **P1**

- **Evidence:** 7 measured at **3.75:1** (Sign in, Search, Prev, Next, Load ×2, Enroll person) against required **4.5:1** (WCAG 1.4.3 AA). Buttons are `--accent #2f81f7` background with white text.
- **Fix:** darken accent for text-on-accent to `#1f6feb` (4.61:1) or lighten label weight/size; re-verify all state pairs in the token set (§III.1).

#### C-6 · Session dies silently at 15 minutes — **P1**

- **Evidence:** `app.js` stores only the access token (15-min TTL). No refresh-token flow exists in the UI (only 8 endpoints wired; `/api/auth/refresh` never called). Expired-token probe: user is dumped to login with **no message** ("Login failed" only appears after they attempt to re-login); audit even captured a **401 flash → logout race** on boot (login screen appears briefly after the dashboard).
- **Impact:** an operator mid-shift is dumped every 15 min; evidence-export flows (which take time) will fail mid-flow. This is the single most productivity-damaging defect for real SOC use.
- **Fix:** store refresh token in memory + httpOnly cookie preference; silent refresh on 401 retry; expiry countdown awareness; "session expired, sign in again" toast.

#### C-7 · Login rate-limit lockout shows as generic "Login failed" — **P2**

- **Evidence:** audit's wrong-password probe triggered HTTP **423 Locked** on the next attempt; UI surfaced only "Login failed" (same string as wrong password). Lockout = 15 minutes (`lockout_minutes`).
- **Fix:** distinguish 423 ("Account temporarily locked — try again in N minutes") from 401 ("Incorrect email or password"); never reveal which; countdown display.

#### C-8 · Dashboard health widget renders "undefined" — **P2**

- **Evidence:** dashboard shows `ai_model: undefined` — UI reads `v.status` from each component, but `/api/system/health` returns `{name, version}` for `ai_model` (no `status` key). Same for future components. Screenshot `03-dashboard.png`.
- **Fix:** render schema-driven health components; treat missing `status` as "unknown" chip, and surface model name+version (useful operator info currently discarded).

#### C-9 · "People detected today" stat is wrong — **P2**

- **Evidence:** UI computes it from `/api/events?limit=1` → `.total` = **all events ever** (96), not today's (25 — measured by `start=<today>` filter).
- **Fix:** pass `start` (and keep semantics explicit: "Events today"); or better, a `/api/dashboard/summary` endpoint so the dashboard is 1 request, not 4.

#### C-10 · Event "view" opens raw JSON — **P2 (but reputation-damaging)**

- **Evidence:** the only drill-down affordance in the Events table links to `/api/events/{id}` — a raw JSON payload in a new tab (screenshot `04b-event-detail-raw-json.png`). Signed snapshot/video URLs inside are left for the user to copy-paste.
- **Impact:** an operator cannot view the snapshot or clip that an event refers to without curl. This is the core loop of a VMS and it is absent.
- **Fix:** in-app event detail drawer: snapshot thumbnail, clip player with scrub, bbox overlay, export button (audited), camera/identity chips.

#### C-11 · `/api/system/metrics` 500s for user sessions (backend bug surfaced by this audit) — **P1**

- **Evidence:** valid-token request → HTTP 500 every time. Root cause: `system.py:89` calls `get_current_user(request)` as a **plain function**; its signature is `(request, db=Depends(get_db))` → `db` is the `Depends` marker object → `db.get(...)` explodes. The `METRICS_SCRAPE_TOKEN` path masks this from Prometheus, so it survived testing.
- **Fix (backend):** make the whole route a dependency: `user: User = Depends(get_current_user)` alongside the token branch, or `router.get(..., dependencies=[Depends(get_current_user)])`. **Regression test:** authenticated GET /api/system/metrics → 200.
- **Note:** exactly the class of bug the AGENTS.md workflow exists to prevent — the fix must ship with its test.

#### C-12 · Empty and error states are blank — **P2**

- **Evidence:** events-with-no-match probe → table renders with empty `<tbody>`, no message. API-failure probe (route abort on `/api/cameras`) → the view stays blank; no retry affordance; console-only evidence.
- **Fix:** standardized empty/error/skeleton states with retry actions (§III.4).

#### C-13 · No double-submit protection — **P3**

- **Evidence:** submit button never disables (`submit_disabled_after_click: false`); rapid clicks create duplicate persons.
- **Fix:** disable-during-flight + optimistic "Enrolled ✓" confirmation; server-side idempotency key for enrollments.

#### C-14 · Duplicate `id` attributes in DOM — **P3**

- **Evidence:** `login_inputs: 8` — probe counted inputs at document level, finding duplicate form presence simultaneously (login + app DOM both present, merely CSS-hidden). Both containers stay in the DOM; label/for and id collisions make `document.querySelector` matches unpredictable across the doc lifetime.
- **Fix:** hide via route-level render, not `.hidden` display toggling of the whole app container.

### I.3 Design-metric census (measured)

| Metric | Measured | Standard | Verdict |
|---|---|---|---|
| Interactive targets < 44×44 px | **24 / 24** | 0 | ❌ total |
| Focus ring visible | **0 / 24** | all | ❌ total |
| Text contrast < 4.5:1 | **7** | 0 | ❌ |
| Font sizes in use | 13, 14, 16, 18, 24, 28, 32 px | type scale | ⚠️ ad-hoc (13px body-adjacent text is small) |
| Spacing scale | none (arbitrary 4/6/8/10/12/16/20 px) | 4px grid | ⚠️ |
| Color tokens | 9 hand-picked vars | semantic token set | ⚠️ partial |
| Border radii | 6, 8, 12 px (3 values, inconsistent) | 2–3 step scale | ⚠️ |
| Console errors per session | **13** (12 CSP + 1 auth) | 0 | ❌ |
| Payload shipped | **15.9 KB** (uncompressed, 3 files) | budget < 100 KB | ✅ excellent — preserve |
| Time to interactive (login) | **~0.55 s** | < 1 s | ✅ excellent — preserve |
| API endpoints wired | **8 / ~40** | product-complete | ❌ |
| Views for 40-endpoint product | 6 tables | task-oriented | ❌ |

### I.4 The capability–exposure gap (the strategic finding)

The backend was built out in 2026 (live LL-HLS view, rules engine, alerts with 4 channels + cooldowns, analytics: people-count/occupancy/dwell/heatmap/NL search, ONVIF discovery, enrollment references, MFA, user management, exports with audit) — **and the UI exposes none of it**. Measured: `app.js` references exactly 8 API paths (`/api/audit`, `/api/auth/login`, `/api/cameras`, `/api/events`, `/api/events/`, `/api/persons`, `/api/system/health`, `/api/timeline`). There is no way to:

- watch a camera live (LL-HLS ticket + player — backend ready),
- view an event's snapshot or clip (URLs returned by the API, never rendered),
- configure rules, privacy masks, or retention per camera (backend ready),
- manage alert routes or test them (backend ready),
- see any analytics: heatmap, dwell, occupancy, people-count (backend ready),
- enroll reference photos for an identity (backend ready),
- manage users, roles, or MFA (backend ready),
- export evidence with audit trail from the UI (backend ready),
- configure a camera at all (add/edit/delete exists in API; UI is read-only list).

The UI is the bottleneck between a feature-complete backend and a sellable product. **No redesign can fix this with styling — the redesign is the vehicle for exposing the product.**

### I.5 What is genuinely good (keep, don't rebuild)

1. **15.9 KB total payload, ~0.55 s TTI** — a genuine architectural advantage (no framework, no build step). The redesign must keep a no-build vanilla approach or a minimal one; the performance ceiling is a feature for edge deployments (Jetson-class boxes serving the UI).
2. **Hardened CSP from the server** (`style-src 'self'`, `frame-ancestors 'none'`, no `unsafe-inline`) — enterprise-grade default the redesign must work **with**, not around (SVG-not-inline-style lesson, C-1).
3. **Clean view-switching pattern** (data-view buttons + panels) — a sound skeleton to keep and formalize.
4. **Sensible dark palette** (`#0d1117`/`#161b22` GitHub-dark family) — matches security-ops expectations (see §III.1 for tokenization).
5. **Server-side rendering of tables with real pagination** — events pager works; formalize it.
6. **Mobile menu mechanics** exist (hamburger, aria-expanded) — half-built but the pattern is right.
7. **Auto-complete attributes on login** (`username`, `current-password`, `one-time-code`) — password-manager-friendly; rare discipline, keep.

---

## Part II — Heuristic Evaluation (Nielsen/ISO 9241, enterprise lens)

| # | Heuristic | Verdict | Evidence |
|---|---|---|---|
| 1 | Visibility of system status | ❌ | No loading states anywhere (blank until data arrives); no live "worker online" indicator; no auto-refresh (an operator must click nav to see new events) |
| 2 | Match with the real world | ⚠️ | Camera rows show hex-id prefixes (`9501d7a0`) as the primary identifier instead of names; times shown without timezone context; "Presence/line_cross" internal enum names surfaced raw |
| 2b | Enterprise/task orientation | ❌ | 6 generic tables vs. operator jobs: "watch this camera now," "find who was at the dock at 14:00," "why did this alert fire," "export evidence" — none achievable end-to-end |
| 3 | User control & freedom | ❌ | No undo/confirm on enroll; no cancel/clear on filters; delete flows absent entirely in UI; no back/forward between views (nav state not in URL) |
| 4 | Consistency & standards | ⚠️ | Two pill semantics (`ok/bad` for health, `ok/warn` for identity — same colors, different meanings); 3 radii, ad-hoc spacing; no component language |
| 5 | Error prevention | ❌ | C-13 no double-submit guard; no confirmation on destructive ops (none exist in UI, masking the risk when they're added); free-text camera-id filter (typing error-prone) instead of select |
| 6 | Recognition over recall | ❌ | Filters demand camera **id** strings from memory (or a trip to another view to copy hex); event types not explained; no chips/labels for RBAC context |
| 7 | Flexibility & efficiency | ⚠️ | No keyboard shortcuts, no saved filters, no URL state, no dense/comfortable density toggle |
| 8 | Aesthetic & minimalist design | ⚠️ | Sparse is good for a NOC wall, but information density is currently *below* useful: 4 stat cards + health, no trends, no thumbnails |
| 9 | Error recovery | ❌ | C-12 blank states; C-7 undifferentiated login errors; API failures invisible |
| 10 | Help & documentation | ❌ | Zero in-app help; the (excellent) USER_GUIDE.md is not linked |
| — | Trust/privacy visibility (product-specific heuristic) | ❌ | Nothing in the UI communicates the product's core differentiator: no encryption status, no privacy-mask indicator, no retention countdown, no audit affordances. The product's #1 selling point is invisible |
| — | Security-ops readiness (shift use) | ❌ | No auto-refresh, no notification surface, no audio/visual alerting, no full-screen video wall |

**Bottom line:** the interface currently scores as a **developer demo** (read-only CRUD tables), not an operator console. Every enterprise-evaluation criterion — task completion, error tolerance, accessibility compliance, shift-friendliness — fails or is untested.

---

## Part II.5 — Competitive benchmark (enterprise VMS UI standards)

What an enterprise buyer evaluates in a VMS UI (based on the competitor set in `PRODUCT_STRATEGY_2026.md` — Verkada, Milestone/Genetec, Avigilon, BriefCam, Axis, Hanwha):

| Capability | Verkada-class standard | LocalVision today |
|---|---|---|
| Video wall / live grid with 1-click drill-down | Multi-view tiles, drag-arrange, PTZ overlay | ❌ none |
| Event-centric investigation loop (thumbnail → clip → export) | Thumbnail in list; hover-scrub; one-click export with audit | ❌ raw JSON link |
| Forensic timeline with recording+event fusion | Scrubbable 24h ribbon per camera; event markers overlay | ❌ broken (C-1), recordings not shown |
| Alerts with severity triage and ack workflow | Inbox pattern, ack/assign, severity routing config in-UI | ❌ none |
| Analytics surfaces (heatmap/dwell/count) | Built-in widgets, time-range compare | ❌ none |
| RBAC-aware UI (visibility by permission) | Menus/actions adapt to role | ❌ loads full UI for any token; `auth/me` permissions unused |
| Privacy controls in-UI (masks, retention) | Draw masks on frame; per-camera retention sliders | ❌ none (backend ready) |
| Onboarding | Guided first-run: add camera → verify feed → tune | ❌ login → empty tables |
| Shift-survivability | 24/7 sessions, refresh, reconnect, toast stack | ❌ 15-min silent logout (C-6) |

None of these are exotic; they are table stakes for the market the strategy document targets. The plan below sequences them by operator value.

---

## Part III — The Enterprise Redesign: Design System, IA, and Screens

### III.0 Design principles (decided upfront)

1. **Operator first.** Every screen answers: "what do I need to know/do right now?" Wall-view and investigation flows beat administration aesthetics.
2. **Local-first visible.** Encryption, masks, retention, and audit are first-class UI citizens — the differentiator must be *seen* (trust surface, §III.6).
3. **CSP-native, not CSP-tolerant.** No inline styles, no inline handlers, no `innerHTML` with untrusted strings, SVG for data-driven geometry. The hardened header is a design constraint we thank, not fight.
4. **No build-step creep.** Keep the no-build vanilla core; adopt ES modules + a single pre-CSP-safe CSS file. If a component library ever enters, it must ship < 150 KB gz and work without a bundler (Web Components). Performance is a feature on edge hardware.
5. **WCAG 2.1 AA as floor.** 44px targets, 4.5:1 contrast, visible focus, keyboard-complete, `prefers-reduced-motion` respected.
6. **Progressive disclosure over density reduction.** Enterprise users want density; provide compact/comfortable toggle rather than hiding function.

### III.1 Design tokens (the foundation, week 1)

One file (`ui/tokens.css`), consumed by everything. Replaces the 9 ad-hoc variables with a semantic, state-complete set:

```css
:root {
  /* surfaces (GitHub-dark family, calibrated for NOC walls) */
  --surface-0: #0b0e14;  /* app background        */
  --surface-1: #11151c;  /* cards                 */
  --surface-2: #161b22;  /* raised: headers, popovers */
  --surface-3: #1c2330;  /* hover overlay         */
  /* text */
  --text-primary:   #e6edf3;  /* 13.9:1 on surface-0 */
  --text-secondary: #9aa7b8;  /*  7.2:1              */
  --text-disabled:  #566070;  /*  3.2:1 (non-text use only) */
  /* brand — fixed for contrast (fixes C-5) */
  --accent:        #2f81f7;   /* decor, borders, charts */
  --accent-strong: #1f6feb;   /* text-on-accent 4.7:1 */
  --accent-soft:   rgba(47,129,247,.16);
  /* status (semantic, one meaning each — fixes consistency finding) */
  --status-ok:      #3fb950;  /* text 5.7:1 on surface-1 */
  --status-warn:    #d29922;  /* 4.9:1 — degraded, uncertain */
  --status-crit:    #f85149;  /* 4.8:1 — offline, critical */
  --status-info:    #58a6ff;  /* known-identity, info     */
  /* fonts: system stack (no download) */
  --font-ui: ui-sans-serif, system-ui, "Segoe UI", Roboto, sans-serif;
  --font-mono: ui-monospace, "Cascadia Mono", Consolas, monospace; /* ids, hex, timestamps */
  /* type scale (1.2 ratio; floors at 13px for dense tables) */
  --text-xs: 12px; --text-sm: 13px; --text-md: 14px; --text-lg: 16px;
  --text-xl: 20px; --text-2xl: 26px; --text-3xl: 34px;
  /* spacing: 4px grid only */
  --sp-1: 4px; --sp-2: 8px; --sp-3: 12px; --sp-4: 16px; --sp-6: 24px; --sp-8: 32px;
  /* radii: 2-step scale */
  --radius-s: 6px; --radius-l: 10px;
  /* elevation via border+shadow (dark UI: shadows subtle) */
  --shadow-1: 0 1px 3px rgba(0,0,0,.4);
  --shadow-2: 0 8px 24px rgba(0,0,0,.5);
  /* layout */
  --header-h: 56px; --nav-w: 220px;
  /* motion (respect reduced-motion) */
  --motion-fast: 120ms cubic-bezier(.2,0,0,1);
}

@media (prefers-reduced-motion: reduce) {
  *, *::before, *::after { transition-duration: .01ms !important; animation-duration: .01ms !important; }
}
```

**Enforcement:** the audit script grows assertions on tokens (contrast pairs, target sizes) so a CSS regression is a build failure, not a discovery (§V).

### III.2 Information architecture — from 6 tables to 5 task-oriented zones

The nav becomes role-aware (permissions from `auth/me`, currently unused) and task-oriented:

```
┌ Monitor ────────────────────────────────┐
│ Overview      status wall: cameras, events, health, trends, alert feed │
│ Live View     camera grid, wall layouts, PTZ, snapshot                 │
├ Investigate ───────────────────────────┤
│ Events        searchable list → detail drawer → clip → export         │
│ Timeline      24h fused ribbon (recordings + events), scrub           │
│ Search        NL forensic search + filters (analytics/search)          │
├ Manage ────────────────────────────────┤
│ Cameras       add/edit/delete, privacy-mask editor, rules, retention   │
│ Identities    enroll + reference photos, embedding status              │
│ Alerts        routes, cooldowns, test-fire, delivery log             │
├ Admin ─────────────────────────────────┤
│ Users & Roles user CRUD, MFA enrollment, permission matrix            │
│ System        health detail, metrics, storage, retention dashboard    │
│ Audit         immutable log, filtered, exportable                     │
└ Trust (surface everywhere, owned here) ┘
│ Privacy       mask inventory, retention countdowns, data-subject erasure │
```

Zone grouping gives operators a mental model (Monitor → Investigate → Manage → Admin) instead of 6 peer tables. Every item above maps to an **existing backend endpoint** (the exposure gap is the work, not new APIs) — except where noted (§III.10 adds only 3 small endpoints).

### III.3 Screen-by-screen redesign

#### 1. Login (keep the best part: 3 fields)

- Split "MFA code" into a conditional second step (email+password → if `mfa_required` response, reveal code field with focus) — removes the confusing "if enabled" label.
- Error copy by status: 401 → "Incorrect email or password"; 423 → "Account temporarily locked — try again in N minutes" (fixes C-7); generic network → "Can't reach server".
- Session-expired re-login shows "Session expired" notice (fixes C-6 message half).
- Keep autocomplete attributes (already correct).
- Add "Remember this device" (marks refresh-token binding; security review needed) — P2.

#### 2. Overview (the new default screen)

```
┌───────────────────────────────────────────────────────────────┐
│ OVERVIEW                                    [time range ▾] [⚙] │
├──────────────┬──────────────┬──────────────┬──────────────────┤
│ Cameras      │ Events today  │ Identities   │ System health    │
│ 4/5 online   │ 25 (+3 unkn)  │ 6 enrolled   │ ● ok · DB ok     │
│ ⚠1 degraded  │ spark ▁▂▅▃▂  │              │ model ref-v0     │
├──────────────┴──────────────┴──────────────┴──────────────────┤
│ Camera status strip: [Lobby ●] [Warehouse ●] [Dock ●] [Gate ⚠]…│ ← one click to live
├───────────────────────────────────────┬───────────────────────┤
│ Events trend (24h, SVG bars)          │ Recent events (5)    │
│                                       │ thumb · Lobby · 14:02 │
│                                       │ thumb · Dock · 13:58 │ ← with thumbnails (fixes C-10 loop start)
├───────────────────────────────────────┤───────────────────────┤
│ Alert feed (last routes fired)        │ Privacy & retention   │
└───────────────────────────────────────┴───────────────────────┘
```

- Fixes C-9: dedicated summary numbers (see III.10 for the one new endpoint that makes this 1 request).
- Sparklines/trends as inline SVG (CSP-safe).
- Camera strip chips link straight to Live View (recognition over recall).
- Auto-refresh every 15s (visibility-aware pause) — the "no one clicks nav to refresh" fix.

#### 3. Live View (new; the operator's home screen)

- **Grid layouts:** 1×1, 2×2, 3×3, custom-drag (localStorage-persisted layout, no backend).
- **Player:** LL-HLS via native `<video>` + hls.js (only allowed 3rd-party dep, ~100 KB, but with native fallback when Safari). Ticket flow (`POST /api/live/ticket` → `GET /live/{id}/play`) wrapped in a `LiveController` with automatic ticket re-issue on 401 (the endpoint expires tickets short-lived).
- **Tile anatomy:** name · status dot · fps · latency badge · [snapshot] [audio?] buttons · overflow menu (stop transcode — the backend `POST /live/{id}/stop` exists).
- **Reaper awareness:** show "transcode expires in 4h" and idle countdown from `/live/streams` `idle_sec` — the backend already reports it; surface it.
- **Wall mode:** full-screen auto-cycle for NOC monitors (`F` for fullscreen, arrow keys to switch tiles).

#### 4. Events + Event Detail drawer (the investigation loop)

- **List:** thumbnail (snapshot URL already in API) · camera **name** (not hex) · identity chip (known → color+name, unknown → neutral) · type with icon · relative time ("14:02, 3m ago") · confidence bar · duration.
- **Filters:** camera *select* (fixes recall finding), status, type multi-select chips, time-range picker, confidence slider; **URL-encoded** so investigation views are shareable/bookmarkable.
- **Detail drawer** (replaces the raw-JSON link, fixes C-10): snapshot with **bbox overlay** (SVG), clip player (signed video_url), identity card, camera card, detail context (direction/dwell/count/zone chips), Export button → audited, generates signed URL with countdown and "Copy" action.
- **Bulk:** select-multiple → export manifest (P2).
- Keyboard: ↑/↓ navigate, Enter opens drawer, E exports (power-user path).

#### 5. Timeline (rebuilt as SVG, fixes C-1)

```
00:00   02    04    06    08    10    12    14    16    18    20    22   24:00
Lobby    ██████████████              ████  events ▲(hover: 14:02 unknown)
Dock         ███████    ▁▁▁▁ (recording density)   ▲▲▲
Gate                  ██████        ▲ (14:58 line_cross → click → drawer)
```

- One SVG ribbon per camera: recording coverage (from `/api/timeline` intervals), event markers layered above with shape-by-type (▲ line_cross, ● presence, ◆ anpr).
- **Scrub:** hover shows timestamp tooltip; click seeks the clip player (needs the clip endpoint; exists: `/api/events/{id}/clip`).
- Multi-camera stacked, synchronized cursor across all ribbons (the classic Genetec/Avigilon pattern).
- Zoom levels: 1h/6h/24h; drag-to-zoom range.

#### 6. Cameras (from read-only table to management)

- **Card grid** (default): thumbnail · name · status · resolution/fps · health · quick actions [Live] [Rules] [Masks] [⋯].
- **Detail page:** tabs — Streams (URLs masked, sub/main, ONVIF re-probe), Privacy Masks (the marquee editor, below), Rules (from `/api/cameras/{id}/rules`), Retention (per-camera override), Health history.
- **Privacy-mask editor (the differentiator made visible):** live snapshot as canvas; drag rectangles = normalized {x,y,w,h} written to `privacy_masks`; each rectangle requires a **reason label** (compliance field); preview shows exactly what the detector will skip. This is the UI that makes "privacy by design" tangible in demos and enterprise evaluations.
- **Add Camera wizard:** 3 steps — Discover (ONVIF WS-Discovery, backend ready) → Select & Credentials (SSRF-validated, never echoed) → Verify (live snapshot test) . First-run onboarding for empty states links here.
- Rules editor: per-type forms (line draw, zone draw, dwell seconds, count threshold) with inline validation against the backend schema.

#### 7. Identities (People → full enrollment)

- List: label · name · status · **faces enrolled count** (embedding rows exist) · last seen.
- Detail: reference photos gallery (`/api/persons/{id}/references` — upload exists in API), embedding quality, delete with **two-step confirm typed-label** (GDPR erasure is destructive; explain cascade: "removes 3 embeddings permanently").

#### 8. Alerts (new)

- **Routes table** (from `/api/alerts/routes`): name/channel/severity/rules-scope/cooldown/enabled toggle.
- **Test-fire** button (backend `/api/alerts/test` exists) with delivery log per route.
- **Recent deliveries feed** + failure surfacing (the worker's alert sender is silent in UI today).
- Route editor drawer: channel-specific fields, mask secret fields, cooldown slider with explanation, `_ALERT_DETAIL_KEYS` allowlist **displayed as "what will be sent"** — trust transparency (aligns with the report's security posture: operators see exactly what leaves the host).

#### 9. Analytics (new)

- Time-range + camera selector shared across widgets:
  - People-count trend (SVG line), Occupancy gauge + peak time, Dwell distribution, Breakdown by identity/camera (stacked bars), Heatmap (canvas, alpha-blended from `analytics/heatmap` bins).
- **NL Search box** (the wow feature, backend `analytics/search`): query history, example chips ("people at the dock after 6pm"), results → Events list with filters pre-filled.
- All charts CSP-safe inline SVG/canvas — no chart library (keeps the bundle honest; ~400 lines of chart code total).

#### 10. Admin (Users, System, Audit, Privacy)

- **Users:** table + drawer (role select from real roles, MFA status, force-logout via refresh-token revoke), permission matrix view per role (RBAC tables readable via API).
- **System:** health detail (fixes C-8 rendering), metrics charts from `/api/system/metrics` (once the C-11 backend fix lands), storage usage by camera, retention dashboard ("2,304 events expire in 12d").
- **Audit:** existing table + filters (action/user/result/date), row expand for detail JSON, CSV export (audited).
- **Privacy (new, strategic):** retention countdowns by data class, encryption status ("all 5 cameras' URLs encrypted · AES envelope"), mask inventory across cameras, **data-subject erasure workflow** (search person → preview cascade → typed-confirm).

### III.4 State design (loading / empty / error) — the invisible 80%

Standardized, token-driven, componentized:

| State | Pattern |
|---|---|
| Loading | Skeleton shimmer on first load; silent spinner on refresh; **never blank** |
| Empty | Illustration-lite + one action: "No cameras yet → [Add your first camera]" (onboarding); "No events match → [Clear filters]" |
| Error | Inline banner with **Retry** button; toast for background failures; offline banner if `/health/live` fails |
| Partial | Pill "2 of 5 cameras unreachable" instead of hiding all |
| Session | C-6 flow: 401 → silent refresh (once) → if fail, toast "Session expired" + login |

### III.5 Interaction & motion

- Transitions: 120ms opacity/transform only; drawer slide 200ms; `prefers-reduced-motion` honored (already in tokens).
- Toasts top-right, 4s auto-dismiss, hover-pause, max 3 stacked.
- Drawers: right-side 480px, focus-trapped, Escape closes, dimmed scrim.
- Tables: sticky header, zebra-free (borders), row hover reveals actions, 40px rows (compact 32px toggle).

### III.5b Keyboard map (complete operability)

```
g o / g l / g e …  go-to-zone shortcuts (gmail-style)
↑/↓   list navigation      Enter  open detail
/     focus search         Esc    close drawer
e     export visible       f     fullscreen (live view)
?     shortcut overlay
```

### III.6 The Trust Surface (product differentiator made visible)

A persistent, subtle presence communicating local-first security:

- Header lock chip: "🔒 Local · AES envelope · RBAC" (click → Privacy page).
- Every encrypted field renders with a small "encrypted" glyph instead of raw value.
- Export actions show "audited" tag; audit entries link back to the export action.
- Mask editor and retention countdowns as above.
- Rationale: the strategy doc positions privacy as the wedge; today the UI sells none of it. This is cheap to build (labels and links) and disproportionately effective in enterprise evals and demos.

### III.7 Responsive strategy

| Breakpoint | Layout |
|---|---|
| ≥1280 | Full sidebar + content |
| 720–1279 | Icon sidebar (labels on hover) |
| <720 | Bottom tab bar (Monitor/Investigate/Manage/More) + stacked cards, tables → card lists, live grid → swipeable 1×1 |

Mobile is for acknowledgment/triage, not deep investigation (enterprise reality); the plan optimizes mobile for: view alert → snapshot → acknowledge.

### III.8 Accessibility conformance plan

- **Targets:** WCAG 2.1 AA baseline; WCAG 2.2 criteria (focus appearance, target size) included; EN 301 549 procurement-ready.
- All C-3/C-4/C-5 fixes as part of the token rollout; aria-live for toasts; roles for drawers (dialog); skip-link; verified tab order (fixing the `login_inputs: 8` DOM duplication).
- **Automated gate:** axe-core in the Playwright suite (§V) — every view scanned per build; violations fail CI.

### III.9 Front-end architecture (no-build, grown-up)

```
ui/
  tokens.css        design tokens (III.1)
  base.css          reset + element styles
  components.css    btn, chip, table, drawer, toast, tile, form
  views/            one JS module per view (ES modules)
    overview.js live.js events.js timeline.js cameras.js
    identities.js alerts.js analytics.js admin/*.js
  core/
    api.js          fetch wrapper: auth, refresh-on-401 (fixes C-6), retry, abort
    router.js       hash router (#/events?camera=…): URL state, back/forward, deep links
    state.js        tiny pub/sub store (no framework)
    dom.js          esc()/h() helpers (fixes C-2), svg() builder
    format.js       time-relative, bytes, ids
  app.js            bootstrap (~60 lines)
```

- **Dependencies: hls.js only** (lazy-loaded on Live View), ~100 KB gz, works without bundler — justified by LL-HLS; native HLS in Safari.
- No virtual DOM, no build. ES modules over HTTP/2 are fine at this scale; server already serves static files.
- Estimated total: ~120 KB source across ~25 files — still 10× lighter than any React app, preserving the C-3 speed advantage measured at 0.55s TTI.

### III.10 Backend additions the UI needs (small, justified)

| Endpoint | Why | Size |
|---|---|---|
| `GET /api/dashboard/summary` | Overview in 1 request (cameras online, events today with breakdown, identities, health, retention countdowns) — fixes C-9 properly | ~40 lines |
| Fix C-11 (`get_current_user` as dependency) | Metrics for the System screen | 3 lines + test |
| `GET /api/cameras/{id}/snapshot` (single frame) | Mask editor + camera verification + live tile posters | ~30 lines, uses existing decoder |

No other new APIs — everything else in this plan wires existing endpoints (the exposure gap is the work).

### III.11 Rollout sequence & estimates

| Wave | Scope | Effort | Exit criteria |
|---|---|---|---|
| **0. Trust repairs** (P0/P1 defects) | C-2 XSS, C-1 timeline SVG rebuild, C-6 refresh flow, C-11 metrics fix, C-8 health, C-9 stat, C-7/C-12 states, tokens.css with contrast fixes | ~1.5 wk | 0 console errors; axe clean on all views; timeline visible under production CSP |
| **1. Investigation loop** | Event list redesign + detail drawer + clip/snapshot + export; SVG timeline with scrub; URL router | ~2 wk | Operator can go thumbnail → clip → export without dev tools; investigation shareable by URL |
| **2. Monitor** | Live View grid + wall mode; Overview rebuild with auto-refresh; alerts feed | ~2 wk | 24h shift session without silent logout; live grid usable on NOC display |
| **3. Manage** | Cameras detail + mask editor + rules editor + add-camera wizard; Identities with references; Alerts admin; Users; Privacy dashboard | ~3 wk | Every backend capability exposed; first-run onboarding complete |
| **4. Analytics & polish** | Analytics screens; NL search; a11y audit pass; shortcuts; density toggle; docs | ~2 wk | WCAG 2.1 AA statement publishable; demo-complete |
| **5. Hardening** | E2E suite in CI (§V), visual-regression on 12 key states, perf budget gate, telemetry (opt-in) | ~1 wk | CI enforces quality gates |

**Total: ~11–12 weeks** of focused frontend work. Waves 0–2 (≈5.5 weeks) transform the product from demo to operable console; waves 3–4 complete the enterprise story.

### III.12 Risks & mitigations

| Risk | Mitigation |
|---|---|
| Scope creep into backend rework | Only 3 backend items (III.10); everything else is wiring |
| Vanilla-JS maintainability at this scale | Strict module boundaries (core/ vs views/), pub/sub store, no cross-view imports; add lint (eslint) + e2e gates early |
| hls.js is the lone dependency | Lazy-load only on Live; Safari native fallback; document rationale in AGENTS.md |
| Accessibility regressions | axe-core in CI per view per build (not a one-time audit) |
| CSP breakage recurrence | E2E assertion: zero console errors on every view (C-1 would have been caught) |
| Breaking operators' habits | Ship nav zones with redirects; URL router keeps old anchors working for 1 release |

---

## Part IV — Success Metrics (how we'll know it worked)

| Metric | Baseline (measured) | Target |
|---|---|---|
| Console errors per session | 13 | **0** |
| Timeline segments visible | 0 px | 100% rendered, scrub-interactive |
| Interactive targets ≥44px & focus-visible | 0/24 | 100% |
| Contrast failures | 7 | 0 |
| API surface wired | 8/40 endpoints | 32+/40 |
| Views with empty/error/skeleton states | 0/6 | 100% |
| Investigation loop (event→clip→export) | impossible via UI | < 45 s, no dev tools |
| Session survivability | 15 min silent logout | 24 h+ with silent refresh |
| Keyboard-complete navigation | impossible | full list-nav + shortcuts |
| TTI (login) | 0.55 s | keep < 0.8 s |
| Payload | 15.9 KB | < 150 KB (incl. lazy hls.js) |
| WCAG | fails AA | **AA statement** publishable |
| a11y automation | none | axe in CI, 0 critical |

---

## Part V — Tooling: make the audit a permanent CI gate

The throwaway audit becomes `tests/ui/` (Playwright + pytest):

```
tests/ui/
  conftest.py        boots the app + seeded DB (reuse scripts/seed_dev_data.py fixtures)
  test_journeys.py   login, investigate, live, configure — per-wave expanded
  test_a11y.py       axe-core scan per view (blocks on critical)
  test_csp.py        assert 0 console errors per view (C-1 guard)
  test_design.py     token assertions: contrast pairs, 44px targets, focus rings (C-3/4/5 guards)
  test_flows.py      refresh-on-401, empty/error states, double-submit (C-6/12/13 guards)
```

Run in CI after the normal suite; adds ~90 s. This converts every measured finding in this report into a permanent regression test — the same discipline AGENTS.md mandates for backend fixes ("the test that would have caught the bug").

---

## Appendix A — Finding-to-fix traceability

| Finding | Severity | Fixed in |
|---|---|---|
| C-1 timeline CSP | P0 | Wave 0 (SVG timeline) |
| C-2 stored XSS | P0 | Wave 0 (esc()/dom.js) |
| C-11 metrics 500 | P1 | Wave 0 (backend, + regression test) |
| C-3 focus/keyboard | P1 | Wave 0 (tokens + a11y pass) |
| C-4 hit targets | P1 | Wave 0 (tokens) |
| C-5 contrast | P1 | Wave 0 (tokens) |
| C-6 session expiry | P1 | Wave 0 (api.js refresh) |
| C-7 lockout copy | P2 | Wave 0 (login states) |
| C-8 health undefined | P2 | Wave 0 (schema-driven health) |
| C-9 stat correctness | P2 | Wave 0 (summary endpoint) |
| C-10 raw-JSON drill-down | P2 | Wave 1 (detail drawer) |
| C-12 blank states | P2 | Wave 0 (state components) |
| C-13 double submit | P3 | Wave 3 (forms) |
| C-14 DOM duplication | P3 | Wave 1 (router) |
| Exposure gap (8/40) | strategic | Waves 1–4 |

## Appendix B — Reproducing the audit

```bash
# 1. seed a realistic dataset
.venv/bin/python scripts/seed_dev_data.py --fresh
# 2. boot the app
.venv/bin/python -m uvicorn apps.api.main:app --port 8777
# 3. run the audit passes
.venv/bin/python scripts/ui_audit.py --password '…' --out ui_audit/desktop
.venv/bin/python scripts/ui_audit.py --password '…' --out ui_audit/mobile --mobile
.venv/bin/python scripts/ui_design_metrics.py '…'
.venv/bin/python scripts/ui_probe_flows.py
```

Screenshots and raw findings live in `ui_audit/` (gitignored as audit artifacts; the *findings* are cited in this document).
