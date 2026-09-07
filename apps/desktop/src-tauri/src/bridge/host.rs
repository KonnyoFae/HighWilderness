use std::collections::HashMap;
use std::ffi::OsString;
use std::io::{Read, Write};
use std::path::{Path, PathBuf};
use std::process::{Child, ChildStderr, ChildStdout, Command, Stdio};
use std::sync::atomic::{AtomicBool, AtomicU64, AtomicUsize, Ordering};
use std::sync::{Arc, Condvar, Mutex, Weak, mpsc};
use std::thread;
use std::time::{Duration, Instant};

use serde::{Deserialize, Serialize};
use serde_json::{Value, json};
use uuid::Uuid;

use super::protocol::{BRIDGE_INTERFACE, FrameDecoder, encode_message};

const HOST_INTERFACE: &str = "gaotian.desktop-bridge-host/v1alpha1";
const EVENT_INTERFACE: &str = "gaotian.desktop-bridge-event/v1alpha1";

#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct EditorRequest {
    pub backend_instance_id: String,
    pub method: String,
    pub params: Value,
    pub session_id: Option<String>,
    pub expected_revision: Option<u64>,
}

#[derive(Debug, Clone, Serialize, PartialEq)]
pub struct HostFailure {
    pub code: String,
    pub path: String,
    pub message: String,
    pub source: String,
    pub retryable: bool,
    pub details: Value,
}

impl HostFailure {
    fn host(code: &str, message: impl Into<String>) -> Self {
        Self {
            code: format!("host.{code}"),
            path: "$".into(),
            message: message.into(),
            source: "host".into(),
            retryable: false,
            details: json!({}),
        }
    }

    fn protocol(code: &str, path: impl Into<String>, message: impl Into<String>) -> Self {
        Self {
            code: code.into(),
            path: path.into(),
            message: message.into(),
            source: "bridge".into(),
            retryable: false,
            details: json!({}),
        }
    }

    fn timeout(outcome_unknown: bool) -> Self {
        Self {
            code: "bridge.request_timeout".into(),
            path: "$.request_id".into(),
            message: "Python sidecar request exceeded its deadline".into(),
            source: "host".into(),
            retryable: false,
            details: json!({"outcome_unknown": outcome_unknown}),
        }
    }

    fn backend_exited(message: impl Into<String>) -> Self {
        Self {
            code: "bridge.backend_exited".into(),
            path: "$".into(),
            message: message.into(),
            source: "host".into(),
            retryable: false,
            details: json!({}),
        }
    }

    fn busy(message: impl Into<String>) -> Self {
        Self {
            code: "bridge.busy".into(),
            path: "$".into(),
            message: message.into(),
            source: "host".into(),
            retryable: true,
            details: json!({"outcome_unknown": false}),
        }
    }

    fn from_response(value: &Value) -> Self {
        let error = &value["error"];
        Self {
            code: error["code"]
                .as_str()
                .unwrap_or("bridge.invalid_error")
                .into(),
            path: error["path"].as_str().unwrap_or("$").into(),
            message: error["message"]
                .as_str()
                .unwrap_or("Unknown sidecar error")
                .into(),
            source: error["source"].as_str().unwrap_or("bridge").into(),
            retryable: error["retryable"].as_bool().unwrap_or(false),
            details: error["details"].clone(),
        }
    }
}

impl std::fmt::Display for HostFailure {
    fn fmt(&self, formatter: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        write!(formatter, "[{}] {}: {}", self.code, self.path, self.message)
    }
}

impl std::error::Error for HostFailure {}

pub type HostResult<T> = Result<T, HostFailure>;
type EventSink = Arc<dyn Fn(Value) + Send + Sync>;

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize)]
#[serde(rename_all = "SCREAMING_SNAKE_CASE")]
enum Lifecycle {
    Stopped,
    Starting,
    Handshaking,
    Ready,
    Stopping,
    Failed,
}

#[derive(Debug, Clone, Serialize, PartialEq)]
pub struct BridgeStatus {
    pub interface: &'static str,
    pub state: String,
    pub backend_instance_id: Option<String>,
    pub bridge_interface: &'static str,
    pub sidecar_interface: Option<String>,
    pub capabilities: Vec<String>,
    pub ship_schema: Option<String>,
    pub max_frame_bytes: Option<u64>,
    pub last_error: Option<HostFailure>,
}

#[derive(Debug, Clone)]
struct NegotiatedBridge {
    sidecar_interface: String,
    capabilities: Vec<String>,
    ship_schema: String,
    max_frame_bytes: u64,
}

#[derive(Clone)]
pub struct SupervisorConfig {
    pub startup_timeout: Duration,
    pub request_timeout: Duration,
    pub idle_ping_interval: Duration,
    pub idle_ping_timeout: Duration,
    pub shutdown_timeout: Duration,
    pub max_in_flight: usize,
    pub max_queued_bytes: usize,
    pub max_log_bytes: usize,
}

impl Default for SupervisorConfig {
    fn default() -> Self {
        Self {
            startup_timeout: Duration::from_secs(10),
            request_timeout: Duration::from_secs(10),
            idle_ping_interval: Duration::from_secs(5),
            idle_ping_timeout: Duration::from_secs(3),
            shutdown_timeout: Duration::from_secs(3),
            max_in_flight: 32,
            max_queued_bytes: 16 * 1024 * 1024,
            max_log_bytes: 1024 * 1024,
        }
    }
}

#[derive(Clone)]
pub struct BackendCommand {
    program: OsString,
    arguments: Vec<OsString>,
    current_dir: PathBuf,
}

impl BackendCommand {
    pub fn production(repo_root: &Path, instance_id: &str) -> Self {
        let program =
            std::env::var_os("HIGH_WILDERNESS_PYTHON").unwrap_or_else(|| OsString::from("python"));
        Self {
            program,
            arguments: vec![
                "-X".into(),
                "utf8".into(),
                "-u".into(),
                "-m".into(),
                "backend.high_wilderness_sidecar".into(),
                "--instance-id".into(),
                instance_id.into(),
            ],
            current_dir: repo_root.to_path_buf(),
        }
    }

    #[cfg(test)]
    fn fixture(repo_root: &Path, instance_id: &str, mode: &str) -> Self {
        let program =
            std::env::var_os("HIGH_WILDERNESS_PYTHON").unwrap_or_else(|| OsString::from("python"));
        Self {
            program,
            arguments: vec![
                "-X".into(),
                "utf8".into(),
                "-u".into(),
                "tests/fixtures/w1_backend_fixture.py".into(),
                "--instance-id".into(),
                instance_id.into(),
                "--mode".into(),
                mode.into(),
            ],
            current_dir: repo_root.to_path_buf(),
        }
    }
}

struct PendingRequest {
    session_id: Option<String>,
    result: mpsc::SyncSender<HostResult<Value>>,
}

struct RunningBackend {
    instance_id: String,
    child: Mutex<Child>,
    writer: mpsc::SyncSender<Vec<u8>>,
    pending: Mutex<HashMap<String, PendingRequest>>,
    enqueue_lock: Mutex<()>,
    next_request: AtomicU64,
    next_event: AtomicU64,
    queued_bytes: AtomicUsize,
    ready: (Mutex<Option<Value>>, Condvar),
    negotiated: Mutex<Option<NegotiatedBridge>>,
    stopping: AtomicBool,
    sink: EventSink,
}

struct SupervisorState {
    lifecycle: Lifecycle,
    running: Option<Arc<RunningBackend>>,
    last_error: Option<HostFailure>,
}

pub struct BackendSupervisor {
    repo_root: PathBuf,
    config: SupervisorConfig,
    recovery_dir: Mutex<Option<PathBuf>>,
    state: Mutex<SupervisorState>,
}

