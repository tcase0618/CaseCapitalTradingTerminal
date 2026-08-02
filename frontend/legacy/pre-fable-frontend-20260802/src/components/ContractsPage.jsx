import { Fragment, useCallback, useEffect, useMemo, useState } from "react";
import axios from "axios";
import { API } from "../config";
import { Link, useNavigate } from "react-router-dom";
import { CrtShell, Card, Stat, tokens } from "./CrtShell";

const { accent, accent2, dim, muted, labelLight, hairline, cardBg } = tokens;

function fmtMoney(amt) {
  const n = Number(amt);
  if (!Number.isFinite(n)) return "-";
  if (Math.abs(n) >= 1e9) return `$${(n / 1e9).toFixed(2)}B`;
  if (Math.abs(n) >= 1e6) return `$${(n / 1e6).toFixed(2)}M`;
  return `$${n.toLocaleString(undefined, { maximumFractionDigits: 0 })}`;
}

function tierColor(amt) {
  if ((amt || 0) >= 1e9) return "#4ade80";
  if ((amt || 0) >= 1e8) return "#fbbf24";
  return labelLight;
}

export default function ContractsPage() {
  const [contracts, setContracts] = useState([]);
  const [loading, setLoading] = useState(true);
  const [days, setDays] = useState(90);
  const [minAmount, setMinAmount] = useState(1_000_000);
  const [agency, setAgency] = useState("");
  const [tickerFilter, setTickerFilter] = useState("ALL");
  const [textFilter, setTextFilter] = useState("");
  const [onlyWithSubs, setOnlyWithSubs] = useState(false);
  const [focusTicker, setFocusTicker] = useState(null);
  const [expanded, setExpanded] = useState(null);
  const [battleContractId, setBattleContractId] = useState(null);
  const [summaryContract, setSummaryContract] = useState(null);
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

  const filtered = useMemo(() => {
    const query = textFilter.trim().toLowerCase();
    return contracts.filter(c => {
      if (onlyWithSubs && !(c.sub_awards || []).length) return false;
      if (tickerFilter !== "ALL" && c.ticker !== tickerFilter) return false;
      if (query) {
        const haystack = `${c.ticker || ""} ${c.recipient || ""} ${c.agency || ""} ${c.description || ""} ${c.award_id || ""}`.toLowerCase();
        if (!haystack.includes(query)) return false;
      }
      return true;
    });
  }, [contracts, onlyWithSubs, tickerFilter, textFilter]);

  const summary = useMemo(() => {
    const total = filtered.reduce((sum, c) => sum + (Number(c.amount) || 0), 0);
    const sorted = [...filtered].sort((a, b) => (b.amount || 0) - (a.amount || 0));
    const agencies = {};
    const tickers = {};
    let subCount = 0;
    filtered.forEach(c => {
      agencies[c.agency || "Unknown"] = (agencies[c.agency || "Unknown"] || 0) + (Number(c.amount) || 0);
      tickers[c.ticker || "UNKNOWN"] = (tickers[c.ticker || "UNKNOWN"] || 0) + (Number(c.amount) || 0);
      subCount += (c.sub_awards || []).length;
    });
    return {
      total,
      top: sorted[0],
      mega: filtered.filter(c => (c.amount || 0) >= 1e9).length,
      subCount,
      agencies: Object.entries(agencies).sort((a, b) => b[1] - a[1]).slice(0, 5),
      tickers: Object.entries(tickers).sort((a, b) => b[1] - a[1]).slice(0, 10),
    };
  }, [filtered]);

  const drilldown = useMemo(() => {
    const tickerRows = [...contracts].sort((a, b) => (b.amount || 0) - (a.amount || 0));
    const tickerOptions = Array.from(new Set(tickerRows.map(c => c.ticker).filter(Boolean))).slice(0, 40);
    const agencyOptions = Array.from(new Set(contracts.map(c => c.agency).filter(Boolean))).sort().slice(0, 40);
    const targetTicker = focusTicker || summary.top?.ticker || tickerOptions[0];
    const history = contracts
      .filter(c => c.ticker === targetTicker)
      .sort((a, b) => String(b.period_start || "").localeCompare(String(a.period_start || "")));
    const historyTotal = history.reduce((sum, c) => sum + (Number(c.amount) || 0), 0);
    const agencyMix = {};
    history.forEach(c => {
      agencyMix[c.agency || "Unknown"] = (agencyMix[c.agency || "Unknown"] || 0) + (Number(c.amount) || 0);
    });
    return {
      tickerOptions,
      agencyOptions,
      targetTicker,
      history,
      historyTotal,
      agencyMix: Object.entries(agencyMix).sort((a, b) => b[1] - a[1]).slice(0, 4),
    };
  }, [contracts, focusTicker, summary.top]);

  return (
    <CrtShell title="CONTRACTS - USASPENDING">
      <div style={{ display: "flex", background: cardBg, border: hairline, marginBottom: 22, flexWrap: "wrap" }}>
        <Stat label="CONTRACT VALUE" value={fmtMoney(summary.total)} sub={`LAST ${days}D`} color={accent} accentBar />
        <Stat label="PRIME AWARDS" value={filtered.length} sub="PUBLIC TICKERS" color={accent2} />
        <Stat label="MEGA AWARDS" value={summary.mega} sub=">= $1B" color="#4ade80" />
        <Stat label="SUB AWARDS" value={summary.subCount} sub="DISCLOSED NETWORK" color="#fbbf24" />
      </div>

      <div style={commandGrid}>
        <Card title="PROCUREMENT COMMAND READ" accentColor={accent}>
          <div style={{ display: "grid", gridTemplateColumns: "minmax(0, 1fr) minmax(220px, 0.45fr)", gap: 18 }}>
            <div>
              <div style={eyebrow}>TOP PRIME EXPOSURE</div>
              {summary.top ? (
                <>
                  <Link to={`/ticker/${summary.top.ticker}`} style={tickerHero}>${summary.top.ticker}</Link>
                  <div style={{ color: tierColor(summary.top.amount), fontSize: 28, fontWeight: 900, marginTop: 8 }}>
                    {fmtMoney(summary.top.amount)}
                  </div>
                  <p style={heroCopy}>
                    {summary.top.recipient || "Unknown recipient"} won through {summary.top.agency || "unknown agency"}. {summary.top.description || "Open the row below for award details and subcontractor exposure."}
                  </p>
                </>
              ) : (
                <p style={heroCopy}>No awards match the active filters.</p>
              )}
            </div>
            <div style={miniPanel}>
              <SmallLine k="Filter" v={`${days}D / ${fmtMoney(minAmount)}+`} />
              <SmallLine k="Agency" v={agency || "ALL"} />
              <SmallLine k="Subs Mode" v={onlyWithSubs ? "ON" : "OFF"} />
              <SmallLine k="Largest Share" v={summary.total && summary.top ? `${((summary.top.amount / summary.total) * 100).toFixed(0)}%` : "-"} />
            </div>
          </div>
        </Card>

        <Card title="AGENCY HEAT" accentColor={accent2}>
          <HeatList rows={summary.agencies} total={summary.total} />
        </Card>
      </div>

      <Card title="TICKER EXPOSURE STRIP" accentColor="#4ade80">
        <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
          {summary.tickers.map(([ticker, amount]) => (
            <button key={ticker} type="button" onClick={() => {
              setTickerFilter(ticker);
              setFocusTicker(ticker);
            }} style={exposureChip(amount, tickerFilter === ticker)}>
              <span>${ticker}</span>
              <strong>{fmtMoney(amount)}</strong>
            </button>
          ))}
          {!summary.tickers.length && <div style={{ color: muted, padding: 10 }}>No ticker exposure in this filter.</div>}
        </div>
      </Card>

      <div style={intelGrid}>
        <Card title="CONTRACTOR DOSSIER" accentColor={accent}>
          <ContractorDossier
            ticker={drilldown.targetTicker}
            rows={drilldown.history}
            total={drilldown.historyTotal}
            agencyMix={drilldown.agencyMix}
            onTicker={setFocusTicker}
          />
        </Card>
        <Card title="AGENCY DRILLDOWN" accentColor="#fbbf24">
          <div style={agencyDrillGrid}>
            {drilldown.agencyOptions.slice(0, 8).map(name => {
              const total = contracts.filter(c => c.agency === name).reduce((sum, c) => sum + (Number(c.amount) || 0), 0);
              return (
                <button key={name} type="button" onClick={() => setAgency(name)} style={agencyDrillButton(agency === name)}>
                  <span>{truncate(name, 34)}</span>
                  <strong>{fmtMoney(total)}</strong>
                </button>
              );
            })}
            {!drilldown.agencyOptions.length && <div style={{ color: muted, fontSize: 12 }}>No agencies loaded.</div>}
          </div>
        </Card>
      </div>

      <Card title="FILTERS">
        <div style={{ display: "flex", gap: 18, flexWrap: "wrap", alignItems: "flex-end" }}>
          <FilterInput label="DAYS" value={days} type="number" onChange={v => setDays(Number(v) || 90)} testid="contracts-days" />
          <FilterInput label="MIN AMOUNT ($)" value={minAmount} type="number" onChange={v => setMinAmount(Number(v) || 0)} testid="contracts-min" />
          <FilterInput label="AGENCY" value={agency} placeholder="Department of Defense" onChange={setAgency} testid="contracts-agency" />
          <FilterSelect label="TICKER" value={tickerFilter} options={["ALL", ...drilldown.tickerOptions]} onChange={setTickerFilter} />
          <FilterInput label="SEARCH" value={textFilter} placeholder="recipient, award id, description" onChange={setTextFilter} testid="contracts-search" />
          <label style={{ display: "flex", alignItems: "center", gap: 8, fontSize: 11, color: labelLight, letterSpacing: "0.12em", cursor: "pointer" }}>
            <input type="checkbox" data-testid="contracts-only-with-subs" checked={onlyWithSubs} onChange={e => setOnlyWithSubs(e.target.checked)} />
            ONLY WITH SUBCONTRACTORS
          </label>
          <button type="button" onClick={() => {
            setAgency("");
            setTickerFilter("ALL");
            setTextFilter("");
            setOnlyWithSubs(false);
          }} style={buttonStyle(dim)}>[ CLEAR ]</button>
          <button data-testid="contracts-apply" onClick={load} style={buttonStyle(accent)}>[ APPLY ]</button>
        </div>
      </Card>

      <Card title={`PRIME CONTRACTS - ${filtered.length} - LAST ${days}D`}>
        {loading ? (
          <div style={{ color: muted, padding: 20 }}>LOADING...</div>
        ) : !filtered.length ? (
          <div style={{ color: muted, padding: 20 }}>No contracts match these filters.</div>
        ) : (
          <table style={{ width: "100%", borderCollapse: "collapse" }}>
            <thead>
              <tr>
                <th style={th}>TICKER</th><th style={th}>BADGE</th><th style={th}>AMOUNT</th>
                <th style={th}>AGENCY</th><th style={th}>DATE</th><th style={th}></th>
              </tr>
            </thead>
            <tbody>
              {filtered.map(c => {
                const id = c.generated_internal_id || c.award_id;
                const open = expanded === id;
                const battleOpen = battleContractId === id;
                const subs = c.sub_awards || [];
                const publicSubs = subs.filter(s => s.ticker);
                const subTotal = subs.reduce((sum, s) => sum + (Number(s.amount) || 0), 0);
                const hiddenSubs = Math.max(0, subs.length - publicSubs.length);
                return (
                  <Fragment key={id}>
                    <tr data-testid={`contract-${id}`} className="row-hover" style={{ borderTop: hairline, cursor: "pointer" }} onClick={() => {
                      setExpanded(open ? null : id);
                      if (open) {
                        setBattleContractId(null);
                      }
                    }}>
                      <td style={{ ...td, color: accent, fontWeight: 700 }}>${c.ticker}</td>
                      <td style={td}>
                        <span style={badge("#fbbf24")}>CONTRACT</span>
                        {subs.length > 0 && <span style={{ marginLeft: 6, color: "#fbbf24", fontSize: 9, letterSpacing: "0.1em", fontWeight: 700 }}>- {subs.length} SUBS</span>}
                      </td>
                      <td style={{ ...td, color: tierColor(c.amount), fontWeight: 700, fontSize: 14 }}>{fmtMoney(c.amount)}</td>
                      <td style={{ ...td, fontSize: 11 }}>{c.agency}</td>
                      <td style={td}>{c.period_start?.slice(0, 10) || "-"}</td>
                      <td style={{ ...td, color: dim, textAlign: "right" }}>{open ? "v" : ">"}</td>
                    </tr>
                    {open && (
                      <tr style={{ background: "#03030680" }}>
                        <td colSpan={6} style={{ padding: "18px 24px" }}>
                          <div style={{ marginBottom: 18 }}>
                            <div style={panelTitle}>// CONTRACT DETAILS</div>
                            <Row k="RECIPIENT" v={c.recipient} />
                            <Row k="AGENCY" v={c.agency} />
                            <Row k="AWARD ID" v={c.award_id} />
                            <Row k="PERIOD" v={`${c.period_start || "-"} -> ${c.period_end || "-"}`} />
                            <div style={detailReadGrid}>
                              <DetailMetric label="Prime Award" value={fmtMoney(c.amount)} />
                              <DetailMetric label="Sub Flow Visible" value={fmtMoney(subTotal)} />
                              <DetailMetric label="Public Subs" value={publicSubs.length} />
                              <DetailMetric label="Private/Hidden Subs" value={hiddenSubs} />
                            </div>
                          </div>
                          <div style={{ display: "flex", justifyContent: "flex-end", gap: 10, marginBottom: battleOpen ? 16 : 0, flexWrap: "wrap" }}>
                            <button
                              type="button"
                              onClick={e => {
                                e.stopPropagation();
                                setSummaryContract(c);
                              }}
                              style={buttonStyle(accent)}
                            >
                              [ SUMMARY ]
                            </button>
                            <button
                              type="button"
                              onClick={e => {
                                e.stopPropagation();
                                setBattleContractId(battleOpen ? null : id);
                              }}
                              style={buttonStyle("#fbbf24")}
                            >
                              [ {battleOpen ? "HIDE BATTLE CARD" : "BATTLE CARD"} ]
                            </button>
                          </div>
                          {battleOpen && (
                            <div style={{ marginBottom: 20 }}>
                              <MoneyFlowBattleCard contract={c} />
                            </div>
                          )}
                          <div style={panelTitle}>// SUBCONTRACTORS - {subs.length} ROWS</div>
                          {!subs.length ? (
                            <div style={{ color: muted, fontSize: 11 }}>No subcontractors reported by prime to USASpending.</div>
                          ) : (
                            <table style={{ width: "100%", borderCollapse: "collapse" }}>
                              <thead>
                                <tr>
                                  <th style={th}>SUBCONTRACTOR</th><th style={th}>TICKER</th>
                                  <th style={th}>AMOUNT</th><th style={th}>% OF PRIME</th><th style={th}>BADGE</th>
                                </tr>
                              </thead>
                              <tbody>
                                {subs.slice().sort((a, b) => (b.amount || 0) - (a.amount || 0)).map((s, i) => {
                                  const pct = c.amount ? (s.amount / c.amount * 100) : 0;
                                  return (
                                    <tr key={`${s.recipient || "x"}-${s.amount || 0}-${i}`} style={{ borderTop: hairline }}>
                                      <td style={td}>{s.recipient}</td>
                                      <td style={{ ...td, color: s.ticker ? accent : muted, fontWeight: 700 }}>
                                        {s.ticker ? (
                                          <span style={{ cursor: "pointer", textDecoration: "underline" }} onClick={e => { e.stopPropagation(); navigate(`/ticker/${s.ticker}`); }}>
                                            ${s.ticker}
                                          </span>
                                        ) : "-"}
                                      </td>
                                      <td style={{ ...td, color: "#fff" }}>{fmtMoney(s.amount)}</td>
                                      <td style={{ ...td, color: accent2 }}>{pct.toFixed(1)}%</td>
                                      <td style={td}>{s.ticker && <span style={badge(accent2)}>SUBCONTRACTOR_WIN</span>}</td>
                                    </tr>
                                  );
                                })}
                              </tbody>
                            </table>
                          )}
                        </td>
                      </tr>
                    )}
                  </Fragment>
                );
              })}
            </tbody>
                          </table>
        )}
      </Card>
      {summaryContract && (
        <ContractSummaryModal contract={summaryContract} onClose={() => setSummaryContract(null)} />
      )}
    </CrtShell>
  );
}

