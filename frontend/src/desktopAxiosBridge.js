import axios, { AxiosError } from "axios";

const defaultAdapter = axios.defaults.adapter;
const isDesktop = () => Boolean(window.__TAURI_INTERNALS__ || window.__TAURI__);
let invokePromise = null;
const inFlightGets = new Map();

function getInvoke() {
  if (!invokePromise) {
    invokePromise = import("@tauri-apps/api/core").then(mod => mod.invoke);
  }
  return invokePromise;
}

function shouldBridge(url = "") {
  try {
    const parsed = new URL(url, "http://127.0.0.1:8001");
    const isApiPath = parsed.pathname === "/api" || parsed.pathname.startsWith("/api/");
    const isLocalBackend = ["127.0.0.1", "localhost"].includes(parsed.hostname);
    return isApiPath && isLocalBackend;
  } catch {
    return url.includes("/api/") || url.endsWith("/api");
  }
}

function buildBackendPath(config) {
  const rawUrl = config.url || "";
  const base = config.baseURL || "http://127.0.0.1:8001";
  const parsed = new URL(rawUrl, base);
  const params = new URLSearchParams(parsed.search);

  if (config.params && typeof config.params === "object") {
    Object.entries(config.params).forEach(([key, value]) => {
      if (value == null) return;
      if (Array.isArray(value)) {
        value.forEach(item => params.append(key, item));
      } else {
        params.set(key, value);
      }
    });
  }

  const query = params.toString();
  return `${parsed.pathname}${query ? `?${query}` : ""}`;
}

function normalizeBody(data) {
  if (data == null) return null;
  if (typeof data === "string") return data;
  return JSON.stringify(data);
}

axios.defaults.adapter = async config => {
  if (!isDesktop() || !shouldBridge(config.url || "")) {
    const adapter = typeof defaultAdapter === "function" ? defaultAdapter : axios.getAdapter(defaultAdapter);
    return adapter(config);
  }

  const method = (config.method || "get").toUpperCase();
  const path = buildBackendPath(config);
  const body = normalizeBody(config.data);
  const requestKey = `${method} ${path}`;

  const run = async () => {
    const invoke = await getInvoke();
    const response = await invoke("backend_request", { method, path, body });

    let data = response.body;
    if (typeof data === "string" && data.length) {
      try {
        data = JSON.parse(data);
      } catch {
        // Keep non-JSON responses as text.
      }
    }

    const axiosResponse = {
      data,
      status: response.status,
      statusText: String(response.status),
      headers: { "content-type": "application/json" },
      config,
      request: null,
    };

    const validateStatus = config.validateStatus || axios.defaults.validateStatus;
    if (!validateStatus || validateStatus(response.status)) {
      return axiosResponse;
    }

    throw new AxiosError(
      `Request failed with status code ${response.status}`,
      AxiosError.ERR_BAD_RESPONSE,
      config,
      null,
      axiosResponse,
    );
  };

  if (method === "GET") {
    if (inFlightGets.has(requestKey)) return inFlightGets.get(requestKey);
    const promise = run().finally(() => inFlightGets.delete(requestKey));
    inFlightGets.set(requestKey, promise);
    return promise;
  }

  return run();
};
