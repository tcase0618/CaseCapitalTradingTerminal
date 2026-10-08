import React, { act } from "react";
import { createRoot } from "react-dom/client";
import axios from "axios";
import CaseCourtPage from "./CaseCourtPage";
import { displayResource } from "../hooks/useDisplayResource";

jest.mock("../config", () => ({ API: "/api" }));
jest.mock("axios", () => ({ get: jest.fn(), post: jest.fn(), defaults: { headers: { common: {} } } }));
jest.mock("./CrtShell", () => ({
  tokens: { accent: "#c8a84b", accent2: "#5eead4", dim: "#888888", muted: "#999999", labelLight: "#ffffff", hairline: "1px solid #333333", cardBg: "#111111", cardBgHi: "#222222" },
  CrtShell: ({ children, headerRight }) => <main>{headerRight}{children}</main>,
  Card: ({ title, children }) => <section><h2>{title}</h2>{children}</section>,
  Stat: ({ label, value }) => <div>{label}: {value}</div>,
}));
jest.mock("./Institutional", () => ({ DataConfidenceStrip: () => null }));

let container, root, visibility;
let serial = 0;
const flush = async () => { for (let i = 0; i < 100; i += 1) await Promise.resolve(); };
const mount = async () => { await act(async () => { root.render(<CaseCourtPage />); await flush(); }); };
const click = async text => {
  const button = [...container.querySelectorAll("button")].find(node => node.textContent.trim() === text);
  expect(button).toBeDefined();
  await act(async () => { button.click(); await flush(); });
};
const advance = async ms => { await act(async () => { jest.advanceTimersByTime(ms); await flush(); }); };
const change = async (node, value) => {
  await act(async () => {
    const setter = Object.getOwnPropertyDescriptor(Object.getPrototypeOf(node), "value").set;
    setter.call(node, value);
    node.dispatchEvent(new Event("change", { bubbles: true }));
    await flush();
  });
};
beforeEach(() => {
  jest.useFakeTimers();
  jest.setSystemTime(new Date("2026-10-08T15:00:00Z"));
  global.IS_REACT_ACT_ENVIRONMENT = true;
  visibility = jest.spyOn(document, "visibilityState", "get").mockReturnValue("visible");
  sessionStorage.clear();
  axios.defaults.headers.common.Authorization = "CaseCourtPage-test-" + serial++;
  displayResource("/CaseCourtPage-test-reset").dispose();
  axios.get.mockReset();
  axios.post.mockReset();
  axios.get.mockImplementation(url => Promise.resolve({ data: fixture(url) }));
  container = document.createElement("div");
  document.body.append(container);
  root = createRoot(container);
});
afterEach(async () => {
  await act(async () => { root.unmount(); await flush(); });
  container.remove();
  visibility.mockRestore();
  jest.useRealTimers();
});

const trial = { ticker: "AAPL", case_id: "aapl-1", judge: { advisory_posture: "COURT_SUPPORTS_PM", affirmative_defense_classes: "bad", dispositive_flags: [null] }, defense: { points: [null] }, prosecution: { points: {} }, witnesses: {}, appeal_triggers: {}, mini_trials: [null, { name: "DATA", missing_required: {} }], exhibits: [null, { key: "a", claims: [null] }] };
const fixture = url => {
  if (url.includes("/sessions")) return { sessions: [null, { session_id: "historical", trials: 1 }] };
  if (url.includes("/record")) return { rows: {}, graded: 0 };
  return { session_id: "latest-1", trials: [null, trial], source: "persisted", summary: {} };
};

test("all Court tabs tolerate malformed nested evidence arrays; record is deferred", async () => {
  await mount();
  expect(axios.get.mock.calls.some(([url]) => url.includes("/record"))).toBe(false);
  await click("COURT DOCS");
  expect(container.textContent).toContain("EVIDENCE EXHIBITS");
  await click("ADVISORY ALIGNMENT");
  expect(axios.get.mock.calls.some(([url]) => url.includes("/record?days=30"))).toBe(true);
  expect(container.textContent).toContain("30D COURT RECORD");
  await click("DOCKET");
  expect(container.textContent).toContain("JUDGE RULING / AAPL");
  expect(axios.post).not.toHaveBeenCalled();
});

test("latest mode remains latest rather than latching a response session id", async () => {
  await mount();
  expect(container.querySelector("select").value).toBe("");
  expect(axios.get).toHaveBeenCalledTimes(2);
  axios.get.mockClear();
  await advance(60000);
  expect(axios.get.mock.calls.map(([url]) => url).sort()).toEqual([
    "/api/case_court/latest?limit=30", "/api/case_court/sessions?limit=16"
  ]);
});

test("one session selection issues one encoded read, and cancels old subscription", async () => {
  await mount();
  axios.get.mockClear();
  await change(container.querySelector("select"), "historical");
  expect(axios.get.mock.calls.map(([url]) => url)).toEqual(["/api/case_court/latest?limit=30&session_id=historical"]);
});

test("hidden tabs pause Court polling and unmount aborts outstanding reads", async () => {
  await mount();
  visibility.mockReturnValue("hidden");
  axios.get.mockClear();
  await advance(120000);
  expect(axios.get).not.toHaveBeenCalled();
  visibility.mockReturnValue("visible");
  const signals = [];
  axios.get.mockImplementation((url, config) => new Promise(() => signals.push(config.signal)));
  await act(async () => { document.dispatchEvent(new Event("visibilitychange")); await flush(); });
  expect(signals).toHaveLength(2);
  await act(async () => { root.unmount(); await flush(); });
  expect(signals.every(signal => signal.aborted)).toBe(true);
});

test("failed refresh reads retain last docket and display a warning", async () => {
  await mount();
  axios.get.mockRejectedValue(new Error("offline"));
  await advance(60000);
  expect(container.querySelector('[role="alert"]').textContent).toContain("Court display unavailable");
  expect(container.textContent).toContain("AAPL");
});

test("RUN COURT remains an explicit manual command with original params", async () => {
  axios.post.mockResolvedValue({ data: { session_id: "historical", trials: [trial], summary: {} } });
  await mount();
  expect(axios.post).not.toHaveBeenCalled();
  await click("RUN COURT");
  expect(axios.post.mock.calls).toEqual([["/api/case_court/refresh", null, { params: { limit: 30 }, timeout: 30000 }]]);
});
