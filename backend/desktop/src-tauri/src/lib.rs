use serde::Serialize;
use serde_json::Value;
use std::collections::HashMap;
use std::fs::File;
use std::io::{Read, Seek, SeekFrom};
use std::path::{Path, PathBuf};
use std::process::Stdio;
use std::sync::Mutex;
use std::time::{Duration, Instant};
use tauri::http::{header, Method, Request, Response, StatusCode};
use tauri::ipc::Channel;
use tauri::Manager;
use tokio::io::{AsyncBufReadExt, AsyncReadExt, AsyncWriteExt, BufReader};
use tokio::process::Command;

#[derive(Debug, Clone, Serialize)]
struct BridgeMessage {
    #[serde(flatten)]
    payload: Value,
}

#[derive(Debug, Serialize)]
#[serde(rename_all = "camelCase")]
struct BridgeResult {
    code: i32,
    messages: Vec<Value>,
    stderr: String,
}

#[derive(Default)]
struct AudioRegistry {
    files: Mutex<HashMap<String, AudioEntry>>,
}

struct AudioEntry {
    path: PathBuf,
    expires_at: Instant,
}

#[derive(Debug, Serialize)]
struct AudioAsset {
    token: String,
    url: String,
}

const AUDIO_EXTENSIONS: &[&str] = &[
    "mp3", "wav", "aiff", "aif", "flac", "m4a", "aac", "ogg", "opus",
];

fn backend_dir() -> PathBuf {
    Path::new(env!("CARGO_MANIFEST_DIR"))
        .parent()
        .and_then(Path::parent)
        .expect("desktop must live under backend/desktop")
        .to_path_buf()
}

fn bridge_command(app: &tauri::AppHandle, bridge_args: &[String]) -> Result<Command, String> {
    let resource_dir = app
        .path()
        .resource_dir()
        .map_err(|error| format!("could not resolve app resources: {error}"))?;
    let bundled_worker_root = resource_dir.join("engine").join("soundcloud-worker");
    let bundled_worker = if bundled_worker_root.is_dir() {
        bundled_worker_root.join("soundcloud-worker")
    } else {
        bundled_worker_root
    };

    if bundled_worker.exists() {
        let mut command = Command::new(bundled_worker);
        command.args(bridge_args);
        return Ok(command);
    }

    let backend = backend_dir();
    let python = std::env::var_os("SOUNDCLOUD_DL_PYTHON")
        .map(PathBuf::from)
        .unwrap_or_else(|| backend.join(".venv").join("bin").join("python"));
    if !python.exists() {
        return Err(format!(
            "SoundCloud engine not found at {}. Run `uv sync` in backend/ for development.",
            python.display()
        ));
    }

    let mut command = Command::new(python);
    command
        .current_dir(backend)
        .arg("-m")
        .arg("app.soundcloud_bridge")
        .args(bridge_args);
    Ok(command)
}

#[tauri::command]
async fn run_bridge(
    app: tauri::AppHandle,
    command: String,
    args: Vec<String>,
    payload: Option<Value>,
    on_message: Channel<BridgeMessage>,
) -> Result<BridgeResult, String> {
    if command.is_empty() || command.starts_with('-') || command.contains('/') {
        return Err("invalid bridge command".to_string());
    }

    let mut bridge_args = vec![command];
    bridge_args.extend(args);
    if payload.is_some() {
        bridge_args.extend(["--payload".to_string(), "-".to_string()]);
    }

    let mut child = bridge_command(&app, &bridge_args)?
        .stdin(Stdio::piped())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        .kill_on_drop(true)
        .spawn()
        .map_err(|error| format!("failed to start SoundCloud engine: {error}"))?;

    if let Some(value) = payload {
        let mut stdin = child.stdin.take().ok_or("engine stdin unavailable")?;
        let input = serde_json::to_vec(&value).map_err(|error| error.to_string())?;
        stdin
            .write_all(&input)
            .await
            .map_err(|error| format!("failed to write engine input: {error}"))?;
        stdin.shutdown().await.map_err(|error| error.to_string())?;
    }

    let stdout = child.stdout.take().ok_or("engine stdout unavailable")?;
    let stderr = child.stderr.take().ok_or("engine stderr unavailable")?;
    let stderr_task = tauri::async_runtime::spawn(async move {
        let mut reader = BufReader::new(stderr);
        let mut output = String::new();
        let _ = reader.read_to_string(&mut output).await;
        output
    });

    let mut messages = Vec::new();
    let mut lines = BufReader::new(stdout).lines();
    while let Some(line) = lines
        .next_line()
        .await
        .map_err(|error| format!("failed reading engine output: {error}"))?
    {
        if line.trim().is_empty() {
            continue;
        }
        let value: Value = serde_json::from_str(&line)
            .unwrap_or_else(|_| serde_json::json!({"event": "status", "message": line}));
        let _ = on_message.send(BridgeMessage {
            payload: value.clone(),
        });
        messages.push(value);
    }

    let status = child
        .wait()
        .await
        .map_err(|error| format!("failed waiting for SoundCloud engine: {error}"))?;
    let stderr = stderr_task.await.unwrap_or_default();
    if status.code().is_none() {
        let value = serde_json::json!({
            "event": "error",
            "message": format!("The audio engine crashed ({status}). Downloaded files are still on disk. Retry sync to resume analysis/import.")
        });
        let _ = on_message.send(BridgeMessage { payload: value.clone() });
        messages.push(value);
    }
    Ok(BridgeResult {
        code: status.code().unwrap_or(1),
        messages,
        stderr,
    })
}

