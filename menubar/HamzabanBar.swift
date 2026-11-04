// Hamzaban menu-bar app: switch the engine on/off and see what is in memory.
//
// Off = the background service is stopped and the translation models are
// unloaded from Ollama, so the RAM/GPU is free for other models. On = the
// service is started again (models load on first use).
//
// The same binary also works from a terminal:  Hamzaban --status | --on | --off
import AppKit
import ServiceManagement
import SwiftUI

let serviceLabel = "com.hamzaban.engine"
let home = FileManager.default.homeDirectoryForCurrentUser.path
let appHome = home + "/Library/Application Support/Hamzaban"
let plistPath = home + "/Library/LaunchAgents/\(serviceLabel).plist"
let domain = "gui/\(getuid())"
let engineURL = "http://127.0.0.1:17860"
let ollamaURL = "http://127.0.0.1:11434"
let ourModelPrefix = "translategemma"

// ------------------------------------------------------------------ helpers
@discardableResult
func sh(_ path: String, _ args: [String]) -> (status: Int32, output: String) {
    let p = Process()
    p.executableURL = URL(fileURLWithPath: path)
    p.arguments = args
    let pipe = Pipe()
    p.standardOutput = pipe
    p.standardError = pipe
    do { try p.run() } catch { return (-1, "\(error)") }
    let data = pipe.fileHandleForReading.readDataToEndOfFile()
    p.waitUntilExit()
    return (p.terminationStatus, String(data: data, encoding: .utf8) ?? "")
}

func http(_ url: String, method: String = "GET", body: [String: Any]? = nil, timeout: Double = 2) -> Data? {
    guard let u = URL(string: url) else { return nil }
    var req = URLRequest(url: u)
    req.httpMethod = method
    req.timeoutInterval = timeout
    if let body {
        req.httpBody = try? JSONSerialization.data(withJSONObject: body)
        req.setValue("application/json", forHTTPHeaderField: "Content-Type")
    }
    let done = DispatchSemaphore(value: 0)
    var result: Data?
    URLSession.shared.dataTask(with: req) { data, resp, _ in
        if let r = resp as? HTTPURLResponse, r.statusCode == 200 { result = data }
        done.signal()
    }.resume()
    _ = done.wait(timeout: .now() + timeout + 0.5)
    return result
}

struct LoadedModel: Identifiable, Sendable {
    let id: String
    let gigabytes: Double
    var ours: Bool { id.hasPrefix(ourModelPrefix) }
}

struct Snapshot: Sendable {
    var serviceLoaded = false
    var healthy = false
    var autoStart = true
    var engineMB = 0
    var models: [LoadedModel] = []

    static func take() -> Snapshot {
        var s = Snapshot()
        let printed = sh("/bin/launchctl", ["print", "\(domain)/\(serviceLabel)"])
        s.serviceLoaded = printed.status == 0
        if let line = printed.output.split(separator: "\n").first(where: { $0.trimmingCharacters(in: .whitespaces).hasPrefix("pid = ") }),
           let pid = Int(line.split(separator: "=").last!.trimmingCharacters(in: .whitespaces)) {
            let rss = sh("/bin/ps", ["-o", "rss=", "-p", "\(pid)"]).output.trimmingCharacters(in: .whitespacesAndNewlines)
            s.engineMB = (Int(rss) ?? 0) / 1024
        }
        let disabled = sh("/bin/launchctl", ["print-disabled", domain]).output
        s.autoStart = !(disabled.contains("\"\(serviceLabel)\" => disabled") || disabled.contains("\"\(serviceLabel)\" => true"))
        s.healthy = http(engineURL + "/api/health", timeout: 1.5) != nil
        if let data = http(ollamaURL + "/api/ps", timeout: 1.5),
           let obj = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
           let list = obj["models"] as? [[String: Any]] {
            s.models = list.compactMap { m in
                guard let name = m["name"] as? String else { return nil }
                let bytes = (m["size_vram"] as? Double) ?? (m["size"] as? Double) ?? 0
                return LoadedModel(id: name, gigabytes: bytes / 1e9)
            }
        }
        return s
    }
}

enum Control {
    static func unloadOurModels() {
        for m in Snapshot.take().models where m.ours {
            _ = http(ollamaURL + "/api/generate", method: "POST", body: ["model": m.id, "keep_alive": 0], timeout: 10)
        }
    }

