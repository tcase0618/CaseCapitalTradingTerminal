import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import axios from "axios";
import { API, BACKEND_BASE_URL } from "../config";
import {
  CheckCircle2,
  Eye,
  LoaderCircle,
  LockKeyhole,
  Power,
  ShieldCheck,
  UserPlus,
  Wifi,
  WifiOff,
} from "lucide-react";
import startupLogo from "../assets/case-capital-startup-logo.png";

const AUTH_KEY = "case_capital_terminal_auth_v1";
const SESSION_KEY = "case_capital_terminal_session_v1";
// Preview-only unlocks. Keep the raw codes out of the shipped bundle.
const PREVIEW_CODE_HASHES = new Set([
  "5ca4f03a02ff51515ab63f01d5c18414b50283687bcb21113d555b92b966f012", // 6969
  "93fbd43880b3b55ef0ef2580668fcb1fcaf0d541aac855f2e1449f933659f5f", // 0209
]);

const accent = "#c8a84b";
const accent2 = "#5eead4";
const pageBg = "#05070b";
const hairline = "0.5px solid rgba(255,255,255,0.11)";
const muted = "#7d8594";
const label = "#c9d0dc";
let previewInterceptorId = null;

export default function StartupGate({ children }) {
  const [auth, setAuth] = useState(() => readAuth());
  const [session, setSession] = useState(() => readSession());
  const sessionReady = Boolean(session?.mode);
  const [bootOpened, setBootOpened] = useState(() => Boolean(readSession()?.mode));
  const [mode, setMode] = useState("login");
  const [authConfig, setAuthConfig] = useState({ cloud: false, operator_login_enabled: true, preview_enabled: true, setup_enabled: false });
  const [name, setName] = useState(() => readAuth()?.name || "CASE CAPITAL OPERATOR");
  const [code, setCode] = useState("");
  const [confirmCode, setConfirmCode] = useState("");
  const [previewPrompted, setPreviewPrompted] = useState(false);
  const [error, setError] = useState("");
  const [backend, setBackend] = useState({ state: "checking", message: "Checking backend link" });
  const [bootChecks, setBootChecks] = useState([]);
  const [launching, setLaunching] = useState(false);
  const autoBootAttempted = useRef(false);

  const isDesktop = useMemo(() => Boolean(window.__TAURI_INTERNALS__ || window.__TAURI__), []);
  const usesLocalDesktopBackend = useMemo(() => {
    if (!isDesktop || !BACKEND_BASE_URL) return isDesktop;
    try {
      const parsed = new URL(BACKEND_BASE_URL);
      return ["127.0.0.1", "localhost"].includes(parsed.hostname);
    } catch {
      return isDesktop;
    }
  }, [isDesktop]);

  useEffect(() => {
    let cancelled = false;
    axios.get(`${API}/auth/config`, { timeout: 6000 })
      .then(({ data }) => {
        if (cancelled) return;
        setAuthConfig(data || {});
        setMode(data?.setup_enabled && !readAuth() ? "setup" : "login");
      })
      .catch(() => {
        if (!cancelled) setMode(readAuth() ? "login" : "setup");
      });
    return () => { cancelled = true; };
  }, []);

  useEffect(() => {
    configureTerminalSession(session);
    return () => {};
  }, [session]);

  useEffect(() => {
    const interceptorId = axios.interceptors.response.use(
      response => response,
      error => {
        const detail = error?.response?.data?.detail || "";
        if (error?.response?.status === 403 && String(detail).includes("Operator session required")) {
          sessionStorage.removeItem(SESSION_KEY);
          configureTerminalSession(null);
          setSession(null);
          setBootOpened(true);
          setMode("login");
          setError("Operator session expired after backend restart. Enter the access code again.");
        }
        return Promise.reject(error);
      }
    );
    return () => axios.interceptors.response.eject(interceptorId);
  }, []);

  const forceBoot = useCallback(async () => {
    setBackend({ state: "checking", message: "Forcing desktop backend boot" });
    try {
      if (!usesLocalDesktopBackend) {
        setBackend({ state: "offline", message: "Force boot is only for local desktop backend mode." });
        return false;
      }
      const { invoke } = await import("@tauri-apps/api/core");
      const result = await invoke("force_boot_backend");
      if (result.ok) {
        await checkBackend(setBackend, setBootChecks, usesLocalDesktopBackend);
        return true;
      }
      setBackend({
        state: "offline",
        message: result.message || "Backend boot failed",
      });
      return false;
    } catch (err) {
      setBackend({ state: "offline", message: err?.message || "Backend boot failed" });
      return false;
    }
  }, [usesLocalDesktopBackend]);

  useEffect(() => {
    if (usesLocalDesktopBackend) {
      autoBootAttempted.current = true;
      forceBoot();
      return;
    }
    checkBackend(setBackend, setBootChecks, usesLocalDesktopBackend);
  }, [forceBoot, usesLocalDesktopBackend]);

  useEffect(() => {
    if (backend.state === "online") return;
    const id = setInterval(() => checkBackend(setBackend, setBootChecks, usesLocalDesktopBackend), 2500);
    return () => clearInterval(id);
  }, [backend.state, usesLocalDesktopBackend]);

  useEffect(() => {
    if (!usesLocalDesktopBackend || backend.state === "online" || autoBootAttempted.current) return;
    autoBootAttempted.current = true;
    forceBoot();
  }, [backend.state, forceBoot, usesLocalDesktopBackend]);

  useEffect(() => {
    if (!sessionReady) return;
    setLaunching(true);
    const id = setTimeout(() => setLaunching(false), 1200);
    return () => clearTimeout(id);
  }, [sessionReady]);

  if (sessionReady && !launching && backend.state !== "offline") {
    return children;
  }

  const submit = async event => {
    event.preventDefault();
    setError("");
    const normalizedCode = code.trim();
    const attemptedHash = await hashCode(normalizedCode);

    if (PREVIEW_CODE_HASHES.has(attemptedHash)) {
      await enterPreview();
      return;
    }

    if (normalizedCode.length < 6) {
      setError("Access code must be at least 6 characters, or use a preview code.");
      return;
    }

    if (mode === "setup") {
      if (!authConfig.setup_enabled) {
        setError("Operator creation is disabled on this server. Use the server access code or preview mode.");
        return;
      }
      if (normalizedCode !== confirmCode.trim()) {
        setError("Access codes do not match.");
        return;
      }
      const nextAuth = {
        name: name.trim() || "CASE CAPITAL OPERATOR",
        created_at: new Date().toISOString(),
        hash: await hashCode(normalizedCode),
      };
      localStorage.setItem(AUTH_KEY, JSON.stringify(nextAuth));
      setAuth(nextAuth);
      const nextSession = { mode: "operator", name: nextAuth.name, local: true };
      writeSession(nextSession);
      setSession(nextSession);
      return;
    }

    if (authConfig.cloud || authConfig.operator_login_enabled) {
      try {
        const { data } = await axios.post(`${API}/auth/login`, { code: normalizedCode }, { timeout: 8000 });
        const nextSession = { mode: "operator", token: data.token, name: data.name || "CASE CAPITAL OPERATOR" };
        writeSession(nextSession);
        setSession(nextSession);
        if (backend.state === "checking") {
          setBackend({ state: "online", message: "Access verified. Backend sync continuing in terminal." });
        }
        return;
      } catch (err) {
        setError(err?.response?.data?.detail || "Access denied. Check the server terminal code.");
        return;
      }
    }

    if (auth?.hash) {
      const attemptedHash = await hashCode(normalizedCode);
      if (attemptedHash !== auth?.hash) {
        setError("Access denied. Check the local terminal code.");
        return;
      }
      const nextSession = { mode: "operator", name: auth.name || "CASE CAPITAL OPERATOR", local: true };
      writeSession(nextSession);
      setSession(nextSession);
    }
    if (backend.state === "checking") {
      setBackend({ state: "online", message: "Access verified. Backend sync continuing in terminal." });
    }
  };

  const enterPreview = async () => {
    setError("");
    try {
      await axios.post(`${API}/auth/preview`, null, { timeout: 6000 }).catch(() => null);
    } finally {
      const nextSession = { mode: "preview", name: "CASE CAPITAL PREVIEW" };
      writeSession(nextSession);
      setSession(nextSession);
      if (backend.state === "checking") {
        setBackend({ state: "online", message: "Preview mode. Backend sync continuing read-only." });
      }
    }
  };

  const requestPreviewCode = () => {
    setPreviewPrompted(true);
    setError("Enter a preview code: 6969 or 0209.");
  };

  return (
    <div className="startup-gate" style={styles.root}>
      <style>{startupAnimations}</style>
      <div className="crt-vignette" />
      <div className="scanline-overlay" />
      <div className="crt-grain" />
      <div style={styles.grid} />

      {!bootOpened ? (
        <BootSplash
          backend={backend}
          checks={bootChecks}
          isDesktop={isDesktop}
          usesLocalDesktopBackend={usesLocalDesktopBackend}
          onRefresh={() => checkBackend(setBackend, setBootChecks, usesLocalDesktopBackend)}
          onForceBoot={forceBoot}
          onOpen={() => setBootOpened(true)}
        />
      ) : (
        <LoginPanel
          auth={auth}
          mode={mode}
          name={name}
          code={code}
          confirmCode={confirmCode}
          error={error}
          backend={backend}
          checks={bootChecks}
          isDesktop={isDesktop}
          usesLocalDesktopBackend={usesLocalDesktopBackend}
          onName={setName}
          onCode={setCode}
          onConfirmCode={setConfirmCode}
          onRefresh={() => checkBackend(setBackend, setBootChecks, usesLocalDesktopBackend)}
          onForceBoot={forceBoot}
          onSubmit={submit}
          onPreview={requestPreviewCode}
          previewPrompted={previewPrompted}
          authConfig={authConfig}
        />
      )}

      {launching && (
        <div className="startup-launch-overlay" style={styles.launchOverlay}>
          <img className="startup-launch-logo" src={startupLogo} alt="" style={styles.launchLogo} />
          <div className="startup-launch-text" style={styles.launchText}>INITIALIZING CASE CAPITAL TRADING TERMINAL</div>
        </div>
      )}
    </div>
  );
}

