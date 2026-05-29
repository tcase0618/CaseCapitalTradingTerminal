import { useEffect, useState } from "react";
import axios from "axios";
import { CrtShell, Card, Stat, tokens } from "./CrtShell";

const API = `${process.env.REACT_APP_BACKEND_URL}/api`;
const { accent, accent2, dim, muted, labelLight, hairline } = tokens;
const DAYS = ["MONDAY", "TUESDAY", "WEDNESDAY", "THURSDAY", "FRIDAY"];

const beatColor = (p) =>
  p == null ? muted : p >= 65 ? "#4ade80" : p >= 45 ? accent : "#f87171";

const stratColor = (s) =>
  s?.includes("LONG CALL") ? "#4ade80"
  : s?.includes("BEAR") ? "#f87171"
  : s?.includes("AVOID") ? muted
  : accent;

export default function EarningsPage() {
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(true);
  useEffect(() => {
    setLoading(true);
    axios.get(`${API}/v32/earnings_week`).then(r => {
      setData(r.data); setLoading(false);
    }).catch(() => setLoading(false));
  }, []);

  const total = data?.total || 0;
  const byDay = data?.by_day || {};
  const allRows = Object.values(byDay).flat();
  const strongBeats = allRows.filter(r => r.beat_probability_pct >= 65).length;
  const weakBeats = allRows.filter(r => r.beat_probability_pct < 45).length;
  const axiomMatches = allRows.filter(r => r.axiom_match).length;
  const top3 = [...allRows].sort((a, b) => b.beat_probability_pct - a.beat_probability_pct).slice(0, 3);

  return (
    <CrtShell title="EARNINGS · CURRENT WEEK">
      <div style={{ display: "flex", background: tokens.cardBg, border: hairline, marginBottom: 22, flexWrap: "wrap" }}>
        <Stat label="WEEK OF" value={data?.week_of?.slice(5) || "—"} sub={data?.week_end?.slice(5) || ""} accentBar />
        <Stat label="TOTAL EARNINGS" value={total} color={accent} />
        <Stat label="STRONG BEATS" value={strongBeats} sub="≥65% PROB" color="#4ade80" />
        <Stat label="LIKELY MISSES" value={weakBeats} sub="<45% PROB" color="#f87171" />
        <Stat label="AXIOM MATCHES" value={axiomMatches} sub="IN SCAN" color={accent2} />
      </div>

      {/* Hero: top 3 by beat probability */}
      {top3.length > 0 && (
        <Card title="🟢 TOP 3 EARNINGS PLAYS THIS WEEK" accentColor="#4ade80">
          <div style={{ display: "grid", gridTemplateColumns: "repeat(3, 1fr)", gap: 14 }}>
            {top3.map((r, i) => (
              <div key={r.ticker} className="corner-brackets" style={{
                padding: "14px 18px",
                border: `0.5px solid ${beatColor(r.beat_probability_pct)}55`,
                background: `linear-gradient(135deg, ${beatColor(r.beat_probability_pct)}10 0%, transparent 70%)`,
              }}>
                <div style={{
                  display: "flex", justifyContent: "space-between", alignItems: "center",
                  fontSize: 9, color: muted, letterSpacing: "0.18em",
                }}>
                  <span>#{i+1} · {r.am_pm || "TBD"}</span>
                  {r.axiom_match && (
                    <span style={{
                      color: accent2, padding: "1px 5px",
                      border: `0.5px solid ${accent2}`, fontSize: 8,
                    }}>AXIOM MATCH</span>
                  )}
                </div>
                <div style={{ fontSize: 30, color: accent, fontWeight: 700, marginTop: 6, letterSpacing: "0.04em" }}>
                  ${r.ticker}
                </div>
                <div style={{ fontSize: 10, color: muted, marginTop: 3 }}>
                  {(r.sector || "—").slice(0, 24)}
                </div>
                <div style={{
                  fontSize: 36, color: beatColor(r.beat_probability_pct), fontWeight: 700,
                  marginTop: 10, fontFamily: "JetBrains Mono",
                  textShadow: `0 0 12px ${beatColor(r.beat_probability_pct)}40`,
                }}>
                  {r.beat_probability_pct?.toFixed(0)}%
                </div>
                <div style={{ fontSize: 9, color: muted, letterSpacing: "0.14em" }}>BEAT PROBABILITY</div>
                <div style={{
                  marginTop: 12, padding: "6px 10px",
                  border: `0.5px solid ${stratColor(r.strategy)}66`,
                  color: stratColor(r.strategy), fontSize: 11, fontWeight: 700,
                  letterSpacing: "0.12em", textAlign: "center",
                }}>{r.strategy}</div>
              </div>
            ))}
          </div>
        </Card>
      )}

      {loading && (
        <div style={{ color: muted, padding: 30, textAlign: "center" }}>
          LOADING EARNINGS DATA...
        </div>
      )}

      {!loading && total === 0 && (
        <Card title="NO EARNINGS THIS WEEK">
          <div style={{ color: muted, padding: 20 }}>
            No tickers report between Monday and Friday this week.
          </div>
        </Card>
      )}

      {DAYS.map(day => {
        const rows = byDay[day] || [];
        if (rows.length === 0) return null;
        return (
          <Card key={day} title={`${day} · ${rows.length} REPORTING`}>
            <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 12 }}>
              <thead>
                <tr style={{ color: dim, letterSpacing: "0.14em", textAlign: "left" }}>
                  <th style={th}>TICKER</th>
                  <th style={th}>WHEN</th>
                  <th style={th}>SECTOR</th>
                  <th style={th}>BEAT PROB</th>
                  <th style={th}>MOM 20D</th>
                  <th style={th}>SHORT %</th>
                  <th style={th}>STRATEGY</th>
                  <th style={th}>AXIOM</th>
                </tr>
              </thead>
              <tbody>
                {rows.map(r => (
                  <tr key={r.ticker} data-testid={`earnings-${r.ticker}`}
                      className="row-hover" style={{ borderTop: hairline }}>
                    <td style={{ ...td, color: accent, fontWeight: 700 }}>${r.ticker}</td>
                    <td style={{ ...td, color: r.am_pm === "AM" ? "#fb923c" : "#60a5fa", fontWeight: 700 }}>
                      {r.am_pm || "—"}
                    </td>
                    <td style={{ ...td, color: muted }}>{(r.sector || "—").slice(0, 14)}</td>
                    <td style={{ ...td, color: beatColor(r.beat_probability_pct), fontWeight: 700 }}>
                      {r.beat_probability_pct?.toFixed(0)}%
                    </td>
                    <td style={{ ...td, color: r.momentum_20d_pct >= 0 ? "#4ade80" : "#f87171" }}>
                      {r.momentum_20d_pct != null ? `${r.momentum_20d_pct >= 0 ? "+" : ""}${r.momentum_20d_pct}%` : "—"}
                    </td>
                    <td style={td}>
                      {r.short_pct != null ? `${(r.short_pct * 100).toFixed(1)}%` : "—"}
                    </td>
                    <td style={{ ...td, color: stratColor(r.strategy), fontWeight: 700 }}>
                      {r.strategy}
                    </td>
                    <td style={td}>
                      {r.axiom_match && (
                        <span style={{
                          fontSize: 9, color: accent2, padding: "2px 6px",
                          border: `0.5px solid ${accent2}`, letterSpacing: "0.1em",
                        }}>MATCH</span>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </Card>
        );
      })}
    </CrtShell>
  );
}

const th = { padding: "10px 8px", fontSize: 10, color: dim, letterSpacing: "0.14em", fontWeight: 400 };
const td = { padding: "10px 8px", color: labelLight, letterSpacing: "0.04em" };
