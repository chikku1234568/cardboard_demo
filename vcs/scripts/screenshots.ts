/** Headless screenshots of the running UI (default http://localhost:4310) into docs/screenshots/. */
import { chromium } from "playwright-core";
import { mkdirSync } from "node:fs";
import { join } from "node:path";
import { VCS_ROOT } from "../core/paths.js";

const BASE = process.env.VCS_URL ?? "http://localhost:4310";
const OUT = join(VCS_ROOT, "docs", "screenshots");
const P = "proj_launch_week_recap";
mkdirSync(OUT, { recursive: true });

const api = (p: string, init?: RequestInit) => fetch(BASE + p, init).then((r) => r.json());
await api(`/api/p/${P}/reset`, { method: "POST", headers: { "content-type": "application/json" }, body: "{}" });
const st = await api(`/api/p/${P}/state`);
const base = st.refs["rough-cut"].head;
const A = st.refs["ava/tighter-cut"].head;

const browser = await chromium.launch({ executablePath: process.env.CHROME ?? "/usr/bin/google-chrome", args: ["--no-sandbox"] });
const page = await browser.newPage({ viewport: { width: 1480, height: 960 }, deviceScaleFactor: 1 });
const shot = async (name: string) => { await page.waitForTimeout(500); await page.screenshot({ path: join(OUT, name), fullPage: true }); console.log("saved", join("docs/screenshots", name)); };

await page.goto(`${BASE}/?v=ava/tighter-cut&from=${base}`);
await page.getByText("Trimmed 1.2s off the end").waitFor();
await shot("01_diff_A_vs_base.png");

await page.goto(`${BASE}/?v=ava/alt-ending&from=${A}`);
await page.getByText("Split the B-cam clip").waitFor();
await shot("02_diff_D_vs_A_split.png");

await page.goto(`${BASE}/?v=ava/tighter-cut`);
await page.getByText("Pull from another branch").click();
await page.locator(".modal select").selectOption("cara/titles-broll");
await page.getByText("Added a lower-third").waitFor();
await shot("03_pull_dialog_B_into_A.png");
await page.getByRole("button", { name: /Pull 3 changes/ }).click();
await page.getByText("Pulled cleanly").waitFor();
await shot("04_after_clean_pull_A+B.png");

await page.goto(`${BASE}/?view=conflict&into=ava/tighter-cut&src=ben/alt-pacing`);
await page.getByText("Resolve pull").waitFor();
await shot("05_conflict_view.png");
await page.getByRole("button", { name: /Take theirs/ }).click();
await shot("06_conflict_view_theirs_picked.png");
await page.getByRole("button", { name: "Create resolved version" }).click();
await page.getByText(/New version/).waitFor();
await page.getByRole("button", { name: /Render & play/ }).click();
await page.locator("video").waitFor({ timeout: 120000 });
await page.waitForFunction(() => { const v = document.querySelector("video") as HTMLVideoElement; return v && v.readyState >= 2; }, null, { timeout: 60000 }).catch(() => {});
await page.evaluate(() => { const v = document.querySelector("video") as HTMLVideoElement; v.pause(); v.currentTime = 16.5; });
await page.waitForTimeout(1200);
await shot("07_resolved_version_render.png");
await browser.close();
// leave the running demo in its initial state
await api(`/api/p/${P}/reset`, { method: "POST", headers: { "content-type": "application/json" }, body: "{}" });