function BootSplash({ backend, checks, isDesktop, usesLocalDesktopBackend, onRefresh, onForceBoot, onOpen }) {
  const online = backend.state === "online";
  return (
    <section className="startup-splash-stage" style={styles.splashStage}>
      <div style={styles.heroLogoWrap}>
        <div style={styles.logoBackdrop} />
        <img src={startupLogo} alt="Case Capital Automated Management" style={styles.heroLogo} />
      </div>

      <div className="startup-boot-console" style={styles.bootConsole}>
        <div style={styles.bootHeader}>
          <span style={{ color: accent2 }}>SYSTEM BOOT</span>
          <span style={{ color: online ? "#4ade80" : accent }}>{online ? "READY" : "LOADING"}</span>
        </div>
        <div style={styles.bootProgress}>
          <span className={online ? "" : "startup-progress"} style={{ ...styles.progressFill, width: online ? "100%" : "70%" }} />
        </div>
        <div style={styles.bootMessage}>
          <LoaderCircle className={online ? "" : "startup-spin"} size={15} />
          <span>{online ? "Backend is up and firing." : backend.message}</span>
        </div>
        <BootChecklist checks={checks} />
        <div style={styles.bootMeta}>
          <span>{usesLocalDesktopBackend ? "DESKTOP LOCAL BACKEND" : "VPS CLOUD BACKEND"}</span>
          <button type="button" onClick={onRefresh} style={styles.textButton}>REFRESH LINK</button>
        </div>
        <div style={styles.splashActions}>
          <button
            type="button"
            onClick={onOpen}
            disabled={!online}
            style={{ ...styles.openButton, opacity: online ? 1 : 0.42, cursor: online ? "pointer" : "not-allowed" }}
          >
            OPEN TERMINAL
          </button>
          {!online && usesLocalDesktopBackend && (
            <button type="button" onClick={onForceBoot} style={styles.secondaryButton}>
              FORCE BACKEND BOOT
            </button>
          )}
        </div>
      </div>
    </section>
  );
}

