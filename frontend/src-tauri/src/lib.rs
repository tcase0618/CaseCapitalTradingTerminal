use std::{
    env,
    net::{SocketAddr, TcpStream},
    path::PathBuf,
    process::{Child, Command},
    sync::Mutex,
    thread,
    time::Duration,
};

use serde::Serialize;
use tauri::{Manager, State, WindowEvent};

struct BackendProcess(Mutex<Option<Child>>);

#[derive(Serialize)]
struct BackendBootResult {
    ok: bool,
    started: bool,
    already_running: bool,
    listening: bool,
    message: String,
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    tauri::Builder::default()
        .setup(|app| {
            let backend = boot_backend().child;
            app.manage(BackendProcess(Mutex::new(backend)));
            Ok(())
        })
        .invoke_handler(tauri::generate_handler![force_boot_backend])
        .on_window_event(|window, event| {
            if matches!(event, WindowEvent::Destroyed) {
                let backend = window.app_handle().state::<BackendProcess>();
                if let Some(mut child) = take_backend_child(&backend) {
                    let _ = child.kill();
                    let _ = child.wait();
                }
            }
        })
        .run(tauri::generate_context!())
        .expect("error while running CaseCapitalTradingTerminal");
}

#[tauri::command]
fn force_boot_backend(backend: State<'_, BackendProcess>) -> BackendBootResult {
    if backend_is_listening() {
        return BackendBootResult {
            ok: true,
            started: false,
            already_running: true,
            listening: true,
            message: "Backend is already listening on 127.0.0.1:8001.".to_string(),
        };
    }

    if let Some(mut child) = take_backend_child(&backend) {
        let _ = child.kill();
        let _ = child.wait();
    }

    let boot = boot_backend();
    let child = boot.child;
    let result = boot.result;

    if let Some(child) = child {
        if let Ok(mut child_guard) = backend.0.lock() {
            *child_guard = Some(child);
        }
    }

    result
}

fn take_backend_child(backend: &BackendProcess) -> Option<Child> {
    backend.0.lock().ok()?.take()
}

struct BackendBoot {
    child: Option<Child>,
    result: BackendBootResult,
}

fn boot_backend() -> BackendBoot {
    if backend_is_listening() {
        return BackendBoot {
            child: None,
            result: BackendBootResult {
                ok: true,
                started: false,
                already_running: true,
                listening: true,
                message: "Backend is already listening on 127.0.0.1:8001.".to_string(),
            },
        };
    }

    let backend_dir = match find_backend_dir() {
        Some(path) => path,
        None => {
            let message = "CaseCapital backend directory was not found.".to_string();
            eprintln!("{message}");
            return BackendBoot {
                child: None,
                result: BackendBootResult {
                    ok: false,
                    started: false,
                    already_running: false,
                    listening: false,
                    message,
                },
            };
        }
    };

    let python = backend_dir.join(".venv").join("Scripts").join("python.exe");
    if !python.exists() {
        let message = format!("CaseCapital backend Python venv was not found at {python:?}.");
        eprintln!("{message}");
        return BackendBoot {
            child: None,
            result: BackendBootResult {
                ok: false,
                started: false,
                already_running: false,
                listening: false,
                message,
            },
        };
    }

    let mut command = Command::new(python);
    command
        .args(["-m", "uvicorn", "server:app", "--host", "127.0.0.1", "--port", "8001"])
        .current_dir(&backend_dir);

    #[cfg(windows)]
    {
        use std::os::windows::process::CommandExt;
        command.creation_flags(0x08000000);
    }

    let child = match command.spawn() {
        Ok(child) => child,
        Err(error) => {
            let message = format!("Failed to start CaseCapital backend: {error}");
            eprintln!("{message}");
            return BackendBoot {
                child: None,
                result: BackendBootResult {
                    ok: false,
                    started: false,
                    already_running: false,
                    listening: false,
                    message,
                },
            };
        }
    };

    for _ in 0..30 {
        if backend_is_listening() {
            return BackendBoot {
                child: Some(child),
                result: BackendBootResult {
                    ok: true,
                    started: true,
                    already_running: false,
                    listening: true,
                    message: "Backend started on 127.0.0.1:8001.".to_string(),
                },
            };
        }
        thread::sleep(Duration::from_millis(500));
    }

    let message = "CaseCapital backend did not answer on port 8001 within 15 seconds.".to_string();
    eprintln!("{message}");
    BackendBoot {
        child: Some(child),
        result: BackendBootResult {
            ok: false,
            started: true,
            already_running: false,
            listening: false,
            message,
        },
    }
}

fn backend_is_listening() -> bool {
    let address = SocketAddr::from(([127, 0, 0, 1], 8001));
    TcpStream::connect_timeout(&address, Duration::from_millis(250)).is_ok()
}

fn find_backend_dir() -> Option<PathBuf> {
    if let Ok(path) = env::var("CASE_CAPITAL_BACKEND_DIR") {
        let candidate = PathBuf::from(path);
        if candidate.join("server.py").exists() {
            return Some(candidate);
        }
    }

    let mut candidates = Vec::new();

    if let Ok(current_dir) = env::current_dir() {
        candidates.push(current_dir.join("backend"));
        candidates.push(current_dir.join("..").join("backend"));
        candidates.push(current_dir.join("..").join("..").join("backend"));
    }

    if let Ok(exe) = env::current_exe() {
        if let Some(exe_dir) = exe.parent() {
            candidates.push(exe_dir.join("backend"));
            candidates.push(exe_dir.join("..").join("backend"));
            candidates.push(exe_dir.join("..").join("..").join("backend"));
        }
    }

    candidates.push(PathBuf::from(r"C:\Case Capital\stock-intel\backend"));

    candidates
        .into_iter()
        .find(|candidate| candidate.join("server.py").exists())
}
