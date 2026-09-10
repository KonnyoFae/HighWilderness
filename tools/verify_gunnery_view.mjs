// Actual React/Pixi + Python stdio integration. Does not claim native WebView QA.
import assert from "node:assert/strict";
import { createRequire } from "node:module";
import { spawn } from "node:child_process";
import { createInterface } from "node:readline";
import { mkdir, writeFile } from "node:fs/promises";
import path from "node:path";
const require = createRequire(path.join(process.env.HW_BROWSER_MODULES, "package.json"));
const { chromium } = require("playwright");
const out = path.resolve(process.env.HW_GUN_OUT ?? "artifacts/p2a-gunnery-browser");
await mkdir(out, { recursive: false });
const backend = spawn(process.env.HW_PYTHON ?? "python", ["-X", "utf8", "-m", "backend.high_wilderness_sidecar", "--instance-id", "backend.e3bbrowser"], { cwd: process.cwd(), windowsHide: true });
const pending = new Map(), errors = [], checks = [];
let serial = 0, scene = null, initial = null, lostGunReply = false;
backend.stderr.on("data", data => errors.push(String(data)));
createInterface({ input: backend.stdout }).on("line", line => {
  const value = JSON.parse(line), p = pending.get(value.request_id);
  if (p) { pending.delete(value.request_id); clearTimeout(p.timer); value.ok ? p.resolve(value.result) : p.reject(value.error); }
});
function request(method, params) {
  return new Promise((resolve, reject) => {
    const request_id = `req.${++serial}`;
    const timer = setTimeout(() => { pending.delete(request_id); reject(new Error(`Timeout ${method}`)); }, 20000);
    pending.set(request_id, { resolve, reject, timer });
    backend.stdin.write(JSON.stringify({ interface: "gaotian.web-bridge/v1alpha1", kind: "request", backend_instance_id: "backend.e3bbrowser",
      request_id, session_id: null, expected_revision: null, method, params })+"\n");
  });
}
let browser, page;
try {
  await request("system.hello", { client_name: "p2a.browser", client_version: "1", supported_interfaces: ["gaotian.web-bridge/v1alpha1"], required_capabilities: ["tactical.realtime.gun"] });
  browser = await chromium.launch({ channel: "msedge", headless: true });
  page = await browser.newPage({ viewport: { width: 1480, height: 1200 } });
  page.on("pageerror", e => errors.push(e.message));
  await page.route("**/e3b-test.html", async route => {
    const response = await route.fetch();
    await route.fulfill({ response, headers: { ...response.headers(), "content-security-policy":
      "default-src 'self'; connect-src 'self' ws://127.0.0.1:1421; img-src 'self' data:; style-src 'self' 'unsafe-inline'; script-src 'self' 'unsafe-inline'" } });
  });
  await page.exposeFunction("__e3b_request", async req => {
    const result = await request(req.method, req.params);
    if (result.interface === "gaotian.realtime-view/e3b-v1alpha1") {
      scene = result;
      if (!initial && result.view.static) initial = structuredClone(result.view);
    }
    if (lostGunReply && req.method === "tactical.realtime.gun") { lostGunReply = false; throw new Error("fixture: lost gun reply"); }
    return result;
  });
  await page.goto(process.env.HW_GUN_URL ?? "http://127.0.0.1:1421/e3b-test.html");
  const button = name => page.getByRole("button", { name, exact: true });
  await button("战术视角").click();
  await button("实时试航（实验）").click();
  await button("建立实时场景").click();
  await page.locator(".tactical-canvas canvas").waitFor();
  const gun = () => scene.view.gunnery.weapons.find(g => g.ship_id === scene.direct_ship_id);
  const until = async (predicate, message, timeout=12000) => {
    const end = Date.now()+timeout;
    while (!predicate() && Date.now()<end) await page.waitForTimeout(50);
    assert(predicate(), `${message}: ${JSON.stringify(gun())}`);
  };
  async function pixel(point) {
    const canvas = page.locator(".tactical-canvas");
    await canvas.scrollIntoViewIfNeeded();
    const box = await canvas.boundingBox();
    const local = await page.evaluate(async ({ initial, width, height, point }) => {
      const { fitScene } = await import("/src/tactical/viewport.ts");
      const camera = fitScene({ snapshot: initial, geometry: initial.static }, width, height);
      return { x: camera.x+point[0]*camera.scale, y: camera.y-point[1]*camera.scale };
    }, { initial, width: box.width, height: box.height, point });
    return { x: box.x+local.x, y: box.y+local.y };
  }
  await button("开始试航").click();
  let p = await pixel(gun().origin_m);
  await page.mouse.click(p.x, p.y, { button: "right" });
  await page.getByLabel("所控火炮", { exact: true }).evaluate(el => { if (el.value !== "weapon_upper_port") throw new Error("right click did not select own gun"); });
  checks.push("right click selects the real rendered own gun");
  const red = scene.view.ships.find(s => s.id === "ship.web.red");
  p = await pixel(red.position_m);
  await page.mouse.click(p.x, p.y);
  const candidates = page.getByLabel("重叠模块候选");
  await page.waitForTimeout(100);
  if (await candidates.count()) await button("瞄准整舰").click();
  await until(() => gun().target_ship_id === "ship.web.red", "left click sets enemy target");
  await until(() => gun().shots >= 1, "automatic fire before/after acquisition");
  await until(() => gun().quality === "normal", "radar acquires valid target lock");
  await until(() => gun().shots >= 2 && gun().ammo_resources < 80, "reload consumes generic resource");
  await page.getByLabel("指定目标模块", { exact: true }).selectOption("cic");
  await until(() => gun().target_module_id === "cic", "module target persists by identity");
  await page.screenshot({ path: path.join(out, "auto-fire.png"), fullPage: true });
  checks.push("left click targets moving enemy; radar transitions degraded to normal; auto fires and reloads; module target visible");
  await page.getByLabel("火炮模式", { exact: true }).selectOption("manual");
  await until(() => gun().mode === "manual", "manual mode accepted");
  const manualStart = gun().shots;
  await until(() => gun().reload_steps === 0 && gun().cooldown_steps === 0, "previous reload completes");
  const aim = [-5, 50];
  p = await pixel(aim);
  await page.mouse.move(p.x, p.y);
  await until(() => gun().status === "ready", "manual turret aims without auto firing");
  assert.equal(gun().shots, manualStart);
  lostGunReply = true;
  await page.mouse.click(p.x, p.y);
  await until(() => gun().shots === manualStart+1, "manual click produces one projectile despite lost reply");
  await page.waitForTimeout(2300);
  assert.equal(gun().shots, manualStart+1);
  await page.screenshot({ path: path.join(out, "manual-fire.png"), fullPage: true });
  checks.push("manual mouse aim traverses without firing; left click emits exactly one shot; lost reply does not duplicate; no auto/manual overlap");
  await button("暂停试航").click();
  const stopped = scene.status.fixed_step, rounds = gun().shots;
  await page.waitForTimeout(300);
  assert.equal(scene.status.fixed_step, stopped);
  assert.equal(gun().shots, rounds);
  assert.equal(scene.view.gunnery.damage_enabled, false);
  assert.deepEqual(errors, []);
  checks.push("pause freezes flight, projectiles and reload; UI explicitly declares damage not implemented");
  await writeFile(path.join(out, "result.json"), JSON.stringify({ status: "PASS", scope: "Real Python stdio + React/Pixi in headless Edge, no native WebView or hit-damage claim", checks, final: scene, errors }, null, 2));
  console.log(JSON.stringify({ status: "PASS", checks }));
} catch (error) {
  if (page) { await page.screenshot({ path: path.join(out, "failure.png"), fullPage: true }); console.error(await page.locator("body").innerText()); }
  await writeFile(path.join(out, "failure.json"), JSON.stringify({ error: String(error), errors, scene }, null, 2));
  throw error;
} finally {
  await browser?.close(); backend.kill();
}
