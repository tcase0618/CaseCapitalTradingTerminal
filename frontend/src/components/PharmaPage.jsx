import { Fragment, useCallback, useEffect, useMemo, useRef, useState } from "react";
import axios from "axios";
import { API } from "../config";
import { Link } from "react-router-dom";
import { toast } from "sonner";
import { CrtShell, Card, Stat, tokens } from "./CrtShell";
import { displayResource } from "../hooks/useDisplayResource";

const { accent, accent2, dim, muted, labelLight, hairline, cardBg } = tokens;

const list = value => Array.isArray(value) ? value.filter(value => value != null) : [];
const rows = value => list(value).filter(value => typeof value === "object" && !Array.isArray(value));
const number = value => value == null || typeof value === "boolean" || String(value).trim() === "" || !Number.isFinite(Number(value)) ? null : Number(value);
const fixed = (value, digits = 0) => number(value) == null ? "-" : number(value).toFixed(digits);
const count = value => number(value) ?? "-";

// One-shot shared reads: do not introduce polling or cache the forced FDA import.
async function readDisplay(path, force = false) {
  const resource = displayResource(`${API}${path}`, 60000);
  const cached = resource.getSnapshot();
  if (force || cached.updatedAt == null || Date.now() - cached.updatedAt >= 60000) await resource.refresh(force);
  const state = resource.getSnapshot();
  if (state.error) throw state.error;
  return state.data;
}

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
  const [activeTab, setActiveTab] = useState("FDA_CALENDAR");
  const [loaded, setLoaded] = useState({});
  const [loadError, setLoadError] = useState("");
  const [calendarError, setCalendarError] = useState("");
  const calendarRequest = useRef(0);
  const calendarController = useRef(null);
  const feedController = useRef(null);
  const feedRequest = useRef(0);
  const mounted = useRef(false);

  const loadCalendar = useCallback((year = calendarYear, month = calendarMonth, forceRefresh = false) => {
    const request = ++calendarRequest.current;
    calendarController.current?.abort();
    const controller = new AbortController();
    calendarController.current = controller;
    setCalendarLoading(true);
    setCalendarError("");
    setCalendar(previous => previous?.year === year && previous?.month === month ? previous : null);
    axios.get(`${API}/pharma/fda_calendar`, { params: { year, month, force_refresh: forceRefresh }, timeout: forceRefresh ? 60000 : 15000, signal: controller.signal })
      .then(r => {
        if (!mounted.current || request !== calendarRequest.current) return;
        if (!r.data || !Array.isArray(r.data.days)) throw new Error("Malformed calendar");
        setCalendar(r.data || null);
        const firstEventDay = rows(r.data?.days).find(d => d.event_count > 0);
        const prefix = `${year}-${String(month).padStart(2, "0")}-`;
        setSelectedDate(prev => (prev && String(prev).startsWith(prefix)) ? prev : firstEventDay?.date || null);
      })
      .catch(() => { if (mounted.current && request === calendarRequest.current && !controller.signal.aborted) setCalendarError("FDA calendar unavailable; any retained values may be stale."); })
      .finally(() => { if (mounted.current && request === calendarRequest.current) setCalendarLoading(false); });
  }, [calendarMonth, calendarYear]);

  const reload = useCallback(async (force = false) => {
    const request = ++feedRequest.current;
    feedController.current?.abort();
    const controller = new AbortController();
    feedController.current = controller;
    const read = (path, key, setter, field, shared = true) => (shared ? readDisplay(path, force) : axios.get(`${API}${path}`, { signal: controller.signal, timeout: 15000 }).then(r => r.data))
      .then(data => {
        if (field && !Array.isArray(data?.[field])) throw new Error("Malformed feed");
        if (key === "track" && !Array.isArray(data?.history)) throw new Error("Malformed track record");
        if (!data || typeof data !== "object") throw new Error("Unavailable feed");
        if (mounted.current && request === feedRequest.current) {
          setter(field ? rows(data[field]) : data);
          setLoaded(previous => ({ ...previous, [key]: true }));
        }
      });
    const results = await Promise.allSettled([
      read("/pharma/pdufa?days=90", "pdufa", setPdufa, "results"),
      read("/pharma/shocks?limit=50", "shocks", setShocks, "results"),
      read("/pharma/active", "active", setActive, "plays", false),
      read("/pharma/track_record", "track", setTrack, null, false),
    ]);
    if (mounted.current && request === feedRequest.current) setLoadError(results.some(result => result.status === "rejected") ? "Pharma feeds unavailable or malformed; retained values may be stale." : "");
  }, []);

  useEffect(() => {
    mounted.current = true;
    reload();
    return () => { mounted.current = false; feedController.current?.abort(); calendarController.current?.abort(); };
  }, [reload]);
  useEffect(() => { loadCalendar(); }, [loadCalendar]);

  useEffect(() => {
    const top = [...pdufa]
      .sort((a, b) => (b.binary_event_score || 0) - (a.binary_event_score || 0))
      .slice(0, 4)
      .map(p => p.ticker)
      .filter(Boolean);
    if (!top.length) return;
    let cancelled = false;
    const controller = new AbortController();
    Promise.allSettled([...new Set(top)].map(async ticker => [ticker, (await axios.get(`${API}/data/free/ticker/${encodeURIComponent(ticker)}`, { signal: controller.signal, timeout: 15000 })).data]))
      .then(results => {
        if (cancelled) return;
        const next = {};
        results.forEach((r, index) => {
          next[[...new Set(top)][index]] = r.status === "fulfilled" ? r.value[1] : { ok: false, unavailable: true };
        });
        setFreeIntel(next);
      });
    return () => { cancelled = true; controller.abort(); };
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
      reload(true);
      loadCalendar(calendarYear, calendarMonth, true);
    } catch {
      toast("PHARMA SCAN FAILED");
    } finally {
      setScanning(false);
    }
  };

  const summary = useMemo(() => {
    const baseRows = pdufa.length ? pdufa : rows(calendar?.events);
    const counts = baseRows.reduce((a, p) => {
      a[p.tier] = (a[p.tier] || 0) + 1;
      return a;
    }, {});
    const sorted = [...baseRows].sort((a, b) => (b.binary_event_score || 0) - (a.binary_event_score || 0));
    const urgent = baseRows.filter(p => number(p.days_until) != null && number(p.days_until) >= 0 && number(p.days_until) <= 14).sort((a, b) => number(a.days_until) - number(b.days_until));
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
    return { counts, leader: sorted[0], urgent, speculative, baseRows };
  }, [pdufa, calendar]);

  return (
    <CrtShell
      title="PHARMA INTEL"
      headerRight={
        <button data-testid="pharma-scan-btn" onClick={runScan} disabled={scanning} style={buttonStyle(accent)}>
          [ {scanning ? "SCANNING..." : "PHARMA SCAN"} ]
        </button>
      }
    >
      <div style={{ minWidth: 0, maxWidth: "100%", overflowWrap: "anywhere" }}>
      {(loadError || calendarError) && <div role="status" style={{ color: "#fbbf24", padding: "10px 0", overflowWrap: "anywhere" }}>{[loadError, calendarError].filter(Boolean).join(" ")}</div>}
      <div style={{ display: "flex", background: cardBg, border: hairline, marginBottom: 22, flexWrap: "wrap" }}>
        <Stat label="PDUFA - 90D" value={loaded.pdufa ? pdufa.length : "-"} sub={`${count(calendar?.summary?.events)} FDA MONTH`} color={accent} accentBar />
        <Stat label="CATALYST SHOCKS" value={loaded.shocks ? shocks.length : "-"} sub={`${loaded.shocks ? shocks.filter(s => Number(s.shock_score) >= 75).length : "-"} HOT`} color="#fb7185" />
        <Stat label="STRONG >=80" value={loaded.pdufa || Array.isArray(calendar?.events) ? summary.counts.STRONG || 0 : "-"} sub="AUTO-ENTER" color={TIER_COLOR.STRONG} />
        <Stat label="WATCH >=65" value={loaded.pdufa || Array.isArray(calendar?.events) ? summary.counts.WATCH || 0 : "-"} sub="MONITORING" color={TIER_COLOR.WATCH} />
        <Stat label="ACTIVE PLAYS" value={loaded.active ? active.length : "-"} sub={`${count(track.open)} OPEN`} color={accent2} />
        <Stat label="HIT RATE" value={number(track.hit_rate) != null ? `${(number(track.hit_rate) * 100).toFixed(0)}%` : "-"} sub={`${count(track.winners)}/${count(track.settled)} SETTLED`} color="#4ade80" />
        <Stat label="UNREALIZED P&L" value={fmtSigned(track.avg_unrealized_pct)} sub="AVG OPEN" color={number(track.avg_unrealized_pct) == null ? muted : track.avg_unrealized_pct >= 0 ? "#4ade80" : "#f87171"} />
      </div>

      <div style={pharmaTabBar}>
        {[
          ["FDA_CALENDAR", "FDA Calendar"],
          ["COMMAND", "Command"],
          ["TRACK_RECORD", "Track Record"],
        ].map(([key, label]) => (
          <button key={key} type="button" aria-pressed={activeTab === key} onClick={() => setActiveTab(key)} style={pharmaTab(activeTab === key)}>
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
          setMonth={(m) => setCalendarMonth(Number(m))}
          setYear={(y) => setCalendarYear(Number(y))}
          refresh={() => loadCalendar(calendarYear, calendarMonth, true)}
        />
      ) : activeTab === "TRACK_RECORD" ? (
        <div style={gridTwo}>
          <Card title={`ACTIVE PLAYS - ${loaded.active ? active.length : "-"} OPEN`} accentColor={accent2}>
            {!active.length ? <div style={{ color: muted, padding: 20 }}>{loaded.active ? "No active plays yet. Plays scoring >= 80 auto-enter; everything else is manual." : "Active plays unavailable."}</div> : <ActiveTable rows={active} />}
          </Card>

          <Card title={`PHARMA TRACK RECORD - ${count(track.settled)} SETTLED - ISOLATED`} accentColor="#4ade80">
            {!rows(track.history).length ? <div style={{ color: muted, padding: 20 }}>{loaded.track ? "No closed plays yet. Track record is isolated from main P&L." : "Track record unavailable."}</div> : <TrackTable rows={rows(track.history)} />}
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
            <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(min(100%, 220px), 1fr))", gap: 18 }}>
              <div>
                <div style={eyebrow}>TOP CATALYST</div>
                <Link to={`/ticker/${summary.leader.ticker}`} style={tickerHero}>${summary.leader.ticker}</Link>
                <div style={{ color: scoreColor(summary.leader.binary_event_score), fontSize: 30, fontWeight: 900, marginTop: 8 }}>
                  {fixed(summary.leader.binary_event_score)}/100
                </div>
                <p style={heroCopy}>
                  {summary.leader.drug || "Unknown drug"} for {summary.leader.indication || "unknown indication"} has a PDUFA date of {summary.leader.pdufa_date || "-"}.
                </p>
                <div style={{ display: "flex", gap: 8, flexWrap: "wrap", marginTop: 12 }}>
                  <span style={badge(TIER_COLOR[summary.leader.tier] || labelLight)}>{summary.leader.tier || "UNKNOWN"}</span>
                  <span style={badge(Number(summary.leader.days_until) <= 14 ? "#fb923c" : accent2)}>{summary.leader.days_until ?? "-"}D TO EVENT</span>
                  <span style={badge(summary.leader.data_quality === "fallback_calendar" ? "#fbbf24" : accent2)}>{String(summary.leader.source || "UNKNOWN").replace(/_/g, " ").toUpperCase()}</span>
                </div>
              </div>
              <div style={miniPanel}>
                <SmallLine k="Prevalence" v={number(summary.leader.prevalence?.pct) != null ? `${fixed(summary.leader.prevalence.pct, 2)}%` : "-"} />
                <SmallLine k="Patients" v={summary.leader.prevalence?.patient_count?.toLocaleString() || "-"} />
                <SmallLine k="Short %" v={summary.leader.short_pct != null ? `${summary.leader.short_pct}%` : "-"} />
                <SmallLine k="IV Rank" v={summary.leader.iv_rank ?? "-"} />
              </div>
            </div>
          ) : (
            <div style={{ color: muted, padding: 20 }}>
              No scored 90-day PDUFA events are loaded. The FDA Calendar tab still shows the imported live FDA docket.
            </div>
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
                <span style={{ color: scoreColor(p.binary_event_score), fontWeight: 900 }}>{fixed(p.binary_event_score)}/100</span>
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
          <div style={{ color: muted, padding: 20 }}>
            No scored next-90-day PDUFA rows are currently active. FDA calendar has {count(calendar?.summary?.events)} event(s)
            for {monthName(calendarMonth)} {calendarYear}; use the FDA Calendar subtab for the full docket.
          </div>
        ) : (
          <div style={tableViewport} tabIndex={0} role="region" aria-label="PDUFA calendar table"><table style={tableStyle}>
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
                      <td style={{ ...td, color: scoreColor(p.binary_event_score), fontWeight: 700, fontSize: 14 }}>{fixed(p.binary_event_score)}<span style={{ color: dim, fontSize: 10 }}>/100</span></td>
                      <td style={td}><span style={badge(TIER_COLOR[p.tier] || labelLight)}>{p.tier}</span></td>
                      <td style={{ ...td, color: accent, fontWeight: 700 }}>${p.ticker}</td>
                      <td style={{ ...td, color: "#fff" }}>{p.drug}</td>
                      <td style={{ ...td, fontSize: 11 }} title={p.indication}>{String(p.indication || "-").slice(0, 40)}</td>
                      <td style={{ ...td, color: accent2, fontWeight: 600 }}>{fixed(p.prevalence?.pct, 2)}%<div style={{ fontSize: 9, color: muted }}>{count(p.prevalence?.patient_count)} US</div></td>
                      <td style={td}>{p.pdufa_date}</td>
                      <td style={{ ...td, color: p.days_until <= 14 ? "#fb923c" : labelLight }}>{p.days_until}d</td>
                      <td style={{ ...td, color: p.data_quality === "fallback_calendar" ? "#fbbf24" : accent2, fontSize: 10, fontWeight: 700 }}>{String(p.source || "UNKNOWN").replace(/_/g, " ").toUpperCase()}</td>
                      <td style={td}>{number(p.current_price) != null ? `$${fixed(p.current_price, 2)}` : "-"}</td>
                      <td style={{ ...td, color: dim, textAlign: "right" }}><button type="button" aria-label={`Research ${p.ticker}`} aria-expanded={open} onClick={e => { e.stopPropagation(); setExpanded(open ? null : key); }} style={buttonStyle(accent2)}>{open ? "v" : ">"}</button></td>
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
          </table></div>
        )}
          </Card>
        </>
      )}
      </div>
    </CrtShell>
  );
}

