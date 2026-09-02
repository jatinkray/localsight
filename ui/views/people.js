// Identities — full enrollment (Wave 3).
//
// List: label · name · status · faces enrolled · enrolled date. Detail:
// reference enrollments (embedding metadata — the honest "we don't keep
// your photo" story), upload with double-submit guard, and GDPR erasure
// behind a typed-label confirm.

import { h, render } from "../core/dom.js";
import { api, can, getMe } from "../core/api.js";
import { skeletonRows, emptyState, errorState } from "../core/states.js";
import { toast } from "../core/toast.js";
import { label as fmtLabel, tone, shortId, fmtDateTime } from "../core/format.js";
import { navigate } from "../core/router.js";

export async function loadPeople(listEl, params = {}) {
  if (params.id) return personDetail(listEl, params.id);
  if (params.new === "1" && can("person:enroll")) return listWithForm(listEl);
  return listPeople(listEl);
}

async function fetchPeople() {
  const data = await api("/api/persons");
  return Array.isArray(data) ? data : [];
}

// ── list ─────────────────────────────────────────────────────────────

async function listPeople(listEl) {
  skeletonRows(listEl, 3);
  try {
    const people = await fetchPeople();
    render(listEl, people.length
      ? h("div", { class: "table-scroll" },
          h("table", {},
            h("thead", {}, h("tr", {},
              h("th", {}, "Label"), h("th", {}, "Name"), h("th", {}, "Status"),
              h("th", {}, "Faces"), h("th", {}, "Enrolled"))),
            h("tbody", {}, people.map(personRow)),
          ))
      : emptyState({
          icon: "👤", title: "No identities enrolled",
          hint: "Known-identity recognition needs at least one enrolled person.",
          action: can("person:enroll") ? h("button", {
            class: "primary", "data-act": "person-add",
            onClick: () => navigate("people", { new: "1" }),
          }, "Enroll a person") : null,
        }));
  } catch (err) {
    render(listEl, errorState(err, { noun: "identities", onRetry: () => listPeople(listEl) }));
  }
}

function personRow(p) {
  return h("tr", { "data-person": p.label },
    h("td", {},
      h("button", {
        class: "linklike mono",
        onClick: () => navigate("people", { id: p.id }),
      }, p.label)),
    h("td", {}, p.display_name || "—"),
    h("td", {}, h("span", { class: `pill ${tone(p.status)}` }, fmtLabel(p.status))),
    h("td", {}, h("span", { class: "count-chip" }, String(p.faces_enrolled ?? 0))),
    h("td", { class: "muted" }, fmtDateTime(p.created_at)));
}

// ── list + enroll form ────────────────────────────────────────────────

async function listWithForm(listEl) {
  await listPeople(listEl);
  const form = h("form", { class: "card form-col", "data-form": "person-new", novalidate: true },
    h("h3", {}, "Enroll a person"),
    h("p", { class: "muted" },
      "Reference photos are reduced to a locally-computed, encrypted face embedding — the image itself is never stored."),
    h("label", { class: "field-hint", for: "np-label" }, "Label (unique, used by rules and exports)",
      h("input", { id: "np-label", "data-field": "label", class: "mono",
        placeholder: "employee-001", required: true, autocomplete: "off" })),
    h("label", { class: "field-hint", for: "np-name" }, "Display name",
      h("input", { id: "np-name", "data-field": "name", placeholder: "Alice Nguyen" })),
    h("div", { class: "form-row" },
      h("button", { class: "primary", type: "submit", "data-act": "person-create" }, "Enroll"),
      h("button", { class: "ghost", type: "button", onClick: () => navigate("people") }, "Cancel")),
  );
  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    const btn = form.querySelector("button[type=submit]");
    const lbl = form.querySelector("[data-field=label]").value.trim();
    const name = form.querySelector("[data-field=name]").value.trim();
    if (!lbl) return toast("A label is required", { tone: "warn" });
    btn.disabled = true; // C-13 double-submit guard
    try {
      await api("/api/persons", {
        method: "POST", body: JSON.stringify({ label: lbl, display_name: name }),
      });
      toast(`Enrolled ${lbl}`, { tone: "ok" });
      navigate("people");
    } catch (err) {
      toast(err.status === 409 ? "That label already exists" : err.message, { tone: "error" });
    } finally { btn.disabled = false; }
  });
  listEl.prepend(form);
  form.querySelector("[data-field=label]").focus();
}

// ── detail ───────────────────────────────────────────────────────────

