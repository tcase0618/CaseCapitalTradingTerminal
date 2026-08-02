const isDesktopShell = Boolean(window.__TAURI_INTERNALS__ || window.__TAURI__);
export const CLOUD_BACKEND_URL = "http://129.121.101.96";

export const BACKEND_BASE_URL = (
  process.env.REACT_APP_BACKEND_URL ||
  CLOUD_BACKEND_URL
).replace(/\/$/, "");
export const API = BACKEND_BASE_URL ? `${BACKEND_BASE_URL}/api` : "/api";
