import React, { act, useMemo, useState } from "react";
import { createRoot } from "react-dom/client";
import axios from "axios";
import useDisplayBatch from "./useDisplayBatch";
import { displayResource, refreshActiveDisplays } from "./useDisplayResource";

jest.mock("axios", () => ({ get: jest.fn(), post: jest.fn(), defaults: { headers: { common: {} } } }));
let root, container, serial = 0;
const flush = async () => { for (let i = 0; i < 80; i += 1) await Promise.resolve(); };
function Workspace() {
  const [fast, setFast] = useState(null);
  const [slow, setSlow] = useState(null);
  const [tab, setTab] = useState("Positions");
  const sources = useMemo(() => [["/fast", setFast], ["/slow", setSlow]], []);
  const batch = useDisplayBatch("/test", sources, 30000);
  return <div><button onClick={() => setTab("Research")}>Research</button>
    <span>{tab}</span><span data-testid="fast">{fast?.value || "pending"}</span>
    <span data-testid="slow">{slow?.value || "pending"}</span>
    <span data-testid="loading">{String(batch.loading)}</span>
    <span data-testid="failed">{batch.failed}</span></div>;
}
beforeEach(() => {
  global.IS_REACT_ACT_ENVIRONMENT = true;
  axios.defaults.headers.common.Authorization = `batch-${serial++}`;
  displayResource("/test/reset").dispose();
  axios.get.mockReset(); axios.post.mockReset();
  container = document.createElement("div"); document.body.append(container); root = createRoot(container);
});
afterEach(async () => {
  await act(async () => { root.unmount(); await flush(); });
  container.remove(); expect(axios.post).not.toHaveBeenCalled();
});
test("fast panel paints without waiting for a slow auxiliary endpoint", async () => {
  let release;
  axios.get.mockImplementation(url => url.endsWith("/fast") ? Promise.resolve({ data: { value: "FAST" } }) : new Promise(resolve => { release = resolve; }));
  await act(async () => { root.render(<Workspace />); await flush(); });
  expect(container.querySelector('[data-testid="fast"]').textContent).toBe("FAST");
  expect(container.querySelector('[data-testid="slow"]').textContent).toBe("pending");
  expect(container.querySelector('[data-testid="loading"]').textContent).toBe("false");
  await act(async () => { release({ data: { value: "SLOW" } }); await flush(); });
  expect(container.querySelector('[data-testid="slow"]').textContent).toBe("SLOW");
});
test("global display refresh preserves tab selection and uses only GETs", async () => {
  axios.get.mockResolvedValue({ data: { value: "first" } });
  await act(async () => { root.render(<Workspace />); await flush(); });
  await act(async () => container.querySelector("button").click());
  axios.get.mockResolvedValue({ data: { value: "second" } });
  await act(async () => { await refreshActiveDisplays(); await flush(); });
  expect(container.textContent).toContain("Research");
  expect(container.querySelector('[data-testid="fast"]').textContent).toBe("second");
  expect(axios.get).toHaveBeenCalledTimes(4);
});
test("failed refresh retains visible data while showing failure count", async () => {
  axios.get.mockResolvedValue({ data: { value: "previous" } });
  await act(async () => { root.render(<Workspace />); await flush(); });
  axios.get.mockRejectedValue(new Error("offline"));
  await act(async () => { await refreshActiveDisplays(); await flush(); });
  expect(container.querySelector('[data-testid="fast"]').textContent).toBe("previous");
  expect(container.querySelector('[data-testid="failed"]').textContent).toBe("2");
});
test("reopening a workspace uses session cache without refetching within cadence", async () => {
  axios.get.mockResolvedValue({ data: { value: "cached" } });
  await act(async () => { root.render(<Workspace />); await flush(); });
  await act(async () => { root.unmount(); await flush(); });
  root = createRoot(container);
  await act(async () => { root.render(<Workspace />); await flush(); });
  expect(container.querySelector('[data-testid="fast"]').textContent).toBe("cached");
  expect(axios.get).toHaveBeenCalledTimes(2);
});