function LoginPanel({
  auth,
  mode,
  authConfig,
  name,
  code,
  confirmCode,
  error,
  backend,
  checks,
  isDesktop,
  usesLocalDesktopBackend,
  onName,
  onCode,
  onConfirmCode,
  onRefresh,
  onForceBoot,
  onSubmit,
  onPreview,
  previewPrompted,
}) {
  return (
    <section className="startup-login-stage" style={styles.loginStage}>
      <div style={styles.loginMark}>
        <img src={startupLogo} alt="Case Capital Automated Management" style={styles.loginLogo} />
      </div>

      <form className="startup-login-panel" onSubmit={onSubmit} style={styles.panel}>
        <div style={styles.panelTop}>
          <div>
            <div style={styles.kicker}>{mode === "setup" ? "LOCAL FIRST RUN SECURITY" : "SECURE TERMINAL LOGIN"}</div>
            <h1 style={styles.title}>{mode === "setup" ? "Create Local Operator Access" : "Operator Verification"}</h1>
          </div>
          <div style={styles.lockBadge}>
            {mode === "setup" ? <UserPlus size={22} /> : <LockKeyhole size={22} />}
          </div>
        </div>

        <div style={styles.bootRail}>
          <StatusItem icon={<ShieldCheck size={14} />} label="AUTH" value={mode === "setup" ? "FIRST RUN" : "ARMED"} tone={accent} />
          <StatusItem icon={backend.state === "online" ? <Wifi size={14} /> : <WifiOff size={14} />} label="BACKEND" value={backend.state.toUpperCase()} tone={backend.state === "online" ? "#4ade80" : backend.state === "checking" ? accent : "#f87171"} />
          <StatusItem icon={<Power size={14} />} label="SHELL" value={isDesktop ? (usesLocalDesktopBackend ? "DESKTOP" : "CLOUD") : "BROWSER"} tone={accent2} />
        </div>

        <div style={styles.backendStrip}>
          <span style={{ color: backend.state === "online" ? "#4ade80" : backend.state === "checking" ? accent : "#f87171" }}>
            {backend.state === "online" ? "ONLINE" : backend.state === "checking" ? "SYNC" : "OFFLINE"}
          </span>
          <span>{backend.message}</span>
          <button type="button" onClick={onRefresh} style={styles.textButton}>REFRESH</button>
        </div>
        <BootChecklist checks={checks} compact />

        {authConfig?.cloud && !authConfig?.operator_login_enabled && (
          <div style={styles.previewNotice}>
            Operator login is not configured on this server. Preview mode is available, but changes are blocked.
          </div>
        )}

        {mode === "setup" && (
          <label style={styles.field}>
            <span>OPERATOR NAME</span>
            <input value={name} onChange={event => onName(event.target.value)} style={styles.input} autoComplete="username" />
          </label>
        )}

        <label style={styles.field}>
          <span>{mode === "setup" ? "CREATE ACCESS CODE" : "ACCESS CODE"}</span>
          <input
            value={code}
            onChange={event => onCode(event.target.value)}
            style={styles.input}
            type="password"
            autoComplete={mode === "setup" ? "new-password" : "current-password"}
            autoFocus
          />
        </label>

        {mode === "setup" && (
          <label style={styles.field}>
            <span>CONFIRM ACCESS CODE</span>
            <input
              value={confirmCode}
              onChange={event => onConfirmCode(event.target.value)}
              style={styles.input}
              type="password"
              autoComplete="new-password"
            />
          </label>
        )}

        {error && <div style={styles.error}>{error}</div>}

        <div style={styles.actions}>
          <button type="submit" style={styles.primaryButton} disabled={authConfig?.cloud && !authConfig?.operator_login_enabled}>
            {mode === "setup" ? "CREATE LOGIN" : "UNLOCK TERMINAL"}
          </button>
          {usesLocalDesktopBackend && (
            <button type="button" onClick={onForceBoot} style={styles.secondaryButton}>
              FORCE BACKEND BOOT
            </button>
          )}
        </div>

        {authConfig?.preview_enabled !== false && (
          <button type="button" onClick={onPreview} style={styles.previewButton}>
            <Eye size={14} />
            {previewPrompted ? "ENTER PREVIEW CODE" : "PREVIEW TERMINAL - READ ONLY"}
          </button>
        )}

        {auth && mode === "login" && (
          <div style={styles.identity}>
            <CheckCircle2 size={14} />
            <span>{auth.name || "CASE CAPITAL OPERATOR"}</span>
          </div>
        )}
      </form>
    </section>
  );
}

