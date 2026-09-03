# LocalSight Marketing Site — Principal UI/UX Review

**Scope:** `localsight.github.io` (GitHub Pages, static HTML/CSS/JS, no build)
**Method:** live audit (Playwright: structure, perf, axe-core, mobile), benchmark
study (Frigate, Supabase, PostHog, Scrypted, VideoraIQ), NN/g usability findings,
OSS-marketing pattern analysis.
**Reviewer stance:** the site is already strong — fast (0.74s load, 101KB transfer),
honest, well-structured after the problem-first restructure. This review is about
converting a *good* site into a *high-converting* one.

---

## 1. Where the site stands today (audit facts)

| Dimension | Measured | Verdict |
|---|---|---|
| Load time (live) | 0.74 s, DCL 595 ms | Excellent |
| Transfer size | 101 KB (89 KB images, 5 KB JS) | Excellent |
| Console errors | 0 | Clean |
| Mobile overflow | none; hamburger works | Clean |
| axe violations | 4 (empty `th`, heading-order jump, nested `aside` landmark, 5× links-in-text-block) | Must fix — the product itself ships zero-violation pages; the marketing site should not lag the product |
| Page height | ~9,500 px desktop / 16,158 px mobile (≈10–18 viewports) | Long; needs internal momentum |
| CTAs | 2 in hero, 2 in bottom CTA — **zero between** sections 2–11 | The biggest conversion gap |
| Social proof | none (stars: 1, forks: 0 — correctly not shown) | Needs an honest alternative |
| Nav | 9 items; label mismatch ("Capabilities" vs section's "AI engines") | Trim/align |

**Section flow (current):** hero → trust strip → problem → tour → engines →
how → compare → privacy → open source → quick start → cameras → CTA.
The narrative arc (problem → answer → proof → differentiator → action) is right.
Gaps are *within* sections, not the order.

---

## 2. Benchmark patterns (what the leaders do that we don't)

**Frigate (closest OSS competitor, 35.5k stars):** verb-led H1 sentence
("Monitor your security cameras with locally processed AI"), 4 plain-English
feature blocks each answering a *user job*, a live **Demo** link, testimonials,
and a paid-model upsell (Frigate+). Their page is simple — their proof is the
star count and the demo.

**Supabase (OSS-marketing gold standard):** outcome H1 ("Build in a weekend,
scale to millions"), dual CTA (Start / Request demo), then a dedicated
**social-proof** section, product-dashboard screenshots, ecosystem logos,
customer stories, community, and an "Open source from day one" section with the
line we echo: *"Read, contribute, self-host. You're never locked in."*

**PostHog:** "Shameless CTA" section, usage pricing transparency, social proof
directly after the hero.

**VideoraIQ (commercial):** problem-first (we adopted this), 16 named engine
tiles (we adopted), 3-step model (adopted), pricing tiers + ROI calculator,
FAQs, 9 momentum CTAs.

**Convergent pattern across all five:** value-prop hero → **proof within one
viewport** → product visuals → differentiator → action at *every* scroll depth.
We have the first, third and fourth. We're missing proof and scroll-depth CTAs.

---

## 3. Findings (severity-ranked)

### F-01 — No social proof anywhere (HIGH, conversion)
Users cannot verify claims at the moment they're deciding. Star counts are 1 —
correctly not shown; never fake it. But LocalSight owns *honest, verifiable
metrics*: 140 automated tests (97 unit + 43 browser e2e), 12 AI engines,
zero third-party network calls, MIT, WCAG 2.1 AA (axe-verified), 0.74s landing
weight. **Fix:** a metrics strip directly under the hero ("140 automated
tests · 12 AI engines · WCAG 2.1 AA · MIT license") — every number clickable
to its evidence (CI runs, test suite, axe gate).

### F-02 — CTA desert between hero and page bottom (HIGH, conversion)
"Get started" appears at scrollY 597 and then not again until ~9,000px.
VideoraIQ ends *every* section with a next step; users decide at every scroll
depth. **Fix:** one contextual micro-CTA at the end of tour ("See it running —
quick start"), compare ("Read the docs"), engines ("Get started"). Keep them
ghost-styled so the hero primary stays visually dominant (banner-blindness
research: over-styled repeats get ignored; understated repeats get clicked).

### F-03 — H1 is a category label, not an outcome (MEDIUM-HIGH)
"Local-first AI video intelligence" describes the architecture; Frigate's H1 is
a verb sentence about the user's job. The lede carries the value prop, but H1
is what 100% of visitors read. **Fix (hypothesis to A/B):** outcome-led H1 —
e.g. *"Watch everything. Miss nothing."* — with the local-first story kept in
the pill + lede. The word "open source" should survive above the fold (it's
the differentiator; currently only in the badge list).

### F-04 — Tour section height kills the slider's momentum (MEDIUM)
Each tab panel renders 1 large shot + a 2-shot grid ≈ 1,300px of scroll; the
auto-slider rotates panels but the visitor scrolls past mid-rotation. The
section is 1,480px tall with ~4,300px of total shot content. **Fix:** make the
slider a true carousel — one image per slide, caption strip, dots + arrows —
and cut section height to ~60%. The 12 shots already exist; it's a presentation
change, not a content change.

### F-05 — axe violations on the marketing page (MEDIUM, must-fix for credibility)
The product ships axe-clean pages (Wave 5 gate); the marketing site must not
lag the product. Fixes are trivial: `<th scope="row">` empty-cell → label or
`aria-hidden` corner; footer h4s → `h2` with class (fixes heading-order);
`aside.privacy__panel` → `section` or top-level `aside`; note links get
underline styling. Also 19px-tall inline links on mobile — pad tap targets.

### F-06 — No FAQ / objection handling (MEDIUM, conversion + SEO)
VideoraIQ ships FAQs; every OSS leader has an "is it really free / does it
phone home / offline?" surface. LocalSight's answers are its *best* material:
zero third-party calls, air-gap capable, GPU optional, face ID off by default.
**Fix:** 6–8 question FAQ before the CTA section (also rich-results eligible).

### F-07 — No structured data / weak social card (LOW-MEDIUM, SEO)
No JSON-LD `SoftwareApplication` (name, license MIT, offers $0, featureList,
screenshot). og.svg is a generic mark; a real product screenshot og-image
(the overview shot exists at 1440px) lifts link-preview CTR in shares.

### F-08 — Nav label drift + crowding (LOW)
Section eyebrow says "AI engines", nav says "Capabilities". 9 nav items is
at the ceiling; rename to match the section, and consider merging
"How it works" out of nav (it's a narrative section, not a destination).

### F-09 — Hero animation risk is managed, verify on low-end (LOW)
The detection-field canvas is aria-hidden, removed under
`prefers-reduced-motion`, pointer-events pass through to CTAs (verified).
Remaining risk: cheap Android devices — cap device pixel ratio at 2 (done)
and drop node count below 480px width (currently 14 boxes at all sizes).

### F-10 — No install friction-killer (LOW-MEDIUM)
Quick start is two tabs and honest, but the single most shareable artifact in
OSS marketing is a **copy-paste one-liner**. A `docker compose up` clip with a
copy button at the *top* of quick start (before prose) shortens
time-to-value-perception.

---

## 4. Prioritized roadmap

**P0 — this week (trust + conversion mechanics, ~half a day):**
1. Fix all four axe violations + mobile tap-target padding (F-05).
2. Metrics strip under hero with evidence links (F-01).
3. Three ghost micro-CTAs at section ends (F-02).
4. Rename nav "Capabilities" → "AI engines" (F-08).
5. JSON-LD SoftwareApplication + og-image to the overview screenshot (F-07).

**P1 — next iteration (hero + tour surgery):**
6. A/B the outcome-led H1 (F-03) — the current H1 is the control.
7. Tour → true one-shot carousel with dots/arrows; cut section height ~40% (F-04).
8. FAQ section, 6–8 honest answers (F-06).
9. Copy-paste one-liner at the top of Quick start (F-10).
10. Mobile sticky "Get started" bar (below 720px only).

**P2 — when there's real usage to show:**
11. "Running in the wild" stories — even 3 self-hosted cases beat zero.
12. Live demo link (a seeded, read-only instance) — Frigate's best card.
13. Contributors / commit-activity strip once the repo has them (honest OSS proof).

## 5. What NOT to change (the site's soul — protect it)

- **The problem-first narrative** — rare among OSS projects; keep.
- **Real screenshots, no mock** — the tour's credibility comes from the fact
  that it's the shipping product. Never replace with illustrations.
- **The compare table** — it's the buyer's decision tool; keep factual tone.
- **The open-source section's "prove it works" framing** — verification over
  marketing is the LocalSight brand. Extend it (metrics strip), don't soften it.
- **Lightness** — 0.74s / 101KB is itself a feature on the privacy story.
  Do not add heavy animation libraries to "improve" the hero.