function FdaCalendar({ data, loading, month, year, selectedDate, setSelectedDate, setMonth, setYear, refresh }) {
  const cells = useMemo(() => buildFdaCells(year, month, rows(data?.days)), [year, month, data]);
  const years = [...new Set([year, ...list(data?.available_years).filter(y => number(y) != null), year + 1, year - 1])];
  const selected = selectedDate ? cells.find(cell => cell.date === selectedDate)?.day : rows(data?.days).find(d => d.event_count > 0);
  const summary = data?.summary || {};
  const weeks = fdaWeekSummary(cells);
  return (
    <Card title="FDA CALENDAR - PM ROUTED BINARY EVENTS" accentColor="#a78bfa">
      <div style={calendarShellHeader}>
        <div style={{ display: "flex", gap: 10, flexWrap: "wrap" }}>
          <select aria-label="FDA calendar month" value={month} onChange={e => setMonth(e.target.value)} style={selectStyle}>
            {Array.from({ length: 12 }).map((_, i) => <option key={i + 1} value={i + 1}>{monthName(i + 1)}</option>)}
          </select>
          <select aria-label="FDA calendar year" value={year} onChange={e => setYear(e.target.value)} style={selectStyle}>
            {years.map(y => <option key={y} value={y}>{y}</option>)}
          </select>
          <button onClick={refresh} disabled={loading} style={buttonStyle(accent2)}>{loading ? "LOADING" : "REFRESH FDA"}</button>
        </div>
        <div style={calendarSummaryStrip}>
          <span>EVENTS <b>{count(summary.events)}</b></span>
          <span>HOT <b>{count(summary.hot)}</b></span>
          <span>PM <b>{count(summary.pm_ready)}</b></span>
          <span>OPTIONS <b>{count(summary.option_ready)}</b></span>
          <span>BLOCK <b>{count(summary.blocked)}</b></span>
          <span>CROSS <b>{count(summary.cross_checked_calendar)}</b></span>
          <span>LIVE <b>{count(summary.live_calendar)}</b></span>
          <span>FALLBACK <b>{count(summary.fallback_calendar)}</b></span>
        </div>
      </div>
      <div style={calendarHeroStats}>
        <div style={calendarHeroTile("#a78bfa")}><span>FDA Month</span><strong>{monthName(month)}</strong><small>{year}</small></div>
        <div style={calendarHeroTile("#fbbf24")}><span>Hot Dockets</span><strong>{count(summary.hot)}</strong><small>score >= 70</small></div>
        <div style={calendarHeroTile("#4ade80")}><span>PM Routed</span><strong>{count(summary.pm_ready)}</strong><small>judge-ready</small></div>
        <div style={calendarHeroTile(accent2)}><span>Option Ready</span><strong>{count(summary.option_ready)}</strong><small>contract captured</small></div>
        <div style={calendarHeroTile("#f87171")}><span>Data Blocks</span><strong>{count(summary.blocked)}</strong><small>cannot route</small></div>
        <div style={calendarHeroTile("#93c5fd")}><span>Source Quality</span><strong>{count(summary.cross_checked_calendar ?? summary.live_calendar)}</strong><small>live/cross rows</small></div>
      </div>
      <div style={tableViewport} role="region" aria-label="FDA month grid" tabIndex={0}><div style={calendarBoard}>
        <div style={{ minWidth: 0 }}>
          <div style={calendarWeekHeader}>
            {["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"].map(d => <span key={d}>{d}</span>)}
          </div>
          <div style={calendarGrid}>
            {cells.map((cell, i) => (
              <button
                key={cell.date || `blank-${i}`}
                disabled={!cell.date || loading || !data}
                aria-label={cell.date ? `${cell.date}: ${number(cell.day?.event_count) == null ? "events unavailable" : `${cell.day.event_count} FDA events`}` : "Outside month"}
                aria-pressed={Boolean(cell.date && selected?.date === cell.date)}
                onClick={() => cell.date && setSelectedDate(cell.date)}
                title={fdaDayTitle(cell.day)}
                style={fdaCalendarCell(cell, selected?.date === cell.date)}
              >
                <span style={calendarDayNumber}>{cell.dayNumber || ""}</span>
                {cell.day?.event_count > 0 && (
                  <span style={calendarDayPayload}>
                    <strong>{cell.day.event_count} FDA</strong>
                    <small>{count(cell.day.hot_count)} hot / {count(cell.day.pm_ready_count)} PM</small>
                    <small>{number(cell.day.best_score) != null ? `${fixed(cell.day.best_score)}/100` : "pending"}</small>
                  </span>
                )}
              </button>
            ))}
          </div>
        </div>
        <div style={calendarWeekRail}>
          {weeks.map((week, idx) => (
            <div key={idx} style={calendarWeekCard(fdaWeekColor(week))} title={`Week ${idx + 1}\nEvents: ${count(week.events)}\nHot: ${count(week.hot)}\nBlocks: ${count(week.blocked)}`}>
              <span>Week {idx + 1}</span>
              <strong>{count(week.events)}</strong>
              <small>{count(week.hot)} hot</small>
            </div>
          ))}
        </div>
      </div></div>
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
  if (!Array.isArray(day.events)) return <div style={{ color: muted, padding: 20 }}>FDA events unavailable on {day.date}.</div>;
  if (!rows(day.events).length) {
    return <div style={{ color: muted, padding: 20 }}>No FDA/PDUFA events on {day.date}.</div>;
  }
  return (
    <div style={{ display: "grid", gap: 10 }}>
      <div style={panelTitle}>// SELECTED FDA DOCKET - {day.date}</div>
      {rows(day.events).map((event, idx) => (
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
  const gateColor = gate.decision === "BLOCK" ? "#f87171" : gate.decision === "WATCH" ? "#fbbf24" : gate.decision === "PASS" ? "#4ade80" : muted;
  return (
    <div style={fdaDocketCard(gateColor)}>
      <div style={{ display: "flex", justifyContent: "space-between", gap: 14, flexWrap: "wrap" }}>
        <div>
          <Link to={`/ticker/${event.ticker}`} style={{ color: accent, fontSize: 22, fontWeight: 900, textDecoration: "none" }}>${event.ticker}</Link>
          <div style={{ color: labelLight, marginTop: 5 }}>{event.drug || "Unknown drug"} - {event.indication || "unknown indication"}</div>
        </div>
        <div style={{ display: "flex", gap: 6, flexWrap: "wrap", alignItems: "flex-start" }}>
          <span style={badge(scoreColor(event.binary_event_score))}>{fixed(event.binary_event_score)}/100</span>
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
        <MiniRead label="Data Score" value={gate.score != null ? `${gate.score}/100` : "-"} sub={list(gate.blockers)[0] || list(gate.warnings)[0] || (gate.decision ? "no reported flags" : "unavailable")} color={gateColor} />
      </div>
      <div style={sourceChipRow}>
        {rows(gate.sources).slice(0, 8).map(source => (
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
    <div style={tableViewport} role="region" aria-label="Active pharma plays" tabIndex={0}><table style={tableStyle}>
      <thead><tr><th style={th}>SOURCE</th><th style={th}>TICKER</th><th style={th}>DRUG</th><th style={th}>PDUFA</th><th style={th}>P&L</th><th style={th}>SCORE</th></tr></thead>
      <tbody>{rows.map((p, i) => (
        <tr key={`${p.ticker}-${p.pdufa_date || i}`} className="row-hover" style={{ borderTop: hairline }}>
          <td style={td}>{String(p.source || "UNKNOWN").toUpperCase()}</td>
          <td style={{ ...td, color: accent, fontWeight: 700 }}>${p.ticker}</td>
          <td style={td}>{p.drug}</td>
          <td style={td}>{p.pdufa_date}</td>
          <td style={{ ...td, color: number(p.gain_pct) == null ? muted : p.gain_pct >= 0 ? "#4ade80" : "#f87171", fontWeight: 700 }}>{fmtSigned(p.gain_pct)}</td>
          <td style={{ ...td, color: scoreColor(p.entry_score), fontWeight: 700 }}>{fixed(p.entry_score)}/100</td>
        </tr>
      ))}</tbody>
    </table></div>
  );
}

function TrackTable({ rows }) {
  return (
    <div style={tableViewport} role="region" aria-label="Pharma track record" tabIndex={0}><table style={tableStyle}>
      <thead><tr><th style={th}>TICKER</th><th style={th}>DRUG</th><th style={th}>PDUFA</th><th style={th}>ENTRY</th><th style={th}>EXIT</th><th style={th}>REALIZED</th></tr></thead>
      <tbody>{rows.map((r, i) => (
        <tr key={`${r.ticker}-${r.exit_date || r.pdufa_date || i}`} className="row-hover" style={{ borderTop: hairline }}>
          <td style={{ ...td, color: accent, fontWeight: 700 }}>${r.ticker}</td>
          <td style={td}>{r.drug}</td>
          <td style={td}>{r.pdufa_date}</td>
          <td style={td}>{number(r.entry_price) != null ? `$${fixed(r.entry_price, 2)}` : "-"}</td>
          <td style={td}>{number(r.exit_price) != null ? `$${fixed(r.exit_price, 2)}` : "-"}</td>
          <td style={{ ...td, color: number(r.realized_pct) == null ? muted : r.realized_pct >= 0 ? "#4ade80" : "#f87171", fontWeight: 700 }}>{fmtSigned(r.realized_pct)}</td>
        </tr>
      ))}</tbody>
    </table></div>
  );
}

function ShockTape({ rows }) {
  const top = useMemo(() => [...rows].sort((a, b) => (number(b.shock_score) ?? -Infinity) - (number(a.shock_score) ?? -Infinity)).slice(0, 10), [rows]);
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
        const score = number(r.shock_score);
        const color = r.direction === "BEARISH" ? "#f87171" : score >= 85 ? "#4ade80" : "#fbbf24";
        const terms = [...list(r.bullish_terms), ...list(r.bearish_terms)].slice(0, 4);
        return (
          <div key={`${r.ticker}-${r.url || r.title || i}`} style={shockRow(color)}>
            <div style={{ minWidth: 84 }}>
              <Link to={`/ticker/${r.ticker}`} style={{ color: accent, textDecoration: "none", fontWeight: 900 }}>${r.ticker}</Link>
              <div style={{ color, fontSize: 10, marginTop: 4, fontWeight: 900 }}>{fixed(score)}/100</div>
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
              <div style={{ color: labelLight, fontWeight: 800 }}>{number(r.current_price) != null ? `$${fixed(r.current_price, 2)}` : "-"}</div>
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
  const q = rows(intel?.sources || intel?.source_quality);
  const trialCount = trials.returned_count ?? trials.trial_count;
  const fdaCount = fda.returned_count ?? fda.event_count ?? fda.events?.length;
  return (
    <div style={depthCard}>
      <div style={{ display: "flex", justifyContent: "space-between", gap: 10 }}>
        <Link to={`/ticker/${p.ticker}`} style={{ color: accent, fontWeight: 900, fontSize: 18, textDecoration: "none" }}>${p.ticker}</Link>
        <span style={badge(intel?.ok ? "#4ade80" : "#fb923c")}>{intel?.unavailable ? "UNAVAILABLE" : intel ? "LOADED" : "FETCHING"}</span>
      </div>
      <div style={{ color: labelLight, fontSize: 12, marginTop: 8, minHeight: 34 }}>{intel?.company_name || p.drug || p.indication}</div>
      <div style={depthMetrics}>
        <DepthMetric label="Revenue" value={money(metrics.revenue?.value)} sub={metrics.revenue?.period_end} />
        <DepthMetric label="Net Income" value={money(metrics.net_income?.value)} sub={metrics.net_income?.period_end} color={(metrics.net_income?.value || 0) >= 0 ? "#4ade80" : "#f87171"} />
        <DepthMetric label="Cash" value={money(metrics.cash?.value ?? metrics.cash_and_equivalents?.value)} sub={metrics.cash?.period_end || metrics.cash_and_equivalents?.period_end} />
        <DepthMetric label="Facts" value={count(facts.fact_count)} sub={facts.source || "SEC"} />
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
      <div title={String(value ?? "-")} style={{ color, fontSize: 12, fontWeight: 800, marginTop: 5, overflowWrap: "anywhere" }}>{value}</div>
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
    <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(min(100%, 260px), 1fr))", gap: 24 }}>
      <div>
        <div style={panelTitle}>// BINARY EVENT SCORE COMPONENTS</div>
        {Object.entries(comp).filter(([, v]) => v && typeof v === "object").map(([k, v]) => (
          <div key={k} style={{ display: "grid", gridTemplateColumns: "minmax(0, 1fr) minmax(0, 2fr) minmax(0, 1fr)", gap: 12, padding: "6px 0", borderBottom: hairline, fontSize: 12, overflowWrap: "anywhere" }}>
            <span style={{ color: dim, letterSpacing: "0.1em" }}>{k.toUpperCase()}</span>
            <span style={{ color: labelLight }}>{v.note}</span>
            <span style={{ color: scoreColor(v.points * 4), fontWeight: 700, textAlign: "right" }}>{v.points}/{v.max}</span>
          </div>
        ))}
        <div style={{ ...panelTitle, marginTop: 18 }}>// PM / OPTIONS ROUTING</div>
        <div style={fdaDocketGrid}>
          <MiniRead label="Gate" value={gate.decision || "UNKNOWN"} sub={list(gate.blockers)[0] || list(gate.warnings)[0] || (gate.decision ? "no reported flags" : "unavailable")} color={gate.decision === "BLOCK" ? "#f87171" : gate.decision === "WATCH" ? "#fbbf24" : gate.decision === "PASS" ? "#4ade80" : muted} />
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
        <Row k="PHASE" v={list(trial.phases).join(", ") || "-"} />
        <Row k="STATUS" v={trial.status || "-"} />
        <Row k="ENROLLMENT" v={count(trial.enrollment)} />
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
    <div style={{ display: "flex", flexWrap: "wrap", gap: 8, justifyContent: "space-between", padding: "5px 0", borderBottom: hairline, fontSize: 11 }}>
      <span style={{ color: dim, letterSpacing: "0.14em" }}>{k}</span>
      <span style={{ color: labelLight, fontWeight: 600, textAlign: "right" }}>{v}</span>
    </div>
  );
}

function SmallLine({ k, v }) {
  return <Row k={k} v={v} />;
}

function money(value) {
  const n = number(value);
  if (n == null) return "-";
  if (Math.abs(n) >= 1e9) return `$${(n / 1e9).toFixed(1)}B`;
  if (Math.abs(n) >= 1e6) return `$${(n / 1e6).toFixed(1)}M`;
  return `$${n.toLocaleString(undefined, { maximumFractionDigits: 0 })}`;
}

function fmtSigned(value) {
  const n = number(value);
  if (n == null) return "-";
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
    cells.push({ date, dayNumber: d, day: byDate.get(date) || { date, day: d, event_count: null, status: "UNKNOWN" } });
  }
  while (cells.length % 7 !== 0) cells.push({ date: null, dayNumber: null, day: null });
  return cells;
}

function fdaWeekSummary(cells) {
  const weeks = [];
  for (let i = 0; i < cells.length; i += 7) {
    const slice = cells.slice(i, i + 7).map(c => c.day).filter(Boolean);
    const total = key => slice.some(day => number(day[key]) == null) ? null : slice.reduce((sum, day) => sum + number(day[key]), 0);
    weeks.push({ events: total("event_count"), hot: total("hot_count"), blocked: total("blocked_count") });
  }
  return weeks;
}

function fdaDayTitle(day) {
  if (number(day?.event_count) == null) return "FDA events unavailable";
  if (!day?.event_count) return "No FDA events";
  return rows(day.events).map(e => `$${e.ticker} ${e.drug || ""} ${fixed(e.binary_event_score)}/100`).join("\n");
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
    minWidth: 0,
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
const td = { padding: "10px 8px", color: labelLight, letterSpacing: 0, fontSize: 12, overflowWrap: "anywhere" };
const tableViewport = { minWidth: 0, maxWidth: "100%", overflowX: "auto" };
const tableStyle = { width: "100%", minWidth: 680, borderCollapse: "collapse" };
const commandGrid = { display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(min(100%, 300px), 1fr))", gap: 18 };
const gridTwo = { display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(min(100%, 320px), 1fr))", gap: 18 };
const sourceLedgerGrid = { display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(min(100%, 280px), 1fr))", gap: 12 };
const riskQueue = { display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(min(100%, 240px), 1fr))", gap: 10 };
const pharmaTabBar = { display: "flex", gap: 8, flexWrap: "wrap", margin: "0 0 18px", borderBottom: hairline, paddingBottom: 10 };
const pharmaTab = (active) => ({
  ...buttonStyle(active ? accent2 : muted),
  background: active ? "rgba(153, 246, 228, 0.08)" : "rgba(255,255,255,0.012)",
  boxShadow: active ? `0 0 18px ${accent2}18` : "none",
});
const eyebrow = { color: dim, fontSize: 9, letterSpacing: "0.18em", fontWeight: 800, marginBottom: 8 };
const tickerHero = { color: accent, fontSize: 32, fontWeight: 900, letterSpacing: 0, textDecoration: "none", overflowWrap: "anywhere" };
const heroCopy = { color: labelLight, lineHeight: 1.55, margin: "12px 0 0", maxWidth: 760 };
const miniPanel = { border: hairline, background: "rgba(255,255,255,0.018)", padding: "10px 14px", alignSelf: "start" };
const panelTitle = { color: labelLight, fontSize: 11, letterSpacing: "0.14em", marginBottom: 10, fontWeight: 700 };
const depthCard = { border: hairline, borderTop: "1px solid rgba(147,197,253,0.7)", background: "linear-gradient(180deg, rgba(147,197,253,0.055), rgba(255,255,255,0.012))", padding: 13, minWidth: 0 };
const depthMetrics = { display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(min(100%, 85px), 1fr))", gap: 7, marginTop: 12 };
const calendarShellHeader = { display: "flex", justifyContent: "space-between", gap: 14, alignItems: "center", flexWrap: "wrap", marginBottom: 16 };
const calendarSummaryStrip = { display: "flex", gap: 14, flexWrap: "wrap", color: muted, fontSize: 10, letterSpacing: "0.14em" };
const calendarHeroStats = { display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(min(100%, 145px), 1fr))", gap: 10, marginBottom: 18 };
const calendarHeroTile = (color) => ({
  border: `0.5px solid ${color}55`,
  background: `linear-gradient(180deg, ${color}12, rgba(255,255,255,0.015))`,
  padding: "12px 13px",
  display: "grid",
  gap: 6,
  minHeight: 82,
});
const calendarBoard = { display: "grid", gridTemplateColumns: "minmax(0, 1fr) 120px", gap: 12, alignItems: "stretch", minWidth: 780 };
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
const fdaDocketGrid = { display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(min(100%, 145px), 1fr))", gap: 8, marginTop: 12 };
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
  return { display: "flex", flexWrap: "wrap", minWidth: 0, alignItems: "center", gap: 10, border: `0.5px solid ${color}55`, background: `${color}0c`, padding: "10px 12px", fontSize: 12 };
}
function shockRow(color) {
  return { display: "flex", flexWrap: "wrap", alignItems: "center", gap: 14, border: `0.5px solid ${color}55`, background: `${color}0c`, padding: "12px 14px", fontSize: 12, minWidth: 0 };
}
function buttonStyle(color) {
  return { background: "transparent", border: `0.5px solid ${color}`, color, fontSize: 11, padding: "8px 16px", cursor: "pointer", letterSpacing: "0.14em", fontFamily: "JetBrains Mono", fontWeight: 700 };
}
