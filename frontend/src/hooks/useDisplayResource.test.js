import axios from "axios";
import { displayResource } from "./useDisplayResource";

jest.mock("axios", () => ({ get: jest.fn(), defaults: { headers: { common: {} } } }));
let serial = 0;
const make = () => displayResource(`/display/${serial++}`, 10000);
beforeEach(() => { jest.useFakeTimers(); axios.get.mockReset(); sessionStorage.clear(); });
afterEach(() => { jest.useRealTimers(); });

test("coalesces concurrent GETs and keeps a stable snapshot between updates", async () => {
  let resolve;
  axios.get.mockImplementation(() => new Promise(done => { resolve = done; }));
  const resource = make();
  const first = resource.refresh();
  const second = resource.refresh();
  expect(first).toBe(second);
  expect(axios.get).toHaveBeenCalledTimes(1);
  resolve({ data: { count: 17 } });
  await first;
  expect(resource.getSnapshot().data).toEqual({ count: 17 });
  expect(resource.getSnapshot()).toBe(resource.getSnapshot());
  resource.dispose();
});

test("retains last values on failure but exposes degraded state", async () => {
  const resource = make();
  axios.get.mockResolvedValueOnce({ data: { count: 2 } });
  await resource.refresh();
  const timestamp = resource.getSnapshot().updatedAt;
  axios.get.mockRejectedValueOnce(new Error("unavailable"));
  await resource.refresh();
  expect(resource.getSnapshot().data.count).toBe(2);
  expect(resource.getSnapshot().error.message).toBe("unavailable");
  expect(resource.getSnapshot().updatedAt).toBe(timestamp);
  expect(resource.getSnapshot().refreshing).toBe(false);
  resource.dispose();
});

test("cache cannot cross operator/preview session identities", async () => {
  axios.get.mockResolvedValue({ data: { private: true } });
  const original = displayResource("/identity", 10000);
  await original.refresh();
  sessionStorage.setItem("case_capital_terminal_session_v1", "preview");
  const preview = displayResource("/identity", 10000);
  expect(preview).not.toBe(original);
  expect(preview.getSnapshot().data).toBeNull();
  preview.dispose();
});

test("unsubscribing stops polling; remount within interval reuses saved data", async () => {
  axios.get.mockResolvedValue({ data: { count: 1 } });
  const resource = make();
  const unsubscribe = resource.subscribe(jest.fn());
  await resource.refresh();
  unsubscribe();
  jest.advanceTimersByTime(5000);
  const off = resource.subscribe(jest.fn());
  expect(axios.get).toHaveBeenCalledTimes(1);
  off();
  jest.advanceTimersByTime(30000);
  expect(axios.get).toHaveBeenCalledTimes(1);
  resource.dispose();
});

test("hidden document suppresses background polling and visible return refreshes", async () => {
  const visibility = jest.spyOn(document, "visibilityState", "get");
  visibility.mockReturnValue("visible");
  axios.get.mockResolvedValue({ data: {} });
  const resource = make();
  const off = resource.subscribe(jest.fn());
  await resource.refresh();
  visibility.mockReturnValue("hidden");
  jest.advanceTimersByTime(20000);
  expect(axios.get).toHaveBeenCalledTimes(1);
  visibility.mockReturnValue("visible");
  document.dispatchEvent(new Event("visibilitychange"));
  await resource.refresh();
  expect(axios.get).toHaveBeenCalledTimes(2);
  off(); resource.dispose(); visibility.mockRestore();
});
