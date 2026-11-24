// AI help on the "Report a problem" form (SDS 4.3, process 2.0; REQ-9 to REQ-11, REQ-13).
//
// 1. After the resident stops typing the description, ask the server for a category and select it,
//    unless the resident has already picked one themselves. The choice is always theirs to change.
// 2. When the form is sent, first ask the server whether a nearby open report describes the same
//    problem. If so, show the duplicate warning and let the resident join that report or send theirs.
//    The photo is only uploaded with the final submit, so nothing is stored if they join instead.
// If the AI is off, slow or failing, the server answers with null (or the request fails) and the
// form simply works as it did before.
(function () {
  "use strict";

  const form = document.getElementById("report-form");
  if (!form) return;

  const el = (id) => document.getElementById(id);
  const description = form.querySelector("[name=description]");
  const category = form.querySelector("[name=category]");
  const latitude = form.querySelector("[name=latitude]");
  const longitude = form.querySelector("[name=longitude]");
  const token = form.querySelector("[name=submission_token]");
  const csrf = form.querySelector("[name=csrf_token]");
  const note = el("ai-category-note");
  const modalEl = el("duplicate-modal");

  const TYPING_PAUSE_MS = 900;
  const REQUEST_TIMEOUT_MS = 15000;   // a little more than the server's 10 s AI limit
  const MIN_LENGTH = 10;

  let residentChoseCategory = false;
  let lastDescriptionSent = "";
  let typingTimer = null;
  let checkedKey = null;              // description|category|location already checked for duplicates
  let sending = false;

  async function postJSON(url, body) {
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), REQUEST_TIMEOUT_MS);
    try {
      const response = await fetch(url, {
        method: "POST",
        headers: { "Content-Type": "application/json", "X-CSRFToken": csrf ? csrf.value : "" },
        body: JSON.stringify(body),
        credentials: "same-origin",
        signal: controller.signal,
      });
      if (!response.ok) throw new Error("HTTP " + response.status);
      return await response.json();
    } finally {
      clearTimeout(timer);
    }
  }

  // ------------------------------------------------------------ 1. category suggestion
  category.addEventListener("change", () => {
    residentChoseCategory = true;
    note.classList.add("d-none");
  });

  description.addEventListener("input", () => {
    clearTimeout(typingTimer);
    typingTimer = setTimeout(suggestCategory, TYPING_PAUSE_MS);
  });

  async function suggestCategory() {
    const text = description.value.trim();
    if (residentChoseCategory || text.length < MIN_LENGTH || text === lastDescriptionSent) return;
    lastDescriptionSent = text;
    try {
      const data = await postJSON(form.dataset.aiCategoryUrl, { description: text, token: token.value });
      if (residentChoseCategory || !data.category_id) return;
      const option = category.querySelector(`option[value="${data.category_id}"]`);
      if (!option) return;
      category.value = String(data.category_id);
      note.classList.remove("d-none");
    } catch (_) {
      /* no suggestion: the resident chooses the category (REQ-13) */
    }
  }

  // ------------------------------------------------------------ 2. duplicate check before sending
  const modal = modalEl && window.bootstrap ? new window.bootstrap.Modal(modalEl) : null;
  let shownDuplicate = null;

  form.addEventListener("submit", async (event) => {
    if (sending || !modal) return;                       // already decided, or no modal support
    const key = [description.value.trim(), category.value, latitude.value, longitude.value].join("|");
    const ready = description.value.trim().length >= MIN_LENGTH && Number(category.value) > 0
      && latitude.value && longitude.value;
    if (!ready || key === checkedKey) return;          // let the server validate and report errors

    event.preventDefault();
    setBusy(true);
    checkedKey = key;
    try {
      const data = await postJSON(form.dataset.aiDuplicateUrl, {
        description: description.value.trim(), category_id: Number(category.value),
        latitude: Number(latitude.value), longitude: Number(longitude.value), token: token.value,
      });
      if (data.duplicate) {
        showDuplicate(data.duplicate);
        setBusy(false);
        return;
      }
    } catch (_) {
      /* the check failed: send the report as normal (REQ-13) */
    }
    submitForReal();
  });

  function showDuplicate(dup) {
    shownDuplicate = dup;
    el("dup-reference").textContent = dup.reference;
    el("dup-category").textContent = dup.category;
    const statusEl = el("dup-status");
    statusEl.textContent = dup.status;
    statusEl.className = "status status-" + dup.status.toLowerCase().replace(/\s+/g, "_");
    el("dup-distance").textContent = `about ${dup.distance_m} m from your pin`;
    el("dup-description").textContent = dup.description;   // textContent: never inserted as HTML
    el("dup-error").classList.add("d-none");
    modal.show();
  }

  el("dup-new").addEventListener("click", () => {
    modal.hide();
    submitForReal();
  });

  el("dup-join").addEventListener("click", async () => {
    if (!shownDuplicate) return;
    const button = el("dup-join");
    button.disabled = true;
    try {
      const url = form.dataset.aiJoinUrl.replace(/\/0\/join$/, `/${shownDuplicate.id}/join`);
      const data = await postJSON(url, { token: token.value });
      window.location.assign(data.redirect);
    } catch (_) {
      el("dup-error").classList.remove("d-none");
      button.disabled = false;
    }
  });

  function submitForReal() {
    sending = true;
    setBusy(true);
    // The form has a button named "submit", which hides form.submit(), so call the original method.
    // It does not fire the "submit" event again; the server's one-time token stops any repeat.
    HTMLFormElement.prototype.submit.call(form);
  }

  function setBusy(busy) {
    form.querySelectorAll("button[type=submit], input[type=submit]").forEach((button) => {
      button.disabled = busy;
      const label = busy ? "Sending…" : "Submit report";
      if (button.tagName === "INPUT") button.value = label; else button.textContent = label;
    });
  }
})();
