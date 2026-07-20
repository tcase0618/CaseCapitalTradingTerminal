// Shared CRT shell — sidebar nav + page wrapper. Used by all sub-pages.
// Refined Bloomberg terminal aesthetic: corner brackets, live system bar,
// status dots, micro-animations.
import { Link, useLocation } from "react-router-dom";
import { useEffect, useState } from "react";
import terminalLogo from "../assets/case-terminal-logo.png";

const accent = "#c8a84b";
const accent2 = "#5eead4";
const dim = "#374151";
const muted = "#6b7280";
const labelLight = "#9ca3af";
const cardBg = "#0c0c12";
const cardBgHi = "#10101a";
const pageBg = "#06060a";
const hairline = "0.5px solid rgba(255,255,255,0.06)";
const hairlineAccent = "0.5px solid rgba(200,168,75,0.18)";

const NAV = [
  { to: "/portfolio-manager", label: "PORTFOLIO MGR", icon: "PM", group: "TRADE FLOOR" },
  { to: "/",            label: "COMMAND CENTER", icon: "CC", group: "CORE" },
  { to: "/scanner",     label: "SCANNER",     icon: "SC", group: "CORE" },
  { to: "/intel",       label: "INTEL FEED",  icon: "◉", group: "CORE" },
  { to: "/contracts",   label: "CONTRACTS",   icon: "▦", group: "CORE" },
  { to: "/sec",         label: "SEC FILINGS", icon: "§", group: "CORE" },
  { to: "/earnings",    label: "EARNINGS",    icon: "▤", group: "CORE" },
  { to: "/lottery",     label: "LOTTERY",     icon: "◈", group: "CORE" },
  { to: "/pharma",      label: "PHARMA",      icon: "🧬", group: "CORE" },
  { to: "/trade-floor", label: "TRADE FLOOR", icon: "⚡", group: "TRADE FLOOR" },
  { to: "/options-desk", label: "OPTIONS DESK", icon: "OD", group: "TRADE FLOOR" },
  { to: "/performance", label: "PERFORMANCE", icon: "▶", group: "ANALYSIS" },
  { to: "/learning",    label: "LEARNING",    icon: "◆", group: "ANALYSIS" },
  { to: "/tf-engine",   label: "TRADE ENGINE", icon: "▼", group: "ANALYSIS" },
  { to: "/audit-logs",  label: "AUDIT LOGS",  icon: "AL", group: "SYSTEM" },
  { to: "/settings",    label: "SETTINGS",    icon: "▥", group: "SYSTEM" },
];

if (!NAV.some(n => n.to === "/georisk")) {
  NAV.splice(8, 0, { to: "/georisk", label: "GEORISK", icon: "GR", group: "CORE" });
}

if (!NAV.some(n => n.to === "/macro")) {
  NAV.splice(9, 0, { to: "/macro", label: "MACRO", icon: "MX", group: "CORE" });
}

const NAV_LOGOS = {
  "/portfolio-manager": { logo: "PM", color: "#c8a84b" },
  "/": { logo: "CC", color: "#ef4444" },
  "/scanner": { logo: "SC", color: "#5eead4" },
  "/intel": { logo: "IF", color: "#60a5fa" },
  "/contracts": { logo: "CT", color: "#f59e0b" },
  "/sec": { logo: "SEC", color: "#a78bfa" },
  "/earnings": { logo: "ER", color: "#4ade80" },
  "/lottery": { logo: "LT", color: "#facc15" },
  "/pharma": { logo: "RX", color: "#f472b6" },
  "/georisk": { logo: "GR", color: "#fb7185" },
  "/macro": { logo: "MX", color: "#38bdf8" },
  "/trade-floor": { logo: "TF", color: "#4ade80" },
  "/options-desk": { logo: "OD", color: "#c8a84b" },
  "/performance": { logo: "PX", color: "#22c55e" },
  "/learning": { logo: "LN", color: "#5eead4" },
  "/tf-engine": { logo: "TE", color: "#f97316" },
  "/audit-logs": { logo: "AL", color: "#e879f9" },
  "/settings": { logo: "ST", color: "#9ca3af" },
};

NAV.forEach(item => Object.assign(item, NAV_LOGOS[item.to] || { logo: item.icon, color: accent }));

