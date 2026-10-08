import { Fragment, useEffect, useMemo, useRef, useState } from "react";
import useDisplayResource from "../hooks/useDisplayResource";
import axios from "axios";
import { API } from "../config";
import { Link } from "react-router-dom";
import { CrtShell, Card, Stat, tokens } from "./CrtShell";

const { accent, accent2, dim, muted, labelLight, hairline, cardBg } = tokens;

const FORM_COLORS = {
  "SC 13D": "#f87171",
  "SC 13G": "#fbbf24",
  "8-K": accent2,
  "Form 4": "#4ade80",
  "13F-HR": labelLight,
};

export default function SECPage() {
  const [form, setForm] = useState("");
  const [days, setDays] = useState(7);
  const [expanded, setExpanded] = useState(null);
  const [polling, setPolling] = useState(false);
  const [battleCards, setBattleCards] = useState({});
  const [battleLoading, setBattleLoading] = useState(null);
  const [pollError, setPollError] = useState("");
  const battleRequest = useRef(null);
  const p = new URLSearchParams({ days });
  if (form) p.append("form", form);
  const { data, error, loading, refresh: load } = useDisplayResource(`${API}/sec/filings?${p}`, 60000);
  const filings = useMemo(() => objectRows(data?.filings), [data]);
  const incomplete = data != null && (!Array.isArray(data.filings) || filings.length !== data.filings.length);
  useEffect(() => { setExpanded(null); }, [form, days, data]);
  useEffect(() => () => battleRequest.current?.abort(), []);

  const triggerPoll = async () => {
    setPolling(true);
    setPollError("");
    try {
      await axios.post(`${API}/sec/poll`);
      load();
    } catch {
      setPollError("EDGAR poll failed. Filing display has not been refreshed.");
    } finally {
      setPolling(false);
    }
  };

  const loadBattleCard = async (ticker) => {
    const t = String(ticker || "").toUpperCase();
    if (!t) return;
    if (battleCards[t] && !battleCards[t].error) return;
    battleRequest.current?.abort();
    const controller = new AbortController();
    battleRequest.current = controller;
    setBattleLoading(t);
    try {
      const r = await axios.get(`${API}/sec/battle_card/${encodeURIComponent(t)}`, { signal: controller.signal });
      if (!controller.signal.aborted) setBattleCards(prev => ({ ...prev, [t]: r.data && typeof r.data === "object" && !Array.isArray(r.data) ? r.data : { error: "Battle card unavailable" } }));
    } catch {
      if (!controller.signal.aborted) setBattleCards(prev => ({ ...prev, [t]: { ticker: t, error: "Battle card unavailable" } }));
    } finally {
      if (!controller.signal.aborted) setBattleLoading(null);
    }
  };

  const summary = useMemo(() => {
    const now = Date.now();
    const enriched = filings.map(f => ({ ...f, ageHours: filingAgeHours(f, now) }));
    const byForm = filings.reduce((acc, f) => {
      acc[f.form || "Other"] = (acc[f.form || "Other"] || 0) + 1;
      return acc;
    }, Object.create(null));
    const top = [...filings].sort((a, b) =>
      (b.narrative_lock_score || 0) - (a.narrative_lock_score || 0) ||
      (b.significance || 0) - (a.significance || 0)
    )[0];
    return {
      locked: filings.filter(f => f.narrative_lock_badge).length,
      activist: filings.filter(f => f.form === "SC 13D" && f.activist).length,
      material8k: filings.filter(f => f.form === "8-K").length,
      form4: filings.filter(f => f.form === "Form 4").length,
      bullish: filings.filter(f => f.bias === "BULLISH").length,
      bearish: filings.filter(f => f.bias === "BEARISH").length,
      byForm: Object.entries(byForm).sort((a, b) => b[1] - a[1]).slice(0, 7),
      fresh24: enriched.filter(f => f.ageHours !== null && f.ageHours <= 24).length,
      fresh72: enriched.filter(f => f.ageHours !== null && f.ageHours <= 72).length,
      unknownAge: enriched.filter(f => f.ageHours === null).length,
      freshest: [...enriched].filter(f => f.ageHours !== null).sort((a, b) => a.ageHours - b.ageHours)[0],
      urgent: [...enriched].sort((a, b) =>
        (a.ageHours ?? 99999) - (b.ageHours ?? 99999) ||
        (b.significance || 0) - (a.significance || 0)
      ).slice(0, 8),
      top,
    };
  }, [filings]);

  return (
    <CrtShell
      title="SEC FILINGS - EDGAR LIVE"
      headerRight={
        <button data-testid="sec-poll-btn" onClick={triggerPoll} disabled={polling} style={buttonStyle(accent)}>
          [ {polling ? "POLLING..." : "POLL EDGAR"} ]
        </button>
      }
    >
      {(loading || error || incomplete || pollError) && <div role={error || incomplete || pollError ? "alert" : "status"} style={{ color: "#fbbf24", marginBottom: 12 }}>{pollError || (error ? "SEC filings unavailable. Retained filings may be stale." : incomplete ? "SEC response is incomplete; only valid filings are shown." : "Loading SEC filings...")}</div>}
      <div style={{ display: "flex", background: cardBg, border: hairline, marginBottom: 22, flexWrap: "wrap" }}>
        <Stat label={`FILINGS - ${days}D`} value={Array.isArray(data?.filings) ? filings.length : "-"} sub="PUBLIC TICKERS" color={accent} accentBar />
        <Stat label="NARRATIVE LOCK" value={Array.isArray(data?.filings) ? summary.locked : "-"} sub="HIGH ALIGNMENT" color="#fbbf24" />
        <Stat label="ACTIVIST 13D" value={Array.isArray(data?.filings) ? summary.activist : "-"} sub="CONTROL WATCH" color="#f87171" />
        <Stat label="8-K MATERIAL" value={Array.isArray(data?.filings) ? summary.material8k : "-"} sub="EVENT RISK" color={accent2} />
        <Stat label="FORM 4" value={Array.isArray(data?.filings) ? summary.form4 : "-"} sub="INSIDER TAPE" color="#4ade80" />
        <Stat label="FRESHEST EDGAR" value={formatAge(summary.freshest?.ageHours)} sub="ACCEPTED AGE" color={freshnessColor(summary.freshest?.ageHours)} />
        <Stat label="SEC SOURCE" value={error ? "STALE" : loading ? "PENDING" : incomplete ? "PARTIAL" : "STORED"} sub="EDGAR ATOM" color={error || incomplete ? "#fbbf24" : labelLight} />
      </div>

      <div style={commandGrid}>
        <Card title="EDGAR COMMAND READ" accentColor={accent}>
          {summary.top ? (
            <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(min(100%, 220px), 1fr))", gap: 18 }}>
              <div>
                <div style={eyebrow}>MOST ACTIONABLE FILING</div>
                <Link to={`/ticker/${summary.top.ticker}`} style={tickerHero}>${summary.top.ticker}</Link>
                <div style={{ display: "flex", gap: 8, flexWrap: "wrap", marginTop: 10 }}>
                  <span style={badge(FORM_COLORS[summary.top.form] || labelLight)}>{summary.top.form || "FILING"}</span>
                  <span style={badge(summary.top.bias === "BULLISH" ? "#4ade80" : summary.top.bias === "BEARISH" ? "#f87171" : labelLight)}>{summary.top.bias || "UNKNOWN"}</span>
                  {summary.top.narrative_lock_badge && <span style={badge("#fbbf24")}>NARRATIVE LOCK</span>}
                </div>
                <p style={heroCopy}>{summary.top.summary || summary.top.title || "Open the filing row for full EDGAR context."}</p>
              </div>
              <div style={miniPanel}>
                <SmallLine k="Company" v={summary.top.company || "-"} />
                <SmallLine k="Significance" v={metric(summary.top.significance, "/100")} />
                <SmallLine k="Tradability" v={metric(summary.top.tradability_pct, "%")} />
                <SmallLine k="Lock Score" v={metric(summary.top.narrative_lock_score, "/100")} />
              </div>
            </div>
          ) : (
            <div style={{ color: muted, padding: 20 }}>No EDGAR filings loaded. Poll EDGAR or widen the day window.</div>
          )}
        </Card>

        <Card title="FORM MIX" accentColor={accent2}>
          <HeatList rows={summary.byForm} total={filings.length} />
        </Card>
      </div>

      <Card title="EDGAR FRESHNESS RADAR" accentColor="#4ade80">
        <div style={freshnessGrid}>
          <div style={freshnessPanel}>
            <div style={eyebrow}>TIME-SENSITIVE RELIABILITY</div>
            <div style={freshMetricGrid}>
              <FreshMetric label="FRESHEST ACCEPTED" value={formatAge(summary.freshest?.ageHours)} color={freshnessColor(summary.freshest?.ageHours)} />
              <FreshMetric label="<= 24 HOURS" value={summary.fresh24} color="#4ade80" />
              <FreshMetric label="<= 72 HOURS" value={summary.fresh72} color="#fbbf24" />
              <FreshMetric label="UNKNOWN AGE" value={summary.unknownAge} color={summary.unknownAge ? "#f87171" : labelLight} />
            </div>
            <div style={sourceLine}>
              <span>PRIMARY SOURCE</span>
              <b>SEC EDGAR ATOM</b>
              <span>ACCEPTED TIMESTAMPS DRIVE FRESHNESS WHEN AVAILABLE</span>
            </div>
          </div>
          <div style={urgentPanel}>
            <div style={eyebrow}>URGENT QUEUE</div>
            {summary.urgent.length ? summary.urgent.map((f, i) => (
              <Link to={`/ticker/${f.ticker}`} key={`${f.ticker}-${f.form}-${f.accepted_at || i}`} style={urgentRow}>
                <span style={{ color: accent, fontWeight: 900 }}>${f.ticker}</span>
                <span style={badge(FORM_COLORS[f.form] || labelLight)}>{f.form || "FILING"}</span>
                <span style={{ color: freshnessColor(f.ageHours), fontWeight: 800 }}>{formatAge(f.ageHours)}</span>
                <span style={{ color: labelLight }}>{f.company || f.title || "-"}</span>
                <span style={{ color: f.significance >= 80 ? "#4ade80" : f.significance >= 65 ? "#fbbf24" : dim, textAlign: "right" }}>{metric(f.significance)}</span>
              </Link>
            )) : <div style={{ color: muted, padding: 10 }}>No urgent filings loaded.</div>}
          </div>
        </div>
      </Card>

      <Card title="FILTERS">
        <div style={{ display: "flex", gap: 16, padding: "8px 0", flexWrap: "wrap" }}>
          <Select label="FORM" value={form} onChange={setForm}
            opts={[["", "ALL"], ["SC 13D", "13D"], ["SC 13G", "13G"], ["8-K", "8-K"], ["Form 4", "FORM 4"], ["13F-HR", "13F"]]} />
          <Select label="DAYS" value={days} onChange={v => setDays(Number(v))}
            opts={[[1, "1D"], [3, "3D"], [7, "7D"], [14, "14D"]]} />
          <div style={biasStrip}>
            <span style={badge("#4ade80")}>{summary.bullish} BULLISH</span>
            <span style={badge("#f87171")}>{summary.bearish} BEARISH</span>
            <span style={badge(labelLight)}>{filings.length - summary.bullish - summary.bearish} OTHER / UNKNOWN</span>
          </div>
        </div>
      </Card>

      <Card title="FILINGS - SORTED BY SIGNIFICANCE">
        {!filings.length ? (
          <div style={{ color: muted, padding: 20 }}>{loading || error || incomplete ? "Filing data unavailable." : "No stored filings match the filters."}</div>
        ) : (
          <div style={{ overflowX: "auto" }}><table style={{ width: "100%", minWidth: 680, borderCollapse: "collapse" }}>
            <thead>
              <tr>
                <th style={th}>TICKER</th><th style={th}>FORM</th><th style={th}>COMPANY</th>
                <th style={th}>DATE</th><th style={th}>SIG</th><th style={th}>NLS</th><th style={th}></th>
              </tr>
            </thead>
            <tbody>
              {filings.map((f, i) => {
                const open = expanded === i;
                return (
                  <Fragment key={`${f.ticker}-${f.form}-${f.accepted_at || f.updated || i}`}>
                    <tr className="row-hover" tabIndex={0} aria-expanded={open} onKeyDown={e => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); setExpanded(open ? null : i); } }} style={{ borderTop: hairline, cursor: "pointer" }} onClick={() => setExpanded(open ? null : i)} data-testid={`sec-${f.ticker}`}>
                      <td style={{ ...td, color: accent, fontWeight: 700 }}>${f.ticker}</td>
                      <td style={td}>
                        <span style={badge(FORM_COLORS[f.form] || labelLight)}>{f.form}</span>
                        {f.activist && <span style={{ marginLeft: 6, color: "#f87171", fontSize: 9, fontWeight: 700, letterSpacing: "0.1em" }}>- {String(f.activist).toUpperCase()}</span>}
                      </td>
                      <td style={{ ...td, fontSize: 11 }}>{f.company}</td>
                      <td style={{ ...td, color: muted }}>{f.filing_date || (typeof f.accepted_at === "string" ? f.accepted_at.slice(0, 10) : "-")}</td>
                      <td style={{ ...td, color: f.significance >= 80 ? "#4ade80" : f.significance >= 65 ? "#fbbf24" : labelLight, fontWeight: 700 }}>{metric(f.significance)}</td>
                      <td style={td}>{f.narrative_lock_badge ? <span style={badge("#fbbf24")}>LOCK {f.narrative_lock_score}</span> : <span style={{ color: dim }}>{f.narrative_lock_score}</span>}</td>
                      <td style={{ ...td, color: dim, textAlign: "right" }}>{open ? "v" : ">"}</td>
                    </tr>
                    {open && (
                      <tr style={{ background: "#03030680" }}>
                        <td colSpan={7} style={{ padding: "18px 24px" }}>
                          <Row k="SOURCE" v={f.source || "SEC EDGAR Atom"} />
                          <Row k="ACCEPTED" v={f.accepted_at || f.updated || "-"} />
                          <Row k="SUMMARY" v={f.summary} />
                          <Row k="BIAS" v={f.bias} color={f.bias === "BULLISH" ? "#4ade80" : f.bias === "BEARISH" ? "#f87171" : labelLight} />
                          <Row k="TRADABILITY" v={metric(f.tradability_pct, "%")} />
                          <Row k="EXPECTED EFFECT" v={pct(f.expected_effect_pct)} />
                          <Row k="NARRATIVE LOCK" v={metric(f.narrative_lock_score, "/100")} />
                          <Row k="CONCURRENT SIGNALS" v={textRows(f.concurrent_signals).join(", ") || "-"} />
                          <Row k="EDGAR LINK" v={safeLink(f.link) ? <a href={f.link} target="_blank" rel="noreferrer" style={{ color: accent }}>{f.link}</a> : "-"} />
                          <div style={{ display: "flex", justifyContent: "flex-end", marginTop: 14 }}>
                            <button
                              type="button"
                              disabled={battleLoading === String(f.ticker || "").toUpperCase()}
                              onClick={() => loadBattleCard(f.ticker)}
                              style={buttonStyle("#fbbf24")}
                            >
                              [ {battleLoading === String(f.ticker || "").toUpperCase() ? "LOADING..." : "SEC BATTLE CARD"} ]
                            </button>
                          </div>
                          {battleCards[String(f.ticker || "").toUpperCase()] && (
                            <SECBattleCard data={battleCards[String(f.ticker || "").toUpperCase()]} />
                          )}
                        </td>
                      </tr>
                    )}
                  </Fragment>
                );
              })}
            </tbody>
          </table></div>
        )}
      </Card>
    </CrtShell>
  );
}

