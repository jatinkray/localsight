// API client with token lifecycle (fixes C-6: 15-min silent logout).
//
// Session design:
//  - access_token (15 min) lives in MEMORY only.
//  - refresh_token (7 days) in sessionStorage: survives reload in the tab,
//    dies with the tab — materially better than the old localStorage access
//    token (no 15-min dump, no persistent XSS-readable bearer token).
//  - On 401: ONE silent refresh-and-retry; if the refresh also fails, the
//    session is over → 'auth:expired' event → login screen with a notice.
//
// Refresh tokens rotate server-side (old jti revoked on use — replay of an
// old token 401s), so a single in-flight refresh is enforced here too.

const TOKEN_KEY = "lv_session";

/** @type {string|null} access token — memory only */
let accessToken = null;
/** @type {string|null} refresh token — sessionStorage backed */
let refreshToken = sessionStorage.getItem(TOKEN_KEY) || null;

/** @type {number|null} epoch-ms when the access token expires */
let expiresAt = null;

/** Refresh promise dedupe: concurrent 401s share one rotation. */
let refreshInFlight = null;

/** @type {{role: string, permissions: string[]}|null} /api/auth/me cache */
let meCache = null;

export function getMe() { return meCache; }

export function can(perm) {
  return Boolean(meCache && meCache.permissions.includes(perm));
}

async function fetchMe() {
  try {
    meCache = await api("/api/auth/me");
  } catch {
    meCache = null; // role-gated UI degrades to showing everything
  }
}

/** Fetch and cache /api/auth/me (called right after session establishment). */
export async function primeMe() {
  if (!accessToken) return;
  await fetchMe();
}

export function getAccessToken() { return accessToken; }

export function hasSession() { return Boolean(accessToken || refreshToken); }

export function sessionSecondsLeft() {
  return expiresAt ? Math.max(0, (expiresAt - Date.now()) / 1000) : null;
}

function persistSession(access, refresh, expiresIn) {
  accessToken = access;
  expiresAt = expiresIn ? Date.now() + expiresIn * 1000 : null;
  if (refresh) {
    refreshToken = refresh;
    sessionStorage.setItem(TOKEN_KEY, refresh);
  }
}

export function clearSession() {
  accessToken = null;
  refreshToken = null;
  expiresAt = null;
  meCache = null;
  sessionStorage.removeItem(TOKEN_KEY);
}

/**
 * Establish a session from a login/refresh response body.
 * Returns {secondsLeft}.
 */
export function establishSession(body) {
  persistSession(body.access_token, body.refresh_token, body.expires_in);
  return { secondsLeft: sessionSecondsLeft() };
}

function authHeaders() {
  return accessToken ? { Authorization: `Bearer ${accessToken}` } : {};
}

function isAuthEventName(name) {
  return name === "auth:expired" || name === "auth:restored";
}

/** Broadcast session events without importing a framework. */
export function onAuthEvent(handler) {
  window.addEventListener("auth:expired", handler);
  window.addEventListener("auth:restored", handler);
}

function emit(name) {
  if (!isAuthEventName(name)) return;
  window.dispatchEvent(new Event(name));
}

/** Attempt token rotation; resolves true on success. */
async function tryRefresh() {
  if (!refreshToken) return false;
  if (!refreshInFlight) {
    refreshInFlight = (async () => {
      try {
        const r = await fetch("/api/auth/refresh", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ refresh_token: refreshToken }),
        });
        if (!r.ok) return false;
        const body = await r.json();
        // The body IS the session: {access_token, refresh_token, expires_in}.
        persistSession(body.access_token, body.refresh_token, body.expires_in);
        return true;
      } catch {
        return false;
      } finally {
        refreshInFlight = null;
      }
    })();
  }
  return refreshInFlight;
}

/**
 * Signed fetch that transparently refreshes once on 401.
 * Throws ApiError on failure (status, message, body available).
 */
export async function api(path, opts = {}) {
  const hadSession = Boolean(accessToken);
  // FormData bodies (file uploads) set their own multipart boundary —
  // forcing application/json on them corrupts the upload.
  const isForm = typeof FormData !== "undefined" && opts.body instanceof FormData;
  const send = () => fetch(path, {
    ...opts,
    headers: {
      ...(isForm ? {} : { "Content-Type": "application/json" }),
      ...(opts.headers || {}),
      ...authHeaders(),
    },
  });

  let response = await send();

  // Never refresh-retry the auth endpoints themselves (login's own 401 means
  // "wrong credentials", refresh's 401 means "session over").
  const isAuthCall = path.startsWith("/api/auth/");
  if (response.status === 401 && !isAuthCall && (await tryRefresh())) {
    emit("auth:restored");
    response = await send();
  }

  if (response.status === 401 && hadSession) {
    // A session existed and is now unrecoverable (refresh impossible/failed).
    clearSession();
    emit("auth:expired");
    throw new ApiError(401, "Session expired");
  }

  const text = await response.text();
  let body = null;
  if (text) {
    try { body = JSON.parse(text); } catch { body = text; }
  }

  if (!response.ok) {
    const detail = body && typeof body === "object" ? body.detail : undefined;
    throw new ApiError(response.status, typeof detail === "string" ? detail : `Request failed (${response.status})`, body);
  }
  return body;
}

export class ApiError extends Error {
  constructor(status, message, body = null) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.body = body;
  }
}

/** Explicit logout: revoke the refresh token server-side, then clear. */
export async function logout() {
  const token = refreshToken;
  clearSession();
  if (!token) return;
  try {
    await fetch("/api/auth/logout", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ refresh_token: token }),
    });
  } catch {
    /* network gone: session is cleared locally regardless */
  }
}

/** Restore a session from a surviving refresh token (page reload). */
export async function restoreSession() {
  if (accessToken || !refreshToken) return Boolean(accessToken);
  const ok = await tryRefresh();
  if (!ok) clearSession();
  else await fetchMe(); // role-gated UI needs the role before first paint
  return ok;
}

/**
 * Proactive refresh: rotate shortly BEFORE expiry so in-flight views never
 * see a 401. Returns a stop handle. Scheduling: half of expires_in.
 */
export function startRefreshLoop() {
  const tick = async () => {
    const left = sessionSecondsLeft();
    if (left != null && left <= 90 && refreshToken) {
      const ok = await tryRefresh();
      if (!ok) { clearSession(); emit("auth:expired"); }
    }
  };
  const handle = setInterval(tick, 30_000);
  // Also refresh eagerly on tab focus (covers long-sleep laptops).
  document.addEventListener("visibilitychange", () => {
    if (document.visibilityState === "visible") tick();
  });
  return handle;
}