function HeatList({ rows, total }) {
  if (!rows.length) return <div style={{ color: muted, padding: 10 }}>No agency mix yet.</div>;
  return (
    <div style={{ display: "grid", gap: 9 }}>
      {rows.map(([name, amount]) => {
        const pct = total ? (amount / total) * 100 : 0;
        return (
          <div key={name}>
            <div style={{ display: "flex", justifyContent: "space-between", gap: 12, fontSize: 11 }}>
              <span style={{ color: labelLight }}>{name}</span>
              <span style={{ color: accent, fontWeight: 800 }}>{fmtMoney(amount)}</span>
            </div>
            <div style={barTrack}><div style={{ ...barFill, width: `${Math.max(4, pct)}%` }} /></div>
          </div>
        );
      })}
    </div>
  );
}

function ContractorDossier({ ticker, rows, total, agencyMix, onTicker }) {
  if (!ticker) return <div style={{ color: muted, padding: 10 }}>No contractor focus loaded.</div>;
  const latest = rows[0];
  const subCount = rows.reduce((sum, c) => sum + ((c.sub_awards || []).length), 0);
  const biggest = rows.slice().sort((a, b) => (b.amount || 0) - (a.amount || 0))[0];
  return (
    <div>
      <div style={dossierHeader}>
        <div>
          <Link to={`/ticker/${ticker}`} style={dossierTicker}>${ticker}</Link>
          <div style={{ color: muted, fontSize: 11, letterSpacing: "0.08em", marginTop: 5 }}>
            {rows.length} awards in loaded window / {fmtMoney(total)}
          </div>
        </div>
        <button type="button" onClick={() => onTicker(ticker)} style={buttonStyle(accent)}>[ FOCUS ]</button>
      </div>
      <div style={dossierMetrics}>
        <DetailMetric label="Latest Award" value={latest?.period_start?.slice(0, 10) || "-"} />
        <DetailMetric label="Largest Award" value={fmtMoney(biggest?.amount)} />
        <DetailMetric label="Subaward Rows" value={subCount} />
      </div>
      <div style={dossierColumns}>
        <div>
          <div style={panelTitle}>// AGENCY MIX</div>
          <HeatList rows={agencyMix} total={total} />
        </div>
        <div>
          <div style={panelTitle}>// RECENT WINS</div>
          <div style={historyList}>
            {rows.slice(0, 4).map((row, i) => (
              <div key={`${row.award_id || row.generated_internal_id || "award"}-${i}`} style={historyRow}>
                <span>{row.period_start?.slice(0, 10) || "-"}</span>
                <strong>{fmtMoney(row.amount)}</strong>
                <em>{truncate(row.agency || "-", 24)}</em>
              </div>
            ))}
            {!rows.length && <div style={{ color: muted, fontSize: 12 }}>No loaded awards for this contractor.</div>}
          </div>
        </div>
      </div>
    </div>
  );
}

