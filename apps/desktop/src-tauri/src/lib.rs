mod bridge;
mod file_dialog;

use std::path::PathBuf;
use std::sync::Arc;

use bridge::{BackendSupervisor, BridgeStatus, EditorRequest, HostFailure};
use serde_json::{Value, json};
use tauri::ipc::Channel;
use tauri::{Manager, State};

struct DesktopState {
    supervisor: Arc<BackendSupervisor>,
}

fn repo_root() -> PathBuf {
    std::env::var_os("HIGH_WILDERNESS_REPO_ROOT")
        .map(PathBuf::from)
        .unwrap_or_else(|| PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("../../.."))
}

fn join_failure(error: impl std::fmt::Display) -> HostFailure {
    HostFailure {
        code: "host.task_join_failed".into(),
        path: "$".into(),
        message: error.to_string(),
        source: "host".into(),
        retryable: false,
        details: json!({}),
    }
}

fn event_sink(events: Channel<Value>) -> Arc<dyn Fn(Value) + Send + Sync> {
    Arc::new(move |event| {
        let _ = events.send(event);
    })
}

#[tauri::command]
async fn bridge_start(
    state: State<'_, DesktopState>,
    events: Channel<Value>,
) -> Result<BridgeStatus, HostFailure> {
    let supervisor = Arc::clone(&state.supervisor);
    tauri::async_runtime::spawn_blocking(move || supervisor.start(event_sink(events)))
        .await
        .map_err(join_failure)?
}

#[tauri::command]
async fn bridge_restart(
    state: State<'_, DesktopState>,
    events: Channel<Value>,
) -> Result<BridgeStatus, HostFailure> {
    let supervisor = Arc::clone(&state.supervisor);
    tauri::async_runtime::spawn_blocking(move || supervisor.restart(event_sink(events)))
        .await
        .map_err(join_failure)?
}

#[tauri::command]
async fn bridge_ping(state: State<'_, DesktopState>, nonce: String) -> Result<Value, HostFailure> {
    let supervisor = Arc::clone(&state.supervisor);
    tauri::async_runtime::spawn_blocking(move || supervisor.ping(nonce))
        .await
        .map_err(join_failure)?
}

#[tauri::command]
async fn bridge_editor_request(
    state: State<'_, DesktopState>,
    request: EditorRequest,
) -> Result<Value, HostFailure> {
    let supervisor = Arc::clone(&state.supervisor);
    tauri::async_runtime::spawn_blocking(move || supervisor.editor_request(request))
        .await
        .map_err(join_failure)?
}

#[tauri::command]
async fn bridge_tactical_request(
    state: State<'_, DesktopState>,
    request: EditorRequest,
) -> Result<Value, HostFailure> {
    let supervisor = Arc::clone(&state.supervisor);
    tauri::async_runtime::spawn_blocking(move || supervisor.tactical_request(request))
        .await
        .map_err(join_failure)?
}

#[tauri::command]
async fn bridge_choose_file(
    window: tauri::WebviewWindow,
    state: State<'_, DesktopState>,
    request: EditorRequest,
) -> Result<Option<Value>, HostFailure> {
    if !matches!(request.method.as_str(), "open" | "save") || request.params != json!({}) {
        return Err(join_failure("未知文件选择操作"));
    }
    let owner = window.hwnd().map_err(join_failure)?.0 as isize;
    let directory = window
        .app_handle()
        .path()
        .app_data_dir()
        .map_err(join_failure)?;
    let supervisor = Arc::clone(&state.supervisor);
    tauri::async_runtime::spawn_blocking(move || {
        let selected = file_dialog::choose(owner, request.method == "save", &directory)
            .map_err(join_failure)?;
        selected
            .map(|path| supervisor.bind_selected_file(request, path))
            .transpose()
    })
    .await
    .map_err(join_failure)?
}

#[tauri::command]
async fn bridge_stop(state: State<'_, DesktopState>) -> Result<BridgeStatus, HostFailure> {
    let supervisor = Arc::clone(&state.supervisor);
    tauri::async_runtime::spawn_blocking(move || supervisor.stop("user_exit"))
        .await
        .map_err(join_failure)?
}

#[tauri::command]
fn bridge_status(state: State<'_, DesktopState>) -> BridgeStatus {
    state.supervisor.status()
}

pub fn run() {
    let supervisor = BackendSupervisor::new(repo_root());
    let application_supervisor = Arc::clone(&supervisor);
    tauri::Builder::default()
        .setup(|app| {
            let recovery = app.path().app_data_dir()?.join("editor-recovery");
            app.state::<DesktopState>()
                .supervisor
                .set_recovery_dir(recovery);
            Ok(())
        })
        .manage(DesktopState { supervisor })
        .invoke_handler(tauri::generate_handler![
            bridge_start,
            bridge_restart,
            bridge_ping,
            bridge_editor_request,
            bridge_tactical_request,
            bridge_choose_file,
            bridge_stop,
            bridge_status,
        ])
        .build(tauri::generate_context!())
        .expect("failed to build High Wilderness desktop application")
        .run(move |_handle, event| {
            if matches!(
                event,
                tauri::RunEvent::Exit | tauri::RunEvent::ExitRequested { .. }
            ) {
                application_supervisor.force_stop();
            }
        });
}
