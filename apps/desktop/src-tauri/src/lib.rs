mod bridge;

use std::path::PathBuf;
use std::sync::Arc;

use bridge::{BackendSupervisor, BridgeStatus, HostFailure};
use serde_json::{Value, json};
use tauri::State;
use tauri::ipc::Channel;

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
        .manage(DesktopState { supervisor })
        .invoke_handler(tauri::generate_handler![
            bridge_start,
            bridge_restart,
            bridge_ping,
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
