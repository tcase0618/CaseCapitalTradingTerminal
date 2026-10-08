import React, { act } from "react";
import { createRoot } from "react-dom/client";
import { renderToString } from "react-dom/server.node";
import axios from "axios";
import { API } from "../config";
import StrategyEvidencePanel from "./StrategyEvidencePanel";
import LearningPage from "./LearningPage";
import useDisplayResource from "../hooks/useDisplayResource";

jest.mock("./CrtShell", () => ({ __esModule: true,
  tokens: { accent: "#ffffff", dim: "#999999", muted: "#999999", labelLight: "#ffffff", hairline: "1px solid #333333", cardBg: "#111111" },
  CrtShell: ({ title, children, headerRight }) => <div>{title}{headerRight}{children}</div>,
  Card: ({ title, children }) => <div>{title}{children}</div>,
  Stat: ({ label, value }) => <div>{label}{value}</div>,
}));
jest.mock("axios", () => ({ get: jest.fn(), post: jest.fn() }));
jest.mock("../hooks/useDisplayResource", () => ({ __esModule: true, default: jest.fn() }));
jest.mock("sonner", () => ({ toast: jest.fn() }));

const scorecard = (horizon, overrides = {}) => ({
  strategy_id: `strategy-${horizon}`, horizon_sessions: horizon,
  episodes: 10, resolved: 8, win_rate: 0.625,
  mean_gross_return_pct: 2.4, mean_excess_return_pct: -0.5,
  mean_net_return_pct: null, ic_days: 3, mean_daily_rank_ic: 0.1234,
  statuses: { RESOLVED: 8, PENDING: 2 }, ...overrides,
});
const saved = overrides => ({
  generated_at: "2026-10-07T12:00:00Z", ok: true, truncated: false,
  observations: 12, scorecards: [scorecard(1), scorecard(5), scorecard(20)],
  limitations: ["Costs have not been measured."], ...overrides,
});

function renderSaved(report, horizon = 5) {
  let index = 0;
  const spy = jest.spyOn(React, "useState").mockImplementation(() => [[report, false, horizon][index++], jest.fn()]);
  try { return renderToString(<StrategyEvidencePanel />); }
  finally { spy.mockRestore(); }
}

beforeEach(() => { jest.clearAllMocks(); useDisplayResource.mockImplementation(() => ({ data: null, error: null, refresh: jest.fn() })); });

test("renders loading without invented performance", () => {
  const html = renderToString(<StrategyEvidencePanel />);
  expect(html).toContain("Loading saved strategy evidence");
  expect(html).not.toContain("0.00%");
  expect(axios.get).not.toHaveBeenCalled();
});

test("renders saved scorecard, provenance, caveats, and unavailable net return", () => {
  const html = renderSaved(saved());
  expect(html).toContain("strategy-5");
  expect(html).not.toContain("strategy-1");
  expect(html).toContain("2026-10-07T12:00:00.000Z");
  expect(html).toContain("62.50%");
  expect(html).toContain("2.40%");
  expect(html).toContain("-0.50%");
  expect(html).toContain("0.1234");
  expect(html).toContain("RESOLVED: 8 | PENDING: 2");
  expect(html).toContain("Costs have not been measured.");
  expect(html).toContain("SPY prior-close benchmark");
  expect(html).toContain("not a simultaneous benchmark or risk-adjusted alpha");
  expect(html).toContain("RESEARCH ONLY");
  const document = new DOMParser().parseFromString(html, "text/html");
  expect(document.querySelectorAll("tbody td")[6].textContent).toBe("--");
  expect(document.querySelector('[aria-label="Strategy evidence table"]').style.overflowX).toBe("auto");
  expect(document.querySelector("section").style.overflowX).toBe("");
});

test("missing metrics remain missing and genuine zero metrics are preserved", () => {
  const html = renderSaved(saved({ observations: null, scorecards: [scorecard(5, {
    win_rate: 0, mean_gross_return_pct: 0, mean_excess_return_pct: null,
    ic_days: null, mean_daily_rank_ic: null,
  })] }));
  expect(html.match(/0\.00%/g)).toHaveLength(2);
  expect(html).not.toContain("null");
  expect(html).not.toContain("undefined");
  const empty = renderSaved(saved({ scorecards: [scorecard(5, { resolved: 0, win_rate: 0.9, ic_days: 0, mean_daily_rank_ic: 0.5 })] }));
  expect(empty).not.toContain("90.00%");
  expect(empty).not.toContain("2.40%");
  expect(empty).not.toContain("0.5000");
});

