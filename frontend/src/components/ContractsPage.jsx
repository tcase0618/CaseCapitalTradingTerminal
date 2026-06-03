import { useEffect, useState, useCallback } from "react";
import axios from "axios";
import { useNavigate } from "react-router-dom";
import { CrtShell, Card, tokens } from "./CrtShell";

const API = `${process.env.REACT_APP_BACKEND_URL}/api`;
const { accent, accent2, dim, muted, labelLight, hairline, cardBg } = tokens;

function fmtMoney(amt) {
  if (amt == null) return "—";
  if (amt >= 1e9) return `$${(amt / 1e9).toFixed(2)}B`;
  if (amt >= 1e6) return `$${(amt / 1e6).toFixed(2)}M`;
  return `$${amt.toLocaleString()}`;
}

function tierColor(amt) {
  if (amt >= 1e9) return "#4ade80";
  if (amt >= 1e8) return "#fbbf24";
  return labelLight;
}

const th = { padding: "10px 8px", fontSize: 10, color: dim, letterSpacing: "0.14em", fontWeight: 400, textAlign: "left" };
const td = { padding: "10px 8px", color: labelLight, letterSpacing: "0.04em", fontSize: 12 };

export default function ContractsPage() {
  const [contracts, setContracts] = useState([]);
  const [loading, setLoading] = useState(true);
  const [days, setDays] = useState(90);
  const [minAmount, setMinAmount] = useState(1_000_000);
  const [agency, setAgency] = useState("");
  const [onlyWithSubs, setOnlyWithSubs] = useState(false);
  const [expanded, setExpanded] = useState(null);
  const navigate = useNavigate();

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const params = new URLSearchParams({ days, min_amount: minAmount });
      if (agency) params.append("agency", agency);
      const r = await axios.get(`${API}/contracts?${params.toString()}`);
      setContracts(r.data.contracts || []);
    } catch {
      setContracts([]);
    } finally {
      setLoading(false);
    }
  }, [days, minAmount, agency]);

  useEffect(() => { load(); }, [load]);

  const filtered = onlyWithSubs
    ? contracts.filter(c => (c.sub_awards || []).length > 0)
    : contracts;

  return (
    <CrtShell title="CONTRACTS · USASPENDING">
      <Card title="FILTERS">
        <div style={{ display: "flex", gap: 18, flexWrap: "wrap", alignItems: "flex-end" }}>
          <FilterInput label="DAYS" value={days} type="number"
            onChange={v => setDays(Number(v) || 90)} testid="contracts-days" />
          <FilterInput label="MIN AMOUNT ($)" value={minAmount} type="number"
            onChange={v => setMinAmount(Number(v) || 0)} testid="contracts-min" />
          <FilterInput label="AGENCY" value={agency} placeholder="Department of Defense"
            onChange={setAgency} testid="contracts-agency" />
          <label style={{
            display: "flex", alignItems: "center", gap: 8,
            fontSize: 11, color: labelLight, letterSpacing: "0.12em", cursor: "pointer",
          }}>
            <input type="checkbox" data-testid="contracts-only-with-subs"
              checked={onlyWithSubs} onChange={e => setOnlyWithSubs(e.target.checked)} />
            ONLY WITH SUBCONTRACTORS
          </label>
          <button data-testid="contracts-apply" onClick={load}
            style={{
              background: "transparent", border: `0.5px solid ${accent}`,
              color: accent, fontSize: 11, padding: "8px 18px", cursor: "pointer",
              letterSpacing: "0.14em", fontFamily: "JetBrains Mono", fontWeight: 700,
            }}>[ APPLY ]</button>
        </div>
      </Card>

      <Card title={`PRIME CONTRACTS · ${filtered.length} · LAST ${days}D`}>
        {loading ? (
          <div style={{ color: muted, padding: 20 }}>LOADING...</div>
        ) : !filtered.length ? (
          <div style={{ color: muted, padding: 20 }}>No contracts match these filters.</div>
        ) : (
          <table style={{ width: "100%", borderCollapse: "collapse" }}>
            <thead>
              <tr>
                <th style={th}>TICKER</th><th style={th}>BADGE</th>
                <th style={th}>AMOUNT</th><th style={th}>AGENCY</th>
                <th style={th}>DATE</th><th style={th}></th>
              </tr>
            </thead>
            <tbody>
              {filtered.map(c => {
                const id = c.generated_internal_id || c.award_id;
                const open = expanded === id;
                const subs = c.sub_awards || [];
                return (
                  <>
                    <tr key={id} data-testid={`contract-${c.ticker}`}
                      className="row-hover"
                      style={{ borderTop: hairline, cursor: "pointer" }}
                      onClick={() => setExpanded(open ? null : id)}>
                      <td style={{ ...td, color: accent, fontWeight: 700 }}>${c.ticker}</td>
                      <td style={td}>
                        <span style={{
                          color: "#fbbf24", padding: "3px 8px",
                          border: "0.5px solid #fbbf2466",
                          background: "#fbbf2408",
                          letterSpacing: "0.14em", fontSize: 10, fontWeight: 700,
                        }}>CONTRACT</span>
                        {subs.length > 0 && (
                          <span style={{
                            marginLeft: 6, color: "#fbbf24", fontSize: 9,
                            letterSpacing: "0.1em", fontWeight: 700,
                          }}>· {subs.length} SUBS</span>
                        )}
                      </td>
                      <td style={{ ...td, color: tierColor(c.amount), fontWeight: 700, fontSize: 14 }}>
                        {fmtMoney(c.amount)}
                      </td>
                      <td style={{ ...td, fontSize: 11 }}>{c.agency}</td>
                      <td style={td}>{c.period_start?.slice(0, 10) || "—"}</td>
                      <td style={{ ...td, color: dim, textAlign: "right" }}>{open ? "▼" : "▶"}</td>
                    </tr>
                    {open && (
                      <tr style={{ background: "#03030680" }}>
                        <td colSpan={6} style={{ padding: "18px 24px" }}>
                          <div style={{ marginBottom: 18 }}>
                            <div style={{ color: labelLight, fontSize: 11, letterSpacing: "0.14em", marginBottom: 8, fontWeight: 700 }}>
                              // CONTRACT DETAILS
                            </div>
                            <Row k="RECIPIENT" v={c.recipient} />
                            <Row k="AGENCY" v={c.agency} />
                            <Row k="AWARD ID" v={c.award_id} />
                            <Row k="DESCRIPTION" v={c.description || "—"} />
                            <Row k="PERIOD" v={`${c.period_start || "—"} → ${c.period_end || "—"}`} />
                          </div>
                          <div style={{ color: labelLight, fontSize: 11, letterSpacing: "0.14em", marginBottom: 10, fontWeight: 700 }}>
                            // SUBCONTRACTORS · {subs.length} ROWS
                          </div>
                          {!subs.length ? (
                            <div style={{ color: muted, fontSize: 11 }}>
                              No subcontractors reported by prime to USASpending.
                            </div>
                          ) : (
                            <table style={{ width: "100%", borderCollapse: "collapse" }}>
                              <thead>
                                <tr>
                                  <th style={th}>SUBCONTRACTOR</th><th style={th}>TICKER</th>
                                  <th style={th}>AMOUNT</th><th style={th}>% OF PRIME</th>
                                  <th style={th}>BADGE</th>
                                </tr>
                              </thead>
                              <tbody>
                                {subs
                                  .slice()
                                  .sort((a, b) => (b.amount || 0) - (a.amount || 0))
                                  .map((s, i) => {
                                    const pct = c.amount ? (s.amount / c.amount * 100) : 0;
                                    return (
                                      <tr key={i} style={{ borderTop: hairline }}>
                                        <td style={td}>{s.recipient}</td>
                                        <td style={{ ...td, color: s.ticker ? accent : muted, fontWeight: 700 }}>
                                          {s.ticker ? (
                                            <span style={{ cursor: "pointer", textDecoration: "underline" }}
                                              onClick={e => { e.stopPropagation(); navigate(`/ticker/${s.ticker}`); }}>
                                              ${s.ticker}
                                            </span>
                                          ) : "—"}
                                        </td>
                                        <td style={{ ...td, color: "#fff" }}>{fmtMoney(s.amount)}</td>
                                        <td style={{ ...td, color: accent2 }}>{pct.toFixed(1)}%</td>
                                        <td style={td}>
                                          {s.ticker && (
                                            <span style={{
                                              color: accent2, padding: "2px 6px",
                                              border: `0.5px solid ${accent2}66`,
                                              background: `${accent2}08`,
                                              letterSpacing: "0.12em", fontSize: 9, fontWeight: 700,
                                            }}>SUBCONTRACTOR_WIN</span>
                                          )}
                                        </td>
                                      </tr>
                                    );
                                  })}
                              </tbody>
                            </table>
                          )}
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

function FilterInput({ label, value, onChange, type = "text", placeholder, testid }) {
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 4 }}>
      <span style={{ color: dim, fontSize: 9, letterSpacing: "0.18em", fontWeight: 700 }}>{label}</span>
      <input data-testid={testid} type={type} value={value} placeholder={placeholder || ""}
        onChange={e => onChange(e.target.value)}
        style={{
          background: cardBg, border: `0.5px solid ${dim}`,
          color: labelLight, fontSize: 12, padding: "8px 12px",
          fontFamily: "JetBrains Mono", minWidth: 140,
        }} />
    </div>
  );
}

function Row({ k, v }) {
  return (
    <div style={{ display: "flex", justifyContent: "space-between", padding: "5px 0", borderBottom: hairline, fontSize: 11 }}>
      <span style={{ color: dim, letterSpacing: "0.14em", flexShrink: 0, marginRight: 12 }}>{k}</span>
      <span style={{ color: labelLight, textAlign: "right" }}>{v}</span>
    </div>
  );
}