// US market hours: 9:30 - 16:00 ET (UTC-5 / UTC-4 DST)
function getMarketStatus() {
  const now = new Date();
  const utcH = now.getUTCHours();
  const utcM = now.getUTCMinutes();
  // ET = UTC-4 in DST. 9:30 ET = 13:30 UTC. 16:00 ET = 20:00 UTC.
  const minutes = utcH * 60 + utcM;
  const openMin = 13 * 60 + 30;
  const closeMin = 20 * 60;
  const day = now.getUTCDay();
  if (day === 0 || day === 6) return { state: "CLOSED", color: "#6b7280" };
  if (minutes < openMin) return { state: "PRE-MARKET", color: "#fb923c" };
  if (minutes < closeMin) return { state: "LIVE", color: "#4ade80" };
  return { state: "POST", color: "#fb923c" };
}

// Compute precise days/hours/minutes until the event timestamp.
function timeUntilEvent(event) {
  try {
    const target = event?.datetime_et ? new Date(event.datetime_et) : new Date(`${event?.date}T${event?.time_et || "09:30"}:00-04:00`);
    const diffMs = target - new Date();
    if (diffMs <= 0) return null;
    const days = Math.floor(diffMs / 86400000);
    const hours = Math.floor((diffMs % 86400000) / 3600000);
    const mins = Math.floor((diffMs % 3600000) / 60000);
    if (days >= 1) return `${days}D ${hours}H`;
    if (hours >= 1) return `${hours}H ${mins}M`;
    return `${mins}M`;
  } catch {
    return null;
  }
}

function useNextMacroEvent() {
  const [next, setNext] = useState(null);
  useEffect(() => {
    let cancelled = false;
    const load = async () => {
      try {
        const API = `${(process.env.REACT_APP_BACKEND_URL || "").replace(/\/$/, "")}/api`;
        const r = await fetch(`${API}/v32/macro?days_ahead=14`);
        const d = await r.json();
        const ev = (d.events || []).find(e => e.days_until >= 0);
        if (!cancelled) setNext(ev || null);
      } catch {}
    };
    load();
    const id = setInterval(load, 5 * 60 * 1000); // refresh every 5 min
    return () => { cancelled = true; clearInterval(id); };
  }, []);
  return next;
}

function useClock() {
  const [now, setNow] = useState(new Date());
  useEffect(() => {
    const id = setInterval(() => setNow(new Date()), 1000);
    return () => clearInterval(id);
  }, []);
  return now;
}

function SystemBar() {
  const now = useClock();
  const market = getMarketStatus();
  const nextMacro = useNextMacroEvent();
  const dateStr = now.toLocaleDateString("en-US", {
    timeZone: "America/New_York",
    weekday: "short", month: "short", day: "2-digit",
  }).toUpperCase();
  const timeStr = now.toLocaleTimeString("en-US", {
    timeZone: "America/New_York",
    hour: "2-digit", minute: "2-digit", second: "2-digit", hour12: false,
  });
  const countdown = nextMacro ? timeUntilEvent(nextMacro) : null;
  const macroColor = nextMacro
    ? (nextMacro.is_imminent ? "#fb923c" : (nextMacro.warns_sectors?.length ? "#fbbf24" : "#5eead4"))
    : muted;
  return (
    <div data-testid="system-bar" style={{
      display: "flex", alignItems: "center", gap: 18,
      padding: "6px 18px", background: "#03030680",
      borderBottom: hairline, fontSize: 10, letterSpacing: "0.14em",
      color: muted, fontFamily: "JetBrains Mono, Courier New",
      backdropFilter: "blur(6px)",
    }}>
      <span style={{ display: "flex", alignItems: "center", gap: 6 }}>
        <span className={`dot dot-${market.color === "#4ade80" ? "green" : market.color === "#fb923c" ? "amber" : "red"} pulse-dot`} />
        <span style={{ color: market.color, fontWeight: 700 }}>NYSE · {market.state}</span>
      </span>
      <span style={{ color: dim }}>│</span>
      <span className="num" style={{ color: labelLight }}>{dateStr}</span>
      <span style={{ color: dim }}>│</span>
      <span className="num" style={{ color: accent, fontWeight: 600 }}>
        {timeStr} ET<span className="blink">_</span>
      </span>
      <span style={{ color: dim }}>│</span>
      {nextMacro ? (
        <span data-testid="macro-countdown" style={{
          display: "flex", alignItems: "center", gap: 6,
          padding: "2px 10px",
          border: `0.5px solid ${macroColor}55`,
          background: `${macroColor}10`,
        }}>
          <span style={{ color: macroColor }}>🌐</span>
          <span style={{ color: muted }}>NEXT:</span>
          <span style={{ color: macroColor, fontWeight: 700 }}>{nextMacro.tag}</span>
          <span style={{ color: muted }}>IN</span>
          <span className="num" style={{ color: macroColor, fontWeight: 700 }}>{countdown}</span>
        </span>
      ) : (
        <span style={{ color: muted }}>🌐 NO MACRO IN WINDOW</span>
      )}
      <span style={{ marginLeft: "auto", display: "flex", gap: 14 }}>
        <span><span className="dot dot-green" /> <span style={{ marginLeft: 6 }}>FEED</span></span>
        <span><span className="dot dot-teal" /> <span style={{ marginLeft: 6 }}>LEARNING</span></span>
        <span><span className="dot dot-amber" /> <span style={{ marginLeft: 6 }}>BOT</span></span>
      </span>
    </div>
  );
}

