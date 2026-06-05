import { useEffect, useState } from "react";
import axios from "axios";
import { CrtShell, Card, Stat, tokens } from "./CrtShell";

const API = `${process.env.REACT_APP_BACKEND_URL}/api`;
const { accent, accent2, dim, muted, labelLight, hairline, cardBg } = tokens;

const FORM_COLORS = {
  "SC 13D":   "#f87171",
  "SC 13G":   "#fbbf24",
  "8-K":      accent2,
  "Form 4":   "#4ade80",
  "13F-HR":   labelLight,
};

const th = { padding: "10px 8px", fontSize: 10, color: dim, letterSpacing: "0.14em", fontWeight: 400, textAlign: "left" };
const td = { padding: "10px 8px", color: labelLight, letterSpacing: "0.04em", fontSize: 12 };

export default function SECPage() {
  const [filings, setFilings] = useState([]);
  const [form, setForm] = useState("");
  const [days, setDays] = useState(7);
  const [expanded, setExpanded] = useState(null);
  const [polling, setPolling] = useState(false);

  const load = () => {
    const p = new URLSearchParams({ days });
    if (form) p.append("form", form);
    axios.get(`${API}/sec/filings?${p}`).then(r => setFilings(r.data.filings || []));
  };
  useEffect(load, [form, days]);

  const triggerPoll = async () => {
    setPolling(true);
    try { await axios.post(`${API}/sec/poll`); load(); }
    finally { setPolling(false); }
  };

  const lockedCount = filings.filter(f => f.narrative_lock_badge).length;

  return (
    <CrtShell title="SEC FILINGS · EDGAR LIVE"
      headerRight={
        <button data-testid="sec-poll-btn" onClick={triggerPoll} disabled={polling}
          style={{
            background: "transparent", border: `0.5px solid ${accent}`,
            color: accent, fontSize: 11, padding: "8px 16px",
            cursor: polling ? "wait" : "pointer", letterSpacing: "0.14em",
            fontFamily: "JetBrains Mono", fontWeight: 700,
          }}>[ {polling ? "POLLING..." : "▶ POLL EDGAR"} ]</button>
      }>
      <div style={{ display: "flex", background: cardBg, border: hairline, marginBottom: 22, flexWrap: "wrap" }}>
        <Stat label="FILINGS · 7D" value={filings.length} sub="PUBLIC TICKERS" color={accent} accentBar />
        <Stat label="NARRATIVE LOCK" value={lockedCount} sub="≥70" color="#fbbf24" />
        <Stat label="ACTIVIST 13D" value={filings.filter(f => f.form === "SC 13D" && f.activist).length} sub="HIGHEST PRI" color="#f87171" />
        <Stat label="8-K MATERIAL" value={filings.filter(f => f.form === "8-K").length} sub="1.01/1.02/2.01" color={accent2} />
        <Stat label="FORM 4 CLUSTER" value={filings.filter(f => f.form === "Form 4").length} sub="OPEN-MKT BUY" color="#4ade80" />
      </div>

      <Card title="FILTERS">
        <div style={{ display: "flex", gap: 16, padding: "8px 0" }}>
          <Select label="FORM" value={form} onChange={setForm}
            opts={[["", "ALL"], ["SC 13D", "13D"], ["SC 13G", "13G"], ["8-K", "8-K"], ["Form 4", "FORM 4"], ["13F-HR", "13F"]]} />
          <Select label="DAYS" value={days} onChange={v => setDays(Number(v))}
            opts={[[1, "1D"], [3, "3D"], [7, "7D"]]} />
        </div>
      </Card>

      <Card title={`FILINGS · SORTED BY SIGNIFICANCE`}>
        {!filings.length ? (
          <div style={{ color: muted, padding: 20 }}>No filings — click POLL EDGAR.</div>
        ) : (
          <table style={{ width: "100%", borderCollapse: "collapse" }}>
            <thead><tr>
              <th style={th}>TICKER</th><th style={th}>FORM</th>
              <th style={th}>COMPANY</th><th style={th}>SIG</th>
              <th style={th}>NLS</th><th style={th}></th>
            </tr></thead>
            <tbody>
              {filings.map((f, i) => {
                const open = expanded === i;
                return (
                  <>
                    <tr key={i} className="row-hover" style={{ borderTop: hairline, cursor: "pointer" }}
                      onClick={() => setExpanded(open ? null : i)} data-testid={`sec-${f.ticker}`}>
                      <td style={{ ...td, color: accent, fontWeight: 700 }}>${f.ticker}</td>
                      <td style={td}>
                        <span style={{
                          color: FORM_COLORS[f.form] || labelLight,
                          padding: "3px 8px", border: `0.5px solid ${(FORM_COLORS[f.form] || labelLight)}66`,
                          background: `${(FORM_COLORS[f.form] || labelLight)}08`,
                          letterSpacing: "0.12em", fontSize: 10, fontWeight: 700,
                        }}>{f.form}</span>
                        {f.activist && (
                          <span style={{ marginLeft: 6, color: "#f87171", fontSize: 9, fontWeight: 700,
                                          letterSpacing: "0.1em" }}>· {f.activist.toUpperCase()}</span>
                        )}
                      </td>
                      <td style={{ ...td, fontSize: 11 }}>{f.company}</td>
                      <td style={{ ...td, color: f.significance >= 80 ? "#4ade80" : f.significance >= 65 ? "#fbbf24" : labelLight, fontWeight: 700 }}>
                        {f.significance}
                      </td>
                      <td style={td}>
                        {f.narrative_lock_badge ? (
                          <span style={{ color: "#fbbf24", padding: "2px 6px",
                                          border: "0.5px solid #fbbf24aa", background: "#fbbf2415",
                                          fontSize: 10, fontWeight: 700, letterSpacing: "0.1em" }}>
                            🔒 LOCK {f.narrative_lock_score}
                          </span>
                        ) : <span style={{ color: dim, fontSize: 11 }}>{f.narrative_lock_score}</span>}
                      </td>
                      <td style={{ ...td, color: dim, textAlign: "right" }}>{open ? "▼" : "▶"}</td>
                    </tr>
                    {open && (
                      <tr style={{ background: "#03030680" }}>
                        <td colSpan={6} style={{ padding: "18px 24px" }}>
                          <Row k="SUMMARY" v={f.summary} />
                          <Row k="BIAS" v={f.bias} color={f.bias === "BULLISH" ? "#4ade80" : f.bias === "BEARISH" ? "#f87171" : labelLight} />
                          <Row k="TRADABILITY" v={`${f.tradability_pct}%`} />
                          <Row k="EXPECTED EFFECT" v={`${f.expected_effect_pct >= 0 ? "+" : ""}${f.expected_effect_pct}%`} />
                          <Row k="NARRATIVE LOCK" v={`${f.narrative_lock_score}/100`} />
                          <Row k="CONCURRENT SIGNALS" v={(f.concurrent_signals || []).join(", ") || "none"} />
                          <Row k="EDGAR LINK" v={
                            <a href={f.link} target="_blank" rel="noreferrer" style={{ color: accent }}>{f.link}</a>
                          } />
                        </td>
                      </tr>
                    )}
                  </>
                );
              })}
            </tbody>
          </table>
        )}
      </Card>
    </CrtShell>
  );
}

function Select({ label, value, onChange, opts }) {
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 4 }}>
      <span style={{ color: dim, fontSize: 9, letterSpacing: "0.18em", fontWeight: 700 }}>{label}</span>
      <select value={value} onChange={e => onChange(e.target.value)}
        style={{ background: cardBg, border: `0.5px solid ${dim}`, color: labelLight,
                  fontSize: 12, padding: "6px 10px", fontFamily: "JetBrains Mono" }}>
        {opts.map(([v, l]) => <option key={v} value={v}>{l}</option>)}
      </select>
    </div>
  );
}
function Row({ k, v, color }) {
  return (
    <div style={{ display: "flex", padding: "6px 0", borderBottom: hairline, fontSize: 11 }}>
      <span style={{ color: dim, letterSpacing: "0.14em", flex: "0 0 200px" }}>{k}</span>
      <span style={{ color: color || labelLight, flex: 1 }}>{v || "—"}</span>
    </div>
  );
}