function MoneyFlowBattleCard({ contract }) {
  const subs = (contract.sub_awards || []).slice().sort((a, b) => (b.amount || 0) - (a.amount || 0));
  const publicSubs = subs.filter(s => s.ticker).slice(0, 6);
  const subTotal = subs.reduce((sum, s) => sum + (Number(s.amount) || 0), 0);
  return (
    <div style={battleGrid}>
      <SankeyFlowChart contract={contract} publicSubs={publicSubs} subTotal={subTotal} />
    </div>
  );
}

function DetailMetric({ label, value }) {
  return (
    <div style={detailMetric}>
      <span>{label}</span>
      <strong style={{ color: labelLight, fontSize: 15, letterSpacing: "0.06em" }}>{value}</strong>
    </div>
  );
}

function ContractSummaryModal({ contract, onClose }) {
  const subs = contract.sub_awards || [];
  const publicSubs = subs.filter(s => s.ticker);
  const subTotal = subs.reduce((sum, s) => sum + (Number(s.amount) || 0), 0);
  return (
    <div style={modalBackdrop} onClick={onClose}>
      <div style={modalPanel} onClick={e => e.stopPropagation()}>
        <div style={modalHeader}>
          <div>
            <div style={eyebrow}>CONTRACT SUMMARY</div>
            <div style={{ color: accent, fontSize: 28, fontWeight: 900, letterSpacing: "0.08em" }}>${contract.ticker}</div>
          </div>
          <button type="button" onClick={onClose} style={modalClose}>X</button>
        </div>
        <div style={summaryInfoGrid}>
          <SmallLine k="Recipient" v={contract.recipient || "-"} />
          <SmallLine k="Agency" v={contract.agency || "-"} />
          <SmallLine k="Prime Award" v={fmtMoney(contract.amount)} />
          <SmallLine k="Sub Flow Visible" v={fmtMoney(subTotal)} />
          <SmallLine k="Public Subs" v={publicSubs.length} />
          <SmallLine k="Total Subs" v={subs.length} />
          <SmallLine k="Award ID" v={contract.award_id || "-"} />
          <SmallLine k="Period" v={`${contract.period_start || "-"} -> ${contract.period_end || "-"}`} />
        </div>
        <div style={summaryDescription}>
          {contract.description || "No award description was supplied by the source."}
        </div>
        <div style={panelTitle}>// TOP SUBCONTRACTOR FLOW</div>
        <div style={modalSubs}>
          {subs.slice().sort((a, b) => (b.amount || 0) - (a.amount || 0)).slice(0, 8).map((s, i) => (
            <div key={`${s.recipient || "sub"}-${s.amount || 0}-${i}`} style={modalSubRow}>
              <span style={{ color: s.ticker ? accent : labelLight, fontWeight: 800 }}>{s.ticker ? `$${s.ticker}` : "PRIVATE"}</span>
              <span>{truncate(s.recipient || "Unknown subcontractor", 46)}</span>
              <strong>{fmtMoney(s.amount)}</strong>
            </div>
          ))}
          {!subs.length && <div style={{ color: muted, fontSize: 12 }}>No subcontractors reported by prime to USASpending.</div>}
        </div>
      </div>
    </div>
  );
}

