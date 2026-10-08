import React, { act } from "react";
import { createRoot } from "react-dom/client";
import axios from "axios";
import SettingsPage from "./SettingsPage";
import { displayResource } from "../hooks/useDisplayResource";

jest.mock("../config", () => ({ API: "/api", BACKEND_BASE_URL: "http://test.invalid" }));
jest.mock("axios", () => ({ get: jest.fn(), post: jest.fn(), defaults: { headers: { common: {} } } }));
jest.mock("sonner", () => ({ toast: jest.fn() }));
jest.mock("@tauri-apps/api/core", () => ({ invoke: jest.fn() }));
jest.mock("./CrtShell", () => ({
  tokens: { accent: "#c8a84b", accent2: "#5eead4", dim: "#555555", muted: "#999999", labelLight: "#dddddd", hairline: "1px solid #333333", cardBg: "#111111" },
  CrtShell: ({ children }) => <div>{children}</div>,
  Card: ({ title, children, action }) => <section><h2>{title}</h2>{action}{children}</section>,
}));

const paths = [
  "/status", "/admin/pipeline_criteria", "/admin/integration_status", "/desktop/diagnostics",
  "/desktop/update_strategy", "/research/dashboard?limit_scans=180", "/telegram/events?limit=20", "/data_truth/overview",
];
let container;
let root;
let visibility;
let fixtures;
let serial = 0;
const flush = async () => { for (let step = 0; step < 100; step += 1) await Promise.resolve(); };
const renderPage = async element => { await act(async () => { root.render(element || <SettingsPage />); await flush(); }); };
const advance = async ms => { await act(async () => { jest.advanceTimersByTime(ms); await flush(); }); };
const warning = () => container.querySelector('[data-testid="settings-display-warning"]');

beforeEach(() => {
  jest.useFakeTimers();
  jest.setSystemTime(new Date("2026-10-08T15:00:00Z"));
  global.IS_REACT_ACT_ENVIRONMENT = true;
  visibility = jest.spyOn(document, "visibilityState", "get").mockReturnValue("visible");
  sessionStorage.clear();
  axios.defaults.headers.common.Authorization = `settings-test-${serial++}`;
  displayResource("/settings-test-reset").dispose();
  axios.get.mockReset(); axios.post.mockReset();
  fixtures = {
    "/status": { last_scan_at: "2026-10-08T14:00:00Z" },
    "/admin/pipeline_criteria": { pre_filter: [{ rule: "VALID RULE", detail: "valid detail" }], final_screener: [{ key: "INSIDER", weight: 1.25, description: "Verified weight" }], axiom_score_formula: "VALID FORMULA" },
    "/admin/integration_status": { integrations: [{ key: "market", name: "Market data", ok: true, last: "2026-10-08T14:59:00Z" }], jobs: [{ id: "scan", name: "Core Scan", cron: "Server cadence" }], commands: [{ cmd: "/scan", desc: "Scan report" }] },
    "/desktop/diagnostics": { ok: true, app: { version: "9.9.9" }, backend: { pid: 12345 }, checklist: [{ key: "backend", label: "Backend", ok: true }] },
    "/desktop/update_strategy": { channel: "local", current_version: "9.9.9", next_steps: ["Verified next step"] },
    "/research/dashboard?limit_scans=180": { stats: { reconstructed_decisions: 42 }, promotion_gates: [{ name: "Matured outcomes", detail: "Verified gate" }] },
    "/telegram/events?limit=20": { events: [], deliveries: [{ created_at: "2026-10-08T14:59:00Z", batch_type: "scan", title: "Stored delivery", sent: true }] },
    "/data_truth/overview": { truth_grade: "B", decision: "ALLOW" },
  };
  axios.get.mockImplementation(url => Promise.resolve({ data: fixtures[url.slice(4)] }));
  container = document.createElement("div");
  document.body.append(container);
  root = createRoot(container);
});