    static func turnOn(autoStart: Bool) {
        sh("/bin/launchctl", ["enable", "\(domain)/\(serviceLabel)"])  // a disabled service cannot start
        if sh("/bin/launchctl", ["print", "\(domain)/\(serviceLabel)"]).status != 0 {
            sh("/bin/launchctl", ["bootstrap", domain, plistPath])
        } else {
            sh("/bin/launchctl", ["kickstart", "\(domain)/\(serviceLabel)"])
        }
        if !autoStart { sh("/bin/launchctl", ["disable", "\(domain)/\(serviceLabel)"]) }
    }

    static func turnOff() {
        sh("/bin/launchctl", ["bootout", "\(domain)/\(serviceLabel)"])
        unloadOurModels()
    }

    static func setAutoStart(_ on: Bool) {
        sh("/bin/launchctl", [on ? "enable" : "disable", "\(domain)/\(serviceLabel)"])
    }
}

// ------------------------------------------------------------------ model
@MainActor
final class EngineState: ObservableObject {
    @Published var snap = Snapshot()
    @Published var busy = false
    @Published var busyText = ""
    @Published var launchAtLogin = SMAppService.mainApp.status == .enabled
    private var timer: Timer?

    init() {
        refresh()
        timer = Timer.scheduledTimer(withTimeInterval: 4, repeats: true) { [weak self] _ in
            Task { @MainActor in self?.refresh() }
        }
    }

    func refresh() {
        Task {
            let s = await Task.detached { Snapshot.take() }.value
            self.snap = s
        }
    }

    func run(_ text: String, _ work: @escaping @Sendable () -> Void) {
        busy = true
        busyText = text
        Task {
            await Task.detached { work() }.value
            // give the service a moment to come up / go down before re-reading
            try? await Task.sleep(nanoseconds: 1_500_000_000)
            self.busy = false
            self.refresh()
        }
    }

    func setEngine(_ on: Bool) {
        let auto = snap.autoStart
        if on { run("در حال روشن کردن…") { Control.turnOn(autoStart: auto) } }
        else { run("در حال خاموش کردن و آزاد کردن حافظه…") { Control.turnOff() } }
    }

    func setAutoStart(_ on: Bool) { run("") { Control.setAutoStart(on) } }
    func setLaunchAtLogin(_ on: Bool) {
        do {
            if on { try SMAppService.mainApp.register() } else { try SMAppService.mainApp.unregister() }
        } catch {}
        launchAtLogin = SMAppService.mainApp.status == .enabled
    }

    func freeModels() { run("در حال خالی کردن حافظه‌ی مدل ترجمه…") { Control.unloadOurModels() } }

    var statusText: String {
        if busy { return busyText }
        if snap.healthy { return "روشن" }
        if snap.serviceLoaded { return "در حال راه‌اندازی…" }
        return "خاموش"
    }

    var statusColor: Color {
        if snap.healthy { return .green }
        if snap.serviceLoaded || busy { return .orange }
        return .secondary
    }
}


// ------------------------------------------------------------------ the user's words
// pronounce.txt («word = how to say it») and idioms.txt («english = plain meaning = Persian»),
// the same files the browser extension's ✎ panel writes. Edited here directly, so this works
// with the engine off; the engine re-reads them on every change.
enum LexKind: String, CaseIterable, Identifiable {
    case pronounce, idioms
    var id: String { rawValue }
    var title: String { self == .pronounce ? "تلفظ درست" : "ترجمه‌ی اصطلاح" }
    var path: String { appHome + "/\(rawValue).txt" }
    var fields: Int { self == .pronounce ? 2 : 3 }
}

struct LexEntry: Identifiable, Hashable {
    let key: String       // first part, lower-cased: what identifies the entry
    let shown: String     // as the user typed it
    let replacement: String
    var id: String { key }
}

enum Lexicon {
    static func lines(_ k: LexKind) -> [String] {
        ((try? String(contentsOfFile: k.path, encoding: .utf8)) ?? "").components(separatedBy: "\n")
    }

    static func key(_ line: String) -> String? {
        let t = line.trimmingCharacters(in: .whitespaces)
        guard !t.isEmpty, !t.hasPrefix("#"), let eq = t.firstIndex(of: "=") else { return nil }
        return t[..<eq].trimmingCharacters(in: .whitespaces).lowercased()
    }