impl BackendSupervisor {
    pub fn new(repo_root: PathBuf) -> Arc<Self> {
        Self::with_config(repo_root, SupervisorConfig::default())
    }

    fn with_config(repo_root: PathBuf, config: SupervisorConfig) -> Arc<Self> {
        Arc::new(Self {
            repo_root,
            config,
            recovery_dir: Mutex::new(None),
            state: Mutex::new(SupervisorState {
                lifecycle: Lifecycle::Stopped,
                running: None,
                last_error: None,
            }),
        })
    }

    pub fn status(&self) -> BridgeStatus {
        let state = self.state.lock().unwrap();
        let negotiated = state
            .running
            .as_ref()
            .and_then(|running| running.negotiated.lock().unwrap().clone());
        BridgeStatus {
            interface: HOST_INTERFACE,
            state: format!("{:?}", state.lifecycle).to_uppercase(),
            backend_instance_id: state
                .running
                .as_ref()
                .map(|running| running.instance_id.clone()),
            bridge_interface: BRIDGE_INTERFACE,
            sidecar_interface: negotiated
                .as_ref()
                .map(|value| value.sidecar_interface.clone()),
            capabilities: negotiated
                .as_ref()
                .map(|value| value.capabilities.clone())
                .unwrap_or_default(),
            ship_schema: negotiated.as_ref().map(|value| value.ship_schema.clone()),
            max_frame_bytes: negotiated.as_ref().map(|value| value.max_frame_bytes),
            last_error: state.last_error.clone(),
        }
    }

    fn set_lifecycle(&self, lifecycle: Lifecycle, error: Option<HostFailure>) {
        let mut state = self.state.lock().unwrap();
        state.lifecycle = lifecycle;
        state.last_error = error;
    }

    fn emit(sink: &EventSink, kind: &str, instance_id: Option<&str>, payload: Value) {
        sink(json!({
            "interface": EVENT_INTERFACE,
            "kind": kind,
            "backend_instance_id": instance_id,
            "payload": payload,
        }));
    }

    fn fail_pending(running: &RunningBackend, error: HostFailure) {
        let pending = std::mem::take(&mut *running.pending.lock().unwrap());
        for request in pending.into_values() {
            let _ = request.result.send(Err(error.clone()));
        }
    }

    fn fail_instance(self: &Arc<Self>, instance_id: &str, error: HostFailure) {
        let running = {
            let mut state = self.state.lock().unwrap();
            let Some(running) = state.running.as_ref().map(Arc::clone) else {
                return;
            };
            if running.instance_id != instance_id || running.stopping.load(Ordering::SeqCst) {
                return;
            }
            if matches!(
                state.lifecycle,
                Lifecycle::Failed | Lifecycle::Stopped | Lifecycle::Stopping
            ) {
                return;
            }
            state.lifecycle = Lifecycle::Failed;
            state.last_error = Some(error.clone());
            running
        };
        Self::fail_pending(&running, error.clone());
        let _ = running.child.lock().unwrap().kill();
        Self::emit(
            &running.sink,
            "lifecycle",
            Some(instance_id),
            json!({
                "state": "FAILED",
                "error": error,
            }),
        );
    }

    fn spawn_reader(self: &Arc<Self>, running: Arc<RunningBackend>, mut stdout: ChildStdout) {
        let supervisor = Arc::downgrade(self);
        thread::Builder::new()
            .name("high-wilderness-sidecar-stdout".into())
            .spawn(move || {
                let mut decoder = FrameDecoder::default();
                let mut buffer = [0_u8; 64 * 1024];
                loop {
                    let count = match stdout.read(&mut buffer) {
                        Ok(0) => {
                            if let Err(error) = decoder.finish() {
                                fail_from_protocol(&supervisor, &running, error);
                            } else if !running.stopping.load(Ordering::SeqCst) {
                                fail_weak(
                                    &supervisor,
                                    &running,
                                    HostFailure::backend_exited("Python sidecar closed stdout"),
                                );
                            }
                            break;
                        }
                        Ok(count) => count,
                        Err(error) => {
                            fail_weak(
                                &supervisor,
                                &running,
                                HostFailure::host("stdout_read_failed", error.to_string()),
                            );
                            break;
                        }
                    };
                    let messages = match decoder.feed(&buffer[..count]) {
                        Ok(messages) => messages,
                        Err(error) => {
                            fail_from_protocol(&supervisor, &running, error);
                            break;
                        }
                    };
                    for message in messages {
                        if message["backend_instance_id"].as_str() != Some(&running.instance_id) {
                            fail_weak(
                                &supervisor,
                                &running,
                                HostFailure::protocol(
                                    "bridge.instance_mismatch",
                                    "$.backend_instance_id",
                                    "response belongs to a different backend instance",
                                ),
                            );
                            return;
                        }
                        match message["kind"].as_str() {
                            Some("response") => {
                                let request_id = message["request_id"].as_str().unwrap().to_owned();
                                let pending = running.pending.lock().unwrap().remove(&request_id);
                                if let Some(pending) = pending {
                                    if message["session_id"].as_str()
                                        != pending.session_id.as_deref()
                                    {
                                        let error = HostFailure::protocol(
                                            "bridge.session_mismatch",
                                            "$.session_id",
                                            "response belongs to a different session",
                                        );
                                        let _ = pending.result.send(Err(error.clone()));
                                        fail_weak(&supervisor, &running, error);
                                        return;
                                    }
                                    let _ = pending.result.send(Ok(message));
                                } else if !running.stopping.load(Ordering::SeqCst) {
                                    fail_weak(
                                        &supervisor,
                                        &running,
                                        HostFailure::protocol(
                                            "bridge.unexpected_response",
                                            "$.request_id",
                                            "response has no pending request",
                                        ),
                                    );
                                    return;
                                }
                            }
                            Some("event") => {
                                let sequence = message["sequence"].as_u64().unwrap();
                                let expected = running.next_event.fetch_add(1, Ordering::SeqCst);
                                if sequence != expected {
                                    fail_weak(
                                        &supervisor,
                                        &running,
                                        HostFailure::protocol(
                                            "bridge.event_sequence_invalid",
                                            "$.sequence",
                                            format!("expected event {expected}, got {sequence}"),
                                        ),
                                    );
                                    return;
                                }
                                if message["event"] == "system.ready" {
                                    let mut ready = running.ready.0.lock().unwrap();
                                    *ready = Some(message["payload"].clone());
                                    running.ready.1.notify_all();
                                }
                                BackendSupervisor::emit(
                                    &running.sink,
                                    "backend",
                                    Some(&running.instance_id),
                                    message,
                                );
                            }
                            _ => unreachable!(),
                        }
                    }
                }
            })
            .expect("failed to create sidecar stdout thread");
    }

    fn spawn_stderr(&self, running: Arc<RunningBackend>, mut stderr: ChildStderr) {
        let limit = self.config.max_log_bytes;
        thread::Builder::new()
            .name("high-wilderness-sidecar-stderr".into())
            .spawn(move || {
                let mut total = 0_usize;
                let mut truncation_sent = false;
                let mut buffer = [0_u8; 4096];
                loop {
                    let count = match stderr.read(&mut buffer) {
                        Ok(0) | Err(_) => break,
                        Ok(count) => count,
                    };
                    if total < limit {
                        let accepted = count.min(limit - total);
                        total += accepted;
                        BackendSupervisor::emit(
                            &running.sink,
                            "log",
                            Some(&running.instance_id),
                            json!({"stream": "stderr", "text": String::from_utf8_lossy(&buffer[..accepted])}),
                        );
                    } else if !truncation_sent {
                        truncation_sent = true;
                        BackendSupervisor::emit(
                            &running.sink,
                            "log",
                            Some(&running.instance_id),
                            json!({"stream": "stderr", "truncated": true, "limit_bytes": limit}),
                        );
                    }
                }
            })
            .expect("failed to create sidecar stderr thread");
    }

