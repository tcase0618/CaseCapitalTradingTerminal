import React from "react";
import { renderToString } from "react-dom/server.node";
import DisplayReadStatus from "./DisplayReadStatus";
jest.mock("./CrtShell", () => ({ tokens: {} }));

test("retrieval status distinguishes missing sources, failed cached reads and real zero data", () => {
  const html = renderToString(<DisplayReadStatus sources={[["/a", null, "Portfolio"], ["/b", null, "Quotes"], ["/c", null, "Scan"]]} states={{
    "/a": { data: { count: 0 }, updatedAt: 1791802800000 },
    "/b": { data: null, error: new Error("offline") },
    "/c": { data: {}, error: new Error("offline"), updatedAt: 1791802800000 },
  }} />);
  expect(new DOMParser().parseFromString(html, "text/html").body.textContent).toContain("2/3 LOADED / 2 FAILED");
  expect(html).toContain("UNAVAILABLE");
  expect(html).toContain("LAST LOADED / FAILED");
  expect(html).toContain("LAST RETRIEVED (ET)");
  expect(html).not.toContain("FRESH");
});
