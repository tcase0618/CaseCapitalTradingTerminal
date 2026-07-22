export const BACKEND_BASE_URL = (process.env.REACT_APP_BACKEND_URL || "").replace(/\/$/, "");
export const API = BACKEND_BASE_URL ? `${BACKEND_BASE_URL}/api` : "/api";