function StatusItem({ icon, label, value, tone }) {
  return (
    <div style={styles.statusItem}>
      <span style={{ ...styles.statusIcon, color: tone }}>{icon}</span>
      <span style={styles.statusLabel}>{label}</span>
      <span style={{ ...styles.statusValue, color: tone }}>{value}</span>
    </div>
  );
}

function BootChecklist({ checks = [], compact = false }) {
  if (!checks.length) return null;
  const visible = compact ? checks.slice(0, 4) : checks;
  return (
    <div style={{ ...styles.checkGrid, gridTemplateColumns: compact ? "repeat(2, minmax(0, 1fr))" : "repeat(4, minmax(0, 1fr))" }}>
      {visible.map(check => (
        <div key={check.key || check.label} style={styles.checkItem}>
          <span style={{ color: check.ok ? "#4ade80" : "#fbbf24", fontWeight: 900 }}>{check.ok ? "READY" : "SYNC"}</span>
          <span style={styles.checkLabel}>{check.label}</span>
        </div>
      ))}
    </div>
  );
}

function readAuth() {
  try {
    return JSON.parse(localStorage.getItem(AUTH_KEY) || "null");
  } catch {
    return null;
  }
}

function readSession() {
  try {
    const raw = sessionStorage.getItem(SESSION_KEY);
    if (!raw) return null;
    if (raw === "open") return { mode: "operator", legacy: true };
    return JSON.parse(raw);
  } catch {
    return null;
  }
}

