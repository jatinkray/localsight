// Density toggle — comfortable (40px rows) vs compact (32px), plan §III.5.
// The choice persists in localStorage (it's a personal display preference,
// not sensitive state — unlike tokens, which never touch localStorage).

const KEY = "lv-density";
const VALID = ["comfortable", "compact"];

export function wireDensity(btnEl) {
  if (!btnEl) return;
  const saved = localStorage.getItem(KEY);
  const current = VALID.includes(saved) ? saved : "comfortable";
  apply(current);
  btnEl.setAttribute("aria-pressed", String(current === "compact"));
  updateLabel(btnEl, current);

  btnEl.addEventListener("click", () => {
    const now = document.body.classList.contains("density-compact") ? "comfortable" : "compact";
    apply(now);
    localStorage.setItem(KEY, now);
    btnEl.setAttribute("aria-pressed", String(now === "compact"));
    updateLabel(btnEl, now);
  });
}

function apply(mode) {
  document.body.classList.toggle("density-compact", mode === "compact");
}

function updateLabel(btnEl, mode) {
  btnEl.title = mode === "compact"
    ? "Compact rows (32px) — press to switch to comfortable (40px)"
    : "Comfortable rows (40px) — press to switch to compact (32px)";
  btnEl.setAttribute("aria-label", btnEl.title);
  const span = btnEl.querySelector("span");
  if (span) span.textContent = mode === "compact" ? "Compact" : "Comfortable";
}

/** Applied at boot so the first paint already honors the saved choice. */
export function restoreDensity() {
  const saved = localStorage.getItem(KEY);
  if (VALID.includes(saved)) apply(saved);
}
