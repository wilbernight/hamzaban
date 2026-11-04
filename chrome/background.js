// Hamzaban dubbing — service worker. All traffic to the local engine goes
// through here (extension origin), never from the web page itself.

const DEFAULTS = { port: 17860, voice: "omni_female", quality: "fast", mode: "both", duck: 0.15, autoWait: true,
                   subScale: 1, subLift: 12, // subtitle size (×) and height (% of the video from the bottom)
                   style: "spoken" };        // dub voice: spoken Persian, or "written" (reads the translation)

const OFFLINE =
  "موتور هم‌زبان خاموش است. از آیکون هم‌زبان در نوار بالای صفحه‌ی مک روشنش کنید " +
  "(یا برنامه‌ی Hamzaban را از پوشه‌ی Applications باز کنید).";

async function settings() {
  return Object.assign({}, DEFAULTS, await chrome.storage.local.get(Object.keys(DEFAULTS)));
}

async function engine(path, { method = "GET", body, raw = false } = {}) {
  const { port } = await settings();
  let res;
  try {
    res = await fetch(`http://127.0.0.1:${port}${path}`, {
      method,
      headers: body ? { "Content-Type": "application/json" } : undefined,
      body: body ? JSON.stringify(body) : undefined,
    });
  } catch (_) {
    throw new Error(OFFLINE);
  }
  if (!res.ok) {
    let detail = `HTTP ${res.status}`;
    try { detail = (await res.json()).detail || detail; } catch (_) {}
    throw new Error(detail);
  }
  return raw ? res : res.json();
}

function toBase64(buffer) {
  const bytes = new Uint8Array(buffer);
  let s = "";
  for (let i = 0; i < bytes.length; i += 0x8000) {
    s += String.fromCharCode.apply(null, bytes.subarray(i, i + 0x8000));
  }
  return btoa(s);
}

const pendingKey = (tabId) => `autostart:${tabId}`;

// YouTube refuses yt-dlp without a signed-in session ("confirm you're not a bot").
// Only for YouTube links, only youtube.com cookies, only to the local engine,
// which keeps them in memory and hands them to yt-dlp for the duration of a call.
async function youtubeCookies(url) {
  let host;
  try { host = new URL(url).hostname; } catch (_) { return {}; }
  if (!/(^|\.)(youtube\.com|youtu\.be)$/.test(host)) return {};
  if (!chrome.cookies) return { note: "no cookies permission (reload the extension)" };
  try {
    const all = await chrome.cookies.getAll({ domain: "youtube.com" });
    const cookies = all.map((c) => ({ domain: c.domain, path: c.path, secure: c.secure, hostOnly: c.hostOnly,
                                      expires: Math.floor(c.expirationDate || 0), name: c.name, value: c.value }));
    return { cookies, note: `${cookies.length} cookies` };
  } catch (e) {
    return { note: `cookies failed: ${e.message}` };
  }
}

async function handle(msg, sender) {
  switch (msg.type) {
    case "settings":
      return settings();
    case "health":
      return engine("/api/health");
    case "voices":
      return engine("/api/voices");
    case "dub-start": {
      const s = await settings();
      const yt = await youtubeCookies(msg.url);
      return engine("/api/dub/start", {
        method: "POST",
        body: { url: msg.url, media: msg.media, t: msg.t, voice: msg.voice || s.voice, quality: s.quality, mode: s.mode,
                captions: msg.captions || undefined, cookies: yt.cookies, cookie_note: yt.note, style: s.style,
                can_live: true }, // no link the engine can read: the page sends the video's sound
      });
    }
    case "dub-poll":
      // no playhead during an ad: leave t out entirely (an empty t is rejected with 422)
      return engine(`/api/dub/${msg.id}?${msg.t == null ? "" : `t=${msg.t}&`}since=${msg.since || 0}` +
                    (msg.tsince == null ? "" : `&tsince=${msg.tsince}`));
    case "dub-audio": { // rev: the version of the sentence (a saved fix makes a new one)
      const res = await engine(`/api/dub/${msg.id}/audio/${msg.sid}?rev=${msg.rev || 0}`, { raw: true });
      return { b64: toBase64(await res.arrayBuffer()), rev: msg.rev || 0 };
    }
    case "fetch-captions": {
      // only subtitle files from Udemy's CDN (the page cannot read them cross-origin)
      const u = new URL(msg.url);
      if (u.protocol !== "https:" || !/(^|\.)udemycdn\.com$/.test(u.hostname)) return { error: "آدرس زیرنویس مجاز نیست" };
      const r = await fetch(u.href);
      if (!r.ok) return { error: `زیرنویس دریافت نشد (${r.status})` };
      return { text: await r.text() };
    }
    case "dub-live": // a second of the video's own sound (live mode)
      return engine(`/api/dub/${msg.id}/live`, { method: "POST", body: { t: msg.t, rate: msg.rate, pcm: msg.pcm, note: msg.note } });
    case "spoken": // what the voice gets for a word now
      return engine(`/api/spoken?text=${encodeURIComponent(msg.text || "")}&voice=${encodeURIComponent((await settings()).voice)}`);
    case "lexicon-list": // the user's pronunciations / idioms (✎ panel, popup)
      return engine(`/api/lexicon/${msg.kind}`);
    case "lexicon-add":
      return engine(`/api/lexicon/${msg.kind}`, { method: "POST", body: { parts: msg.parts } });
    case "lexicon-delete":
      return engine(`/api/lexicon/${msg.kind}/delete`, { method: "POST", body: { key: msg.key } });
    case "tts-preview": { // hear how a word will be said, before saving it
      const s = await settings();
      // a phoneme fix (Latin, ParSiGo voices) is heard exactly as it will be said
      const phonemes = /^[A-Za-z? ]+$/.test(msg.text || "") ? msg.text : undefined;
      const res = await engine("/api/tts", { method: "POST", raw: true,
                                             body: { text: phonemes ? "" : msg.text, phonemes, voice: msg.voice || s.voice } });
      return { b64: toBase64(await res.arrayBuffer()) };
    }
    case "dub-stop":
      return engine(`/api/dub/${msg.id}/stop`, { method: "POST" });
    case "open-and-dub": {
      const tab = await chrome.tabs.create({ url: msg.url });
      await chrome.storage.session.set({ [pendingKey(tab.id)]: Date.now() });
      return { ok: true };
    }
    case "autostart?": {
      const key = pendingKey(sender.tab?.id);
      const got = (await chrome.storage.session.get(key))[key];
      return { pending: Boolean(got && Date.now() - got < 120000) };
    }
    case "autostart-claimed":
      await chrome.storage.session.remove(pendingKey(sender.tab?.id));
      return { ok: true };
    default:
      return { error: "unknown message" };
  }
}

chrome.runtime.onMessage.addListener((msg, sender, reply) => {
  handle(msg, sender).then(reply, (e) => reply({ error: e.message }));
  return true; // async reply
});

chrome.runtime.onInstalled.addListener(() => {
  // a reload keeps the old item: create it afresh ("duplicate id" otherwise)
  chrome.contextMenus.removeAll(() => {
    chrome.contextMenus.create({ id: "hz-dub", title: "دوبله‌ی فارسی این ویدیو", contexts: ["video"] });
  });
});

chrome.contextMenus.onClicked.addListener((info, tab) => {
  if (info.menuItemId === "hz-dub" && tab?.id != null) {
    chrome.tabs.sendMessage(tab.id, { cmd: "start" }, { frameId: info.frameId }).catch(() => {});
  }
});

chrome.tabs.onRemoved.addListener((tabId) => chrome.storage.session.remove(pendingKey(tabId)));
