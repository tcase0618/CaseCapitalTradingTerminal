import axios from "axios";
import { displayResource } from "./useDisplayResource";

jest.mock("axios", () => ({ get: jest.fn(), defaults: { headers: { common: {} } } }));
let serial = 0;
const make = () => displayResource(`/display/${serial++}`, 10000);
const flush = async () => { for (let step = 0; step < 20; step += 1) await Promise.resolve(); };
beforeEach(() => {
  jest.useFakeTimers(); axios.get.mockReset(); sessionStorage.clear();
  axios.defaults.headers.common.Authorization = `test-${serial++}`;
  displayResource("/test-reset").dispose();
});
afterEach(() => { jest.useRealTimers(); });

test("coalesces concurrent GETs and keeps a stable snapshot between updates", async () => {
  let resolve;
  axios.get.mockImplementation(() => new Promise(done => { resolve = done; }));
  const resource = make();
  const first = resource.refresh();
  const second = resource.refresh();
  expect(first).toBe(second);
  await Promise.resolve();
  expect(axios.get).toHaveBeenCalledTimes(1);
  resolve({ data: { count: 17 } });
  await first;
  expect(resource.getSnapshot().data).toEqual({ count: 17 });
  expect(resource.getSnapshot()).toBe(resource.getSnapshot());
  resource.dispose();
});

