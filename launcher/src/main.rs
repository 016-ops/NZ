//! Tauri 原生窗口负责单实例、受控 Python 子服务及退出握手；账号和游戏凭据只在 Python 内存。
//! 页面不获得 Rust 命令权限，不能执行程序或选择服务地址；关闭窗口先停止游戏并清理再退出。
#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

use serde::Deserialize;
use std::{
    io::{BufRead, BufReader, Read, Write},
    path::PathBuf,
    process::{ChildStdin, Command, Stdio},
    sync::{atomic::{AtomicBool, Ordering}, mpsc, Arc, Mutex},
    thread,
    time::Duration,
};
use std::os::windows::process::CommandExt;
use tauri::{Manager, WebviewUrl, WebviewWindowBuilder};

/// 仅保存子服务控制管道和退出状态；从不保存用户邮箱、密码或业务会话。
#[derive(Default)]
struct DesktopBridge {
    input: Mutex<Option<ChildStdin>>,
    close_requested: AtomicBool,
    finished: AtomicBool,
}

impl DesktopBridge {
    /// 发送固定关闭命令；服务未启动时记录意图，启动后立即交付，防止关闭与启动竞态。
    fn close(&self) {
        self.close_requested.store(true, Ordering::SeqCst);
        if let Ok(mut input) = self.input.lock() {
            if let Some(pipe) = input.as_mut() {
                let _ = pipe.write_all(b"close\n");
                let _ = pipe.flush();
            }
        }
    }
}

/// 私有父子管道的首次应答，仅允许本构建支持的版本和固定回环入口。
#[derive(Deserialize)]
struct Bootstrap { version: String, url: String }

/// 只在本程序所在目录的有限祖先寻找完整仓库，不使用工作目录或任意命令行指定程序。
fn repository() -> Result<PathBuf, &'static str> {
    let executable = std::env::current_exe().map_err(|_| "EXECUTABLE_PATH_UNAVAILABLE")?;
    for root in executable.ancestors().skip(1).take(5) {
        if root.join("tools/native_launcher.py").is_file()
            && root.join(".venv/Scripts/python.exe").is_file()
            && root.join("admin/dist/index.html").is_file() {
            return root.canonicalize().map_err(|_| "WORKSPACE_PATH_UNAVAILABLE");
        }
    }
    Err("LAUNCHER_WORKSPACE_MISSING")
}

/// 发行目录只启动随包本机服务；没有发行标记时才沿用原仓库虚拟环境入口。
/// 不从PATH、当前工作目录或网页参数寻找Python，避免便携包依赖开发机安装。
struct ServiceLaunch {
    executable: PathBuf,
    arguments: Vec<&'static str>,
    directory: PathBuf,
}

fn service_launch() -> Result<ServiceLaunch, &'static str> {
    let executable = std::env::current_exe().map_err(|_| "EXECUTABLE_PATH_UNAVAILABLE")?;
    let folder = executable.parent().ok_or("EXECUTABLE_PATH_UNAVAILABLE")?;
    if folder.join("launcher-config.json").is_file() || folder.join("service").is_dir() {
        let service = folder.join("service/NZLauncherService.exe");
        if !service.is_file() || !folder.join("launcher-config.json").is_file()
            || !folder.join("service/_internal/admin/dist/index.html").is_file() {
            return Err("LAUNCHER_PACKAGE_INCOMPLETE");
        }
        return Ok(ServiceLaunch { executable: service, arguments: vec!["--desktop"],
            directory: folder.to_path_buf() });
    }
    let root = repository()?;
    Ok(ServiceLaunch { executable: root.join(".venv/Scripts/python.exe"),
        arguments: vec!["-u", "-m", "tools.native_launcher", "--desktop"], directory: root })
}

/// 校验 URL 的全部组成部分，不允许子服务应答转向外网或把能力放进查询字符串。
fn bootstrap_url(line: &str) -> Result<tauri::Url, &'static str> {
    let message: Bootstrap = serde_json::from_str(line).map_err(|_| "BOOTSTRAP_INVALID")?;
    let url = tauri::Url::parse(&message.url).map_err(|_| "BOOTSTRAP_INVALID")?;
    let access = url.fragment().and_then(|value| value.strip_prefix("access=")).unwrap_or("");
    if message.version != "cn2020-launcher-v1" || url.scheme() != "http"
        || url.host_str() != Some("127.0.0.1") || url.port() != Some(18769)
        || url.path() != "/launcher" || url.query().is_some() || !url.username().is_empty()
        || url.password().is_some() || access.len() != 43
        || !access.bytes().all(|value| value.is_ascii_alphanumeric() || value == b'_' || value == b'-') {
        return Err("BOOTSTRAP_INVALID");
    }
    Ok(url)
}

/// 本窗口只承载内置启动页和固定本机 Launcher；导航不能打开第三方站点或其他本机页面。
fn allowed_navigation(url: &tauri::Url) -> bool {
    ((url.scheme() == "tauri" && url.host_str() == Some("localhost"))
        || (url.scheme() == "http" && url.host_str() == Some("tauri.localhost")))
        || (url.scheme() == "http" && url.host_str() == Some("127.0.0.1")
            && url.port() == Some(18769) && url.path() == "/launcher"
            && url.query().is_none() && url.username().is_empty() && url.password().is_none())
}

/// 错误页只显示固定代码；无日志输出原始应答、命令参数、路径或凭据。
fn show_failure(app: &tauri::AppHandle, bridge: &DesktopBridge, code: &str) {
    bridge.finished.store(true, Ordering::SeqCst);
    if let Some(window) = app.get_webview_window("main") {
        let safe = serde_json::to_string(code).unwrap_or_else(|_| "\"LAUNCHER_FAILED\"".into());
        let _ = window.eval(&format!("document.getElementById('status').textContent='启动器未能完成准备。请保留下面的错误码。';document.getElementById('error').textContent={safe}"));
    }
}