function useLiveAlertCounts() {
  const [counts, setCounts] = useState({ dh: 0, xf: 0, locks: 0, lottery_hot: 0 });
  useEffect(() => {
    let cancelled = false;
    const API = `${(process.env.REACT_APP_BACKEND_URL || "").replace(/\/$/, "")}/api`;
    const load = async () => {
      try {
        const [dh, xf, conv, lot] = await Promise.all([
          fetch(`${API}/v32/dark_horse?days=2`).then(r => r.json()).catch(() => []),
          fetch(`${API}/v32/x_factor?days=2`).then(r => r.json()).catch(() => []),
          fetch(`${API}/v32/conviction`).then(r => r.json()).catch(() => ({})),
          fetch(`${API}/v32/lottery/current`).then(r => r.json()).catch(() => ({})),
        ]);
        if (cancelled) return;
        const hot = (lot.picks || []).filter(p => p.tier === "JACKPOT" || p.tier === "HOT").length;
        setCounts({
          dh: (dh || []).length,
          xf: (xf || []).length,
          locks: ((conv && conv.narrative_locks_14d) || []).length,
          lottery_hot: hot,
        });
      } catch {}
    };
    load();
    const id = setInterval(load, 60 * 1000);
    return () => { cancelled = true; clearInterval(id); };
  }, []);
  return counts;
}

