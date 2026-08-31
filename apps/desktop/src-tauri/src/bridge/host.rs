use std::collections::HashMap;
use std::ffi::OsString;
use std::io::{Read, Write};
use std::path::{Path, PathBuf};
use std::process::{Child, ChildStderr, ChildStdout, Command, Stdio};
use std::sync::atomic::{AtomicBool, AtomicU64, AtomicUsize, Ordering};
use std::sync::{Arc, Condvar, Mutex, Weak, mpsc};
use std::thread;
use std::time::{Duration, Instant};

use serde::Serialize;
use serde_json::{Value, json};
use uuid::Uuid;

use super::protocol::{BRIDGE_INTERFACE, FrameDecoder, encode_message};

const HOST_INTERFACE: &str = "gaotian.desktop-bridge-host/v1alpha1";
const EVENT_INTERFACE: &str = "gaotian.desktop-bridge-event/v1alpha1";

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
    result: mpsc::SyncSender<HostResult<Value>>,
}

struct RunningBackend {
    instance_id: String,
    child: Mutex<Child>,
    writer: mpsc::SyncSender<Vec<u8>>,
    pending: Mutex<HashMap<String, PendingRequest>>,
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
        let command = BackendCommand::production(&self.repo_root, &instance_id);
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
        let request_number = running.next_request.fetch_add(1, Ordering::SeqCst);
        let request_id = format!("req.{request_number}");
        let request = json!({
            "backend_instance_id": running.instance_id,
            "expected_revision": null,
            "interface": BRIDGE_INTERFACE,
            "kind": "request",
            "method": method,
            "params": params,
            "request_id": request_id,
            "session_id": null,
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
            pending.insert(request_id.clone(), PendingRequest { result: sender });
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