function HeatList({ rows, total }) {
  if (!rows.length) return <div style={{ color: muted, padding: 10 }}>No form mix yet.</div>;
  return (
    <div style={{ display: "grid", gap: 9 }}>
      {rows.map(([name, count]) => {
        const pct = total ? (count / total) * 100 : 0;
        const color = FORM_COLORS[name] || labelLight;
        return (
          <div key={name}>
            <div style={{ display: "flex", justifyContent: "space-between", gap: 12, fontSize: 11 }}>
              <span style={{ color }}>{name}</span>
              <span style={{ color: labelLight, fontWeight: 800 }}>{count}</span>
            </div>
            <div style={barTrack}><div style={{ ...barFill, background: color, boxShadow: `0 0 10px ${color}66`, width: `${Math.max(4, pct)}%` }} /></div>
          </div>
        );
      })}
    </div>
  );
}

function Select({ label, value, onChange, opts }) {
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 4 }}>
      <span style={{ color: dim, fontSize: 9, letterSpacing: "0.18em", fontWeight: 700 }}>{label}</span>
      <select value={value} onChange={e => onChange(e.target.value)} style={{ background: cardBg, border: `0.5px solid ${dim}`, color: labelLight, fontSize: 12, padding: "7px 10px", fontFamily: "JetBrains Mono" }}>
        {opts.map(([v, l]) => <option key={v} value={v}>{l}</option>)}
      </select>
    </div>
  );
}

