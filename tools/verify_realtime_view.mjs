// Real Python worker + actual React/Pixi browser. Rust IPC is tested separately.
import assert from "node:assert/strict";
import { createRequire } from "node:module";
import { spawn } from "node:child_process";
import { createInterface } from "node:readline";
import { mkdir, writeFile } from "node:fs/promises";
import path from "node:path";
const require = createRequire(path.join(process.env.HW_BROWSER_MODULES, "package.json"));
const { chromium } = require("playwright");
const out = path.resolve(process.env.HW_E3B_OUT ?? "artifacts/t3a-realtime-experiment/e3b-20260909-browser");
await mkdir(out, { recursive: false });
const backend = spawn(process.env.HW_PYTHON ?? "python", ["-X", "utf8", "-m", "backend.high_wilderness_sidecar", "--instance-id", "backend.e3bbrowser"], { cwd: process.cwd(), windowsHide: true });
let sequence = 0, lostControl = false, scene = null;
const pending = new Map(), errors = [], checks = [], observations = [];
backend.stderr.on("data", data => errors.push(String(data)));
createInterface({ input: backend.stdout }).on("line", line => {
  const value = JSON.parse(line), p = pending.get(value.request_id);
  if (p) { pending.delete(value.request_id); clearTimeout(p.timer); value.ok ? p.resolve(value.result) : p.reject(value.error); }
});
function request(method, params, scope = {}) {
  return new Promise((resolve, reject) => {
    const id = `req.${++sequence}`;
    const timer = setTimeout(() => { pending.delete(id); reject(new Error(`Timeout ${method}`)); }, 20000);
    pending.set(id, { resolve, reject, timer });
    backend.stdin.write(JSON.stringify({ interface: "gaotian.web-bridge/v1alpha1", kind: "request", backend_instance_id: "backend.e3bbrowser",
      request_id: id, session_id: null, expected_revision: null, ...scope, method, params }) + "\n");
  });
}
let browser;
try {
  await request("system.hello", { client_name: "e3bbrowser", client_version: "1", supported_interfaces: ["gaotian.web-bridge/v1alpha1"], required_capabilities: ["tactical.realtime.create"] });
  browser = await chromium.launch({ channel: "msedge", headless: true });
  const page = await browser.newPage({ viewport: { width: 1440, height: 1100 } });
  page.on("pageerror", e => errors.push(e.message));
  await page.route("**/e3b-test.html", async route => {
    const response = await route.fetch();
    await route.fulfill({ response, headers: { ...response.headers(), "content-security-policy":
      "default-src 'self'; connect-src 'self' ws://127.0.0.1:1421; img-src 'self' data:; style-src 'self' 'unsafe-inline'; script-src 'self' 'unsafe-inline'" } });
  });
  await page.exposeFunction("__e3b_request", async req => {
    const result = await request(req.method, req.params, { session_id: req.session_id, expected_revision: req.expected_revision });
    if (result.interface === "gaotian.realtime-view/e3b-v1alpha1") {
      scene = result;
      observations.push({ step: result.status.fixed_step, view_step: result.view.fixed_step, running: result.status.running,
        generation: result.status.generation, input: result.status.highest_input_sequence });
    }
    if (lostControl && req.method === "tactical.realtime.control") { lostControl = false; throw new Error("fixture: lost reply after acceptance"); }
    return result;
  });
  await page.goto(process.env.HW_E3B_URL ?? "http://127.0.0.1:1421/e3b-test.html");
  const button = name => page.getByRole("button", { name, exact: true });
  await button("战术视角").click();
  await button("实时试航（实验）").click();
  await button("建立实时场景").click();
  await page.locator(".tactical-canvas canvas").waitFor();
  await button("开始试航").click();
  await page.getByLabel("实时车钟", { exact: true }).selectOption("full");
  await button("执行车钟 / 停止转向").click();
  await page.getByRole("status").filter({ hasText: "操纵 1：已执行" }).waitFor();
  await page.waitForTimeout(1800);
  assert(scene.status.fixed_step > 90, scene.status);
  assert(scene.view.ships[0].speed_mps > 0);
  checks.push("real background flight advances at wall-clock rate with positive speed and actual Pixi geometry");
  await button("持续左转").click();
  await page.getByRole("status").filter({ hasText: "操纵 2：已执行" }).waitFor();
  await page.waitForTimeout(400);
  assert.notEqual(scene.view.ships[0].yaw_rate_radps, 0);
  lostControl = true;
  await button("执行车钟 / 停止转向").click();
  await page.getByRole("status").filter({ hasText: "操纵 3：已执行" }).waitFor();
  assert.equal(scene.status.highest_input_sequence, 3);
  checks.push("turning works; dropped control reply is reconciled without automatic resubmission");
  await button("暂停试航").click();
  const paused = scene.status.fixed_step;
  await page.waitForTimeout(250);
  assert.equal(scene.status.fixed_step, paused);
  await button("开始试航").click();
  await button("舰艇编辑").click();
  await button("战术视角").click();
  await page.waitForTimeout(160);
  assert.equal(scene.status.running, false);
  checks.push("pause and return-to-editor preserve the committed step and cancel held control");
  await page.screenshot({ path: path.join(out, "realtime-view.png"), fullPage: true });
  await button("开始试航").click();
  await page.goto("about:blank");
  await page.waitForTimeout(2300);
  const read = await request("tactical.realtime.read", { scene_id: scene.status.epoch, known_static_sha256: scene.view.static_sha256, ack_inputs: [], ack_events: scene.status.acknowledged_event_sequence });
  assert.equal(read.status.running, false);
  assert.equal(read.status.pause_reason, "disconnected");
  checks.push("loss of view polling pauses the worker through its two-second lease");
  assert.deepEqual(errors, []);
  await writeFile(path.join(out, "result.json"), JSON.stringify({ status: "PASS", scope: "Real Python stdio worker + React/Pixi headless Edge; excludes native Rust/WebView embedding and long runs", checks, observations, errors }, null, 2));
  console.log(JSON.stringify({ status: "PASS", checks }));
} catch (error) {
  if (browser) {
    const p = browser.contexts()[0]?.pages()[0];
    if (p) { await p.screenshot({ path: path.join(out,"failure.png"),fullPage:true }); console.error(await p.locator("body").innerText()); }
  }
  await writeFile(path.join(out,"failure.json"),JSON.stringify({ error:String(error), errors, observations },null,2));
  throw error;
} finally {
  if (browser) await browser.close();
  await request("system.shutdown", { reason: "user_exit" }).catch(() => {});
  backend.kill();
}
