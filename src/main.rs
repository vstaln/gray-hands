//! gray-hands: Android control via mobilerun Portal + Jev (TypeSafe System One).
//! Protocol v1.1. Tools: device_status, device_ui, device_tap, device_tap_element,
//! device_type, device_swipe, device_press, device_launch, device_apps,
//! device_screenshot, device_run (autonomous Jev action-selection loop).
//! Command: /device <subcommand>.

use std::collections::hash_map::DefaultHasher;
use std::hash::{Hash, Hasher};
use std::io::Write;

use regex::Regex;
use serde_json::{Value, json};

#[derive(serde::Deserialize)]
struct Request {
    id: Value,
    method: String,
    #[serde(default)]
    params: Option<Value>,
}

#[derive(Debug)]
struct El {
    i: i64,
    cls: String,
    label: String,
    x1: i64,
    y1: i64,
    x2: i64,
    y2: i64,
}

impl El {
    fn cx(&self) -> i64 { (self.x1 + self.x2) / 2 }
    fn cy(&self) -> i64 { (self.y1 + self.y2) / 2 }
}

// ---------- mobilerun device shell ----------

/// Runs `mobilerun device <args>`; returns combined stdout+stderr on success.
fn mr(args: &[&str]) -> Result<String, String> {
    let out = std::process::Command::new("mobilerun")
        .arg("device")
        .args(args)
        .output()
        .map_err(|e| format!("mobilerun not runnable: {e}"))?;
    let text = format!(
        "{}{}",
        String::from_utf8_lossy(&out.stdout),
        String::from_utf8_lossy(&out.stderr)
    );
    if out.status.success() { Ok(text) } else { Err(format!("mobilerun {:?}: {}", args, text.trim().chars().take(300).collect::<String>())) }
}

