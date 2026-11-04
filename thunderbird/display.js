// Hamzaban for Thunderbird — runs inside every displayed e-mail.
// Draws the Persian translation panel above the message (in a shadow root so
// the e-mail's own CSS cannot touch it) and streams the translation into it.
(() => {
  if (window.__hamzaban) return;
  window.__hamzaban = true;
  const api = globalThis.messenger ?? globalThis.browser;

  const QUOTE_SELECTORS = [
    'blockquote[type="cite"]', ".moz-cite-prefix", ".gmail_quote", ".gmail_attr",
    ".yahoo_quoted", "#mail-editor-reference-message-container",
  ];
  const QUOTE_STOPPERS = ["#divRplyFwdMsg", "#appendonsend"]; // Outlook: all that follows is the quote
  const SKIP = ["#hamzaban-host", "#hamzaban-sel", "script", "style", "noscript", "template", "head", "title"];
  const BLOCK_TAGS = new Set(["P", "DIV", "LI", "UL", "OL", "TR", "TABLE", "H1", "H2", "H3", "H4",
    "H5", "H6", "PRE", "BLOCKQUOTE", "SECTION", "ARTICLE", "HEADER", "FOOTER", "HR", "DL", "DT",
    "DD", "CENTER", "FORM", "ADDRESS", "TBODY", "THEAD", "FIELDSET"]);
  const BLOCK_DISPLAY = /^(block|list-item|table|table-row|flex|grid|flow-root)$/;

  // ------------------------------------------------------------ e-mail -> text
  function extractText(skipQuotes) {
    const skip = SKIP.concat(skipQuotes ? QUOTE_SELECTORS : []).join(",");
    const stoppers = skipQuotes ? QUOTE_STOPPERS.join(",") : null;
    const out = [];
    let stop = false;

    const walk = (node, pre) => {
      if (stop) return;
      if (node.nodeType === Node.TEXT_NODE) {
        out.push(pre ? node.nodeValue : node.nodeValue.replace(/\s+/g, " "));
        return;
      }
      if (node.nodeType !== Node.ELEMENT_NODE || node.matches(skip)) return;
      if (stoppers && node.matches(stoppers)) { stop = true; return; }
      if (node.tagName === "BR") { out.push("\n"); return; }
      const cs = getComputedStyle(node);
      if (cs && (cs.display === "none" || cs.visibility === "hidden")) return;
      const block = BLOCK_TAGS.has(node.tagName) || (cs && BLOCK_DISPLAY.test(cs.display));
      const isPre = pre || (cs && cs.whiteSpace.startsWith("pre"));
      if (block) out.push("\n");
      for (const child of node.childNodes) walk(child, isPre);
      if (block) out.push("\n");
      else if (node.tagName === "TD" || node.tagName === "TH") out.push(" ");
    };
    walk(document.body, false);

    let text = out.join("")
      .replace(/ /g, " ")
      .replace(/[ \t]+\n/g, "\n")
      .replace(/\n[ \t]+/g, "\n");
    if (skipQuotes) text = text.split("\n").filter((l) => !/^\s*>/.test(l)).join("\n");
    return text.replace(/\n{3,}/g, "\n\n").trim().slice(0, 20000);
  }

  function looksPersian(text) {
    const letters = text.slice(0, 5000).match(/\p{L}/gu) || [];
    if (letters.length < 8) return true; // nothing worth translating
    const arabicScript = letters.filter((c) => /[؀-ۿ]/.test(c)).length;
    if (arabicScript / letters.length < 0.5) return false;
    const fa = (text.match(/[پچژگ]/g) || []).length * 3 + (text.match(/[یک]/g) || []).length;
    const ar = (text.match(/ة/g) || []).length * 3 + (text.match(/[يكى]/g) || []).length;
    return fa >= ar;
  }

  // ------------------------------------------------------------ font
  (async () => {
    try {
      const buf = await (await fetch(api.runtime.getURL("fonts/Vazirmatn.woff2"))).arrayBuffer();
      const face = new FontFace("HZ Vazirmatn", buf, { weight: "100 900" });
      await face.load();
      document.fonts.add(face);
    } catch (_) { /* falls back to Tahoma */ }
  })();

  // ------------------------------------------------------------ panel
  const ICONS = {
    speak: '<svg viewBox="0 0 24 24"><path d="M4 9v6h4l5 4V5L8 9H4z"/><path d="M16 8.5a5 5 0 0 1 0 7M18.5 6a8.5 8.5 0 0 1 0 12" fill="none"/></svg>',
    stop: '<svg viewBox="0 0 24 24"><rect x="6" y="6" width="12" height="12" rx="2"/></svg>',
    copy: '<svg viewBox="0 0 24 24"><rect x="8" y="8" width="12" height="12" rx="2" fill="none"/><path d="M16 8V6a2 2 0 0 0-2-2H6a2 2 0 0 0-2 2v8a2 2 0 0 0 2 2h2" fill="none"/></svg>',
    retry: '<svg viewBox="0 0 24 24"><path d="M20 12a8 8 0 1 1-2.3-5.6M20 4v5h-5" fill="none"/></svg>',
    close: '<svg viewBox="0 0 24 24"><path d="M6 6l12 12M18 6L6 18" fill="none"/></svg>',
  };

  const STYLE = `
    :host { all: initial; display: block; }
    * { box-sizing: border-box; }
    .card {
      --bg: #f3fbf9; --border: #cdeee7; --text: #0f1f1c; --muted: #52665f;
      --accent: #0f766e; --accent-soft: rgba(15,118,110,.09); --err: #b42318; --err-bg: #fdf0ee;
      direction: rtl; text-align: right;
      font-family: "HZ Vazirmatn", Vazirmatn, Tahoma, "Geeza Pro", sans-serif;
      font-size: 15px; line-height: 1.95; color: var(--text); background: var(--bg);
      border: 1px solid var(--border); border-radius: 14px;
      padding: 12px 18px 14px; margin: 8px 0 18px;
    }
    @media (prefers-color-scheme: dark) {
      .card { --bg: #10221f; --border: #1f433d; --text: #e3f2ee; --muted: #9db4ad;
        --accent: #5eead4; --accent-soft: rgba(94,234,212,.1); --err: #fda29b; --err-bg: #3a1a17; }
    }
    .bar { display: flex; align-items: center; justify-content: space-between; gap: 12px; flex-wrap: wrap; }
    .brand { display: flex; align-items: center; gap: 8px; font-weight: 700; font-size: 14px; }
    .brand .dot { width: 9px; height: 9px; border-radius: 50%; background: var(--accent); }
    .meta { font-weight: 400; color: var(--muted); font-size: 13px; }
    .actions { display: flex; gap: 6px; }
    button {
      font: inherit; font-size: 13px; line-height: 1; color: var(--accent);
      background: transparent; border: 1px solid var(--border); border-radius: 999px;
      padding: 6px 11px; cursor: pointer; display: inline-flex; align-items: center; gap: 6px;
    }
    button:hover { background: var(--accent-soft); }
    button:focus-visible { outline: 2px solid var(--accent); outline-offset: 2px; }
    button[hidden] { display: none; }
    svg { width: 15px; height: 15px; fill: currentColor; stroke: currentColor; stroke-width: 1.8;
      stroke-linecap: round; stroke-linejoin: round; }
    .icon-only { padding: 6px 8px; }
    .subject { font-weight: 700; font-size: 17px; margin: 10px 0 2px; }
    .subject:empty { display: none; }
    .body { margin-top: 8px; white-space: pre-wrap; overflow-wrap: anywhere; }
    .body .p { unicode-bidi: plaintext; min-height: 0.9em; }
    .status { display: flex; align-items: center; gap: 8px; color: var(--muted); font-size: 13px; margin-top: 8px; }
    .status[hidden] { display: none; }
    .spin { width: 14px; height: 14px; border-radius: 50%; border: 2px solid var(--border);
      border-top-color: var(--accent); animation: r .8s linear infinite; }
    @keyframes r { to { transform: rotate(360deg); } }
    .error { color: var(--err); background: var(--err-bg); border-radius: 10px; padding: 8px 12px; margin-top: 10px; font-size: 14px; }
    .error:empty { display: none; }
  `;

  function makeCard(host, { closable = true } = {}) {
    const root = host.attachShadow({ mode: "open" });
    root.innerHTML = `<style>${STYLE}</style>
      <div class="card" role="region" aria-label="ترجمه‌ی فارسی">
        <div class="bar">
          <div class="brand"><span class="dot"></span><span class="title">ترجمه‌ی فارسی</span><span class="meta"></span></div>
          <div class="actions">
            <button data-act="speak" hidden title="خواندن ترجمه با صدای فارسی">${ICONS.speak}<span>خواندن</span></button>
            <button data-act="copy" hidden title="کپی ترجمه">${ICONS.copy}<span>کپی</span></button>
            <button data-act="retry" hidden title="دوباره امتحان کن">${ICONS.retry}<span>دوباره</span></button>
            ${closable ? `<button data-act="close" class="icon-only" title="بستن" aria-label="بستن">${ICONS.close}</button>` : ""}
          </div>
        </div>
        <div class="subject"></div>
        <div class="body" aria-live="polite"></div>
        <div class="status"><span class="spin"></span><span class="status-text">در حال ترجمه…</span></div>
        <div class="error"></div>
      </div>`;
    const $ = (s) => root.querySelector(s);
    return { root, $, card: $(".card") };
  }

  function renderBody(el, text) {
    el.textContent = "";
    for (const line of text.split("\n")) {
      const p = document.createElement("div");
      p.className = "p";
      p.dir = "auto";
      p.textContent = line;
      el.appendChild(p);
    }
  }

  // ------------------------------------------------------------ reading aloud
  // The background page fetches and plays the audio (see readAloud there);
  // disconnecting the port stops it.
  function speaker(button, showError) {
    let port = null;
    const setIdle = () => {
      button.innerHTML = `${ICONS.speak}<span>خواندن</span>`;
      port = null;
    };
    return (text) => {
      if (port) { port.disconnect(); setIdle(); return; }
      showError("");
      button.innerHTML = `${ICONS.stop}<span>توقف</span>`;
      const mine = (port = api.runtime.connect({ name: "hz-speak" }));
      mine.onMessage.addListener((ev) => {
        if (port !== mine) return;
        if (ev.type === "error") showError(ev.message);
        mine.disconnect();
        setIdle();
      });
      mine.postMessage({ type: "speak", text });
    };
  }

  // ------------------------------------------------------------ e-mail panel
  let panel = null;

  function translate(ui, text, { withSubject }) {
    const port = api.runtime.connect({ name: "hz-translate" });
    let raw = "";
    let frame = 0;
    ui.$(".status").hidden = false;
    ui.$(".status-text").textContent = "در حال ترجمه…";
    ui.$(".error").textContent = "";
    for (const b of ["speak", "copy", "retry"]) ui.$(`[data-act=${b}]`).hidden = true;

    port.onMessage.addListener((ev) => {
      if (ev.type === "meta") {
        ui.$(".meta").textContent = ev.source === "fa" ? "· متن فارسی است" : `· از ${ev.source_fa} · آفلاین`;
      } else if (ev.type === "subject") {
        ui.$(".subject").textContent = ev.text;
        ui.$(".subject").dir = "auto";
      } else if (ev.type === "delta") {
        raw += ev.text;
        if (!frame) frame = requestAnimationFrame(() => { frame = 0; renderBody(ui.$(".body"), raw); });
      } else if (ev.type === "piece" && ev.of > 1) {
        ui.$(".status-text").textContent = `در حال ترجمه… (${ev.done} از ${ev.of})`;
      } else if (ev.type === "done") {
        cancelAnimationFrame(frame);
        ui.result = ev.text;
        renderBody(ui.$(".body"), ev.text);
        ui.$(".status").hidden = true;
        ui.$("[data-act=speak]").hidden = false;
        ui.$("[data-act=copy]").hidden = false;
        port.disconnect();
      } else if (ev.type === "error") {
        ui.$(".status").hidden = true;
        ui.$(".error").textContent = ev.message;
        ui.$("[data-act=retry]").hidden = false;
        port.disconnect();
      }
    });
    port.postMessage({ type: "translate", text, withSubject });
    return port;
  }

  function wireButtons(ui, getText, onClose, onRetry) {
    const speak = speaker(ui.$("[data-act=speak]"), (m) => (ui.$(".error").textContent = m));
    ui.root.addEventListener("click", async (e) => {
      const act = e.target.closest("button")?.dataset.act;
      if (act === "copy") {
        const subject = ui.$(".subject").textContent;
        await navigator.clipboard.writeText((subject ? subject + "\n\n" : "") + (ui.result || ""));
        const label = ui.$("[data-act=copy] span");
        label.textContent = "کپی شد";
        setTimeout(() => (label.textContent = "کپی"), 1500);
      } else if (act === "speak") {
        const subject = ui.$(".subject").textContent;
        speak((subject ? subject + ".\n" : "") + (ui.result || getText()));
      } else if (act === "retry") onRetry();
      else if (act === "close") onClose();
    });
  }

  async function openPanel() {
    const s = await api.runtime.sendMessage({ type: "settings" });
    const text = extractText(s.skipQuotes);
    const host = document.createElement("div");
    host.id = "hamzaban-host";
    document.body.prepend(host);
    const ui = makeCard(host);
    panel = { host, ui, port: null };
    const start = () => {
      if (!text) {
        ui.$(".status").hidden = true;
        ui.$(".error").textContent = "متنی برای ترجمه در این ایمیل پیدا نشد.";
        return;
      }
      panel.port = translate(ui, text, { withSubject: true });
    };
    wireButtons(ui, () => ui.$(".body").textContent, () => (host.style.display = "none"), start);
    start();
  }

  function toggle() {
    if (!panel) return openPanel();
    const hidden = panel.host.style.display === "none";
    panel.host.style.display = hidden ? "" : "none";
  }

  // ------------------------------------------------------------ selection card
  function translateSelection(text) {
    document.getElementById("hamzaban-sel")?.remove();
    const host = document.createElement("div");
    host.id = "hamzaban-sel";
    document.body.appendChild(host);
    const ui = makeCard(host);
    ui.$(".title").textContent = "ترجمه‌ی متن انتخاب‌شده";
    const run = () => translate(ui, text, { withSubject: false });
    wireButtons(ui, () => ui.$(".body").textContent, () => host.remove(), run);
    run();
  }

  api.runtime.onMessage.addListener((msg) => {
    if (msg.cmd === "toggle") toggle();
    else if (msg.cmd === "selection") translateSelection(msg.text);
    return undefined;
  });

  // ------------------------------------------------------------ auto mode
  api.runtime.sendMessage({ type: "settings" }).then((s) => {
    if (!s.autoTranslate || panel) return;
    const text = extractText(s.skipQuotes);
    if (text && !looksPersian(text)) openPanel();
  }).catch(() => {});
})();
