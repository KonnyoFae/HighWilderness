// Run only on a freshly launched test desktop with no editor session or scene.
import assert from "node:assert/strict";
import { createRequire } from "node:module";
import { mkdir, writeFile } from "node:fs/promises";
import path from "node:path";
const require = createRequire(process.env.HW_BROWSER_MODULES ? path.join(process.env.HW_BROWSER_MODULES, "package.json") : import.meta.url);
const { chromium } = require("playwright");
if (!process.env.HW_TACTICAL_CDP) throw new Error("A local test desktop CDP endpoint is required");
const browser = await chromium.connectOverCDP(process.env.HW_TACTICAL_CDP);
const page = browser.contexts().flatMap(c => c.pages()).find(p => p.url().includes("tauri.localhost"));
assert(page, "Expected built Tauri desktop");
const button = name => page.getByRole("button", { name, exact: true });
const output = path.resolve("artifacts/t3a-paused"); await mkdir(output, { recursive: true });
const checks = [], errors = []; page.on("pageerror", error => errors.push(error.message));
try {
  await page.getByTestId("bridge-state").filter({ hasText: "READY" }).waitFor();
  assert.equal(await page.locator('input[aria-label="舰体名称"]').count(), 0, "Do not test in an open editor session");
  await button("战术视角").click();
  assert(await button("建立两舰场景").isEnabled(), "Do not overwrite an existing scene");
  await button("建立两舰场景").click();
  await page.locator(".tactical-canvas canvas").waitFor();
  await page.getByLabel("车钟", { exact: true }).selectOption("full");
  const step = async (name, index) => {
    await button(name).click();
    await page.getByRole("status").filter({ hasText: `输入 ${index} 已执行，推进至第 ${index} 步。` }).waitFor();
  };
  await step("单步推进", 1);
  assert.match(await page.locator(".tactical-panel > .editor-summary").innerText(), /第 1 步 · 0.017 秒/);
  await button("读取场景状态").click();
  assert.match(await page.locator(".tactical-panel > .editor-summary").innerText(), /第 1 步/);
  checks.push("real native step advances exactly 1/60 second and paused read does not advance");
  for (let index = 2; index <= 10; index++) await step("单步推进", index);
  await page.getByText("推进响应与安全限制", { exact: true }).click();
  const engines = await page.locator(".propulsion-tables table").nth(1).locator("tbody tr td:last-child").allTextContents();
  assert(engines.some(n => parseFloat(n) > 0));
  checks.push("real engine response progresses toward full telegraph under v7 safety");
  await button("红方测试舰").click();
  await step("左转单步", 11);
  const yaw = page.locator(".propulsion-tables table").first().locator("tr").filter({ hasText: "左转" });
  assert.equal(await yaw.locator("td").nth(1).innerText(), "25%");
  await step("单步推进", 12);
  assert.equal(await yaw.locator("td").nth(1).innerText(), "0%");
  checks.push("blue direct control remains fixed while inspecting red; normal step clears requested yaw");
  await page.getByLabel("自动线性制动").check();
  await step("单步推进", 13);
  assert.match(await page.locator(".tactical-controls .editor-summary").innerText(), /上一步：自动制动/);
  await page.getByLabel("自动线性制动").uncheck();
  await button("舰艇编辑").click(); await button("战术视角").click();
  assert.match(await page.locator(".tactical-panel > .editor-summary").innerText(), /已暂停 · 第 13 步/);
  assert.equal(await page.getByLabel("车钟", { exact: true }).inputValue(), "full");
  await page.locator(".tactical-controls").screenshot({ path: path.join(output, "native-control-panel.png") });
  checks.push("automatic brake and return from editing retain paused clock and telegraph selection");
  assert.deepEqual(errors, []);
  await writeFile(path.join(output, "native-verification.json"), JSON.stringify({ status: "PASS", scope: "built Tauri WebView2, real Rust/Python v7 paused controls", checks, page_errors: errors, final_fixed_step: 13 }, null, 2) + "\n");
  console.log(JSON.stringify({ status: "PASS", checks }, null, 2));
} catch (error) {
  await page.screenshot({ path: path.join(output, "native-failure.png"), fullPage: true });
  console.error(await page.locator("body").innerText()); throw error;
} finally { await browser.close(); }