function writeSession(session) {
  sessionStorage.setItem(SESSION_KEY, JSON.stringify(session));
  configureTerminalSession(session);
}

function configureTerminalSession(session) {
  const mode = session?.mode || "";
  document.body.dataset.terminalMode = mode;
  if (session?.token) {
    axios.defaults.headers.common.Authorization = `Bearer ${session.token}`;
  } else {
    delete axios.defaults.headers.common.Authorization;
  }
  if (previewInterceptorId != null) {
    axios.interceptors.request.eject(previewInterceptorId);
    previewInterceptorId = null;
  }
  if (mode === "preview") {
    previewInterceptorId = axios.interceptors.request.use(config => {
      const method = String(config.method || "get").toLowerCase();
      if (!["get", "head", "options"].includes(method) && !String(config.url || "").includes("/auth/")) {
        return Promise.reject(new Error("Preview mode is read-only. Operator login required for changes."));
      }
      return config;
    });
  }
}

async function hashCode(code) {
  const input = `case-capital:${code}`;
  if (!crypto?.subtle) {
    return fallbackHash(input);
  }
  const encoder = new TextEncoder();
  const buffer = await crypto.subtle.digest("SHA-256", encoder.encode(input));
  return Array.from(new Uint8Array(buffer)).map(byte => byte.toString(16).padStart(2, "0")).join("");
}

function fallbackHash(input) {
  let h1 = 0xdeadbeef;
  let h2 = 0x41c6ce57;

  for (let i = 0; i < input.length; i += 1) {
    const ch = input.charCodeAt(i);
    h1 = Math.imul(h1 ^ ch, 2654435761);
    h2 = Math.imul(h2 ^ ch, 1597334677);
  }

  h1 = Math.imul(h1 ^ (h1 >>> 16), 2246822507) ^ Math.imul(h2 ^ (h2 >>> 13), 3266489909);
  h2 = Math.imul(h2 ^ (h2 >>> 16), 2246822507) ^ Math.imul(h1 ^ (h1 >>> 13), 3266489909);

  return `fallback-${(h2 >>> 0).toString(16).padStart(8, "0")}${(h1 >>> 0).toString(16).padStart(8, "0")}`;
}

async function checkBackend(setBackend, setBootChecks, isDesktop = false) {
  setBackend({ state: "checking", message: "Checking backend link" });
  const configuredBackendIsLocal = isLocalBackendBase(BACKEND_BASE_URL);
  if (isDesktop && configuredBackendIsLocal) {
    try {
      const { invoke } = await import("@tauri-apps/api/core");
      const result = await invoke("backend_status");
      if (result.ok) {
        setBackend({ state: "online", message: "Backend is responding. Syncing terminal data." });
        warmTerminalData(undefined, setBootChecks);
        return;
      }
      if (result.listening) {
        setBackend({ state: "checking", message: result.message || "Backend port is open. Verifying terminal data." });
        const warm = await waitForTerminalData(undefined, setBootChecks, 8000);
        if (warm.ok) {
          setBackend({ state: "online", message: "Backend and terminal data are ready" });
          return;
        }
      }
    } catch {}
  }

  const browserBase = window.location?.origin || "";
  const localBases = isDesktop && configuredBackendIsLocal
    ? ["http://127.0.0.1:8001", "http://localhost:8001"]
    : [];
  const bases = [
    BACKEND_BASE_URL,
    isLocalBackendBase(BACKEND_BASE_URL) ? browserBase : "",
    ...localBases,
  ].filter(Boolean);
  const uniqueBases = [...new Set(bases)];
  const errors = [];
  for (const base of uniqueBases) {
    try {
      await axios.get(`${base}/api/status`, { timeout: 6000 });
      setBackend({ state: "online", message: `Backend responding at ${base}` });
      warmTerminalData(base, setBootChecks);
      return;
    } catch (err) {
      errors.push(`${base}: ${err?.message || "failed"}`);
    }
  }
  setBackend({
    state: "offline",
    message: `Backend is not responding. Tried ${uniqueBases.map(formatBackendBase).join(" / ")}`,
    detail: errors.join(" | "),
  });
}

