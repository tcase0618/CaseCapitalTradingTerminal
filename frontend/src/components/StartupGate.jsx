import { useEffect, useMemo, useState } from "react";
import axios from "axios";
import {
  CheckCircle2,
  LoaderCircle,
  LockKeyhole,
  Power,
  ShieldCheck,
  UserPlus,
  Wifi,
  WifiOff,
} from "lucide-react";
import startupLogo from "../assets/case-capital-startup-logo.png";

const API = `${(process.env.REACT_APP_BACKEND_URL || "").replace(/\/$/, "")}/api`;
const AUTH_KEY = "case_capital_terminal_auth_v1";
const SESSION_KEY = "case_capital_terminal_session_v1";
// Preview-only unlocks. Keep these hashed so the raw codes are not stored in the bundle.
const PREVIEW_CODE_HASHES = new Set([
  "5ca4f03a02ff51515ab63f01d5c18414b50283687bcb21113d555b92b966f012", // 6969
  "93fbd43880b3b55ef0ef2580668fcb1fc5af0d541aac855f2e1449f933659f5f", // 0209
]);

const accent = "#c8a84b";
const accent2 = "#5eead4";
const pageBg = "#05070b";
const hairline = "0.5px solid rgba(255,255,255,0.11)";
const muted = "#7d8594";
const label = "#c9d0dc";

export default function StartupGate({ children }) {
  const [auth, setAuth] = useState(() => readAuth());
  const [sessionReady, setSessionReady] = useState(() => sessionStorage.getItem(SESSION_KEY) === "open");
  const [bootOpened, setBootOpened] = useState(() => sessionStorage.getItem(SESSION_KEY) === "open");
  const [mode] = useState(() => (readAuth() ? "login" : "setup"));
  const [name, setName] = useState(() => readAuth()?.name || "CASE CAPITAL OPERATOR");
  const [code, setCode] = useState("");
  const [confirmCode, setConfirmCode] = useState("");
  const [error, setError] = useState("");
  const [backend, setBackend] = useState({ state: "checking", message: "Checking backend link" });
  const [launching, setLaunching] = useState(false);

  const isDesktop = useMemo(() => Boolean(window.__TAURI_INTERNALS__), []);

  useEffect(() => {
    checkBackend(setBackend);
  }, []);

  useEffect(() => {
    if (backend.state === "online") return;
    const id = setInterval(() => checkBackend(setBackend), 2500);
    return () => clearInterval(id);
  }, [backend.state]);

  useEffect(() => {
    if (!sessionReady) return;
    setLaunching(true);
    const id = setTimeout(() => setLaunching(false), 1200);
    return () => clearTimeout(id);
  }, [sessionReady]);

  if (sessionReady && !launching) {
    return children;
  }

  const submit = async event => {
    event.preventDefault();
    setError("");
    const normalizedCode = code.trim();
    const attemptedHash = await hashCode(normalizedCode);

    // Preview access is available on first launch and remains session-scoped.
    if (PREVIEW_CODE_HASHES.has(attemptedHash)) {
      const nextAuth = auth || {
        name: "CASE CAPITAL PREVIEW OPERATOR",
        created_at: new Date().toISOString(),
        hash: attemptedHash,
      };
      if (!auth) localStorage.setItem(AUTH_KEY, JSON.stringify(nextAuth));
      setAuth(nextAuth);
      sessionStorage.setItem(SESSION_KEY, "open");
      setSessionReady(true);
      return;
    }

    if (normalizedCode.length < 6) {
      setError("Access code must be at least 6 characters, or use a preview code.");
      return;
    }

    if (mode === "setup") {
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
      sessionStorage.setItem(SESSION_KEY, "open");
      setSessionReady(true);
      return;
    }

    if (attemptedHash !== auth?.hash) {
      setError("Access denied. Check the local terminal code.");
      return;
    }
    sessionStorage.setItem(SESSION_KEY, "open");
    setSessionReady(true);
  };

  const forceBoot = async () => {
    setBackend({ state: "checking", message: "Sending desktop backend boot command" });
    try {
      if (!isDesktop) {
        setBackend({ state: "offline", message: "Force boot is available inside the desktop app." });
        return;
      }
      const { invoke } = await import("@tauri-apps/api/core");
      const result = await invoke("force_boot_backend");
      setBackend({
        state: result.ok ? "online" : "offline",
        message: result.message || (result.ok ? "Backend online" : "Backend boot failed"),
      });
    } catch (err) {
      setBackend({ state: "offline", message: err?.message || "Backend boot failed" });
    }
  };

  return (
    <div style={styles.root}>
      <style>{startupAnimations}</style>
      <div className="crt-vignette" />
      <div className="scanline-overlay" />
      <div className="crt-grain" />
      <div style={styles.grid} />

      {!bootOpened ? (
        <BootSplash
          backend={backend}
          isDesktop={isDesktop}
          onRefresh={() => checkBackend(setBackend)}
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
          isDesktop={isDesktop}
          onName={setName}
          onCode={setCode}
          onConfirmCode={setConfirmCode}
          onRefresh={() => checkBackend(setBackend)}
          onForceBoot={forceBoot}
          onSubmit={submit}
        />
      )}

      {launching && (
        <div style={styles.launchOverlay}>
          <img src={startupLogo} alt="" style={styles.launchLogo} />
          <div style={styles.launchText}>INITIALIZING CASE CAPITAL TRADING TERMINAL</div>
        </div>
      )}
    </div>
  );
}

