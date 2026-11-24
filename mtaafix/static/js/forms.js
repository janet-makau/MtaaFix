// Small helpers used by many forms.
(function () {
  "use strict";

  // REQ-29: disable the submit button after the first click, so a form is only sent once.
  // (The server also ignores a repeated submission of the same report form.)
  document.querySelectorAll("form[data-submit-once]").forEach((form) => {
    form.addEventListener("submit", () => {
      form.querySelectorAll("button[type=submit], input[type=submit]").forEach((button) => {
        button.disabled = true;
        if (button.tagName === "INPUT") { button.value = "Sending…"; } else { button.textContent = "Sending…"; }
      });
    });
  });

  // Live character count for text areas with a maxlength, e.g. "24 / 2000 characters".
  // data-count-min shows how many more are needed before the form will accept it.
  document.querySelectorAll("textarea[maxlength]").forEach((area) => {
    const max = Number(area.getAttribute("maxlength"));
    const min = Number(area.dataset.countMin || 0);
    const out = document.createElement("div");
    out.className = "form-text char-count";
    out.setAttribute("aria-live", "polite");
    const help = area.nextElementSibling && area.nextElementSibling.classList.contains("form-text")
      ? area.nextElementSibling : area;
    help.insertAdjacentElement("afterend", out);
    const update = () => {
      const n = area.value.length;
      const short = min && n > 0 && n < min;
      out.textContent = short ? `${min - n} more characters needed` : `${n} / ${max} characters`;
      out.classList.toggle("is-short", Boolean(short));
    };
    area.addEventListener("input", update);
    update();
  });

  // Photo preview: show a thumbnail and the file size, and warn early if it is too big.
  document.querySelectorAll("input[type=file][data-preview]").forEach((input) => {
    const box = document.getElementById(input.dataset.preview);
    const MAX_BYTES = 5 * 1024 * 1024;
    let url = null;
    input.addEventListener("change", () => {
      if (url) URL.revokeObjectURL(url);
      box.replaceChildren();
      const file = input.files && input.files[0];
      if (!file) { box.hidden = true; return; }
      const mb = (file.size / (1024 * 1024)).toFixed(1);
      const text = document.createElement("span");
      if (file.size > MAX_BYTES) {
        text.className = "text-danger fw-bold";
        text.textContent = `This photo is ${mb} MB. Choose one under 5 MB.`;
      } else {
        url = URL.createObjectURL(file);
        const img = document.createElement("img");
        img.src = url;
        img.alt = "Preview of the photo you chose";
        box.appendChild(img);
        text.className = "small text-muted";
        text.textContent = `${file.name}, ${mb} MB`;
      }
      box.appendChild(text);
      box.hidden = false;
    });
  });
})();
