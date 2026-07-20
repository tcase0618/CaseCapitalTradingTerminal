use std::{
    env,
    net::{SocketAddr, TcpStream},
    path::PathBuf,
    process::{Child, Command},
    sync::Mutex,
    thread,
    time::Duration,
};

use tauri::{Manager, WindowEvent};

struct BackendProcess(Mutex<Option<Child>>);

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    tauri::Builder::default()
        .setup(|app| {
            let backend = start_backend_if_needed();
            app.manage(BackendProcess(Mutex::new(backend)));
            Ok(())
        })
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

fn take_backend_child(backend: &BackendProcess) -> Option<Child> {
    backend.0.lock().ok()?.take()
}

fn start_backend_if_needed() -> Option<Child> {
    if backend_is_listening() {
        return None;
    }

    let backend_dir = match find_backend_dir() {
        Some(path) => path,
        None => {
            eprintln!("CaseCapital backend directory was not found.");
            return None;
        }
    };

    let python = backend_dir.join(".venv").join("Scripts").join("python.exe");
    if !python.exists() {
        eprintln!("CaseCapital backend Python venv was not found at {python:?}.");
        return None;
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
            eprintln!("Failed to start CaseCapital backend: {error}");
            return None;
        }
    };

    for _ in 0..30 {
        if backend_is_listening() {
            return Some(child);
        }
        thread::sleep(Duration::from_millis(500));
    }

    eprintln!("CaseCapital backend did not answer on port 8001 within 15 seconds.");
    Some(child)
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
