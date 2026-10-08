import { useEffect, useState } from "react";
import axios from "axios";
import { API } from "../config";
import { tokens } from "./CrtShell";

const { accent, muted, labelLight, hairline } = tokens;
const number = (value, digits = 0) => typeof value === "number" && Number.isFinite(value) ? value.toFixed(digits) : "--";
const percent = (value, multiplier = 1) => typeof value === "number" && Number.isFinite(value) ? `${(value * multiplier).toFixed(2)}%` : "--";
const cell = { padding: "8px", textAlign: "right", whiteSpace: "nowrap", borderBottom: hairline };

export default function StrategyEvidencePanel() {
  const [report, setReport] = useState(null);
  const [error, setError] = useState(false);
  const [horizon, setHorizon] = useState(5);

  useEffect(() => {
    let cancelled = false;
    const controller = new AbortController();
    axios.get(`${API}/research/strategy-evidence`, { signal: controller.signal, timeout: 12000 }).then(({ data }) => {
      if (!cancelled) {
        if (!data || typeof data.ok !== "boolean") setError(true);
        else setReport(data);
      }
    }).catch(() => { if (!cancelled) setError(true); });
    return () => { cancelled = true; controller.abort(); };
  }, []);

  const rows = Array.isArray(report?.scorecards) ? report.scorecards.filter(row => row && row.horizon_sessions === horizon) : [];
  const generated = report?.generated_at ? new Date(report.generated_at) : null;
  const timestamp = generated && Number.isFinite(generated.getTime()) ? generated.toISOString() : "--";

  return (
    <section aria-label="Strategy research evidence" style={{ minWidth: 0, maxWidth: "100%", marginBottom: 20, color: labelLight, fontSize: 12, overflowWrap: "anywhere" }}>
      <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", flexWrap: "wrap", gap: 12, padding: "10px 0", borderBottom: hairline }}>
        <h2 style={{ margin: 0, fontSize: 13, color: accent }}>STRATEGY EVIDENCE / RESEARCH ONLY</h2>
        <label style={{ display: "flex", alignItems: "center", gap: 8 }}>
          HORIZON
          <select aria-label="Evidence horizon" value={horizon} onChange={event => setHorizon(Number(event.target.value))}
            style={{ background: tokens.cardBg, color: labelLight, border: hairline, padding: "4px 8px", fontFamily: "inherit" }}>
            {[1, 5, 20].map(value => <option key={value} value={value}>{value} {value === 1 ? "SESSION" : "SESSIONS"}</option>)}
          </select>
        </label>
      </div>
      <p style={{ color: muted }}>SAVED REPORT: {timestamp} | OBSERVATIONS: {number(report?.observations)}</p>
      {error ? <p role="alert">Saved strategy evidence unavailable.</p> : !report ? <p role="status">Loading saved strategy evidence...</p> : (
        <>
          {!report.ok && <p role="alert">Saved report is incomplete or unavailable; evidence is not verified.</p>}
          {report.truncated && <p role="status">TRUNCATED: saved report covers a limited observation set.</p>}
          {rows.length === 0 ? <p>No saved scorecards for this horizon.</p> : (
            <div role="region" aria-label="Strategy evidence table" tabIndex={0} style={{ overflowX: "auto", maxWidth: "100%" }}>
              <table style={{ width: "100%", minWidth: 1050, borderCollapse: "collapse", fontSize: 11 }}>
                <thead><tr style={{ color: muted }}>
                  {["STRATEGY", "SESSIONS", "EPISODES", "RESOLVED", "WIN RATE", "GROSS RETURN", "EXCESS VS SPY", "NET RETURN", "IC DAYS", "DAILY RANK IC", "STATUSES"].map(title => (
                    <th key={title} scope="col" style={{ ...cell, textAlign: title === "STRATEGY" || title === "STATUSES" ? "left" : "right", fontWeight: 400 }}>{title}</th>
                  ))}
                </tr></thead>
                <tbody>{rows.map(row => {
                  const resolved = typeof row.resolved === "number" && row.resolved > 0;
                  return (
                    <tr key={JSON.stringify([row.strategy_id, row.horizon_sessions, row.scoring_version, row.mode])}>
                      <th scope="row" style={{ ...cell, textAlign: "left", color: accent, fontWeight: 400 }}>
                        {row.strategy_id ?? "--"}
                        <div style={{ marginTop: 3, fontSize: 10, color: muted }}>VERSION: {row.scoring_version ?? "--"} | MODE: {row.mode ?? "--"}</div>
                      </th>
                      <td style={cell}>{number(row.horizon_sessions)}</td>
                      <td style={cell}>{number(row.episodes)}</td>
                      <td style={cell}>{number(row.resolved)}</td>
                      <td style={cell}>{resolved ? percent(row.win_rate, 100) : "--"}</td>
                      <td style={cell}>{resolved ? percent(row.mean_gross_return_pct) : "--"}</td>
                      <td style={cell}>{resolved ? percent(row.mean_excess_return_pct) : "--"}</td>
                      <td style={cell}>{resolved ? percent(row.mean_net_return_pct) : "--"}</td>
                      <td style={cell}>{number(row.ic_days)}</td>
                      <td style={cell}>{row.ic_days > 0 ? number(row.mean_daily_rank_ic, 4) : "--"}</td>
                      <td style={{ ...cell, textAlign: "left" }}>{row.statuses && Object.keys(row.statuses).length ? Object.entries(row.statuses).map(([status, count]) => `${status}: ${number(count)}`).join(" | ") : "--"}</td>
                    </tr>
                  );
                })}</tbody>
              </table>
            </div>
          )}
          {Array.isArray(report.limitations) && report.limitations.length > 0 && <ul aria-label="Evidence limitations" style={{ paddingLeft: 18, color: muted }}>{report.limitations.map((text, index) => <li key={index}>{text}</li>)}</ul>}
        </>
      )}
      <p style={{ color: muted, lineHeight: 1.6 }}>Excess return uses the SPY prior-close benchmark, not a simultaneous benchmark or risk-adjusted alpha. Net returns are unavailable when costs are not measured. Research only; no execution authority or automatic weight promotion.</p>
    </section>
  );
}