    static func list(_ k: LexKind) -> [LexEntry] {
        lines(k).compactMap { line in
            guard let key = key(line) else { return nil }
            let parts = line.components(separatedBy: "=").map { $0.trimmingCharacters(in: .whitespaces) }
            let first = parts[0].replacingOccurrences(of: #"\\(.)"#, with: "$1", options: .regularExpression)
            let repl = k == .pronounce ? (parts.count > 1 ? parts[1] : "")
                : (parts.count > 2 && !parts[2].isEmpty ? parts[2] : (parts.count > 1 ? parts[1] : ""))
            return LexEntry(key: key, shown: first, replacement: repl)
        }
    }

    /// The user's file, plus the copies kept in the code (so a fix survives reinstalls) —
    /// what the engine does itself when it is running.
    static func write(_ k: LexKind, _ lines: [String]) {
        let text = lines.joined(separator: "\n").trimmingCharacters(in: .newlines) + "\n"
        try? FileManager.default.createDirectory(atPath: appHome, withIntermediateDirectories: true)
        try? text.write(toFile: k.path, atomically: true, encoding: .utf8)
        let entries = lines.filter { key($0) != nil }
        let head = "# Fixes saved from the ✎ panel / the Mac app (also in ~/Library/Application Support/Hamzaban/\(k.rawValue).txt). Kept in the code so they survive reinstalls.\n"
        var dirs = [appHome + "/engine/hamzaban/words", appHome + "/source/engine/hamzaban/words"]
        if let src = try? String(contentsOfFile: appHome + "/source_dir.txt", encoding: .utf8) {
            let s = src.trimmingCharacters(in: .whitespacesAndNewlines)
            if !s.isEmpty { dirs.append(s + "/engine/hamzaban/words") }
        }
        for dir in Set(dirs) {
            try? FileManager.default.createDirectory(atPath: dir, withIntermediateDirectories: true)
            try? (head + entries.joined(separator: "\n") + (entries.isEmpty ? "" : "\n"))
                .write(toFile: dir + "/\(k.rawValue).txt", atomically: true, encoding: .utf8)
        }
    }

    /// Through the running engine when possible: it also redoes the sentences a dub has
    /// prepared but not played yet. false = engine off (the caller writes the files).
    static func viaEngine(_ k: LexKind, _ path: String, _ body: [String: Any]) -> Bool {
        http(engineURL + "/api/lexicon/\(k.rawValue)" + path, method: "POST", body: body, timeout: 5) != nil
    }

    /// Adds (or replaces) an entry. nil = saved, else the reason it was not.
    static func add(_ k: LexKind, _ word: String, _ replacement: String) -> String? {
        let clean = { (s: String) in s.split(whereSeparator: \.isWhitespace).joined(separator: " ") }
        var first = clean(word), repl = clean(replacement)
        if first.isEmpty || repl.isEmpty { return "هر دو کادر را پر کنید." }
        if (first + repl).contains("=") || (first + repl).contains("#") { return "از = و # استفاده نکنید." }
        if k == .pronounce {
            // a rule is for the bare word; the engine applies it with any ending («‌ها»، «‌ی»…)
            let ending = "\u{200C}(هایی|های|ها|ای|ام|ات|اش|ی)$"
            first = first.replacingOccurrences(of: ending, with: "", options: .regularExpression)
            // one spelling, two words: the engine picks the reading from the English sentence
            let twoReadings: Set<String> = ["اشکال", "کشتی", "مرد", "کردها", "شکر", "پر", "مهر", "گرد", "خرد", "کشت",
                                            "کره", "ملک", "حکم", "سم"]
            if twoReadings.contains(first) {
                return "«\(first)» دو تلفظ دارد و هم‌زبان خودش از روی جمله‌ی انگلیسی درستش را انتخاب می‌کند؛ یک قاعده‌ی همیشگی معنای دیگرش را خراب می‌کند."
            }
            let same = { (s: String) in s.replacingOccurrences(of: "\u{200C}", with: "").replacingOccurrences(of: " ", with: "").lowercased() }
            if same(first) == same(repl.replacingOccurrences(of: ending, with: "", options: .regularExpression)) {
                return "تلفظ با خود کلمه یکی است؛ آن را طوری بنویسید که باید گفته شود (مثلاً با اعراب: کُد)."
            }
        }
        if k == .idioms {  // typed text, not a pattern (the engine reads it as a regular expression)
            first = NSRegularExpression.escapedPattern(for: first.trimmingCharacters(in: CharacterSet(charactersIn: " .,!?;:")))
                .replacingOccurrences(of: "\\ ", with: " ")
        }
        if viaEngine(k, "", ["parts": k == .pronounce ? [word, repl] : [word, "", repl]]) { return nil }
        let key = first.lowercased()
        var ls = lines(k).filter { self.key($0) != key }
        ls.append(k == .pronounce ? "\(first) = \(repl)" : "\(first) =  = \(repl)")
        write(k, ls)
        return nil
    }

    static func remove(_ k: LexKind, _ key: String) {
        if viaEngine(k, "/delete", ["key": key]) { return }
        write(k, lines(k).filter { self.key($0) != key })
    }
}

@MainActor
final class LexiconModel: ObservableObject {
    @Published var kind: LexKind = .pronounce
    @Published var entries: [LexEntry] = []
    @Published var word = ""
    @Published var replacement = ""
    @Published var message = ""
    private var sound: NSSound?

