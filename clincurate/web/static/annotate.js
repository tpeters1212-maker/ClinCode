// ClinCurate annotation workspace. Plain JS, no external libraries, so the
// app works offline and nothing loads from outside the laptop.
(function () {
  "use strict";
  const data = JSON.parse(document.getElementById("cc-data").textContent);
  const token = document.querySelector('meta[name="cc-token"]').content;
  const form = data.form;
  const text = data.note.text;
  const item = data.item;
  const readonly = data.readonly || !item;
  const R = Object.assign({}, item ? item.responses : {});
  if (!Array.isArray(R[form.screen])) R[form.screen] = [];
  if (!Array.isArray(R.evidence)) R.evidence = [];
  const colors = Object.fromEntries(form.domains.map((d) => [d.name, d.color]));
  const dlabels = Object.fromEntries(form.domains.map((d) => [d.name, d.label]));
  const NUM = /^\s*[-+]?\d+(?:\.\d+)?\s*(?:°|deg(?:rees?)?)?\s*$/i;
  const UNCLEAR = new Set(["?", "unclear", "unc"]);

  const el = (tag, attrs, ...kids) => {
    const n = document.createElement(tag);
    for (const [k, v] of Object.entries(attrs || {})) {
      if (k === "class") n.className = v;
      else if (k === "text") n.textContent = v;
      else if (k.startsWith("on")) n.addEventListener(k.slice(2), v);
      else if (v !== false && v != null) n.setAttribute(k, v === true ? "" : v);
    }
    for (const k of kids.flat()) if (k != null) n.append(k.nodeType ? k : document.createTextNode(k));
    return n;
  };

  // ---------------------------------------------------------------- text
  function renderText(container, start, end) {
    container.dataset.start = start;
    container.textContent = "";
    const cuts = new Set([start, end]);
    const hits = data.hits.filter((h) => h.e > start && h.s < end);
    const evs = R.evidence.filter((v) => v.end > start && v.start < end);
    for (const h of hits) { cuts.add(Math.max(h.s, start)); cuts.add(Math.min(h.e, end)); }
    for (const v of evs) { cuts.add(Math.max(v.start, start)); cuts.add(Math.min(v.end, end)); }
    const pts = [...cuts].sort((a, b) => a - b);
    for (let i = 0; i < pts.length - 1; i++) {
      const a = pts[i], b = pts[i + 1];
      const span = document.createElement("span");
      span.textContent = text.slice(a, b);
      const h = hits.find((x) => x.s <= a && x.e >= b);
      if (h) { span.className = "hit"; span.style.background = colors[h.d]; span.title = dlabels[h.d]; }
      const ev = evs.find((x) => x.start <= a && x.end >= b);
      if (ev) { span.classList.add("ev"); span.style.setProperty("--ev", colors[ev.label] || "#475569"); }
      container.append(span);
    }
  }

  function renderLeft() {
    const focus = document.getElementById("focus");
    focus.textContent = "";
    for (const s of data.snippets) {
      const box = el("div", { class: "notetext snippet" });
      renderText(box, s.s, s.e);
      focus.append(el("div", { class: "snipwrap" },
        el("div", { class: "sniptags" }, s.d.map((d) => el("span", { class: "chip", style: `background:${colors[d]}` }, dlabels[d]))),
        box));
    }
    if (!data.snippets.length) focus.append(el("p", { class: "muted" }, "No highlighted words in this note. Read the full note."));
    else focus.append(el("p", { class: "hint" }, "Only sentences with highlighted words are shown. Check the Full note tab when you need more context."));
    renderText(document.getElementById("fulltext"), 0, text.length);
  }

  // tabs, meta, legend
  document.querySelectorAll(".tab").forEach((b) => b.addEventListener("click", () => {
    document.querySelectorAll(".tab").forEach((x) => x.classList.toggle("active", x === b));
    document.getElementById("focus").classList.toggle("hidden", b.dataset.tab !== "focus");
    document.getElementById("full").classList.toggle("hidden", b.dataset.tab !== "full");
    document.getElementById("legend").classList.toggle("hidden", b.dataset.tab !== "full");
  }));
  document.getElementById("notemeta").textContent =
    [data.note.patient_id && `Patient ${data.note.patient_id}`, data.note.date, data.note.type].filter(Boolean).join(" · ");
  const legend = document.getElementById("legend");
  legend.classList.add("hidden");
  for (const d of form.domains) legend.append(el("span", { class: "chip", style: `background:${d.color}` }, d.label));

  // ------------------------------------------------------------ evidence
  const pop = document.getElementById("evpop");
  let pending = null;
  function selectionOffsets() {
    const sel = window.getSelection();
    if (!sel || sel.isCollapsed || !sel.rangeCount) return null;
    const range = sel.getRangeAt(0);
    const box = (n) => (n.nodeType === 1 ? n : n.parentElement).closest(".notetext");
    const a = box(range.startContainer), b = box(range.endContainer);
    if (!a || a !== b) return null;
    const pre = document.createRange();
    pre.selectNodeContents(a);
    pre.setEnd(range.startContainer, range.startOffset);
    let s = +a.dataset.start + pre.toString().length;
    let e = s + range.toString().length;
    while (s < e && /\s/.test(text[s])) s++;
    while (e > s && /\s/.test(text[e - 1])) e--;
    return e > s ? { start: s, end: e, rect: range.getBoundingClientRect() } : null;
  }
  if (!readonly) {
    const buttons = document.getElementById("evbuttons");
    for (const d of form.domains) {
      buttons.append(el("button", { type: "button", style: `background:${d.color}`, onclick: () => addEvidence(d.name) }, d.label));
    }
    document.querySelector(".pane.left").addEventListener("mouseup", () => setTimeout(() => {
      pending = selectionOffsets();
      if (!pending) { pop.classList.add("hidden"); return; }
      const pane = document.querySelector(".pane.left").getBoundingClientRect();
      pop.style.top = `${pending.rect.bottom - pane.top + document.querySelector(".pane.left").scrollTop + 6}px`;
      pop.classList.remove("hidden");
    }, 0));
    document.addEventListener("keydown", (e) => { if (e.key === "Escape") pop.classList.add("hidden"); });
  }
  function addEvidence(domain) {
    if (!pending) return;
    R.evidence.push({ label: domain, start: pending.start, end: pending.end });
    if (!R[form.screen].includes(domain)) R[form.screen].push(domain);
    pending = null;
    pop.classList.add("hidden");
    window.getSelection().removeAllRanges();
    renderLeft();
    renderForm();
    changed();
  }

  // ---------------------------------------------------------------- form
  const formEl = document.getElementById("form");
  const usableQ = form.record.find((q) => q.name === "note_usable");
  const isUnusable = () => usableQ && R.note_usable && R.note_usable !== usableQ.options[0];

  function choiceField(f, fieldName) {
    const multi = f.type === "multi_choice";
    const cur = R[fieldName];
    const wrap = el("div", { class: "options" });
    for (const opt of f.options) {
      const on = multi ? Array.isArray(cur) && cur.includes(opt) : cur === opt;
      const missing = Object.values(form.missing).includes(opt);
      wrap.append(el("button", {
        type: "button", class: `opt${on ? " on" : ""}${missing ? " missing" : ""}`, disabled: readonly,
        "aria-pressed": on ? "true" : "false",
        onclick: () => {
          if (multi) {
            const v = Array.isArray(R[fieldName]) ? R[fieldName] : [];
            const missingSet = new Set(Object.values(form.missing));
            // Not documented and Unclear exclude every other choice.
            if (v.includes(opt)) R[fieldName] = v.filter((x) => x !== opt);
            else if (missing) R[fieldName] = [opt];
            else R[fieldName] = v.filter((x) => !missingSet.has(x)).concat(opt);
          } else {
            R[fieldName] = R[fieldName] === opt ? null : opt;
          }
          renderForm();
          changed();
        },
      }, opt));
    }
    return wrap;
  }

  function numericField(f) {
    const input = el("input", {
      type: "text", inputmode: "decimal", class: "num", value: R[f.name] ?? "", disabled: readonly,
      placeholder: "number",
      oninput: (e) => { R[f.name] = e.target.value; checkNumeric(f, e.target); changed(); },
    });
    const box = el("div", { class: "numwrap" }, input, f.units ? el("span", { class: "units" }, f.units) : null,
      el("span", { class: "numerr" }), el("span", { class: "hint" }, "Blank = not documented · ? = unclear"));
    setTimeout(() => checkNumeric(f, input), 0);
    return box;
  }

  function numericError(f, v) {
    if (v == null || String(v).trim() === "" || UNCLEAR.has(String(v).trim().toLowerCase())) return "";
    if (!NUM.test(String(v))) return "Numbers only, or ? if unclear";
    const n = parseFloat(v);
    if (f.range && (n < f.range[0] || n > f.range[1])) return `Expected ${f.range[0]} to ${f.range[1]}`;
    return "";
  }
  function checkNumeric(f, input) {
    const msg = numericError(f, input.value);
    input.classList.toggle("bad", !!msg);
    input.parentElement.querySelector(".numerr").textContent = msg;
  }

  function fieldBlock(f) {
    let control;
    if (f.type === "numeric") control = numericField(f);
    else if (f.type === "text") control = el("textarea", { rows: 2, disabled: readonly, oninput: (e) => { R[f.name] = e.target.value; changed(); } }, R[f.name] || "");
    else control = choiceField(f, f.name);
    return el("div", { class: "field", "data-field": f.name },
      el("div", { class: "flabel" }, f.label, f.help ? el("details", { class: "help" }, el("summary", {}, "?"), el("div", {}, f.help)) : null),
      control);
  }

  function renderForm() {
    const scroll = formEl.parentElement.scrollTop;
    formEl.textContent = "";
    const unusable = isUnusable();

    if (usableQ) {
      formEl.append(el("fieldset", { class: "record" }, el("legend", {}, usableQ.label), choiceField(usableQ, usableQ.name)));
    }
    if (!unusable) {
      const screen = el("fieldset", { class: "screen" }, el("legend", {}, "What does this note document?"),
        el("p", { class: "hint" }, "Tick every topic the note mentions. Questions for that topic will appear."));
      const boxes = el("div", { class: "options" });
      for (const d of form.domains) {
        const on = R[form.screen].includes(d.name);
        boxes.append(el("button", {
          type: "button", class: `opt topic${on ? " on" : ""}`, style: `--c:${d.color}`, disabled: readonly,
          onclick: () => {
            R[form.screen] = on ? R[form.screen].filter((x) => x !== d.name) : R[form.screen].concat(d.name);
            renderForm();
            changed();
          },
        }, d.label));
      }
      screen.append(boxes);
      formEl.append(screen);

      for (const d of form.domains) {
        if (!R[form.screen].includes(d.name)) continue;
        const fs = el("fieldset", { class: "domain", style: `--c:${d.color}`, "data-domain": d.name }, el("legend", {}, d.label));
        d.fields.forEach((f) => fs.append(fieldBlock(f)));
        const evs = R.evidence.map((v, i) => [v, i]).filter(([v]) => v.label === d.name);
        const evbox = el("div", { class: "evlist" }, el("div", { class: "flabel" }, "Evidence"));
        if (!evs.length) evbox.append(el("p", { class: "hint" }, "Select text in the note on the left, then click this topic."));
        for (const [v, i] of evs) {
          evbox.append(el("div", { class: "evq" }, el("q", {}, text.slice(v.start, v.end)),
            readonly ? null : el("button", { type: "button", class: "linkish", title: "Remove", onclick: () => { R.evidence.splice(i, 1); renderLeft(); renderForm(); changed(); } }, "Remove")));
        }
        fs.append(evbox);
        formEl.append(fs);
      }
    }

    for (const q of form.record) {
      if (q === usableQ) continue;
      formEl.append(el("fieldset", { class: "record" }, el("legend", {}, q.label),
        q.type === "text"
          ? el("textarea", { rows: 3, disabled: readonly, oninput: (e) => { R[q.name] = e.target.value; changed(); } }, R[q.name] || "")
          : choiceField(q, q.name)));
    }

    if (!readonly) {
      formEl.append(el("div", { class: "errors hidden", id: "errors" }),
        el("div", { class: "actions" },
          el("button", { type: "button", class: "primary big", onclick: submit }, item.status === "submitted" ? "Resubmit and next" : "Submit and next"),
          el("a", { class: "button", href: item.queue_url }, "Back to my notes"),
          el("span", { class: "hint" }, "Ctrl+Enter submits")));
    }
    formEl.parentElement.scrollTop = scroll;
  }

  // ---------------------------------------------------------------- saving
  let active = 0, lastTouch = Date.now(), timer = null, saving = null, dirty = false, status = item ? item.status : "";
  ["mousemove", "keydown", "mousedown", "scroll", "wheel"].forEach((ev) =>
    document.addEventListener(ev, () => { lastTouch = Date.now(); }, { passive: true, capture: true }));
  setInterval(() => { if (document.visibilityState === "visible" && Date.now() - lastTouch < 60000) active += 1; }, 1000);

  const bar = document.getElementById("statusbar");
  function renderStatus(msg) {
    bar.textContent = "";
    if (!item) { bar.append(el("strong", {}, "Preview: highlighting as annotators will see it")); return; }
    const p = item.progress;
    bar.append(
      el("a", { href: item.queue_url }, `${item.who} · ${item.batch}`),
      el("div", { class: "bar" }, el("span", { style: `width:${Math.round(100 * p.done / Math.max(1, p.total))}%` })),
      el("strong", {}, `${p.left} left`), el("span", { class: "muted" }, `${p.done} of ${p.total} done`),
      el("span", { class: `savestate ${msg === "Not saved" ? "bad" : ""}` }, msg || (readonly ? "Read only" : status === "submitted" ? "Submitted" : "Saved")));
  }

  function post(st) {
    const sent = active;
    return fetch(item.save_url, {
      method: "POST", keepalive: true, headers: { "Content-Type": "application/json", "X-CC-Token": token },
      body: JSON.stringify({ responses: R, status: st, seconds: sent }),
    }).then((r) => r.json().then((j) => ({ ok: r.ok, j }))).then(({ ok, j }) => {
      if (!ok || !j.ok) throw new Error(j.error || "Save failed");
      active -= sent;
      item.progress = j.progress;
      status = j.status;
      return j;
    });
  }
  function changed() {
    if (readonly) return;
    dirty = true;
    renderStatus("Saving…");
    clearTimeout(timer);
    timer = setTimeout(flush, 600);
  }
  function flush() {
    if (!dirty) return Promise.resolve();
    dirty = false;
    saving = post("draft").then(() => renderStatus(), () => { dirty = true; renderStatus("Not saved"); });
    return saving;
  }
  window.addEventListener("beforeunload", (e) => { if (dirty) { flush(); e.preventDefault(); e.returnValue = ""; } });
  document.addEventListener("visibilitychange", () => { if (document.visibilityState === "hidden") flush(); });

  function validate() {
    const errs = [];
    document.querySelectorAll(".field.err").forEach((n) => n.classList.remove("err"));
    const mark = (name) => { const n = document.querySelector(`[data-field="${name}"]`); if (n) n.classList.add("err"); };
    if (usableQ && !R.note_usable) errs.push(`Answer "${usableQ.label}"`);
    if (!isUnusable()) {
      if (!R[form.screen].length) errs.push("Tick at least one topic, or mark the note as not usable.");
      for (const d of form.domains) {
        if (!R[form.screen].includes(d.name)) continue;
        for (const f of d.fields) {
          const v = R[f.name];
          if (f.type === "numeric") {
            if (numericError(f, v)) { errs.push(`${f.label}: ${numericError(f, v)}`); mark(f.name); }
          } else if (f.type !== "text" && (v == null || v === "" || (Array.isArray(v) && !v.length))) {
            errs.push(`${d.label}: ${f.label}`); mark(f.name);
          }
        }
      }
    }
    return errs;
  }

  function submit() {
    const errs = validate();
    const box = document.getElementById("errors");
    if (errs.length) {
      box.textContent = "";
      box.append(el("strong", {}, "Before submitting, answer these (choose Not documented if the note does not say):"),
        el("ul", {}, errs.map((x) => el("li", {}, x))));
      box.classList.remove("hidden");
      const first = document.querySelector(".field.err") || box;
      first.scrollIntoView({ block: "center", behavior: "smooth" });
      return;
    }
    box.classList.add("hidden");
    clearTimeout(timer);
    dirty = false;
    renderStatus("Submitting…");
    Promise.resolve(saving).then(() => post("submitted")).then((j) => {
      window.location = j.next ? item.item_url + j.next : item.queue_url;
    }, (e) => { dirty = true; renderStatus("Not saved"); alert(`Could not submit: ${e.message}`); });
  }
  document.addEventListener("keydown", (e) => {
    if ((e.ctrlKey || e.metaKey) && e.key === "Enter" && !readonly) { e.preventDefault(); submit(); }
  });

  renderLeft();
  renderForm();
  renderStatus();
})();
