import { useCallback, useSyncExternalStore } from "react";
import axios from "axios";

const resources = new Map();
let identity;
let activeRequests = 0;
const queue = [];
function scheduleRead(task) {
  return new Promise((resolve, reject) => {
    const start = () => {
      activeRequests += 1;
      Promise.resolve().then(task).then(resolve, reject).finally(() => {
        activeRequests -= 1;
        queue.shift()?.();
      });
    };
    if (activeRequests < 4) start(); else queue.push(start);
  });
}

function sessionIdentity() {
  return `${axios.defaults.headers.common.Authorization || ""}|${sessionStorage.getItem("case_capital_terminal_session_v1") || ""}`;
}

export function displayResource(url, interval = 30000) {
  const nextIdentity = sessionIdentity();
  if (identity !== nextIdentity) {
    resources.forEach(resource => resource.dispose());
    resources.clear();
    identity = nextIdentity;
  }
  if (resources.has(url)) return resources.get(url);
  let snapshot = { data: null, error: null, updatedAt: null, refreshing: false };
  let pending = null;
  let timer;
  let controller;
  let disposed = false;
  let failures = 0;
  let retryAfter = 0;
  const listeners = new Set();
  const publish = patch => {
    if (disposed) return;
    snapshot = { ...snapshot, ...patch };
    listeners.forEach(listener => listener());
  };
  const refresh = (force = false) => {
    if (disposed || pending) return pending || Promise.resolve();
    if (!force && Date.now() < retryAfter) return Promise.resolve();
    controller = new AbortController();
    publish({ refreshing: true });
    pending = scheduleRead(() => {
      if (disposed) throw new Error("Display subscription disposed");
      return axios.get(url, { timeout: 12000, signal: controller.signal });
    })
      .then(response => { failures = 0; retryAfter = 0; publish({ data: response.data, error: null, updatedAt: Date.now() }); })
      .catch(error => {
        failures += 1;
        retryAfter = Date.now() + Math.min(120000, 15000 * (2 ** failures));
        if (!disposed) publish({ error });
      })
      .finally(() => { pending = null; publish({ refreshing: false }); });
    return pending;
  };
  const check = () => {
    if (document.visibilityState !== "hidden" && (!snapshot.updatedAt || Date.now() - snapshot.updatedAt >= interval)) refresh();
  };
  const resource = {
    getSnapshot: () => snapshot,
    refresh,
    subscribe(listener) {
      listeners.add(listener);
      if (listeners.size === 1) {
        timer = setInterval(check, interval);
        document.addEventListener("visibilitychange", check);
        window.addEventListener("focus", check);
        check();
      }
      return () => {
        listeners.delete(listener);
        if (!listeners.size) {
          clearInterval(timer);
          document.removeEventListener("visibilitychange", check);
          window.removeEventListener("focus", check);
        }
      };
    },
    dispose() {
      disposed = true;
      controller?.abort();
      clearInterval(timer);
      document.removeEventListener("visibilitychange", check);
      window.removeEventListener("focus", check);
    },
  };
  resources.set(url, resource);
  return resource;
}

// Explicit opt-in display GETs only. Never used to authorize or transmit orders.
export default function useDisplayResource(url, interval) {
  const resource = displayResource(url, interval);
  const state = useSyncExternalStore(resource.subscribe, resource.getSnapshot, resource.getSnapshot);
  const refresh = useCallback(() => resource.refresh(true), [resource]);
  return { ...state, loading: state.data == null && !state.error, refresh };
}