function SankeyFlowChart({ contract, publicSubs, subTotal }) {
  const total = Number(contract.amount) || subTotal || 1;
  const untraced = Math.max(0, total - subTotal);
  const shownSubs = publicSubs.slice(0, 5);
  const shownSubTotal = shownSubs.reduce((sum, s) => sum + (Number(s.amount) || 0), 0);
  const otherPublic = Math.max(0, subTotal - shownSubTotal);
  const buckets = [
    ...shownSubs.map((s, i) => ({
      key: `${s.ticker}-${s.amount}-${i}`,
      label: `$${s.ticker}`,
      detail: s.recipient || "Public subcontractor",
      amount: Number(s.amount) || 0,
      color: ["#69d8c1", "#a7f3d0", "#93c5fd", "#c4b5fd", "#f9a8d4"][i % 5],
    })),
    ...(otherPublic > 0 ? [{
      key: "other-public-subs",
      label: "OTHER SUBS",
      detail: "Additional disclosed flow",
      amount: otherPublic,
      color: "#fbbf24",
    }] : []),
    ...(untraced > 0 ? [{
      key: "prime-untraced",
      label: "PRIME / UNTRACED",
      detail: "Not mapped to public subs",
      amount: untraced,
      color: "#9ca3af",
    }] : []),
  ];
  const maxBucket = Math.max(...buckets.map(b => b.amount), 1);
  const rows = buckets.map((b, i) => ({
    ...b,
    y: 92 + i * Math.min(52, 292 / Math.max(buckets.length, 1)),
    width: Math.max(8, Math.min(42, 8 + Math.sqrt(b.amount / maxBucket) * 34)),
  }));

  return (
    <div style={sankeyWrap}>
      <svg viewBox="0 0 980 420" preserveAspectRatio="xMidYMid meet" style={sankeySvg} role="img" aria-label="Contract money flow diagram">
        <defs>
          <filter id="sankeyGlow" x="-20%" y="-20%" width="140%" height="140%">
            <feGaussianBlur stdDeviation="2.4" result="blur" />
            <feMerge>
              <feMergeNode in="blur" />
              <feMergeNode in="SourceGraphic" />
            </feMerge>
          </filter>
        </defs>
        <rect x="18" y="18" width="944" height="384" fill="rgba(255,255,255,0.015)" stroke="rgba(255,255,255,0.12)" />
        <text x="38" y="42" fill={dim} fontSize="10" fontWeight="800" letterSpacing="2">MONEY FLOW MAP</text>
        <text x="38" y="66" fill={accent2} fontSize="22" fontWeight="900">{fmtMoney(total)} PRIME AWARD</text>

        <path d="M 150 210 C 270 210, 282 210, 402 210" stroke="rgba(251,191,36,0.34)" strokeWidth="74" fill="none" filter="url(#sankeyGlow)" />
        <path d="M 150 210 C 270 210, 282 210, 402 210" stroke="rgba(255,255,255,0.18)" strokeWidth="1" fill="none" />
        {rows.map(row => (
          <path
            key={`flow-${row.key}`}
            d={`M 512 210 C 620 210, 640 ${row.y}, 748 ${row.y}`}
            stroke={hexToRgba(row.color, row.key === "prime-untraced" ? 0.24 : 0.42)}
            strokeWidth={row.width}
            fill="none"
          />
        ))}

        <rect x="68" y="146" width="82" height="128" fill="rgba(156,163,175,0.88)" />
        <rect x="402" y="112" width="110" height="196" fill="rgba(156,163,175,0.88)" />
        <text x="62" y="302" fill={labelLight} fontSize="14" fontWeight="800">AGENCY</text>
        <text x="62" y="322" fill={muted} fontSize="12">{truncate(contract.agency || "Unknown agency", 28)}</text>
        <text x="390" y="332" fill={labelLight} fontSize="14" fontWeight="800">PRIME</text>
        <text x="390" y="354" fill={accent} fontSize="16" fontWeight="900">${contract.ticker}</text>
        <text x="390" y="374" fill={muted} fontSize="12">{truncate(contract.recipient || "Prime recipient", 38)}</text>

        {rows.map(row => (
          <g key={`node-${row.key}`}>
            <rect x="748" y={row.y - 18} width="28" height="36" fill={row.color} />
            <text x="790" y={row.y - 5} fill={labelLight} fontSize="14" fontWeight="900">{row.label}</text>
            <text x="790" y={row.y + 13} fill={muted} fontSize="11">{fmtMoney(row.amount)} - {truncate(row.detail, 30)}</text>
          </g>
        ))}
      </svg>
    </div>
  );
}