function isLocalBackendBase(base = "") {
  if (!base) return false;
  try {
    const parsed = new URL(base);
    return ["127.0.0.1", "localhost"].includes(parsed.hostname);
  } catch {
    return false;
  }
}

function warmTerminalData(base, setBootChecks) {
  waitForTerminalData(base, setBootChecks).catch(() => {});
}

async function waitForTerminalData(base = API.replace(/\/api$/, ""), setBootChecks = () => {}, timeoutMs = 14000) {
  const started = Date.now();
  const root = (base || BACKEND_BASE_URL || window.location?.origin || "").replace(/\/$/, "");
  let lastError = "";

  while (Date.now() - started < timeoutMs) {
    try {
      const { data } = await axios.get(`${root}/api/desktop/diagnostics`, { timeout: 4500 });
      setBootChecks(data.checklist || []);
      if (data.ok) {
        return { ok: true };
      }
      const waiting = (data.checklist || []).filter(item => !item.ok).map(item => item.label).slice(0, 2).join(", ");
      lastError = waiting ? `Waiting for ${waiting}` : "Waiting for terminal readiness";
    } catch (err) {
      lastError = err?.message || "terminal data unavailable";
    }
    await sleep(700);
  }

  return { ok: false, message: `Backend online. Terminal data still warming: ${lastError}` };
}

function sleep(ms) {
  return new Promise(resolve => setTimeout(resolve, ms));
}

