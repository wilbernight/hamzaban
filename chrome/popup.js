const $ = (id) => document.getElementById(id);
const send = (msg) => chrome.runtime.sendMessage(msg).catch((e) => ({ error: e.message }));

const FALLBACK_VOICES = [
  { id: "omni_female", name: "VoiceStudio (اُمنی) · بانو — پیشنهادی" },
  { id: "omni_male", name: "VoiceStudio (اُمنی) · آقا — پیشنهادی" },
  { id: "male_news", name: "آقا · خبری" },
  { id: "female_narration", name: "بانو · روایت" },
  { id: "male_hello", name: "آقا · صمیمی" },
];

function setStatus(kind, text) {
  $("status").className = `status ${kind}`;
  $("status-text").textContent = text;
}

async function checkEngine() {
  const h = await send({ type: "health" });
  if (h.error) return setStatus("err", h.error);
  if (h.dub_model_ready === null) return setStatus("warn", "موتور روشن است ولی Ollama نه. در ترمینال: brew services start ollama");
  if (h.dub_model_ready === false) return setStatus("warn", `مدل ${h.dub_model} هنوز دانلود نشده است.`);
  if (!h.tts_ready) return setStatus("warn", "مدل صدای فارسی نصب نشده است؛ اسکریپت نصب را دوباره اجرا کنید.");
  setStatus("ok", "موتور هم‌زبان آماده است");
}

async function loadSettings() {
  const s = await send({ type: "settings" });
  const v = await send({ type: "voices" });
  const voices = v.voices?.length ? v.voices : FALLBACK_VOICES;
  $("voice").replaceChildren(...voices.map((x) => new Option(x.name, x.id, false, x.id === s.voice)));
  $("quality").value = s.quality || "fast";
  $("duck").value = Math.round((s.duck ?? 0.15) * 100);
  $("mode").value = s.mode || "both";
  $("style").value = s.style || "spoken";
  $("autoWait").checked = s.autoWait;
  $("subScale").value = Math.round((s.subScale ?? 1) * 100);
  $("subLift").value = s.subLift ?? 12;

  $("voice").onchange = () => chrome.storage.local.set({ voice: $("voice").value });
  $("quality").onchange = () => chrome.storage.local.set({ quality: $("quality").value });
  $("duck").oninput = () => chrome.storage.local.set({ duck: Number($("duck").value) / 100 });
  $("mode").onchange = () => chrome.storage.local.set({ mode: $("mode").value });
  $("style").onchange = () => chrome.storage.local.set({ style: $("style").value });
  $("autoWait").onchange = () => chrome.storage.local.set({ autoWait: $("autoWait").checked });
  $("subScale").oninput = () => chrome.storage.local.set({ subScale: Number($("subScale").value) / 100 });
  $("subLift").oninput = () => chrome.storage.local.set({ subLift: Number($("subLift").value) });
}

async function checkPage() {
  const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
  if (!tab?.id) return;
  const res = await chrome.tabs.sendMessage(tab.id, { cmd: "status" }).catch(() => null);
  if (res?.active) {
    $("dub-here").textContent = "قطع دوبله‌ی این صفحه";
    $("dub-here").disabled = false;
    $("dub-here").onclick = () => chrome.tabs.sendMessage(tab.id, { cmd: "stop" }).then(() => window.close());
  } else if (res?.hasVideo) {
    $("dub-here").disabled = false;
    $("dub-here").onclick = () => chrome.tabs.sendMessage(tab.id, { cmd: "start" }).then(() => window.close());
  } else {
    $("here-hint").textContent = "در این صفحه ویدیویی پیدا نشد (اگر صفحه تازه باز شده، یک‌بار رفرش کنید).";
  }
}

$("link-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const url = $("link").value.trim();
  if (!/^https?:\/\//.test(url)) return;
  await send({ type: "open-and-dub", url });
  window.close();
});

checkEngine();
loadSettings();
checkPage();


// ---------------------------------------------------------------- the user's words
async function loadLexicon(kind) {
  const box = document.querySelector(`#lex-${kind} .lex-list`);
  const res = await send({ type: "lexicon-list", kind });
  if (res.error) { box.innerHTML = `<li class="lex-empty">${res.error}</li>`; return; }
  const entries = res.entries || [];
  if (!entries.length) { box.innerHTML = '<li class="lex-empty">هنوز چیزی اضافه نشده.</li>'; return; }
  box.replaceChildren(...entries.map((e) => {
    const li = document.createElement("li");
    const text = document.createElement("span");
    const shown = kind === "idioms" ? e[0].replace(/\\(.)/g, "$1") : e[0]; // typed text was escaped
    text.textContent = `${shown} ← ${kind === "idioms" ? (e[2] || e[1]) : e[1]}`;
    const del = document.createElement("button");
    del.textContent = "حذف";
    del.onclick = async () => { await send({ type: "lexicon-delete", kind, key: e[0] }); loadLexicon(kind); };
    li.append(text, del);
    return li;
  }));
}

for (const form of document.querySelectorAll(".lex-form")) {
  form.addEventListener("submit", async (ev) => {
    ev.preventDefault();
    const kind = form.dataset.kind;
    const a = form.elements.a.value.trim();
    const b = form.elements.b.value.trim();
    const parts = kind === "idioms" ? [a, "", b] : [a, b];
    const res = await send({ type: "lexicon-add", kind, parts });
    if (res.error) { alert(res.error); return; }
    form.reset();
    loadLexicon(kind);
  });
}
loadLexicon("pronounce");
loadLexicon("idioms");
