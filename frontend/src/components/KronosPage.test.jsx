import React from "react";
import { renderToString } from "react-dom/server.node";
import KronosPage from "./KronosPage";

jest.mock("./CrtShell", () => ({ __esModule: true,
  tokens: { accent: "#ffffff", accent2: "#ffffff", dim: "#999999", muted: "#999999", labelLight: "#ffffff", hairline: "1px solid #333333", cardBg: "#111111" },
  default: ({ title, children }) => <div>{title}{children}</div>,
  CrtShell: ({ title, children }) => <div>{title}{children}</div>,
  Card: ({ title, children }) => <div>{title}{children}</div>,
  Stat: ({ label, value }) => <div>{label}{value}</div>,
}));
jest.mock("axios", () => ({ get: jest.fn(), post: jest.fn() }));

test("Kronos renders its empty research view without missing helper functions", () => {
  const html = renderToString(<KronosPage />);
  expect(html).toContain("KRONOS FORECAST LAB");
  expect(html).toContain("OHLCV BASELINE / RESEARCH");
});

test("Kronos renders a populated backend position with the fixed-horizon contract", () => {
  let stateNumber = 0;
  const forecast = { forecasts: [{ ticker: "LDOS", instrument: "EQUITY", pm_action: "ACCUMULATE", forecast_bias: "BEARISH", forecast_pct: -0.7,
    bear_pct: -1.2, bull_pct: 0.2, confidence: 45, kronos_score: 45, market_value: 100, unrealized_pct: -1.5, anchor_price: 100,
    target_at: "2026-10-08T20:00:00Z", horizons: [{ horizon: "NEXT FULL RTH BAR", forecast_pct: -0.7, cone_low_pct: -1.2, cone_high_pct: 0.2 }],
    tripwires: [], catalysts: [], probabilities: { up: 20, down: 60, flat: 20 }, exit_forecast: { research_only: true } }] };
  const spy = jest.spyOn(React, "useState").mockImplementation(initial => {
    stateNumber += 1;
    return [stateNumber === 10 ? forecast : typeof initial === "function" ? initial() : initial, jest.fn()];
  });
  try {
    const html = renderToString(<KronosPage />);
    expect(html).toContain("LDOS");
    expect(html).toContain("NEXT FULL RTH BAR");
    expect(html).toContain("UNCALIBRATED SCENARIO WEIGHTS");
  } finally {
    spy.mockRestore();
  }
});
