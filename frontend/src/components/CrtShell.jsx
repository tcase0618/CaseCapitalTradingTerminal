// Shared CRT shell — sidebar nav + page wrapper. Used by all sub-pages.
// Refined Bloomberg terminal aesthetic: corner brackets, live system bar,
// status dots, micro-animations.
import { Link, useLocation } from "react-router-dom";
import { useEffect, useState } from "react";

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
  { to: "/", label: "DASHBOARD", icon: "▣" },
  { to: "/performance", label: "PERFORMANCE", icon: "▶" },
  { to: "/learning", label: "LEARNING", icon: "◆" },
  { to: "/settings", label: "SETTINGS", icon: "▥" },
];

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
  const dateStr = now.toLocaleDateString("en-US", {
    timeZone: "America/New_York",
    weekday: "short", month: "short", day: "2-digit",
  }).toUpperCase();
  const timeStr = now.toLocaleTimeString("en-US", {
    timeZone: "America/New_York",
    hour: "2-digit", minute: "2-digit", second: "2-digit", hour12: false,
  });
  return (
    <div data-testid="system-bar" style={{
      display: "flex", alignItems: "center", gap: 20,
      padding: "5px 16px", background: "#03030680",
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
      <span style={{ color: muted }}>FEED · MASSIVE/FINNHUB</span>
      <span style={{ marginLeft: "auto", display: "flex", gap: 14 }}>
        <span><span className="dot dot-green" /> <span style={{ marginLeft: 6 }}>SCAN ENGINE</span></span>
        <span><span className="dot dot-teal" /> <span style={{ marginLeft: 6 }}>LEARNING</span></span>
        <span><span className="dot dot-amber" /> <span style={{ marginLeft: 6 }}>BOT WEBHOOK</span></span>
      </span>
    </div>
  );
}

export function CrtShell({ title, children, headerRight = null }) {
  const loc = useLocation();
  return (
    <>
      <div className="crt-vignette" />
      <div className="scanline-overlay" />
      <div className="crt-grain" />
      <div style={{
        height: "100vh", overflow: "hidden",
        background: pageBg, color: "#e5e7eb",
        display: "grid", gridTemplateColumns: "220px 1fr",
        fontFamily: "JetBrains Mono, Courier New, monospace",
        position: "relative", zIndex: 1,
      }}>
        {/* ── Sidebar ── */}
        <aside style={{
          background: `linear-gradient(180deg, ${cardBg} 0%, ${pageBg} 100%)`,
          borderRight: hairline,
          padding: "18px 14px",
          display: "flex", flexDirection: "column", gap: 22,
          height: "100vh", overflowY: "auto",
        }}>
          {/* Brand */}
          <Link to="/" style={{ textDecoration: "none" }}>
            <div className="corner-brackets" style={{
              padding: "10px 8px",
              border: hairlineAccent,
              background: "rgba(200,168,75,0.025)",
            }}>
              <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
                <div style={{
                  width: 24, height: 24, border: `1.5px solid ${accent}`,
                  display: "flex", alignItems: "center", justifyContent: "center",
                  boxShadow: `0 0 10px rgba(200,168,75,0.25), inset 0 0 6px rgba(200,168,75,0.1)`,
                }}>
                  <div style={{ width: 8, height: 8, background: accent }} />
                </div>
                <div>
                  <div className="glow-amber" style={{
                    fontSize: 16, color: accent, letterSpacing: "0.18em", fontWeight: 800,
                  }}>AXIOM</div>
                  <div style={{ fontSize: 8, color: muted, letterSpacing: "0.14em", marginTop: 1 }}>
                    v3.0 · INTEL
                  </div>
                </div>
              </div>
            </div>
          </Link>

          {/* Navigation */}
          <nav style={{ display: "flex", flexDirection: "column", gap: 2 }}>
            <div style={{
              fontSize: 9, color: dim, letterSpacing: "0.18em", marginBottom: 6,
              paddingLeft: 4,
            }}>
              {"// NAVIGATION"}
            </div>
            {NAV.map((n, i) => {
              const isActive = loc.pathname === n.to ||
                (n.to !== "/" && loc.pathname.startsWith(n.to));
              return (
                <Link key={n.to} to={n.to}
                  data-testid={`nav-${n.label.toLowerCase()}`}
                  className={`fade-in fade-in-${i+1} hover-glow`}
                  style={{
                    display: "flex", alignItems: "center", gap: 8,
                    padding: "8px 10px",
                    fontSize: 11.5,
                    color: isActive ? accent : labelLight,
                    background: isActive ? "rgba(200,168,75,0.06)" : "transparent",
                    borderLeft: `3px solid ${isActive ? accent : "transparent"}`,
                    borderRight: hairline,
                    textDecoration: "none",
                    letterSpacing: "0.1em",
                    fontWeight: isActive ? 700 : 500,
                    transition: "all 0.18s",
                    boxShadow: isActive ? `inset 0 0 12px rgba(200,168,75,0.08)` : "none",
                  }}>
                  <span style={{
                    color: isActive ? accent : dim,
                    fontSize: 9, width: 12,
                  }}>{isActive ? "▸" : n.icon}</span>
                  <span>{n.label}</span>
                </Link>
              );
            })}
          </nav>

          {/* Status block */}
          <div style={{ borderTop: hairline, paddingTop: 14 }}>
            <div style={{
              fontSize: 9, color: dim, letterSpacing: "0.18em", marginBottom: 8, paddingLeft: 4,
            }}>{"// SYSTEM"}</div>
            <div style={{ display: "flex", flexDirection: "column", gap: 6, fontSize: 10 }}>
              <StatusRow label="ENGINE" color="#4ade80" />
              <StatusRow label="LEARN" color={accent2} />
              <StatusRow label="BOT" color={accent} />
              <StatusRow label="FEED" color="#4ade80" />
            </div>
          </div>

          {/* Footer */}
          <div style={{
            marginTop: "auto", fontSize: 9, color: dim, letterSpacing: "0.12em",
            paddingTop: 14, borderTop: hairline,
          }}>
            <div>V3.0.0 · FEAT 3.0</div>
            <div style={{ marginTop: 4, color: muted }}>@Quantninjabot</div>
            <div style={{ marginTop: 8, color: accent2, opacity: 0.6 }}>
              {"// MARKETS NEVER SLEEP"}
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
            padding: "22px 30px",
            borderBottom: hairline,
            display: "flex", alignItems: "center", justifyContent: "space-between",
            background: `linear-gradient(180deg, ${cardBg} 0%, ${pageBg} 100%)`,
          }}>
            <div className="fade-in">
              <div style={{
                fontSize: 9, color: muted, letterSpacing: "0.22em",
                display: "flex", alignItems: "center", gap: 8,
              }}>
                <span style={{ color: accent }}>▣</span>
                AXIOM INTELLIGENCE PLATFORM
                <span style={{ color: dim }}>│</span>
                <span style={{ color: accent2 }}>{loc.pathname.toUpperCase()}</span>
              </div>
              <div style={{
                fontSize: 26, color: accent, fontWeight: 700,
                letterSpacing: "0.08em", marginTop: 6,
                textShadow: "0 0 12px rgba(200,168,75,0.15)",
              }}>
                {title}
              </div>
            </div>
            <div className="fade-in fade-in-1">{headerRight}</div>
          </div>

          {/* Page body */}
          <div style={{ padding: "22px 30px", flex: 1 }} className="fade-in fade-in-2">
            {children}
          </div>
        </main>
      </div>
    </>
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