test("shows version and mode beneath the strategy ID for distinct report groups", () => {
  const html = renderSaved(saved({ scorecards: [
    scorecard(5, { scoring_version: "v1", mode: "SHADOW" }),
    scorecard(5, { scoring_version: "v2", mode: "SHADOW" }),
    scorecard(5, { scoring_version: "v2", mode: "RESEARCH" }),
    scorecard(5),
  ] }));
  const parsed = new DOMParser().parseFromString(html, "text/html");
  const cells = Array.from(parsed.querySelectorAll('tbody th[scope="row"]'));
  expect(cells).toHaveLength(4);
  expect(cells.map(cell => cell.firstChild.textContent)).toEqual(Array(4).fill("strategy-5"));
  expect(cells.map(cell => cell.querySelector("div").textContent)).toEqual([
    "VERSION: v1 | MODE: SHADOW",
    "VERSION: v2 | MODE: SHADOW",
    "VERSION: v2 | MODE: RESEARCH",
    "VERSION: -- | MODE: --",
  ]);
});

test("renders empty, truncated, and incomplete report states", () => {
  const html = renderSaved(saved({ ok: false, truncated: true, scorecards: [] }));
  expect(html).toContain("evidence is not verified");
  expect(html).toContain("TRUNCATED");
  expect(html).toContain("No saved scorecards for this horizon");
});

test("fetches only saved evidence once and filters all horizons locally", async () => {
  global.IS_REACT_ACT_ENVIRONMENT = true;
  axios.get.mockResolvedValue({ data: saved() });
  const container = document.createElement("div");
  const root = createRoot(container);
  try {
    await act(async () => { root.render(<StrategyEvidencePanel />); });
    expect(axios.get).toHaveBeenCalledWith(`${API}/research/strategy-evidence`, expect.objectContaining({ signal: expect.any(AbortSignal), timeout: 12000 }));
    expect(container.textContent).toContain("strategy-5");
    for (const horizon of [1, 20, 5]) {
      await act(async () => {
        const select = container.querySelector("select");
        select.value = String(horizon);
        select.dispatchEvent(new Event("change", { bubbles: true }));
      });
      expect(container.querySelector("tbody").textContent).toContain(`strategy-${horizon}`);
      expect(container.querySelectorAll("tbody tr")).toHaveLength(1);
    }
    expect(axios.get).toHaveBeenCalledTimes(1);
    expect(axios.post).not.toHaveBeenCalled();
  } finally { await act(async () => root.unmount()); }
});

test.each(["rejected", "malformed"])("renders unavailable evidence for %s response", async failure => {
  global.IS_REACT_ACT_ENVIRONMENT = true;
  if (failure === "rejected") axios.get.mockRejectedValue(new Error("offline"));
  else axios.get.mockResolvedValue({ data: {} });
  const container = document.createElement("div");
  const root = createRoot(container);
  try {
    await act(async () => root.render(<StrategyEvidencePanel />));
    expect(container.querySelector('[role="alert"]').textContent).toContain("unavailable");
    expect(container.textContent).not.toContain("0.00%");
  } finally { await act(async () => root.unmount()); }
});

test("Learning page renders evidence and shadow mode while retaining active weights", () => {
  const html = renderToString(<LearningPage />);
  expect(html).toContain("LEARNING ENGINE / SHADOW");
  expect(html).toContain("STRATEGY EVIDENCE / RESEARCH ONLY");
  expect(html).toContain("ACTIVE SIGNAL WEIGHTS");
  expect(html).not.toContain("WEIGHTS ADJUSTED");
  expect(html).not.toContain("OVERALL WIN RATE0.0%");
});

test("Learning page preserves current weights without inventing unsampled win rates", () => {
  useDisplayResource.mockImplementation(url => ({ error: null, refresh: jest.fn(), data:
    url.endsWith("/learning/status") ? { weights: [{ weight_key: "test_signal", default_value: 1, current_value: 1.25, sample_count: 0, win_rate: 0.9 }], last_run: { trades_analyzed: 0, overall_win_rate: 0.9 } }
      : url.endsWith("/learning/combos") ? [{ signal_combo: "test_combo", trade_count: 0, win_rate: null }] : null,
  }));
    const html = renderToString(<LearningPage />);
    expect(html).toContain("1.25");
    expect(html).toContain("TEST SIGNAL");
    expect(html).not.toContain("90%");
    expect(html).not.toContain("90.0%");
    const parsed = new DOMParser().parseFromString(html, "text/html");
    expect(parsed.querySelector('[data-testid="combo-test_combo"]').children[2].textContent).toBe("—");
});