    fn spawn_writer(
        self: &Arc<Self>,
        running: Arc<RunningBackend>,
        mut stdin: impl Write + Send + 'static,
        receiver: mpsc::Receiver<Vec<u8>>,
    ) {
        let supervisor = Arc::downgrade(self);
        thread::Builder::new()
            .name("high-wilderness-sidecar-stdin".into())
            .spawn(move || {
                while let Ok(wire) = receiver.recv() {
                    let length = wire.len();
                    let result = stdin.write_all(&wire).and_then(|_| stdin.flush());
                    running.queued_bytes.fetch_sub(length, Ordering::SeqCst);
                    if let Err(error) = result {
                        fail_weak(
                            &supervisor,
                            &running,
                            HostFailure::host("stdin_write_failed", error.to_string()),
                        );
                        break;
                    }
                }
            })
            .expect("failed to create sidecar stdin thread");
    }

    fn spawn_heartbeat(self: &Arc<Self>, running: Arc<RunningBackend>) {
        let supervisor = Arc::downgrade(self);
        let interval = self.config.idle_ping_interval;
        let timeout = self.config.idle_ping_timeout;
        thread::Builder::new()
            .name("high-wilderness-sidecar-heartbeat".into())
            .spawn(move || {
                let mut heartbeat_number = 1_u64;
                loop {
                    thread::sleep(interval);
                    if running.stopping.load(Ordering::SeqCst) {
                        break;
                    }
                    let Some(supervisor) = supervisor.upgrade() else {
                        break;
                    };
                    let is_current_ready = {
                        let state = supervisor.state.lock().unwrap();
                        state.lifecycle == Lifecycle::Ready
                            && state
                                .running
                                .as_ref()
                                .is_some_and(|value| value.instance_id == running.instance_id)
                    };
                    if !is_current_ready {
                        break;
                    }
                    let nonce = format!("heartbeat.{heartbeat_number}");
                    heartbeat_number = heartbeat_number.saturating_add(1);
                    match supervisor.request_on_internal(
                        &running,
                        "system.ping",
                        json!({"nonce": nonce}),
                        timeout,
                        true,
                    ) {
                        Ok(Some(result)) => BackendSupervisor::emit(
                            &running.sink,
                            "heartbeat",
                            Some(&running.instance_id),
                            result,
                        ),
                        Ok(None) => continue,
                        Err(_) => break,
                    }
                }
            })
            .expect("failed to create sidecar heartbeat thread");
    }

    pub fn start(self: &Arc<Self>, sink: EventSink) -> HostResult<BridgeStatus> {
        let instance_id = format!("backend.{}", Uuid::new_v4().simple());
        let mut command = BackendCommand::production(&self.repo_root, &instance_id);
        if let Some(path) = self.recovery_dir.lock().unwrap().as_ref() {
            command.arguments.push("--recovery-dir".into());
            command.arguments.push(path.as_os_str().to_owned());
        }
        self.start_command(command, instance_id, sink)
    }

    fn start_command(
        self: &Arc<Self>,
        command: BackendCommand,
        instance_id: String,
        sink: EventSink,
    ) -> HostResult<BridgeStatus> {
        self.force_stop();
        self.set_lifecycle(Lifecycle::Starting, None);
        Self::emit(
            &sink,
            "lifecycle",
            Some(&instance_id),
            json!({"state": "STARTING"}),
        );
        let started = Instant::now();
        let mut process = Command::new(&command.program);
        process
            .args(&command.arguments)
            .current_dir(&command.current_dir)
            .stdin(Stdio::piped())
            .stdout(Stdio::piped())
            .stderr(Stdio::piped());
        #[cfg(windows)]
        {
            use std::os::windows::process::CommandExt;
            process.creation_flags(0x0800_0000);
        }
        let mut child = process.spawn().map_err(|error| {
            let failure = HostFailure::host("spawn_failed", error.to_string());
            self.set_lifecycle(Lifecycle::Failed, Some(failure.clone()));
            failure
        })?;
        let stdin = child.stdin.take().unwrap();
        let stdout = child.stdout.take().unwrap();
        let stderr = child.stderr.take().unwrap();
        let (writer, receiver) = mpsc::sync_channel(self.config.max_in_flight);
        let running = Arc::new(RunningBackend {
            instance_id: instance_id.clone(),
            child: Mutex::new(child),
            writer,
            pending: Mutex::new(HashMap::new()),
            enqueue_lock: Mutex::new(()),
            next_request: AtomicU64::new(1),
            next_event: AtomicU64::new(1),
            queued_bytes: AtomicUsize::new(0),
            ready: (Mutex::new(None), Condvar::new()),
            negotiated: Mutex::new(None),
            stopping: AtomicBool::new(false),
            sink,
        });
        {
            let mut state = self.state.lock().unwrap();
            state.lifecycle = Lifecycle::Handshaking;
            state.running = Some(Arc::clone(&running));
        }
        self.spawn_writer(Arc::clone(&running), stdin, receiver);
        self.spawn_reader(Arc::clone(&running), stdout);
        self.spawn_stderr(Arc::clone(&running), stderr);

        let remaining = self
            .config
            .startup_timeout
            .saturating_sub(started.elapsed());
        let result = self.request_on(
            &running,
            "system.hello",
            json!({
                "client_name": "high-wilderness-desktop",
                "client_version": env!("CARGO_PKG_VERSION"),
                "supported_interfaces": [BRIDGE_INTERFACE],
                "required_capabilities": ["system.hello", "system.ping", "system.shutdown"],
            }),
            remaining,
        );
        let result = match result {
            Ok(result) => result,
            Err(error) => {
                self.fail_instance(&instance_id, error.clone());
                return Err(error);
            }
        };
        if result["selected_interface"] != BRIDGE_INTERFACE {
            let error = HostFailure::protocol(
                "bridge.unsupported_interface",
                "$.result.selected_interface",
                "sidecar selected an unexpected interface",
            );
            self.fail_instance(&instance_id, error.clone());
            return Err(error);
        }
        let remaining = self
            .config
            .startup_timeout
            .saturating_sub(started.elapsed());
        let ready = running.ready.0.lock().unwrap();
        let (ready, timeout) = running
            .ready
            .1
            .wait_timeout_while(ready, remaining, |ready| ready.is_none())
            .unwrap();
        if timeout.timed_out() || ready.is_none() {
            drop(ready);
            let error = HostFailure::host("startup_timeout", "sidecar did not emit system.ready");
            self.fail_instance(&instance_id, error.clone());
            return Err(error);
        }
        let ready_payload = ready.clone().unwrap();
        drop(ready);
        if ready_payload["selected_interface"] != BRIDGE_INTERFACE {
            let error = HostFailure::protocol(
                "bridge.unsupported_interface",
                "$.payload.selected_interface",
                "system.ready selected an unexpected interface",
            );
            self.fail_instance(&instance_id, error.clone());
            return Err(error);
        }
        let negotiated = (|| -> HostResult<NegotiatedBridge> {
            let sidecar_interface = ready_payload["sidecar_interface"]
                .as_str()
                .filter(|value| !value.is_empty())
                .ok_or_else(|| {
                    HostFailure::protocol(
                        "bridge.invalid_message",
                        "$.payload.sidecar_interface",
                        "system.ready must identify the sidecar interface",
                    )
                })?
                .to_owned();
            let capabilities = result["capabilities"]
                .as_array()
                .filter(|values| values.iter().all(Value::is_string))
                .ok_or_else(|| {
                    HostFailure::protocol(
                        "bridge.invalid_message",
                        "$.result.capabilities",
                        "hello capabilities must be a string array",
                    )
                })?
                .iter()
                .map(|value| value.as_str().unwrap().to_owned())
                .collect();
            let ship_schema = result["ship_schema"]
                .as_str()
                .filter(|value| !value.is_empty())
                .ok_or_else(|| {
                    HostFailure::protocol(
                        "bridge.invalid_message",
                        "$.result.ship_schema",
                        "hello must identify the ship schema",
                    )
                })?
                .to_owned();
            let max_frame_bytes = result["max_frame_bytes"].as_u64().ok_or_else(|| {
                HostFailure::protocol(
                    "bridge.invalid_message",
                    "$.result.max_frame_bytes",
                    "hello must identify the frame byte limit",
                )
            })?;
            Ok(NegotiatedBridge {
                sidecar_interface,
                capabilities,
                ship_schema,
                max_frame_bytes,
            })
        })();
        let negotiated = match negotiated {
            Ok(negotiated) => negotiated,
            Err(error) => {
                self.fail_instance(&instance_id, error.clone());
                return Err(error);
            }
        };
        *running.negotiated.lock().unwrap() = Some(negotiated.clone());
        self.set_lifecycle(Lifecycle::Ready, None);
        Self::emit(
            &running.sink,
            "handshake",
            Some(&instance_id),
            json!({
                "selected_interface": BRIDGE_INTERFACE,
                "sidecar_interface": negotiated.sidecar_interface,
                "capabilities": negotiated.capabilities,
                "ship_schema": negotiated.ship_schema,
                "max_frame_bytes": negotiated.max_frame_bytes,
            }),
        );
        Self::emit(
            &running.sink,
            "lifecycle",
            Some(&instance_id),
            json!({"state": "READY"}),
        );
        self.spawn_heartbeat(running);
        Ok(self.status())
    }

