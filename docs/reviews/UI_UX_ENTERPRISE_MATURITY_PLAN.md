# LocalSight UI/UX Enterprise Maturity Plan

**Author:** Principal Designer / UI-UX Engineering
**Date:** September 2026 (post-Wave 5)
**Status:** Proposed — next phase after the completed redesign (waves 0–5)

---

## 0. Executive summary

The 11–12 week redesign is complete and enforced by CI gates (axe-core
zero violations, zero console errors under CSP, 12-state visual
regression, perf budgets). The product now looks and behaves like a
serious console. This scan — 19 screen states, Playwright-instrumented
(`scripts/ui_maturity_scan.py`, screenshots + computed-style telemetry
in `ui_maturity/`) — asks the next question: **what separates this from
what a security operations team runs for a living?**

The answer is not styling. The foundation (tokens, a11y, honest states,
RBAC-designed UI) is enterprise-grade. What remains are the
**operational workhorses** an enterprise expects and the **account
lifecycle** a security product must own:

1. **Data workhorses** — the events/audit tables can't sort, can't bulk
   act, can't export a result set (only single-event export exists).
   An analyst investigating a Thursday can't slice by it.
2. **Time is ambiguous** — timestamps render as bare clocks
   (`15:30:12`, `5h ago`) with no date, no timezone, no user-set
   timezone. In a multi-site product this is a compliance issue, not a
   nicety.
3. **Account lifecycle gaps** — MFA enrollment API exists with **no UI
   to use it**; there is no password change anywhere in the product
   (found while resetting the demo admin password — requires a DB
   script today); no user profile screen; session/SERP surfaces absent.
4. **Density for professionals** — every control is ≥44px tall (WCAG
   floor); data-dense screens (tables, route lists, mask rows) need a
   compact mode that stays accessible, so one screen shows work, not
   three screens of scrolling.
5. **Toast host + global patterns** — feedback is per-view; no global
   toast host, no command palette for the 11-view keyboard story, no
   saved filters/share for investigations.

Full detail: findings E-1 … E-14 below with evidence, severity, and a
3-wave roadmap with exit criteria. Everything proposed is measured, CSP-
safe (SVG attributes, `h()`/`render()`), and lands behind the existing
CI gates — no regressions can merge silently.

---

## 1. Method

- **Instrument:** `scripts/ui_maturity_scan.py` (read-only; logs in as
  the demo admin; screenshots at 1440×900; computed-style telemetry:
  typography scale, colors in use, control geometry, table semantics,
  affordances, form labeling, timestamp formats, landmark/drawer
  semantics, honest-state presence).
- **States scanned (19):** login; dashboard; live; cameras;
  camera-detail; camera-masks; camera-rules; events; event-drawer;
  timeline; analytics; people; person-detail; alerts; route-editor;
  users; user-editor; privacy; audit.
- **Cross-checks:** direct DOM interaction (sort headers, focus trap,
  filter empty states, header controls), API surface greps, DB queries.
- **What this plan is NOT:** a restyle. Tokens, contrast, WCAG 2.1 AA,
  honest states are done and gated. This is operational maturity.

---

## 2. Findings

Severity: **P0** = blocks enterprise deployment story; **P1** = visible
gap a trial customer hits in week 1; **P2** = polish/retention.

### E-1 · Tables are not sortable (P0)
**Evidence:** `aria-sort` absent on every table (scan: all 19 states);
users/audit/events `<th>` have no handlers (verified in DOM: `[None ×5]`).
APIs return fixed-order results; events API has limit/offset but no
`sort` param.
**Why it matters:** "show me today's intrusions by confidence" is the
job. Sorting is assumed competence in any data product.
**Fix:** clickable `<th aria-sort>` (server-side `sort=` + `dir=` param,
whitelisted columns); arrow affordance via existing pill/badge classes.

### E-2 · No result-set export (P0)
**Evidence:** scan `export_buttons: 0` on events/audit/analytics;
per-event export exists in the drawer (`events:export`-gated, audited)
but the LIST cannot be exported.
**Fix:** "Export CSV" on events/audit/analytics respecting current
filters (permission-gated, audited, streamed ≤50k rows, CSV injection
escaping). Same button pattern as the drawer's.

### E-3 · No bulk operations (P1)
**Evidence:** scan `bulk_checkbox: False` everywhere.
**Fix:** row checkboxes + a bulk bar (acknowledge/export) on events
where the workflow is real; skip fake bulk on screens where it isn't
(persons: GDPR erasure must stay deliberate, typed-confirm — keep).

