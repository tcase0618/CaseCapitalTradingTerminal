use std::{
    env,
    fs::{create_dir_all, OpenOptions},
    io::{Read, Write},
    net::{SocketAddr, TcpStream},
    path::PathBuf,
    process::{Child, Command},
    sync::Mutex,
    thread,
    time::Duration,
};

use serde::Serialize;
use tauri::{Manager, State};

struct BackendProcess(Mutex<Option<Child>>);

#[derive(Serialize)]
struct BackendBootResult {
    ok: bool,
    started: bool,
    already_running: bool,
    listening: bool,
    message: String,
}

#[derive(Serialize)]
struct BackendHttpResponse {
    status: u16,
    body: String,
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    tauri::Builder::default()
        .setup(|app| {
            let backend = boot_backend().child;
            app.manage(BackendProcess(Mutex::new(backend)));
            Ok(())
        })
        .invoke_handler(tauri::generate_handler![
            force_boot_backend,
            backend_status,
            backend_request
        ])
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

#[tauri::command]
fn backend_status() -> BackendBootResult {
    if backend_http_status_ok() {
        BackendBootResult {
            ok: true,
            started: false,
            already_running: true,
            listening: true,
            message: "Backend HTTP status is healthy on 127.0.0.1:8001.".to_string(),
        }
    } else if backend_is_listening() {
        BackendBootResult {
            ok: false,
            started: false,
            already_running: true,
            listening: true,
            message: "Backend port is open, but /api/status did not return healthy.".to_string(),
        }
    } else {
        BackendBootResult {
            ok: false,
            started: false,
            already_running: false,
            listening: false,
            message: "Backend is not listening on 127.0.0.1:8001.".to_string(),
        }
    }
}

#[tauri::command]
fn backend_request(method: String, path: String, body: Option<String>) -> Result<BackendHttpResponse, String> {
    let method = method.to_uppercase();
    let mut path = path;
    if !path.starts_with('/') {
        path = format!("/{path}");
    }
    if !path.starts_with("/api/") && path != "/api" {
        return Err(format!("Refusing non-api backend path: {path}"));
    }
    backend_http_request(&method, &path, body.as_deref()).map_err(|error| error.to_string())
}

fn take_backend_child(backend: &BackendProcess) -> Option<Child> {
    backend.0.lock().ok()?.take()
}

struct BackendBoot {
    child: Option<Child>,
    result: BackendBootResult,
}

fn boot_backend() -> BackendBoot {
    write_boot_log("boot_backend called");
    if backend_is_listening() {
        write_boot_log("backend already listening on 127.0.0.1:8001");
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
            write_boot_log(&message);
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
    write_boot_log(&format!("backend_dir={backend_dir:?} python={python:?}"));
    if !python.exists() {
        let message = format!("CaseCapital backend Python venv was not found at {python:?}.");
        eprintln!("{message}");
        write_boot_log(&message);
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
            write_boot_log(&message);
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
    write_boot_log(&format!("backend spawn requested pid={}", child.id()));

    for _ in 0..30 {
        if backend_is_listening() {
            write_boot_log("backend port opened after spawn");
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
    write_boot_log(&message);
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

fn backend_http_status_ok() -> bool {
    let address = SocketAddr::from(([127, 0, 0, 1], 8001));
    let mut stream = match TcpStream::connect_timeout(&address, Duration::from_secs(2)) {
        Ok(stream) => stream,
        Err(_) => return false,
    };
    let _ = stream.set_read_timeout(Some(Duration::from_secs(3)));
    let _ = stream.set_write_timeout(Some(Duration::from_secs(3)));

    let request = b"GET /api/status HTTP/1.1\r\nHost: 127.0.0.1:8001\r\nConnection: close\r\n\r\n";
    if stream.write_all(request).is_err() {
        return false;
    }

    let mut response = String::new();
    if stream.read_to_string(&mut response).is_err() {
        return false;
    }

    response.starts_with("HTTP/1.1 200") && response.contains("\"online\":true")
}

fn backend_http_request(method: &str, path: &str, body: Option<&str>) -> std::io::Result<BackendHttpResponse> {
    let address = SocketAddr::from(([127, 0, 0, 1], 8001));
    let mut stream = TcpStream::connect_timeout(&address, Duration::from_secs(5))?;
    stream.set_read_timeout(Some(Duration::from_secs(20)))?;
    stream.set_write_timeout(Some(Duration::from_secs(10)))?;

    let body = body.unwrap_or("");
    let request = format!(
        "{method} {path} HTTP/1.1\r\nHost: 127.0.0.1:8001\r\nConnection: close\r\nAccept: application/json\r\nContent-Type: application/json\r\nContent-Length: {}\r\n\r\n{}",
        body.as_bytes().len(),
        body
    );
    stream.write_all(request.as_bytes())?;

    let mut raw = Vec::new();
    stream.read_to_end(&mut raw)?;
    let header_end = find_subsequence(&raw, b"\r\n\r\n").unwrap_or(raw.len());
    let head = String::from_utf8_lossy(&raw[..header_end]).to_string();
    let mut body_bytes = if header_end + 4 <= raw.len() {
        raw[(header_end + 4)..].to_vec()
    } else {
        Vec::new()
    };
    let status = head
        .lines()
        .next()
        .and_then(|line| line.split_whitespace().nth(1))
        .and_then(|code| code.parse::<u16>().ok())
        .unwrap_or(0);
    let is_chunked = head.lines().any(|line| {
        let lower = line.to_ascii_lowercase();
        lower.starts_with("transfer-encoding:") && lower.contains("chunked")
    });
    if is_chunked {
        if let Some(decoded) = decode_chunked_body(&body_bytes) {
            body_bytes = decoded;
        }
    }

    Ok(BackendHttpResponse {
        status,
        body: String::from_utf8_lossy(&body_bytes).to_string(),
    })
}

fn find_subsequence(haystack: &[u8], needle: &[u8]) -> Option<usize> {
    haystack.windows(needle.len()).position(|window| window == needle)
}

fn decode_chunked_body(body: &[u8]) -> Option<Vec<u8>> {
    let mut decoded = Vec::new();
    let mut index = 0usize;

    loop {
        let line_end = find_subsequence(&body[index..], b"\r\n")? + index;
        let size_line = std::str::from_utf8(&body[index..line_end]).ok()?;
        let size_hex = size_line.split(';').next()?.trim();
        let chunk_size = usize::from_str_radix(size_hex, 16).ok()?;
        index = line_end + 2;

        if chunk_size == 0 {
            break;
        }

        let chunk_end = index.checked_add(chunk_size)?;
        if chunk_end > body.len() {
            return None;
        }
        decoded.extend_from_slice(&body[index..chunk_end]);
        index = chunk_end;

        if body.get(index..index + 2) == Some(b"\r\n") {
            index += 2;
        }
    }

    Some(decoded)
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

fn write_boot_log(message: &str) {
    let path = PathBuf::from(r"C:\Case Capital\stock-intel\logs\desktop-rust-boot.log");
    if let Some(parent) = path.parent() {
        let _ = create_dir_all(parent);
    }
    if let Ok(mut file) = OpenOptions::new().create(true).append(true).open(path) {
        let _ = writeln!(file, "{:?} {}", std::time::SystemTime::now(), message);
    }
}
