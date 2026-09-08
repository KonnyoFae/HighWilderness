// Run against the Vite development fixture; does not claim native IPC or simulation coverage.
import assert from "node:assert/strict";
import { createRequire } from "node:module";
import { mkdir, writeFile } from "node:fs/promises";
import path from "node:path";
const require = createRequire(process.env.HW_BROWSER_MODULES ? path.join(process.env.HW_BROWSER_MODULES, "package.json") : import.meta.url);
const { chromium } = require("playwright");
const output = path.resolve("artifacts/t2a");
await mkdir(output, { recursive: true });
const browser = await chromium.launch({ channel: "msedge", headless: true });
const page = await browser.newPage({ viewport: { width: 1440, height: 1100 }, deviceScaleFactor: 1 });
const failures = [], checks = [];
page.on("pageerror", error => failures.push(error.message));
const button = name => page.getByRole("button", { name, exact: true });
try {
  // Exercise the desktop's no-eval policy; Vite's default page has no CSP.
  await page.route("**/t2a-test.html", async route => {
    const response = await route.fetch();
    await route.fulfill({ response, headers: { ...response.headers(), "content-security-policy": "default-src 'self'; connect-src 'self' ws://127.0.0.1:1420; img-src 'self' data:; style-src 'self' 'unsafe-inline'; script-src 'self' 'unsafe-inline'" } });
  });
  await page.goto(process.env.HW_TACTICAL_TEST_URL ?? "http://127.0.0.1:1420/t2a-test.html");
  await button("打开设计").click();
  await page.getByLabel("舰体名称", { exact: true }).fill("跨模式保留的未提交名称");
  await button("绘制区域").click();
  assert(await button("战术视角").isDisabled());
  await button("取消本地编辑").click();
  assert(!(await button("战术视角").isDisabled()));
  await button("关闭会话").click();
  assert(await button("战术视角").isDisabled());
  await button("取消关闭").click();
  checks.push("active drawing and unsaved-close decision block mode switching until finished or cancelled");
  await button("战术视角").click();
  await button("建立两舰场景").click();
  const viewport = page.locator(".tactical-canvas");
  await viewport.locator("canvas").waitFor();
  await page.locator(".tactical-scale").waitFor();
  assert.equal(await button("蓝方测试舰").getAttribute("aria-pressed"), "true");
  assert.match(await page.locator(".tactical-stats").innerText(), /2 层 · 18 个部件/);
  assert.equal(await viewport.locator(".tactical-ship-label").count(), 2);
  checks.push("actual two-ship static projection renders in WebGL under a no-eval CSP with deck/module counts");
  await viewport.screenshot({ path: path.join(output, "two-ship-overview.png") });
  const canvas = await viewport.locator("canvas").elementHandle();
  await button("读取场景状态").click();
  assert(await canvas.evaluate(node => node.isConnected));
  checks.push("matching static hash refresh retains renderer");

  const labelPosition = name => viewport.locator(".tactical-ship-label", { hasText: name }).evaluate(el => ({ x: parseFloat(el.style.left) - 18, y: parseFloat(el.style.top) }));
  const red = await labelPosition("红方测试舰");
  await viewport.click({ position: red });
  assert.equal(await button("红方测试舰").getAttribute("aria-pressed"), "true");
  await button("聚焦所选舰").click();
  await viewport.screenshot({ path: path.join(output, "red-ship-detail.png") });
  const before = await labelPosition("红方测试舰");
  const rect = await viewport.boundingBox();
  await page.mouse.move(rect.x + 30, rect.y + 30);
  await page.mouse.down(); await page.mouse.move(rect.x + 100, rect.y + 75, { steps: 8 }); await page.mouse.up();
  const moved = await labelPosition("红方测试舰");
  assert(Math.abs(moved.x - before.x - 70) < 1); assert(Math.abs(moved.y - before.y - 45) < 1);
  assert.equal(await button("红方测试舰").getAttribute("aria-pressed"), "true");
  await viewport.focus(); await page.keyboard.press("Home");
  await button("战术放大").click(); await button("战术缩小").click();
  checks.push("canvas hit testing, focus, drag without selecting and keyboard/button camera controls");

  const retained = await labelPosition("红方测试舰");
  await button("舰艇编辑").click();
  assert.equal(await page.getByLabel("舰体名称", { exact: true }).inputValue(), "跨模式保留的未提交名称");
  assert.equal(await page.locator(".tactical-canvas canvas").count(), 0);
  await button("战术视角").click();
  await viewport.locator("canvas").waitFor();
  await page.locator(".tactical-scale").waitFor();
  const restored = await labelPosition("红方测试舰");
  assert(Math.abs(restored.x - retained.x) < 1); assert(Math.abs(restored.y - retained.y) < 1);
  assert.equal(await button("红方测试舰").getAttribute("aria-pressed"), "true");
  checks.push("mode switch preserves unsubmitted form, camera and selection; hidden renderer destroyed");

  await button("释放测试场景").click();
  assert.equal(await page.locator(".tactical-canvas canvas").count(), 0);
  await button("建立两舰场景").click();
  await viewport.locator("canvas").waitFor();
  assert.equal(await button("蓝方测试舰").getAttribute("aria-pressed"), "true");
  checks.push("release/recreate resets scene selection and geometry");

  await button("舰艇编辑").click();
  await page.locator("#lose-mode-ack").check();
  await button("战术视角").click();
  await page.getByRole("alert").filter({ hasText: "确认丢失" }).waitFor();
  assert(await page.getByRole("region", { name: "舰艇编辑会话", exact: true }).evaluate(el => el.parentElement.inert));
  await button("舰艇编辑").click();
  assert.equal(await page.getByLabel("舰体名称", { exact: true }).inputValue(), "跨模式保留的未提交名称");
  assert(!(await page.getByRole("region", { name: "舰艇编辑会话", exact: true }).evaluate(el => el.parentElement.inert)));
  checks.push("unknown mode acknowledgement locks editor until idempotent confirmation recovers");

  await button("战术视角").click();
  await viewport.locator("canvas").waitFor();
  await page.setViewportSize({ width: 860, height: 1000 });
  await button("适应全场").click();
  assert(await viewport.evaluate(el => el.scrollWidth <= el.clientWidth));
  await page.locator(".tactical-panel").screenshot({ path: path.join(output, "compact-workspace.png") });
  checks.push("compact viewport resizes without horizontal overflow");
  assert.deepEqual(failures, []);
  await writeFile(path.join(output, "browser-verification.json"), JSON.stringify({ status: "PASS", scope: "browser presentation fixture; no native IPC or live simulation", checks, page_errors: failures }, null, 2) + "\n");
  console.log(JSON.stringify({ status: "PASS", checks }, null, 2));
} catch (error) {
  await page.screenshot({ path: path.join(output, "failure.png"), fullPage: true });
  console.error(await page.locator("body").innerText());
  throw error;
} finally { await browser.close(); }