afterEach(async () => {
  await act(async () => { root.unmount(); await flush(); });
  expect(axios.post).not.toHaveBeenCalled();
  expect(require("@tauri-apps/api/core").invoke).not.toHaveBeenCalled();
  container.remove();
  visibility.mockRestore();
  jest.useRealTimers();
});

test("eight stable display GETs poll through shared cache at 30 seconds; manual controls remain idle", async () => {
  await renderPage();
  expect(axios.get.mock.calls.map(([url]) => url).sort()).toEqual(paths.map(path => `/api${path}`).sort());
  expect(axios.get.mock.calls.every(([, config]) => config.signal && config.timeout === 12000)).toBe(true);
  expect(container.textContent).toContain("Verified gate");
  expect(container.textContent).toContain("1.25");
  expect(warning()).toBeNull();
  for (const id of ["settings-force-backend-boot", "settings-backend-link-refresh", "trigger-learning-btn", "reset-weights-btn", "trigger-pnl-btn", "seed-backtest-btn-settings"]) {
    expect(container.querySelector(`[data-testid="${id}"]`)).not.toBeNull();
  }
  await advance(29999);
  expect(axios.get).toHaveBeenCalledTimes(8);
  await advance(1);
  expect(axios.get).toHaveBeenCalledTimes(16);
});

test("hidden mount and hidden polling issue no reads; visibility return refreshes stale cache", async () => {
  visibility.mockReturnValue("hidden");
  await renderPage(); await advance(60000);
  expect(axios.get).not.toHaveBeenCalled();
  visibility.mockReturnValue("visible");
  await act(async () => { document.dispatchEvent(new Event("visibilitychange")); await flush(); });
  expect(axios.get).toHaveBeenCalledTimes(8);
  visibility.mockReturnValue("hidden");
  await advance(60000);
  expect(axios.get).toHaveBeenCalledTimes(8);
  visibility.mockReturnValue("visible");
  await act(async () => { document.dispatchEvent(new Event("visibilitychange")); await flush(); });
  expect(axios.get).toHaveBeenCalledTimes(16);
});

test("pending reads never overlap their resource or exceed four concurrent GETs", async () => {
  let active = 0;
  let peak = 0;
  const releases = [];
  axios.get.mockImplementation(url => new Promise(resolve => {
    active += 1; peak = Math.max(peak, active);
    releases.push(() => { active -= 1; resolve({ data: fixtures[url.slice(4)] }); });
  }));
  await renderPage(); await advance(90000);
  await act(async () => { window.dispatchEvent(new Event("focus")); document.dispatchEvent(new Event("visibilitychange")); await flush(); });
  expect(axios.get).toHaveBeenCalledTimes(4);
  await act(async () => { releases.splice(0).forEach(release => release()); await flush(); });
  expect(axios.get).toHaveBeenCalledTimes(8);
  await act(async () => { releases.splice(0).forEach(release => release()); await flush(); });
  expect(peak).toBe(4);
  expect(new Set(axios.get.mock.calls.map(([url]) => url)).size).toBe(8);
});

test("unmount aborts active GETs, removes queued reads and stops polling", async () => {
  const releases = [];
  axios.get.mockImplementation(url => new Promise(resolve => releases.push(() => resolve({ data: fixtures[url.slice(4)] }))));
  await renderPage();
  expect(axios.get).toHaveBeenCalledTimes(4);
  const signals = axios.get.mock.calls.map(([, options]) => options.signal);
  await act(async () => { root.unmount(); await flush(); });
  expect(signals.every(signal => signal.aborted)).toBe(true);
  await act(async () => { releases.forEach(release => release()); await flush(); });
  await advance(120000);
  expect(axios.get).toHaveBeenCalledTimes(4);
  expect(container.textContent).toBe("");
  root = createRoot(container);
});

