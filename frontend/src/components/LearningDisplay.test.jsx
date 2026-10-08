import React from "react";
import { renderToString } from "react-dom/server.node";
import LearningPage from "./LearningPage";
import useDisplayResource from "../hooks/useDisplayResource";

jest.mock("../hooks/useDisplayResource", () => ({ __esModule: true, default: jest.fn() }));
jest.mock("./CrtShell", () => ({ tokens: { accent: "#d7bd68", muted: "#7d8796", labelLight: "#ddd" }, CrtShell: ({ children }) => <main>{children}</main>, Card: ({ children, title }) => <section><h2>{title}</h2>{children}</section>, Stat: ({ label, value }) => <div>{label}{value}</div> }));
jest.mock("./StrategyEvidencePanel", () => () => <div>Evidence fixture</div>);
jest.mock("sonner", () => ({ toast: jest.fn() }));

beforeEach(() => useDisplayResource.mockReset());

test("malformed learning lists render an explicit warning rather than crash", () => {
  useDisplayResource.mockImplementation(() => ({ data: {}, error: null, refresh: jest.fn() }));
  expect(renderToString(<LearningPage />)).toContain("Learning data is incomplete or unavailable");
});

test("missing learning data remains renderable through cancellable display reads", () => {
  useDisplayResource.mockImplementation(() => ({ data: null, error: new Error("offline"), refresh: jest.fn() }));
  const html = renderToString(<LearningPage />);
  expect(html).toContain("missing observations are not zero results");
  expect(useDisplayResource).toHaveBeenCalledTimes(5);
  expect(useDisplayResource.mock.calls.every(([, period]) => period === 60000)).toBe(true);
});