export function CrtShell({ title, children, headerRight = null }) {
  const loc = useLocation();
  const nextMacro = useNextMacroEvent();
  const alerts = useLiveAlertCounts();
  const [isMobile, setIsMobile] = useState(false);
  const [drawerOpen, setDrawerOpen] = useState(false);
  useEffect(() => {
    const check = () => setIsMobile(window.innerWidth <= 768);
    check();
    window.addEventListener("resize", check);
    return () => window.removeEventListener("resize", check);
  }, []);
  // Close drawer on route change
  useEffect(() => { setDrawerOpen(false); }, [loc.pathname]);
  return (
    <>
      <div className="crt-vignette" />
      <div className="scanline-overlay" />
      <div className="crt-grain" />
      <div style={{
        height: "100vh", overflow: "hidden",
        background: pageBg, color: "#e5e7eb",
        display: "grid",
        gridTemplateColumns: isMobile ? "1fr" : "230px 1fr",
        fontFamily: "JetBrains Mono, Courier New, monospace",
        position: "relative", zIndex: 1,
      }}>
        {/* Mobile drawer backdrop */}
        {isMobile && drawerOpen && (
          <div data-testid="drawer-backdrop"
            onClick={() => setDrawerOpen(false)}
            style={{
              position: "fixed", inset: 0, background: "rgba(0,0,0,0.65)",
              zIndex: 90, backdropFilter: "blur(2px)",
            }} />
        )}
        {/* ── Sidebar ── */}
        <aside style={{
          background: `linear-gradient(180deg, ${cardBg} 0%, ${pageBg} 100%)`,
          borderRight: hairline,
          padding: "16px 12px 16px 14px",
          display: "flex", flexDirection: "column", gap: 18,
          height: "100vh", overflowY: "auto",
          position: isMobile ? "fixed" : "static",
          top: 0, left: 0, width: isMobile ? 260 : "auto",
          transform: isMobile && !drawerOpen ? "translateX(-100%)" : "translateX(0)",
          transition: "transform 0.28s ease-out",
          zIndex: 100,
          boxShadow: isMobile && drawerOpen ? "6px 0 30px rgba(0,0,0,0.6)" : "none",
        }}>
          {/* Brand block */}
          <Link to="/" style={{ textDecoration: "none" }}>
            <div className="corner-brackets" style={{
              padding: "14px 12px",
              border: hairlineAccent,
              background: `linear-gradient(135deg, rgba(200,168,75,0.06) 0%, transparent 70%)`,
              position: "relative", overflow: "hidden",
            }}>
              {/* animated scanline inside brand */}
              <div style={{
                position: "absolute", left: 0, right: 0, top: "30%", height: 1,
                background: `linear-gradient(90deg, transparent, ${accent}80, transparent)`,
                opacity: 0.4,
              }} />
              <div style={{ display: "flex", alignItems: "center", gap: 12, position: "relative" }}>
                <div style={{
                  width: 32, height: 32, border: `1.5px solid ${accent}`,
                  display: "flex", alignItems: "center", justifyContent: "center",
                  boxShadow: `0 0 14px rgba(200,168,75,0.3), inset 0 0 8px rgba(200,168,75,0.12)`,
                  position: "relative",
                }}>
                  <img src={terminalLogo} alt="Case Capital Trading Terminal" style={{
                    width: 28,
                    height: 28,
                    objectFit: "contain",
                    filter: "drop-shadow(0 0 6px rgba(200,168,75,0.65))",
                  }} />
                  {/* corner ticks on logo */}
                  <span style={{ position: "absolute", top: -3, left: -3, width: 4, height: 4, borderTop: `1px solid ${accent}`, borderLeft: `1px solid ${accent}` }} />
                  <span style={{ position: "absolute", bottom: -3, right: -3, width: 4, height: 4, borderBottom: `1px solid ${accent}`, borderRight: `1px solid ${accent}` }} />
                </div>
                <div style={{ flex: 1 }}>
                  <div className="glow-amber" style={{
                    fontSize: 18, color: accent, letterSpacing: "0.24em", fontWeight: 800,
                    lineHeight: 1,
                  }}>CASE CAP</div>
                  <div style={{ fontSize: 8, color: muted, letterSpacing: "0.16em", marginTop: 4 }}>
                    TRADING TERMINAL
                  </div>
                </div>
              </div>
              {/* macro micro-row inside brand */}
              {nextMacro && (
                <div style={{
                  marginTop: 10, paddingTop: 8, borderTop: hairline,
                  fontSize: 9, color: muted, letterSpacing: "0.14em",
                  display: "flex", justifyContent: "space-between", gap: 6,
                }}>
                  <span style={{ color: nextMacro.is_imminent ? "#fb923c" : accent2 }}>
                    🌐 {nextMacro.tag}
                  </span>
                  <span className="num" style={{
                    color: nextMacro.is_imminent ? "#fb923c" : labelLight, fontWeight: 700,
                  }}>{timeUntilEvent(nextMacro) || "—"}</span>
                </div>
              )}
            </div>
          </Link>

          {/* Live alerts micro-panel */}
          <div style={{
            padding: "10px 10px",
            border: hairline,
            background: "rgba(255,255,255,0.012)",
          }}>
            <div style={{
              fontSize: 8, color: dim, letterSpacing: "0.22em", marginBottom: 8,
              display: "flex", alignItems: "center", gap: 6, fontWeight: 700,
            }}>
              <span className="dot dot-green pulse-dot" />
              {"// LIVE ALERTS"}
            </div>
            <div style={{ display: "flex", flexDirection: "column", gap: 5 }}>
              <AlertChip icon="🐴" label="DARK HORSE" count={alerts.dh} color="#fb923c" to="/intel" />
              <AlertChip icon="⚡" label="X FACTOR" count={alerts.xf} color={accent2} to="/intel" />
              <AlertChip icon="🔒" label="N. LOCK" count={alerts.locks} color="#a78bfa" to="/intel" />
              <AlertChip icon="🎰" label="LOTTERY HOT" count={alerts.lottery_hot} color={accent} to="/lottery" />
            </div>
          </div>

          {/* Navigation — grouped */}
          <nav style={{ display: "flex", flexDirection: "column", gap: 1 }}>
            {["CORE", "TRADE FLOOR", "ANALYSIS", "SYSTEM"].map((group, gi) => {
              const groupItems = NAV.filter(n => n.group === group);
              if (groupItems.length === 0) return null;
              return (
                <div key={group} style={{ marginBottom: 6 }}>
                  <div style={{
                    fontSize: 8, color: dim, letterSpacing: "0.22em",
                    marginBottom: 4, marginTop: gi === 0 ? 0 : 6,
                    paddingLeft: 4, fontWeight: 700,
                    display: "flex", alignItems: "center", gap: 8,
                  }}>
                    <span>{"// "+group}</span>
                    <span style={{ flex: 1, height: 1, background: "rgba(255,255,255,0.04)" }} />
                  </div>
                  {groupItems.map((n, i) => {
                    const isActive = loc.pathname === n.to ||
                      (n.to !== "/" && loc.pathname.startsWith(n.to));
                    const hue = n.color || accent;
                    return (
                      <Link key={n.to} to={n.to}
                        data-testid={`nav-${n.label.toLowerCase().replace(' ', '-')}`}
                        className={`fade-in fade-in-${(gi+i+1) % 5 + 1}`}
                        style={{
                          display: "flex", alignItems: "center", gap: 10,
                          padding: "7px 10px",
                          fontSize: 11.5,
                          color: isActive ? hue : labelLight,
                          background: isActive ? `${hue}10` : "transparent",
                          borderLeft: `3px solid ${isActive ? hue : "transparent"}`,
                          textDecoration: "none",
                          letterSpacing: "0.08em",
                          fontWeight: isActive ? 700 : 500,
                          transition: "all 0.18s",
                          boxShadow: isActive ? `inset 0 0 14px ${hue}14` : "none",
                        }}
                        onMouseEnter={e => {
                          if (!isActive) {
                            e.currentTarget.style.background = "rgba(255,255,255,0.025)";
                            e.currentTarget.style.color = "#e5e7eb";
                          }
                        }}
                        onMouseLeave={e => {
                          if (!isActive) {
                            e.currentTarget.style.background = "transparent";
                            e.currentTarget.style.color = labelLight;
                          }
                        }}>
                        <span style={{
                          color: isActive ? hue : dim,
                          fontSize: 10, width: 12,
                          textShadow: isActive ? `0 0 6px ${hue}` : "none",
                        }}>{isActive ? "▸" : n.icon}</span>
                        <NavLogo item={n} active={isActive} />
                        <span style={{ flex: 1 }}>{n.label}</span>
                        {isActive && <span className="blink" style={{ color: hue, fontSize: 8 }}>●</span>}
                      </Link>
                    );
                  })}
                </div>
              );
            })}
          </nav>

          {/* Status block */}
          <div style={{ borderTop: hairline, paddingTop: 12 }}>
            <div style={{
              fontSize: 8, color: dim, letterSpacing: "0.22em", marginBottom: 6,
              paddingLeft: 4, fontWeight: 700,
            }}>{"// SYSTEM HEALTH"}</div>
            <div style={{ display: "flex", flexDirection: "column", gap: 4, fontSize: 10 }}>
              <StatusRow label="ENGINE" color="#4ade80" />
              <StatusRow label="LEARN" color={accent2} />
              <StatusRow label="BOT" color={accent} />
              <StatusRow label="FEED" color="#4ade80" />
            </div>
          </div>

          {/* Footer */}
          <div style={{
            marginTop: "auto", fontSize: 8, color: dim, letterSpacing: "0.14em",
            paddingTop: 10, borderTop: hairline,
          }}>
            <div>BUILD 3.2.0 · STABLE</div>
            <div style={{ marginTop: 3, color: muted }}>@CaseCapitalTerminalQuant</div>
            <div style={{ marginTop: 6, color: accent2, opacity: 0.6 }}>
              {"// THE MARKET NEVER SLEEPS"}
            </div>
          </div>
        </aside>

        {/* ── Main column ── */}
        <main style={{ overflowY: "auto", height: "100vh", display: "flex", flexDirection: "column" }}>
          {/* Sticky system bar */}
          <div style={{ position: "sticky", top: 0, zIndex: 10 }}>
            <SystemBar />
          </div>

          {/* Page header */}
          <div style={{
            padding: isMobile ? "14px 14px" : "22px 30px",
            borderBottom: hairline,
            display: "flex", alignItems: "center", justifyContent: "space-between",
            background: `linear-gradient(180deg, ${cardBg} 0%, ${pageBg} 100%)`,
            gap: 10,
          }}>
            {isMobile && (
              <button
                data-testid="mobile-menu-toggle"
                onClick={() => setDrawerOpen(v => !v)}
                aria-label="Toggle nav"
                style={{
                  background: "transparent", border: `0.5px solid ${accent}66`,
                  color: accent, padding: "8px 10px", cursor: "pointer",
                  fontSize: 14, letterSpacing: "0.14em", fontFamily: "inherit",
                  lineHeight: 1,
                }}>
                ☰
              </button>
            )}
            <div className="fade-in" style={{ flex: 1, minWidth: 0 }}>
              <div style={{
                fontSize: 9, color: muted, letterSpacing: "0.22em",
                display: "flex", alignItems: "center", gap: 8,
                whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis",
              }}>
                <span style={{ color: accent }}>▣</span>
                {!isMobile && (
                  <>
                    CASE CAP TERMINAL
                    <span style={{ color: dim }}>│</span>
                  </>
                )}
                <span style={{ color: accent2 }}>{loc.pathname.toUpperCase()}</span>
              </div>
              <div style={{
                fontSize: isMobile ? 18 : 26, color: accent, fontWeight: 700,
                letterSpacing: "0.08em", marginTop: 6,
                textShadow: "0 0 12px rgba(200,168,75,0.15)",
                whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis",
              }}>
                {title}
              </div>
            </div>
            <div className="fade-in fade-in-1">{headerRight}</div>
          </div>

          {/* Page body */}
          <div style={{ padding: isMobile ? "14px 12px" : "22px 30px", flex: 1 }}
                className="fade-in fade-in-2">
            {children}
          </div>
        </main>
      </div>
    </>
  );
}