fn parse_ui(text: &str) -> (String, Vec<El>) {
    let app = Regex::new(r"App:\*\*\s*(.+)")
        .unwrap()
        .captures(text)
        .map(|c| c[1].trim().to_string())
        .unwrap_or_else(|| "?".into());
    let el_re = Regex::new(r"(?m)^\s*(\d+)\.\s+([A-Za-z]+):\s+(.*?)\s*-\s*\((\d+),(\d+),(\d+),(\d+)\)\s*$").unwrap();
    let q_re = Regex::new(r#""([^"]+)""#).unwrap();
    let mut elements = vec![];
    for c in el_re.captures_iter(text) {
        let labels: Vec<String> = q_re.captures_iter(&c[3]).map(|q| q[1].to_string()).collect();
        let label = labels
            .iter()
            .find(|l| !l.contains(":id/"))
            .or_else(|| labels.first())
            .cloned()
            .unwrap_or_else(|| c[2].to_string());
        elements.push(El {
            i: c[1].parse().unwrap_or(0),
            cls: c[2].to_string(),
            label,
            x1: c[4].parse().unwrap_or(0),
            y1: c[5].parse().unwrap_or(0),
            x2: c[6].parse().unwrap_or(0),
            y2: c[7].parse().unwrap_or(0),
        });
    }
    (app, elements)
}

fn ui_hash(elements: &[El]) -> u64 {
    let mut h = DefaultHasher::new();
    for e in elements { e.label.hash(&mut h); }
    h.finish()
}

fn screen_size() -> (i64, i64) {
    let out = std::process::Command::new("adb")
        .args(["shell", "wm", "size"])
        .output();
    if let Ok(o) = out {
        let s = String::from_utf8_lossy(&o.stdout);
        if let Some(c) = Regex::new(r"(\d+)x(\d+)").unwrap().captures(&s) {
            return (c[1].parse().unwrap_or(1080), c[2].parse().unwrap_or(2412));
        }
    }
    (1080, 2412)
}

fn ui_dump() -> Result<(String, Vec<El>), String> {
    let text = mr(&["ui"])?;
    Ok(parse_ui(&text))
}

/// Installed apps as (package, display name) pairs.
fn installed_apps() -> Vec<(String, String)> {
    let Ok(text) = mr(&["apps"]) else { return vec![] };
    let re = Regex::new(r"(?m)^(\S+)\s+\((.+)\)\s*$").unwrap();
    re.captures_iter(&text)
        .map(|c| (c[1].to_string(), c[2].to_string()))
        .collect()
}

// ---------- Jev (TypeSafe System One) ----------

fn typesafe_key() -> Option<String> {
    if let Ok(k) = std::env::var("TYPESAFE_API_KEY") { if !k.is_empty() { return Some(k); } }
    let home = std::env::var("HOME").ok()?;
    let raw = std::fs::read_to_string(format!("{home}/bench/.typesafe_key")).ok()?;
    raw.lines()
        .map(str::trim)
        .find(|l| !l.is_empty() && !l.starts_with('#') && !l.starts_with("PASTE_"))
        .map(String::from)
}

async fn jev(http: &reqwest::Client, state: &Value, questions: &Value) -> Result<Value, String> {
    let key = typesafe_key().ok_or("no TYPESAFE_API_KEY and ~/bench/.typesafe_key unreadable")?;
    let body = json!({"state": state, "model": "jev-latest", "questions": questions});
    for attempt in 1..=5u32 {
        let res = http
            .post("https://api.typesafe.ai/v1/systemone")
            .bearer_auth(&key)
            .json(&body)
            .send()
            .await;
        match res {
            Ok(r) if r.status().is_success() => {
                let v: Value = r.json().await.map_err(|e| format!("jev json: {e}"))?;
                return Ok(v.get("answers").cloned().unwrap_or(json!({})));
            }
            Ok(r) => {
                let st = r.status().as_u16();
                if [429, 500, 502, 503, 504, 529].contains(&st) && attempt < 5 {
                    tokio::time::sleep(std::time::Duration::from_secs_f64((0.5 * 2f64.powi(attempt as i32)).min(15.0))).await;
                    continue;
                }
                let t = r.text().await.unwrap_or_default();
                return Err(format!("jev HTTP {st}: {}", t.chars().take(200).collect::<String>()));
            }
            Err(e) => {
                if attempt < 5 {
                    tokio::time::sleep(std::time::Duration::from_secs_f64((0.5 * 2f64.powi(attempt as i32)).min(15.0))).await;
                    continue;
                }
                return Err(format!("jev fetch: {e}"));
            }
        }
    }
    Err("jev: unreachable".into())
}

// ---------- autonomous loop ----------

/// True when the task text names this installed app (display name or package tail).
fn name_matches(task_lc: &str, pkg: &str, name: &str) -> bool {
    let norm = |s: &str| s.chars().map(|c| if c.is_alphanumeric() { c.to_ascii_lowercase() } else { ' ' }).collect::<String>();
    let task_n = format!(" {} ", norm(task_lc));
    let n = norm(name);
    let n = n.trim();
    if n.len() >= 4 && task_n.contains(&format!(" {n} ")) { return true; }
    if let Some(tail) = pkg.rsplit('.').next() {
        if tail.len() >= 4 && task_n.contains(&format!(" {tail} ")) { return true; }
    }
    false
}

fn task_words(task: &str) -> Vec<String> {
    task.split(|c: char| !c.is_alphanumeric()).filter(|w| w.len() > 3).map(String::from).collect()
}

/// Second Jev call: choose the text to type from candidates derived from the task
/// (Jev has no free-text primitive — select instead of generate).
async fn pick_text(http: &reqwest::Client, task: &str) -> Option<String> {
    let mut cand: Vec<String> = vec![];
    let mut seen = std::collections::HashSet::new();
    let mut push = |s: String| {
        if s.len() >= 3 && seen.insert(s.to_lowercase()) { cand.push(s); }
    };
    let words = task_words(task);
    for w in words.windows(2).rev() { push(w.join(" ")); }
    for w in words.iter().rev() { push(w.clone()); }
    cand.truncate(8);
    if cand.is_empty() { return None; }
    let mut criteria = serde_json::Map::new();
    criteria.insert("abort".into(), json!("none of these is the right text to type"));
    for (i, c) in cand.iter().enumerate() { criteria.insert(format!("s{i}"), json!(c)); }
    let q = json!({"text": {"type": "choice",
        "instructions": {"question": "The agent needs to type into a focused field (e.g. a search box) to accomplish the task. Which candidate string should be typed?"},
        "criteria": criteria}});
    let ans = jev(http, &json!({"task": task}), &q).await.ok()?;
    let i = ans["text"]["choice"].as_str()?.strip_prefix('s')?.parse::<usize>().ok()?;
    cand.get(i).cloned()
}

/// Second Jev call: which installed app to launch for this task.
async fn pick_app(http: &reqwest::Client, task: &str, apps: &[(String, String)]) -> Option<String> {
    if apps.is_empty() { return None; }
    let mut criteria = serde_json::Map::new();
    criteria.insert("none".into(), json!("no installed app helps"));
    for (p, n) in apps.iter().take(60) { criteria.insert(p.clone(), json!(format!("{n} ({p})"))); }
    let q = json!({"app": {"type": "choice",
        "instructions": {"question": "Which installed app should be opened to accomplish this task?"},
        "criteria": criteria}});
    let ans = jev(http, &json!({"task": task}), &q).await.ok()?;
    let c = ans["app"]["choice"].as_str()?;
    if c.is_empty() || c == "none" { None } else { Some(c.to_string()) }
}

async fn device_run(http: &reqwest::Client, task: &str, max_steps: usize) -> Value {
    let type_text = Regex::new(r#"["']([^"']+)["']"#)
        .unwrap()
        .captures(task)
        .map(|c| c[1].to_string());
    let actions = json!({
        "tap": "Tap the element chosen in `target`",
        "type": format!("Type text into the focused/selected field{}", type_text.as_ref().map(|t| format!(" — the text is \"{t}\"")).unwrap_or_default()),
        "swipe_up": "Scroll up to reveal elements below",
        "swipe_down": "Scroll down to reveal elements above",
        "launch": "Launch an installed app — fastest way to open an app that is not on screen",
        "back": "Press the Android back button",
        "home": "Go to the home screen",
        "wait": "Wait ~2s for the screen to load or settle, then re-evaluate",
        "done": "The task is already accomplished on the current screen — stop",
        "stuck": "No available action makes progress — stop and escalate",
    });
    let mut history: Vec<String> = vec![];
    let mut transcript: Vec<String> = vec![];
    let (w, h) = screen_size();
    let mut last_hash = 0u64;
    let mut same_count = 0u32;
    let task_lc = task.to_lowercase();
    let mut apps = installed_apps();
    apps.sort_by_key(|(p, n)| !name_matches(&task_lc, p, n));
    apps.truncate(120);

    // Fast path: the task names an installed app — launch it before asking Jev anything.
    if let Some((p, n)) = apps.iter().find(|(p, n)| name_matches(&task_lc, p, n)) {
        transcript.push(format!("pre: launching {n} ({p})"));
        history.push(format!("launch {n}"));
        let _ = mr(&["start", p]);
        tokio::time::sleep(std::time::Duration::from_millis(1200)).await;
    }

    for step in 1..=max_steps {
        let t0 = std::time::Instant::now();
        let (app, mut elements) = match ui_dump() {
            Ok(v) => v,
            Err(e) => { transcript.push(format!("step {step}: ui dump failed: {e}")); break; }
        };
        elements.dedup_by(|a, b| a.label == b.label && a.x1 == b.x1 && a.y1 == b.y1 && a.x2 == b.x2 && a.y2 == b.y2);
        if elements.is_empty() { transcript.push(format!("step {step}: empty UI tree")); break; }
        let hash = ui_hash(&elements);
        same_count = if hash == last_hash { same_count + 1 } else { 0 };
        last_hash = hash;
        if same_count >= 2 {
            transcript.push(format!("step {step}: screen unchanged {}x — stopping", same_count + 1));
            break;
        }

        let mut target = serde_json::Map::new();
        target.insert("none".into(), json!("no listed element helps"));
        for e in elements.iter().take(40) {
            target.insert(format!("el{}", e.i), json!(format!("{} \"{}\"", e.cls, e.label.chars().take(60).collect::<String>())));
        }
        let state = json!({
            "task": task,
            "current_app": app,
            "clickable_elements": elements.iter().take(40).map(|e| format!("{}. {} \"{}\"", e.i, e.cls, e.label.chars().take(60).collect::<String>())).collect::<Vec<_>>(),
            "action_history": history.iter().rev().take(8).rev().collect::<Vec<_>>(),
        });
        let questions = json!({
            "action": {"type": "choice", "instructions": {"question": "Given the task, current app, visible clickable elements and what already happened: what single action makes the most progress right now? Pick done only if the task's goal is already achieved on screen."}, "criteria": actions},
            "target": {"type": "choice", "instructions": {"question": "If the next action is tap or type, which element should receive it? Answer none if the action needs no element."}, "criteria": target},
        });
        let answers = match jev(http, &state, &questions).await {
            Ok(a) => a,
            Err(e) => { transcript.push(format!("step {step}: jev failed: {e}")); break; }
        };
        let action = answers["action"]["choice"].as_str().unwrap_or("stuck").to_string();
        let conf = answers["action"]["confidence"].as_f64();
        let ti = answers["target"]["choice"].as_str().unwrap_or("").strip_prefix("el").and_then(|s| s.parse::<i64>().ok());
        let el = ti.and_then(|i| elements.iter().find(|e| e.i == i));
        transcript.push(format!(
            "step {step}: app={app} action={action}{}{} ({:.0}ms)",
            el.map(|e| format!(" -> [{}] \"{}\" ({},{})", e.i, e.label, e.cx(), e.cy())).unwrap_or_default(),
            conf.map(|c| format!(" conf={c:.2}")).unwrap_or_default(),
            t0.elapsed().as_millis()
        ));

        if action == "done" || action == "stuck" { break; }
        history.push(format!("{action}{}", el.map(|e| format!(" \"{}\"", e.label)).unwrap_or_default()));
        let r = match (action.as_str(), el) {
            ("tap", Some(e)) => mr(&["tap", &e.cx().to_string(), &e.cy().to_string()]),
            ("type", _) => {
                let text = match &type_text { Some(t) => Some(t.clone()), None => pick_text(http, task).await };
                match (text, el) {
                    (Some(t), Some(e)) => mr(&["tap", &e.cx().to_string(), &e.cy().to_string()]).and_then(|_| mr(&["type", &t])),
                    (Some(t), None) => mr(&["type", &t]),
                    (None, _) => Err("type chosen but no text could be determined".into()),
                }
            }
            ("swipe_up", _) => mr(&["swipe", &(w / 2).to_string(), &(h * 4 / 5).to_string(), &(w / 2).to_string(), &(h / 3).to_string()]),
            ("swipe_down", _) => mr(&["swipe", &(w / 2).to_string(), &(h / 3).to_string(), &(w / 2).to_string(), &(h * 4 / 5).to_string()]),
            ("back" | "home" | "enter", _) => mr(&["press", &action]),
            ("launch", _) => {
                let pkg = apps.iter().find(|(p, n)| name_matches(&task_lc, p, n)).map(|(p, _)| p.clone());
                let pkg = match pkg { Some(p) => Some(p), None => pick_app(http, task, &apps).await };
                match pkg { Some(p) => mr(&["start", &p]), None => Err("launch chosen but no app selected".into()) }
            }
            ("wait", _) => Ok(String::new()),
            _ => { transcript.push(format!("   no-op: {action}")); continue; }
        };
        if let Err(e) = r { transcript.push(format!("   exec failed: {e}")); }
        tokio::time::sleep(std::time::Duration::from_millis(700)).await;
    }
    json!({
        "task": task,
        "steps_taken": history.len(),
        "transcript": transcript,
        "history": history,
    })
}

// ---------- tool handlers ----------

fn str_arg<'a>(args: &'a Value, k: &str) -> Option<&'a str> { args.get(k).and_then(Value::as_str) }
fn ok(content: String) -> Value { json!({"content": content.chars().take(8000).collect::<String>()}) }
fn err(e: impl std::fmt::Display) -> Value { json!({"content": format!("error: {e}"), "is_error": true}) }

async fn handle_tool(http: &reqwest::Client, name: &str, args: &Value) -> Value {
    match name {
        "device_ui" => match ui_dump() {
            Ok((app, els)) => ok(format!("app: {app}\n{}", els.iter().map(|e| format!("{}. {} \"{}\" ({},{})", e.i, e.cls, e.label, e.cx(), e.cy())).collect::<Vec<_>>().join("\n"))),
            Err(e) => err(e),
        },
        "device_status" => match ui_dump() {
            Ok((app, els)) => ok(format!("app: {app}, {} clickable elements", els.len())),
            Err(e) => err(e),
        },
        "device_tap" => {
            let (x, y) = (args["x"].as_i64().unwrap_or(-1), args["y"].as_i64().unwrap_or(-1));
            if x < 0 || y < 0 { return err("x and y required"); }
            match mr(&["tap", &x.to_string(), &y.to_string()]) { Ok(t) => ok(t.trim().to_string()), Err(e) => err(e) }
        }
        "device_tap_element" => {
            let i = args["index"].as_i64().unwrap_or(-1);
            match ui_dump() {
                Ok((_, els)) => match els.iter().find(|e| e.i == i) {
                    Some(e) => match mr(&["tap", &e.cx().to_string(), &e.cy().to_string()]) {
                        Ok(t) => ok(format!("tapped [{}] \"{}\" — {}", e.i, e.label, t.trim())),
                        Err(e) => err(e),
                    },
                    None => err(format!("no element index {i}")),
                },
                Err(e) => err(e),
            }
        }
        "device_type" => {
            let text = str_arg(args, "text").unwrap_or("");
            if text.is_empty() { return err("text required"); }
            let focus = args["element_index"].as_i64();
            let r = (|| {
                if let Some(i) = focus {
                    let (_, els) = ui_dump()?;
                    let e = els.iter().find(|e| e.i == i).ok_or_else(|| format!("no element index {i}"))?;
                    mr(&["tap", &e.cx().to_string(), &e.cy().to_string()])?;
                }
                mr(&["type", text])
            })();
            match r { Ok(t) => ok(t.trim().to_string()), Err(e) => err(e) }
        }
        "device_swipe" => {
            let dir = str_arg(args, "direction").unwrap_or("up");
            let (w, h) = screen_size();
            let (x1, y1, x2, y2) = match dir {
                "down" => (w / 2, h / 3, w / 2, h * 4 / 5),
                "left" => (w * 4 / 5, h / 2, w / 5, h / 2),
                "right" => (w / 5, h / 2, w * 4 / 5, h / 2),
                _ => (w / 2, h * 4 / 5, w / 2, h / 3),
            };
            match mr(&["swipe", &x1.to_string(), &y1.to_string(), &x2.to_string(), &y2.to_string()]) {
                Ok(t) => ok(t.trim().to_string()), Err(e) => err(e),
            }
        }
        "device_press" => {
            let b = str_arg(args, "button").unwrap_or("back");
            if !["back", "home", "enter"].contains(&b) { return err("button must be back|home|enter"); }
            match mr(&["press", b]) { Ok(t) => ok(t.trim().to_string()), Err(e) => err(e) }
        }
        "device_launch" => {
            let pkg = str_arg(args, "package").unwrap_or("");
            if pkg.is_empty() { return err("package required"); }
            match mr(&["start", pkg]) { Ok(t) => ok(t.trim().to_string()), Err(e) => err(e) }
        }
        "device_apps" => match mr(&["apps"]) { Ok(t) => ok(t.trim().chars().take(6000).collect()), Err(e) => err(e) },
        "device_screenshot" => match mr(&["screenshot"]) { Ok(t) => ok(t.trim().to_string()), Err(e) => err(e) },
        "device_run" => {
            let task = str_arg(args, "task").unwrap_or("");
            if task.is_empty() { return err("task required"); }
            let max = args["max_steps"].as_u64().unwrap_or(15).min(30) as usize;
            let r = device_run(http, task, max).await;
            ok(serde_json::to_string_pretty(&r).unwrap_or_default())
        }
        _ => err(format!("unknown tool {name}")),
    }
}

fn manifest() -> Value {
    let strp = |d: &str| json!({"type": "string", "description": d});
    json!({
        "name": "hands", "version": "0.1.0", "protocol": "1.1",
        "tools": [
            {"name": "device_status", "description": "Current foreground app and clickable element count", "parameters": {"type": "object", "properties": {}}, "snippet": "device_status"},
            {"name": "device_ui", "description": "Dump the device UI tree: current app plus every clickable element's index, class, label and center coordinates", "parameters": {"type": "object", "properties": {}}, "snippet": "device_ui"},
            {"name": "device_tap", "description": "Tap screen coordinates", "parameters": {"type": "object", "properties": {"x": {"type": "integer"}, "y": {"type": "integer"}}, "required": ["x", "y"]}, "snippet": "device_tap <x> <y>"},
            {"name": "device_tap_element", "description": "Tap a UI element by its index from device_ui", "parameters": {"type": "object", "properties": {"index": {"type": "integer"}}, "required": ["index"]}, "snippet": "device_tap_element <index>"},
            {"name": "device_type", "description": "Type text into a field (optionally focus an element by index first)", "parameters": {"type": "object", "properties": {"text": strp("text to type"), "element_index": {"type": "integer"}}, "required": ["text"]}, "snippet": "device_type <text>"},
            {"name": "device_swipe", "description": "Swipe to scroll", "parameters": {"type": "object", "properties": {"direction": strp("up|down|left|right")}}, "snippet": "device_swipe <direction>"},
            {"name": "device_press", "description": "Press a system button", "parameters": {"type": "object", "properties": {"button": strp("back|home|enter")}, "required": ["button"]}, "snippet": "device_press <button>"},
            {"name": "device_launch", "description": "Launch an app by package name", "parameters": {"type": "object", "properties": {"package": strp("e.g. com.android.settings")}, "required": ["package"]}, "snippet": "device_launch <package>"},
            {"name": "device_apps", "description": "List installed apps", "parameters": {"type": "object", "properties": {}}, "snippet": "device_apps"},
            {"name": "device_screenshot", "description": "Take a screenshot; returns the saved file path", "parameters": {"type": "object", "properties": {}}, "snippet": "device_screenshot"},
            {"name": "device_run", "description": "Autonomously run a natural-language task on the phone: Jev (System One) picks each action from the real UI tree, code executes it. Fast (~2-3s/step).", "parameters": {"type": "object", "properties": {"task": strp("natural language task; quote any literal text to type"), "max_steps": {"type": "integer"}}, "required": ["task"]}, "snippet": "device_run <task>"},
        ],
        "commands": ["/hands"], "hooks": [],
    })
}

#[tokio::main(flavor = "multi_thread")]
async fn main() -> anyhow::Result<()> {
    let mut lines = tokio::io::AsyncBufReadExt::lines(tokio::io::BufReader::new(tokio::io::stdin()));
    let http = reqwest::Client::builder()
        .user_agent("gray-hands/0.1")
        .timeout(std::time::Duration::from_secs(30))
        .build()?;
    let mut stdout = std::io::stdout();
    while let Some(line) = lines.next_line().await? {
        if line.trim().is_empty() { continue; }
        let request: Request = match serde_json::from_str(&line) { Ok(r) => r, Err(_) => continue };
        if request.method == "plugin/shutdown" { break; }
        let params = request.params.clone().unwrap_or_else(|| json!({}));
        let reply: anyhow::Result<Value> = match request.method.as_str() {
            "plugin/manifest" => Ok(manifest()),
            "tool/call" => {
                let name = params.get("name").and_then(Value::as_str).unwrap_or("");
                let args = params.get("args").cloned().unwrap_or(json!({}));
                Ok(handle_tool(&http, name, &args).await)
            }
            "command/run" => {
                let argv: Vec<String> = params.get("argv").and_then(Value::as_array)
                    .map(|a| a.iter().filter_map(Value::as_str).map(String::from).collect()).unwrap_or_default();
                let (sub, rest) = argv.split_first().map(|(s, r)| (s.as_str(), r)).unwrap_or(("ui", &[][..]));
                let joined = rest.join(" ");
                let (tname, targs) = match sub {
                    "ui" => ("device_ui", json!({})),
                    "status" => ("device_status", json!({})),
                    "apps" => ("device_apps", json!({})),
                    "shot" | "screenshot" => ("device_screenshot", json!({})),
                    "tap" => ("device_tap", json!({"x": rest.first().and_then(|s| s.parse::<i64>().ok()), "y": rest.get(1).and_then(|s| s.parse::<i64>().ok())})),
                    "swipe" => ("device_swipe", json!({"direction": rest.first()})),
                    "press" => ("device_press", json!({"button": rest.first().map(String::as_str).unwrap_or("back")})),
                    "launch" | "start" => ("device_launch", json!({"package": joined})),
                    "type" => ("device_type", json!({"text": joined})),
                    "run" => ("device_run", json!({"task": joined})),
                    _ => ("", json!({})),
                };
                let out = if tname.is_empty() {
                    json!({"content": "usage: /hands [ui|status|apps|screenshot|tap x y|swipe dir|press btn|launch pkg|type text|run task]"})
                } else {
                    handle_tool(&http, tname, &targs).await
                };
                let text = out.get("content").and_then(Value::as_str).unwrap_or("").to_string();
                Ok(json!({"text": text}))
            }
            _ => Ok(json!({})),
        };
        let response = match reply {
            Ok(result) => json!({"id": request.id, "result": result}),
            Err(e) => json!({"id": request.id, "error": {"message": e.to_string()}}),
        };
        writeln!(stdout, "{}", response)?;
        stdout.flush()?;
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn parses_ui_dump() {
        let text = std::fs::read_to_string("testdata/ui-settings.txt").unwrap();
        let (app, els) = parse_ui(&text);
        assert!(app.contains("Settings"), "app was {app}");
        assert!(els.len() > 10, "only {} elements", els.len());
        assert!(els.iter().all(|e| e.x2 >= e.x1 && e.y2 >= e.y1));
        assert!(els.iter().any(|e| e.label == "Airplane mode"), "labels: {:?}", els.iter().map(|e| &e.label).collect::<Vec<_>>());
        assert_eq!(els.iter().find(|e| e.label == "Airplane mode").unwrap().cx(), 532);
    }

    #[test]
    fn matches_task_apps() {
        assert!(name_matches("open the settings app", "com.android.settings", "Settings"));
        assert!(name_matches("put on spotify", "com.spotify.music", "Spotify"));
        assert!(name_matches("show me music", "com.spotify.music", "Spotify")); // pkg tail
        assert!(!name_matches("check the weather", "com.spotify.music", "Spotify"));
        assert!(!name_matches("open app", "com.oppo.camera", "Camera"));
    }
}