    fn reserve_queue_bytes(&self, running: &RunningBackend, bytes: usize) -> bool {
        let mut current = running.queued_bytes.load(Ordering::SeqCst);
        loop {
            let Some(next) = current.checked_add(bytes) else {
                return false;
            };
            if next > self.config.max_queued_bytes {
                return false;
            }
            match running.queued_bytes.compare_exchange(
                current,
                next,
                Ordering::SeqCst,
                Ordering::SeqCst,
            ) {
                Ok(_) => return true,
                Err(actual) => current = actual,
            }
        }
    }

    fn request_on(
        self: &Arc<Self>,
        running: &Arc<RunningBackend>,
        method: &str,
        params: Value,
        timeout: Duration,
    ) -> HostResult<Value> {
        self.request_on_internal(running, method, params, timeout, false)
            .map(|result| result.expect("non-idle request must be enqueued"))
    }

    fn request_on_internal(
        self: &Arc<Self>,
        running: &Arc<RunningBackend>,
        method: &str,
        params: Value,
        timeout: Duration,
        only_if_idle: bool,
    ) -> HostResult<Option<Value>> {
        self.request_scoped(running, method, params, timeout, only_if_idle, None)
    }

    fn request_scoped(
        self: &Arc<Self>,
        running: &Arc<RunningBackend>,
        method: &str,
        params: Value,
        timeout: Duration,
        only_if_idle: bool,
        scope: Option<(&str, u64)>,
    ) -> HostResult<Option<Value>> {
        // Number assignment and enqueue must be atomic across UI and heartbeat callers.
        let enqueue_guard = running.enqueue_lock.lock().unwrap();
        let request_number = running.next_request.fetch_add(1, Ordering::SeqCst);
        let request_id = format!("req.{request_number}");
        let request = json!({
            "backend_instance_id": running.instance_id,
            "expected_revision": scope.map(|(_, revision)| revision),
            "interface": BRIDGE_INTERFACE,
            "kind": "request",
            "method": method,
            "params": params,
            "request_id": request_id,
            "session_id": scope.map(|(session_id, _)| session_id),
        });
        let wire = encode_message(&request)
            .map_err(|error| HostFailure::protocol(error.code, error.path, error.message))?;
        let (sender, receiver) = mpsc::sync_channel(1);
        {
            let mut pending = running.pending.lock().unwrap();
            if only_if_idle && !pending.is_empty() {
                return Ok(None);
            }
            if pending.len() >= self.config.max_in_flight {
                return Err(HostFailure::busy("too many in-flight requests"));
            }
            pending.insert(
                request_id.clone(),
                PendingRequest {
                    session_id: scope.map(|(id, _)| id.to_owned()),
                    result: sender,
                },
            );
        }
        if !self.reserve_queue_bytes(running, wire.len()) {
            running.pending.lock().unwrap().remove(&request_id);
            return Err(HostFailure::busy("queued request byte limit exceeded"));
        }
        if let Err(error) = running.writer.try_send(wire) {
            let bytes = match &error {
                mpsc::TrySendError::Full(wire) | mpsc::TrySendError::Disconnected(wire) => {
                    wire.len()
                }
            };
            running.queued_bytes.fetch_sub(bytes, Ordering::SeqCst);
            running.pending.lock().unwrap().remove(&request_id);
            return Err(HostFailure::busy("sidecar writer queue is unavailable"));
        }
        drop(enqueue_guard);
        let response = match receiver.recv_timeout(timeout) {
            Ok(result) => result?,
            Err(_) => {
                running.pending.lock().unwrap().remove(&request_id);
                let error = HostFailure::timeout(true);
                self.fail_instance(&running.instance_id, error.clone());
                return Err(error);
            }
        };
        if response["ok"].as_bool() == Some(true) {
            Ok(Some(response["result"].clone()))
        } else {
            Err(HostFailure::from_response(&response))
        }
    }

    pub fn set_recovery_dir(&self, path: PathBuf) {
        *self.recovery_dir.lock().unwrap() = Some(path);
    }

    pub fn bind_selected_file(
        self: &Arc<Self>,
        mut request: EditorRequest,
        path: PathBuf,
    ) -> HostResult<Value> {
        if !matches!(request.method.as_str(), "open" | "save") {
            return Err(HostFailure::host(
                "invalid_file_mode",
                "invalid file selection mode",
            ));
        }
        request.params = json!({"mode": request.method, "host_path": path});
        request.method = "editor.bind_file".into();
        self.editor_request_impl(request, true)
    }

    pub fn editor_request(self: &Arc<Self>, request: EditorRequest) -> HostResult<Value> {
        self.editor_request_impl(request, false)
    }

    pub fn tactical_request(self: &Arc<Self>, request: EditorRequest) -> HostResult<Value> {
        if !matches!(request.method.as_str(), "tactical.create" | "tactical.inspect" | "tactical.close") {
            return Err(HostFailure::host("method_not_supported", "tactical method not enabled"));
        }
        if request.session_id.is_some() || request.expected_revision.is_some() {
            return Err(HostFailure::host("invalid_scope", "tactical requests use a scene id in params"));
        }
        self.domain_request(request)
    }

    fn editor_request_impl(
        self: &Arc<Self>,
        request: EditorRequest,
        trusted_file_selection: bool,
    ) -> HostResult<Value> {
        if !matches!(
            request.method.as_str(),
            "resource.list"
                | "editor.open"
                | "editor.create"
                | "editor.inspect"
                | "editor.preview"
                | "editor.command"
                | "editor.undo"
                | "editor.redo"
                | "editor.close"
                | "editor.open_file"
                | "editor.save"
                | "editor.recovery_list"
                | "editor.recover"
        ) && !(trusted_file_selection && request.method == "editor.bind_file")
        {
            return Err(HostFailure::host(
                "method_not_supported",
                "editor method not enabled",
            ));
        }
        if request.session_id.is_some() != request.expected_revision.is_some() {
            return Err(HostFailure::host(
                "invalid_scope",
                "session and revision must be paired",
            ));
        }
        self.domain_request(request)
    }