function NavLogo({ item, active }) {
  const color = item.color || accent;
  const logo = item.logo || item.icon || "";
  return (
    <span style={{
      width: 24,
      height: 20,
      minWidth: 24,
      border: `0.5px solid ${active ? color : "rgba(255,255,255,0.12)"}`,
      background: active ? `${color}18` : "rgba(255,255,255,0.025)",
      color: active ? color : muted,
      display: "inline-flex",
      alignItems: "center",
      justifyContent: "center",
      fontSize: logo.length > 2 ? 6.5 : 8,
      fontWeight: 900,
      letterSpacing: logo.length > 2 ? "0.02em" : "0.06em",
      boxShadow: active ? `0 0 10px ${color}33, inset 0 0 8px ${color}14` : "none",
      position: "relative",
    }}>
      <span style={{
        position: "absolute",
        left: 2,
        top: 2,
        width: 3,
        height: 3,
        background: color,
        opacity: active ? 1 : 0.45,
      }} />
      {logo}
    </span>
  );
}

function StatusRow({ label, color }) {
  return (
    <div style={{
      display: "flex", alignItems: "center", gap: 8,
      padding: "4px 8px", color: labelLight, letterSpacing: "0.1em",
    }}>
      <span className="dot pulse-dot" style={{ background: color, boxShadow: `0 0 6px ${color}66` }} />
      <span style={{ flex: 1 }}>{label}</span>
      <span style={{ color: muted, fontSize: 8 }}>OK</span>
    </div>
  );
}