test("partial failure retains last values, names degraded source, and clears on recovery", async () => {
  await renderPage();
  axios.get.mockImplementation(url => url.endsWith("/desktop/diagnostics") ? Promise.reject(new Error("offline")) : Promise.resolve({ data: fixtures[url.slice(4)] }));
  await advance(30000);
  expect(warning().textContent).toContain("could not refresh: Desktop diagnostics");
  expect(warning().textContent).toContain("Last loaded values may be outdated");
  expect(container.textContent).toContain("12345");
  expect(container.textContent).toContain("VALID RULE");
  axios.get.mockImplementation(url => Promise.resolve({ data: fixtures[url.slice(4)] }));
  await advance(30000);
  expect(warning()).toBeNull();
});

test("all-empty object fixtures render safely and warn about missing lists, not zero counts", async () => {
  paths.forEach(path => { fixtures[path] = {}; });
  await renderPage();
  expect(warning().textContent).toContain("Settings lists are unavailable or malformed");
  expect(warning().textContent).toContain("diagnostics.checklist");
  expect(warning().textContent).toContain("admin.integrations");
  expect(warning().textContent).toContain("criteria.final_screener");
  expect(container.textContent).toContain("INTEGRATION STATUS · --");
  expect(container.textContent).not.toMatch(/undefined|NaN/);
});

test("malformed nested lists and rows are guarded without discarding healthy sections", async () => {
  fixtures["/desktop/diagnostics"].checklist = { bad: true };
  fixtures["/desktop/update_strategy"].next_steps = [null, { bad: true }, "Safe step"];
  fixtures["/desktop/update_strategy"].channel = {};
  fixtures["/research/dashboard?limit_scans=180"].promotion_gates = "bad";
  fixtures["/telegram/events?limit=20"] = { events: {}, deliveries: [null, {}, { title: {}, batch_type: {} }] };
  fixtures["/admin/integration_status"] = { integrations: "bad", jobs: [null, { name: {}, cron: {} }], commands: [null, {}, { cmd: {}, desc: {} }] };
  fixtures["/admin/pipeline_criteria"] = { pre_filter: [null, {}, { rule: {}, detail: {} }], final_screener: [null, {}, { key: {}, weight: "1.25", description: {} }], axiom_score_formula: {} };
  await renderPage();
  expect(warning().textContent).toContain("update_strategy.next_steps");
  expect(warning().textContent).toContain("research.promotion_gates");
  expect(warning().textContent).toContain("telegram.deliveries");
  expect(warning().textContent).toContain("admin.commands");
  expect(warning().textContent).toContain("criteria.final_screener");
  expect(container.textContent).toContain("Safe step");
  expect(container.textContent).toContain("12345");
});

test("bad top-level payloads and producer ok:false responses are explicitly degraded", async () => {
  fixtures["/admin/integration_status"] = [];
  fixtures["/desktop/diagnostics"] = null;
  fixtures["/data_truth/overview"] = { ok: false };
  await renderPage();
  expect(warning().textContent).toContain("Malformed settings responses: Integration status, Desktop diagnostics");
  expect(warning().textContent).toContain("could not refresh: Data truth");
});

test("scoped grids use container-bounded tracks and long row values wrap", async () => {
  fixtures["/desktop/diagnostics"].backend.url = `https://test.invalid/${"long".repeat(100)}`;
  await renderPage();
  const grids = container.querySelectorAll(".settings-display-grid");
  expect(grids).toHaveLength(4);
  grids.forEach(grid => expect(grid.style.gridTemplateColumns).toMatch(/repeat\(auto-fit, minmax\(min\(\d+px, 100%\), 1fr\)\)/));
  const label = Array.from(container.querySelectorAll("span")).find(span => span.textContent === "BACKEND URL");
  expect(label.parentElement.style.flexWrap).toBe("wrap");
  expect(label.parentElement.style.overflowWrap).toBe("anywhere");
});

test("fresh remount reuses cached values without duplicating GETs", async () => {
  await renderPage();
  await act(async () => { root.render(null); await flush(); });
  await renderPage();
  expect(axios.get).toHaveBeenCalledTimes(8);
  expect(container.textContent).toContain("VALID RULE");
  expect(warning()).toBeNull();
});