    fn domain_request(self: &Arc<Self>, request: EditorRequest) -> HostResult<Value> {
        let running = {
            let state = self.state.lock().unwrap();
            if state.lifecycle != Lifecycle::Ready {
                return Err(HostFailure::host("not_ready", "sidecar is not ready"));
            }
            let running = Arc::clone(state.running.as_ref().unwrap());
            if running.instance_id != request.backend_instance_id {
                return Err(HostFailure::host(
                    "stale_instance",
                    "reopen the session or scene after restart",
                ));
            }
            running
        };
        let scope = request.session_id.as_deref().zip(request.expected_revision);
        self.request_scoped(
            &running,
            &request.method,
            request.params,
            self.config.request_timeout,
            false,
            scope,
        )
        .map(|result| result.expect("domain request must be enqueued"))
    }

    pub fn ping(self: &Arc<Self>, nonce: String) -> HostResult<Value> {
        let running = {
            let state = self.state.lock().unwrap();
            if state.lifecycle != Lifecycle::Ready {
                return Err(HostFailure::host("not_ready", "sidecar is not ready"));
            }
            Arc::clone(state.running.as_ref().unwrap())
        };
        self.request_on(
            &running,
            "system.ping",
            json!({"nonce": nonce}),
            self.config.request_timeout,
        )
    }

    pub fn stop(self: &Arc<Self>, reason: &str) -> HostResult<BridgeStatus> {
        let running = {
            let mut state = self.state.lock().unwrap();
            let Some(running) = state.running.as_ref().cloned() else {
                state.lifecycle = Lifecycle::Stopped;
                return Ok(self.status_unlocked(&state));
            };
            state.lifecycle = Lifecycle::Stopping;
            running
        };
        running.stopping.store(true, Ordering::SeqCst);
        let deadline = Instant::now() + self.config.shutdown_timeout;
        let _ = self.request_on(
            &running,
            "system.shutdown",
            json!({"reason": reason}),
            self.config.shutdown_timeout,
        );
        let mut forced = false;
        loop {
            if running
                .child
                .lock()
                .unwrap()
                .try_wait()
                .ok()
                .flatten()
                .is_some()
            {
                break;
            }
            if Instant::now() >= deadline {
                forced = true;
                let _ = running.child.lock().unwrap().kill();
                let _ = running.child.lock().unwrap().wait();
                break;
            }
            thread::sleep(Duration::from_millis(10));
        }
        Self::fail_pending(
            &running,
            HostFailure::host("backend_stopped", "sidecar stopped"),
        );
        {
            let mut state = self.state.lock().unwrap();
            if state
                .running
                .as_ref()
                .is_some_and(|value| value.instance_id == running.instance_id)
            {
                state.running = None;
            }
            state.lifecycle = Lifecycle::Stopped;
            state.last_error = None;
        }
        Self::emit(
            &running.sink,
            "lifecycle",
            Some(&running.instance_id),
            json!({
                "state": "STOPPED",
                "forced": forced,
            }),
        );
        Ok(self.status())
    }

    fn status_unlocked(&self, state: &SupervisorState) -> BridgeStatus {
        let negotiated = state
            .running
            .as_ref()
            .and_then(|running| running.negotiated.lock().unwrap().clone());
        BridgeStatus {
            interface: HOST_INTERFACE,
            state: format!("{:?}", state.lifecycle).to_uppercase(),
            backend_instance_id: state
                .running
                .as_ref()
                .map(|running| running.instance_id.clone()),
            bridge_interface: BRIDGE_INTERFACE,
            sidecar_interface: negotiated
                .as_ref()
                .map(|value| value.sidecar_interface.clone()),
            capabilities: negotiated
                .as_ref()
                .map(|value| value.capabilities.clone())
                .unwrap_or_default(),
            ship_schema: negotiated.as_ref().map(|value| value.ship_schema.clone()),
            max_frame_bytes: negotiated.as_ref().map(|value| value.max_frame_bytes),
            last_error: state.last_error.clone(),
        }
    }

    pub fn restart(self: &Arc<Self>, sink: EventSink) -> HostResult<BridgeStatus> {
        let _ = self.stop("host_restart")?;
        self.start(sink)
    }

    pub fn force_stop(&self) {
        let running = {
            let mut state = self.state.lock().unwrap();
            let running = state.running.take();
            state.lifecycle = Lifecycle::Stopped;
            state.last_error = None;
            running
        };
        if let Some(running) = running {
            running.stopping.store(true, Ordering::SeqCst);
            Self::fail_pending(
                &running,
                HostFailure::host("backend_stopped", "sidecar stopped"),
            );
            let _ = running.child.lock().unwrap().kill();
            let _ = running.child.lock().unwrap().wait();
        }
    }
}

impl Drop for BackendSupervisor {
    fn drop(&mut self) {
        let running = self
            .state
            .get_mut()
            .ok()
            .and_then(|state| state.running.take());
        if let Some(running) = running {
            running.stopping.store(true, Ordering::SeqCst);
            let _ = running.child.lock().unwrap().kill();
            let _ = running.child.lock().unwrap().wait();
        }
    }
}

fn fail_weak(supervisor: &Weak<BackendSupervisor>, running: &RunningBackend, error: HostFailure) {
    if let Some(supervisor) = supervisor.upgrade() {
        supervisor.fail_instance(&running.instance_id, error);
    }
}

fn fail_from_protocol(
    supervisor: &Weak<BackendSupervisor>,
    running: &RunningBackend,
    error: super::protocol::ProtocolFailure,
) {
    fail_weak(
        supervisor,
        running,
        HostFailure::protocol(error.code, error.path, error.message),
    );
}

#[cfg(test)]
mod tests {
    use super::*;

    fn repo_root() -> PathBuf {
        PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("../../..")
    }

    fn fast_supervisor() -> Arc<BackendSupervisor> {
        BackendSupervisor::with_config(
            repo_root(),
            SupervisorConfig {
                startup_timeout: Duration::from_secs(3),
                request_timeout: Duration::from_millis(400),
                idle_ping_interval: Duration::from_secs(60),
                idle_ping_timeout: Duration::from_millis(400),
                shutdown_timeout: Duration::from_millis(400),
                max_in_flight: 32,
                max_queued_bytes: 16 * 1024 * 1024,
                max_log_bytes: 16 * 1024,
            },
        )
    }

    fn sink() -> (EventSink, Arc<Mutex<Vec<Value>>>) {
        let events = Arc::new(Mutex::new(Vec::new()));
        let captured = Arc::clone(&events);
        let sink: EventSink = Arc::new(move |event| captured.lock().unwrap().push(event));
        (sink, events)
    }

    fn wait_until(timeout: Duration, predicate: impl Fn() -> bool) -> bool {
        let deadline = Instant::now() + timeout;
        while Instant::now() < deadline {
            if predicate() {
                return true;
            }
            thread::sleep(Duration::from_millis(10));
        }
        predicate()
    }

    #[test]
    fn real_sidecar_start_ping_and_stop() {
        let supervisor = fast_supervisor();
        let (sink, events) = sink();
        assert_eq!(supervisor.start(sink).unwrap().state, "READY");
        assert_eq!(
            supervisor.ping("ping.rust.test".into()).unwrap(),
            json!({"nonce": "ping.rust.test"})
        );
        assert_eq!(supervisor.stop("user_exit").unwrap().state, "STOPPED");
        let events = events.lock().unwrap();
        assert_eq!(
            events
                .iter()
                .filter(|event| event["payload"]["state"] == "READY")
                .count(),
            1
        );
        assert!(
            events
                .iter()
                .any(|event| event["payload"]["state"] == "STOPPED")
        );
    }