function FilterInput({ label, value, onChange, type = "text", placeholder, testid }) {
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 4 }}>
      <span style={{ color: dim, fontSize: 9, letterSpacing: "0.18em", fontWeight: 700 }}>{label}</span>
      <input data-testid={testid} type={type} value={value} placeholder={placeholder || ""} onChange={e => onChange(e.target.value)}
        style={{ background: cardBg, border: `0.5px solid ${dim}`, color: labelLight, fontSize: 12, padding: "8px 12px", fontFamily: "JetBrains Mono", minWidth: 140 }} />
    </div>
  );
}

function FilterSelect({ label, value, options, onChange }) {
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 4 }}>
      <span style={{ color: dim, fontSize: 9, letterSpacing: "0.18em", fontWeight: 700 }}>{label}</span>
      <select value={value} onChange={e => onChange(e.target.value)} style={selectStyle}>
        {options.map(option => <option key={option} value={option}>{option}</option>)}
      </select>
    </div>
  );
}

function SmallLine({ k, v }) {
  return (
    <div style={{ display: "flex", justifyContent: "space-between", gap: 12, borderBottom: hairline, padding: "7px 0", fontSize: 11 }}>
      <span style={{ color: dim, letterSpacing: "0.14em" }}>{k}</span>
      <span style={{ color: labelLight, textAlign: "right" }}>{v}</span>
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

function truncate(value, length) {
  const text = String(value || "");
  return text.length > length ? `${text.slice(0, length - 1)}...` : text;
}

function hexToRgba(hex, alpha) {
  const clean = hex.replace("#", "");
  const n = parseInt(clean.length === 3 ? clean.split("").map(ch => ch + ch).join("") : clean, 16);
  const r = (n >> 16) & 255;
  const g = (n >> 8) & 255;
  const b = n & 255;
  return `rgba(${r},${g},${b},${alpha})`;
}

const th = { padding: "10px 8px", fontSize: 10, color: dim, letterSpacing: "0.14em", fontWeight: 400, textAlign: "left" };
const td = { padding: "10px 8px", color: labelLight, letterSpacing: "0.04em", fontSize: 12 };
const commandGrid = { display: "grid", gridTemplateColumns: "minmax(0, 1.25fr) minmax(300px, 0.75fr)", gap: 18 };
const intelGrid = { display: "grid", gridTemplateColumns: "minmax(0, 1.2fr) minmax(300px, 0.8fr)", gap: 18 };
const dossierHeader = { display: "flex", justifyContent: "space-between", gap: 14, alignItems: "flex-start", borderBottom: hairline, paddingBottom: 12, marginBottom: 12 };
const dossierTicker = { color: accent, fontSize: 28, fontWeight: 900, letterSpacing: "0.08em", textDecoration: "none" };
const dossierMetrics = { display: "grid", gridTemplateColumns: "repeat(3, minmax(0, 1fr))", gap: 8, marginBottom: 14 };
const dossierColumns = { display: "grid", gridTemplateColumns: "minmax(0, 0.8fr) minmax(0, 1fr)", gap: 16 };
const historyList = { display: "grid", gap: 7 };
const historyRow = { display: "grid", gridTemplateColumns: "86px 90px minmax(0, 1fr)", gap: 10, alignItems: "center", color: labelLight, borderBottom: hairline, padding: "7px 0", fontSize: 11, fontStyle: "normal" };
const agencyDrillGrid = { display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(190px, 1fr))", gap: 8 };
const selectStyle = { background: cardBg, border: `0.5px solid ${dim}`, color: labelLight, fontSize: 12, padding: "8px 12px", fontFamily: "JetBrains Mono", minWidth: 120 };
const battleGrid = { display: "block" };
const sankeyWrap = { border: hairline, background: "linear-gradient(180deg, rgba(255,255,255,0.035), rgba(255,255,255,0.012))", padding: 10, minHeight: 430, overflow: "hidden" };
const sankeySvg = { width: "100%", height: "100%", minHeight: 430, display: "block" };
const detailReadGrid = { display: "grid", gridTemplateColumns: "repeat(4, minmax(130px, 1fr))", gap: 10, marginTop: 14 };
const detailMetric = { border: hairline, background: "rgba(251,191,36,0.035)", padding: "10px 12px", display: "grid", gap: 6, color: dim, fontSize: 10, letterSpacing: "0.13em" };
const modalBackdrop = { position: "fixed", inset: 0, background: "rgba(0,0,0,0.88)", zIndex: 10000, display: "flex", alignItems: "center", justifyContent: "center", padding: 24 };
const modalPanel = { width: "min(860px, calc(100vw - 48px))", maxHeight: "calc(100vh - 72px)", overflow: "auto", border: `1px solid ${accent}55`, background: "#05060b", boxShadow: `0 0 60px ${accent}28`, padding: 22, position: "relative" };
const modalHeader = { display: "flex", justifyContent: "space-between", gap: 18, alignItems: "flex-start", borderBottom: hairline, paddingBottom: 14, marginBottom: 16 };
const modalClose = { background: "transparent", border: `0.5px solid ${dim}`, color: labelLight, cursor: "pointer", fontFamily: "JetBrains Mono", fontWeight: 900, padding: "6px 10px" };
const summaryInfoGrid = { display: "grid", gridTemplateColumns: "repeat(2, minmax(0, 1fr))", gap: "0 22px", marginBottom: 16 };
const summaryDescription = { color: labelLight, lineHeight: 1.55, border: hairline, background: "rgba(255,255,255,0.025)", padding: 14, marginBottom: 18, fontSize: 13 };
const modalSubs = { display: "grid", gap: 8 };
const modalSubRow = { display: "grid", gridTemplateColumns: "90px minmax(0, 1fr) 110px", gap: 12, borderBottom: hairline, padding: "8px 0", color: labelLight, fontSize: 12, alignItems: "center" };
const eyebrow = { color: dim, fontSize: 9, letterSpacing: "0.18em", fontWeight: 800, marginBottom: 8 };
const tickerHero = { color: accent, fontSize: 42, fontWeight: 900, letterSpacing: "0.08em", textDecoration: "none" };
const heroCopy = { color: labelLight, lineHeight: 1.55, margin: "12px 0 0", maxWidth: 760 };
const miniPanel = { border: hairline, background: "rgba(255,255,255,0.018)", padding: "10px 14px", alignSelf: "start" };
const panelTitle = { color: labelLight, fontSize: 11, letterSpacing: "0.14em", marginBottom: 10, fontWeight: 700 };
const barTrack = { height: 5, background: "rgba(255,255,255,0.06)", marginTop: 7, overflow: "hidden" };
const barFill = { height: "100%", background: accent2, boxShadow: `0 0 10px ${accent2}66` };
function badge(color) {
  return { color, padding: "3px 8px", border: `0.5px solid ${color}66`, background: `${color}08`, letterSpacing: "0.12em", fontSize: 10, fontWeight: 700 };
}
function buttonStyle(color) {
  return { background: "transparent", border: `0.5px solid ${color}`, color, fontSize: 11, padding: "8px 18px", cursor: "pointer", letterSpacing: "0.14em", fontFamily: "JetBrains Mono", fontWeight: 700 };
}
function agencyDrillButton(active) {
  return {
    display: "flex",
    justifyContent: "space-between",
    gap: 10,
    alignItems: "center",
    background: active ? "rgba(251,191,36,0.12)" : "rgba(255,255,255,0.018)",
    border: `0.5px solid ${active ? "#fbbf24" : dim}`,
    color: active ? "#fbbf24" : labelLight,
    padding: "9px 10px",
    cursor: "pointer",
    letterSpacing: "0.07em",
    fontSize: 10,
    fontFamily: "JetBrains Mono",
    textAlign: "left",
  };
}
function exposureChip(amount, active = false) {
  const color = tierColor(amount);
  return { display: "flex", gap: 8, alignItems: "center", color, border: `0.5px solid ${active ? color : `${color}55`}`, background: active ? `${color}1f` : `${color}0d`, padding: "8px 10px", textDecoration: "none", letterSpacing: "0.08em", fontSize: 11, cursor: "pointer", fontFamily: "JetBrains Mono" };
}
