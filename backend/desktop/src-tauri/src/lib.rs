use serde::Serialize;
use serde_json::Value;
use std::path::{Path, PathBuf};
use std::process::Stdio;
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
        let value: Value = serde_json::from_str(&line).unwrap_or_else(|_| {
            serde_json::json!({"event": "status", "message": line})
        });
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
    Ok(BridgeResult {
        code: status.code().unwrap_or(1),
        messages,
        stderr,
    })
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    tauri::Builder::default()
        .invoke_handler(tauri::generate_handler![run_bridge])
        .run(tauri::generate_context!())
        .expect("error while running SoundCloud DL desktop");
}
