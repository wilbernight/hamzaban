// Hamzaban dubbing — content script.
// Finds the main <video>, draws a small control pill + Persian subtitles over
// it, and plays the Persian audio the local engine prepares, locked to the
// video clock: every sentence starts when the original sentence starts, or
// right after the previous one when the Persian ran longer ("elastic sync":
// nothing is dropped, the video slows a little until the dub catches up).
// If the dub is not ready yet the video waits instead of drifting.
// Live mode: when the engine cannot fetch the video by its link (a player in a
// frame, a blob stream), the page sends the sound of the playing <video>
// itself; the dub then follows a few seconds behind the picture.
(() => {
  if (window.__hzDub) return;
  window.__hzDub = true;

  const send = (msg) => new Promise((resolve) => {
    try {
      chrome.runtime.sendMessage(msg, (res) => {
        const err = chrome.runtime.lastError;
        resolve(err ? { error: err.message } : res || {});
      });
    } catch (_) {
      resolve({ error: "افزونه به‌روز شده؛ صفحه را یک‌بار رفرش کنید." });
    }
  });

  const faDigits = (s) => String(s).replace(/\d/g, (d) => "۰۱۲۳۴۵۶۷۸۹"[d]);
  // how long a subtitle line must stay up to be read: ~14 letters a second, 2–7 s
  const readMs = (text) => Math.max(2000, Math.min(7000, 800 + text.length * 70));
  const toBase64 = (buffer) => {
    const bytes = new Uint8Array(buffer);
    let out = "";
    for (let i = 0; i < bytes.length; i += 0x8000) out += String.fromCharCode.apply(null, bytes.subarray(i, i + 0x8000));
    return btoa(out);
  };
  const span = (s) => Math.max(s.dur, s.end - s.start); // video time a sentence's dub may take
  // elastic sync: video speed (× the user's own) by how late the dub is
  const SLOW = 0.92;       // lag > 0.4 s
  const SLOWER = 0.85;     // lag > 3 s
  const MAX_BOOST = 1.05;  // a late clip plays at most this much faster (pitch shifts a little)
  const LIVE_STALE = 30;   // live: a sentence this far behind the picture is not spoken any more
  const CAP_RATE = 16000;  // live: sound sent to the engine, mono 16 kHz
  const isAd = (v) => Boolean(v.closest(".html5-video-player")?.classList.contains("ad-showing"));
  const mediaUrl = (v) => (/^https?:/.test(v.currentSrc || "") ? v.currentSrc : undefined);
  // "۱ دقیقه و ۵ ثانیه" / "۴۰ ثانیه"
  const faDuration = (secs) => {
    const m = Math.floor(secs / 60), r = secs % 60;
    if (!m) return `${faDigits(r)} ثانیه`;
    return r ? `${faDigits(m)} دقیقه و ${faDigits(r)} ثانیه` : `${faDigits(m)} دقیقه`;
  };

  // How long a dub took from the click until the video could play, remembered per
  // kind of dub (from subtitles or from the audio, which voice), so the wait can be
  // counted down from the first second. Until there is a history: a rough guess.
  const startKind = (captions, speak, voice) => `${captions ? "captions" : "audio"}/${speak ? voice : "subs"}`;
  const startGuess = (captions, speak) => (captions ? 8 : 45) + (speak ? (captions ? 15 : 30) : 0);
  const START_HISTORY = 5;
  function learnedStart(kind) {
    return new Promise((resolve) => chrome.storage.local.get("startTimes", (r) => {
      const xs = r?.startTimes?.[kind] || [];
      resolve(xs.length ? xs.reduce((a, b) => a + b, 0) / xs.length : null);
    }));
  }
  function rememberStart(kind, secs) {
    chrome.storage.local.get("startTimes", (r) => {
      const all = r?.startTimes || {};
      all[kind] = [...(all[kind] || []), Math.round(secs * 10) / 10].slice(-START_HISTORY);
      chrome.storage.local.set({ startTimes: all });
    });
  }

  let fontLoading = null;
  function loadFont() {
    fontLoading ??= (async () => {
      try {
        const buf = await (await fetch(chrome.runtime.getURL("fonts/Vazirmatn.woff2"))).arrayBuffer();
        const face = new FontFace("HZ Vazirmatn", buf, { weight: "100 900" });
        await face.load();
        document.fonts.add(face);
      } catch (_) { /* Tahoma fallback */ }
    })();
    return fontLoading;
  }

  // ================================================================ page subtitles
  // Where the audio cannot be fetched from outside (Udemy: login + DRM), the
  // lecture's own subtitles carry everything the dub needs: text and timing.
  const sleep = (ms) => new Promise((ok) => setTimeout(ok, ms));

  function vttSeconds(stamp) {
    const p = stamp.trim().replace(",", ".").split(":").map(Number);
    return p.length === 3 ? p[0] * 3600 + p[1] * 60 + p[2] : p[0] * 60 + p[1];
  }

  function parseVTT(text) {
    const cues = [];
    for (const block of text.replace(/\r/g, "").split(/\n{2,}/)) {
      const lines = block.split("\n");
      const i = lines.findIndex((l) => l.includes("-->"));
      if (i < 0) continue;
      const [a, b] = lines[i].split("-->").map((s) => s.trim().split(/\s+/)[0]);
      const t = lines.slice(i + 1).join(" ").replace(/<[^>]+>/g, "").replace(/&nbsp;/g, " ").trim();
      if (t) cues.push({ start: vttSeconds(a), end: vttSeconds(b), text: t });
    }
    return cues;
  }

  async function udemyCaptions() {
    const m = location.pathname.match(/\/course\/([^/]+)\/learn\/lecture\/(\d+)/);
    if (!m) return null;
    const api = async (path) => {
      const r = await fetch(path, { credentials: "include", headers: { accept: "application/json" } });
      if (!r.ok) throw new Error(`Udemy API ${r.status}`);
      return r.json();
    };
    const course = await api(`/api-2.0/courses/${m[1]}/?fields[course]=id,title`);
    const lecture = await api(`/api-2.0/users/me/subscribed-courses/${course.id}/lectures/${m[2]}/` +
      "?fields[lecture]=title,asset&fields[asset]=captions");
    const caps = lecture.asset?.captions || [];
    if (!caps.length) return null;
    // English first, human-made before auto-generated
    const rank = (c) => (/^en/i.test(c.locale_id || "") ? 0 : 2) + (c.source === "manual" ? 0 : 1);
    const cap = [...caps].sort((x, y) => rank(x) - rank(y))[0];
    const r = await send({ type: "fetch-captions", url: cap.url });
    if (r.error) throw new Error(r.error);
    return { cues: parseVTT(r.text), lang: (cap.locale_id || "").slice(0, 2).toLowerCase() || null,
             title: lecture.title || course.title };
  }

  async function trackCaptions(v) {
    const tracks = [...v.textTracks].filter((t) => t.kind === "subtitles" || t.kind === "captions");
    const track = tracks.find((t) => /^en/i.test(t.language)) || tracks[0];
    if (!track) return null;
    const before = track.mode;
    if (before === "disabled") track.mode = "hidden"; // makes the browser load the cues
    for (let i = 0; i < 30 && !track.cues?.length; i++) await sleep(100);
    const cues = [...(track.cues || [])].map((c) => ({
      start: c.startTime, end: c.endTime, text: (c.text || "").replace(/<[^>]+>/g, "").trim(),
    })).filter((c) => c.text);
    if (before === "disabled") track.mode = "disabled";
    return cues.length ? { cues, lang: (track.language || "").slice(0, 2).toLowerCase() || null, title: document.title } : null;
  }

  async function pageCaptions(v) {
    if (/(^|\.)udemy\.com$/.test(location.hostname)) return udemyCaptions();
    return trackCaptions(v);
  }

  function pickVideo() {
    let best = null;
    let bestArea = 0;
    for (const v of document.querySelectorAll("video")) {
      const r = v.getBoundingClientRect();
      const area = r.width * r.height;
      if (r.width >= 200 && r.height >= 110 && area > bestArea) {
        best = v;
        bestArea = area;
      }
    }
    return best;
  }

  // ================================================================ overlay
  const CSS = `
    :host { all: initial; }
    [hidden] { display: none !important; }
    .pill {
      position: absolute; top: 12px; right: 12px; direction: rtl;
      display: flex; align-items: center; gap: 4px; padding: 4px;
      font: 500 13px/1.2 "HZ Vazirmatn", Vazirmatn, Tahoma, sans-serif; color: #fff;
      background: rgba(10, 18, 17, .8); border: 1px solid rgba(255,255,255,.14);
      -webkit-backdrop-filter: blur(10px); backdrop-filter: blur(10px);
      border-radius: 999px; pointer-events: auto; opacity: 0; transition: opacity .25s;
      box-shadow: 0 6px 20px rgba(0,0,0,.25);
    }
    .pill:not(.show) { pointer-events: none; } /* invisible = click-through */
    .pill.show { opacity: 1; }
    .pill.dim { opacity: .5; }
    .pill.dim:hover { opacity: 1; }
    button { all: unset; cursor: pointer; box-sizing: border-box; }
    button:focus-visible { outline: 2px solid #5eead4; outline-offset: 1px; }
    .go { display: flex; align-items: center; gap: 8px; padding: 6px 12px; border-radius: 999px; white-space: nowrap; }
    .go:hover { background: rgba(255,255,255,.1); }
    .dot { width: 8px; height: 8px; border-radius: 50%; background: #2dd4bf; flex: none; }
    .pill[data-kind=wait] .dot, .pill[data-kind=click] .dot { background: #fbbf24; animation: pulse 1s ease-in-out infinite; }
    .pill[data-kind=err] .dot { background: #f87171; }
    .pill[data-kind=err] .go { white-space: normal; max-width: 360px; line-height: 1.6; }
    @keyframes pulse { 50% { opacity: .35; } }
    .buf { color: rgba(255,255,255,.66); font-size: 12px; white-space: nowrap; padding-left: 4px; }
    .ctrls { display: flex; align-items: center; gap: 2px; border-right: 1px solid rgba(255,255,255,.16); padding-right: 6px; margin-right: 2px; }
    .ctl { height: 28px; min-width: 28px; padding: 0 8px; border-radius: 999px; display: grid; place-items: center; font-size: 12px; }
    .ctl:hover { background: rgba(255,255,255,.12); }
    .ctl.off { opacity: .45; text-decoration: line-through; }
    .subctl { display: flex; gap: 0; }
    .subctl .ctl { min-width: 24px; padding: 0 5px; }
    .vol { display: flex; align-items: center; gap: 6px; font-size: 12px; color: rgba(255,255,255,.75); padding: 0 6px; white-space: nowrap; }
    .vol input { width: 72px; accent-color: #2dd4bf; }
    .subs { position: absolute; left: 50%; bottom: var(--lift, 12%); transform: translateX(-50%);
      width: max-content; max-width: 86%; text-align: center; pointer-events: none; direction: rtl; }
    .fix { position: absolute; top: 56px; right: 12px; width: min(360px, calc(100% - 24px)); direction: rtl;
      max-height: calc(100% - 70px); overflow: auto; pointer-events: auto; padding: 10px 12px 12px;
      font: 500 13px/1.6 "HZ Vazirmatn", Vazirmatn, Tahoma, sans-serif; color: #fff;
      background: rgba(10, 18, 17, .94); border: 1px solid rgba(255,255,255,.16); border-radius: 14px;
      box-shadow: 0 10px 30px rgba(0,0,0,.4); }
    .fix .head { display: flex; justify-content: space-between; align-items: center; font-weight: 700; margin-bottom: 4px; }
    .fix .lbl { color: rgba(255,255,255,.6); font-size: 11.5px; margin: 6px 0 2px; }
    .chips { display: flex; flex-wrap: wrap; gap: 4px; }
    .chips[dir=ltr] { direction: ltr; }
    .chip { padding: 1px 7px; border-radius: 7px; background: rgba(255,255,255,.1); font-size: 12.5px; }
    .chip:hover { background: rgba(45,212,191,.3); }
    .chip.on { background: #2dd4bf; color: #062522; }
    .tabs { display: flex; gap: 4px; margin: 10px 0 6px; }
    .tabs .ctl { background: rgba(255,255,255,.08); height: 26px; }
    .tabs .ctl.on { background: #2dd4bf; color: #062522; }
    .fix input { all: unset; box-sizing: border-box; width: 100%; padding: 5px 9px; margin-top: 4px; border-radius: 8px;
      background: rgba(255,255,255,.1); border: 1px solid rgba(255,255,255,.18); font-size: 13px; }
    .fix input:focus { border-color: #2dd4bf; }
    .fix .row { display: flex; gap: 6px; margin-top: 8px; }
    .fix .act { flex: 1; text-align: center; padding: 6px; border-radius: 8px; background: rgba(255,255,255,.12); }
    .fix .act.main { background: #2dd4bf; color: #062522; font-weight: 700; }
    .fix .msg { margin-top: 6px; font-size: 12px; color: #5eead4; min-height: 1em; }
    .fix .msg.err { color: #f87171; }
    .subs .line + .line { margin-top: .3em; }
    .subs .prev { opacity: .7; font-size: .86em; }   /* the previous sentence, moved up to finish reading */
    .subs span { background: rgba(0,0,0,.76); color: #fff; line-height: 1.9; padding: .12em .55em;
      border-radius: 6px; font-family: "HZ Vazirmatn", Vazirmatn, Tahoma, sans-serif; font-weight: 500;
      -webkit-box-decoration-break: clone; box-decoration-break: clone; }
  `;

  class Overlay {
    constructor(handlers) {
      this.handlers = handlers;
      this.host = document.createElement("div");
      this.host.id = "hamzaban-dub";
      Object.assign(this.host.style, {
        position: "fixed", left: "0px", top: "0px", width: "0px", height: "0px",
        zIndex: "2147483647", pointerEvents: "none", display: "none",
      });
      const root = this.host.attachShadow({ mode: "open" });
      root.innerHTML = `<style>${CSS}</style>
        <div class="pill" data-kind="idle">
          <button class="go" data-act="main" title="دوبله‌ی فارسی این ویدیو با هوش مصنوعی آفلاین">
            <span class="dot"></span><span class="label">دوبله‌ی فارسی</span>
          </button>
          <span class="buf" hidden></span>
          <span class="ctrls" hidden>
            <button class="ctl" data-act="sync" title="تصویر چند ثانیه دیرتر تا دوبله و زیرنویس دقیقاً سر جایشان باشند" hidden>هم‌زمان</button>
            <button class="ctl" data-act="subs" title="نمایش زیرنویس فارسی">زیرنویس</button>
            <span class="subctl">
              <button class="ctl" data-act="smaller" title="زیرنویس کوچک‌تر">A−</button>
              <button class="ctl" data-act="bigger" title="زیرنویس بزرگ‌تر">A+</button>
              <button class="ctl" data-act="lower" title="زیرنویس پایین‌تر">⇣</button>
              <button class="ctl" data-act="higher" title="زیرنویس بالاتر">⇡</button>
            </span>
            <label class="vol" title="بلندی صدای اصلی ویدیو زیر دوبله"><span>صدای اصلی</span>
              <input type="range" min="0" max="60" step="5"></label>
            <button class="ctl" data-act="fix" title="درست کردن تلفظ یا ترجمه‌ی همین جمله">✎</button>
            <button class="ctl" data-act="stop" title="قطع دوبله" aria-label="قطع دوبله">✕</button>
          </span>
        </div>
        <div class="fix" hidden>
          <div class="head"><span>درست کردن همین جمله</span><button class="ctl" data-act="fixclose" title="بستن">✕</button></div>
          <div class="lbl">کلمه‌ای را که اشتباه گفته شد بزنید:</div>
          <div class="chips" data-chips="fa"></div>
          <div class="lbl en-l">متن اصلی:</div>
          <div class="chips" dir="ltr" data-chips="en"></div>
          <div class="tabs">
            <button class="ctl on" data-act="fixmode" data-mode="pron">تلفظ درست</button>
            <button class="ctl" data-act="fixmode" data-mode="idiom">ترجمه‌ی درست</button>
          </div>
          <div data-pane="pron">
            <input data-f="word" placeholder="کلمه (فارسی یا انگلیسی)">
            <div class="lbl now" hidden></div>
            <input data-f="say" placeholder="چطور گفته شود؟ مثلاً کُد، کوبرنتیز">
            <div class="lbl sug-l" hidden>پیشنهاد (بزنید و با 🔊 گوش کنید):</div>
            <div class="chips" data-chips="sug"></div>
            <div class="row"><button class="act" data-act="fixplay">🔊 امتحان</button>
              <button class="act main" data-act="fixsave">ذخیره</button></div>
          </div>
          <div data-pane="idiom" hidden>
            <input data-f="en" dir="ltr" placeholder="عبارت انگلیسی (از متن اصلی بزنید)">
            <input data-f="fa" placeholder="ترجمه‌ی درست فارسی">
            <div class="row"><button class="act main" data-act="fixsave">ذخیره</button></div>
          </div>
          <div class="msg"></div>
        </div>
        <div class="subs" hidden><div class="line prev" hidden><span></span></div><div class="line cur"><span></span></div></div>`;
      this.$ = (s) => root.querySelector(s);
      this.$$ = (s) => root.querySelectorAll(s);
      this.pill = this.$(".pill");
      this.mouse = { x: -1, y: -1, t: 0 };
      root.addEventListener("click", (e) => {
        const btn = e.target.closest("button");
        const act = btn?.dataset.act;
        if (act === "fixmode") return this.fixMode(btn.dataset.mode);
        if (act === "chip") return this.pickChip(btn);
        if (act === "fixplay") return handlers.fixplay?.(this.$("[data-f=say]").value || this.$("[data-f=word]").value);
        if (act === "fixsave") return handlers.fixsave?.(this.fixValues());
        if (act) handlers[act]?.();
      });
      // typing in the panel must not reach the site's player (space = pause, f = fullscreen…)
      root.addEventListener("keydown", (e) => { if (e.target.matches?.("input")) e.stopPropagation(); }, true);
      this.$(".vol input").addEventListener("input", (e) => handlers.volume?.(Number(e.target.value) / 100));
      document.addEventListener("mousemove", (e) => {
        this.mouse = { x: e.clientX, y: e.clientY, t: Date.now() };
      }, { passive: true, capture: true });
      document.addEventListener("fullscreenchange", () => this.reparent());
      this.reparent();
    }

    reparent() {
      const fs = document.fullscreenElement;
      const target = fs && fs.tagName !== "VIDEO" && this.video && fs.contains(this.video)
        ? fs : document.body || document.documentElement;
      if (this.host.parentNode !== target) target.appendChild(this.host);
    }

    place(v, active, dimmable) {
      this.video = v;
      const r = v.getBoundingClientRect();
      if (r.width < 50 || r.height < 30) { this.host.style.display = "none"; return; }
      Object.assign(this.host.style, {
        display: "block", left: `${r.left}px`, top: `${r.top}px`, width: `${r.width}px`, height: `${r.height}px`,
      });
      const scale = this.look?.scale ?? 1;
      this.$(".subs").style.fontSize = `${Math.round(Math.min(32, Math.max(15, r.width * 0.027)) * scale)}px`;
      this.$(".subs").style.setProperty("--lift", `${this.look?.lift ?? 12}%`);
      const m = this.mouse;
      const over = m.x >= r.left && m.x <= r.right && m.y >= r.top && m.y <= r.bottom;
      const recent = Date.now() - m.t < 2500;
      this.pill.classList.toggle("show", active || (over && recent));
      this.pill.classList.toggle("dim", active && dimmable && !(over && recent));
      this.renderSubs();
    }

    setActive(on, s, mode) {
      this.$(".ctrls").hidden = !on;
      this.$(".buf").hidden = !on;
      if (!on) {
        this.closeFix();
        this.$("[data-act=sync]").hidden = true;
        this.setState("idle", "دوبله‌ی فارسی");
        this.clearSubs();
      } else {
        this.$(".vol input").value = Math.round((s.duck ?? 0.15) * 100);
        this.$(".vol").hidden = mode === "subs";
        this.$("[data-act=subs]").hidden = mode === "subs";
        this.$("[data-act=subs]").classList.toggle("off", mode === "dub");
        this.$(".subctl").hidden = mode === "dub";
      }
    }

    setState(kind, text) {
      this.pill.dataset.kind = kind;
      this.$(".label").textContent = text;
    }

    setBuffer(text) { this.$(".buf").textContent = text || ""; }

    setSubsButton(on) { this.$("[data-act=subs]").classList.toggle("off", !on); }

    setSync(on) { // live only: the time-shift switch
      const b = this.$("[data-act=sync]");
      b.hidden = false;
      b.classList.toggle("off", !on);
    }

    // ✎ panel: fix how a word is said, or how a phrase is translated, from the sentence just heard
    openFix(seg) {
      const fill = (box, text, ltr) => {
        box.replaceChildren(...String(text || "").split(/\s+/).filter(Boolean).map((w) => {
          const b = document.createElement("button");
          b.className = "chip";
          b.dataset.act = "chip";
          b.dataset.lang = ltr ? "en" : "fa";
          b.textContent = w;
          return b;
        }));
      };
      fill(this.$("[data-chips=fa]"), seg?.fa, false);
      fill(this.$("[data-chips=en]"), seg?.src, true);
      this.$(".en-l").hidden = !seg?.src;
      for (const f of ["word", "say", "en", "fa"]) this.$(`[data-f=${f}]`).value = "";
      this.showSpoken("", "");
      this.fixMsg(seg ? "" : "هنوز جمله‌ای پخش نشده؛ کلمه را خودتان بنویسید.");
      this.fixMode("pron");
      this.$(".fix").hidden = false;
    }

    closeFix() { this.$(".fix").hidden = true; }

    fixMode(mode) {
      this.mode = mode;
      for (const b of this.$$("[data-act=fixmode]")) b.classList.toggle("on", b.dataset.mode === mode);
      this.$("[data-pane=pron]").hidden = mode !== "pron";
      this.$("[data-pane=idiom]").hidden = mode !== "idiom";
    }

    pickChip(chip) {
      const word = chip.textContent.replace(/^[^\p{L}\p{N}]+|[^\p{L}\p{N}\u200c]+$/gu, "");
      if (this.mode === "idiom" && chip.dataset.lang === "en") {
        chip.classList.toggle("on"); // several words of the original form the phrase
        this.$("[data-f=en]").value = [...this.$$("[data-chips=en] .chip.on")].map((c) => c.textContent).join(" ")
          .replace(/[.,!?;:]+$/, "");
        return;
      }
      if (chip.dataset.lang === "sug") { // a suggested spelling
        this.$("[data-f=say]").value = chip.textContent;
        return this.handlers.fixplay?.(chip.textContent);
      }
      this.fixMode("pron");
      const stem = word.replace(/\u200c(?:هایی|های|ها|ای|ام|ات|اش|ی)$/, "");
      this.$("[data-f=word]").value = stem;
      this.$("[data-f=say]").value = "";
      this.$("[data-f=say]").focus();
      this.handlers.spoken?.(stem); // shows what the voice gets now, and spellings to try
    }

    // what the voice gets for the word now, and light respellings of it to try
    showSpoken(word, now, phonemes) {
      const nowBox = this.$(".now");
      nowBox.hidden = !now;
      // a ParSiGo voice: the exact phonemes it says, and a fix in phonemes always works
      nowBox.textContent = phonemes
        ? `صدا دقیقاً این را می‌گوید: ${phonemes} — برای درست کردن، همین را با حروف لاتین درست بنویسید (A=آ، S=ش، C=چ، x=خ، ?=ء).`
        : now ? `الان صدا این را می‌گیرد: «${now}»` : "";
      if (phonemes) this.$("[data-f=say]").value = phonemes;
      const base = now || word;
      const out = [];
      const marks = ["\u064F", "\u064E", "\u0650"]; // ـُ ـَ ـِ
      // a compound («نیمکره»): split at the joint with a ZWNJ and mark the second part,
      // which the voice follows far better than a mark alone (نیم‌کُره: 4 of 4)
      const joint = base.match(/^(نیم|هم|بی|نا|پیش|پس|سر|دو|چند|خود|کم|پر|بد|خوش|ابر|فرو|فرا|در|بر)(?!\u200c)(.{2,})$/);
      if (joint) for (const m of marks) out.push(`${joint[1]}\u200c${joint[2][0]}${m}${joint[2].slice(1)}`);
      if (/^[\u0600-\u06FF]/.test(base)) {
        const [first, rest] = [base[0], base.slice(1)];
        for (const m of marks) out.push(first + m + rest); // a vowel after the first letter
        if (!/^[اآوی\u064B-\u0652]/.test(rest)) out.push(first + "و" + rest, first + "ا" + rest, first + "ی" + rest);
      }
      const box = this.$("[data-chips=sug]");
      box.replaceChildren(...[...new Set(out)].filter((x) => x !== word).slice(0, 8).map((w) => {
        const b = document.createElement("button");
        b.className = "chip";
        b.dataset.act = "chip";
        b.dataset.lang = "sug";
        b.textContent = w;
        return b;
      }));
      this.$(".sug-l").hidden = !box.children.length;
    }

    fixValues() {
      const v = (f) => this.$(`[data-f=${f}]`).value.trim();
      return this.mode === "idiom" ? { kind: "idioms", parts: [v("en"), "", v("fa")] }
        : { kind: "pronounce", parts: [v("word"), v("say")] };
    }

    fixMsg(text, err = false) {
      const m = this.$(".fix .msg");
      m.textContent = text;
      m.classList.toggle("err", err);
    }

    // Subtitles: a line stays up at least as long as it takes to read (fast speech
    // would otherwise wipe it); when the next one comes, it moves up above it.
    showSub(text) {
      const now = performance.now();
      const cur = this.subCur;
      if (text) {
        if (cur && cur.text === text) { cur.ended = false; return; }
        if (cur) this.subPrev = { text: cur.text, until: Math.max(cur.minUntil, now + 1500) };
        this.subCur = { text, minUntil: now + readMs(text), ended: false };
      } else if (cur) {
        cur.ended = true; // the sentence is over; the line goes once it has been read
      }
      this.renderSubs();
    }

    clearSubs() { // a seek or stop: nothing on screen belongs any more
      this.subCur = this.subPrev = null;
      this.renderSubs();
    }

    renderSubs() {
      const now = performance.now();
      if (this.subPrev && now >= this.subPrev.until) this.subPrev = null;
      // read long enough, and never before the older line above it (that would leave
      // the older one alone on screen)
      if (this.subCur?.ended && now >= this.subCur.minUntil && !this.subPrev) this.subCur = null;
      const prev = this.subPrev?.text || "";
      const cur = this.subCur?.text || "";
      this.$(".subs").hidden = !prev && !cur;
      this.$(".prev").hidden = !prev;
      this.$(".prev span").textContent = prev;
      this.$(".cur").hidden = !cur;
      this.$(".cur span").textContent = cur;
      this.nativeSubs(prev, cur);
    }

    // The bare <video> in browser fullscreen hides every other element, so the
    // subtitle goes to the video's own text track, which the browser draws there.
    nativeSubs(prev, cur) {
      const v = this.video;
      const fs = v && (document.fullscreenElement === v || v.webkitDisplayingFullscreen);
      const text = fs ? [prev, cur].filter(Boolean).join("\n") : "";
      if (!v || (text === (this.nativeText || "") && !(fs && !this.track))) return;
      this.nativeText = text;
      if (!this.track && !fs) return;
      this.track ??= v.addTextTrack("subtitles", "هم‌زبان", "fa");
      this.track.mode = fs ? "showing" : "hidden";
      for (const c of [...(this.track.cues || [])]) this.track.removeCue(c);
      if (text) {
        const cue = new VTTCue(Math.max(0, v.currentTime - 1), v.currentTime + 3600, text);
        cue.line = -3;
        this.track.addCue(cue);
      }
    }
  }


  // ================================================================ time shift
  // Live mode can only hear a sentence once it plays, so its subtitle and dub
  // would come late. Instead the viewer is shown the video `delay` seconds
  // late: the picture is drawn from a short buffer of frames on a canvas over
  // the <video>, the sound goes through a delay line, and the engine hears the
  // undelayed sound, so everything is ready by the time the viewer reaches it.
  // Nothing is downloaded: it is the same picture and sound, played later.
  const shiftNodes = new WeakMap(); // <video> -> {ctx, src}: an element's sound can be taken only once

  function resampleTo16k(data, rate) {
    if (rate === CAP_RATE) return new Float32Array(data);
    const n = Math.floor((data.length * CAP_RATE) / rate);
    const out = new Float32Array(n);
    const step = rate / CAP_RATE;
    for (let i = 0; i < n; i++) {
      const x = i * step;
      const j = Math.floor(x);
      const f = x - j;
      out[i] = data[j] * (1 - f) + (data[Math.min(j + 1, data.length - 1)] || 0) * f;
    }
    return out;
  }

  // A node that hands the sound reaching it to onBlock (Float32 blocks at ctx's rate):
  // an AudioWorklet, or the old ScriptProcessor where a page's rules block worklets.
  async function soundTap(ctx, onBlock) {
    try {
      await ctx.audioWorklet.addModule(chrome.runtime.getURL("capture-worklet.js"));
      const node = new AudioWorkletNode(ctx, "hz-capture", { numberOfInputs: 1, numberOfOutputs: 1,
        channelCount: 1, channelCountMode: "explicit", channelInterpretation: "speakers" });
      node.port.onmessage = (e) => onBlock(e.data);
      return node;
    } catch (_) {
      const node = ctx.createScriptProcessor(4096, 1, 1);
      node.onaudioprocess = (e) => onBlock(e.inputBuffer.getChannelData(0));
      return node;
    }
  }

  class TimeShift {
    constructor(video, delay, width, onSound) {
      this.v = video;
      this.delay = delay;
      this.width = width;
      this.onSound = onSound; // 16 kHz blocks of the undelayed sound
      this.frames = [];       // [{at: clock seconds, mediaTime, bmp}]
      this.clock = 0;         // seconds the video has been playing (stops while paused)
      this.lastWall = null;
      this.lastGrab = -1;
      this.mediaTime = null;  // video time of the frame on screen
      this.ready = false;     // the buffer is full: what is shown is `delay` seconds late
    }

    // A video from another site that did not allow it cannot be drawn or heard
    // through the page; check before taking its sound, or it would go silent.
    static async possible(v) {
      try {
        if (!v.videoWidth) return false;
        const bmp = await createImageBitmap(v, { resizeWidth: 16, resizeHeight: 9 });
        bmp.close();
        return true;
      } catch (_) {
        return false;
      }
    }

    start() {
      const v = this.v;
      let nodes = shiftNodes.get(v);
      if (!nodes) {
        const ctx = new AudioContext();
        nodes = { ctx, src: ctx.createMediaElementSource(v) };
        shiftNodes.set(v, nodes);
      }
      this.ctx = nodes.ctx;
      this.src = nodes.src;
      this.src.disconnect();
      this.makeDelay();
      soundTap(this.ctx, (block) => {
        if (this.on && !this.v.paused) this.onSound(resampleTo16k(block, this.ctx.sampleRate));
      }).then((node) => {
        if (!this.on) return;
        this.proc = node;
        this.src.connect(node);
        node.connect(this.ctx.destination); // outputs silence; keeps it running
      });
      this.canvas = document.createElement("canvas");
      Object.assign(this.canvas.style, { position: "absolute", pointerEvents: "none", objectFit: "contain",
                                         background: "#000", margin: "0", padding: "0", border: "0" });
      v.insertAdjacentElement("afterend", this.canvas);
      this.g = this.canvas.getContext("2d");
      this.on = true;
      this.onFrame = (_now, meta) => {
        if (!this.on) return;
        this.grab(meta.mediaTime);
        v.requestVideoFrameCallback(this.onFrame);
      };
      v.requestVideoFrameCallback(this.onFrame);
      this.events = { pause: () => this.ctx.suspend().catch(() => {}), play: () => this.ctx.resume().catch(() => {}) };
      for (const [ev, fn] of Object.entries(this.events)) v.addEventListener(ev, fn);
      if (!v.paused) this.ctx.resume().catch(() => {});
      const loop = () => { if (this.on) { this.draw(); requestAnimationFrame(loop); } };
      requestAnimationFrame(loop);
    }

    makeDelay() {
      try { this.dly?.disconnect(); } catch (_) {}
      this.dly = this.ctx.createDelay(Math.ceil(this.delay) + 1);
      this.dly.delayTime.value = this.delay;
      this.src.connect(this.dly);
      this.dly.connect(this.ctx.destination);
    }

    grab(mediaTime) {
      const v = this.v;
      if (!v.videoWidth || this.clock - this.lastGrab < 1 / 20) return; // ≤ 20 frames a second (memory)
      this.lastGrab = this.clock;
      const at = this.clock;
      const w = Math.min(this.width, v.videoWidth);
      const h = Math.round((w * v.videoHeight) / v.videoWidth);
      createImageBitmap(v, { resizeWidth: w, resizeHeight: h, resizeQuality: "low" })
        .then((bmp) => (this.on ? this.frames.push({ at, mediaTime, bmp }) : bmp.close()))
        .catch(() => {});
    }

    draw() {
      const v = this.v;
      const wall = performance.now() / 1000;
      if (!v.paused && !v.seeking && this.lastWall != null) this.clock += wall - this.lastWall;
      this.lastWall = wall;
      this.place();
      const target = this.clock - this.delay;
      let shown = null;
      while (this.frames.length && this.frames[0].at <= target) {
        if (shown) shown.bmp.close();
        shown = this.frames.shift();
      }
      if (!shown) return;
      if (this.canvas.width !== shown.bmp.width) this.canvas.width = shown.bmp.width;
      if (this.canvas.height !== shown.bmp.height) this.canvas.height = shown.bmp.height;
      this.g.drawImage(shown.bmp, 0, 0);
      shown.bmp.close();
      this.mediaTime = shown.mediaTime;
      this.ready = true;
    }

    // Cover exactly the video's box, from the canvas's own containing block (handles
    // transforms and wrappers); the site's controls stay above. Once a second, make
    // sure the canvas is not underneath the video (a player may give it a z-index).
    place() {
      const v = this.v;
      const c = this.canvas;
      const vr = v.getBoundingClientRect();
      const op = c.offsetParent;
      const pr = op ? op.getBoundingClientRect() : { left: -window.scrollX, top: -window.scrollY };
      const bx = op ? op.clientLeft - op.scrollLeft : 0;
      const by = op ? op.clientTop - op.scrollTop : 0;
      Object.assign(c.style, { left: `${vr.left - pr.left - bx}px`, top: `${vr.top - pr.top - by}px`,
                               width: `${vr.width}px`, height: `${vr.height}px` });
      const now = performance.now();
      if (now - (this.zCheck || 0) < 1000 || !vr.width) return;
      this.zCheck = now;
      const z = getComputedStyle(v).zIndex;
      if (c.style.zIndex !== z && z !== "auto") c.style.zIndex = z; // same layer, later in the page = on top
      c.style.pointerEvents = "auto"; // only to ask the page what is on top here
      const top = document.elementFromPoint(vr.left + vr.width / 2, vr.top + vr.height / 2);
      c.style.pointerEvents = "none";
      if (top === v) c.style.zIndex = String((parseInt(z, 10) || 0) + 1);
    }

    // The delayed picture cannot be drawn there: browser fullscreen of the bare
    // <video>, or picture-in-picture.
    blocked() {
      return document.fullscreenElement === this.v || document.pictureInPictureElement === this.v;
    }

    // after a seek: what was buffered belongs to the old place
    reset() {
      for (const f of this.frames) f.bmp.close();
      this.frames = [];
      this.ready = false;
      this.mediaTime = null;
      this.makeDelay();
    }

    stop() {
      this.on = false;
      for (const f of this.frames) f.bmp.close();
      this.frames = [];
      this.canvas?.remove();
      for (const [ev, fn] of Object.entries(this.events || {})) this.v.removeEventListener(ev, fn);
      try { this.proc?.disconnect(); this.dly?.disconnect(); this.src.disconnect(); } catch (_) {}
      this.src.connect(this.ctx.destination); // the video's own sound, undelayed again
      this.ctx.resume().catch(() => {});      // never closed: the element's sound lives in it now
    }
  }

  // ================================================================ dubbing
  class Dubber {
    constructor(video, overlay) {
      this.v = video;
      this.ui = overlay;
      this.active = false;
    }

    reset() {
      this.segs = new Map();
      this.buffers = new Map();
      this.loading = new Set();
      this.seq = 0;
      this.jobId = null;
      this.status = "starting";
      this.error = null;
      this.readyUntil = 0;
      this.eof = false;
      this.current = null;
      this.autoPaused = false;
      this.ourPause = false;
      this.firstStart = true;
      this.startedAt = null;   // when the dub was asked for (performance.now, seconds)
      this.startKind = null;   // see startKind()
      this.startEst = null;    // seconds this kind of dub usually takes to start
      this.rate = null;        // seconds of video the engine prepares per second (measured)
      this.rateSamples = [];
      this.leadCap = null;     // the engine prepares at most this far ahead (less when the Mac is hot)
      this.hot = false;
      this.url = location.href;
      this.origVolume = null;
      this.claimed = null;     // sentences starting up to here were reached by playback (owed in full)
      this.lastStart = null;   // start of the sentence the dub spoke last (null after a seek)
      this.userRate = this.v.playbackRate || 1; // the user's own speed; ours is userRate × slow
      this.slow = 1;           // 1, SLOW or SLOWER
      this.paceAt = 0;         // when the video speed last changed (performance.now)
      this.undone = 0;         // times the player undid our slowdown at once
      this.noSlow = false;     // the player fights our slowdown: never slow the video
      this.origPitch = null;
      this.mutedTicks = 0;
      this.live = false;       // the engine hears the video through us (no readable link)
      this.cap = null;         // live: the chunk of sound being collected
      this.liveDelay = null;   // live: how far the dub runs behind the picture
      this.liveSubs = [];      // live: translated sentences to show, in order (before their voice)
      this.tseq = 0;
      this.shift = null;       // live: the video shown a few seconds late, so the dub is on time
    }

    // The video time the viewer sees: the real one, or the delayed picture's (time shift).
    now() {
      return this.shift ? (this.shift.mediaTime ?? -1) : this.v.currentTime;
    }

    async start() {
      if (this.active) return;
      this.reset();
      this.active = true;
      this.startedAt = performance.now() / 1000;
      this.s = await send({ type: "settings" });
      this.speak = this.s.mode !== "subs";     // Persian voice
      this.showSubs = this.s.mode !== "dub";   // Persian subtitles
      this.ui.setActive(true, this.s, this.s.mode);
      this.ui.setState("wait", "در حال خواندن زیرنویس…");
      let captions = null;
      let captionError = "";
      try {
        captions = await pageCaptions(this.v);
      } catch (e) {
        captionError = e.message;
      }
      if (!this.active) return;
      const udemy = /(^|\.)udemy\.com$/.test(location.hostname);
      if (!captions && (udemy || this.v.mediaKeys)) {
        // no way to get the audio (DRM / login) and no subtitles to work from
        this.status = "error";
        this.ui.setState("err", udemy
          ? `زیرنویس این درس پیدا نشد${captionError ? ` (${captionError})` : ""}. دوبله‌ی Udemy از روی زیرنویس انجام می‌شود؛ درسی را امتحان کنید که دکمه‌ی CC دارد.`
          : "این ویدیو قفل‌شده (DRM) است و زیرنویسی هم ندارد؛ دوبله‌اش ممکن نیست.");
        this.listeners = {};
        this.tickTimer = setInterval(() => this.tick(), 100);
        return;
      }
      if (captions) captions.duration = Number.isFinite(this.v.duration) ? this.v.duration : null;
      this.captions = captions;
      this.startKind = startKind(Boolean(captions), this.speak, this.s.voice);
      this.startEst = (await learnedStart(this.startKind)) ?? startGuess(Boolean(captions), this.speak);
      if (!this.active) return;
      loadFont();
      if (this.speak) {
        this.ctx = new AudioContext();
        this.gain = this.ctx.createGain();
        this.gain.connect(this.ctx.destination);
        this.ctx.resume().catch(() => {});
      }
      this.ui.setActive(true, this.s, this.s.mode);
      this.ui.setState("wait", "در حال آماده‌سازی دوبله…");

      this.listeners = {
        pause: () => {
          if (this.ourPause) this.ourPause = false;
          else this.autoPaused = false; // the user paused: do not auto-resume later
          this.setPace(1);
          // at the end of the video the dub still finishes what it owes
          if (!this.v.ended) this.ctx?.suspend().catch(() => {});
        },
        play: () => this.ctx?.resume().catch(() => {}),
        seeking: () => this.onSeek(),
        ratechange: () => this.syncRate(),
      };
      for (const [ev, fn] of Object.entries(this.listeners)) this.v.addEventListener(ev, fn);
      this.tickTimer = setInterval(() => this.tick(), 100);

      const res = await send({ type: "dub-start", url: this.url, media: mediaUrl(this.v), t: this.v.currentTime, captions: this.captions });
      if (!this.active) return;
      if (res.error) return this.fail(res.error);
      this.jobId = res.id;
      this.ingest(res);
      this.pollTimer = setInterval(() => this.poll(), 700);
    }

    stop({ keepPaused = false } = {}) {
      if (!this.active) return;
      this.active = false;
      clearInterval(this.tickTimer);
      clearInterval(this.pollTimer);
      this.stopCapture();
      this.stopClip();
      this.setPace(1);
      for (const [ev, fn] of Object.entries(this.listeners || {})) this.v.removeEventListener(ev, fn);
      this.v.volume = this.origVolume ?? this.v.volume;
      if (this.autoPaused && !keepPaused) this.v.play().catch(() => {});
      this.ctx?.close().catch(() => {});
      if (this.jobId) send({ type: "dub-stop", id: this.jobId });
      this.ui.setActive(false);
    }

    fail(message) {
      this.status = "error";
      this.error = message;
      clearInterval(this.pollTimer);
      this.stopCapture();
      this.stopClip();
      this.setPace(1);
      this.v.volume = this.origVolume ?? this.v.volume;
      if (this.autoPaused) { this.autoPaused = false; this.v.play().catch(() => {}); }
      this.ui.setState("err", message);
      this.ui.setBuffer("");
    }

    // The original is turned down only once the dub is really running, so a
    // failed start never leaves the video quiet or paused.
    applyDuck() {
      if (this.origVolume == null) this.origVolume = this.v.volume;
      this.v.volume = Math.min(1, this.origVolume * (this.s.duck ?? 0.15));
    }

    setDuck(value) {
      this.s.duck = value;
      if (this.origVolume != null) this.applyDuck();
      chrome.storage.local.set({ duck: value });
    }

    toggleSync() { // live: the delayed (in-sync) picture on or off
      const on = !this.shift;
      chrome.storage.local.set({ liveSync: on });
      this.s.liveSync = on;
      if (on) { this.stopCapture(); this.startShift(); }
      else { this.stopCapture(); this.resync(this.v.currentTime); this.startCapture(); }
    }

    toggleSubs() { // dub + subtitles <-> dub only
      this.showSubs = !this.showSubs;
      this.ui.setSubsButton(this.showSubs);
      if (!this.showSubs) { this.ui.clearSubs(); this.liveSubs = []; this.liveSub = null; }
      else if (this.current && !this.live) this.ui.showSub(this.current.seg.fa);
      this.ownModeChange = true;
      chrome.storage.local.set({ mode: this.showSubs ? "both" : "dub" });
    }

    pauseVideo() {
      this.autoPaused = true;
      this.ourPause = true;
      this.setPace(1);
      this.v.pause();
    }

    // ---------------------------------------------------------- engine sync
    async poll() {
      if (this.polling || !this.jobId || this.status === "error") return;
      this.polling = true;
      const t = isAd(this.v) ? undefined : this.v.currentTime;
      const res = await send({ type: "dub-poll", id: this.jobId, t, since: this.seq,
                               tsince: this.live && !this.shift ? this.tseq : undefined });
      this.polling = false;
      if (!this.active) return;
      if (res.error && /فعال نیست/.test(res.error)) {
        // the engine restarted or dropped an idle job: open a new one here
        const again = await send({ type: "dub-start", url: this.url, media: mediaUrl(this.v), t: this.v.currentTime, captions: this.captions });
        if (!again.error && this.active) {
          this.jobId = again.id;
          this.seq = 0;
          this.segs.clear();
          this.buffers.clear();
          this.ingest(again); // the queue is rebuilt from lastStart (see ingest)
        }
        return;
      }
      if (res.error) {
        this.ui.setState("err", res.error);
        return;
      }
      this.ingest(res);
    }

    ingest(res) {
      if (res.live && !this.live) this.enterLive();
      this.status = res.status;
      this.readyUntil = res.ready_until;
      this.measureRate(res.ready_until);
      this.leadCap = res.lead_cap || null;
      this.hot = !!res.hot;
      this.eof = res.eof;
      this.seq = Math.max(this.seq, res.seq || 0);
      if (res.texts) {
        // live: the subtitle is shown as soon as it is translated, not with the voice
        const now = this.v.currentTime;
        for (const x of res.texts) if (this.showSubs && now - x.end < LIVE_STALE) this.liveSubs.push(x);
        this.tseq = Math.max(this.tseq, res.tseq || 0);
      }
      const t = this.now();
      const busy = Boolean(this.current) || [...this.segs.values()].some((s) => s.queued);
      for (const s of res.segments || []) {
        const old = this.segs.get(s.id);
        if (old) {
          // a saved fix changed it (new translation or pronunciation): not heard yet -> take the new one
          if ((s.rev || 0) > (old.rev || 0) && !old.played && this.current?.seg !== old) {
            Object.assign(old, { fa: s.fa, dur: s.dur, audio: s.audio, rev: s.rev });
            this.buffers.delete(s.id);
          }
          continue;
        }
        const seg = { ...s, played: false, queued: false };
        if (this.live && !this.shift) {
          // everything arrives after the picture passed it: say it now, in order
          seg.queued = Boolean(s.audio) && t - (s.start + span(s)) < LIVE_STALE;
          seg.played = !seg.queued;
        } else if (this.shift && s.start + span(s) < t) {
          // even the delayed picture is past it (very fast speech): late beats never
          seg.queued = Boolean(s.audio) && t - (s.start + span(s)) < LIVE_STALE;
          seg.played = !seg.queued;
          if (!this.speak && this.showSubs && s.fa) this.liveSubs.push(s);
        } else if (s.start <= (this.claimed ?? t)) {
          // it arrived after the video reached it (no autoWait, or a new engine job)
          if (this.lastStart != null && s.start <= this.lastStart + 0.3) seg.played = true; // the dub is past it
          else if (busy && s.audio) seg.queued = true;           // the dub is behind it: joins the queue
          else seg.played = s.start + span(s) < t - 0.5;         // too late to be heard
        }
        this.segs.set(s.id, seg);
      }
      if (res.status === "running" && this.origVolume == null && this.speak) this.applyDuck();
      if (res.status === "error") this.fail(res.error || "خطای ناشناخته");
      if (res.status === "stopped" && this.active) {
        // the engine runs one dub at a time: another tab or voice took over
        this.stop();
        this.ui.setState("idle", "دوبله متوقف شد (دوبله‌ی دیگری شروع شد)؛ برای ادامه اینجا بزنید");
      }
    }

    // ---------------------------------------------------------- playback
    // How fast the dub is being prepared, from how quickly ready_until moves.
    measureRate(readyUntil) {
      const now = performance.now() / 1000;
      const samples = this.rateSamples;
      samples.push([now, readyUntil]);
      while (samples.length > 2 && now - samples[0][0] > 15) samples.shift();
      const [t0, r0] = samples[0];
      if (now - t0 >= 4) this.rate = Math.max(0, (readyUntil - r0) / (now - t0));
    }

    // How much must be ready before playing so the dub then runs to the end
    // without stopping: little when the engine is faster than playback, more
    // when it is slower (capped below the engine's own look-ahead).
    requiredAhead() {
      const MIN = 12;
      const speed = this.userRate || 1; // our own slowdown is not the user's speed
      const remaining = Number.isFinite(this.v.duration) ? Math.max(0, this.v.duration - this.v.currentTime) : 600;
      let need = MIN;
      if (this.rate != null && this.rate < speed * 1.15) need = remaining * (1 - this.rate / speed) + MIN;
      // never wait for more than the engine will prepare, or the video would wait forever
      const cap = this.leadCap ? Math.max(MIN, this.leadCap - 3) : 200;
      return Math.min(remaining, Math.max(MIN, Math.min(need, cap)));
    }

    onSeek() {
      if (this.shift && !isAd(this.v)) this.shift.reset(); // the delayed picture refills from here
      const t = this.v.currentTime;
      // an ad's own timeline, or the player's tiny seeks (quality switch, return
      // from an ad), are not the user moving: keep what the dub still owes
      if (isAd(this.v) || (this.lastT != null && Math.abs(t - this.lastT) < 0.3)) return;
      this.rateSamples = [];   // ready_until jumps on a seek: measure afresh
      this.flushCapture();     // live: the next sound starts at the new time
      this.stopClip();
      this.setPace(1);
      this.resync(t);
      this.poll();
    }

    // Start over at video time t: the queue is dropped, sentences wholly before
    // t are skipped, and the one t is inside of is picked up mid-way.
    resync(t) {
      this.claimed = t;
      this.lastStart = null;
      for (const s of this.segs.values()) {
        s.queued = false;
        s.played = s.start + span(s) <= t - 0.05;
      }
    }

    stopClip({ requeue = false } = {}) {
      if (this.current) {
        try { this.current.src.stop(); } catch (_) {}
        if (requeue) Object.assign(this.current.seg, { played: false, queued: true });
        this.current = null;
      }
      this.ui.clearSubs();
    }

    // What to say now: the oldest sentence still owed (queued, spoken whole even
    // if late), else the one the video is inside of after a seek or the start.
    nextSegment(t) {
      let best = null;
      for (const s of this.segs.values()) {
        if (s.played || !s.audio || s.start > t + 0.08) continue;
        if (!s.queued && t >= s.start + span(s)) continue;
        if (!best || s.start < best.start) best = s;
      }
      return best;
    }

    async load(seg) {
      if (this.loading.has(seg.id) || this.buffers.has(seg.id) || this.loading.size >= 3) return;
      this.loading.add(seg.id);
      try {
        const res = await send({ type: "dub-audio", id: this.jobId, sid: seg.id, rev: seg.rev || 0 });
        if (!res.error && this.active && (res.rev ?? 0) === (seg.rev || 0)) { // not an older version
          const bin = atob(res.b64);
          const bytes = new Uint8Array(bin.length);
          for (let i = 0; i < bin.length; i++) bytes[i] = bin.charCodeAt(i);
          this.buffers.set(seg.id, await this.ctx.decodeAudioData(bytes.buffer));
        }
      } catch (_) {
        /* retried on a later tick */
      } finally {
        this.loading.delete(seg.id);
      }
    }

    manageBuffers(t) {
      // owed sentences first: they may already lie behind the video
      for (const s of this.segs.values()) {
        if (s.queued && !this.buffers.has(s.id)) this.load(s);
      }
      for (const s of this.segs.values()) {
        if (s.audio && s.start >= t - 3 && s.start <= t + 40 && !this.buffers.has(s.id)) this.load(s);
      }
      for (const id of this.buffers.keys()) {
        const s = this.segs.get(id);
        if (!s || (!s.queued && (s.start < t - 120 || s.start > t + 400))) this.buffers.delete(id);
      }
    }

    play(seg, t) {
      const buf = this.buffers.get(seg.id);
      if (!buf) {
        // an owed sentence waits for its audio, but not forever (~5 s): it may never come
        if (seg.queued && (seg.waits = (seg.waits || 0) + 1) > 50) Object.assign(seg, { played: true, queued: false });
        return this.load(seg);
      }
      // an owed sentence is always spoken from its beginning; only one the video
      // was already inside of (a seek, the first start) is picked up where the
      // video is when that is far in, or skipped when almost over
      const late = t - seg.start;
      const offset = seg.queued || late < 2.5 ? 0 : late;
      Object.assign(seg, { played: true, queued: false });
      this.lastStart = seg.start;
      if (this.live) this.liveDelay = Math.max(0, t - seg.start);
      if (offset >= buf.duration - 0.25) return;
      // a late start is partly caught up in the clip (≤ MAX_BOOST, raises the
      // pitch a little); the rest by slowing the video (pace)
      const boost = offset === 0 && late > 0.25 ? Math.min(MAX_BOOST, 1 + late / buf.duration) : 1;
      const rate = this.userRate * boost; // the user's speed, never our slowdown
      const src = this.ctx.createBufferSource();
      src.buffer = buf;
      src.playbackRate.value = rate;
      src.connect(this.gain);
      src.start(0, offset);
      this.current = { src, seg, boost, rate, endsAt: this.ctx.currentTime + (buf.duration - offset) / rate };
      if (this.showSubs && (!this.live || this.shift)) this.ui.showSub(seg.fa);
      this.lastSeg = seg;
    }

    // ---------------------------------------------------------- elastic sync
    // A speed change we did not make (the site's speed menu) becomes the user's
    // speed; our own slowdown is never taken for the user's choice.
    syncRate() {
      const r = this.v.playbackRate;
      if (!r || Math.abs(r - this.userRate * this.slow) < 1e-3) return;
      const undone = this.slow !== 1 && Math.abs(r - this.userRate) < 1e-3;
      // a player that puts its speed back right away fights us: stop slowing
      if (undone && performance.now() - this.paceAt < 2000 && ++this.undone >= 3) this.noSlow = true;
      if (!undone) this.userRate = r;
      this.slow = 1;
      this.paceAt = performance.now();
      if (this.origPitch != null) { this.v.preservesPitch = this.origPitch; this.origPitch = null; }
      this.retime();
    }

    // Video speed = userRate × f (pitch kept; the original is ducked under the dub).
    setPace(f) {
      this.syncRate();
      if (this.noSlow) f = 1;
      if (f === this.slow) return;
      const v = this.v;
      if (f < 1 && this.origPitch == null) { this.origPitch = v.preservesPitch; v.preservesPitch = true; }
      this.slow = f;
      this.paceAt = performance.now();
      try { v.playbackRate = this.userRate * f; } catch (_) { this.slow = 1; }
      if (this.slow === 1 && this.origPitch != null) { v.preservesPitch = this.origPitch; this.origPitch = null; }
    }

    // The clip follows the user's speed; its end moves with it.
    retime() {
      const c = this.current;
      const rate = this.userRate * (c?.boost ?? 1);
      if (!c || rate === c.rate) return;
      const now = this.ctx.currentTime;
      c.endsAt = now + Math.max(0, c.endsAt - now) * c.rate / rate;
      c.rate = rate;
      c.src.playbackRate.value = rate;
    }

    // How late the dub will be (video seconds at the user's speed): the rest of
    // the clip, then each owed sentence in order until one starts on time
    // again. null = nothing is being said or owed.
    lag(t) {
      const c = this.current;
      const owed = [];
      for (const s of this.segs.values()) {
        if (!s.played && s.audio && (s.queued || (s.start > t && s.start < t + 30))) owed.push(s);
      }
      if (!c && !owed.some((s) => s.queued)) return null;
      owed.sort((a, b) => a.start - b.start);
      let at = t + (c ? Math.max(0, c.endsAt - this.ctx.currentTime) * this.userRate : 0);
      let worst = 0;
      for (const s of owed) {
        const late = at + 0.05 - s.start;
        if (late <= 0.2) break;
        worst = Math.max(worst, late);
        at += s.dur / Math.min(MAX_BOOST, 1 + late / s.dur);
      }
      return worst;
    }

    // Persian often runs longer than the original: instead of dropping speech
    // the video slows a little until the dub catches up. Measured against the
    // user's speed, so slowing does not undo itself; one change per 1.5 s at most.
    pace(t) {
      if (this.live) return this.setPace(1); // the dub follows; the picture never waits
      const lag = this.lag(t);
      const f = this.slow;
      let want = f;
      if (lag == null || lag <= 0.2) want = 1;
      else if (lag > 3) want = SLOWER;
      else if (lag > 0.4 && (f === 1 || lag < 2)) want = SLOW;
      if (want !== f && (lag == null || performance.now() - this.paceAt > 1500)) this.setPace(want);
    }

    // First start: rewind to the beginning of the sentence being spoken, so
    // the dub starts on a sentence boundary instead of lagging behind it.
    snapToSentence() {
      const t = this.v.currentTime;
      for (const s of this.segs.values()) {
        if (s.start <= t && t < s.end && t - s.start > 0.3 && t - s.start < 10) {
          this.v.currentTime = Math.max(0, s.start - 0.05);
          return;
        }
      }
    }

    tick() {
      const v = this.v;
      if (!v.isConnected) return this.stop();
      if (location.href !== this.url) return this.stop({ keepPaused: true }); // navigated to another video
      this.ui.place(v, true, this.status === "running" && !v.paused && !this.autoPaused);
      if (this.status === "error" || !this.jobId) return;
      if (isAd(v)) {
        this.stopClip({ requeue: true }); // said again, whole, after the ad
        this.setPace(1);
        this.ui.setState("wait", "بعد از تبلیغ ادامه می‌دهیم");
        return;
      }

      if (this.shift?.blocked()) { // fullscreen video / PiP: no delayed picture there, so no delayed sound
        this.shift.stop();
        this.shift = null;
        this.liveNote = "time shift off (fullscreen or picture-in-picture)";
        this.resync(v.currentTime);
        this.startCapture();
      }
      const t = this.now();
      if (this.shift && !this.shift.ready) return this.renderState(0); // the delay is still filling
      this.claimed ??= t;
      if (!v.seeking) this.lastT = t;
      const ahead = this.readyUntil - t;
      if (this.live && this.capCtx?.state === "suspended") this.capCtx.resume().catch(() => {});
      if (this.live) this.liveReport();
      if (!this.s.autoWait && this.firstStart && ahead >= 0.4) this.firstStart = false; // the dub has begun
      if (this.s.autoWait && this.status === "running" && !this.live) {
        if (!v.paused && !this.eof && ahead < 0.4) this.pauseVideo();
        else if (this.autoPaused && v.paused && (this.eof || ahead >= this.requiredAhead())) {
          if (this.firstStart) {
            this.snapToSentence();
            const took = performance.now() / 1000 - this.startedAt;
            if (took > 5) rememberStart(this.startKind, took); // a quicker start was an earlier dub of this video
          }
          this.autoPaused = false;
          this.firstStart = false;
          v.play().catch(() => {});
        }
      }
      this.renderState(ahead);
      if (!this.speak) return this.showTimedSub(t);
      if (this.live && !this.shift && this.showSubs) this.showLiveSub();
      this.manageBuffers(t);
      if ((v.paused && !v.ended) || v.seeking) return this.setPace(1);
      if (this.ctx.state !== "running") {
        // the browser has not let the dub sound (no click on the page yet):
        // nothing is said, so nothing may pile up or slow the video
        if (++this.mutedTicks > 10) { this.stopClip(); this.resync(t); this.setPace(1); }
        return;
      }
      this.mutedTicks = 0;
      this.syncRate();
      // a jump we saw no seek for (src swap around an ad): start over there
      if (t < this.claimed - 0.5 || t - this.claimed > 2.5 * this.userRate) this.resync(t);
      // every sentence the video reaches is owed in full, in order
      for (const s of this.segs.values()) {
        if (!s.played && s.audio && s.start > this.claimed && s.start <= t + 0.08) s.queued = true;
      }
      this.claimed = Math.max(this.claimed, t + 0.08);

      const c = this.current;
      if (!c || this.ctx.currentTime >= c.endsAt) {
        if (c) {
          this.current = null;
          if (!this.live || this.shift) this.ui.showSub("");
        }
        const seg = this.nextSegment(t);
        if (seg) this.play(seg, t);
      }
      if (v.ended) this.setPace(1);
      else this.pace(t);
    }

    // subtitles-only mode: show whichever sentence the video is in
    showTimedSub(t) {
      if (this.live && !this.shift) return this.showLiveSub();
      if (this.shift && (this.liveSubs.length || this.liveSub)) return this.showLiveSub(); // late ones first
      let text = "";
      for (const s of this.segs.values()) {
        if (s.start <= t && t < Math.max(s.end, s.start + 1.5)) { text = s.fa; this.lastSeg = s; break; }
      }
      this.ui.showSub(text);
    }

    renderState(ahead) {
      if (this.ctx?.state === "suspended" && !this.v.paused) {
        this.ui.setState("click", "برای شنیدن دوبله اینجا کلیک کنید");
      } else if (this.status === "resolving") {
        this.ui.setState("wait", this.startText(ahead, "در حال پیدا کردن صدای ویدیو…"));
      } else if (this.shift) {
        this.ui.setSync(true);
        const filled = this.shift.ready;
        this.ui.setState(filled ? "ok" : "wait", filled ? (this.speak ? "دوبله‌ی هم‌زمان" : "زیرنویس هم‌زمان")
          : "در حال آماده‌سازی هم‌زمانی…");
        this.ui.setBuffer(`تصویر ${faDigits(this.shift.delay)} ثانیه دیرتر نشان داده می‌شود`);
        return;
      } else if (this.live) {
        this.ui.setSync(false);
        this.ui.setState("ok", this.speak ? "دوبله‌ی زنده" : "زیرنویس زنده");
        const d = this.liveDelay;
        this.ui.setBuffer(this.segs.size === 0 ? "در حال شنیدن ویدیو…"
          : d != null ? `حدود ${faDigits(Math.round(d))} ثانیه پس از تصویر` : "");
        return;
      } else if (this.autoPaused || (this.firstStart && !this.eof && ahead < 0.4)) {
        this.ui.setState("wait", this.startText(ahead, "در حال آماده‌سازی دوبله…"));
      } else {
        this.ui.setState("ok", this.speak ? "دوبله‌ی فارسی" : "زیرنویس فارسی");
      }
      const heat = this.hot ? " · مک داغ است، آرام‌تر کار می‌کنم" : "";
      const slow = this.slow < 1 ? " · ویدیو کمی آهسته‌تر تا دوبله برسد" : "";
      if (this.eof) this.ui.setBuffer(`تا آخر آماده است${slow}`);
      else if (ahead > 1) this.ui.setBuffer(`${faDigits(Math.floor(ahead))} ثانیه آماده${heat}${slow}`);
      else this.ui.setBuffer((heat + slow).slice(3));
    }

    // Seconds until the video can play with the dub: from how fast the engine is
    // preparing it once that is measured; on the first start, before that, from how
    // long this kind of dub took the last times, counted down.
    secsToStart(ahead) {
      const missing = this.requiredAhead() - ahead;
      if (this.rate > 0.05) return Math.max(1, Math.ceil(missing / this.rate));
      if (!this.firstStart || this.startedAt == null || this.startEst == null) return null;
      return Math.ceil(this.startEst - (performance.now() / 1000 - this.startedAt));
    }

    startText(ahead, plain) {
      const what = this.speak ? "دوبله" : "زیرنویس";
      const secs = this.secsToStart(ahead);
      if (secs == null || secs >= 600) return plain;
      if (secs <= 0) return `${what} کمی دیرتر از معمول آماده می‌شود؛ چند لحظه‌ی دیگر…`;
      return `${what} حدود ${faDuration(secs)} دیگر شروع می‌شود`;
    }

    // ---------------------------------------------------------- ✎ fixes
    openFix() {
      if (!this.v.paused) { this.fixPaused = true; this.v.pause(); } // time to choose and type
      this.ui.openFix(this.lastSeg);
    }

    closeFix() {
      this.ui.closeFix();
      if (this.fixPaused) { this.fixPaused = false; this.v.play().catch(() => {}); }
    }

    async previewSay(text) {
      text = (text || "").trim();
      if (!text) return this.ui.fixMsg("اول بنویسید چطور گفته شود.", true);
      this.ui.fixMsg("در حال ساختن صدا…");
      const res = await send({ type: "tts-preview", text });
      if (res.error) return this.ui.fixMsg(res.error, true);
      const bytes = Uint8Array.from(atob(res.b64), (c) => c.charCodeAt(0));
      const url = URL.createObjectURL(new Blob([bytes], { type: "audio/wav" }));
      const audio = new Audio(url);
      audio.onended = () => URL.revokeObjectURL(url);
      audio.play().catch(() => {});
      this.ui.fixMsg("");
    }

    async lookupSpoken(word) {
      const res = await send({ type: "spoken", text: word });
      if (!res.error) this.ui.showSpoken(word, res.spoken, res.phonemes);
    }

    async saveFix({ kind, parts }) {
      if (!parts[0] || !parts[parts.length - 1]) return this.ui.fixMsg("هر دو کادر را پر کنید.", true);
      const res = await send({ type: "lexicon-add", kind, parts });
      if (res.error) return this.ui.fixMsg(res.error, true);
      this.ui.fixMsg("ذخیره شد؛ جمله‌های بعدی (حتی آن‌هایی که از قبل آماده بودند) با همین درست گفته می‌شوند.");
    }

    // ---------------------------------------------------------- live mode
    // The engine could not fetch this video: send it the sound of the <video>
    // as it plays (captureStream: the same sound the user hears, nothing else).
    enterLive() {
      this.live = true;
      clearInterval(this.pollTimer);
      this.pollTimer = setInterval(() => this.poll(), 350); // new sentences sooner
      this.noSlow = true;
      this.setPace(1);
      if (this.autoPaused) { this.autoPaused = false; this.v.play().catch(() => {}); }
      this.startShift();
    }

    // Time shift if this video allows it (see TimeShift), else the plain live mode.
    async startShift() {
      if (this.s.liveSync !== false && await TimeShift.possible(this.v) && this.active) {
        try {
          // seconds the picture is held back: enough for fast speech to be heard, translated (and spoken)
          const shift = new TimeShift(this.v, this.speak ? 10 : 8, this.speak ? 768 : 854,
                                      (data) => this.onCapture(data));
          shift.start();
          this.shift = shift;
          this.claimed = null;
          this.lastStart = null;
          this.liveNote = `time shift ${shift.delay}s`;
          return;
        } catch (e) {
          this.liveNote = `time shift failed: ${e.message}`;
        }
      } else {
        this.liveNote = this.s.liveSync === false ? "time shift turned off by the user" : "time shift not possible here";
      }
      this.startCapture();
    }

    startCapture() {
      let stream;
      try {
        stream = this.v.captureStream ? this.v.captureStream() : this.v.mozCaptureStream();
      } catch (e) {
        return this.fail("صدای این ویدیو از داخل صفحه هم گرفته نمی‌شود (قفل‌شده یا از سایت دیگری است).");
      }
      this.capStream = stream;
      this.capCtx = new AudioContext({ sampleRate: CAP_RATE });
      const ctx = this.capCtx;
      const hook = async () => {
        const tracks = stream.getAudioTracks();
        if (!tracks.length || this.capNode || this.capHooking || !this.active) return;
        this.capHooking = true;
        const node = await soundTap(ctx, (block) => this.onCapture(block));
        this.capHooking = false;
        if (this.capCtx !== ctx) return; // stopped meanwhile
        this.capNode = node;
        ctx.createMediaStreamSource(new MediaStream(tracks)).connect(node);
        node.connect(ctx.destination); // outputs silence; keeps it running
      };
      this.capHook = hook;
      stream.addEventListener("addtrack", hook);
      hook();
      this.capCtx.resume().catch(() => {});
    }

    onCapture(data) {
      const v = this.v;
      this.capBlocks = (this.capBlocks || 0) + 1;
      if (!this.jobId || v.paused || v.seeking || isAd(v)) return this.flushCapture();
      const rate = v.playbackRate || 1;
      const start = v.currentTime - (data.length / CAP_RATE) * rate; // this block was just heard
      const c = this.cap;
      if (c && (Math.abs(start - (c.t + (c.len / CAP_RATE) * c.rate)) > 0.6 || c.rate !== rate)) this.flushCapture();
      this.cap ??= { t: Math.max(0, start), rate, parts: [], len: 0 };
      this.cap.parts.push(new Float32Array(data));
      this.cap.len += data.length;
      if (this.cap.len >= CAP_RATE / 2) this.flushCapture(); // half a second at a time: less delay
    }

    // every 15 s in live mode, one line in the engine's log: is sound arriving, is the delayed
    // picture ready, is the video paused — so a dub that stops can be explained afterwards
    liveReport() {
      const now = performance.now();
      if (now - (this.reportAt || 0) < 15000 || !this.jobId) return;
      const v = this.v;
      const shift = this.shift ? `shift ${this.shift.ready ? "ready" : "filling"} shown=${(this.shift.mediaTime ?? -1).toFixed(1)}` : "no shift";
      const owed = [...this.segs.values()].filter((x) => x.queued).length;
      const note = `report: ${this.capBlocks || 0} sound blocks/15s, ${shift}, video=${v.currentTime.toFixed(1)}` +
        `${v.paused ? " paused" : ""}${v.seeking ? " seeking" : ""}, sentences=${this.segs.size} owed=${owed}` +
        `${this.capCtx ? ` capture=${this.capCtx.state}` : ""}${this.shift ? ` shiftAudio=${this.shift.ctx?.state}` : ""}`;
      this.reportAt = now;
      this.capBlocks = 0;
      send({ type: "dub-live", id: this.jobId, t: v.currentTime, rate: 1, pcm: "", note });
    }

    flushCapture() {
      const c = this.cap;
      this.cap = null;
      if (!c || !c.len || !this.jobId) return;
      const pcm = new Int16Array(c.len);
      let i = 0;
      for (const part of c.parts) {
        for (let j = 0; j < part.length; j++) pcm[i++] = Math.max(-32768, Math.min(32767, Math.round(part[j] * 32767)));
      }
      send({ type: "dub-live", id: this.jobId, t: c.t, rate: c.rate, pcm: toBase64(pcm.buffer), note: this.liveNote });
      this.liveNote = undefined; // said once
    }

    stopCapture() {
      this.shift?.stop();
      this.shift = null;
      this.flushCapture();
      try { this.capNode?.disconnect(); } catch (_) {}
      if (this.capHook) this.capStream?.removeEventListener("addtrack", this.capHook);
      this.capCtx?.close().catch(() => {});
      this.capNode = this.capCtx = this.capStream = null;
    }

    // live, subtitles only: each sentence as it arrives, long enough to read
    showLiveSub() {
      const now = performance.now();
      if (this.liveSub && now < this.liveSub.until) return;
      const seg = this.liveSubs.shift();
      if (!seg) { this.liveSub = null; return this.ui.showSub(""); }
      this.lastSeg = seg;
      // as long as the sentence lasted (readable), shorter when more are waiting
      const ms = Math.max(1500, Math.min(7000, (seg.end - seg.start) * 1000));
      this.liveSub = { until: now + (this.liveSubs.length ? Math.max(1200, ms * 0.6) : ms) };
      this.ui.showSub(seg.fa);
    }

    onMainClick() {
      if (!this.active) return this.start();
      if (this.ctx?.state === "suspended") this.ctx.resume().catch(() => {});
      return undefined;
    }
  }

  // ================================================================ wiring
  let overlay = null;
  let dubber = null;

  function ensureFor(video) {
    if (!overlay) {
      overlay = new Overlay({
        main: () => dubber?.onMainClick(),
        stop: () => dubber?.stop(),
        subs: () => dubber?.toggleSubs(),
        sync: () => dubber?.toggleSync(),
        fix: () => dubber?.openFix(),
        fixclose: () => dubber ? dubber.closeFix() : overlay.closeFix(),
        fixplay: (text) => dubber?.previewSay(text),
        fixsave: (v) => dubber?.saveFix(v),
        spoken: (w) => dubber?.lookupSpoken(w),
        smaller: () => nudgeLook({ scale: -0.1 }),
        bigger: () => nudgeLook({ scale: +0.1 }),
        lower: () => nudgeLook({ lift: -3 }),
        higher: () => nudgeLook({ lift: +3 }),
        volume: (v) => dubber?.setDuck(v),
      });
      overlay.look = look;
    }
    if (!dubber || dubber.v !== video) {
      if (dubber?.active) return dubber; // keep the running dub
      dubber = new Dubber(video, overlay);
    }
    return dubber;
  }

  // subtitle size and height: the user's choice, shared by every page
  const look = { scale: 1, lift: 12 };
  chrome.storage.local.get(["subScale", "subLift"], (r) => {
    look.scale = r?.subScale ?? 1;
    look.lift = r?.subLift ?? 12;
  });
  function nudgeLook({ scale = 0, lift = 0 }) {
    const next = { subScale: Math.round(Math.min(2, Math.max(0.6, look.scale + scale)) * 10) / 10,
                   subLift: Math.min(45, Math.max(2, look.lift + lift)) };
    chrome.storage.local.set(next);
  }

  // idle loop: keep the launcher glued to the biggest video on the page
  setInterval(() => {
    if (dubber?.active) return;
    const v = pickVideo();
    if (!v) {
      if (overlay) overlay.host.style.display = "none";
      return;
    }
    ensureFor(v);
    overlay.place(v, false, false);
  }, 250);

  function startOnPage() {
    const v = pickVideo();
    if (!v) return false;
    ensureFor(v).start();
    return true;
  }

  // Messages go to every frame of the tab; only a frame that owns a video (or
  // a running dub) answers, so the popup hears from the right one.
  chrome.runtime.onMessage.addListener((msg, _sender, reply) => {
    const active = Boolean(dubber?.active);
    const hasVideo = Boolean(pickVideo());
    if (!active && !hasVideo) return false;
    if (msg.cmd === "start") reply({ ok: active || startOnPage() });
    else if (msg.cmd === "stop") { dubber?.stop(); reply({ ok: true }); }
    else if (msg.cmd === "status") reply({ hasVideo, active });
    return false;
  });

  chrome.storage.onChanged.addListener((changes, area) => {
    if (area === "local" && changes.subScale) look.scale = changes.subScale.newValue ?? 1;
    if (area === "local" && changes.subLift) look.lift = changes.subLift.newValue ?? 12;
    if (area !== "local" || !dubber?.active) return;
    if (changes.mode && dubber.ownModeChange) { dubber.ownModeChange = false; return; }
    if (changes.voice || changes.quality || changes.mode || changes.style) { // new voice, model, mode or style = new job
      dubber.stop({ keepPaused: true });
      dubber.start();
    }
  });

  // "open this link and dub it" from the popup
  send({ type: "autostart?" }).then(async (r) => {
    if (!r.pending) return;
    for (let i = 0; i < 120; i++) {
      if (pickVideo()) {
        await send({ type: "autostart-claimed" });
        startOnPage();
        return;
      }
      await new Promise((ok) => setTimeout(ok, 500));
    }
  });
})();