    #[test]
    fn real_blank_hull_geometry_batch_and_undo() {
        let supervisor = BackendSupervisor::new(repo_root());
        let (sink, _) = sink();
        let instance = supervisor.start(sink).unwrap().backend_instance_id.unwrap();
        let call = |method: &str, params: Value, session: Option<String>, revision: Option<u64>| {
            supervisor.editor_request(EditorRequest {
                backend_instance_id: instance.clone(),
                method: method.into(),
                params,
                session_id: session,
                expected_revision: revision,
            })
        };
        let created = call(
            "editor.create",
            json!({"resource_id":"user.hull.rust", "name":"Rust H1"}),
            None,
            None,
        )
        .unwrap();
        assert_eq!(created["draft"]["decks"], json!([]));
        assert_eq!(created["dirty"], true);
        let id = created["session_id"].as_str().unwrap().to_string();
        let armor = json!({"material":{"id":"gtw.material.base_armor.armor_steel","version":1},"thickness_m":0});
        let edited = call("editor.command", json!({"command":"hull.batch","arguments":{"commands":[
            {"command":"hull.add_deck","arguments":{"deck_id":"deck.0","level":0,"material":{"id":"gtw.material.structure.armor_steel","version":1}}},
            {"command":"hull.add_region","arguments":{"deck_id":"deck.0","region":{"id":"region.0","vertices_m":[[-10,-10],[10,-10],[10,10],[-10,10]],"edge_armor":[armor.clone(),armor.clone(),armor.clone(),armor]}}}
        ]}}), Some(id.clone()), Some(0)).unwrap();
        assert_eq!(edited["preview"]["valid"], true);
        assert_eq!(edited["revision"], 1);
        let undone = call("editor.undo", json!({}), Some(id), Some(1)).unwrap();
        assert_eq!(undone["draft_sha256"], created["draft_sha256"]);
        assert_eq!(undone["dirty"], true);
        supervisor.stop("user_exit").unwrap();
    }

    #[test]
    fn real_tactical_scene_roundtrip_and_scope_rejection() {
        let supervisor = BackendSupervisor::new(repo_root());
        let (events, _) = sink();
        let status = supervisor.start(events).unwrap();
        assert!(status.capabilities.contains(&"tactical.create".into()));
        let instance = status.backend_instance_id.unwrap();
        let request = |method: &str, params: Value| EditorRequest {
            backend_instance_id: instance.clone(), method: method.into(), params,
            session_id: None, expected_revision: None,
        };
        let created = supervisor.tactical_request(request("tactical.create", json!({"scenario_id":"gtw.sample.web.two_ship.v1"}))).unwrap();
        assert_eq!(created["ships"].as_array().unwrap().len(), 2);
        assert_eq!(created["authority_interface"], "gaotian.tactical-scene-timeline/v7alpha1");
        assert_eq!(created["fixed_step"], 0);
        let inspect = || request("tactical.inspect", json!({"scene_id":created["scene_id"],"known_static_sha256":created["static_sha256"]}));
        let read = supervisor.tactical_request(inspect()).unwrap();
        assert_eq!(read["static"], Value::Null);
        assert_eq!(read["ships"], created["ships"]);
        assert_eq!(read, supervisor.tactical_request(inspect()).unwrap());
        assert_eq!(supervisor.tactical_request(request("editor.save", json!({}))).unwrap_err().code, "host.method_not_supported");
        let mut scoped = inspect(); scoped.session_id = Some("editor.1".into()); scoped.expected_revision = Some(0);
        assert_eq!(supervisor.tactical_request(scoped).unwrap_err().code, "host.invalid_scope");
        supervisor.tactical_request(request("tactical.close", json!({"scene_id":created["scene_id"]}))).unwrap();
        assert_eq!(supervisor.tactical_request(inspect()).unwrap_err().code, "tactical.scene_missing");
        let (events, _) = sink();
        supervisor.restart(events).unwrap();
        assert_eq!(supervisor.tactical_request(inspect()).unwrap_err().code, "host.stale_instance");
        supervisor.stop("user_exit").unwrap();
    }

    #[test]
    fn real_editor_scopes_conflicts_restart_and_concurrent_requests() {
        let supervisor = BackendSupervisor::new(repo_root());
        let (sink, _) = sink();
        let status = supervisor.start(Arc::clone(&sink)).unwrap();
        let instance = status.backend_instance_id.unwrap();
        let call = |method: &str, params: Value, session: Option<String>, revision: Option<u64>| {
            supervisor.editor_request(EditorRequest {
                backend_instance_id: instance.clone(),
                method: method.into(),
                params,
                session_id: session,
                expected_revision: revision,
            })
        };
        let resources = call("resource.list", json!({}), None, None).unwrap();
        let key = resources["resources"]
            .as_array()
            .unwrap()
            .iter()
            .find(|entry| entry["editable"] == true)
            .unwrap()["key"]
            .clone();
        let opened = call("editor.open", json!({"resource_key": key}), None, None).unwrap();
        let session = opened["session_id"].as_str().unwrap().to_string();
        let changed = call(
            "editor.command",
            json!({"command": "hull.rename",
            "arguments": {"name": "Rust bridge test"}}),
            Some(session.clone()),
            Some(0),
        )
        .unwrap();
        assert_eq!(changed["revision"], 1);
        assert_eq!(
            call("editor.undo", json!({}), Some(session.clone()), Some(0))
                .unwrap_err()
                .code,
            "editor.revision_conflict"
        );
        assert_eq!(supervisor.status().state, "READY");
        let restored = call("editor.undo", json!({}), Some(session.clone()), Some(1)).unwrap();
        assert_eq!(restored["draft_sha256"], opened["draft_sha256"]);
        let threads: Vec<_> = (0..8)
            .map(|n| {
                let supervisor = Arc::clone(&supervisor);
                thread::spawn(move || supervisor.ping(format!("ping.concurrent.{n}")).unwrap())
            })
            .collect();
        for thread in threads {
            thread.join().unwrap();
        }
        supervisor.restart(sink).unwrap();
        assert_eq!(
            call("editor.inspect", json!({}), Some(session), Some(2))
                .unwrap_err()
                .code,
            "host.stale_instance"
        );
        supervisor.stop("user_exit").unwrap();
    }