function AlertChip({ icon, label, count, color, to }) {
  return (
    <Link to={to} style={{
      display: "flex", alignItems: "center", gap: 8,
      padding: "6px 8px",
      background: count > 0 ? `${color}10` : "transparent",
      border: `0.5px solid ${count > 0 ? color + "55" : "transparent"}`,
      textDecoration: "none",
      transition: "all 0.18s",
    }}>
      <span style={{ fontSize: 11, filter: count > 0 ? "none" : "grayscale(0.6) opacity(0.4)" }}>{icon}</span>
      <span style={{
        flex: 1, fontSize: 9.5, letterSpacing: "0.14em",
        color: count > 0 ? color : muted, fontWeight: 600,
      }}>{label}</span>
      <span style={{
        fontSize: 11, fontWeight: 700, fontFamily: "JetBrains Mono",
        color: count > 0 ? color : muted,
        textShadow: count > 0 ? `0 0 6px ${color}80` : "none",
        minWidth: 16, textAlign: "right",
      }}>{count}</span>
    </Link>
  );
}

export { SystemBar };

export const tokens = {
  accent, accent2, dim, muted, labelLight, cardBg, cardBgHi, pageBg,
  hairline, hairlineAccent,
};

/**
 * Card — Bloomberg-style data panel with corner brackets and optional
 * accent stripe along the top edge.
 */