    init() { reload() }

    func reload() { entries = Lexicon.list(kind) }

    func add() {
        if let err = Lexicon.add(kind, word, replacement) { message = err; return }
        message = kind == .pronounce ? "ذخیره شد؛ از جمله‌های بعدی این‌طور گفته می‌شود."
            : "ذخیره شد؛ از جمله‌های بعدی این‌طور ترجمه می‌شود."
        word = ""; replacement = ""
        reload()
    }

    func remove(_ e: LexEntry) { Lexicon.remove(kind, e.key); reload() }

    /// Hear it in the dub voice before saving (needs the engine).
    func preview() {
        let text = replacement.isEmpty ? word : replacement
        guard !text.trimmingCharacters(in: .whitespaces).isEmpty else { message = "اول بنویسید چطور گفته شود."; return }
        message = "در حال ساختن صدا…"
        Task {
            // a fix in phonemes (Latin) is for the ParSiGo voices: heard exactly as it will be said
            let isPhonemes = text.range(of: "^[A-Za-z? ]+$", options: .regularExpression) != nil
            let body: [String: Any] = isPhonemes ? ["text": "", "phonemes": text, "voice": "female_narration"]
                                                 : ["text": text, "voice": "omni_female"]
            let data = await Task.detached {
                http(engineURL + "/api/tts", method: "POST", body: body, timeout: 120)
            }.value
            guard let data, let s = NSSound(data: data) else {
                message = "موتور خاموش است یا صدا ساخته نشد؛ موتور را روشن کنید."
                return
            }
            sound = s
            s.play()
            message = ""
        }
    }
}

struct LexiconView: View {
    @StateObject private var model = LexiconModel()

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            Picker("", selection: $model.kind) {
                ForEach(LexKind.allCases) { Text($0.title).tag($0) }
            }
            .pickerStyle(.segmented)
            .labelsHidden()
            .onChange(of: model.kind) { _, _ in model.message = ""; model.reload() }

            Text(model.kind == .pronounce
                 ? "کلمه‌ای که اشتباه گفته می‌شود و این‌که چطور گفته شود. صدای اُمنی اعراب را اغلب نادیده می‌گیرد؛ با صدای ParSiGo (بانو · روایت، آقا · خبری) آوانگار لاتین بنویسید (nimkore؛ A=آ، S=ش، C=چ، x=خ) تا دقیقاً همان گفته شود."
                 : "عبارت انگلیسی‌ای که اشتباه ترجمه می‌شود، و ترجمه‌ی درستش. اگر کل جمله همین عبارت باشد، همین ترجمه نشان داده می‌شود.")
                .font(.caption).foregroundStyle(.secondary).fixedSize(horizontal: false, vertical: true)

            HStack {
                TextField(model.kind == .pronounce ? "کلمه" : "عبارت انگلیسی", text: $model.word)
                    .environment(\.layoutDirection, model.kind == .idioms ? .leftToRight : .rightToLeft)
                TextField(model.kind == .pronounce ? "چطور گفته شود" : "ترجمه‌ی فارسی", text: $model.replacement)
            }
            .textFieldStyle(.roundedBorder)
            HStack {
                if model.kind == .pronounce {
                    Button("🔊 امتحان") { model.preview() }
                }
                Spacer()
                Button("افزودن") { model.add() }.keyboardShortcut(.defaultAction)
            }
            if !model.message.isEmpty {
                Text(model.message).font(.callout).foregroundStyle(.secondary)
            }

            Divider()
            if model.entries.isEmpty {
                Text("هنوز چیزی اضافه نشده.").font(.callout).foregroundStyle(.secondary)
                Spacer()
            } else {
                List {
                    ForEach(model.entries) { e in
                        HStack {
                            Text(e.shown).lineLimit(1)
                            Text("←").foregroundStyle(.secondary)
                            Text(e.replacement).lineLimit(1)
                            Spacer()
                            Button("حذف") { model.remove(e) }.buttonStyle(.borderless).foregroundStyle(.red)
                        }
                    }
                }
                .listStyle(.inset)
            }
        }
        .padding(16)
        .frame(minWidth: 420, minHeight: 380)
        .environment(\.layoutDirection, .rightToLeft)
        .onAppear { model.reload() }
    }
}

