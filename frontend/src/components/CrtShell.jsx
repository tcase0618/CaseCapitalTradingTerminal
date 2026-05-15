// Shared CRT shell — sidebar nav + page wrapper. Used by all sub-pages.
// Same Bloomberg/Courier aesthetic, BIGGER font for readability.
import { Link, useLocation } from "react-router-dom";

const accent = "#c8a84b";
const dim = "#374151";
const muted = "#6b7280";
const labelLight = "#4a5568";
const cardBg = "#0c0c12";
const pageBg = "#06060a";
const hairline = "0.5px solid rgba(255,255,255,0.06)";

const NAV = [
  { to: "/", label: "DASHBOARD" },
  { to: "/performance", label: "PERFORMANCE" },
  { to: "/learning", label: "LEARNING" },
  { to: "/settings", label: "SETTINGS" },
];

export function CrtShell({ title, children, headerRight = null }) {
  const loc = useLocation();
  return (
    <>
      <div className="scanline-overlay" />
      <div style={{
        minHeight: "100vh", background: pageBg, color: "#e5e7eb",
        display: "grid", gridTemplateColumns: "200px 1fr",
        fontFamily: "Courier New, monospace",
      }}>
        {/* Sidebar */}
        <aside style={{
          background: cardBg, borderRight: hairline,
          padding: "20px 16px", display: "flex", flexDirection: "column", gap: 24,
          position: "sticky", top: 0, height: "100vh", overflowY: "auto",
        }}>
          <Link to="/" style={{ textDecoration: "none" }}>
            <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
              <div style={{
                width: 26, height: 26, border: `1.5px solid ${accent}`,
                display: "flex", alignItems: "center", justifyContent: "center",
              }}>
                <div style={{ width: 9, height: 9, background: accent }} />
              </div>
              <span style={{ fontSize: 14, color: accent, letterSpacing: "0.14em", fontWeight: 700 }}>
                AXIOM
              </span>
            </div>
            <div style={{ fontSize: 10, color: dim, marginTop: 4, letterSpacing: "0.12em" }}>
              INTELLIGENCE PLATFORM
            </div>
          </Link>

          <nav style={{ display: "flex", flexDirection: "column", gap: 2 }}>
            <div style={{ fontSize: 10, color: dim, letterSpacing: "0.14em", marginBottom: 8 }}>
              {"// NAVIGATION"}
            </div>
            {NAV.map((n) => {
              const isActive = loc.pathname === n.to ||
                (n.to !== "/" && loc.pathname.startsWith(n.to));
              return (
                <Link key={n.to} to={n.to}
                  data-testid={`nav-${n.label.toLowerCase()}`}
                  style={{
                    display: "block",
                    padding: "8px 10px",
                    fontSize: 13,
                    color: isActive ? accent : labelLight,
                    background: isActive ? "rgba(200,168,75,0.05)" : "transparent",
                    borderLeft: `3px solid ${isActive ? accent : "transparent"}`,
                    textDecoration: "none",
                    letterSpacing: "0.08em",
                    fontWeight: isActive ? 700 : 400,
                    transition: "all 0.15s",
                  }}>
                  [{n.label}]
                </Link>
              );
            })}
          </nav>

          <div style={{ marginTop: "auto", fontSize: 10, color: dim, letterSpacing: "0.1em" }}>
            <div>VERSION 3.0.0</div>
            <div>FEATURE_VERSION 3.0</div>
            <div style={{ marginTop: 8, color: muted }}>// @Quantninjabot</div>
          </div>
        </aside>

        {/* Main */}
        <main style={{ overflowY: "auto", minHeight: "100vh" }}>
          {/* Page header */}
          <div style={{
            padding: "20px 28px", borderBottom: hairline,
            display: "flex", alignItems: "center", justifyContent: "space-between",
            background: cardBg,
          }}>
            <div>
              <div style={{ fontSize: 10, color: dim, letterSpacing: "0.18em" }}>
                AXIOM INTELLIGENCE PLATFORM
              </div>
              <div style={{ fontSize: 22, color: accent, fontWeight: 700, letterSpacing: "0.06em", marginTop: 4 }}>
                {title}
              </div>
            </div>
            {headerRight}
          </div>
          <div style={{ padding: "20px 28px" }}>
            {children}
          </div>
        </main>
      </div>
    </>
  );
}

export const tokens = { accent, dim, muted, labelLight, cardBg, pageBg, hairline };

export function Card({ title, children, action = null }) {
  return (
    <div style={{
      background: cardBg, border: hairline,
      marginBottom: 20, padding: "18px 22px",
    }}>
      <div style={{
        display: "flex", justifyContent: "space-between", alignItems: "center",
        fontSize: 11, color: dim, letterSpacing: "0.16em", marginBottom: 14,
      }}>
        <span>{"// " + title}</span>
        {action}
      </div>
      {children}
    </div>
  );
}

export function Stat({ label, value, color = "#fff", sub = null }) {
  return (
    <div style={{ padding: "16px 22px", borderRight: hairline, flex: 1 }}>
      <div style={{ fontSize: 11, color: dim, letterSpacing: "0.14em" }}>{label}</div>
      <div style={{
        fontSize: 28, color, marginTop: 6, fontWeight: 300,
        fontFamily: "Courier New", letterSpacing: "0.04em",
      }}>{value}</div>
      {sub && <div style={{ fontSize: 10, color: dim, marginTop: 4, letterSpacing: "0.1em" }}>{sub}</div>}
    </div>
  );
}