function Row({ k, v, color }) {
  return (
    <div style={{ display: "flex", padding: "6px 0", borderBottom: hairline, fontSize: 11 }}>
      <span style={{ color: dim, letterSpacing: "0.14em", flex: "0 0 35%" }}>{k}</span>
      <span style={{ color: color || labelLight, flex: 1, minWidth: 0, overflowWrap: "anywhere" }}>{v ?? "-"}</span>
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

function FreshMetric({ label, value, color }) {
  return (
    <div style={freshMetric}>
      <span style={{ color: dim, fontSize: 9, letterSpacing: "0.14em" }}>{label}</span>
      <strong style={{ color, fontSize: 20, letterSpacing: "0.08em" }}>{value ?? "-"}</strong>
    </div>
  );
}

function SECBattleCard({ data }) {
  if (data.error) {
    return <div style={{ ...battleCard, color: "#f87171" }}>{data.error}</div>;
  }
  const history = objectRows(data.history);
  const cluster = data.insider_cluster || {};
  const reaction = data.reaction_summary || {};
  const risks = objectRows(data.risk_language);
  const edgar = data.edgartools || {};
  const entity = edgar.entity || {};
  const fundamentals = edgar.fundamentals || {};
  const edgarFilings = objectRows(edgar.latest_filings);
  const partial = [data.history, data.risk_language, edgar.latest_filings].some(rows => !Array.isArray(rows) || objectRows(rows).length !== rows.length);
  return (
    <div style={battleCard}>
      {partial && <div role="alert" style={{ color: "#fbbf24", marginBottom: 10 }}>Battle card is incomplete; missing sections are unavailable, not clean results.</div>}
      <div style={battleHeader}>
        <div>
          <div style={eyebrow}>FILING BATTLE CARD</div>
          <div style={{ color: accent, fontSize: 28, fontWeight: 900, letterSpacing: "0.08em" }}>${data.ticker}</div>
          <div style={{ color: muted, fontSize: 12 }}>{data.company || "SEC issuer history"}</div>
        </div>
        <div style={battleStats}>
          <MiniMetric label="FILINGS" value={metric(data.filing_count)} color={labelLight} />
          <MiniMetric label="AVG 1M" value={pct(reaction.avg_30d_pct)} color={reactionColor(reaction.avg_30d_pct)} />
          <MiniMetric label="W/L" value={`${metric(reaction.wins)}/${metric(reaction.losses)}`} color="#fbbf24" />
          <MiniMetric label="INSIDER CLUSTER" value={cluster.active == null ? "UNKNOWN" : cluster.active ? "ACTIVE" : "QUIET"} color={cluster.active ? "#4ade80" : dim} />
          <MiniMetric label="EDGARTOOLS" value={edgar.ok ? "LIVE" : "FALLBACK"} color={edgar.ok ? "#4ade80" : "#fbbf24"} />
        </div>
      </div>

      <div style={battleGrid}>
        <div style={battlePanel}>
          <div style={panelTitle}>// COMPANY SEC HISTORY - 1 MONTH REACTION</div>
          <table style={{ width: "100%", borderCollapse: "collapse" }}>
            <thead>
              <tr>
                <th style={th}>ACCEPTED</th><th style={th}>FORM</th><th style={th}>ACCESSION</th><th style={th}>BIAS</th><th style={th}>1M %</th><th style={th}>STATUS</th>
              </tr>
            </thead>
            <tbody>
              {history.map((h, i) => {
                const r = h.reaction_30d || {};
                return (
                  <tr key={`${h.form}-${h.filing_date}-${i}`} style={{ borderTop: hairline }}>
                    <td style={td}>{formatAccepted(h.accepted_at || h.filing_date)}</td>
                    <td style={td}><span style={badge(FORM_COLORS[h.form] || labelLight)}>{h.form || "-"}</span></td>
                    <td style={{ ...td, color: muted }}>{h.accession || "-"}</td>
                    <td style={{ ...td, color: h.bias === "BULLISH" ? "#4ade80" : h.bias === "BEARISH" ? "#f87171" : labelLight }}>{h.bias || "UNKNOWN"}</td>
                    <td style={{ ...td, color: reactionColor(r.reaction_pct), fontWeight: 900 }}>{pct(r.reaction_pct)}</td>
                    <td style={{ ...td, color: r.status === "complete" ? "#4ade80" : r.status === "pending" ? "#fbbf24" : muted }}>{r.label || r.status || "-"}</td>
                  </tr>
                );
              })}
              {!history.length && (
                <tr><td colSpan={6} style={{ ...td, color: muted }}>{Array.isArray(data.history) ? "No company filing history stored yet." : "Company filing history unavailable."}</td></tr>
              )}
            </tbody>
          </table>
        </div>

        <div style={battlePanel}>
          <div style={panelTitle}>// EDGARTOOLS COMPANY FILE</div>
          <SmallLine k="Company" v={edgar.company || data.company || "-"} />
          <SmallLine k="CIK" v={edgar.cik || "-"} />
          <SmallLine k="Industry" v={entity.industry || "-"} />
          <SmallLine k="SIC" v={entity.sic || "-"} />
          <SmallLine k="Filer" v={entity.filer_category || "-"} />
          <SmallLine k="Fiscal Year End" v={entity.fiscal_year_end || "-"} />
          <SmallLine k="Public Float" v={compactMoney(fundamentals.public_float)} />
          <SmallLine k="Shares Out" v={compactNumber(fundamentals.shares_outstanding)} />
          <SmallLine k="TTM Revenue" v={compactMoney(fundamentals.ttm_revenue?.value)} />
          <SmallLine k="TTM Net Income" v={compactMoney(fundamentals.ttm_net_income?.value)} />
          <div style={{ display: "flex", gap: 6, flexWrap: "wrap", marginTop: 10 }}>
            {textRows(entity.flags).slice(0, 6).map(flag => <span key={flag} style={badge(labelLight)}>{flag.toUpperCase()}</span>)}
            {!edgar.ok && <span style={badge("#fbbf24")}>{(edgar.reason || "EDGARTOOLS UNAVAILABLE").slice(0, 42).toUpperCase()}</span>}
          </div>

          <div style={{ ...panelTitle, marginTop: 18 }}>// LATEST EDGAR COMPANY FILINGS</div>
          {edgarFilings.length ? edgarFilings.slice(0, 5).map((f, i) => (
            <div key={`${f.form}-${f.accession || i}`} style={riskHit}>
              <div style={{ display: "flex", justifyContent: "space-between", gap: 10 }}>
                <span style={badge(FORM_COLORS[f.form] || labelLight)}>{f.form || "FILING"}</span>
                <span style={{ color: muted }}>{f.filing_date || "-"}</span>
              </div>
              <div style={{ color: labelLight, marginTop: 7 }}>{f.description || f.accession || "SEC filing"}</div>
            </div>
          )) : (
            <div style={{ color: muted, fontSize: 12, lineHeight: 1.5 }}>No EdgarTools company filing snapshot loaded yet.</div>
          )}
        </div>

        <div style={battlePanel}>
          <div style={panelTitle}>// INSIDER CLUSTER</div>
          <SmallLine k="Window" v={cluster.window || "-"} />
          <SmallLine k="Recent Form 4" v={metric(cluster.recent_form4_count)} />
          <SmallLine k="Read" v={cluster.read || "-"} />
          <div style={{ ...clusterBadge, color: cluster.active ? "#4ade80" : dim, borderColor: `${cluster.active ? "#4ade80" : dim}66` }}>
            {cluster.active == null ? "CLUSTER UNKNOWN" : cluster.active ? "CLUSTER DETECTED" : "NO ACTIVE CLUSTER"}
          </div>

          <div style={{ ...panelTitle, marginTop: 18 }}>// RISK LANGUAGE SCANNER</div>
          {risks.length ? risks.map((r, i) => (
            <div key={`${r.form}-${r.filing_date}-${i}`} style={riskHit}>
              <div style={{ display: "flex", justifyContent: "space-between", gap: 10 }}>
                <span style={{ color: "#f87171", fontWeight: 900 }}>{r.form}</span>
                <span style={{ color: muted }}>{r.filing_date || "-"}</span>
              </div>
              <div style={{ display: "flex", gap: 6, flexWrap: "wrap", marginTop: 8 }}>
                {textRows(r.terms).map(term => <span key={term} style={badge("#f87171")}>{term.toUpperCase()}</span>)}
              </div>
            </div>
          )) : (
            <div style={{ color: muted, fontSize: 12, lineHeight: 1.5 }}>{Array.isArray(data.risk_language) ? "No high-risk language matched in stored title/summary text." : "Risk language data unavailable."}</div>
          )}
        </div>
      </div>
    </div>
  );
}

function MiniMetric({ label, value, color }) {
  return (
    <div style={miniMetric}>
      <span>{label}</span>
      <strong style={{ color }}>{value ?? "-"}</strong>
    </div>
  );
}

function filingAgeHours(filing, now) {
  const stamp = filing?.accepted_at || filing?.updated || filing?.filing_date;
  if (!stamp) return null;
  const parsed = new Date(stamp).getTime();
  if (!Number.isFinite(parsed)) return null;
  return parsed > now ? null : (now - parsed) / 36e5;
}

function formatAge(hours) {
  if (hours === null || hours === undefined) return "-";
  if (hours < 1) return "<1H";
  if (hours < 48) return `${Math.round(hours)}H`;
  return `${Math.round(hours / 24)}D`;
}

function freshnessColor(hours) {
  if (hours === null || hours === undefined) return dim;
  if (hours <= 24) return "#4ade80";
  if (hours <= 72) return "#fbbf24";
  return "#f87171";
}

function pct(value) {
  if (!validNumber(value)) return "-";
  const n = Number(value);
  return `${n >= 0 ? "+" : ""}${n.toFixed(2)}%`;
}

function compactMoney(value) {
  if (!validNumber(value)) return "-";
  const n = Number(value);
  const abs = Math.abs(n);
  if (abs >= 1e12) return `$${(n / 1e12).toFixed(2)}T`;
  if (abs >= 1e9) return `$${(n / 1e9).toFixed(2)}B`;
  if (abs >= 1e6) return `$${(n / 1e6).toFixed(2)}M`;
  return `$${n.toLocaleString(undefined, { maximumFractionDigits: 0 })}`;
}

function compactNumber(value) {
  if (!validNumber(value)) return "-";
  const n = Number(value);
  const abs = Math.abs(n);
  if (abs >= 1e9) return `${(n / 1e9).toFixed(2)}B`;
  if (abs >= 1e6) return `${(n / 1e6).toFixed(2)}M`;
  if (abs >= 1e3) return `${(n / 1e3).toFixed(2)}K`;
  return n.toLocaleString();
}

function formatAccepted(value) {
  if (!value) return "-";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return String(value).slice(0, 16);
  const yyyy = date.getFullYear();
  const mm = String(date.getMonth() + 1).padStart(2, "0");
  const dd = String(date.getDate()).padStart(2, "0");
  const hh = String(date.getHours()).padStart(2, "0");
  const min = String(date.getMinutes()).padStart(2, "0");
  return `${yyyy}-${mm}-${dd} ${hh}:${min}`;
}

function reactionColor(value) {
  if (!validNumber(value)) return muted;
  const n = Number(value);
  if (n >= 3) return "#4ade80";
  if (n <= -3) return "#f87171";
  return "#fbbf24";
}

const th = { padding: "10px 8px", fontSize: 10, color: dim, letterSpacing: "0.14em", fontWeight: 400, textAlign: "left" };
const td = { padding: "10px 8px", color: labelLight, letterSpacing: "0.04em", fontSize: 12 };
const commandGrid = { display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(min(100%, 300px), 1fr))", gap: 18 };
const eyebrow = { color: dim, fontSize: 9, letterSpacing: "0.18em", fontWeight: 800, marginBottom: 8 };
const panelTitle = { color: labelLight, fontSize: 11, letterSpacing: "0.14em", marginBottom: 10, fontWeight: 700 };
const tickerHero = { color: accent, fontSize: 42, fontWeight: 900, letterSpacing: "0.08em", textDecoration: "none" };
const heroCopy = { color: labelLight, lineHeight: 1.55, margin: "12px 0 0", maxWidth: 760 };
const miniPanel = { border: hairline, background: "rgba(255,255,255,0.018)", padding: "10px 14px", alignSelf: "start" };
const biasStrip = { display: "flex", gap: 8, alignItems: "end", flexWrap: "wrap", marginLeft: "auto" };
const barTrack = { height: 5, background: "rgba(255,255,255,0.06)", marginTop: 7, overflow: "hidden" };
const barFill = { height: "100%" };
const freshnessGrid = { display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(min(100%, 320px), 1fr))", gap: 18, alignItems: "stretch" };
const freshnessPanel = { border: hairline, background: "rgba(74,222,128,0.025)", padding: 16, minWidth: 0, overflow: "hidden" };
const urgentPanel = { border: hairline, background: "rgba(255,255,255,0.018)", padding: 14, display: "grid", gap: 8, overflowX: "auto" };
const freshMetricGrid = { display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(130px, 1fr))", gap: 10 };
const freshMetric = { border: hairline, background: "#020407cc", padding: "12px 13px", display: "grid", gap: 8, minWidth: 0 };
const sourceLine = { marginTop: 14, borderTop: hairline, paddingTop: 12, display: "flex", gap: 12, flexWrap: "wrap", alignItems: "center", color: dim, fontSize: 10, letterSpacing: "0.13em" };
const urgentRow = { display: "grid", gridTemplateColumns: "70px 92px 54px minmax(0, 1fr) 42px", gap: 10, alignItems: "center", padding: "8px 0", borderTop: hairline, textDecoration: "none", fontSize: 11 };
const battleCard = { marginTop: 18, border: `0.5px solid ${accent}55`, background: "rgba(0,0,0,0.28)", padding: 16 };
const battleHeader = { display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(min(100%, 280px), 1fr))", gap: 18, alignItems: "start", borderBottom: hairline, paddingBottom: 14, marginBottom: 14 };
const battleStats = { display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(96px, 1fr))", gap: 8 };
const battleGrid = { display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(min(100%, 280px), 1fr))", gap: 16 };
const battlePanel = { border: hairline, background: "rgba(255,255,255,0.018)", padding: 14, minWidth: 0, overflowX: "auto", overflowWrap: "anywhere" };
const miniMetric = { border: hairline, background: "#020407cc", padding: "10px 11px", display: "grid", gap: 6, color: dim, fontSize: 9, letterSpacing: "0.12em" };
const clusterBadge = { marginTop: 12, border: `0.5px solid ${dim}66`, padding: "9px 10px", fontWeight: 900, letterSpacing: "0.12em", fontSize: 10 };
const riskHit = { borderTop: hairline, padding: "10px 0", color: labelLight, fontSize: 11 };
function badge(color) {
  return { color, padding: "3px 8px", border: `0.5px solid ${color}66`, background: `${color}08`, letterSpacing: "0.12em", fontSize: 10, fontWeight: 700 };
}
function buttonStyle(color) {
  return { background: "transparent", border: `0.5px solid ${color}`, color, fontSize: 11, padding: "8px 16px", cursor: "pointer", letterSpacing: "0.14em", fontFamily: "JetBrains Mono", fontWeight: 700 };
}

function objectRows(value) { return Array.isArray(value) ? value.filter(row => row && typeof row === "object" && !Array.isArray(row)) : []; }
function textRows(value) { return Array.isArray(value) ? value.filter(item => typeof item === "string") : []; }
function validNumber(value) { return (typeof value === "number" || (typeof value === "string" && value.trim() !== "")) && Number.isFinite(Number(value)); }
function metric(value, suffix = "") { return validNumber(value) ? `${value}${suffix}` : "-"; }
function safeLink(value) { try { return ["http:", "https:"].includes(new URL(value).protocol); } catch { return false; } }
