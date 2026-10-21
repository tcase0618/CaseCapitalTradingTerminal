import { useCallback, useSyncExternalStore } from "react";
import axios from "axios";
import { DISPLAY_REFRESH_EVENT } from "./useDisplayRefresh";

const resources = new Map();
const MAX_IDLE_RESOURCES = 100;
const IDLE_TTL = 5 * 60 * 1000;
const normalizeInterval = interval => interval === false ? Infinity : Number.isFinite(interval) && interval > 0 ? Math.max(1000, interval) : 30000;
let identity;
let activeRequests = 0;
const queue = [];
function scheduleRead(task, signal, priority = 0) {
  return new Promise((resolve, reject) => {
    const cancel = () => {
      const index = queue.indexOf(start);
      if (index !== -1) queue.splice(index, 1);
      signal.removeEventListener("abort", cancel);
      const error = new Error("Display read canceled");
      error.name = "AbortError";
      reject(error);
    };
    const start = () => {
      signal.removeEventListener("abort", cancel);
      if (signal.aborted) { cancel(); return; }
      activeRequests += 1;
      let abortRead;
      const aborted = new Promise((unused, fail) => {
        abortRead = () => {
          const error = new Error("Display read canceled");
          error.name = "AbortError";
          fail(error);
        };
        signal.addEventListener("abort", abortRead, { once: true });
      });
      // Release the scheduler slot even if a transport ignores cancellation.
      // Late responses still cannot publish through the resource's signal guard.
      Promise.race([Promise.resolve().then(task), aborted]).then(resolve, reject).finally(() => {
        signal.removeEventListener("abort", abortRead);
        activeRequests -= 1;
        queue.shift()?.();
      });
    };
    if (signal.aborted) { cancel(); return; }
    signal.addEventListener("abort", cancel, { once: true });
    start.priority = priority;
    if (activeRequests < 4) start();
    else {
      const index = queue.findIndex(waiting => waiting.priority < priority);
      if (index === -1) queue.push(start); else queue.splice(index, 0, start);
    }
  });
}

function sessionIdentity() {
  let session = "";
  try { session = sessionStorage.getItem("case_capital_terminal_session_v1") || ""; } catch { /* Browser storage can be unavailable. */ }
  return `${axios.defaults.headers.common.Authorization || ""}|${session}`;
}

function syncSession() {
  const nextIdentity = sessionIdentity();
  if (identity !== nextIdentity) {
    const previous = [...resources.values()];
    resources.clear();
    identity = nextIdentity;
    previous.forEach(resource => resource.dispose());
  }
}

function pruneIdleResources() {
  const idle = [...resources.values()].filter(resource => resource.isIdle())
    .sort((a, b) => a.idleSince() - b.idleSince());
  idle.forEach((resource, index) => {
    if (Date.now() - resource.idleSince() >= IDLE_TTL || index < idle.length - MAX_IDLE_RESOURCES) resource.dispose();
  });
}

export function displayResource(url, interval = 30000) {
  syncSession();
  pruneIdleResources();
  if (resources.has(url)) return resources.get(url);
  const resourceIdentity = identity;
  let snapshot = { data: null, error: null, updatedAt: null, refreshing: false };
  let pending = null;
  let timer;
  let controller;
  let disposed = false;
  let failures = 0;
  let retryAfter = 0;
  let idleAt = Date.now();
  const listeners = new Map();
  const priorities = new Map();
  const publish = patch => {
    if (disposed) return;
    snapshot = { ...snapshot, ...patch };
    listeners.forEach((period, listener) => listener());
  };
  const refresh = (force = false, priority = 0) => {
    syncSession();
    if (disposed || pending) return pending || Promise.resolve();
    if (!force && Date.now() < retryAfter) return Promise.resolve();
    controller = new AbortController();
    const requestController = controller;
    publish({ refreshing: true });
    pending = scheduleRead(() => {
      syncSession();
      if (disposed || requestController.signal.aborted) throw new Error("Display subscription disposed");
      return axios.get(url, { timeout: 12000, signal: requestController.signal });
    }, requestController.signal, priority)
      .then(response => {
        syncSession();
        if (disposed || requestController.signal.aborted || identity !== resourceIdentity) return;
        if (response.data == null || response.data?.error) throw new Error("Display source returned an unavailable payload");
        failures = 0; retryAfter = 0;
        publish({ data: response.data, error: null, updatedAt: Date.now() });
      })
      .catch(error => {
        syncSession();
        if (disposed || requestController.signal.aborted) return;
        failures += 1;
        retryAfter = Date.now() + Math.min(120000, 15000 * (2 ** failures));
        if (!disposed) publish({ error });
      })
      .finally(() => {
        pending = null; controller = null;
        publish({ refreshing: false });
        if (!listeners.size) { idleAt = Date.now(); pruneIdleResources(); }
        else if (requestController.signal.aborted) check();
      });
    return pending;
  };
  const check = () => {
    syncSession();
    if (disposed || !listeners.size) return;
    const period = Math.min(...listeners.values());
    if (document.visibilityState !== "hidden" && (snapshot.updatedAt == null || Date.now() - snapshot.updatedAt >= period)) refresh(false, Math.max(...priorities.values()));
  };
  const resetTimer = () => {
    clearInterval(timer);
    const period = Math.min(...listeners.values());
    if (listeners.size && Number.isFinite(period)) timer = setInterval(check, period);
  };
  const resource = {
    getSnapshot: () => snapshot,
    refresh,
    isIdle: () => !listeners.size && !pending,
    idleSince: () => idleAt,
    subscribe(listener, subscriberInterval = interval, priority = 0) {
      if (disposed) return () => {};
      listeners.set(listener, normalizeInterval(subscriberInterval));
      priorities.set(listener, priority);
      resetTimer();
      if (listeners.size === 1) {
        document.addEventListener("visibilitychange", check);
        window.addEventListener("focus", check);
      }
      check();
      return () => {
        listeners.delete(listener);
        priorities.delete(listener);
        resetTimer();
        if (!listeners.size) {
          idleAt = Date.now();
          controller?.abort();
          document.removeEventListener("visibilitychange", check);
          window.removeEventListener("focus", check);
          pruneIdleResources();
        }
      };
    },
    dispose() {
      if (disposed) return;
      disposed = true;
      if (resources.get(url) === resource) resources.delete(url);
      controller?.abort();
      clearInterval(timer);
      document.removeEventListener("visibilitychange", check);
      window.removeEventListener("focus", check);
      snapshot = { data: null, error: null, updatedAt: null, refreshing: false };
      listeners.forEach((period, listener) => listener());
      listeners.clear();
      priorities.clear();
    },
  };
  resources.set(url, resource);
  pruneIdleResources();
  return resource;
}

// Explicit opt-in display GETs only. Never used to authorize or transmit orders.
export function refreshActiveDisplays() {
  const reads = [...resources.values()].filter(resource => !resource.isIdle()).map(resource => resource.refresh(true, 2));
  window.dispatchEvent(new Event(DISPLAY_REFRESH_EVENT));
  return Promise.allSettled(reads);
}

export default function useDisplayResource(url, interval, priority = 0) {
  const resource = displayResource(url, interval);
  const subscribe = useCallback(listener => resource.subscribe(listener, interval, priority), [resource, interval, priority]);
  const state = useSyncExternalStore(subscribe, resource.getSnapshot, resource.getSnapshot);
  const refresh = useCallback(() => resource.refresh(true), [resource]);
  return { ...state, loading: state.data == null && !state.error, refresh };
}
