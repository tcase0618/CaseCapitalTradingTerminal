import { useEffect, useState } from "react";
import axios from "axios";
import { CrtShell, Card, Stat, tokens } from "./CrtShell";

const API = `${process.env.REACT_APP_BACKEND_URL}/api`;
const { accent, accent2, dim, muted, labelLight, hairline } = tokens;

export default function IntelPage() {
  const [conviction, setConviction] = useState(null);
  const [dh, setDh] = useState([]);
  const [xf, setXf] = useState([]);
  const [macro, setMacro] = useState(null);

  useEffect(() => {
    axios.get(`${API}/v32/conviction`).then(r => setConviction(r.data)).catch(() => {});
    axios.get(`${API}/v32/dark_horse?days=14`).then(r => setDh(r.data || [])).catch(() => {});
    axios.get(`${API}/v32/x_factor?days=14`).then(r => setXf(r.data || [])).catch(() => {});
    axios.get(`${API}/v32/macro`).then(r => setMacro(r.data)).catch(() => {});
  }, []);

  const top3 = conviction?.top3 || [];
  const locks = conviction?.narrative_locks_14d || [];
  const events = macro?.events || [];

  return (
    <CrtShell title="INTEL FEED">
      <div style={{ display: "flex", background: tokens.cardBg, border: hairline, marginBottom: 22, flexWrap: "wrap" }}>
        <Stat label="MAX CONVICTION" value={top3.length} sub="TOP 3 TODAY" color={accent} accentBar />
        <Stat label="NARRATIVE LOCKS" value={locks.length} sub="14D" color="#a78bfa" />
        <Stat label="DARK HORSE" value={dh.length} sub="FINRA · 14D" color="#fb923c" />
        <Stat label="X FACTOR" value={xf.length} sub="SENTIMENT · 14D" color={accent2} />
        <Stat label="MACRO EVENTS" value={events.length} sub="NEXT 14D" />
        <Stat label="IMMINENT WARNINGS" value={macro?.imminent_warnings?.length || 0}
              color={(macro?.imminent_warnings?.length || 0) > 0 ? "#fb923c" : muted} sub="<48H" />
      </div>

      <Card title="🟢 MAX CONVICTION · TOP 3 PICKS" accentColor={accent}>
        {top3.length === 0 ? (
          <div style={{ color: muted, padding: 16 }}>
            No max-conviction picks yet — run a scan to surface today's top alignments.
          </div>
        ) : (
          <div style={{ display: "grid", gridTemplateColumns: "repeat(3, 1fr)", gap: 14 }}>
            {top3.map((p, i) => (
              <div key={p.ticker} data-testid={`conv-${p.ticker}`} className="corner-brackets" style={{
                padding: "16px 18px", border: `0.5px solid ${accent}66`,
                background: `linear-gradient(135deg, ${accent}10 0%, transparent 60%)`,
                position: "relative",
              }}>
                <div style={{ fontSize: 9, color: muted, letterSpacing: "0.18em" }}>RANK #{i+1}</div>
                <div style={{ fontSize: 28, color: accent, fontWeight: 700, marginTop: 4,
                              textShadow: `0 0 12px ${accent}40`, letterSpacing: "0.04em" }}>
                  ${p.ticker}
                </div>
                <div style={{ fontSize: 11, color: labelLight, marginTop: 6 }}>
                  CONVICTION: <span style={{ color: accent, fontWeight: 700 }}>{p.conviction_score}</span>
                  {" · "}AXIOM: <span style={{ color: accent }}>{p.axiom_score}</span>
                </div>
                <div style={{ fontSize: 10, color: muted, marginTop: 8, letterSpacing: "0.08em" }}>
                  {(p.components || []).join(" · ")}
                </div>
                <div style={{ fontSize: 11, color: "#e5e7eb", marginTop: 10, lineHeight: 1.5, fontStyle: "italic" }}>
                  {p.thesis || "—"}
                </div>
                {p.narrative_lock && (
                  <div style={{
                    marginTop: 10, padding: "4px 8px", background: "rgba(167,139,250,0.1)",
                    border: "0.5px solid #a78bfa", color: "#a78bfa",
                    fontSize: 10, letterSpacing: "0.14em", fontWeight: 700,
                    display: "inline-block",
                  }}>🔒 NARRATIVE LOCK</div>
                )}
              </div>
            ))}
          </div>
        )}
      </Card>

      <Card title="🐴 DARK HORSE · INSTITUTIONAL ACCUMULATION (FINRA)" accentColor="#fb923c">
        {dh.length === 0 ? (
          <div style={{ color: muted, padding: 16 }}>
            No Dark Horse alerts in the last 14 days. Fires when off-exchange ratio &gt;45%, block size &gt;10% ADV, and price closed above prior day +0.5%.
          </div>
        ) : (
          <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 12 }}>
            <thead>
              <tr style={{ color: dim, letterSpacing: "0.14em", textAlign: "left" }}>
                <th style={th}>DATE</th><th style={th}>TICKER</th>
                <th style={th}>OFF-EX %</th><th style={th}>BLOCK VOL</th>
                <th style={th}>% OF ADV</th><th style={th}>PREMIUM</th>
              </tr>
            </thead>
            <tbody>
              {dh.map((a, i) => (
                <tr key={i} className="row-hover" style={{ borderTop: hairline }}>
                  <td style={td}>{a.date}</td>
                  <td style={{ ...td, color: accent, fontWeight: 700 }}>${a.ticker}</td>
                  <td style={{ ...td, color: "#fb923c", fontWeight: 700 }}>{a.off_exchange_pct}%</td>
                  <td style={td}>{(a.block_volume / 1000).toFixed(0)}k</td>
                  <td style={td}>{a.block_pct_of_adv}%</td>
                  <td style={{ ...td, color: "#4ade80", fontWeight: 700 }}>+{a.premium_pct}%</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </Card>

      <Card title="⚡ X FACTOR · RETAIL SENTIMENT SURGE" accentColor={accent2}>
        {xf.length === 0 ? (
          <div style={{ color: muted, padding: 16 }}>
            No X Factor alerts in the last 14 days. Fires on StockTwits 10× mention spike, sentiment flip below 40 → above 65, or Google Trends spike from &lt;20 baseline to &gt;70.
          </div>
        ) : (
          <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 12 }}>
            <thead>
              <tr style={{ color: dim, letterSpacing: "0.14em", textAlign: "left" }}>
                <th style={th}>FIRED</th><th style={th}>TICKER</th>
                <th style={th}>PLATFORM</th><th style={th}>TYPE</th>
                <th style={th}>SPIKE</th><th style={th}>BULLISH</th>
              </tr>
            </thead>
            <tbody>
              {xf.map((a, i) => {
                const p = a.primary_trigger || {};
                return (
                  <tr key={i} className="row-hover" style={{ borderTop: hairline }}>
                    <td style={td}>{a.fired_at?.slice(0, 16).replace("T", " ")}</td>
                    <td style={{ ...td, color: accent, fontWeight: 700 }}>${a.ticker}</td>
                    <td style={{ ...td, color: accent2 }}>{p.platform}</td>
                    <td style={td}>{p.type}</td>
                    <td style={{ ...td, color: accent, fontWeight: 700 }}>
                      {p.spike_x ? `${p.spike_x}x` : p.ratio ? `${p.ratio}x` : "—"}
                    </td>
                    <td style={{ ...td, color: "#4ade80" }}>{p.bullish_pct}%</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        )}
      </Card>

      <Card title={`🌐 MACRO PULSE · UPCOMING ECONOMIC EVENTS${macro?.fred_available === false ? " · FRED OFFLINE" : ""}`}>
        {events.length === 0 ? (
          <div style={{ color: muted, padding: 16 }}>No events in window.</div>
        ) : (
          <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 12 }}>
            <thead>
              <tr style={{ color: dim, letterSpacing: "0.14em", textAlign: "left" }}>
                <th style={th}>DATE</th><th style={th}>DAYS</th>
                <th style={th}>EVENT</th><th style={th}>NAME</th>
                <th style={th}>WARNS</th><th style={th}>BOOSTS</th>
              </tr>
            </thead>
            <tbody>
              {events.map((e, i) => (
                <tr key={i} className="row-hover" style={{
                  borderTop: hairline,
                  background: e.is_imminent ? "rgba(251,146,60,0.04)" : "transparent",
                }}>
                  <td style={td}>{e.date}</td>
                  <td style={{ ...td, color: e.is_imminent ? "#fb923c" : muted, fontWeight: 700 }}>
                    {e.days_until}D {e.is_imminent && "⚠"}
                  </td>
                  <td style={{ ...td, color: accent, fontWeight: 700 }}>{e.tag}</td>
                  <td style={td}>{e.name}</td>
                  <td style={{ ...td, color: "#f87171" }}>
                    {(e.warns_sectors || []).join(" · ") || "—"}
                  </td>
                  <td style={{ ...td, color: "#4ade80" }}>
                    {(e.boosts_sectors || []).join(" · ") || "—"}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </Card>
    </CrtShell>
  );
}

const th = { padding: "10px 8px", fontSize: 10, color: dim, letterSpacing: "0.14em", fontWeight: 400 };
const td = { padding: "10px 8px", color: labelLight, letterSpacing: "0.04em" };