    #[test]
    fn real_outfit_three_ship_file_roundtrips() {
        let directory = std::env::temp_dir().join(format!("high-wilderness-o4-{}", Uuid::new_v4()));
        std::fs::create_dir_all(&directory).unwrap();
        let supervisor = BackendSupervisor::new(repo_root());
        supervisor.set_recovery_dir(directory.join("recovery"));
        let (sink, _) = sink();
        let instance = supervisor.start(sink).unwrap().backend_instance_id.unwrap();
        let make = |method: &str, params: Value, state: Option<&Value>| EditorRequest {
            backend_instance_id: instance.clone(),
            method: method.into(),
            params,
            session_id: state.map(|s| s["session_id"].as_str().unwrap().to_string()),
            expected_revision: state.map(|s| s["revision"].as_u64().unwrap()),
        };
        let resources = supervisor
            .editor_request(make("resource.list", json!({}), None))
            .unwrap();
        let outfits: Vec<_> = resources["resources"]
            .as_array()
            .unwrap()
            .iter()
            .filter(|entry| entry["kind"] == "OutfitPlan")
            .collect();
        assert_eq!(outfits.len(), 3);
        for (n, entry) in outfits.into_iter().enumerate() {
            let opened = supervisor
                .editor_request(make(
                    "editor.open",
                    json!({"resource_key":entry["key"]}),
                    None,
                ))
                .unwrap();
            assert_eq!(opened["preview"]["valid"], true);
            assert_eq!(
                opened["preview"]["model"]["layout"]["interface"],
                "gaotian.outfit-layout/v1alpha1"
            );
            assert_eq!(
                opened["preview"]["model"]["weapon_control"]["interface"],
                "gaotian.weapon-control-view/v2alpha1"
            );
            let path = directory.join(format!("outfit-{n}.json"));
            let token = supervisor
                .bind_selected_file(make("save", json!({}), Some(&opened)), path.clone())
                .unwrap();
            let saved = supervisor
                .editor_request(make(
                    "editor.save",
                    json!({"destination_handle":token["destination_handle"],"new_version":false}),
                    Some(&opened),
                ))
                .unwrap();
            assert_eq!(saved["preview"], opened["preview"]);
            assert_eq!(saved["dirty"], false);
            supervisor
                .editor_request(make(
                    "editor.close",
                    json!({"discard_changes":false}),
                    Some(&saved),
                ))
                .unwrap();
            let token = supervisor
                .bind_selected_file(make("open", json!({}), None), path)
                .unwrap();
            let reloaded = supervisor
                .editor_request(make(
                    "editor.open_file",
                    json!({"destination_handle":token["destination_handle"]}),
                    None,
                ))
                .unwrap();
            assert_eq!(reloaded["draft"], opened["draft"]);
            assert_eq!(reloaded["preview"], opened["preview"]);
            assert_eq!(reloaded["dirty"], false);
            supervisor
                .editor_request(make(
                    "editor.close",
                    json!({"discard_changes":false}),
                    Some(&reloaded),
                ))
                .unwrap();
        }
        supervisor.stop("user_exit").unwrap();
        assert!(directory.starts_with(std::env::temp_dir()));
        std::fs::remove_dir_all(directory).unwrap();
    }

    #[test]
    fn file_binding_is_host_only_and_recovery_survives_restart() {
        let directory =
            std::env::temp_dir().join(format!("high-wilderness-w2b-{}", Uuid::new_v4()));
        std::fs::create_dir_all(&directory).unwrap();
        let supervisor = BackendSupervisor::new(repo_root());
        supervisor.set_recovery_dir(directory.join("recovery"));
        let (sink, _) = sink();
        let instance = supervisor
            .start(Arc::clone(&sink))
            .unwrap()
            .backend_instance_id
            .unwrap();
        let make = |method: &str,
                    params: Value,
                    session_id: Option<String>,
                    expected_revision: Option<u64>| EditorRequest {
            backend_instance_id: instance.clone(),
            method: method.into(),
            params,
            session_id,
            expected_revision,
        };
        assert_eq!(
            supervisor
                .editor_request(make(
                    "editor.bind_file",
                    json!({"host_path":"x", "mode":"save"}),
                    None,
                    None
                ))
                .unwrap_err()
                .code,
            "host.method_not_supported"
        );
        let index = supervisor
            .editor_request(make("resource.list", json!({}), None, None))
            .unwrap();
        let key = index["resources"]
            .as_array()
            .unwrap()
            .iter()
            .find(|r| r["editable"] == true)
            .unwrap()["key"]
            .clone();
        let opened = supervisor
            .editor_request(make("editor.open", json!({"resource_key":key}), None, None))
            .unwrap();
        let id = opened["session_id"].as_str().unwrap().to_string();
        supervisor
            .editor_request(make(
                "editor.command",
                json!({"command":"hull.rename", "arguments":{"name":"恢复验收舰"}}),
                Some(id.clone()),
                Some(0),
            ))
            .unwrap();
        let token = supervisor
            .bind_selected_file(
                make("save", json!({}), Some(id.clone()), Some(1)),
                directory.join("ship.json"),
            )
            .unwrap();
        let saved = supervisor
            .editor_request(make(
                "editor.save",
                json!({"destination_handle":token["destination_handle"],"new_version":false}),
                Some(id.clone()),
                Some(1),
            ))
            .unwrap();
        assert_eq!(saved["dirty"], false);
        supervisor
            .editor_request(make(
                "editor.command",
                json!({"command":"hull.rename", "arguments":{"name":"恢复验收舰第二次修改"}}),
                Some(id),
                Some(1),
            ))
            .unwrap();
        let new_instance = supervisor
            .restart(sink)
            .unwrap()
            .backend_instance_id
            .unwrap();
        let list = supervisor
            .editor_request(EditorRequest {
                backend_instance_id: new_instance.clone(),
                method: "editor.recovery_list".into(),
                params: json!({}),
                session_id: None,
                expected_revision: None,
            })
            .unwrap();
        assert_eq!(list["records"].as_array().unwrap().len(), 1);
        let recovered = supervisor
            .editor_request(EditorRequest {
                backend_instance_id: new_instance,
                method: "editor.recover".into(),
                params: json!({"recovery_key":list["records"][0]["key"]}),
                session_id: None,
                expected_revision: None,
            })
            .unwrap();
        assert_eq!(recovered["draft"]["name"], "恢复验收舰第二次修改");
        assert_eq!(recovered["can_save_current"], false);
        supervisor.stop("user_exit").unwrap();
        std::fs::remove_dir_all(directory).unwrap();
    }

    #[test]
    fn malformed_output_fails_closed() {
        let supervisor = fast_supervisor();
        let (sink, events) = sink();
        let instance = "backend.fixture.rust.malformed".to_string();
        let command = BackendCommand::fixture(&repo_root(), &instance, "malformed");
        let error = supervisor
            .start_command(command, instance, sink)
            .unwrap_err();
        assert!(
            matches!(
                error.code.as_str(),
                "bridge.invalid_json" | "bridge.request_timeout"
            ),
            "unexpected error {error:?}; events: {:?}",
            events.lock().unwrap()
        );
        supervisor.force_stop();
    }

    #[test]
    fn startup_timeout_and_shutdown_deadline_are_bounded() {
        let supervisor = fast_supervisor();
        let (sink, _) = sink();
        let instance = "backend.fixture.rust.hang".to_string();
        let command = BackendCommand::fixture(&repo_root(), &instance, "hang");
        let started = Instant::now();
        let error = supervisor
            .start_command(command, instance, sink)
            .unwrap_err();
        assert_eq!(
            error.code, "bridge.request_timeout",
            "unexpected error: {error:?}"
        );
        assert!(started.elapsed() < Duration::from_secs(5));
        supervisor.force_stop();
    }

    #[test]
    fn spawn_failure_is_structured_and_bounded() {
        let supervisor = fast_supervisor();
        let (sink, _) = sink();
        let instance = "backend.fixture.rust.spawn".to_string();
        let command = BackendCommand {
            program: OsString::from("high-wilderness-definitely-missing.exe"),
            arguments: Vec::new(),
            current_dir: repo_root(),
        };
        let error = supervisor
            .start_command(command, instance, sink)
            .unwrap_err();
        assert_eq!(error.code, "host.spawn_failed");
        assert_eq!(error.source, "host");
        assert_eq!(supervisor.status().state, "FAILED");
    }

    #[test]
    fn request_timeout_marks_unknown_outcome_and_fails_instance() {
        let supervisor = fast_supervisor();
        let (sink, _) = sink();
        let instance = "backend.fixture.rust.request_timeout".to_string();
        let command = BackendCommand::fixture(&repo_root(), &instance, "hang_ping");
        supervisor.start_command(command, instance, sink).unwrap();
        let error = supervisor.ping("ping.timeout".into()).unwrap_err();
        assert_eq!(error.code, "bridge.request_timeout");
        assert_eq!(error.details["outcome_unknown"], true);
        assert_eq!(supervisor.status().state, "FAILED");
        supervisor.force_stop();
    }