function BootSplash({ backend, isDesktop, onRefresh, onForceBoot, onOpen }) {
  const online = backend.state === "online";
  return (
    <section style={styles.splashStage}>
      <div style={styles.heroLogoWrap}>
        <div style={styles.logoBackdrop} />
        <img src={startupLogo} alt="Case Capital Automated Management" style={styles.heroLogo} />
      </div>

      <div style={styles.bootConsole}>
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
        <div style={styles.bootMeta}>
          <span>{isDesktop ? "DESKTOP SHELL DETECTED" : "BROWSER DEVELOPMENT MODE"}</span>
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
          {!online && (
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
  name,
  code,
  confirmCode,
  error,
  backend,
  isDesktop,
  onName,
  onCode,
  onConfirmCode,
  onRefresh,
  onForceBoot,
  onSubmit,
}) {
  return (
    <section style={styles.loginStage}>
      <div style={styles.loginMark}>
        <img src={startupLogo} alt="Case Capital Automated Management" style={styles.loginLogo} />
      </div>

      <form onSubmit={onSubmit} style={styles.panel}>
        <div style={styles.panelTop}>
          <div>
            <div style={styles.kicker}>{mode === "setup" ? "FIRST RUN SECURITY" : "SECURE TERMINAL LOGIN"}</div>
            <h1 style={styles.title}>{mode === "setup" ? "Create Operator Access" : "Operator Verification"}</h1>
          </div>
          <div style={styles.lockBadge}>
            {mode === "setup" ? <UserPlus size={22} /> : <LockKeyhole size={22} />}
          </div>
        </div>

        <div style={styles.bootRail}>
          <StatusItem icon={<ShieldCheck size={14} />} label="AUTH" value={mode === "setup" ? "FIRST RUN" : "ARMED"} tone={accent} />
          <StatusItem icon={backend.state === "online" ? <Wifi size={14} /> : <WifiOff size={14} />} label="BACKEND" value={backend.state.toUpperCase()} tone={backend.state === "online" ? "#4ade80" : backend.state === "checking" ? accent : "#f87171"} />
          <StatusItem icon={<Power size={14} />} label="SHELL" value={isDesktop ? "DESKTOP" : "BROWSER"} tone={accent2} />
        </div>

        <div style={styles.backendStrip}>
          <span style={{ color: backend.state === "online" ? "#4ade80" : backend.state === "checking" ? accent : "#f87171" }}>
            {backend.state === "online" ? "ONLINE" : backend.state === "checking" ? "SYNC" : "OFFLINE"}
          </span>
          <span>{backend.message}</span>
          <button type="button" onClick={onRefresh} style={styles.textButton}>REFRESH</button>
        </div>

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
          <button type="submit" style={styles.primaryButton}>
            {mode === "setup" ? "CREATE LOGIN" : "UNLOCK TERMINAL"}
          </button>
          <button type="button" onClick={onForceBoot} style={styles.secondaryButton}>
            FORCE BACKEND BOOT
          </button>
        </div>

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

function readAuth() {
  try {
    return JSON.parse(localStorage.getItem(AUTH_KEY) || "null");
  } catch {
    return null;
  }
}

async function hashCode(code) {
  const encoder = new TextEncoder();
  const buffer = await crypto.subtle.digest("SHA-256", encoder.encode(`case-capital:${code}`));
  return Array.from(new Uint8Array(buffer)).map(byte => byte.toString(16).padStart(2, "0")).join("");
}

async function checkBackend(setBackend) {
  setBackend({ state: "checking", message: "Checking backend link" });
  const bases = [
    (process.env.REACT_APP_BACKEND_URL || "").replace(/\/$/, ""),
    "http://127.0.0.1:8001",
    "http://localhost:8001",
  ].filter(Boolean);
  const uniqueBases = [...new Set(bases)];
  const errors = [];
  for (const base of uniqueBases) {
    try {
      await axios.get(`${base}/api/status`, { timeout: 6000 });
      setBackend({ state: "online", message: `Backend online at ${base}` });
      return;
    } catch (err) {
      errors.push(`${base}: ${err?.message || "failed"}`);
    }
  }
  setBackend({
    state: "offline",
    message: `Backend is not responding. Tried ${uniqueBases.map(base => base.replace("http://", "")).join(" / ")}`,
    detail: errors.join(" | "),
  });
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
    minHeight: "100vh",
    background: `radial-gradient(circle at 50% 18%, rgba(200,168,75,0.13), transparent 20%), radial-gradient(circle at 50% 70%, rgba(94,234,212,0.055), transparent 26%), linear-gradient(180deg, #06111f 0%, ${pageBg} 52%, #030306 100%)`,
    color: "#f8fafc",
    position: "relative",
    overflowX: "hidden",
    overflowY: "auto",
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
    minHeight: "100vh",
    display: "flex",
    flexDirection: "column",
    justifyContent: "center",
    alignItems: "center",
    gap: 22,
    padding: "32px 24px",
  },
  heroLogoWrap: {
    width: "min(940px, 88vw)",
    position: "relative",
    display: "flex",
    justifyContent: "center",
    alignItems: "center",
  },
  logoBackdrop: {
    position: "absolute",
    inset: "-8% -4%",
    background: "radial-gradient(circle at 50% 50%, rgba(200,168,75,0.14), transparent 44%)",
    filter: "blur(18px)",
  },
  heroLogo: {
    width: "100%",
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
    minHeight: "100dvh",
    display: "flex",
    flexDirection: "column",
    justifyContent: "center",
    alignItems: "center",
    gap: 14,
    padding: "28px 24px 44px",
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