async function personDetail(listEl, id) {
  skeletonRows(listEl, 4);
  let p;
  try {
    const people = await fetchPeople();
    p = people.find((x) => x.id === id);
  } catch (err) {
    return render(listEl, errorState(err, { noun: "person", onRetry: () => personDetail(listEl, id) }));
  }
  if (!p) {
    return render(listEl, emptyState({
      icon: "👤", title: "Person not found",
      hint: "The identity may have been erased.",
      action: h("button", { class: "ghost", onClick: () => navigate("people") }, "All identities"),
    }));
  }

  const root = h("div", { class: "cam-detail", "data-person-id": p.id },
    h("div", { class: "panel-head" },
      h("div", {},
        h("button", { class: "linklike", onClick: () => navigate("people") }, "People"),
        h("h2", {}, p.display_name || p.label),
        h("div", { class: "muted mono text-xs" }, `${p.label} · ${shortId(p.id)}`)),
      h("span", { class: `pill ${tone(p.status)}` }, fmtLabel(p.status))),
    h("div", { class: "card", "data-role": "refs" },
      h("h3", {}, "Reference enrollments"),
      h("p", { class: "muted" },
        "Each upload becomes a locally-computed, encrypted face embedding. The photo itself is never stored — there is nothing to leak, and nothing to subpoena."),
      h("ul", { class: "ref-meta", "data-role": "ref-list" })),
    can("person:enroll") ? h("form", { class: "card form-col", "data-form": "ref-upload" },
      h("h3", {}, "Add a reference photo"),
      h("label", { class: "field-hint", for: "ref-file" }, "Image (PNG/JPEG, max 10MB)",
        h("input", { id: "ref-file", type: "file", accept: "image/*", required: true })),
      h("button", { class: "primary", type: "submit", "data-act": "ref-upload" }, "Upload reference"),
    ) : null,
    can("person:delete") ? h("div", { class: "card" },
      h("h3", {}, "Delete identity (GDPR erasure)"),
      h("p", { class: "muted" },
        `Permanently removes this person and all ${p.faces_enrolled ?? 0} face embedding(s). Events stay — they were only ever linked to an anonymous track id, never to a name.`),
      h("button", { class: "ghost", "data-act": "delete-person" }, "Delete identity…"),
      h("div", { class: "confirm-zone hidden", "data-role": "delete-confirm" },
        h("label", { class: "field-hint", for: "del-confirm" },
          `Type the label “${p.label}” to confirm`,
          h("input", { id: "del-confirm", "data-field": "confirm-label", class: "mono", autocomplete: "off" })),
        h("div", { class: "form-row" },
          h("button", { class: "ghost", "data-act": "delete-person-confirm", disabled: true }, "Erase permanently"),
          h("button", { class: "ghost", type: "button", onClick: (e) => {
            e.currentTarget.closest(".confirm-zone").classList.add("hidden");
          } }, "Cancel"))),
    ) : null,
  );
  render(listEl, root);

  // reference metadata
  try {
    const refs = await api(`/api/persons/${p.id}/references`);
    const list = root.querySelector("[data-role=ref-list]");
    render(list, refs.references.length
      ? refs.references.map((r) => h("li", { "data-ref": r.id },
          h("span", {},
            h("strong", {}, r.model_version || "embedding"),
            h("span", { class: "muted text-xs" }, ` · ${r.dimension}-dim · quality ${(r.quality_score ?? 0).toFixed(2)}`)),
          h("span", { class: "muted" }, fmtDateTime(r.created_at))))
      : [h("li", { class: "muted" }, "No reference photos enrolled yet.")]);
  } catch { /* metadata unavailable — the honest-empty list stays */ }

  // upload wiring
  const upForm = root.querySelector("[data-form=ref-upload]");
  if (upForm) upForm.addEventListener("submit", async (e) => {
    e.preventDefault();
    const btn = upForm.querySelector("button[type=submit]");
    const input = upForm.querySelector("input[type=file]");
    const file = input.files && input.files[0];
    if (!file) return toast("Choose an image first", { tone: "warn" });
    btn.disabled = true;
    try {
      const fd = new FormData();
      fd.append("file", file);
      await api(`/api/persons/${p.id}/references`, { method: "POST", body: fd });
      toast("Reference enrolled", { tone: "ok" });
      input.value = "";
      navigate("people", { id: p.id }); // same-hash navigate re-runs the loader
    } catch (err) {
      toast(err.status === 413 ? "Image too large (max 10MB)" : err.message, { tone: "error" });
    } finally { btn.disabled = false; }
  });

  // delete wiring
  const delBtn = root.querySelector("[data-act=delete-person]");
  if (delBtn) delBtn.addEventListener("click", () => {
    root.querySelector("[data-role=delete-confirm]").classList.remove("hidden");
  });
  const confirmInput = root.querySelector("[data-field=confirm-label]");
  if (confirmInput) confirmInput.addEventListener("input", () => {
    const go = root.querySelector("[data-act=delete-person-confirm]");
    go.disabled = confirmInput.value.trim() !== p.label;
  });
  const goBtn = root.querySelector("[data-act=delete-person-confirm]");
  if (goBtn) goBtn.addEventListener("click", async () => {
    goBtn.disabled = true;
    try {
      await api(`/api/persons/${p.id}`, { method: "DELETE" });
      toast("Identity erased", { tone: "ok" });
      navigate("people");
    } catch (err) {
      toast(err.message || "Erase failed", { tone: "error" });
      goBtn.disabled = false;
    }
  });
}