// ------------------------------------------------------------------ UI
struct Panel: View {
    @EnvironmentObject var engine: EngineState
    @Environment(\.openWindow) private var openWindow

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            HStack(spacing: 8) {
                Circle().fill(engine.statusColor).frame(width: 9, height: 9)
                Text("هم‌زبان").font(.headline)
                Spacer()
                Text(engine.statusText).font(.callout).foregroundStyle(.secondary)
            }

            Toggle(isOn: Binding(get: { engine.snap.serviceLoaded }, set: { engine.setEngine($0) })) {
                VStack(alignment: .leading, spacing: 2) {
                    Text("موتور ترجمه و دوبله")
                    Text("خاموش که باشد، رم و گرافیک برای مدل‌های دیگر آزاد است")
                        .font(.caption).foregroundStyle(.secondary)
                }
            }
            .toggleStyle(.switch)
            .disabled(engine.busy)

            Divider()

            VStack(alignment: .leading, spacing: 6) {
                Text("در حافظه").font(.caption).foregroundStyle(.secondary)
                if engine.snap.engineMB > 0 {
                    row("موتور (گفتار و صدا)", String(format: "%.1f GB", Double(engine.snap.engineMB) / 1024))
                }
                ForEach(engine.snap.models) { m in
                    row(m.ours ? "\(m.id) · هم‌زبان" : m.id, String(format: "%.1f GB", m.gigabytes))
                }
                if engine.snap.engineMB == 0 && engine.snap.models.isEmpty {
                    Text("هیچ مدلی بار نشده است").font(.callout).foregroundStyle(.secondary)
                }
                if engine.snap.models.contains(where: { $0.ours }) {
                    Button("فقط مدل ترجمه را از حافظه بیرون کن") { engine.freeModels() }
                        .buttonStyle(.link).disabled(engine.busy)
                }
            }

            Divider()

            Toggle("موتور با روشن شدن مک خودکار روشن شود",
                   isOn: Binding(get: { engine.snap.autoStart }, set: { engine.setAutoStart($0) }))
                .toggleStyle(.checkbox)
            Toggle("این برنامه هم با روشن شدن مک باز شود",
                   isOn: Binding(get: { engine.launchAtLogin }, set: { engine.setLaunchAtLogin($0) }))
                .toggleStyle(.checkbox)

            Divider()

            Button("واژه‌ها و تلفظ‌ها (درست کردن تلفظ و ترجمه‌ی اصطلاح)…") {
                openWindow(id: "lexicon")
                NSApp.activate(ignoringOtherApps: true)
            }
            .buttonStyle(.link)

            HStack {
                Button("واژه‌نامه") { open(appHome + "/glossary.txt") }
                Button("گزارش‌ها") { open(appHome + "/logs/engine.log") }
                Spacer()
                Button("خروج") { NSApp.terminate(nil) }
            }
            .buttonStyle(.borderless)
        }
        .padding(14)
        .frame(width: 330)
        .environment(\.layoutDirection, .rightToLeft)
        .onAppear { engine.refresh() }
    }

    private func row(_ name: String, _ size: String) -> some View {
        HStack {
            Text(name).lineLimit(1)
            Spacer()
            Text(size).monospacedDigit().foregroundStyle(.secondary)
        }
        .font(.callout)
    }

    private func open(_ path: String) {
        NSWorkspace.shared.open(URL(fileURLWithPath: path))
    }
}

struct HamzabanApp: App {
    @StateObject private var engine = EngineState()

    var body: some Scene {
        MenuBarExtra {
            Panel().environmentObject(engine)
        } label: {
            Image(systemName: engine.snap.healthy ? "waveform.circle.fill" : "waveform.circle")
        }
        .menuBarExtraStyle(.window)

        Window("واژه‌ها و تلفظ‌ها — هم‌زبان", id: "lexicon") {
            LexiconView()
        }
        .windowResizability(.contentSize)
    }
}

// ------------------------------------------------------------------ entry
@main
enum Entry {
    static func main() {
        let args = CommandLine.arguments
        if args.contains("--status") || args.contains("--on") || args.contains("--off") {
            if args.contains("--on") { Control.turnOn(autoStart: Snapshot.take().autoStart); sleep(2) }
            if args.contains("--off") { Control.turnOff(); sleep(1) }
            let s = Snapshot.take()
            print("service: \(s.serviceLoaded ? "loaded" : "stopped"), api: \(s.healthy ? "up" : "down"), "
                  + "autostart: \(s.autoStart), engine: \(s.engineMB) MB")
            for m in s.models { print(String(format: "  %@  %.1f GB%@", m.id, m.gigabytes, m.ours ? "  (hamzaban)" : "")) }
            return
        }
        HamzabanApp.main()
    }
}
