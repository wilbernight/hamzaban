// Hamzaban for Thunderbird — background page.
// Talks to the local engine (127.0.0.1) and relays results to the panel
// that display.js draws on top of the open e-mail.

const DEFAULTS = {
  port: 17860,
  autoTranslate: false,
  skipQuotes: true,
  model: "",            // "" = the engine's e-mail model (TranslateGemma 12B)
  voice: "female_narration",
};

const OFFLINE =
  "موتور هم‌زبان خاموش است. از آیکون هم‌زبان در نوار بالای صفحه‌ی مک روشنش کنید " +
  "(یا برنامه‌ی Hamzaban را از پوشه‌ی Applications باز کنید).";

async function settings() {
  return Object.assign({}, DEFAULTS, await messenger.storage.local.get(Object.keys(DEFAULTS)));
}

async function apiUrl(path) {
  const { port } = await settings();
  return `http://127.0.0.1:${port}${path}`;
}

async function postJSON(path, body, signal) {
  let res;
  try {
    res = await fetch(await apiUrl(path), {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
      signal,
    });
  } catch (e) {
    if (e.name === "AbortError") throw e;
    throw new Error(OFFLINE);
  }
  if (!res.ok) {
    let detail = `HTTP ${res.status}`;
    try { detail = (await res.json()).detail || detail; } catch (_) {}
    throw new Error(detail);
  }
  return res;
}

async function* ndjson(res) {
  const reader = res.body.getReader();
  const dec = new TextDecoder();
  let buf = "";
  for (;;) {
    const { value, done } = await reader.read();
    if (done) break;
    buf += dec.decode(value, { stream: true });
    let i;
    while ((i = buf.indexOf("\n")) >= 0) {
      const line = buf.slice(0, i);
      buf = buf.slice(i + 1);
      if (line.trim()) yield JSON.parse(line);
    }
  }
  if (buf.trim()) yield JSON.parse(buf);
}

// ---------------------------------------------------------------- panel script
messenger.messageDisplayScripts.register({
  js: [{ file: "display.js" }],
  css: [{ file: "display.css" }],
});

async function sendToTab(tabId, msg) {
  try {
    return await messenger.tabs.sendMessage(tabId, msg);
  } catch (e) {
    // a message that was already open before the add-on started
    await messenger.tabs.insertCSS(tabId, { file: "display.css" });
    await messenger.tabs.executeScript(tabId, { file: "display.js" });
    return messenger.tabs.sendMessage(tabId, msg);
  }
}

messenger.messageDisplayAction.onClicked.addListener((tab) => {
  sendToTab(tab.id, { cmd: "toggle" }).catch(console.error);
});

messenger.menus.create({
  id: "hz-selection",
  title: "ترجمه‌ی متن انتخاب‌شده به فارسی",
  contexts: ["selection"],
});

messenger.menus.onClicked.addListener((info, tab) => {
  if (info.menuItemId === "hz-selection" && info.selectionText && tab) {
    sendToTab(tab.id, { cmd: "selection", text: info.selectionText }).catch(console.error);
  }
});

// ---------------------------------------------------------------- translation
async function displayedMessage(tabId) {
  try {
    const md = messenger.messageDisplay;
    if (md.getDisplayedMessages) {
      const list = await md.getDisplayedMessages(tabId);
      const msgs = Array.isArray(list) ? list : list?.messages || [];
      if (msgs.length === 1) return msgs[0];
    }
    if (md.getDisplayedMessage) return await md.getDisplayedMessage(tabId);
  } catch (_) {}
  return null;
}

messenger.runtime.onConnect.addListener((port) => {
  if (port.name !== "hz-translate") return;
  const ctl = new AbortController();
  port.onDisconnect.addListener(() => ctl.abort());
  const post = (m) => { try { port.postMessage(m); } catch (_) {} };

  port.onMessage.addListener(async (msg) => {
    if (msg.type !== "translate") return;
    const s = await settings();
    const model = s.model || undefined;
    try {
      if (msg.withSubject && port.sender?.tab) {
        const header = await displayedMessage(port.sender.tab.id);
        if (header?.subject) {
          postJSON("/api/translate", { text: header.subject, purpose: "email", model }, ctl.signal)
            .then((r) => r.json())
            .then((r) => post({ type: "subject", text: r.text, original: header.subject }))
            .catch(() => {});
        }
      }
      const res = await postJSON("/api/translate/stream",
        { text: msg.text, purpose: "email", model }, ctl.signal);
      for await (const ev of ndjson(res)) post(ev);
    } catch (e) {
      if (e.name !== "AbortError") post({ type: "error", message: e.message });
    }
  });
});

messenger.runtime.onMessage.addListener(async (msg) => {
  if (msg.type === "settings") return settings();
  return undefined;
});

// ---------------------------------------------------------------- reading aloud
// The audio plays here and not inside the e-mail: the message pane is a
// locked-down content document where playback fails silently, the add-on's
// own page is not. One reader at a time; disconnecting its port stops it.
let reader = null;

function speechChunks(text) {
  const parts = text.replace(/\n+/g, "\n").split(/(?<=[.!؟?\n])\s*/);
  const out = [];
  let cur = "";
  for (const p of parts) {
    if (cur && (cur + " " + p).length > 320) { out.push(cur); cur = p; }
    else cur = cur ? `${cur} ${p}` : p;
  }
  if (cur.trim()) out.push(cur);
  return out.filter((c) => /\p{L}/u.test(c));
}

async function readAloud(port, text) {
  reader?.stop();
  const ctl = new AbortController();
  let audio = null;
  const me = { stop() { ctl.abort(); audio?.pause(); } };
  reader = me;
  port.onDisconnect.addListener(() => me.stop());
  const post = (m) => { try { port.postMessage(m); } catch (_) {} };

  const { voice } = await settings();
  const fetchAudio = (t) => postJSON("/api/tts", { text: t, voice }, ctl.signal)
    .then((r) => r.blob())
    .then((b) => URL.createObjectURL(b));
  const list = speechChunks(text);
  let next = list.length ? fetchAudio(list[0]) : null;
  try {
    for (let i = 0; i < list.length && !ctl.signal.aborted; i++) {
      const url = await next;
      next = i + 1 < list.length ? fetchAudio(list[i + 1]) : null; // prefetch while playing
      next?.catch(() => {});
      if (ctl.signal.aborted) { URL.revokeObjectURL(url); break; }
      audio = new Audio(url);
      try {
        await new Promise((ok, fail) => {
          audio.onended = audio.onpause = ok;
          audio.onerror = () => fail(new Error("پخش صدا ناموفق بود."));
          audio.play().catch(fail);
        });
      } finally {
        URL.revokeObjectURL(url);
      }
    }
    post({ type: "done" });
  } catch (e) {
    if (ctl.signal.aborted || e.name === "AbortError") post({ type: "done" });
    else post({ type: "error", message: e.message || String(e) });
  } finally {
    next?.then((url) => URL.revokeObjectURL(url), () => {});
    if (reader === me) reader = null;
  }
}

messenger.runtime.onConnect.addListener((port) => {
  if (port.name !== "hz-speak") return;
  port.onMessage.addListener((msg) => {
    if (msg.type === "speak") readAloud(port, msg.text);
  });
});
