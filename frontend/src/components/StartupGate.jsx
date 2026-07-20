import { useEffect, useMemo, useState } from "react";
import axios from "axios";
import { CheckCircle2, LockKeyhole, Power, ShieldCheck, UserPlus, Wifi, WifiOff } from "lucide-react";
import startupLogo from "../assets/case-capital-startup-logo.png";

const API = `${(process.env.REACT_APP_BACKEND_URL || "").replace(/\/$/, "")}/api`;
const AUTH_KEY = "case_capital_terminal_auth_v1";
const SESSION_KEY = "case_capital_terminal_session_v1";

const accent = "#c8a84b";
const accent2 = "#5eead4";
const pageBg = "#05070b";
const hairline = "0.5px solid rgba(255,255,255,0.11)";
const muted = "#7d8594";
const label = "#c9d0dc";

export default function StartupGate({ children }) {
  const [auth, setAuth] = useState(() => readAuth());
  const [sessionReady, setSessionReady] = useState(() => sessionStorage.getItem(SESSION_KEY) === "open");
  const [mode, setMode] = useState(() => (readAuth() ? "login" : "setup"));
  const [name, setName] = useState(() => readAuth()?.name || "CASE CAPITAL OPERATOR");
  const [code, setCode] = useState("");
  const [confirmCode, setConfirmCode] = useState("");
  const [error, setError] = useState("");
  const [backend, setBackend] = useState({ state: "checking", message: "Checking backend link" });
  const [launching, setLaunching] = useState(false);

  useEffect(() => {
    checkBackend(setBackend);
  }, []);

  useEffect(() => {
    if (!sessionReady) return;
    setLaunching(true);
    const id = setTimeout(() => setLaunching(false), 1300);
    return () => clearTimeout(id);
  }, [sessionReady]);

  const isDesktop = useMemo(() => Boolean(window.__TAURI_INTERNALS__), []);

  if (sessionReady && !launching) {
    return children;
  }

  const submit = async event => {
    event.preventDefault();
    setError("");
    const normalizedCode = code.trim();
    if (normalizedCode.length < 6) {
      setError("Access code must be at least 6 characters.");
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

    const attemptedHash = await hashCode(normalizedCode);
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
      <div className="crt-vignette" />
      <div className="scanline-overlay" />
      <div className="crt-grain" />
      <div style={styles.grid} />

      <section style={styles.stage}>
        <div style={styles.brandColumn}>
          <img src={startupLogo} alt="Case Capital Automated Management" style={styles.logo} />
          <div style={styles.terminalLine}>
            <span>CASE CAPITAL TRADING TERMINAL</span>
            <span style={{ color: accent2 }}>SECURE BOOT</span>
          </div>
        </div>

        <form onSubmit={submit} style={styles.panel}>
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
              {backend.state === "online" ? "●" : backend.state === "checking" ? "◆" : "■"}
            </span>
            <span>{backend.message}</span>
            <button type="button" onClick={() => checkBackend(setBackend)} style={styles.textButton}>REFRESH</button>
          </div>

          {mode === "setup" && (
            <label style={styles.field}>
              <span>OPERATOR NAME</span>
              <input value={name} onChange={event => setName(event.target.value)} style={styles.input} autoComplete="username" />
            </label>
          )}

          <label style={styles.field}>
            <span>{mode === "setup" ? "CREATE ACCESS CODE" : "ACCESS CODE"}</span>
            <input
              value={code}
              onChange={event => setCode(event.target.value)}
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
                onChange={event => setConfirmCode(event.target.value)}
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
            <button type="button" onClick={forceBoot} style={styles.secondaryButton}>
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

      {launching && (
        <div style={styles.launchOverlay}>
          <img src={startupLogo} alt="" style={styles.launchLogo} />
          <div style={styles.launchText}>INITIALIZING CASE CAPITAL TRADING TERMINAL</div>
        </div>
      )}
    </div>
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
  try {
    await axios.get(`${API}/status`, { timeout: 3500 });
    setBackend({ state: "online", message: "Backend online at 127.0.0.1:8001" });
  } catch {
    setBackend({ state: "offline", message: "Backend is not responding on 127.0.0.1:8001" });
  }
}

const styles = {
  root: {
    minHeight: "100vh",
    background: `radial-gradient(circle at 50% 18%, rgba(200,168,75,0.13), transparent 20%), radial-gradient(circle at 50% 70%, rgba(94,234,212,0.055), transparent 26%), linear-gradient(180deg, #06111f 0%, ${pageBg} 52%, #030306 100%)`,
    color: "#f8fafc",
    position: "relative",
    overflow: "hidden",
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
  stage: {
    position: "relative",
    zIndex: 3,
    minHeight: "100vh",
    display: "flex",
    flexDirection: "column",
    justifyContent: "center",
    alignItems: "center",
    gap: 14,
    padding: "28px 24px",
  },
  brandColumn: {
    width: "min(560px, 76vw)",
    textAlign: "center",
  },
  logo: {
    width: "100%",
    display: "block",
    objectFit: "contain",
    filter: "contrast(1.04) saturate(0.94) drop-shadow(0 22px 42px rgba(0,0,0,0.45))",
  },
  terminalLine: {
    marginTop: 6,
    display: "flex",
    justifyContent: "center",
    gap: 18,
    color: muted,
    fontSize: 9,
    letterSpacing: "0.22em",
    fontWeight: 800,
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
    gridTemplateColumns: "16px 1fr auto",
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