function formatBackendBase(base = "") {
  return base.replace(/^https?:\/\//, "") || "/api";
}

const startupAnimations = `
@keyframes startupSpin { to { transform: rotate(360deg); } }
@keyframes bootPulse { 0%, 100% { opacity: 0.65; } 50% { opacity: 1; } }
@keyframes progressSweep { 0% { transform: translateX(-70%); } 100% { transform: translateX(70%); } }
.startup-spin { animation: startupSpin 1.1s linear infinite; }
.startup-progress::after {
  content: "";
  position: absolute;
  inset: 0;
  background: linear-gradient(90deg, transparent, rgba(255,255,255,0.35), transparent);
  animation: progressSweep 1.35s ease-in-out infinite;
}
`;

const styles = {
  root: {
    height: "100vh",
    minHeight: "100vh",
    background: `radial-gradient(circle at 50% 18%, rgba(200,168,75,0.13), transparent 20%), radial-gradient(circle at 50% 70%, rgba(94,234,212,0.055), transparent 26%), linear-gradient(180deg, #06111f 0%, ${pageBg} 52%, #030306 100%)`,
    color: "#f8fafc",
    position: "relative",
    overflowX: "hidden",
    overflowY: "auto",
    overscrollBehavior: "contain",
    WebkitOverflowScrolling: "touch",
    fontFamily: "JetBrains Mono, Courier New, monospace",
  },
  grid: {
    position: "absolute",
    inset: 0,
    backgroundImage: "linear-gradient(rgba(255,255,255,0.025) 1px, transparent 1px), linear-gradient(90deg, rgba(255,255,255,0.025) 1px, transparent 1px)",
    backgroundSize: "80px 80px",
    opacity: 0.55,
    maskImage: "radial-gradient(circle at center, black 0%, black 48%, transparent 78%)",
  },
  splashStage: {
    position: "relative",
    zIndex: 3,
    minHeight: "100%",
    display: "flex",
    flexDirection: "column",
    justifyContent: "flex-start",
    alignItems: "center",
    gap: 22,
    padding: "clamp(18px, 4vh, 32px) 24px 44px",
    boxSizing: "border-box",
  },
  heroLogoWrap: {
    width: "min(940px, 88vw)",
    maxHeight: "42vh",
    position: "relative",
    display: "flex",
    justifyContent: "center",
    alignItems: "center",
    flexShrink: 0,
  },
  logoBackdrop: {
    position: "absolute",
    inset: "-8% -4%",
    background: "radial-gradient(circle at 50% 50%, rgba(200,168,75,0.14), transparent 44%)",
    filter: "blur(18px)",
  },
  heroLogo: {
    width: "100%",
    maxHeight: "42vh",
    objectFit: "contain",
    position: "relative",
    opacity: 0.9,
    filter: "contrast(1.05) saturate(0.9) drop-shadow(0 28px 70px rgba(0,0,0,0.65))",
  },
  bootConsole: {
    width: "min(640px, 90vw)",
    border: "1px solid rgba(200,168,75,0.2)",
    background: "linear-gradient(180deg, rgba(5,7,12,0.86), rgba(2,4,8,0.92))",
    boxShadow: "0 24px 90px rgba(0,0,0,0.54)",
    padding: 18,
  },
  bootHeader: {
    display: "flex",
    justifyContent: "space-between",
    color: muted,
    fontSize: 10,
    letterSpacing: "0.22em",
    fontWeight: 900,
    marginBottom: 12,
  },
  bootProgress: {
    height: 8,
    border: "1px solid rgba(255,255,255,0.12)",
    background: "rgba(255,255,255,0.025)",
    overflow: "hidden",
    marginBottom: 12,
  },
  progressFill: {
    display: "block",
    height: "100%",
    background: `linear-gradient(90deg, ${accent}, ${accent2})`,
    boxShadow: `0 0 18px ${accent}66`,
    position: "relative",
    transition: "width 0.35s ease",
  },
  bootMessage: {
    display: "flex",
    alignItems: "center",
    gap: 10,
    color: label,
    fontSize: 12,
    letterSpacing: "0.08em",
    minHeight: 22,
  },
  checkGrid: {
    marginTop: 12,
    display: "grid",
    gap: 8,
  },
  checkItem: {
    border: "0.5px solid rgba(255,255,255,0.08)",
    background: "rgba(255,255,255,0.018)",
    padding: "8px 9px",
    display: "flex",
    flexDirection: "column",
    gap: 4,
    minHeight: 46,
    fontSize: 9,
    letterSpacing: "0.12em",
  },
  checkLabel: {
    color: muted,
    whiteSpace: "nowrap",
    overflow: "hidden",
    textOverflow: "ellipsis",
  },
  bootMeta: {
    marginTop: 12,
    paddingTop: 12,
    borderTop: hairline,
    display: "flex",
    justifyContent: "space-between",
    alignItems: "center",
    gap: 12,
    color: muted,
    fontSize: 10,
    letterSpacing: "0.16em",
  },
  splashActions: {
    marginTop: 16,
    display: "grid",
    gridTemplateColumns: "1fr 1fr",
    gap: 10,
  },
  openButton: {
    background: `linear-gradient(180deg, ${accent}, #9b7a22)`,
    color: "#05070b",
    border: `1px solid ${accent}`,
    minHeight: 46,
    fontFamily: "JetBrains Mono, Courier New, monospace",
    fontWeight: 900,
    letterSpacing: "0.12em",
  },
  loginStage: {
    position: "relative",
    zIndex: 3,
    minHeight: "100%",
    display: "flex",
    flexDirection: "column",
    justifyContent: "flex-start",
    alignItems: "center",
    gap: 14,
    padding: "clamp(18px, 4vh, 28px) 24px 44px",
    boxSizing: "border-box",
  },
  loginMark: {
    width: "min(520px, 76vw)",
    textAlign: "center",
  },
  loginLogo: {
    width: "100%",
    display: "block",
    objectFit: "contain",
    filter: "contrast(1.04) saturate(0.94) drop-shadow(0 22px 42px rgba(0,0,0,0.45))",
  },
  bootRail: {
    marginBottom: 14,
    display: "grid",
    gridTemplateColumns: "repeat(3, minmax(0, 1fr))",
    gap: 8,
  },
  statusItem: {
    border: "0.5px solid rgba(255,255,255,0.08)",
    background: "rgba(255,255,255,0.018)",
    padding: "9px 10px",
    display: "grid",
    gridTemplateColumns: "16px 1fr",
    gap: "4px 8px",
    alignItems: "center",
  },
  statusIcon: {
    gridRow: "1 / span 2",
    display: "inline-flex",
    alignItems: "center",
  },
  statusLabel: {
    color: muted,
    fontSize: 9,
    letterSpacing: "0.18em",
  },
  statusValue: {
    fontSize: 11,
    fontWeight: 800,
    letterSpacing: "0.1em",
  },
  panel: {
    width: "min(500px, 92vw)",
    border: "1px solid rgba(200,168,75,0.18)",
    background: "linear-gradient(180deg, rgba(8,10,16,0.9), rgba(3,5,10,0.92))",
    boxShadow: "0 30px 90px rgba(0,0,0,0.58), inset 0 0 38px rgba(200,168,75,0.025)",
    padding: 20,
    position: "relative",
  },
  panelTop: {
    display: "flex",
    justifyContent: "space-between",
    gap: 16,
    alignItems: "flex-start",
    marginBottom: 12,
    paddingBottom: 12,
    borderBottom: hairline,
  },
  kicker: {
    color: accent2,
    fontSize: 10,
    letterSpacing: "0.22em",
    fontWeight: 800,
  },
  title: {
    margin: "8px 0 0",
    color: accent,
    fontSize: 20,
    lineHeight: 1.15,
    letterSpacing: "0.06em",
  },
  lockBadge: {
    width: 40,
    height: 40,
    border: `1px solid ${accent}66`,
    color: accent,
    display: "flex",
    alignItems: "center",
    justifyContent: "center",
    boxShadow: `0 0 24px rgba(200,168,75,0.18), inset 0 0 18px rgba(200,168,75,0.08)`,
  },
  backendStrip: {
    display: "grid",
    gridTemplateColumns: "58px 1fr auto",
    gap: 8,
    alignItems: "center",
    border: hairline,
    background: "rgba(255,255,255,0.025)",
    padding: "10px 12px",
    color: label,
    fontSize: 11,
    marginBottom: 12,
  },
  field: {
    display: "block",
    marginBottom: 10,
  },
  input: {
    width: "100%",
    marginTop: 6,
    background: "#03050a",
    border: "1px solid rgba(255,255,255,0.13)",
    color: "#f8fafc",
    padding: "10px 12px",
    outline: "none",
    fontFamily: "JetBrains Mono, Courier New, monospace",
    fontSize: 14,
    letterSpacing: "0.08em",
    boxSizing: "border-box",
  },
  error: {
    color: "#f87171",
    border: "1px solid rgba(248,113,113,0.28)",
    background: "rgba(248,113,113,0.08)",
    padding: "10px 12px",
    fontSize: 11,
    marginBottom: 14,
  },
  previewNotice: {
    border: "1px solid rgba(200,168,75,0.22)",
    background: "rgba(200,168,75,0.07)",
    color: label,
    padding: "10px 12px",
    fontSize: 11,
    lineHeight: 1.5,
    marginBottom: 12,
  },
  actions: {
    display: "grid",
    gridTemplateColumns: "1.1fr 1fr",
    gap: 10,
    marginTop: 14,
  },
  primaryButton: {
    background: `linear-gradient(180deg, ${accent}, #9b7a22)`,
    color: "#05070b",
    border: `1px solid ${accent}`,
    minHeight: 44,
    fontFamily: "JetBrains Mono, Courier New, monospace",
    fontWeight: 900,
    letterSpacing: "0.1em",
    cursor: "pointer",
  },
  secondaryButton: {
    background: "transparent",
    color: accent2,
    border: `1px solid ${accent2}66`,
    minHeight: 44,
    fontFamily: "JetBrains Mono, Courier New, monospace",
    fontWeight: 800,
    letterSpacing: "0.08em",
    cursor: "pointer",
  },
  textButton: {
    background: "transparent",
    border: "none",
    color: accent2,
    fontFamily: "JetBrains Mono, Courier New, monospace",
    fontSize: 10,
    cursor: "pointer",
    letterSpacing: "0.12em",
  },
  previewButton: {
    width: "100%",
    marginTop: 10,
    background: "rgba(94,234,212,0.06)",
    color: accent2,
    border: `1px solid ${accent2}66`,
    minHeight: 42,
    fontFamily: "JetBrains Mono, Courier New, monospace",
    fontWeight: 900,
    letterSpacing: "0.1em",
    cursor: "pointer",
    display: "inline-flex",
    alignItems: "center",
    justifyContent: "center",
    gap: 8,
  },
  identity: {
    marginTop: 16,
    color: muted,
    display: "flex",
    gap: 8,
    alignItems: "center",
    fontSize: 11,
    letterSpacing: "0.12em",
  },
  launchOverlay: {
    position: "fixed",
    inset: 0,
    zIndex: 10,
    background: "rgba(1,5,12,0.94)",
    display: "flex",
    flexDirection: "column",
    justifyContent: "center",
    alignItems: "center",
    gap: 18,
  },
  launchLogo: {
    width: "min(520px, 68vw)",
    filter: "drop-shadow(0 0 52px rgba(200,168,75,0.12))",
  },
  launchText: {
    color: accent2,
    fontSize: 11,
    letterSpacing: "0.22em",
    fontWeight: 800,
  },
};
