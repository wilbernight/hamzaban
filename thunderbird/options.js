const DEFAULTS = { port: 17860, autoTranslate: false, skipQuotes: true, model: "", voice: "female_narration" };
const api = globalThis.messenger ?? globalThis.browser;

async function load() {
  const s = Object.assign({}, DEFAULTS, await api.storage.local.get(Object.keys(DEFAULTS)));
  for (const [key, value] of Object.entries(s)) {
    const el = document.getElementById(key);
    if (!el) continue;
    if (el.type === "checkbox") el.checked = value;
    else el.value = value;
    el.addEventListener("change", () => {
      const v = el.type === "checkbox" ? el.checked : el.type === "number" ? Number(el.value) : el.value;
      api.storage.local.set({ [key]: v }).then(checkEngine);
    });
  }
  checkEngine();
}

async function checkEngine() {
  const box = document.getElementById("status");
  const { port } = Object.assign({}, DEFAULTS, await api.storage.local.get("port"));
  try {
    const h = await (await fetch(`http://127.0.0.1:${port}/api/health`)).json();
    const bits = [`موتور هم‌زبان روشن است (نسخه‌ی ${h.version})`];
    if (h.email_model_ready === false) bits.push(`مدل ${h.email_model} هنوز دانلود نشده`);
    if (h.email_model_ready === null) bits.push("Ollama روشن نیست");
    box.className = "status" + (bits.length === 1 ? " ok" : "");
    box.textContent = bits.join(" — ");
  } catch (_) {
    box.className = "status";
    box.textContent = "موتور هم‌زبان در دسترس نیست. اسکریپت نصب را اجرا کنید یا کامپیوتر را یک‌بار ری‌استارت کنید.";
  }
}

load();
