import { Fragment, useCallback, useEffect, useMemo, useState } from "react";
import axios from "axios";
import { API } from "../config";
import { Link } from "react-router-dom";
import { toast } from "sonner";
import { CrtShell, Card, Stat, tokens } from "./CrtShell";

const { accent, accent2, dim, muted, labelLight, hairline, cardBg } = tokens;

const TIER_COLOR = {
  STRONG: "#4ade80",
  WATCH: "#fbbf24",
  NEUTRAL: "#9ca3af",
  WEAK: "#6b7280",
  MANUAL: accent2,
};

function scoreColor(s) {
  if (s == null) return muted;
  if (s >= 80) return "#4ade80";
  if (s >= 65) return "#fbbf24";
  if (s >= 40) return labelLight;
  return muted;
}

export default function PharmaPage() {
  const now = new Date();
  const [pdufa, setPdufa] = useState([]);
  const [active, setActive] = useState([]);
  const [shocks, setShocks] = useState([]);
  const [track, setTrack] = useState({});
  const [freeIntel, setFreeIntel] = useState({});
  const [expanded, setExpanded] = useState(null);
  const [scanning, setScanning] = useState(false);
  const [calendar, setCalendar] = useState(null);
  const [calendarLoading, setCalendarLoading] = useState(false);
  const [calendarMonth, setCalendarMonth] = useState(now.getMonth() + 1);
  const [calendarYear, setCalendarYear] = useState(now.getFullYear());
  const [selectedDate, setSelectedDate] = useState(null);
  const [activeTab, setActiveTab] = useState("COMMAND");

  const loadCalendar = useCallback((year = calendarYear, month = calendarMonth, forceRefresh = false) => {
    setCalendarLoading(true);
    axios.get(`${API}/pharma/fda_calendar`, { params: { year, month, force_refresh: forceRefresh }, timeout: forceRefresh ? 60000 : 15000 })
      .then(r => {
        setCalendar(r.data || null);
        const firstEventDay = (r.data?.days || []).find(d => d.event_count > 0);
        setSelectedDate(prev => prev || firstEventDay?.date || null);
      })
      .catch(() => {})
      .finally(() => setCalendarLoading(false));
  }, [calendarMonth, calendarYear]);

  const reload = useCallback(() => {
    axios.get(`${API}/pharma/pdufa?days=90`).then(r => setPdufa(r.data.results || [])).catch(() => {});
    axios.get(`${API}/pharma/shocks?limit=50`).then(r => setShocks(r.data.results || [])).catch(() => {});
    axios.get(`${API}/pharma/active`).then(r => setActive(r.data.plays || [])).catch(() => {});
    axios.get(`${API}/pharma/track_record`).then(r => setTrack(r.data || {})).catch(() => {});
    loadCalendar();
  }, [loadCalendar]);

  useEffect(() => { reload(); }, [reload]);

  useEffect(() => {
    const top = [...pdufa]
      .sort((a, b) => (b.binary_event_score || 0) - (a.binary_event_score || 0))
      .slice(0, 4)
      .map(p => p.ticker)
      .filter(Boolean);
    if (!top.length) return;
    let cancelled = false;
    Promise.allSettled(top.map(async ticker => [ticker, (await axios.get(`${API}/data/free/ticker/${ticker}`)).data]))
      .then(results => {
        if (cancelled) return;
        const next = {};
        results.forEach(r => {
          if (r.status === "fulfilled") next[r.value[0]] = r.value[1];
        });
        setFreeIntel(next);
      });
    return () => { cancelled = true; };
  }, [pdufa]);

  const runScan = async () => {
    setScanning(true);
    toast("PHARMA SCAN + CATALYST SHOCK SWEEP INITIATED");
    try {
      const [calendar, shock] = await Promise.all([
        axios.post(`${API}/pharma/scan`),
        axios.post(`${API}/pharma/shocks/scan`),
      ]);
      toast(
        `PHARMA SCAN - ${calendar.data.results?.length || 0} PDUFA - `
        + `${shock.data.hot_count || 0} HOT SHOCKS`
      );
      reload();
      loadCalendar(calendarYear, calendarMonth, true);
    } catch {
      toast("PHARMA SCAN FAILED");
    } finally {
      setScanning(false);
    }
  };

  const summary = useMemo(() => {
    const counts = pdufa.reduce((a, p) => {
      a[p.tier] = (a[p.tier] || 0) + 1;
      return a;
    }, {});
    const sorted = [...pdufa].sort((a, b) => (b.binary_event_score || 0) - (a.binary_event_score || 0));
    const urgent = pdufa.filter(p => Number(p.days_until) <= 14).sort((a, b) => (a.days_until || 999) - (b.days_until || 999));
    const speculative = sorted.map(p => ({
      ...p,
      riskFlags: [
        p.data_quality === "fallback_calendar" ? "fallback calendar" : null,
        Number(p.short_pct) >= 15 ? "high short interest" : null,
        Number(p.iv_rank) >= 60 ? "high IV" : null,
        !p.trial?.nct_id ? "missing trial id" : null,
        p.trial?.status && !["COMPLETED", "ACTIVE_NOT_RECRUITING"].includes(p.trial.status) ? `trial ${p.trial.status}` : null,
      ].filter(Boolean),
    }));
    return { counts, leader: sorted[0], urgent, speculative };
  }, [pdufa]);

  return (
    <CrtShell
      title="PHARMA INTEL"
      headerRight={
        <button data-testid="pharma-scan-btn" onClick={runScan} disabled={scanning} style={buttonStyle(accent)}>
          [ {scanning ? "SCANNING..." : "PHARMA SCAN"} ]
        </button>
      }
    >
      <div style={{ display: "flex", background: cardBg, border: hairline, marginBottom: 22, flexWrap: "wrap" }}>
        <Stat label="PDUFA - 90D" value={pdufa.length} sub="UPCOMING" color={accent} accentBar />
        <Stat label="CATALYST SHOCKS" value={shocks.length} sub={`${shocks.filter(s => Number(s.shock_score) >= 75).length} HOT`} color="#fb7185" />
        <Stat label="STRONG >=80" value={summary.counts.STRONG || 0} sub="AUTO-ENTER" color={TIER_COLOR.STRONG} />
        <Stat label="WATCH >=65" value={summary.counts.WATCH || 0} sub="MONITORING" color={TIER_COLOR.WATCH} />
        <Stat label="ACTIVE PLAYS" value={active.length} sub={`${track.open || 0} OPEN`} color={accent2} />
        <Stat label="HIT RATE" value={track.hit_rate != null ? `${(track.hit_rate * 100).toFixed(0)}%` : "-"} sub={`${track.winners || 0}/${track.settled || 0} SETTLED`} color="#4ade80" />
        <Stat label="UNREALIZED P&L" value={track.avg_unrealized_pct != null ? `${track.avg_unrealized_pct >= 0 ? "+" : ""}${track.avg_unrealized_pct}%` : "-"} sub="AVG OPEN" color={(track.avg_unrealized_pct ?? 0) >= 0 ? "#4ade80" : "#f87171"} />
      </div>

      <div style={pharmaTabBar}>
        {[
          ["COMMAND", "Command"],
          ["FDA_CALENDAR", "FDA Calendar"],
          ["TRACK_RECORD", "Track Record"],
        ].map(([key, label]) => (
          <button key={key} onClick={() => setActiveTab(key)} style={pharmaTab(activeTab === key)}>
            {label}
          </button>
        ))}
      </div>

      {activeTab === "FDA_CALENDAR" ? (
        <FdaCalendar
          data={calendar}
          loading={calendarLoading}
          month={calendarMonth}
          year={calendarYear}
          selectedDate={selectedDate}
          setSelectedDate={setSelectedDate}
          setMonth={(m) => { setCalendarMonth(Number(m)); loadCalendar(calendarYear, Number(m)); }}
          setYear={(y) => { setCalendarYear(Number(y)); loadCalendar(Number(y), calendarMonth); }}
          refresh={() => loadCalendar(calendarYear, calendarMonth, true)}
        />
      ) : activeTab === "TRACK_RECORD" ? (
        <div style={gridTwo}>
          <Card title={`ACTIVE PLAYS - ${active.length} OPEN`} accentColor={accent2}>
            {!active.length ? <div style={{ color: muted, padding: 20 }}>No active plays yet. Plays scoring >= 80 auto-enter; everything else is manual.</div> : <ActiveTable rows={active} />}
          </Card>

          <Card title={`PHARMA TRACK RECORD - ${track.settled || 0} SETTLED - ISOLATED`} accentColor="#4ade80">
            {!track.history?.length ? <div style={{ color: muted, padding: 20 }}>No closed plays yet. Track record is isolated from main P&L.</div> : <TrackTable rows={track.history} />}
          </Card>
        </div>
      ) : (
        <>
          <Card title="CATALYST SHOCK TAPE - SAME DAY CLINICAL / FDA NEWS" accentColor="#fb7185">
            <ShockTape rows={shocks} />
          </Card>

          <div style={commandGrid}>
        <Card title="BINARY EVENT COMMAND READ" accentColor={accent}>
          {summary.leader ? (
            <div style={{ display: "grid", gridTemplateColumns: "minmax(0, 1fr) minmax(220px, 0.45fr)", gap: 18 }}>
              <div>
                <div style={eyebrow}>TOP CATALYST</div>
                <Link to={`/ticker/${summary.leader.ticker}`} style={tickerHero}>${summary.leader.ticker}</Link>
                <div style={{ color: scoreColor(summary.leader.binary_event_score), fontSize: 30, fontWeight: 900, marginTop: 8 }}>
                  {summary.leader.binary_event_score?.toFixed(0) || "-"}/100
                </div>
                <p style={heroCopy}>
                  {summary.leader.drug || "Unknown drug"} for {summary.leader.indication || "unknown indication"} has a PDUFA date of {summary.leader.pdufa_date || "-"}.
                </p>
                <div style={{ display: "flex", gap: 8, flexWrap: "wrap", marginTop: 12 }}>
                  <span style={badge(TIER_COLOR[summary.leader.tier] || labelLight)}>{summary.leader.tier || "UNKNOWN"}</span>
                  <span style={badge(Number(summary.leader.days_until) <= 14 ? "#fb923c" : accent2)}>{summary.leader.days_until ?? "-"}D TO EVENT</span>
                  <span style={badge(summary.leader.data_quality === "fallback_calendar" ? "#fbbf24" : "#4ade80")}>{(summary.leader.source || "LIVE").replace(/_/g, " ").toUpperCase()}</span>
                </div>
              </div>
              <div style={miniPanel}>
                <SmallLine k="Prevalence" v={summary.leader.prevalence?.pct != null ? `${summary.leader.prevalence.pct.toFixed(2)}%` : "-"} />
                <SmallLine k="Patients" v={summary.leader.prevalence?.patient_count?.toLocaleString() || "-"} />
                <SmallLine k="Short %" v={summary.leader.short_pct != null ? `${summary.leader.short_pct}%` : "-"} />
                <SmallLine k="IV Rank" v={summary.leader.iv_rank ?? "-"} />
              </div>
            </div>
          ) : (
            <div style={{ color: muted, padding: 20 }}>No PDUFA events loaded. Run Pharma Scan.</div>
          )}
        </Card>

        <Card title="CATALYST COUNTDOWN" accentColor="#fb923c">
          {!summary.urgent.length ? (
            <div style={{ color: muted, padding: 20 }}>No PDUFA events inside 14 days.</div>
          ) : (
            <div style={{ display: "grid", gap: 9 }}>
              {summary.urgent.slice(0, 6).map(p => (
                <div key={`${p.ticker}-${p.pdufa_date}`} style={urgentRow(p.days_until)}>
                  <Link to={`/ticker/${p.ticker}`} style={{ color: accent, fontWeight: 900, textDecoration: "none" }}>${p.ticker}</Link>
                  <span style={{ color: labelLight, flex: 1 }}>{p.drug || p.indication || "Catalyst"}</span>
                  <span style={{ color: Number(p.days_until) <= 7 ? "#f87171" : "#fb923c", fontWeight: 900 }}>{p.days_until}D</span>
                </div>
              ))}
            </div>
          )}
        </Card>
          </div>

          <Card title="SPECULATIVE DATA DEPTH - FREE SOURCE LEDGER" accentColor="#93c5fd">
        <div style={sourceLedgerGrid}>
          {summary.speculative.slice(0, 4).map(p => (
            <DataDepthCard key={p.ticker} p={p} intel={freeIntel[p.ticker]} />
          ))}
          {!summary.speculative.length && <div style={{ color: muted, padding: 20 }}>No pharma candidates loaded.</div>}
        </div>
          </Card>

          <Card title="RISK STACK - WHY THIS IS SPECULATIVE" accentColor="#f87171">
        <div style={riskQueue}>
          {summary.speculative.slice(0, 8).map(p => (
            <div key={`${p.ticker}-${p.pdufa_date}-risk`} style={riskCard(p.riskFlags.length)}>
              <div style={{ display: "flex", justifyContent: "space-between", gap: 10 }}>
                <Link to={`/ticker/${p.ticker}`} style={{ color: accent, fontWeight: 900, textDecoration: "none" }}>${p.ticker}</Link>
                <span style={{ color: scoreColor(p.binary_event_score), fontWeight: 900 }}>{p.binary_event_score?.toFixed(0) || "-"}/100</span>
              </div>
              <div style={{ color: labelLight, fontSize: 12, marginTop: 6 }}>{p.drug} - {p.indication}</div>
              <div style={{ display: "flex", gap: 6, flexWrap: "wrap", marginTop: 9 }}>
                {(p.riskFlags.length ? p.riskFlags : ["clean current flags"]).map(flag => <span key={flag} style={badge(p.riskFlags.length ? "#f87171" : "#4ade80")}>{flag.toUpperCase()}</span>)}
              </div>
            </div>
          ))}
        </div>
          </Card>

          <Card title="PDUFA CALENDAR - NEXT 90 DAYS - SORTED BY SCORE">
        {!pdufa.length ? (
          <div style={{ color: muted, padding: 20 }}>No PDUFA dates loaded. Click PHARMA SCAN to pull from FDA calendar.</div>
        ) : (
          <table style={{ width: "100%", borderCollapse: "collapse" }}>
            <thead>
              <tr>
                <th style={th}>SCORE</th><th style={th}>TIER</th><th style={th}>TICKER</th><th style={th}>DRUG</th>
                <th style={th}>INDICATION</th><th style={th}>PREVALENCE</th><th style={th}>PDUFA</th><th style={th}>DAYS</th><th style={th}>SOURCE</th><th style={th}>PRICE</th><th style={th}></th>
              </tr>
            </thead>
            <tbody>
              {pdufa.map(p => {
                const key = `${p.ticker}-${p.pdufa_date}`;
                const open = expanded === key;
                return (
                  <Fragment key={key}>
                    <tr data-testid={`pdufa-${p.ticker}`} className="row-hover" style={{ borderTop: hairline, cursor: "pointer" }} onClick={() => setExpanded(open ? null : key)}>
                      <td style={{ ...td, color: scoreColor(p.binary_event_score), fontWeight: 700, fontSize: 14 }}>{p.binary_event_score != null ? p.binary_event_score.toFixed(0) : "-"}<span style={{ color: dim, fontSize: 10 }}>/100</span></td>
                      <td style={td}><span style={badge(TIER_COLOR[p.tier] || labelLight)}>{p.tier}</span></td>
                      <td style={{ ...td, color: accent, fontWeight: 700 }}>${p.ticker}</td>
                      <td style={{ ...td, color: "#fff" }}>{p.drug}</td>
                      <td style={{ ...td, fontSize: 11 }}>{p.indication?.slice(0, 40)}</td>
                      <td style={{ ...td, color: accent2, fontWeight: 600 }}>{p.prevalence?.pct?.toFixed(2)}%<div style={{ fontSize: 9, color: muted }}>{p.prevalence?.patient_count?.toLocaleString()} US</div></td>
                      <td style={td}>{p.pdufa_date}</td>
                      <td style={{ ...td, color: p.days_until <= 14 ? "#fb923c" : labelLight }}>{p.days_until}d</td>
                      <td style={{ ...td, color: p.data_quality === "fallback_calendar" ? "#fbbf24" : accent2, fontSize: 10, fontWeight: 700 }}>{(p.source || "LIVE").replace(/_/g, " ").toUpperCase()}</td>
                      <td style={td}>{p.current_price != null ? `$${p.current_price.toFixed(2)}` : "-"}</td>
                      <td style={{ ...td, color: dim, textAlign: "right" }}>{open ? "v" : ">"}</td>
                    </tr>
                    {open && (
                      <tr style={{ background: "#03030680" }}>
                        <td colSpan={11} style={{ padding: "18px 24px" }}>
                          <ResearchPanel p={p} />
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
        </>
      )}
    </CrtShell>
  );
}

function FdaCalendar({ data, loading, month, year, selectedDate, setSelectedDate, setMonth, setYear, refresh }) {
  const cells = buildFdaCells(year, month, data?.days || []);
  const years = data?.available_years?.length ? data.available_years : [year, year + 1, year - 1];
  const selected = (data?.days || []).find(d => d.date === selectedDate) || (data?.days || []).find(d => d.event_count > 0);
  const summary = data?.summary || {};
  const weeks = fdaWeekSummary(cells);
  return (
    <Card title="FDA CALENDAR - PM ROUTED BINARY EVENTS" accentColor="#a78bfa">
      <div style={calendarShellHeader}>
        <div style={{ display: "flex", gap: 10, flexWrap: "wrap" }}>
          <select value={month} onChange={e => setMonth(e.target.value)} style={selectStyle}>
            {Array.from({ length: 12 }).map((_, i) => <option key={i + 1} value={i + 1}>{monthName(i + 1)}</option>)}
          </select>
          <select value={year} onChange={e => setYear(e.target.value)} style={selectStyle}>
            {years.map(y => <option key={y} value={y}>{y}</option>)}
          </select>
          <button onClick={refresh} disabled={loading} style={buttonStyle(accent2)}>{loading ? "LOADING" : "REFRESH FDA"}</button>
        </div>
        <div style={calendarSummaryStrip}>
          <span>EVENTS <b>{summary.events || 0}</b></span>
          <span>HOT <b>{summary.hot || 0}</b></span>
          <span>PM <b>{summary.pm_ready || 0}</b></span>
          <span>OPTIONS <b>{summary.option_ready || 0}</b></span>
          <span>BLOCK <b>{summary.blocked || 0}</b></span>
          <span>CROSS <b>{summary.cross_checked_calendar || 0}</b></span>
          <span>LIVE <b>{summary.live_calendar || 0}</b></span>
          <span>FALLBACK <b>{summary.fallback_calendar || 0}</b></span>
        </div>
      </div>
      <div style={calendarHeroStats}>
        <div style={calendarHeroTile("#a78bfa")}><span>FDA Month</span><strong>{monthName(month)}</strong><small>{year}</small></div>
        <div style={calendarHeroTile("#fbbf24")}><span>Hot Dockets</span><strong>{summary.hot || 0}</strong><small>score >= 70</small></div>
        <div style={calendarHeroTile("#4ade80")}><span>PM Routed</span><strong>{summary.pm_ready || 0}</strong><small>judge-ready</small></div>
        <div style={calendarHeroTile(accent2)}><span>Option Ready</span><strong>{summary.option_ready || 0}</strong><small>contract captured</small></div>
        <div style={calendarHeroTile("#f87171")}><span>Data Blocks</span><strong>{summary.blocked || 0}</strong><small>cannot route</small></div>
        <div style={calendarHeroTile("#93c5fd")}><span>Source Quality</span><strong>{summary.cross_checked_calendar || summary.live_calendar || 0}</strong><small>live/cross rows</small></div>
      </div>
      <div style={calendarBoard}>
        <div style={{ minWidth: 0 }}>
          <div style={calendarWeekHeader}>
            {["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"].map(d => <span key={d}>{d}</span>)}
          </div>
          <div style={calendarGrid}>
            {cells.map((cell, i) => (
              <button
                key={cell.date || `blank-${i}`}
                disabled={!cell.date}
                onClick={() => cell.date && setSelectedDate(cell.date)}
                title={fdaDayTitle(cell.day)}
                style={fdaCalendarCell(cell, selected?.date === cell.date)}
              >
                <span style={calendarDayNumber}>{cell.dayNumber || ""}</span>
                {cell.day?.event_count > 0 && (
                  <span style={calendarDayPayload}>
                    <strong>{cell.day.event_count} FDA</strong>
                    <small>{cell.day.hot_count || 0} hot / {cell.day.pm_ready_count || 0} PM</small>
                    <small>{cell.day.best_score != null ? `${Number(cell.day.best_score).toFixed(0)}/100` : "pending"}</small>
                  </span>
                )}
              </button>
            ))}
          </div>
        </div>
        <div style={calendarWeekRail}>
          {weeks.map((week, idx) => (
            <button key={idx} style={calendarWeekCard(fdaWeekColor(week))} title={`Week ${idx + 1}\nEvents: ${week.events}\nHot: ${week.hot}\nBlocks: ${week.blocked}`}>
              <span>Week {idx + 1}</span>
              <strong>{week.events}</strong>
              <small>{week.hot} hot</small>
            </button>
          ))}
        </div>
      </div>
      <div style={{ marginTop: 16 }}>
        <SelectedFdaDay day={selected} />
      </div>
    </Card>
  );
}

function SelectedFdaDay({ day }) {
  if (!day) {
    return <div style={{ color: muted, padding: 20 }}>Select an FDA calendar day to inspect PM dockets, options snapshots, and evidence gates.</div>;
  }
  if (!day.events?.length) {
    return <div style={{ color: muted, padding: 20 }}>No FDA/PDUFA events on {day.date}.</div>;
  }
  return (
    <div style={{ display: "grid", gap: 10 }}>
      <div style={panelTitle}>// SELECTED FDA DOCKET - {day.date}</div>
      {day.events.map((event, idx) => (
        <FdaEventDocket key={`${event.ticker}-${event.pdufa_date}-${idx}`} event={event} />
      ))}
    </div>
  );
}

function FdaEventDocket({ event }) {
  const gate = event.data_gate || {};
  const pm = event.pm_summary || {};
  const opt = event.option_summary || {};
  const scenario = event.scenario || {};
  const strategy = event.strategy_read || {};
  const gateColor = gate.decision === "BLOCK" ? "#f87171" : gate.decision === "WATCH" ? "#fbbf24" : "#4ade80";
  return (
    <div style={fdaDocketCard(gateColor)}>
      <div style={{ display: "flex", justifyContent: "space-between", gap: 14, flexWrap: "wrap" }}>
        <div>
          <Link to={`/ticker/${event.ticker}`} style={{ color: accent, fontSize: 22, fontWeight: 900, textDecoration: "none" }}>${event.ticker}</Link>
          <div style={{ color: labelLight, marginTop: 5 }}>{event.drug || "Unknown drug"} - {event.indication || "unknown indication"}</div>
        </div>
        <div style={{ display: "flex", gap: 6, flexWrap: "wrap", alignItems: "flex-start" }}>
          <span style={badge(scoreColor(event.binary_event_score))}>{Number(event.binary_event_score || 0).toFixed(0)}/100</span>
          <span style={badge(gateColor)}>GATE {gate.decision || "UNKNOWN"}</span>
          <span style={badge(pm.action === "REJECT" ? "#f87171" : pm.action === "NOT_ROUTED" ? "#9ca3af" : "#4ade80")}>PM {pm.action || "PENDING"}</span>
          <span style={badge(opt.ok ? "#4ade80" : "#fbbf24")}>OPT {opt.status || "NO_SNAPSHOT"}</span>
        </div>
      </div>
      <div style={fdaDocketGrid}>
        <MiniRead label="PDUFA" value={event.pdufa_date || "-"} sub={`${event.days_until ?? "-"} days`} color="#a78bfa" />
        <MiniRead label="Strategy" value={strategy.lane || "-"} sub={strategy.strategy || "-"} color={accent2} />
        <MiniRead label="Approval Proxy" value={scenario.approval_probability_proxy != null ? `${scenario.approval_probability_proxy}%` : "-"} sub={scenario.model || "research"} color="#4ade80" />
        <MiniRead label="Scenario" value={`${fmtSigned(scenario.base_move_pct)} base`} sub={`${fmtSigned(scenario.bear_move_pct)} / ${fmtSigned(scenario.bull_move_pct)}`} color={scenario.base_move_pct >= 0 ? "#4ade80" : "#f87171"} />
        <MiniRead label="Contract" value={opt.contract || "NONE"} sub={opt.expiration ? `${opt.expiration} ${opt.strike || ""}` : opt.reason || "-"} color={opt.ok ? "#4ade80" : "#fbbf24"} />
        <MiniRead label="Data Score" value={gate.score != null ? `${gate.score}/100` : "-"} sub={(gate.blockers || [])[0] || (gate.warnings || [])[0] || "clean"} color={gateColor} />
      </div>
      <div style={sourceChipRow}>
        {(gate.sources || []).slice(0, 8).map(source => (
          <span key={source.key} style={badge(source.status === "PASS" ? "#4ade80" : source.status === "BLOCK" ? "#f87171" : "#fbbf24")}>
            {source.key}:{source.status}
          </span>
        ))}
      </div>
    </div>
  );
}

function MiniRead({ label, value, sub, color = labelLight }) {
  return (
    <div style={miniReadCard}>
      <span>{label}</span>
      <strong style={{ color }}>{value}</strong>
      <small>{sub || "-"}</small>
    </div>
  );
}

function ActiveTable({ rows }) {
  return (
    <table style={{ width: "100%", borderCollapse: "collapse" }}>
      <thead><tr><th style={th}>SOURCE</th><th style={th}>TICKER</th><th style={th}>DRUG</th><th style={th}>PDUFA</th><th style={th}>P&L</th><th style={th}>SCORE</th></tr></thead>
      <tbody>{rows.map((p, i) => (
        <tr key={`${p.ticker}-${p.pdufa_date || i}`} className="row-hover" style={{ borderTop: hairline }}>
          <td style={td}>{p.source?.toUpperCase()}</td>
          <td style={{ ...td, color: accent, fontWeight: 700 }}>${p.ticker}</td>
          <td style={td}>{p.drug}</td>
          <td style={td}>{p.pdufa_date}</td>
          <td style={{ ...td, color: (p.gain_pct ?? 0) >= 0 ? "#4ade80" : "#f87171", fontWeight: 700 }}>{p.gain_pct != null ? `${p.gain_pct >= 0 ? "+" : ""}${p.gain_pct}%` : "-"}</td>
          <td style={{ ...td, color: scoreColor(p.entry_score), fontWeight: 700 }}>{p.entry_score?.toFixed(0) || "-"}/100</td>
        </tr>
      ))}</tbody>
    </table>
  );
}

function TrackTable({ rows }) {
  return (
    <table style={{ width: "100%", borderCollapse: "collapse" }}>
      <thead><tr><th style={th}>TICKER</th><th style={th}>DRUG</th><th style={th}>PDUFA</th><th style={th}>ENTRY</th><th style={th}>EXIT</th><th style={th}>REALIZED</th></tr></thead>
      <tbody>{rows.map((r, i) => (
        <tr key={`${r.ticker}-${r.exit_date || r.pdufa_date || i}`} className="row-hover" style={{ borderTop: hairline }}>
          <td style={{ ...td, color: accent, fontWeight: 700 }}>${r.ticker}</td>
          <td style={td}>{r.drug}</td>
          <td style={td}>{r.pdufa_date}</td>
          <td style={td}>{r.entry_price ? `$${r.entry_price.toFixed(2)}` : "-"}</td>
          <td style={td}>{r.exit_price ? `$${r.exit_price.toFixed(2)}` : "-"}</td>
          <td style={{ ...td, color: (r.realized_pct ?? 0) >= 0 ? "#4ade80" : "#f87171", fontWeight: 700 }}>{r.realized_pct != null ? `${r.realized_pct >= 0 ? "+" : ""}${r.realized_pct}%` : "-"}</td>
        </tr>
      ))}</tbody>
    </table>
  );
}

function ShockTape({ rows }) {
  const top = [...(rows || [])].sort((a, b) => (b.shock_score || 0) - (a.shock_score || 0)).slice(0, 10);
  if (!top.length) {
    return (
      <div style={{ color: muted, padding: 20 }}>
        No same-day pharma catalyst shocks captured yet. This tape watches clinical trial, Phase 3, oncology vaccine, FDA approval, and trial-failure headlines.
      </div>
    );
  }
  return (
    <div style={{ display: "grid", gap: 9 }}>
      {top.map((r, i) => {
        const score = Number(r.shock_score || 0);
        const color = r.direction === "BEARISH" ? "#f87171" : score >= 85 ? "#4ade80" : "#fbbf24";
        const terms = [...(r.bullish_terms || []), ...(r.bearish_terms || [])].slice(0, 4);
        return (
          <div key={`${r.ticker}-${r.url || r.title || i}`} style={shockRow(color)}>
            <div style={{ minWidth: 84 }}>
              <Link to={`/ticker/${r.ticker}`} style={{ color: accent, textDecoration: "none", fontWeight: 900 }}>${r.ticker}</Link>
              <div style={{ color, fontSize: 10, marginTop: 4, fontWeight: 900 }}>{score.toFixed(0)}/100</div>
            </div>
            <div style={{ minWidth: 0, flex: 1 }}>
              <div style={{ color: labelLight, fontWeight: 800, lineHeight: 1.35 }}>{r.title || "Clinical/FDA catalyst detected"}</div>
              <div style={{ display: "flex", gap: 6, flexWrap: "wrap", marginTop: 7 }}>
                <span style={badge(color)}>{r.direction || "WATCH"}</span>
                <span style={badge(accent2)}>{r.source || r.source_key || "NEWS"}</span>
                {terms.map(term => <span key={term} style={badge("#93c5fd")}>{String(term).toUpperCase()}</span>)}
              </div>
            </div>
            <div style={{ textAlign: "right", minWidth: 96 }}>
              <div style={{ color: labelLight, fontWeight: 800 }}>{r.current_price ? `$${Number(r.current_price).toFixed(2)}` : "-"}</div>
              {r.url && <a href={r.url} target="_blank" rel="noreferrer" style={{ color: accent2, fontSize: 10, letterSpacing: "0.12em", textDecoration: "none" }}>SOURCE</a>}
            </div>
          </div>
        );
      })}
    </div>
  );
}

function DataDepthCard({ p, intel }) {
  const facts = intel?.sec?.companyfacts || {};
  const metrics = facts.metrics || {};
  const trials = intel?.clinical_trials || {};
  const fda = intel?.openfda || {};
  const q = intel?.sources || intel?.source_quality || [];
  const trialCount = trials.returned_count ?? trials.trial_count;
  const fdaCount = fda.returned_count ?? fda.event_count ?? fda.events?.length;
  return (
    <div style={depthCard}>
      <div style={{ display: "flex", justifyContent: "space-between", gap: 10 }}>
        <Link to={`/ticker/${p.ticker}`} style={{ color: accent, fontWeight: 900, fontSize: 18, textDecoration: "none" }}>${p.ticker}</Link>
        <span style={badge(intel?.ok ? "#4ade80" : "#fb923c")}>{intel ? "LOADED" : "FETCHING"}</span>
      </div>
      <div style={{ color: labelLight, fontSize: 12, marginTop: 8, minHeight: 34 }}>{intel?.company_name || p.drug || p.indication}</div>
      <div style={depthMetrics}>
        <DepthMetric label="Revenue" value={money(metrics.revenue?.value)} sub={metrics.revenue?.period_end} />
        <DepthMetric label="Net Income" value={money(metrics.net_income?.value)} sub={metrics.net_income?.period_end} color={(metrics.net_income?.value || 0) >= 0 ? "#4ade80" : "#f87171"} />
        <DepthMetric label="Cash" value={money(metrics.cash?.value || metrics.cash_and_equivalents?.value)} sub={metrics.cash?.period_end || metrics.cash_and_equivalents?.period_end} />
        <DepthMetric label="Facts" value={facts.fact_count || "-"} sub={facts.source || "SEC"} />
      </div>
      <div style={{ display: "grid", gap: 6, marginTop: 10 }}>
        <SmallLine k="ClinicalTrials" v={`${trials.quality || "unknown"} ${trialCount != null ? `(${trialCount})` : ""}`} />
        <SmallLine k="openFDA" v={`${fda.quality || "unknown"} ${fdaCount != null ? `(${fdaCount})` : ""}`} />
        <SmallLine k="SEC CIK" v={intel?.sec?.lookup?.cik || "-"} />
      </div>
      <div style={{ display: "flex", gap: 5, flexWrap: "wrap", marginTop: 10 }}>
        {q.map(source => <span key={source.key} style={badge(source.ok ? "#4ade80" : "#fb923c")}>{source.key}:{source.quality}</span>)}
      </div>
    </div>
  );
}

function DepthMetric({ label, value, sub, color = labelLight }) {
  return (
    <div style={{ border: hairline, background: "rgba(255,255,255,0.016)", padding: "8px 7px", minWidth: 0 }}>
      <div style={{ color: dim, fontSize: 8, letterSpacing: "0.14em" }}>{label}</div>
      <div style={{ color, fontSize: 12, fontWeight: 800, marginTop: 5, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{value}</div>
      <div style={{ color: muted, fontSize: 8, marginTop: 3, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{sub || "-"}</div>
    </div>
  );
}

function ResearchPanel({ p }) {
  const comp = p.score_components || {};
  const trial = p.trial || {};
  const gate = p.data_gate || {};
  const pm = p.pm_summary || {};
  const opt = p.option_summary || {};
  const strategy = p.strategy_read || {};
  const scenario = p.scenario || {};
  const fdaLink = trial.nct_id ? `https://clinicaltrials.gov/study/${trial.nct_id}` : "https://www.fda.gov/drugs/development-resources/drug-approvals-and-databases";
  return (
    <div style={{ display: "grid", gridTemplateColumns: "2fr 1fr", gap: 24 }}>
      <div>
        <div style={panelTitle}>// BINARY EVENT SCORE COMPONENTS</div>
        {Object.entries(comp).map(([k, v]) => (
          <div key={k} style={{ display: "grid", gridTemplateColumns: "150px 1fr 70px", gap: 12, padding: "6px 0", borderBottom: hairline, fontSize: 12 }}>
            <span style={{ color: dim, letterSpacing: "0.1em" }}>{k.toUpperCase()}</span>
            <span style={{ color: labelLight }}>{v.note}</span>
            <span style={{ color: scoreColor(v.points * 4), fontWeight: 700, textAlign: "right" }}>{v.points}/{v.max}</span>
          </div>
        ))}
        <div style={{ ...panelTitle, marginTop: 18 }}>// PM / OPTIONS ROUTING</div>
        <div style={fdaDocketGrid}>
          <MiniRead label="Gate" value={gate.decision || "UNKNOWN"} sub={(gate.blockers || [])[0] || (gate.warnings || [])[0] || "clean"} color={gate.decision === "BLOCK" ? "#f87171" : gate.decision === "WATCH" ? "#fbbf24" : "#4ade80"} />
          <MiniRead label="PM Ruling" value={pm.action || "NOT_ROUTED"} sub={pm.score != null ? `score ${Number(pm.score).toFixed(1)}` : pm.authority || "-"} color={pm.action === "REJECT" ? "#f87171" : pm.action === "NOT_ROUTED" ? muted : "#4ade80"} />
          <MiniRead label="Option" value={opt.contract || opt.status || "NONE"} sub={opt.expiration || opt.reason || "-"} color={opt.ok ? "#4ade80" : "#fbbf24"} />
          <MiniRead label="Strategy" value={strategy.lane || "-"} sub={strategy.strategy || "-"} color={accent2} />
          <MiniRead label="Approval Proxy" value={scenario.approval_probability_proxy != null ? `${scenario.approval_probability_proxy}%` : "-"} sub={scenario.model || "research"} color="#4ade80" />
          <MiniRead label="Scenario" value={`${fmtSigned(scenario.base_move_pct)} base`} sub={`${fmtSigned(scenario.bear_move_pct)} / ${fmtSigned(scenario.bull_move_pct)}`} color={scenario.base_move_pct >= 0 ? "#4ade80" : "#f87171"} />
        </div>
      </div>
      <div>
        <div style={panelTitle}>// CLINICAL DATA</div>
        <Row k="NCT ID" v={trial.nct_id || "-"} />
        <Row k="PHASE" v={(trial.phases || []).join(", ") || "-"} />
        <Row k="STATUS" v={trial.status || "-"} />
        <Row k="ENROLLMENT" v={trial.enrollment || "-"} />
        <Row k="PRIMARY COMPLETION" v={trial.primary_completion || "-"} />
        <Row k="SHORT INTEREST" v={p.short_pct != null ? `${p.short_pct}%` : "-"} />
        <Row k="IV RANK" v={p.iv_rank != null ? `${p.iv_rank}` : "-"} />
        <a href={fdaLink} target="_blank" rel="noreferrer" style={{ ...buttonStyle(accent), display: "block", textAlign: "center", textDecoration: "none", marginTop: 14 }}>READ SOURCE</a>
      </div>
    </div>
  );
}

function Row({ k, v }) {
  return (
    <div style={{ display: "flex", justifyContent: "space-between", padding: "5px 0", borderBottom: hairline, fontSize: 11 }}>
      <span style={{ color: dim, letterSpacing: "0.14em" }}>{k}</span>
      <span style={{ color: labelLight, fontWeight: 600, textAlign: "right" }}>{v}</span>
    </div>
  );
}

function SmallLine({ k, v }) {
  return <Row k={k} v={v} />;
}

function money(value) {
  const n = Number(value);
  if (!Number.isFinite(n)) return "-";
  if (Math.abs(n) >= 1e9) return `$${(n / 1e9).toFixed(1)}B`;
  if (Math.abs(n) >= 1e6) return `$${(n / 1e6).toFixed(1)}M`;
  return `$${n.toLocaleString(undefined, { maximumFractionDigits: 0 })}`;
}

function fmtSigned(value) {
  const n = Number(value);
  if (!Number.isFinite(n)) return "-";
  return `${n >= 0 ? "+" : ""}${n.toFixed(1)}%`;
}

function monthName(month) {
  const idx = Math.max(0, Math.min(11, Number(month) - 1));
  return ["JANUARY", "FEBRUARY", "MARCH", "APRIL", "MAY", "JUNE", "JULY", "AUGUST", "SEPTEMBER", "OCTOBER", "NOVEMBER", "DECEMBER"][idx];
}

function buildFdaCells(year, month, days) {
  const first = new Date(Number(year), Number(month) - 1, 1);
  const count = new Date(Number(year), Number(month), 0).getDate();
  const byDate = new Map((days || []).map(day => [day.date, day]));
  const cells = [];
  for (let i = 0; i < first.getDay(); i += 1) cells.push({ date: null, dayNumber: null, day: null });
  for (let d = 1; d <= count; d += 1) {
    const date = `${year}-${String(month).padStart(2, "0")}-${String(d).padStart(2, "0")}`;
    cells.push({ date, dayNumber: d, day: byDate.get(date) || { date, day: d, event_count: 0, events: [], status: "EMPTY" } });
  }
  while (cells.length % 7 !== 0) cells.push({ date: null, dayNumber: null, day: null });
  return cells;
}

function fdaWeekSummary(cells) {
  const weeks = [];
  for (let i = 0; i < cells.length; i += 7) {
    const slice = cells.slice(i, i + 7).map(c => c.day).filter(Boolean);
    weeks.push({
      events: slice.reduce((a, d) => a + Number(d.event_count || 0), 0),
      hot: slice.reduce((a, d) => a + Number(d.hot_count || 0), 0),
      blocked: slice.reduce((a, d) => a + Number(d.blocked_count || 0), 0),
    });
  }
  return weeks;
}

function fdaDayTitle(day) {
  if (!day?.event_count) return "No FDA events";
  return (day.events || []).map(e => `$${e.ticker} ${e.drug || ""} ${Number(e.binary_event_score || 0).toFixed(0)}/100`).join("\n");
}

function fdaStatusColor(status) {
  if (status === "BLOCK") return "#f87171";
  if (status === "HOT") return "#fbbf24";
  if (status === "EVENT") return "#a78bfa";
  return "rgba(255,255,255,0.09)";
}

function fdaWeekColor(week) {
  if (week.blocked) return "#f87171";
  if (week.hot) return "#fbbf24";
  if (week.events) return "#a78bfa";
  return dim;
}

function fdaCalendarCell(cell, active) {
  const color = fdaStatusColor(cell.day?.status);
  const hasEvent = Number(cell.day?.event_count || 0) > 0;
  return {
    minHeight: 112,
    border: active ? `1px solid ${accent2}` : `0.5px solid ${hasEvent ? `${color}88` : "rgba(255,255,255,0.08)"}`,
    background: hasEvent
      ? `linear-gradient(180deg, ${color}22, rgba(255,255,255,0.018))`
      : "rgba(255,255,255,0.015)",
    boxShadow: active ? `0 0 22px ${accent2}22` : "none",
    color: labelLight,
    display: "grid",
    alignContent: "space-between",
    padding: "9px 8px",
    textAlign: "left",
    cursor: cell.date ? "pointer" : "default",
    opacity: cell.date ? 1 : 0.28,
    fontFamily: "JetBrains Mono",
    overflow: "hidden",
  };
}

const th = { padding: "10px 8px", fontSize: 10, color: dim, letterSpacing: "0.14em", fontWeight: 400, textAlign: "left" };
const td = { padding: "10px 8px", color: labelLight, letterSpacing: "0.04em", fontSize: 12 };
const commandGrid = { display: "grid", gridTemplateColumns: "minmax(0, 1.2fr) minmax(300px, 0.8fr)", gap: 18 };
const gridTwo = { display: "grid", gridTemplateColumns: "minmax(0, 1fr) minmax(320px, 0.9fr)", gap: 18 };
const sourceLedgerGrid = { display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(280px, 1fr))", gap: 12 };
const riskQueue = { display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(240px, 1fr))", gap: 10 };
const pharmaTabBar = { display: "flex", gap: 8, flexWrap: "wrap", margin: "0 0 18px", borderBottom: hairline, paddingBottom: 10 };
const pharmaTab = (active) => ({
  ...buttonStyle(active ? accent2 : muted),
  background: active ? "rgba(153, 246, 228, 0.08)" : "rgba(255,255,255,0.012)",
  boxShadow: active ? `0 0 18px ${accent2}18` : "none",
});
const eyebrow = { color: dim, fontSize: 9, letterSpacing: "0.18em", fontWeight: 800, marginBottom: 8 };
const tickerHero = { color: accent, fontSize: 42, fontWeight: 900, letterSpacing: "0.08em", textDecoration: "none" };
const heroCopy = { color: labelLight, lineHeight: 1.55, margin: "12px 0 0", maxWidth: 760 };
const miniPanel = { border: hairline, background: "rgba(255,255,255,0.018)", padding: "10px 14px", alignSelf: "start" };
const panelTitle = { color: labelLight, fontSize: 11, letterSpacing: "0.14em", marginBottom: 10, fontWeight: 700 };
const depthCard = { border: hairline, borderTop: "1px solid rgba(147,197,253,0.7)", background: "linear-gradient(180deg, rgba(147,197,253,0.055), rgba(255,255,255,0.012))", padding: 13 };
const depthMetrics = { display: "grid", gridTemplateColumns: "repeat(4, minmax(0, 1fr))", gap: 7, marginTop: 12 };
const calendarShellHeader = { display: "flex", justifyContent: "space-between", gap: 14, alignItems: "center", flexWrap: "wrap", marginBottom: 16 };
const calendarSummaryStrip = { display: "flex", gap: 14, flexWrap: "wrap", color: muted, fontSize: 10, letterSpacing: "0.14em" };
const calendarHeroStats = { display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(145px, 1fr))", gap: 10, marginBottom: 18 };
const calendarHeroTile = (color) => ({
  border: `0.5px solid ${color}55`,
  background: `linear-gradient(180deg, ${color}12, rgba(255,255,255,0.015))`,
  padding: "12px 13px",
  display: "grid",
  gap: 6,
  minHeight: 82,
});
const calendarBoard = { display: "grid", gridTemplateColumns: "minmax(0, 1fr) 120px", gap: 12, alignItems: "stretch" };
const calendarWeekHeader = { display: "grid", gridTemplateColumns: "repeat(7, minmax(0, 1fr))", gap: 5, color: dim, fontSize: 10, letterSpacing: "0.08em", margin: "12px 0 8px" };
const calendarGrid = { display: "grid", gridTemplateColumns: "repeat(7, minmax(0, 1fr))", gap: 5 };
const calendarDayNumber = { alignSelf: "flex-start", color: dim, fontSize: 10, lineHeight: 1 };
const calendarDayPayload = { display: "grid", gap: 3, placeItems: "center", textAlign: "center", color: labelLight, minHeight: 62 };
const calendarWeekRail = { display: "grid", gap: 7, alignContent: "start", paddingTop: 24 };
const calendarWeekCard = (color) => ({
  minHeight: 67,
  border: `0.5px solid ${color}55`,
  background: `${color}10`,
  color: labelLight,
  textAlign: "left",
  padding: "9px 10px",
  display: "grid",
  gap: 3,
  fontFamily: "JetBrains Mono",
  cursor: "default",
});
const selectStyle = { ...buttonStyle(labelLight), color: labelLight, background: "#06070c" };
const fdaDocketCard = (color) => ({ border: `0.5px solid ${color}66`, borderLeft: `3px solid ${color}`, background: "rgba(255,255,255,0.018)", padding: "14px 16px" });
const fdaDocketGrid = { display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(145px, 1fr))", gap: 8, marginTop: 12 };
const sourceChipRow = { display: "flex", gap: 6, flexWrap: "wrap", marginTop: 12 };
const miniReadCard = { border: hairline, background: "rgba(255,255,255,0.018)", padding: "9px 10px", display: "grid", gap: 5, minWidth: 0 };
function riskCard(flagCount) {
  const color = flagCount ? "#f87171" : "#4ade80";
  return { border: `0.5px solid ${color}55`, background: `${color}0b`, padding: "11px 12px" };
}
function badge(color) {
  return { color, padding: "3px 8px", border: `0.5px solid ${color}66`, background: `${color}08`, letterSpacing: "0.12em", fontSize: 10, fontWeight: 700 };
}
function urgentRow(days) {
  const color = Number(days) <= 7 ? "#f87171" : "#fb923c";
  return { display: "flex", alignItems: "center", gap: 10, border: `0.5px solid ${color}55`, background: `${color}0c`, padding: "10px 12px", fontSize: 12 };
}
function shockRow(color) {
  return { display: "flex", alignItems: "center", gap: 14, border: `0.5px solid ${color}55`, background: `${color}0c`, padding: "12px 14px", fontSize: 12, minWidth: 0 };
}
function buttonStyle(color) {
  return { background: "transparent", border: `0.5px solid ${color}`, color, fontSize: 11, padding: "8px 16px", cursor: "pointer", letterSpacing: "0.14em", fontFamily: "JetBrains Mono", fontWeight: 700 };
}
