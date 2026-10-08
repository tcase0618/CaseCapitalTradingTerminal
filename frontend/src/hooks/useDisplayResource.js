import { useCallback, useSyncExternalStore } from "react";
import axios from "axios";

const resources = new Map();
const MAX_IDLE_RESOURCES = 100;
const IDLE_TTL = 5 * 60 * 1000;
const normalizeInterval = interval => Number.isFinite(interval) && interval > 0 ? Math.max(1000, interval) : 30000;
let identity;
let activeRequests = 0;
const queue = [];
function scheduleRead(task, signal) {
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
      Promise.resolve().then(task).then(resolve, reject).finally(() => {
        activeRequests -= 1;
        queue.shift()?.();
      });
    };
    if (signal.aborted) { cancel(); return; }
    signal.addEventListener("abort", cancel, { once: true });
    if (activeRequests < 4) start(); else queue.push(start);
  });
}

function sessionIdentity() {
  return `${axios.defaults.headers.common.Authorization || ""}|${sessionStorage.getItem("case_capital_terminal_session_v1") || ""}`;
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
  const publish = patch => {
    if (disposed) return;
    snapshot = { ...snapshot, ...patch };
    listeners.forEach((period, listener) => listener());
  };
  const refresh = (force = false) => {
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
    }, requestController.signal)
      .then(response => {
        syncSession();
        if (disposed || requestController.signal.aborted || identity !== resourceIdentity) return;
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
    if (document.visibilityState !== "hidden" && (snapshot.updatedAt == null || Date.now() - snapshot.updatedAt >= period)) refresh();
  };
  const resetTimer = () => {
    clearInterval(timer);
    if (listeners.size) timer = setInterval(check, Math.min(...listeners.values()));
  };
  const resource = {
    getSnapshot: () => snapshot,
    refresh,
    isIdle: () => !listeners.size && !pending,
    idleSince: () => idleAt,
    subscribe(listener, subscriberInterval = interval) {
      if (disposed) return () => {};
      listeners.set(listener, normalizeInterval(subscriberInterval));
      resetTimer();
      if (listeners.size === 1) {
        document.addEventListener("visibilitychange", check);
        window.addEventListener("focus", check);
      }
      check();
      return () => {
        listeners.delete(listener);
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
    },
  };
  resources.set(url, resource);
  pruneIdleResources();
  return resource;
}

// Explicit opt-in display GETs only. Never used to authorize or transmit orders.
export default function useDisplayResource(url, interval) {
  const resource = displayResource(url, interval);
  const subscribe = useCallback(listener => resource.subscribe(listener, interval), [resource, interval]);
  const state = useSyncExternalStore(subscribe, resource.getSnapshot, resource.getSnapshot);
  const refresh = useCallback(() => resource.refresh(true), [resource]);
  return { ...state, loading: state.data == null && !state.error, refresh };
}
