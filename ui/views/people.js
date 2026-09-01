// People view — enrollment with double-submit protection (C-13), known-good
// table, empty/error states. Reference-photo upload ships in Wave 3.

import { h, render } from "../core/dom.js";
import { api } from "../core/api.js";
import { skeletonRows, emptyState, errorState } from "../core/states.js";
import { label, tone } from "../core/format.js";
import { toast } from "../core/toast.js";

export async function loadPeople(listEl) {
  skeletonRows(listEl, 3);
  try {
    const data = await api("/api/persons");
    const people = Array.isArray(data) ? data : [];
    render(listEl, people.length
      ? h("div", { class: "table-scroll" },
          h("table", {},
            h("thead", {}, h("tr", {},
              h("th", {}, "Label"), h("th", {}, "Name"), h("th", {}, "Status"),
            )),
            h("tbody", {}, people.map((p) => h("tr", {},
              h("td", { class: "mono" }, p.label),
              h("td", {}, p.display_name || "—"),
              h("td", {}, h("span", { class: `pill ${tone(p.status)}` }, label(p.status))),
            ))),
          ),
        )
      : emptyState({
          icon: "👤", title: "No identities enrolled",
          hint: "Enroll a person above to enable known-identity recognition.",
        }));
  } catch (err) {
    render(listEl, errorState(err, { noun: "identities", onRetry: () => loadPeople(listEl) }));
  }
}

export function wireEnrollForm(form, listEl) {
  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    const labelInput = document.getElementById("person-label");
    const nameInput = document.getElementById("person-name");
    const btn = form.querySelector("button[type=submit]");
    btn.disabled = true; // double-submit guard (C-13)
    try {
      await api("/api/persons", {
        method: "POST",
        body: JSON.stringify({ label: labelInput.value.trim(), display_name: nameInput.value.trim() }),
      });
      labelInput.value = "";
      nameInput.value = "";
      toast(`Enrolled ${labelInput.value.trim() || "person"}`, { tone: "ok" });
      await loadPeople(listEl);
    } catch (err) {
      toast(err.status === 400 ? "Label already exists" : "Enrollment failed — try again", { tone: "error" });
    } finally {
      btn.disabled = false;
    }
  });
}