/// 启动唯一受控子服务；使用无控制台进程和继承管道，父进程崩溃时 EOF 也触发服务清理。
fn run_service(app: tauri::AppHandle, bridge: Arc<DesktopBridge>) -> Result<(), &'static str> {
    let launch = service_launch()?;
    let mut child = Command::new(&launch.executable)
        .args(&launch.arguments)
        .current_dir(&launch.directory).stdin(Stdio::piped()).stdout(Stdio::piped()).stderr(Stdio::null())
        .creation_flags(0x08000000) // CREATE_NO_WINDOW：仅显示 Tauri 及用户启动的游戏窗口。
        .spawn().map_err(|_| "LOCAL_SERVICE_START_FAILED")?;
    *bridge.input.lock().map_err(|_| "CONTROL_PIPE_FAILED")? = child.stdin.take();
    if bridge.close_requested.load(Ordering::SeqCst) { bridge.close(); }
    let output = child.stdout.take().ok_or("CONTROL_PIPE_FAILED")?;
    let (sender, receiver) = mpsc::channel();
    thread::spawn(move || {
        // 首行有界读取，异常服务输出不会无限占用内存；后续输出仅排空而不保存。
        let mut reader = BufReader::new(output);
        let mut line = String::new();
        let result = (&mut reader).take(2048).read_line(&mut line);
        let _ = sender.send(result.map(|_| line));
        let _ = std::io::copy(&mut reader, &mut std::io::sink());
    });
    let destination = receiver.recv_timeout(Duration::from_secs(30))
        .map_err(|_| "LOCAL_SERVICE_TIMEOUT")
        .and_then(|line| line.map_err(|_| "CONTROL_PIPE_FAILED"))
        .and_then(|line| bootstrap_url(&line));
    match destination {
        Ok(url) => {
            if let Some(window) = app.get_webview_window("main") {
                if window.navigate(url).is_err() { bridge.close(); }
            }
        }
        Err(code) => {
            // 未完成准备时也请求正常清理；释放 stdin 使故障进程观察 EOF，不终止其他进程。
            bridge.close();
            bridge.input.lock().map_err(|_| "CONTROL_PIPE_FAILED")?.take();
            let _ = child.wait();
            return Err(code);
        }
    }
    let status = child.wait().map_err(|_| "LOCAL_SERVICE_WAIT_FAILED")?;
    bridge.finished.store(true, Ordering::SeqCst);
    if !status.success() {
        // 运行中失败时载入内置页，确保不会因 React 页面缺少错误元素而静默丢失诊断。
        if let Some(window) = app.get_webview_window("main") {
            let _ = window.eval("document.body.innerHTML='<main style=\"padding:48px;font-family:sans-serif\"><h1>启动器服务已停止</h1><p>请查看当前用户 AppData/NZRevivalLauncher/runtime 或研究仓库 runtime 中的错误记录，然后重新打开 Launcher。</p></main>'");
        }
        return Ok(());
    }
    app.exit(0);
    Ok(())
}

/// 注册单实例后创建唯一桌面窗口；重复运行仅聚焦既有窗口，不重新登录或启动游戏。
fn main() {
    let bridge = Arc::new(DesktopBridge::default());
    let setup_bridge = bridge.clone();
    let event_bridge = bridge.clone();
    tauri::Builder::default()
        .plugin(tauri_plugin_single_instance::init(|app, _, _| {
            if let Some(window) = app.get_webview_window("main") {
                let _ = window.show(); let _ = window.unminimize(); let _ = window.set_focus();
            }
        }))
        .setup(move |app| {
            WebviewWindowBuilder::new(app, "main", WebviewUrl::App("index.html".into()))
                .title("逆战 Launcher").inner_size(1080.0, 820.0).min_inner_size(800.0, 650.0)
                .incognito(true).devtools(false).on_navigation(allowed_navigation).build()?;
            let handle = app.handle().clone();
            thread::spawn(move || {
                if let Err(code) = run_service(handle.clone(), setup_bridge.clone()) {
                    show_failure(&handle, &setup_bridge, code);
                }
            });
            Ok(())
        })
        .on_window_event(move |window, event| {
            if let tauri::WindowEvent::CloseRequested { api, .. } = event {
                if !event_bridge.finished.load(Ordering::SeqCst) {
                    api.prevent_close();
                    let _ = window.set_title("逆战 Launcher · 正在清理本次会话");
                    event_bridge.close();
                }
            }
        })
        .run(tauri::generate_context!())
        .expect("TAURI_RUNTIME_FAILED");
}

#[cfg(test)]
mod tests {
    use super::*;
    /// 地址/能力边界：拒绝外网、用户信息、错误路径、错误版本及非随机格式，防止启动应答注入。
    #[test]
    fn rejects_untrusted_bootstrap() {
        let token = "a".repeat(43);
        let valid = format!(r#"{{"version":"cn2020-launcher-v1","url":"http://127.0.0.1:18769/launcher#access={token}"}}"#);
        assert!(bootstrap_url(&valid).is_ok());
        for invalid in [valid.replace("127.0.0.1", "example.com"), valid.replace("18769", "80"),
            valid.replace("/launcher#", "/other#"), valid.replace("v1", "v2"),
            valid.replace("access=", "password="), valid.replace("http://", "http://user@"),
            valid.replace("#access", "?x=y#access")] {
            assert!(bootstrap_url(&invalid).is_err());
        }
        assert!(!allowed_navigation(&tauri::Url::parse("https://example.com").unwrap()));
        assert!(!allowed_navigation(&tauri::Url::parse("http://127.0.0.1:18769/admin").unwrap()));
    }
}