    #[test]
    fn crash_and_wrong_epoch_fail_only_the_owned_instance() {
        let supervisor = fast_supervisor();
        let (sink, _) = sink();
        let instance = "backend.fixture.rust.crash".to_string();
        let command = BackendCommand::fixture(&repo_root(), &instance, "crash_ping");
        supervisor
            .start_command(command, instance.clone(), Arc::clone(&sink))
            .unwrap();
        assert_eq!(
            supervisor.ping("ping.crash".into()).unwrap_err().code,
            "bridge.backend_exited"
        );
        let restarted = supervisor.start(Arc::clone(&sink)).unwrap();
        assert_ne!(
            restarted.backend_instance_id.as_deref(),
            Some(instance.as_str())
        );
        supervisor.stop("host_restart").unwrap();

        let wrong_instance = "backend.fixture.rust.current_epoch".to_string();
        let command = BackendCommand::fixture(&repo_root(), &wrong_instance, "wrong_instance");
        supervisor
            .start_command(command, wrong_instance, sink)
            .unwrap();
        assert_eq!(
            supervisor.ping("ping.old_epoch".into()).unwrap_err().code,
            "bridge.instance_mismatch"
        );
        supervisor.force_stop();
    }

    #[test]
    fn stderr_flood_is_truncated_without_blocking_protocol() {
        let mut config = fast_supervisor().config.clone();
        config.max_log_bytes = 4096;
        let supervisor = BackendSupervisor::with_config(repo_root(), config);
        let (sink, events) = sink();
        let instance = "backend.fixture.rust.stderr_flood".to_string();
        let command = BackendCommand::fixture(&repo_root(), &instance, "stderr_flood");
        supervisor.start_command(command, instance, sink).unwrap();
        assert_eq!(
            supervisor.ping("ping.after_log_flood".into()).unwrap(),
            json!({"nonce": "ping.after_log_flood"})
        );
        assert!(wait_until(Duration::from_secs(1), || {
            events
                .lock()
                .unwrap()
                .iter()
                .any(|event| event["payload"]["truncated"] == true)
        }));
        let accepted_bytes: usize = events
            .lock()
            .unwrap()
            .iter()
            .filter_map(|event| event["payload"]["text"].as_str())
            .map(str::len)
            .sum();
        assert_eq!(accepted_bytes, 4096);
        supervisor.stop("user_exit").unwrap();
    }

    #[test]
    fn idle_heartbeat_timeout_fails_the_instance() {
        let supervisor = BackendSupervisor::with_config(
            repo_root(),
            SupervisorConfig {
                startup_timeout: Duration::from_secs(3),
                request_timeout: Duration::from_millis(400),
                idle_ping_interval: Duration::from_millis(40),
                idle_ping_timeout: Duration::from_millis(80),
                shutdown_timeout: Duration::from_millis(200),
                max_in_flight: 32,
                max_queued_bytes: 16 * 1024 * 1024,
                max_log_bytes: 4096,
            },
        );
        let (sink, _) = sink();
        let instance = "backend.fixture.rust.heartbeat".to_string();
        let command = BackendCommand::fixture(&repo_root(), &instance, "hang_ping");
        supervisor.start_command(command, instance, sink).unwrap();
        assert!(wait_until(Duration::from_secs(1), || supervisor
            .status()
            .state
            == "FAILED"));
        let status = supervisor.status();
        assert_eq!(status.last_error.unwrap().code, "bridge.request_timeout");
        supervisor.force_stop();
    }

    #[test]
    fn shutdown_uses_one_deadline_and_forces_only_owned_child() {
        let supervisor = fast_supervisor();
        let (sink, events) = sink();
        let instance = "backend.fixture.rust.ignore_shutdown".to_string();
        let command = BackendCommand::fixture(&repo_root(), &instance, "ignore_shutdown");
        supervisor.start_command(command, instance, sink).unwrap();
        let started = Instant::now();
        assert_eq!(supervisor.stop("user_exit").unwrap().state, "STOPPED");
        assert!(started.elapsed() < Duration::from_millis(800));
        assert!(events.lock().unwrap().iter().any(|event| {
            event["payload"]["state"] == "STOPPED" && event["payload"]["forced"] == true
        }));
    }

    #[test]
    fn in_flight_limit_rejects_the_thirty_third_request_before_write() {
        let supervisor = BackendSupervisor::with_config(
            repo_root(),
            SupervisorConfig {
                startup_timeout: Duration::from_secs(3),
                request_timeout: Duration::from_secs(3),
                idle_ping_interval: Duration::from_secs(60),
                idle_ping_timeout: Duration::from_millis(400),
                shutdown_timeout: Duration::from_millis(200),
                max_in_flight: 32,
                max_queued_bytes: 16 * 1024 * 1024,
                max_log_bytes: 4096,
            },
        );
        let (sink, _) = sink();
        let instance = "backend.fixture.rust.capacity".to_string();
        let command = BackendCommand::fixture(&repo_root(), &instance, "hang_ping");
        supervisor.start_command(command, instance, sink).unwrap();
        let mut requests = Vec::new();
        for number in 0..32 {
            let supervisor = Arc::clone(&supervisor);
            requests.push(thread::spawn(move || {
                supervisor.ping(format!("load.{number}"))
            }));
        }
        assert!(wait_until(Duration::from_secs(1), || {
            let state = supervisor.state.lock().unwrap();
            state
                .running
                .as_ref()
                .unwrap()
                .pending
                .lock()
                .unwrap()
                .len()
                == 32
        }));
        assert_eq!(
            supervisor.ping("load.overflow".into()).unwrap_err().code,
            "bridge.busy"
        );
        supervisor.force_stop();
        for request in requests {
            assert!(request.join().unwrap().is_err());
        }
    }

    #[test]
    fn queued_byte_limit_rejects_before_writing_and_leaves_no_pending_request() {
        let supervisor = fast_supervisor();
        let (sink, _) = sink();
        supervisor.start(sink).unwrap();
        let running = supervisor
            .state
            .lock()
            .unwrap()
            .running
            .as_ref()
            .cloned()
            .unwrap();
        running
            .queued_bytes
            .store(supervisor.config.max_queued_bytes - 4, Ordering::SeqCst);
        assert_eq!(
            supervisor.ping("load.byte_limit".into()).unwrap_err().code,
            "bridge.busy"
        );
        assert!(running.pending.lock().unwrap().is_empty());
        running.queued_bytes.store(0, Ordering::SeqCst);
        supervisor.stop("user_exit").unwrap();
    }

    #[test]
    fn domain_error_adapter_preserves_code_path_and_message() {
        let supervisor = fast_supervisor();
        let (sink, _) = sink();
        let instance = "backend.fixture.rust.domain_error".to_string();
        let command = BackendCommand::fixture(&repo_root(), &instance, "domain_error");
        supervisor.start_command(command, instance, sink).unwrap();
        let running = supervisor
            .state
            .lock()
            .unwrap()
            .running
            .as_ref()
            .cloned()
            .unwrap();
        let error = supervisor
            .request_on(
                &running,
                "editor.fixture",
                json!({}),
                Duration::from_millis(400),
            )
            .unwrap_err();
        assert_eq!(error.code, "vessel.fixture_rejected");
        assert_eq!(error.path, "$.fixture");
        assert_eq!(error.message, "受控领域错误");
        assert_eq!(error.source, "domain");
        supervisor.stop("user_exit").unwrap();
    }
}