### E-4 · Timestamps carry no date or timezone (P0)
**Evidence:** scan `tz_hint: False`, `iso_count: 0` across all states;
events render `15:30:12` + `5h ago` (DOM-verified). No user timezone
setting exists anywhere.
**Fix:** render `Sep 2, 15:30:12 UTC` (user-tz configurable in a new
account screen; UTC default); tooltips with ISO 8601; analytics axis
labels gain the tz suffix. Multi-site = unambiguous time or bust.

### E-5 · MFA enrollment has no UI (P0 — security story)
**Evidence:** `/api/auth/mfa/setup` + `/mfa/verify` exist
(apps/api/routers/auth.py:205-214) and are never referenced from `ui/`
(grep: zero hits outside login's code-entry field). Users view shows
"MFA off" pill only (users.js:66). DB: all 4 demo users `mfa_enabled=0`.
**Why:** the backend security is ahead of the frontend — the inverse of
the original audit. An enterprise buyer that reads "TOTP MFA" in the
docs and finds no way to turn it on will not forgive it.
**Fix:** "Security" card in a new Account view: enroll (QR + secret +
verify code), disable-with-confirmation (audited), per-user status in
Users admin (admin-initiated reset w/ typed confirm).

### E-6 · No password change / account screen (P0)
**Evidence:** no endpoint (grep `password` in routers: only login
verify), no UI. The demo admin password was reset via a DB script this
turn — with an audit entry written manually. Users cannot rotate their
own credentials; admins can't either.
**Fix:** `POST /api/auth/password` (old+new, Argon2 verify, rate
limited, audited, invalidates other sessions' refresh tokens) + Account
view form (write-only fields, double-submit guard, strength meter with
honest copy — no theater).

### E-7 · Density only as a body class; data screens stay tall (P1)
**Evidence:** scan control heights: `min_h 18–24px` chips vs 44px
buttons on the same screens; density toggle exists but affects spacing
only.
**Fix:** true compact table mode (36–38px rows, 32px controls inside
tables/lists, still ≥24px touch target per WCAG 2.2 AA exception for
inline secondary controls — document the reasoning in the tooltip);
persist with existing `lv-density` key.

### E-8 · No global toast/feedback host (P1)
**Evidence:** scan `toast_host: False` (per-view toasts render, but
there's no single aria-live region; screen readers get no global
announcement channel).
**Fix:** one `#toast` host (aria-live="polite", role="status"), queue,
dedupe; every view routes through it. (Most views already have the
pattern — unify.)

### E-9 · Focus trap exists but drawer is not labeled "modal" to AT (P2)
**Evidence:** drawer `role=dialog aria-modal=true` (good), focus stays
trapped over 6 tabs (verified) — but no `aria-labelledby` → the drawer
announces "Event detail" only via `aria-label`; scrim click + Esc work.
**Fix:** `aria-labelledby` to the drawer's heading id; keep trap.

### E-10 · No saved/shareable filters beyond URL hash (P1)
**Evidence:** router deep-links views+params (Wave 1 win), but filters
aren't captured in the hash — only camera/status reach the URL via
manual navigation; "send me what you see" = screenshot.
**Fix:** serialize active filters into the hash (shareable
investigations, the Wave-1 exit criterion, extended); "Copy link"
button on events/audit/analytics.

### E-11 · Analytics lacks date-range presets + comparison (P1)
**Evidence:** analytics has `range` select (24h default) but no
presets ("this shift", "this week") or period comparison; charts are
static-render per load.
**Fix:** preset chips + A/B compare (two ranges, delta on headline
numbers); keep CSP-safe SVG geometry.

### E-12 · No command palette / global search (P2)
**Evidence:** 11 views + `g-` shortcuts + `?` overlay exist (good
keyboard story), but no `Ctrl-K` to jump to camera/event/person by
name.
**Fix:** small palette over the existing GOTO map (fuzzy match on
cameras/persons/recent events; ≤10 results; full keyboard path).

### E-13 · Users view lacks session management (P1)
**Evidence:** refresh tokens rotate server-side (good) but admins can't
see or revoke active sessions per user; no "sessions" anywhere in `ui/`.
**Fix:** Users → user detail → "Active sessions" (device, last seen,
revoke) + "revoke all" — API: list-by-user + delete-by-user on the
existing tokens router (needs audit entries).

### E-14 · Audit view: no filtering by user/action/date (P1)
**Evidence:** audit renders a flat table (verified headers: Time, User,
Action, Resource, Result, IP; 35 rows; no pagination, no filter inputs —
scan `filter_inputs` counted only the events view's).
**Fix:** filter bar (user, action, result, date range) + pagination +
CSV export (E-2 pattern). Compliance reviewers live here.

---

## 3. Per-screen designer's notes (from the 19 screenshots + telemetry)

- **login** — clean, honest errors, MFA field ready (hidden). With E-5,
  add an "MFA required" state to this screen (enforced flag later).
- **dashboard** — strong: honest stat cards (3/5 online, degraded
  badge), health rows, auto-refresh. Add tz-stamped "last updated"
  (E-4) and a "view shift report" entry to Analytics (E-11).
- **live** — the best screen: honest "Stream unavailable" tiles, layout
  switch. Wall mode candidate for a second-look pass on NOC displays
  (P2: clock overlay + camera-name size at 4m distance).
- **cameras / detail / masks / rules** — tab structure is right; mask
  editor drawing is delightful. Rules editor: add per-rule enable
  toggles in the list (currently only route-level pause).
- **events + drawer** — the workhorse. Needs E-1/E-2/E-3/E-4/E-10 the
  most. Row hover reveals the actions (good affordance discipline).
- **timeline** — CSP-safe SVG, scrub works. Add day-jump + tz label.
- **analytics** — NL search is the differentiator; charts are clean.
  E-11 presets + export make it report-grade.
- **people / person-detail** — typed-confirm erasure is exactly right
  (GDPR theater-free). Add "recent appearances" summary card.
- **alerts / route-editor** — route rows are honest (enabled/cooldown).
  Test-fire is a great trust move. Add delivery-log drilldown per route.
- **users / user-editor** — role select + active toggle present. Needs
  E-5/E-6/E-13 to close the account story.
- **privacy** — the differentiator screen. With E-5's Security card
  here (or in Account), the trust story is complete: data map,
  retention, masks, erasure, AND your own credentials.
- **audit** — E-14. This screen sells to compliance.

---

## 4. Roadmap (3 waves, ~6 weeks total)

### Wave M1 — "The analyst's Tuesday" (E-1, E-2, E-4, E-14) ~2wk  ✅ SHIPPED
Sortable, exportable, time-stamped, filterable data surfaces.
**Exit (met, verified by the maturity scan):** Events sorts by any column
(server-side whitelist; `aria-sort` on 7 headers) and exports the filtered
set as CSV (96-row demo export verified, audited, CSV-injection-safe);
all timestamps render UTC-suffixed end to end (74 tz labels on Events,
191 on Audit; zero bare clocks); Audit filters by user/action/result/date,
paginates, and exports (57-row export verified). Existing CI gates +
probes stay green (90 unit, 43 e2e, 5 wave probes).

### Wave M2 — "The account story" (E-5, E-6, E-13 + Account view) ~2wk
MFA enroll/disable, password change, session list/revoke, profile.
**Exit:** a user can enroll TOTP, rotate their password, see and revoke
their sessions — every action audited; admin can force-reset MFA and
revoke sessions (typed-confirm). New e2e: full MFA enroll flow with a
virtual authenticator (RFC 6238 in-test).

### Wave M3 — "Professional density" (E-3, E-7, E-8, E-10, E-11, E-12, E-9) ~2wk  ✅ SHIPPED
Bulk ops where real, compact tables, global toast host, shareable
filters, analytics presets/compare, command palette, drawer labelling.
**Exit (met, verified):** Events bulk selection (25 checkboxes on page,
select-all, bar with count + "Export selected" — the CSV endpoint takes
an explicit `ids` list, capped 1k, audited; 25-row bulk export verified);
compact mode tightens tables for real (36px rows, 24px inline controls,
WCAG 2.2 reasoning in the toggle tooltip); a single eager `#toast`
aria-live host created at module load; the drawer is labelled by its
heading (`aria-labelledby=drawer-title`) from the FIRST frame — skeleton
included; Copy link on Events/Audit/Analytics round-trips through the
hash (verified: sort=confidence&direction=asc reproduces ascending);
Analytics gained This-shift/24h/7d/30d preset chips + Compare mode with
delta pills (▼19% vs previous window verified); Ctrl-K palette with
fuzzy subsequence match over views/cameras/persons/recent events, full
keyboard path, axe-clean (an aria-required-children on the loading state
was caught and fixed during the wave). e2e 43/43 on regenerated
baselines; probes green; scan asserts bulk_boxes=25, toast_host,
copy_link, palette_wired, drawer_labelled — all true.

**Not proposed (deliberately):** theme customization (the token system
is the brand), dark-mode toggle (already dark, purpose-built),
framework migration (the no-build vanilla-JS decision is load-bearing:
payload budget is enforced at <300KB and hls.js stays the only vendor).

---

## 5. Verification discipline (unchanged, extended)

Every wave lands behind the standing gates — `pytest tests/ui -m ui`
(journeys, axe zero-violations, CSP console gate, visual baselines
with `UPDATE_BASELINES=1` for intended changes, perf budgets) — plus
new e2e coverage per feature. The maturity scan itself
(`scripts/ui_maturity_scan.py`) becomes a wave probe: each M-wave adds
assertions so the gaps it found can't return.
