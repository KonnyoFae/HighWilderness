//! Development test launcher. Commands are available only in an explicit test process.
use std::io::Write;
use std::process::{Command, Stdio};
#[cfg(windows)]
use std::os::windows::process::CommandExt;
use serde_json::Value;

pub fn enabled() -> bool {
    std::env::var("HIGH_WILDERNESS_TESTBENCH").as_deref() == Ok("1")
}

pub fn request(action: &str, scenario: Option<String>, run_id: Option<String>, note: Option<String>) -> Result<Value, String> {
    if !enabled() { return Err("请通过战术测试台入口启动".into()); }
    if !matches!(action, "list" | "create" | "show" | "note") { return Err("未知测试操作".into()); }
    let python = std::env::var_os("HIGH_WILDERNESS_PYTHON").unwrap_or_else(|| "python".into());
    let mut command = Command::new(python);
    command.args(["-X", "utf8", "-m", "tools.tactical_testbench", action]).current_dir(super::repo_root());
    if let Some(value) = scenario { command.args(["--scenario", &value]); }
    if let Some(value) = run_id { command.args(["--run-id", &value]); }
    command.stdin(Stdio::piped()).stdout(Stdio::piped()).stderr(Stdio::piped());
    #[cfg(windows)]
    command.creation_flags(0x08000000);
    let mut child = command.spawn().map_err(|e| e.to_string())?;
    if let Some(mut input) = child.stdin.take() {
        input.write_all(note.unwrap_or_default().as_bytes()).map_err(|e| e.to_string())?;
    }
    let output = child.wait_with_output().map_err(|e| e.to_string())?;
    if !output.status.success() { return Err(String::from_utf8_lossy(&output.stderr).into_owned()); }
    serde_json::from_slice(&output.stdout).map_err(|e| e.to_string())
}
