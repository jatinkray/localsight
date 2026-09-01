// Toast stack — transient, non-blocking notices (state design §III.4).
// aria-live=polite so screen readers announce async outcomes.

const MAX_TOASTS = 3;
const DEFAULT_MS = 4000;

let stack = null;

function ensureStack() {
  if (!stack) {
    stack = document.createElement("div");
    stack.className = "toast-stack";
    stack.setAttribute("role", "status");
    stack.setAttribute("aria-live", "polite");
    document.body.append(stack);
  }
  return stack;
}

/**
 * toast("Saved", {tone: "ok", timeout: 4000})
 * tone: ok | warn | error (default: no tone border)
 */
export function toast(message, { tone = "", timeout = DEFAULT_MS } = {}) {
  const el = document.createElement("div");
  el.className = `toast ${tone}`.trim();
  el.textContent = message;
  const s = ensureStack();
  s.append(el);
  while (s.children.length > MAX_TOASTS) s.removeChild(s.firstChild);

  let killed = false;
  const kill = () => {
    if (killed) return;
    killed = true;
    el.remove();
  };
  el.addEventListener("mouseenter", () => clearTimeout(handle));
  el.addEventListener("mouseleave", () => { handle = setTimeout(kill, 1200); });
  let handle = setTimeout(kill, timeout);
  return kill;
}