#[tauri::command]
fn register_audio(
    path: String,
    state: tauri::State<'_, AudioRegistry>,
) -> Result<AudioAsset, String> {
    let path = PathBuf::from(path)
        .canonicalize()
        .map_err(|error| format!("audio file is unavailable: {error}"))?;
    if !path.is_file() {
        return Err("audio path is not a file".to_string());
    }
    let extension = path
        .extension()
        .and_then(|value| value.to_str())
        .unwrap_or_default()
        .to_ascii_lowercase();
    if !AUDIO_EXTENSIONS.contains(&extension.as_str()) {
        return Err(format!("unsupported audio type: {extension}"));
    }
    let token = uuid::Uuid::new_v4().simple().to_string();
    state
        .files
        .lock()
        .map_err(|_| "audio registry is unavailable".to_string())?
        .insert(
            token.clone(),
            AudioEntry {
                path,
                expires_at: Instant::now() + Duration::from_secs(2 * 60 * 60),
            },
        );
    Ok(AudioAsset {
        url: format!("soundcloud-audio://localhost/{token}"),
        token,
    })
}

#[tauri::command]
fn release_audio(token: String, state: tauri::State<'_, AudioRegistry>) -> Result<(), String> {
    state
        .files
        .lock()
        .map_err(|_| "audio registry is unavailable".to_string())?
        .remove(&token);
    Ok(())
}

fn audio_response(request: Request<Vec<u8>>, path: &Path) -> Response<Vec<u8>> {
    let mut file = match File::open(path) {
        Ok(file) => file,
        Err(_) => return response(StatusCode::NOT_FOUND, "text/plain", Vec::new()),
    };
    let total = match file.metadata() {
        Ok(metadata) => metadata.len(),
        Err(_) => return response(StatusCode::INTERNAL_SERVER_ERROR, "text/plain", Vec::new()),
    };
    let mime = audio_mime(path);
    let range = request
        .headers()
        .get(header::RANGE)
        .and_then(|value| value.to_str().ok())
        .and_then(|value| parse_range(value, total));
    if request.headers().contains_key(header::RANGE) && range.is_none() {
        return Response::builder()
            .status(StatusCode::RANGE_NOT_SATISFIABLE)
            .header(header::CONTENT_RANGE, format!("bytes */{total}"))
            .header(header::ACCESS_CONTROL_ALLOW_ORIGIN, "*")
            .body(Vec::new())
            .unwrap();
    }
    let (start, end, status) = range
        .map(|(start, end)| (start, end, StatusCode::PARTIAL_CONTENT))
        .unwrap_or_else(|| (0, total.saturating_sub(1), StatusCode::OK));
    let length = if total == 0 { 0 } else { end - start + 1 };
    let mut body = Vec::new();
    if request.method() != Method::HEAD && length > 0 {
        if file.seek(SeekFrom::Start(start)).is_err() {
            return response(StatusCode::INTERNAL_SERVER_ERROR, "text/plain", Vec::new());
        }
        body.resize(length as usize, 0);
        if file.read_exact(&mut body).is_err() {
            return response(StatusCode::INTERNAL_SERVER_ERROR, "text/plain", Vec::new());
        }
    }
    let mut builder = Response::builder()
        .status(status)
        .header(header::CONTENT_TYPE, mime)
        .header(header::CONTENT_LENGTH, length.to_string())
        .header(header::ACCEPT_RANGES, "bytes")
        .header(header::ACCESS_CONTROL_ALLOW_ORIGIN, "*")
        .header(header::CACHE_CONTROL, "no-store");
    if status == StatusCode::PARTIAL_CONTENT {
        builder = builder.header(
            header::CONTENT_RANGE,
            format!("bytes {start}-{end}/{total}"),
        );
    }
    builder.body(body).unwrap()
}