export function Card({ title, children, action = null, accentColor = accent }) {
  return (
    <div className="corner-brackets fade-in" style={{
      background: `linear-gradient(180deg, ${cardBg} 0%, ${pageBg} 200%)`,
      border: hairline,
      marginBottom: 22,
      position: "relative",
    }}>
      {/* Accent stripe */}
      <div style={{
        position: "absolute", top: 0, left: 0, right: 0,
        height: 1, background: `linear-gradient(90deg, ${accentColor} 0%, ${accentColor}33 30%, transparent 100%)`,
      }} />
      <div style={{ padding: "16px 22px 18px" }}>
        <div style={{
          display: "flex", justifyContent: "space-between", alignItems: "center",
          marginBottom: 14, paddingBottom: 10, borderBottom: hairline,
        }}>
          <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
            <span style={{ color: accentColor, fontSize: 9 }}>▸</span>
            <span style={{
              fontSize: 10, color: labelLight, letterSpacing: "0.18em", fontWeight: 600,
            }}>{title}</span>
          </div>
          {action}
        </div>
        {children}
      </div>
    </div>
  );
}

/**
 * Stat — KPI tile with optional trend indicator + tabular numerals.
 * `trend` can be "up" | "down" | null. `accentBar` paints a thin colored
 * bar on the left edge to call attention to the most-important metric.
 */
export function Stat({ label, value, color = "#e5e7eb", sub = null,
                       trend = null, accentBar = false, testid = null }) {
  const trendColor = trend === "up" ? "#4ade80" : trend === "down" ? "#f87171" : muted;
  return (
    <div data-testid={testid} style={{
      padding: "18px 22px", borderRight: hairline, flex: 1,
      position: "relative", transition: "background 0.15s",
      background: accentBar ? `linear-gradient(90deg, rgba(200,168,75,0.04) 0%, transparent 100%)` : "transparent",
    }} className="row-hover">
      {accentBar && (
        <div style={{
          position: "absolute", left: 0, top: 14, bottom: 14, width: 2,
          background: accent, boxShadow: `0 0 6px ${accent}80`,
        }} />
      )}
      <div style={{
        fontSize: 9, color: muted, letterSpacing: "0.18em", fontWeight: 600,
        display: "flex", alignItems: "center", gap: 6,
      }}>
        <span style={{ color: dim, fontSize: 8 }}>▸</span>
        {label}
      </div>
      <div className="num" style={{
        fontSize: 26, color, marginTop: 8, fontWeight: 600,
        fontFamily: "JetBrains Mono", letterSpacing: "0.02em",
        display: "flex", alignItems: "baseline", gap: 6,
      }}>
        {value}
        {trend && (
          <span style={{
            fontSize: 10, color: trendColor, fontWeight: 700,
            letterSpacing: "0.1em",
          }}>{trend === "up" ? "▲" : "▼"}</span>
        )}
      </div>
      {sub && <div style={{
        fontSize: 9, color: muted, marginTop: 6, letterSpacing: "0.14em",
      }}>{sub}</div>}
    </div>
  );
}
