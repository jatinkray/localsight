// LocalVision dashboard — minimal vanilla SPA talking to the API.
(() => {
  const $ = (s) => document.querySelector(s);
  const api = (path, opts = {}) => fetch(path, {
    ...opts,
    headers: { "Content-Type": "application/json", ...(opts.headers || {}), ...authHeader() },
  }).then(async (r) => {
    if (r.status === 401) { logout(); throw new Error("unauthorized"); }
    const text = await r.text();
    return text ? JSON.parse(text) : {};
  });

  let token = localStorage.getItem("lv_token") || null;
  const authHeader = () => (token ? { Authorization: `Bearer ${token}` } : {});

  function show(view) {
    document.querySelectorAll("nav button").forEach((b) => b.classList.toggle("active", b.dataset.view === view));
    document.querySelectorAll(".panel").forEach((p) => p.classList.toggle("hidden", p.dataset.panel !== view));
    const _nav = document.getElementById("nav");
    if (_nav && _nav.classList.contains("open")) {
      _nav.classList.remove("open");
      const _t = document.getElementById("nav-toggle");
      if (_t) _t.setAttribute("aria-expanded", "false");
    }
    if (view === "dashboard") loadDashboard();
    if (view === "cameras") loadCameras();
    if (view === "events") loadEvents(0);
    if (view === "people") loadPeople();
    if (view === "timeline") loadTimeline();
    if (view === "audit") loadAudit();
  }

  async function login(e) {
    e.preventDefault();
    $("#login-error").textContent = "";
    const body = {
      email: $("#email").value, password: $("#password").value,
      mfa_code: $("#mfa").value || undefined,
    };
    try {
      const r = await fetch("/api/auth/login", {
        method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
      });
      if (!r.ok) { $("#login-error").textContent = "Login failed"; return; }
      const data = await r.json();
      token = data.access_token;
      localStorage.setItem("lv_token", token);
      enterApp();
    } catch { $("#login-error").textContent = "Login error"; }
  }

  function logout() {
    token = null; localStorage.removeItem("lv_token");
    $("#app").classList.add("hidden"); $("#login").classList.remove("hidden");
  }
  const enterApp = () => { $("#login").classList.add("hidden"); $("#app").classList.remove("hidden"); show("dashboard"); };

  async function loadDashboard() {
    const [health, cams, evs, people] = await Promise.all([
      api("/api/system/health"), api("/api/cameras"), api("/api/events?limit=1"), api("/api/persons"),
    ]).catch(() => [{}, { items: [] }, { total: 0 }, []]);
    const online = (cams.items || []).filter((c) => c.status === "ONLINE").length;
    const cards = [
      ["Cameras online", `${(cams.items || []).length ? online + "/" + (cams.items || []).length : 0}`],
      ["People detected today", (evs.total || 0)],
      ["Known identities", (people || []).length],
      ["System", (health.status || "?").toUpperCase()],
    ];
    $("#stat-cards").innerHTML = cards.map(([t, v]) =>
      `<div class="card stat"><h3>${t}</h3><div class="big">${v}</div></div>`).join("");
    const comp = (health.components || {});
    $("#health").innerHTML = `<h2>Health</h2>` + Object.entries(comp).map(([k, v]) =>
      `<div>${k}: <span class="pill ${v.status === "ok" ? "ok" : "bad"}">${v.status}</span></div>`).join("");
  }

  async function loadCameras() {
    const data = await api("/api/cameras");
    $("#cameras-list").innerHTML = (data.items || data || []).length
      ? `<div class="table-scroll"><table><thead><tr><th>Name</th><th>ID</th><th>Status</th><th>Health</th><th>Res</th></tr></thead><tbody>`
        + (data.items || data).map((c) => `<tr><td>${c.name}</td><td>${c.id.slice(0,8)}</td>
          <td><span class="pill ${c.status === "ONLINE" ? "ok" : "warn"}">${c.status}</span></td>
          <td>${c.health}</td><td>${c.resolution || "-"}</td></tr>`).join("") + `</tbody></table></div>`
      : `<p class="muted">No cameras configured.</p>`;
  }

  let evOffset = 0;
  async function loadEvents(offset) {
    evOffset = offset;
    const cam = $("#ev-camera").value, status = $("#ev-status").value;
    const qs = new URLSearchParams({ limit: 25, offset });
    if (cam) qs.set("camera_id", cam);
    if (status) qs.set("identity_status", status);
    const data = await api("/api/events?" + qs.toString());
    $("tbody", ).innerHTML = "";
    const tb = document.querySelector("#events-table tbody");
    tb.innerHTML = (data.items || []).map((e) => `<tr>
      <td>${e.camera_id.slice(0,8)}</td>
      <td>${e.identity_id ? e.identity_id.slice(0,8) : "-"}</td>
      <td><span class="pill ${e.identity_status === "known" ? "ok" : "warn"}">${e.identity_status}</span></td>
      <td>${new Date(e.timestamp_start).toLocaleTimeString()}</td>
      <td>${new Date(e.timestamp_end).toLocaleTimeString()}</td>
      <td>${e.confidence.toFixed(2)}</td>
      <td><a href="/api/events/${e.id}" target="_blank">view</a></td></tr>`).join("");
    $("#ev-page").textContent = String(Math.floor(offset / 25) + 1);
  }

  async function loadPeople() {
    const data = await api("/api/persons");
    $("#people-list").innerHTML = (data || []).length
      ? `<div class="table-scroll"><table><thead><tr><th>Label</th><th>Name</th><th>Status</th></tr></thead><tbody>`
        + data.map((p) => `<tr><td>${p.label}</td><td>${p.display_name || "-"}</td><td>${p.status}</td></tr>`).join("")
        + `</tbody></table></div>`
      : `<p class="muted">No identities enrolled.</p>`;
  }

  async function loadTimeline() {
    const date = $("#tl-date").value || new Date().toISOString().slice(0, 10);
    const cam = $("#tl-camera").value;
    const qs = new URLSearchParams({ date });
    if (cam) qs.set("camera_id", cam);
    const data = await api("/api/timeline?" + qs.toString());
    const rows = (data.timeline || []).map((t) => {
      const segs = t.intervals.map((iv) => {
        const s = new Date(iv.start).getHours() * 60 + new Date(iv.start).getMinutes();
        const e = new Date(iv.end).getHours() * 60 + new Date(iv.end).getMinutes();
        const left = (s / 1440) * 100, width = Math.max(0.5, ((e - s) / 1440) * 100);
        return `<div class="tl-seg" style="left:${left}%;width:${width}%"></div>`;
      }).join("");
      return `<div class="tl-cam"><strong>${t.camera_id.slice(0,8)} · ${t.label}</strong>
        <div class="tl-bar">${segs}</div></div>`;
    }).join("");
    $("#timeline-out").innerHTML = rows || `<p class="muted">No activity.</p>`;
  }

  async function loadAudit() {
    const data = await api("/api/audit?limit=100");
    document.querySelector("#audit-table tbody").innerHTML = (data.items || []).map((a) =>
      `<tr><td>${new Date(a.ts).toLocaleString()}</td><td>${a.username}</td><td>${a.action}</td>
       <td>${a.resource}</td><td>${a.result}</td><td>${a.source_ip}</td></tr>`).join("");
  }

  // wire up
  $("#login-form").addEventListener("submit", login);
  $("#logout").addEventListener("click", logout);
  $("#nav-toggle").addEventListener("click", () => {
    const nav = $("#nav");
    const open = nav.classList.toggle("open");
    $("#nav-toggle").setAttribute("aria-expanded", String(open));
  });
  document.querySelectorAll("nav button").forEach((b) => b.addEventListener("click", () => show(b.dataset.view)));
  $("#ev-search").addEventListener("click", () => loadEvents(0));
  $("#ev-prev").addEventListener("click", () => loadEvents(Math.max(0, evOffset - 25)));
  $("#ev-next").addEventListener("click", () => loadEvents(evOffset + 25));
  $("#person-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    await api("/api/persons", { method: "POST", body: JSON.stringify({
      label: $("#person-label").value, display_name: $("#person-name").value }) });
    $("#person-label").value = ""; $("#person-name").value = ""; loadPeople();
  });
  $("#tl-go").addEventListener("click", loadTimeline);
  $("#audit-load").addEventListener("click", loadAudit);

  if (token) enterApp(); else { $("#login").classList.remove("hidden"); }
})();