fn parse_range(value: &str, total: u64) -> Option<(u64, u64)> {
    if total == 0 || !value.starts_with("bytes=") || value.contains(',') {
        return None;
    }
    let (start, end) = value.trim_start_matches("bytes=").split_once('-')?;
    if start.is_empty() {
        let suffix = end.parse::<u64>().ok()?.min(total);
        if suffix == 0 {
            return None;
        }
        return Some((total - suffix, total - 1));
    }
    let start = start.parse::<u64>().ok()?;
    if start >= total {
        return None;
    }
    let end = if end.is_empty() {
        total - 1
    } else {
        end.parse::<u64>().ok()?.min(total - 1)
    };
    (end >= start).then_some((start, end))
}

fn audio_mime(path: &Path) -> &'static str {
    match path
        .extension()
        .and_then(|value| value.to_str())
        .unwrap_or_default()
        .to_ascii_lowercase()
        .as_str()
    {
        "mp3" => "audio/mpeg",
        "wav" => "audio/wav",
        "aiff" | "aif" => "audio/aiff",
        "flac" => "audio/flac",
        "m4a" | "aac" => "audio/mp4",
        "ogg" | "opus" => "audio/ogg",
        _ => "application/octet-stream",
    }
}

fn response(status: StatusCode, mime: &'static str, body: Vec<u8>) -> Response<Vec<u8>> {
    Response::builder()
        .status(status)
        .header(header::CONTENT_TYPE, mime)
        .header(header::ACCESS_CONTROL_ALLOW_ORIGIN, "*")
        .body(body)
        .unwrap()
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    tauri::Builder::default()
        .plugin(tauri_plugin_dialog::init())
        .manage(AudioRegistry::default())
        .register_uri_scheme_protocol("soundcloud-audio", |context, request| {
            let token = request.uri().path().trim_matches('/');
            let state = context.app_handle().state::<AudioRegistry>();
            let path = state.files.lock().ok().and_then(|mut files| {
                let now = Instant::now();
                files.retain(|_, entry| entry.expires_at > now);
                files.get(token).map(|entry| entry.path.clone())
            });
            match path {
                Some(path) => audio_response(request, &path),
                None => response(StatusCode::NOT_FOUND, "text/plain", Vec::new()),
            }
        })
        .invoke_handler(tauri::generate_handler![
            run_bridge,
            register_audio,
            release_audio
        ])
        .run(tauri::generate_context!())
        .expect("error while running Crate desktop");
}

#[cfg(test)]
mod tests {
    use super::{audio_response, parse_range};
    use std::fs;
    use tauri::http::{header, Request, StatusCode};

    #[test]
    fn parses_audio_byte_ranges() {
        assert_eq!(parse_range("bytes=0-99", 1000), Some((0, 99)));
        assert_eq!(parse_range("bytes=900-", 1000), Some((900, 999)));
        assert_eq!(parse_range("bytes=-100", 1000), Some((900, 999)));
        assert_eq!(parse_range("bytes=1000-", 1000), None);
        assert_eq!(parse_range("bytes=0-1,4-5", 1000), None);
    }

    #[test]
    fn serves_partial_audio_with_range_headers() {
        let path =
            std::env::temp_dir().join(format!("soundcloud-audio-{}.mp3", uuid::Uuid::new_v4()));
        fs::write(&path, (0_u8..=255).collect::<Vec<_>>()).unwrap();
        let request = Request::builder()
            .header(header::RANGE, "bytes=10-19")
            .body(Vec::new())
            .unwrap();

        let response = audio_response(request, &path);

        assert_eq!(response.status(), StatusCode::PARTIAL_CONTENT);
        assert_eq!(response.headers()[header::CONTENT_RANGE], "bytes 10-19/256");
        assert_eq!(response.body(), &(10_u8..20).collect::<Vec<_>>());
        fs::remove_file(path).unwrap();
    }
}