test("automatic failures back off; an explicit refresh can retry immediately", async () => {
  axios.get.mockRejectedValue(new Error("timeout"));
  const resource = make();
  await resource.refresh();
  await resource.refresh();
  expect(axios.get).toHaveBeenCalledTimes(1);
  await resource.refresh(true);
  expect(axios.get).toHaveBeenCalledTimes(2);
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

test("idle cache is bounded, expires lazily, and never evicts subscribed resources", async () => {
  axios.get.mockResolvedValue({ data: { count: 1 } });
  const active = displayResource("/active");
  const off = active.subscribe(jest.fn());
  await active.refresh();
  const oldest = displayResource("/oldest");
  for (let index = 0; index < 101; index += 1) displayResource(`/idle/${index}`);
  expect(displayResource("/oldest")).not.toBe(oldest);
  expect(displayResource("/active")).toBe(active);
  const cached = displayResource("/expires");
  jest.setSystemTime(Date.now() + 5 * 60 * 1000);
  expect(displayResource("/expires")).not.toBe(cached);
  expect(displayResource("/active")).toBe(active);
  off(); active.dispose();
});

test("disposed resources are removed and can be recreated", () => {
  const original = displayResource("/recreated");
  original.dispose();
  expect(displayResource("/recreated")).not.toBe(original);
});

test("last unsubscribe cancels queued GET before a concurrency slot opens", async () => {
  const releases = [];
  axios.get.mockImplementation(() => new Promise(resolve => releases.push(resolve)));
  const blockers = Array.from({ length: 4 }, make);
  const reads = blockers.map(resource => resource.refresh());
  await flush();
  const waiting = make();
  const off = waiting.subscribe(jest.fn());
  const queued = waiting.refresh();
  off();
  await queued;
  expect(waiting.getSnapshot().error).toBeNull();
  expect(waiting.getSnapshot().refreshing).toBe(false);
  releases.forEach(resolve => resolve({ data: {} }));
  await Promise.all(reads);
  await flush();
  expect(axios.get).toHaveBeenCalledTimes(4);
  blockers.forEach(resource => resource.dispose()); waiting.dispose();
});

test("session switch removes queued reads and suppresses late old-session results", async () => {
  const releases = [];
  axios.get.mockImplementation(() => new Promise(resolve => releases.push(resolve)));
  const blockers = Array.from({ length: 4 }, make);
  const reads = blockers.map(resource => resource.refresh());
  await flush();
  const waiting = make();
  const queued = waiting.refresh();
  sessionStorage.setItem("case_capital_terminal_session_v1", "new-session");
  const current = displayResource("/new-session");
  await queued;
  expect(axios.get.mock.calls.every(([, options]) => options.signal.aborted)).toBe(true);
  releases.forEach(resolve => resolve({ data: { private: true } }));
  await Promise.all(reads); await flush();
  expect(axios.get).toHaveBeenCalledTimes(4);
  blockers.forEach(resource => expect(resource.getSnapshot().data).toBeNull());
  expect(current.getSnapshot().data).toBeNull(); current.dispose();
});

test("identity is rechecked when a queued read starts without a new resource lookup", async () => {
  const releases = [];
  axios.get.mockImplementation(() => new Promise(resolve => releases.push(resolve)));
  const blockers = Array.from({ length: 4 }, make);
  const reads = blockers.map(resource => resource.refresh());
  await flush();
  const waiting = make();
  const queued = waiting.refresh();
  axios.defaults.headers.common.Authorization = "changed-token";
  releases.forEach(resolve => resolve({ data: { private: true } }));
  await Promise.all([...reads, queued]); await flush();
  expect(axios.get).toHaveBeenCalledTimes(4);
  expect(waiting.getSnapshot().data).toBeNull();
});

test("subscriber cadence uses fastest active interval and slows when it leaves", async () => {
  axios.get.mockResolvedValue({ data: {} });
  const resource = displayResource("/cadence", 30000);
  const slow = resource.subscribe(jest.fn(), 30000);
  await flush();
  const fast = resource.subscribe(jest.fn(), 5000);
  jest.advanceTimersByTime(5000); await flush();
  expect(axios.get).toHaveBeenCalledTimes(2);
  fast();
  jest.advanceTimersByTime(10000); await flush();
  expect(axios.get).toHaveBeenCalledTimes(2);
  jest.advanceTimersByTime(20000); await flush();
  expect(axios.get).toHaveBeenCalledTimes(3);
  slow(); resource.dispose();
});

test("invalid polling intervals cannot create a tight request loop", async () => {
  axios.get.mockResolvedValue({ data: {} });
  const resource = displayResource("/invalid", 0);
  const off = resource.subscribe(jest.fn());
  await flush();
  jest.advanceTimersByTime(10000); await flush();
  expect(axios.get).toHaveBeenCalledTimes(1);
  jest.advanceTimersByTime(20000); await flush();
  expect(axios.get).toHaveBeenCalledTimes(2);
  off(); resource.dispose();
});

test("unmount aborts active read; a remount retries without cancellation backoff", async () => {
  let release;
  axios.get.mockImplementationOnce(() => new Promise(resolve => { release = resolve; }));
  axios.get.mockResolvedValue({ data: { fresh: true } });
  const resource = make();
  const off = resource.subscribe(jest.fn());
  await flush();
  off();
  expect(axios.get.mock.calls[0][1].signal.aborted).toBe(true);
  const remount = resource.subscribe(jest.fn());
  release({ data: { abandoned: true } });
  await flush();
  expect(axios.get).toHaveBeenCalledTimes(2);
  expect(resource.getSnapshot().data).toEqual({ fresh: true });
  expect(resource.getSnapshot().error).toBeNull();
  remount(); resource.dispose();
});

test("automatic recovery clears errors and resets exponential backoff", async () => {
  axios.get.mockRejectedValueOnce(new Error("offline"));
  axios.get.mockResolvedValueOnce({ data: { recovered: true } });
  axios.get.mockRejectedValueOnce(new Error("offline again"));
  const resource = make();
  const off = resource.subscribe(jest.fn());
  await flush();
  jest.advanceTimersByTime(20000); await flush();
  expect(axios.get).toHaveBeenCalledTimes(1);
  jest.advanceTimersByTime(10000); await flush();
  expect(resource.getSnapshot().error).toBeNull();
  expect(resource.getSnapshot().data).toEqual({ recovered: true });
  jest.advanceTimersByTime(10000); await flush();
  expect(axios.get).toHaveBeenCalledTimes(3);
  jest.advanceTimersByTime(20000); await flush();
  expect(axios.get).toHaveBeenCalledTimes(3);
  axios.get.mockResolvedValue({ data: {} });
  jest.advanceTimersByTime(10000); await flush();
  expect(axios.get).toHaveBeenCalledTimes(4);
  off(); resource.dispose();
});

test("persistent failure backoff is capped at two minutes", async () => {
  axios.get.mockRejectedValue(new Error("offline"));
  const resource = make();
  const off = resource.subscribe(jest.fn());
  await flush();
  for (const delay of [30000, 60000, 120000, 120000]) {
    const count = axios.get.mock.calls.length;
    jest.advanceTimersByTime(delay - 10000); await flush();
    expect(axios.get).toHaveBeenCalledTimes(count);
    jest.advanceTimersByTime(10000); await flush();
    expect(axios.get).toHaveBeenCalledTimes(count + 1);
  }
  off(); resource.dispose();
});

test("canceling a queued read does not block surviving reads or exceed four active GETs", async () => {
  const releases = [];
  axios.get.mockImplementation(() => new Promise(resolve => releases.push(resolve)));
  const blockers = Array.from({ length: 4 }, make);
  const reads = blockers.map(resource => resource.refresh());
  await flush();
  const abandoned = make();
  const off = abandoned.subscribe(jest.fn());
  const canceled = abandoned.refresh();
  const survivor = make();
  const survivingRead = survivor.refresh();
  await flush();
  expect(axios.get).toHaveBeenCalledTimes(4);
  off(); await canceled;
  releases[0]({ data: {} });
  await flush();
  expect(axios.get).toHaveBeenCalledTimes(5);
  expect(axios.get.mock.calls[4][0]).not.toBe(axios.get.mock.calls[0][0]);
  releases.slice(1).forEach(resolve => resolve({ data: { survived: true } }));
  await Promise.all([...reads, survivingRead]); await flush();
  expect(survivor.getSnapshot().data).toEqual({ survived: true });
  blockers.forEach(resource => resource.dispose());
  abandoned.dispose(); survivor.dispose();
});
