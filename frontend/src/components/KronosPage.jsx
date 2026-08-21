import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import axios from "axios";
import { API } from "../config";
import {
  ResponsiveContainer,
  AreaChart,
  Area,
  BarChart,
  Bar,
  CartesianGrid,
  Cell,
  LineChart,
  Line,
  XAxis,
  YAxis,
  Tooltip,
  ReferenceLine,
} from "recharts";
import { CrtShell, Card, Stat, tokens } from "./CrtShell";
import { DataConfidenceStrip } from "./Institutional";
import TradingViewMiniChart from "./TradingViewMiniChart";

const { accent, accent2, dim, muted, labelLight, hairline, cardBg, pageBg } = tokens;

const th = { padding: "10px 8px", fontSize: 10, color: dim, letterSpacing: "0.14em", fontWeight: 500, textAlign: "left" };
const td = { padding: "11px 8px", color: labelLight, letterSpacing: "0.04em", fontSize: 12, borderTop: hairline, verticalAlign: "top" };
const biasColors = { BULLISH: "#4ade80", BEARISH: "#f87171", CHOP: "#fbbf24", HEDGE: "#a78bfa" };

export default function KronosPage() {
  const [scan, setScan] = useState(null);
  const [pm, setPm] = useState(null);
  const [equityPositions, setEquityPositions] = useState(null);
  const [optionPositions, setOptionPositions] = useState(null);
  const [optionRisk, setOptionRisk] = useState(null);
  const [optionTrades, setOptionTrades] = useState(null);
  const [tracker, setTracker] = useState(null);
  const [lseHealth, setLseHealth] = useState(null);
  const [macro, setMacro] = useState(null);
  const [kronos, setKronos] = useState(null);
  const [kronosStatus, setKronosStatus] = useState(null);
  const [kronosAccuracy, setKronosAccuracy] = useState(null);
  const [disagreements, setDisagreements] = useState(null);
  const [tab, setTab] = useState("FORECAST");
  const [selectedKey, setSelectedKey] = useState(null);
  const [selectedContext, setSelectedContext] = useState(null);
  const [chartKey, setChartKey] = useState("SPY");
  const [chartCandles, setChartCandles] = useState(null);
  const [candleSuite, setCandleSuite] = useState(null);
  const [chartLoading, setChartLoading] = useState(false);
  const [sandboxDraft, setSandboxDraft] = useState("SPY");
  const [sandboxSymbol, setSandboxSymbol] = useState("SPY");
  const [sandbox, setSandbox] = useState(null);
  const [sandboxLoading, setSandboxLoading] = useState(false);
  const now = useMemo(() => new Date(), []);
  const [calendarMonth, setCalendarMonth] = useState(now.getMonth() + 1);
  const [calendarYear, setCalendarYear] = useState(now.getFullYear());
  const [calendar, setCalendar] = useState(null);
  const [calendarLoading, setCalendarLoading] = useState(false);
  const [selectedCalendarDay, setSelectedCalendarDay] = useState(null);
  const [loading, setLoading] = useState(true);
  const [actionLoading, setActionLoading] = useState(false);
  const [lastSync, setLastSync] = useState(null);
  const refreshInFlight = useRef(false);

  const refresh = useCallback(async () => {
    if (refreshInFlight.current) return;
    refreshInFlight.current = true;
    setLoading(true);
    const get = (path, fallback, timeout = 10000) =>
      axios.get(`${API}${path}`, { timeout }).catch(e => ({ data: { ...fallback, error: e.message, degraded: true } }));
    try {
      const [scanRes, pmRes, eqRes, optRes, riskRes, tradeRes, trackerRes, healthRes, macroRes, kronosRes, statusRes, accuracyRes, disagreementRes] = await Promise.all([
        get("/scan/latest", { results: [] }),
        get("/portfolio_manager/latest", { decisions: [] }, 12000),
        get("/trade_floor/positions", { db_positions: [], live_alpaca: [] }),
        get("/options_desk/positions", { positions: [] }),
        get("/options_desk/risk", { checks: [] }),
        get("/options_desk/trades?sync_live=false", { trades: [] }),
        get("/signals/tracker?limit=250", { rows: [] }),
        get("/data/lse/health", { ok: false }),
        get("/data/lse/macro?limit=80", { economic_calendar: [], bond_yields: [] }),
        get("/kronos/forecast?persist=true", { forecasts: [] }, 12000),
        get("/kronos/status", { ok: false, health: "DEGRADED" }, 12000),
        get("/kronos/accuracy?limit=900", { ok: false, overall: {} }, 16000),
        get("/kronos/disagreements?limit=250", { rows: [] }),
      ]);
      setScan(scanRes.data);
      setPm(pmRes.data);
      setEquityPositions(eqRes.data);
      setOptionPositions(optRes.data);
      setOptionRisk(riskRes.data);
      setOptionTrades(tradeRes.data);
      setTracker(trackerRes.data);
      setLseHealth(healthRes.data);
      setMacro(macroRes.data);
      setKronos(kronosRes.data);
      setKronosStatus(statusRes.data);
      setKronosAccuracy(accuracyRes.data);
      setDisagreements(disagreementRes.data);
      setLastSync(new Date().toISOString());
    } finally {
      refreshInFlight.current = false;
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    refresh();
    const id = setInterval(refresh, 60000);
    return () => clearInterval(id);
  }, [refresh]);

  const forceRefresh = useCallback(async () => {
    if (actionLoading) return;
    setActionLoading(true);
    try {
      const r = await axios.post(`${API}/kronos/refresh`, {}, { timeout: 16000 });
      setKronos(r.data?.forecast || r.data);
      setKronosStatus(r.data?.status || null);
      const d = await axios.get(`${API}/kronos/disagreements?limit=250`, { timeout: 10000 }).catch(() => null);
      if (d?.data) setDisagreements(d.data);
      const a = await axios.get(`${API}/kronos/accuracy?limit=900&persist=true`, { timeout: 16000 }).catch(() => null);
      if (a?.data) setKronosAccuracy(a.data);
      setLastSync(new Date().toISOString());
    } finally {
      setActionLoading(false);
    }
  }, [actionLoading]);

  const forecasts = useMemo(() => buildForecasts({
    scan,
    pm,
    equityPositions,
    optionPositions,
    optionRisk,
    optionTrades,
    tracker,
    macro,
  }), [scan, pm, equityPositions, optionPositions, optionRisk, optionTrades, tracker, macro]);
  const backendForecasts = useMemo(() => normalizeBackendForecasts(kronos), [kronos]);
  const activeForecasts = backendForecasts.length ? backendForecasts : forecasts;

  useEffect(() => {
    if (!selectedKey && activeForecasts.length) setSelectedKey(activeForecasts[0].key);
  }, [activeForecasts, selectedKey]);

  const selected = activeForecasts.find(f => f.key === selectedKey) || activeForecasts[0] || null;
  const selectedBackend = useMemo(() => {
    if (!selected) return null;
    return (kronos?.forecasts || []).find(r =>
      String(r.contract || "") === String(selected.contract || "")
      || (String(r.ticker || "").toUpperCase() === selected.ticker && String(r.instrument || "").toUpperCase() === selected.instrument)
    );
  }, [kronos, selected]);
  const selectedFull = selected ? { ...selected, ...(selectedBackend || {}) } : null;
  const market = useMemo(() => kronos?.market_forecast || {}, [kronos]);
  const cone = useMemo(() => kronos?.portfolio_day_cone || {}, [kronos]);
  const chartChoices = useMemo(() => buildChartChoices(activeForecasts), [activeForecasts]);
  const chartChoice = chartChoices.find(c => c.key === chartKey) || chartChoices[0];
  const chartForecast = useMemo(() => buildChartForecast(chartChoice, activeForecasts, market), [chartChoice, activeForecasts, market]);

  useEffect(() => {
    if (!chartChoices.find(c => c.key === chartKey)) setChartKey("SPY");
  }, [chartChoices, chartKey]);

  useEffect(() => {
    let cancelled = false;
    const ticker = selected?.ticker;
    if (!ticker) {
      setSelectedContext(null);
      return () => { cancelled = true; };
    }
    setSelectedContext({ loading: true, ticker });
    Promise.all([
      axios.get(`${API}/data/lse/ticker/${ticker}`, { timeout: 10000 }).catch(e => ({ data: { error: e.message, degraded: true } })),
      axios.get(`${API}/data/lse/candles/${ticker}?timeframe=1d&limit=80`, { timeout: 10000 }).catch(e => ({ data: { rows: [], error: e.message, degraded: true } })),
      axios.get(`${API}/data/lse/options_flow?underlying=${ticker}&limit=40&max_dte=90`, { timeout: 10000 }).catch(e => ({ data: { rows: [], error: e.message, degraded: true } })),
    ]).then(([profile, candles, flow]) => {
      if (!cancelled) setSelectedContext({ loading: false, ticker, profile: profile.data, candles: candles.data, flow: flow.data });
    });
    return () => { cancelled = true; };
  }, [selected?.ticker]);

  const stats = useMemo(() => summarizeForecasts(activeForecasts, lseHealth, selectedContext), [activeForecasts, lseHealth, selectedContext]);
  const scenario = selectedFull ? buildScenario(selectedFull) : [];
  const radarRows = buildRadar(activeForecasts);
  const tripwires = activeForecasts.filter(f => f.tripwires.length > 0);
  const macroTone = inferMacroTone(macro);

  useEffect(() => {
    let cancelled = false;
    const ticker = chartChoice?.ticker || "SPY";
    setChartLoading(true);
    setCandleSuite(null);
    const path = ticker === "SPY"
      ? `${API}/price/history/SPY?days=140`
      : `${API}/data/lse/candles/${ticker}?timeframe=1d&limit=140&order=asc`;
    Promise.all([
      axios.get(path, { timeout: 12000 }).catch(e => ({ data: { rows: [], error: e.message, degraded: true } })),
      axios.get(`${API}/kronos/candle_forecast/${ticker}?persist=true`, { timeout: 16000 }).catch(e => ({ data: { ok: false, error: e.message, timeframes: [] } })),
    ])
      .then(([priceRes, candleRes]) => {
        if (!cancelled) {
          setChartCandles(priceRes.data);
          setCandleSuite(candleRes.data);
        }
      })
      .finally(() => {
        if (!cancelled) setChartLoading(false);
      });
    return () => { cancelled = true; };
  }, [chartChoice?.ticker]);

  const runSandbox = useCallback(async (symbol) => {
    const ticker = normalizeTicker(symbol || sandboxDraft || "SPY") || "SPY";
    setSandboxSymbol(ticker);
    setSandboxLoading(true);
    const get = (path, fallback, timeout = 10000) =>
      axios.get(`${API}${path}`, { timeout }).catch(e => ({ data: { ...fallback, error: e.message, degraded: true } }));
    const [profile, candles, flow, macroRes] = await Promise.all([
      get(`/data/lse/ticker/${ticker}`, { error: "missing profile" }),
      get(`/data/lse/candles/${ticker}?timeframe=1d&limit=120&order=asc`, { rows: [] }),
      get(`/data/lse/options_flow?underlying=${ticker}&limit=80&max_dte=120`, { rows: [] }),
      get("/data/lse/macro?limit=80", { economic_calendar: [], bond_yields: [] }),
    ]);
    setSandbox({ ticker, profile: profile.data, candles: candles.data, flow: flow.data, macro: macroRes.data, syncedAt: new Date().toISOString() });
    setSandboxLoading(false);
  }, [sandboxDraft]);

  useEffect(() => {
    if (tab === "SANDBOX" && !sandbox && !sandboxLoading) runSandbox(sandboxSymbol);
  }, [tab, sandbox, sandboxLoading, sandboxSymbol, runSandbox]);

  const loadCalendar = useCallback(async (year = calendarYear, month = calendarMonth) => {
    setCalendarLoading(true);
    try {
      const r = await axios.get(`${API}/kronos/calendar`, { params: { year, month }, timeout: 12000 });
      setCalendar(r.data);
      setSelectedCalendarDay((r.data.days || []).find(d => d.has_prediction) || (r.data.days || [])[0] || null);
    } catch (e) {
      setCalendar({ ok: false, error: e.message, year, month, days: [] });
      setSelectedCalendarDay(null);
    } finally {
      setCalendarLoading(false);
    }
  }, [calendarMonth, calendarYear]);

  useEffect(() => {
    if (tab === "CALENDAR" && !calendar && !calendarLoading) loadCalendar();
  }, [tab, calendar, calendarLoading, loadCalendar]);

  return (
    <CrtShell
      title="KRONOS FORECAST LAB"
      headerRight={<div style={{ display: "flex", gap: 10, flexWrap: "wrap", justifyContent: "flex-end" }}>
        <button onClick={refresh} disabled={loading} style={buttonStyle(accent2)}>{loading ? "SYNCING" : "REFRESH VIEW"}</button>
        <button onClick={forceRefresh} disabled={actionLoading} style={buttonStyle(accent)}>{actionLoading ? "RUNNING" : "FORCE KRONOS"}</button>
      </div>}
    >
      <div style={hero}>
        <MarketForecastBand market={market} cone={cone} />
        <div style={bootBox}>
          <KronosStatusPanel status={kronosStatus} lseHealth={lseHealth} pm={pm} lastSync={lastSync} />
        </div>
      </div>

      <DataConfidenceStrip
        title="KRONOS SOURCE CONFIDENCE"
        items={[
          { label: "Forecast Engine", value: kronosStatus?.health || "CHECKING" },
          { label: "LSE Feed", value: lseHealth?.ok ? "ONLINE" : "DEGRADED" },
          { label: "PM Context", value: kronosStatus?.pm_context_health || "CHECKING", color: (kronosStatus?.unmapped_pm || 0) ? "#fbbf24" : "#4ade80", detail: `${kronosStatus?.mapped_pm ?? 0}/${kronosStatus?.positions ?? 0} mapped` },
          { label: "Open Audits", value: kronosStatus?.open_disagreement_audits ?? 0, color: kronosStatus?.open_disagreement_audits ? "#fbbf24" : "#4ade80" },
          { label: "Last Sync", value: fmtTime(lastSync), color: accent2 },
        ]}
      />

      <div className="kronos-stat-grid" style={{ display: "grid", gridTemplateColumns: "repeat(9, minmax(0, 1fr))", background: cardBg, border: hairline, marginBottom: 22 }}>
        <Stat label="MODEL HEALTH" value={kronosStatus?.health || "CHECKING"} sub={`snapshot ${ageText(kronosStatus?.snapshot_age_minutes)}`} color={healthColor(kronosStatus?.health)} accentBar />
        <Stat label="OPEN UNDERLYINGS" value={stats.underlyings} sub={`${activeForecasts.length} instruments`} color={accent} />
        <Stat label="SPY TODAY" value={`${market.direction || "UNKNOWN"} ${signed(market.forecast_pct)}%`} sub={`cone ${signed(market.cone_low_pct)} to ${signed(market.cone_high_pct)}%`} color={marketColor(market.direction)} />
        <Stat label="P/L DAY CONE" value={fmtMoney(cone.base_usd)} sub={`${fmtMoney(cone.low_usd)} to ${fmtMoney(cone.high_usd)}`} color={Number(cone.base_usd || 0) >= 0 ? "#4ade80" : "#f87171"} />
        <Stat label="NET FORECAST" value={stats.netBias} sub={`${stats.bullish} bull / ${stats.bearish} bear`} color={biasColors[stats.netBias] || accent} />
        <Stat label="PM ALIGNED" value={`${stats.alignedPct}%`} sub={`${stats.aligned}/${activeForecasts.length || 0} positions`} color={stats.alignedPct >= 70 ? "#4ade80" : "#fbbf24"} />
        <Stat label="AT RISK" value={stats.atRisk} sub="tripwire flags" color={stats.atRisk ? "#f87171" : "#4ade80"} />
        <Stat label="OPTIONS DECAY" value={stats.thetaWatch} sub="theta watch" color={stats.thetaWatch ? "#fbbf24" : "#4ade80"} />
        <Stat label="MACRO TONE" value={macroTone.label} sub={macroTone.detail} color={macroTone.color} />
      </div>

      <div style={tabBar}>
        {["FORECAST", "SANDBOX", "CALENDAR", "PM DISAGREEMENTS", "FORECAST MEMORY"].map(k => (
          <button key={k} onClick={() => setTab(k)} style={tabButton(tab === k)}>{k}</button>
        ))}
      </div>

      {tab === "SANDBOX" && (
        <SandboxView
          draft={sandboxDraft}
          setDraft={setSandboxDraft}
          run={() => runSandbox(sandboxDraft)}
          loading={sandboxLoading}
          data={sandbox}
        />
      )}

      {tab === "CALENDAR" && (
        <KronosCalendarView
          data={calendar}
          loading={calendarLoading}
          month={calendarMonth}
          year={calendarYear}
          selected={selectedCalendarDay}
          setSelected={setSelectedCalendarDay}
          setMonth={(m) => {
            const next = Number(m);
            setCalendarMonth(next);
            loadCalendar(calendarYear, next);
          }}
          setYear={(y) => {
            const next = Number(y);
            setCalendarYear(next);
            loadCalendar(next, calendarMonth);
          }}
          refresh={() => loadCalendar(calendarYear, calendarMonth)}
        />
      )}

      {tab === "PM DISAGREEMENTS" && (
        <DisagreementView data={disagreements} liveRows={kronos?.disagreements || []} />
      )}

      {tab === "FORECAST MEMORY" && (
        <Card title="FORECAST MEMORY / MODEL ACCOUNTABILITY" accentColor={accent2}>
          <div style={memoryGrid}>
            <MemoryItem label="Latest Snapshot" value={kronosStatus?.health || "CHECKING"} detail={`Age: ${ageText(kronosStatus?.snapshot_age_minutes)}. Latest daily snapshot updates in place; full runs are retained separately.`} />
            <MemoryItem label="Disagreement Audits" value={(disagreements?.rows || []).length} detail="Each PM conflict is saved for future return review." />
            <MemoryItem label="PM Map" value={`${kronosStatus?.mapped_pm ?? 0}/${kronosStatus?.positions ?? 0}`} detail={`${kronosStatus?.unmapped_pm ?? 0} open instruments still unmapped to PM.`} />
            <MemoryItem label="Calendar Score" value={kronosStatus?.calendar?.scored_days ?? 0} detail={`Direction win ${kronosStatus?.calendar?.direction_win_rate_pct ?? "-"}% / cone win ${kronosStatus?.calendar?.cone_win_rate_pct ?? "-"}%.`} />
            <MemoryItem label="Candle Proof" value={kronosAccuracy?.overall?.sample ?? 0} detail={`Direction ${kronosAccuracy?.overall?.direction_win_rate_pct ?? "-"}% / cone ${kronosAccuracy?.overall?.cone_coverage_pct ?? "-"}% / MAE ${kronosAccuracy?.overall?.mae_pct ?? "-"}%.`} />
            <MemoryItem label="Morning Report" value="09:30" detail="SPY forecast plus open-position P/L cone dispatches to Telegram Mon-Fri." />
          </div>
          <KronosAccuracyPanel accuracy={kronosAccuracy} />
        </Card>
      )}

      {tab === "FORECAST" && (
        <KronosTerminalChart
          choice={chartChoice}
          choices={chartChoices}
          candles={chartCandles}
          candleSuite={candleSuite}
          forecast={chartForecast}
          loading={chartLoading}
          onSelect={setChartKey}
        />
      )}

      {tab === "FORECAST" && <KronosAccuracyPanel accuracy={kronosAccuracy} compact />}

      {tab === "FORECAST" && <div style={topGrid}>
        <Card title="KRONOS SELECTION MATRIX" accentColor="#a78bfa">
          <SelectionMatrix rows={activeForecasts} selectedKey={selected?.key} onSelect={setSelectedKey} />
        </Card>

        <Card title={selectedFull ? `SCENARIO FAN / ${selectedFull.ticker}` : "SCENARIO FAN"} accentColor={accent2}>
          {selectedFull ? <ScenarioFan selected={selectedFull} rows={scenario} /> : <EmptyState compact />}
        </Card>
      </div>}

      {tab === "FORECAST" && <div style={middleGrid}>
        <Card title={selectedFull ? `POSITION COMMAND CARD / ${selectedFull.ticker}` : "POSITION COMMAND CARD"} accentColor={accent}>
          {selectedFull ? <CommandCard item={selectedFull} context={selectedContext} /> : <EmptyState compact />}
        </Card>

        <Card title="PM ALIGNMENT RADAR" accentColor="#4ade80">
          <AlignmentRadar rows={radarRows} />
        </Card>
      </div>}

      {tab === "FORECAST" && <div style={bottomGrid}>
        <Card title="TRIPWIRE DECK" accentColor="#f87171">
          <TripwireDeck rows={tripwires} />
        </Card>

        <Card title="OPTIONS FLOW / SELECTED UNDERLYING" accentColor="#fbbf24">
          <OptionsFlow context={selectedContext} />
        </Card>
      </div>}

      <Card title="KRONOS MODEL AUDIT" accentColor={accent2}>
        <AuditGrid
          scan={scan}
          pm={pm}
          equityPositions={equityPositions}
          optionPositions={optionPositions}
          optionRisk={optionRisk}
          tracker={tracker}
          lseHealth={lseHealth}
          kronosStatus={kronosStatus}
        />
      </Card>
    </CrtShell>
  );
}

function KronosStatusPanel({ status, lseHealth, pm, lastSync }) {
  return (
    <>
      <div style={bootRow}><span>MODEL HEALTH</span><strong style={{ color: healthColor(status?.health) }}>{status?.health || "CHECKING"}</strong></div>
      <div style={bootRow}><span>SNAPSHOT AGE</span><strong>{ageText(status?.snapshot_age_minutes)}</strong></div>
      <div style={bootRow}><span>PM CONTEXT</span><strong style={{ color: (status?.unmapped_pm || 0) ? "#fbbf24" : "#4ade80" }}>{status?.pm_context_health || "CHECKING"} / {status?.mapped_pm ?? 0}/{status?.positions ?? 0}</strong></div>
      <div style={bootRow}><span>OPEN AUDITS</span><strong style={{ color: status?.open_disagreement_audits ? "#fbbf24" : "#4ade80" }}>{status?.open_disagreement_audits ?? 0}</strong></div>
      <div style={bootRow}><span>LSE DATA</span><strong style={{ color: lseHealth?.ok ? "#4ade80" : "#fbbf24" }}>{lseHealth?.ok ? "ONLINE" : "DEGRADED"}</strong></div>
      <div style={bootRow}><span>PM LINK</span><strong>{pm?.error ? "DEGRADED" : "READING"}</strong></div>
      <div style={bootRow}><span>LAST SYNC</span><strong>{fmtTime(lastSync)}</strong></div>
    </>
  );
}

function KronosAccuracyPanel({ accuracy, compact = false }) {
  const overall = accuracy?.overall || {};
  const tfRows = accuracy?.by_timeframe || [];
  const regimeRows = accuracy?.by_regime || [];
  const recent = accuracy?.recent || [];
  const ok = accuracy?.ok !== false;
  return (
    <div style={{ display: "grid", gap: 14, marginTop: compact ? 0 : 18 }}>
      <div style={proofGrid}>
        <Mini label="PROOF SAMPLE" value={overall.sample ?? 0} color={labelLight} />
        <Mini label="PENDING" value={overall.pending ?? accuracy?.pending ?? 0} color={(overall.pending || accuracy?.pending) ? "#fbbf24" : "#4ade80"} />
        <Mini label="DIR WIN" value={overall.direction_win_rate_pct == null ? "-" : `${overall.direction_win_rate_pct}%`} color={rateColor(overall.direction_win_rate_pct)} />
        <Mini label="CONE HIT" value={overall.cone_coverage_pct == null ? "-" : `${overall.cone_coverage_pct}%`} color={rateColor(overall.cone_coverage_pct)} />
        <Mini label="MAE" value={overall.mae_pct == null ? "-" : `${overall.mae_pct}%`} color={errorColor(overall.mae_pct)} />
        <Mini label="RMSE" value={overall.rmse_pct == null ? "-" : `${overall.rmse_pct}%`} color={errorColor(overall.rmse_pct)} />
      </div>
      {!ok && <div style={{ ...explainText, color: "#fbbf24" }}>Kronos proof is degraded: {accuracy?.error || "accuracy endpoint unavailable"}</div>}
      {!compact && (
        <div style={proofTables}>
          <ProofTable title="TIMEFRAME ACCURACY" rows={tfRows} />
          <ProofTable title="REGIME ACCURACY" rows={regimeRows} />
        </div>
      )}
      {!compact && recent.length ? (
        <div style={{ overflowX: "auto" }}>
          <table style={{ width: "100%", borderCollapse: "collapse", minWidth: 760 }}>
            <thead>
              <tr><th style={th}>TIME</th><th style={th}>SYMBOL</th><th style={th}>TF</th><th style={th}>CALL</th><th style={th}>FORECAST</th><th style={th}>ACTUAL</th><th style={th}>ERROR</th><th style={th}>REGIME</th></tr>
            </thead>
            <tbody>
              {recent.slice(0, 28).map((r, i) => (
                <tr key={`${r.generated_at}-${r.symbol}-${r.timeframe}-${i}`}>
                  <td style={td}>{fmtDate(r.generated_at)}</td>
                  <td style={{ ...td, color: accent, fontWeight: 900 }}>${r.symbol}</td>
                  <td style={td}>{r.timeframe}</td>
                  <td style={{ ...td, color: marketColor(r.direction) }}>{r.direction}</td>
                  <td style={td}>{fmtPct(r.forecast_pct)}</td>
                  <td style={{ ...td, color: pctColor(r.actual_pct) }}>{fmtPct(r.actual_pct)}</td>
                  <td style={{ ...td, color: errorColor(Math.abs(Number(r.error_pct || 0))) }}>{fmtPct(r.error_pct)}</td>
                  <td style={td}>{r.regime}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : null}
      <div style={explainText}>
        Proof scores compare each saved Kronos candle forecast to the next raw OHLCV candle. Pending forecasts are not counted as wins or losses.
      </div>
    </div>
  );
}

function ProofTable({ title, rows }) {
  return (
    <div style={stackPanel}>
      <div style={sectionLabel}>{title}</div>
      {!rows?.length ? <div style={emptySmall}>No mature forecast samples yet.</div> : rows.map(row => (
        <div key={row.key} style={proofRow}>
          <strong>{String(row.key || "-").toUpperCase()}</strong>
          <span>n={row.sample || 0}</span>
          <span style={{ color: rateColor(row.direction_win_rate_pct) }}>DIR {row.direction_win_rate_pct == null ? "-" : `${row.direction_win_rate_pct}%`}</span>
          <span style={{ color: rateColor(row.cone_coverage_pct) }}>CONE {row.cone_coverage_pct == null ? "-" : `${row.cone_coverage_pct}%`}</span>
          <span style={{ color: errorColor(row.mae_pct) }}>MAE {row.mae_pct == null ? "-" : `${row.mae_pct}%`}</span>
        </div>
      ))}
    </div>
  );
}

function SelectionMatrix({ rows, selectedKey, onSelect }) {
  if (!rows.length) return <EmptyState />;
  return (
    <div style={{ display: "grid", gap: 10 }}>
      {rows.map(row => (
        <button key={row.key} onClick={() => onSelect(row.key)} style={selectionCard(row, selectedKey === row.key)}>
          <div style={{ minWidth: 0 }}>
            <div style={{ color: row.color, fontSize: 18, fontWeight: 900, letterSpacing: "0.08em" }}>${row.ticker}</div>
            <div style={{ color: muted, fontSize: 10, letterSpacing: "0.1em", marginTop: 4, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
              {row.instrument}{row.contract ? ` / ${row.contract}` : ""} / {row.horizon}
            </div>
          </div>
          <div style={pill(row.color)}>{row.bias}</div>
          <div style={{ textAlign: "right" }}>
            <div style={{ color: labelLight, fontWeight: 900 }}>{row.confidence}%</div>
            <div style={{ color: dim, fontSize: 9, letterSpacing: "0.1em" }}>CONF</div>
          </div>
        </button>
      ))}
    </div>
  );
}

function KronosTerminalChart({ choice, choices, candles, candleSuite, forecast, loading, onSelect }) {
  const rows = buildTerminalChartRows(candles, forecast);
  const candleRows = candleSuite?.timeframes || [];
  const primary = candleSuite?.primary || candleRows.find(r => r?.ok);
  const color = forecast?.color || accent2;
  const last = rows.filter(r => r.actual != null).slice(-1)[0];
  return (
    <Card title="KRONOS COMMAND CENTER / CANDLE ENGINE" accentColor={color}>
      <div style={terminalChartLayout}>
        <div style={terminalChartBody}>
          <div style={chartHeaderRow}>
            <div>
              <div style={{ color, fontSize: 24, fontWeight: 900, letterSpacing: "0.08em" }}>${choice?.ticker || "SPY"}</div>
              <div style={{ color: muted, fontSize: 10, letterSpacing: "0.14em", marginTop: 5 }}>
                {choice?.label || "SPY MARKET FORECAST"} / RAW OHLCV CANDLE ENGINE / TRADINGVIEW VISUAL
              </div>
            </div>
            <div style={{ textAlign: "right" }}>
              <div style={{ color: labelLight, fontSize: 22, fontWeight: 900 }}>{last?.actual == null ? "-" : `$${Number(last.actual).toFixed(2)}`}</div>
              <div style={{ color: forecast?.basePct >= 0 ? "#4ade80" : "#f87171", fontSize: 11, letterSpacing: "0.12em" }}>
                CONE {signed(forecast?.lowPct)}% / {signed(forecast?.basePct)}% / {signed(forecast?.highPct)}%
              </div>
            </div>
          </div>
          <div style={kronosCommandGrid}>
            <div style={tradingViewShell}>
              <TradingViewMiniChart ticker={choice?.ticker || "SPY"} companyName={`${choice?.ticker || "SPY"} Kronos Candle View`} height={620} />
            </div>
            <div style={candlePredictionPanel}>
              <div style={sectionLabel}>KRONOS NEXT CANDLE</div>
              <div style={{ color: marketColor(primary?.direction), fontSize: 30, fontWeight: 900, letterSpacing: "0.08em", marginTop: 10 }}>
                {primary?.direction || "CHECKING"} {signed(primary?.forecast_pct)}%
              </div>
              <div style={probGrid}>
                <MiniProb label="UP" value={primary?.probabilities?.up} color="#4ade80" />
                <MiniProb label="DOWN" value={primary?.probabilities?.down} color="#f87171" />
                <MiniProb label="FLAT" value={primary?.probabilities?.flat} color="#fbbf24" />
              </div>
              <div style={ohlcGrid}>
                {["open", "high", "low", "close"].map(k => (
                  <div key={k} style={ohlcBox}>
                    <span>{k.toUpperCase()}</span>
                    <strong>{primary?.predicted_next_candle?.[k] == null ? "-" : Number(primary.predicted_next_candle[k]).toFixed(2)}</strong>
                  </div>
                ))}
              </div>
              <div style={candleFeatureStack}>
                <PlanRow k="Pattern" v={primary?.features?.last_candle_pattern || "-"} color={accent2} />
                <PlanRow k="Trend" v={primary?.features?.structure || "-"} color={primary?.features?.structure === "HIGHER_HIGH" ? "#4ade80" : primary?.features?.structure === "LOWER_LOW" ? "#f87171" : "#fbbf24"} />
                <PlanRow k="ATR / Noise" v={`${num(primary?.features?.atr_pct, 2)}% / ${num(primary?.noise_band_pct, 2)}%`} />
                <PlanRow k="RSI / VWAP" v={`${num(primary?.features?.rsi14, 1)} / ${signed(primary?.features?.vwap_distance_pct)}%`} />
                <PlanRow k="Volume Z" v={primary?.features?.volume_z ?? "-"} color={Number(primary?.features?.volume_z || 0) >= 1 ? "#4ade80" : muted} />
                <PlanRow k="Source" v={primary?.provider || "raw_ohlcv"} color={primary?.degraded ? "#fbbf24" : "#4ade80"} />
              </div>
            </div>
          </div>
          <CandleHorizonTable rows={candleRows} />
          <div style={chartBox(430)}>
            {loading ? (
              <div style={loadingText}>LOADING LSE PRICE TAPE...</div>
            ) : rows.length ? (
              <ResponsiveContainer width="100%" height="100%">
                <LineChart data={rows} margin={{ top: 12, right: 18, left: 0, bottom: 0 }}>
                  <defs>
                    <linearGradient id="kronosConeFill" x1="0" y1="0" x2="0" y2="1">
                      <stop offset="0%" stopColor={color} stopOpacity={0.24} />
                      <stop offset="100%" stopColor={color} stopOpacity={0.03} />
                    </linearGradient>
                  </defs>
                  <CartesianGrid stroke="rgba(255,255,255,0.055)" vertical={false} />
                  <XAxis dataKey="label" stroke={dim} tick={{ fill: muted, fontSize: 10 }} axisLine={false} tickLine={false} minTickGap={22} />
                  <YAxis stroke={dim} tick={{ fill: muted, fontSize: 10 }} axisLine={false} tickLine={false} domain={["auto", "auto"]} tickFormatter={v => `$${Number(v).toFixed(0)}`} />
                  <Tooltip contentStyle={tooltipStyle} formatter={(v, name) => [Array.isArray(v) ? v.map(x => `$${Number(x).toFixed(2)}`).join(" - ") : `$${Number(v).toFixed(2)}`, name]} />
                  <ReferenceLine x="NOW" stroke={accent} strokeDasharray="4 4" />
                  <Area type="monotone" dataKey="coneRange" name="cone" fill="url(#kronosConeFill)" stroke="transparent" connectNulls dot={false} />
                  <Line type="monotone" dataKey="actual" name="actual" stroke={labelLight} strokeWidth={2.2} dot={false} connectNulls />
                  <Line type="monotone" dataKey="base" name="kronos base" stroke={color} strokeWidth={3} dot={false} connectNulls />
                  <Line type="monotone" dataKey="high" name="bull edge" stroke="#4ade80" strokeWidth={1.5} strokeDasharray="5 5" dot={false} connectNulls />
                  <Line type="monotone" dataKey="low" name="bear edge" stroke="#f87171" strokeWidth={1.5} strokeDasharray="5 5" dot={false} connectNulls />
                </LineChart>
              </ResponsiveContainer>
            ) : (
              <div style={loadingText}>NO LSE CANDLE HISTORY RETURNED FOR THIS SYMBOL.</div>
            )}
          </div>
          <div style={explainText}>
            TradingView is the human chart. Kronos analyzes raw OHLCV candles underneath it, then keeps the portfolio forecast engine separate from candle prediction.
          </div>
        </div>
        <div style={chartSelectorRail}>
          <div style={sectionLabel}>CHART MENU</div>
          {choices.map(c => (
            <button key={c.key} onClick={() => onSelect(c.key)} style={chartChoiceButton(choice?.key === c.key, c.color)}>
              <strong>${c.ticker}</strong>
              <span>{c.type}</span>
            </button>
          ))}
        </div>
      </div>
    </Card>
  );
}

function SandboxView({ draft, setDraft, run, loading, data }) {
  const candles = normalizeRows(data?.candles).map(normalizeCandle).filter(Boolean);
  const flow = normalizeRows(data?.flow);
  const stats = sandboxStats(candles, flow);
  const macroTone = inferMacroTone(data?.macro);
  return (
    <div style={{ display: "grid", gap: 22 }}>
      <Card title="LSE SANDBOX / READ-ONLY TEST BENCH" accentColor="#a78bfa">
        <div style={sandboxControlRow}>
          <div>
            <div style={{ color: accent2, fontSize: 10, letterSpacing: "0.16em", fontWeight: 900 }}>SYMBOL</div>
            <input
              value={draft}
              onChange={e => setDraft(e.target.value.toUpperCase())}
              onKeyDown={e => { if (e.key === "Enter") run(); }}
              style={sandboxInput}
              placeholder="SPY"
            />
          </div>
          <button onClick={run} disabled={loading} style={buttonStyle("#a78bfa")}>{loading ? "PULLING LSE" : "RUN LSE MODEL"}</button>
          <div style={{ color: muted, fontSize: 11, lineHeight: 1.55 }}>
            Sandbox uses LSE profile, candles, options flow, and macro only. No PM decision, no order routing, no trade journal writes.
          </div>
        </div>
      </Card>

      <div style={sandboxGrid}>
        <Card title={`SANDBOX CHART / ${data?.ticker || "SPY"}`} accentColor={accent2}>
          <div style={chartBox(360)}>
            {candles.length ? (
              <ResponsiveContainer width="100%" height="100%">
                <AreaChart data={candles.slice(-90)} margin={{ top: 10, right: 14, left: 0, bottom: 0 }}>
                  <defs>
                    <linearGradient id="sandboxTape" x1="0" y1="0" x2="0" y2="1">
                      <stop offset="0%" stopColor={accent2} stopOpacity={0.36} />
                      <stop offset="100%" stopColor={accent2} stopOpacity={0.02} />
                    </linearGradient>
                  </defs>
                  <CartesianGrid stroke="rgba(255,255,255,0.045)" vertical={false} />
                  <XAxis dataKey="label" stroke={dim} tick={{ fill: muted, fontSize: 10 }} axisLine={false} tickLine={false} minTickGap={22} />
                  <YAxis stroke={dim} tick={{ fill: muted, fontSize: 10 }} axisLine={false} tickLine={false} domain={["auto", "auto"]} tickFormatter={v => `$${Number(v).toFixed(0)}`} />
                  <Tooltip contentStyle={tooltipStyle} formatter={(v) => [`$${Number(v).toFixed(2)}`, "close"]} />
                  <Area type="monotone" dataKey="close" stroke={accent2} fill="url(#sandboxTape)" strokeWidth={2.5} dot={false} />
                </AreaChart>
              </ResponsiveContainer>
            ) : <div style={loadingText}>{loading ? "LOADING LSE SANDBOX..." : "NO LSE SANDBOX CANDLES RETURNED."}</div>}
          </div>
        </Card>

        <Card title="SANDBOX READOUT" accentColor={accent}>
          <div style={miniGrid}>
            <Mini label="LAST CLOSE" value={stats.last == null ? "-" : `$${stats.last.toFixed(2)}`} color={labelLight} />
            <Mini label="20D MOVE" value={fmtPct(stats.move20)} color={pctColor(stats.move20)} />
            <Mini label="90D RANGE" value={stats.range == null ? "-" : `${stats.range.toFixed(1)}%`} color={accent2} />
            <Mini label="FLOW ROWS" value={flow.length} color={flow.length ? "#fbbf24" : muted} />
            <Mini label="MACRO" value={macroTone.label} color={macroTone.color} />
            <Mini label="SYNC" value={fmtTime(data?.syncedAt)} color={accent} />
          </div>
          <div style={commandRows}>
            <PlanRow k="Profile" v={data?.profile?.error ? "LSE profile degraded" : "LSE profile linked"} color={data?.profile?.error ? "#fbbf24" : "#4ade80"} />
            <PlanRow k="Tape Source" v="London Strategic Edge candles" color={accent2} />
            <PlanRow k="Options Source" v={flow.length ? "LSE options flow linked" : "No LSE flow rows returned"} color={flow.length ? "#fbbf24" : muted} />
            <PlanRow k="Sandbox Use" v="Idea testing and forecast comparison only" color="#a78bfa" />
          </div>
        </Card>
      </div>
    </div>
  );
}

function KronosCalendarView({ data, loading, month, year, selected, setSelected, setMonth, setYear, refresh }) {
  const years = data?.available_years?.length ? data.available_years : [year, year - 1, year - 2];
  const cells = buildCalendarCells(year, month, data?.days || []);
  const summary = calendarSummary(data?.days || []);
  const apiSummary = data?.summary || {};
  const weeks = calendarWeekSummary(cells);
  return (
    <div style={{ display: "grid", gap: 22 }}>
      <Card title="KRONOS CALENDAR / PREDICTION ACCOUNTABILITY" accentColor={accent2}>
        <div style={calendarShellHeader}>
          <div style={{ display: "flex", gap: 10, flexWrap: "wrap" }}>
            <select value={month} onChange={e => setMonth(e.target.value)} style={selectStyle}>
              {Array.from({ length: 12 }).map((_, i) => <option key={i + 1} value={i + 1}>{monthName(i + 1)}</option>)}
            </select>
            <select value={year} onChange={e => setYear(e.target.value)} style={selectStyle}>
              {years.map(y => <option key={y} value={y}>{y}</option>)}
            </select>
            <button onClick={refresh} disabled={loading} style={buttonStyle(accent2)}>{loading ? "LOADING" : "REFRESH MONTH"}</button>
          </div>
          <button onClick={() => { setMonth(new Date().getMonth() + 1); setYear(new Date().getFullYear()); }} style={calendarMonthButton}>THIS MONTH</button>
        </div>
        <div style={calendarHeroStats}>
          <div style={calendarHeroTile("#4ade80")}><span>Good Days</span><strong>{summary.good}</strong><small>prediction wins</small></div>
          <div style={calendarHeroTile("#f87171")}><span>Bad Days</span><strong>{summary.bad}</strong><small>misses</small></div>
          <div style={calendarHeroTile("#fbbf24")}><span>Hit Rate</span><strong>{summary.hitRate}%</strong><small>{summary.watch} watch/pending</small></div>
          <div style={calendarHeroTile(accent2)}><span>Directional</span><strong>{apiSummary.direction_win_rate_pct == null ? "-" : `${apiSummary.direction_win_rate_pct}%`}</strong><small>UP/DOWN win</small></div>
          <div style={calendarHeroTile("#a78bfa")}><span>Cone</span><strong>{apiSummary.cone_win_rate_pct == null ? "-" : `${apiSummary.cone_win_rate_pct}%`}</strong><small>coverage</small></div>
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
                  onClick={() => cell.day && setSelected(cell.day)}
                  title={calendarTitle(cell.day)}
                  style={calendarCell(cell, selected?.date === cell.date)}
                >
                  <span style={calendarDayNumber}>{cell.dayNumber || ""}</span>
                  {cell.day?.has_prediction && (
                    <span style={calendarDayPayload}>
                      <strong>{fmtPct(cell.day.spy_actual_pct)}</strong>
                      <small>{cell.day.status || "WATCH"}</small>
                      <small>{cell.day.direction_win == null ? "pending" : cell.day.direction_win ? "direction win" : "direction miss"}</small>
                    </span>
                  )}
                </button>
              ))}
            </div>
          </div>
          <WeekRail weeks={weeks} mode="kronos" />
        </div>
      </Card>

      <Card title={selected ? `SELECTED DAY / ${selected.date}` : "SELECTED DAY"} accentColor={calendarStatusColor(selected?.status)}>
        {selected ? <CalendarDayDetail day={selected} /> : <div style={{ color: muted, padding: 20 }}>Select a day to inspect the prediction cone and actual result.</div>}
      </Card>
    </div>
  );
}

function CalendarDayDetail({ day }) {
  const rows = buildCalendarDetailChart(day);
  return (
    <div style={calendarDetailGrid}>
      <div>
        <div style={miniGrid}>
          <Mini label="VERDICT" value={day.status || "PENDING"} color={calendarStatusColor(day.status)} />
          <Mini label="SPY PRED" value={fmtPct(day.spy_prediction_pct)} color={pctColor(day.spy_prediction_pct)} />
          <Mini label="SPY ACTUAL" value={fmtPct(day.spy_actual_pct)} color={pctColor(day.spy_actual_pct)} />
          <Mini label="FUND ACTUAL" value={fmtPct(day.fund_actual_pct)} color={pctColor(day.fund_actual_pct)} />
        </div>
        <PlanRow k="SPY CONE" v={`${fmtPct(day.spy_cone_low_pct)} / ${fmtPct(day.spy_prediction_pct)} / ${fmtPct(day.spy_cone_high_pct)}`} color={accent2} />
        <PlanRow k="FUND CONE" v={`${fmtMoney(day.fund_cone_low_usd)} / ${fmtMoney(day.fund_prediction_usd)} / ${fmtMoney(day.fund_cone_high_usd)}`} color={accent} />
        <PlanRow k="RELATIVE VS SPY" v={fmtPct(day.relative_pct)} color={pctColor(day.relative_pct)} />
        <PlanRow k="SNAPSHOT" v={fmtDate(day.snapshot_at)} />
      </div>
      <div style={chartBox(320)}>
        <ResponsiveContainer width="100%" height="100%">
          <LineChart data={rows} margin={{ top: 12, right: 12, left: 0, bottom: 0 }}>
            <CartesianGrid stroke="rgba(255,255,255,0.055)" vertical={false} />
            <XAxis dataKey="label" stroke={dim} tick={{ fill: muted, fontSize: 10 }} axisLine={false} tickLine={false} />
            <YAxis stroke={dim} tick={{ fill: muted, fontSize: 10 }} axisLine={false} tickLine={false} tickFormatter={v => `${v}%`} />
            <Tooltip contentStyle={tooltipStyle} formatter={(v, name) => [`${Number(v).toFixed(2)}%`, name]} />
            <ReferenceLine y={0} stroke={dim} strokeDasharray="3 3" />
            <Line type="monotone" dataKey="low" name="cone low" stroke="#f87171" strokeWidth={1.5} strokeDasharray="5 5" dot={false} />
            <Line type="monotone" dataKey="base" name="forecast" stroke={accent2} strokeWidth={3} dot={false} />
            <Line type="monotone" dataKey="high" name="cone high" stroke="#4ade80" strokeWidth={1.5} strokeDasharray="5 5" dot={false} />
            <Line type="monotone" dataKey="actual" name="actual" stroke={labelLight} strokeWidth={2.5} dot />
          </LineChart>
        </ResponsiveContainer>
      </div>
    </div>
  );
}

function WeekRail({ weeks }) {
  return (
    <div style={calendarWeekRail}>
      {weeks.map((week, idx) => (
        <button key={idx} style={calendarWeekCard(calendarWeekColor(week))} title={`Week ${idx + 1}\nGood: ${week.good}\nBad: ${week.bad}\nWatch: ${week.watch}`}>
          <span>Week {idx + 1}</span>
          <strong>{week.good - week.bad >= 0 ? "+" : ""}{week.good - week.bad}</strong>
          <small>{week.days} days</small>
        </button>
      ))}
    </div>
  );
}

function ScenarioFan({ selected, rows }) {
  return (
    <div>
      <div style={miniGrid}>
        <Mini label="BASE CASE" value={`${selected.baseMove >= 0 ? "+" : ""}${selected.baseMove.toFixed(1)}%`} color={selected.color} />
        <Mini label="BEAR FLOOR" value={`${selected.bearMove.toFixed(1)}%`} color="#f87171" />
        <Mini label="BULL CEILING" value={`+${selected.bullMove.toFixed(1)}%`} color="#4ade80" />
        <Mini label="EDGE SCORE" value={selected.edgeScore} color={accent2} />
      </div>
      <div style={chartBox(330)}>
        <ResponsiveContainer width="100%" height="100%">
          <LineChart data={rows} margin={{ top: 12, right: 12, left: 0, bottom: 0 }}>
            <CartesianGrid stroke="rgba(255,255,255,0.055)" vertical={false} />
            <XAxis dataKey="step" stroke={dim} tick={{ fill: muted, fontSize: 10 }} axisLine={false} tickLine={false} />
            <YAxis stroke={dim} tick={{ fill: muted, fontSize: 10 }} axisLine={false} tickLine={false} tickFormatter={v => `${v}%`} />
            <Tooltip contentStyle={tooltipStyle} formatter={(v, name) => [`${Number(v).toFixed(1)}%`, name]} />
            <ReferenceLine y={0} stroke={dim} strokeDasharray="3 3" />
            <Line type="monotone" dataKey="bull" name="bull" stroke="#4ade80" strokeWidth={2} dot={false} />
            <Line type="monotone" dataKey="base" name="base" stroke={selected.color} strokeWidth={3} dot={false} />
            <Line type="monotone" dataKey="bear" name="bear" stroke="#f87171" strokeWidth={2} dot={false} />
          </LineChart>
        </ResponsiveContainer>
      </div>
      <div style={explainText}>{selected.note}</div>
    </div>
  );
}

function CommandCard({ item, context }) {
  const candles = normalizeRows(context?.candles).slice(-40);
  const spark = candles.length ? candles.map((r, i) => ({
    i,
    close: numberish(r.close ?? r.c ?? r.price ?? r.value),
  })).filter(r => r.close != null) : item.path.map((p, i) => ({ i, close: 100 + p.value }));

  return (
    <div>
      <div style={miniGrid}>
        <Mini label="PM ROUTE" value={item.pmAction} color={routeColor(item.pmAction)} />
        <Mini label="CAPITAL" value={fmtMoney(item.marketValue)} color={labelLight} />
        <Mini label="UNREALIZED" value={fmtPct(item.unrealizedPct)} color={pctColor(item.unrealizedPct)} />
        <Mini label="RISK STATE" value={item.tripwires.length ? "WATCH" : "CLEAR"} color={item.tripwires.length ? "#fbbf24" : "#4ade80"} />
      </div>
      <div style={chartBox(220)}>
        <ResponsiveContainer width="100%" height="100%">
          <AreaChart data={spark}>
            <defs>
              <linearGradient id={`kronosSpark-${item.key}`} x1="0" y1="0" x2="0" y2="1">
                <stop offset="0%" stopColor={item.color} stopOpacity={0.42} />
                <stop offset="100%" stopColor={item.color} stopOpacity={0.02} />
              </linearGradient>
            </defs>
            <CartesianGrid stroke="rgba(255,255,255,0.045)" vertical={false} />
            <XAxis dataKey="i" hide />
            <YAxis hide domain={["dataMin", "dataMax"]} />
            <Tooltip contentStyle={tooltipStyle} formatter={(v) => [Number(v).toFixed(2), "price/proxy"]} />
            <Area type="monotone" dataKey="close" stroke={item.color} fill={`url(#kronosSpark-${item.key})`} strokeWidth={2} dot={false} />
          </AreaChart>
        </ResponsiveContainer>
      </div>
      <div style={commandRows}>
        <PlanRow k="Kronos Read" v={item.bias} color={item.color} />
        <PlanRow k="PM Agreement" v={item.aligned ? "ALIGNED" : "CONFLICT / WATCH"} color={item.aligned ? "#4ade80" : "#fbbf24"} />
        <PlanRow k="Known Catalysts" v={item.catalysts.length ? item.catalysts.join(", ") : "No mapped catalyst"} />
        <PlanRow k="Data Context" v={context?.loading ? "Loading LSE context" : context?.profile?.error ? "LSE profile degraded" : "LSE profile linked"} color={context?.profile?.error ? "#fbbf24" : accent2} />
      </div>
      <ForecastStack item={item} />
    </div>
  );
}

function MarketForecastBand({ market, cone }) {
  return (
      <div style={marketBand}>
      <div style={marketCell}>
        <span>SPY TODAY</span>
        <strong style={{ color: marketColor(market.direction) }}>{market.direction || "UNKNOWN"} {signed(market.forecast_pct)}%</strong>
      </div>
      <div style={marketCell}>
        <span>SPY CONE</span>
        <strong>{signed(market.cone_low_pct)}% / {signed(market.cone_high_pct)}%</strong>
      </div>
      <div style={marketCell}>
        <span>OPEN P/L CONE</span>
        <strong style={{ color: Number(cone.base_usd || 0) >= 0 ? "#4ade80" : "#f87171" }}>
          {fmtMoney(cone.low_usd)} / {fmtMoney(cone.base_usd)} / {fmtMoney(cone.high_usd)}
        </strong>
      </div>
      <div style={marketCell}>
        <span>TELEGRAM</span>
        <strong>09:30 ET</strong>
      </div>
    </div>
  );
}

function ForecastStack({ item }) {
  const attribution = item.attribution || [];
  const horizons = item.horizons || {};
  const probs = item.probabilities || {};
  const exit = item.exit_forecast || item.exitForecast || {};
  return (
    <div style={{ marginTop: 16, display: "grid", gap: 14 }}>
      <div style={stackGrid}>
        <div style={stackPanel}>
          <div style={sectionLabel}>FORECAST ATTRIBUTION</div>
          {attribution.length ? attribution.map(a => (
            <div key={a.factor} style={barRow}>
              <span>{a.factor}</span>
              <div style={barTrack}><div style={{ ...barFill, width: `${Math.max(4, Number(a.weight || 0))}%` }} /></div>
              <strong>{a.state}</strong>
            </div>
          )) : <div style={emptySmall}>No attribution payload yet.</div>}
        </div>
        <div style={stackPanel}>
          <div style={sectionLabel}>PATH PROBABILITIES</div>
          {[
            ["+5%", probs.plus_5],
            ["+10%", probs.plus_10],
            ["-5%", probs.minus_5],
            ["-10%", probs.minus_10],
            ["STOP", probs.stop_hit],
            ["RATCHET", probs.ratchet_hit],
          ].map(([k, v]) => (
            <div key={k} style={probRow}>
              <span>{k}</span>
              <strong style={{ color: Number(v || 0) >= 50 ? "#4ade80" : Number(v || 0) >= 30 ? "#fbbf24" : muted }}>{v == null ? "-" : `${v}%`}</strong>
            </div>
          ))}
        </div>
      </div>
      <div style={stackGrid}>
        <div style={stackPanel}>
          <div style={sectionLabel}>MULTI-HORIZON FORECAST</div>
          {Object.entries(horizons).map(([k, h]) => (
            <div key={k} style={horizonRow}>
              <span>{k}</span>
              <strong>{signed(h.low_pct)}% / {signed(h.base_pct)}% / {signed(h.high_pct)}%</strong>
            </div>
          ))}
        </div>
        <div style={stackPanel}>
          <div style={sectionLabel}>EXIT FORECAST</div>
          <PlanRow k="Style" v={exit.style || "ADVISORY"} color={accent2} />
          <PlanRow k="Hard Stop" v={exit.hard_stop_pct == null ? "-" : `${exit.hard_stop_pct}%`} color="#f87171" />
          {(exit.tiers || []).slice(0, 6).map(t => (
            <PlanRow key={t.trigger_pct} k={`+${t.trigger_pct}%`} v={`lock +${t.locked_floor_pct}% / ${t.probability}%`} color="#fbbf24" />
          ))}
        </div>
      </div>
    </div>
  );
}

function DisagreementView({ data, liveRows }) {
  const rows = data?.rows || [];
  const summary = data?.summary || [];
  return (
    <div>
      <div style={memoryGrid}>
        <MemoryItem label="Live Conflicts" value={liveRows.length} detail="Current Kronos vs PM disagreements." />
        <MemoryItem label="Saved Audits" value={rows.length} detail="Stored disagreement snapshots." />
        <MemoryItem label="Setups" value={summary.length} detail="Grouped disagreement patterns." />
        <MemoryItem label="Performance" value="TRACKING" detail="Resolved when future returns mature." />
      </div>
      <Card title="PM DISAGREEMENT PERFORMANCE LEDGER" accentColor="#fbbf24">
        {!rows.length ? (
          <div style={{ color: muted, padding: 20 }}>No Kronos/PM disagreement snapshots have been recorded yet.</div>
        ) : (
          <div style={{ overflowX: "auto" }}>
            <table style={{ width: "100%", borderCollapse: "collapse", minWidth: 760 }}>
              <thead>
                <tr><th style={th}>TIME</th><th style={th}>TICKER</th><th style={th}>TYPE</th><th style={th}>PM</th><th style={th}>KRONOS</th><th style={th}>SCORE</th><th style={th}>STATUS</th></tr>
              </thead>
              <tbody>
                {rows.slice(0, 80).map((r, i) => (
                  <tr key={`${r.generated_at}-${r.ticker}-${i}`}>
                    <td style={td}>{fmtDate(r.generated_at)}</td>
                    <td style={{ ...td, color: accent, fontWeight: 900 }}>${r.ticker}</td>
                    <td style={td}>{r.instrument}{r.contract ? <div style={{ color: muted, fontSize: 10 }}>{r.contract}</div> : null}</td>
                    <td style={{ ...td, color: routeColor(r.pm_action) }}>{r.pm_action}</td>
                    <td style={{ ...td, color: biasColors[r.forecast_bias] || labelLight }}>{r.forecast_bias}</td>
                    <td style={td}>{r.kronos_score}</td>
                    <td style={{ ...td, color: "#fbbf24" }}>{r.status || "OPEN_AUDIT"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Card>
    </div>
  );
}

function MemoryItem({ label, value, detail }) {
  return (
    <div style={{ border: hairline, background: "rgba(255,255,255,0.018)", padding: 14 }}>
      <div style={{ color: dim, fontSize: 9, letterSpacing: "0.14em", marginBottom: 8 }}>{label}</div>
      <div style={{ color: accent, fontSize: 20, fontWeight: 900, letterSpacing: "0.08em" }}>{value}</div>
      <div style={{ color: muted, fontSize: 11, lineHeight: 1.5, marginTop: 8 }}>{detail}</div>
    </div>
  );
}

function AlignmentRadar({ rows }) {
  if (!rows.length) return <EmptyState compact />;
  return (
    <div>
      <div style={chartBox(250)}>
        <ResponsiveContainer width="100%" height="100%">
          <BarChart data={rows} margin={{ top: 10, right: 10, left: -15, bottom: 0 }}>
            <CartesianGrid stroke="rgba(255,255,255,0.05)" vertical={false} />
            <XAxis dataKey="ticker" stroke={dim} tick={{ fill: muted, fontSize: 10 }} axisLine={false} tickLine={false} />
            <YAxis stroke={dim} tick={{ fill: muted, fontSize: 10 }} axisLine={false} tickLine={false} domain={[0, 100]} />
            <Tooltip contentStyle={tooltipStyle} formatter={(v) => [`${v}`, "alignment"]} />
            <Bar dataKey="alignment" radius={[2, 2, 0, 0]}>
              {rows.map((r) => <Cell key={r.ticker} fill={r.color} />)}
            </Bar>
          </BarChart>
        </ResponsiveContainer>
      </div>
      <div style={explainText}>
        Alignment compares PM route, Case Score, current P/L pressure, and instrument type. Low alignment is not an order signal; it is a review flag.
      </div>
    </div>
  );
}

function TripwireDeck({ rows }) {
  if (!rows.length) {
    return <div style={{ color: "#4ade80", padding: 22, fontSize: 13, letterSpacing: "0.08em" }}>NO ACTIVE TRIPWIRES DETECTED.</div>;
  }
  return (
    <div style={{ overflowX: "auto" }}>
      <table style={{ width: "100%", borderCollapse: "collapse", minWidth: 680 }}>
        <thead>
          <tr><th style={th}>TICKER</th><th style={th}>TYPE</th><th style={th}>FLAGS</th><th style={th}>PM</th><th style={th}>P/L</th></tr>
        </thead>
        <tbody>
          {rows.map(r => (
            <tr key={`tw-${r.key}`}>
              <td style={{ ...td, color: accent, fontWeight: 900 }}>${r.ticker}</td>
              <td style={td}>{r.instrument}</td>
              <td style={{ ...td, color: "#fbbf24" }}>{r.tripwires.join(" / ")}</td>
              <td style={{ ...td, color: routeColor(r.pmAction) }}>{r.pmAction}</td>
              <td style={{ ...td, color: pctColor(r.unrealizedPct) }}>{fmtPct(r.unrealizedPct)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function OptionsFlow({ context }) {
  const rows = normalizeRows(context?.flow).slice(0, 8);
  if (context?.loading) return <div style={{ color: muted, padding: 20 }}>Loading selected ticker options flow...</div>;
  if (!rows.length) return <div style={{ color: muted, padding: 20 }}>No selected-ticker options flow available from the current data source.</div>;
  return (
    <div style={{ display: "grid", gap: 8 }}>
      {rows.map((r, i) => {
        const premium = numberish(r.premium || r.total_premium || r.notional || r.value);
        const side = String(r.side || r.sentiment || r.option_type || r.type || "FLOW").toUpperCase();
        const color = side.includes("CALL") || side.includes("BULL") ? "#4ade80" : side.includes("PUT") || side.includes("BEAR") ? "#f87171" : accent;
        return (
          <div key={i} style={flowRow(color)}>
            <span style={{ color, fontWeight: 900 }}>{side}</span>
            <span>{r.contract || r.symbol || r.ticker || context?.ticker}</span>
            <strong>{premium == null ? "-" : fmtMoney(premium)}</strong>
          </div>
        );
      })}
    </div>
  );
}

function AuditGrid({ scan, pm, equityPositions, optionPositions, optionRisk, tracker, lseHealth, kronosStatus }) {
  const items = [
    auditItem("Kronos Snapshot", kronosStatus?.health || "CHECKING", healthColor(kronosStatus?.health)),
    auditItem("Kronos PM Map", `${kronosStatus?.mapped_pm ?? 0}/${kronosStatus?.positions ?? 0}`, (kronosStatus?.unmapped_pm || 0) ? "#fbbf24" : "#4ade80"),
    auditItem("Latest Scan", scan?.error ? "DEGRADED" : `${scan?.results?.length || 0} rows`, scan?.error ? "#fbbf24" : "#4ade80"),
    auditItem("Portfolio Manager", pm?.error ? "DEGRADED" : "CONNECTED", pm?.error ? "#fbbf24" : "#4ade80"),
    auditItem("Equity Positions", equityPositions?.error ? "DEGRADED" : `${normalizeEquityPositions(equityPositions).length} rows`, equityPositions?.error ? "#fbbf24" : accent2),
    auditItem("Options Positions", optionPositions?.error ? "DEGRADED" : `${optionPositions?.positions?.length || 0} rows`, optionPositions?.error ? "#fbbf24" : "#a78bfa"),
    auditItem("Options Risk", optionRisk?.error ? "DEGRADED" : `${optionRisk?.checks?.length || 0} checks`, optionRisk?.error ? "#fbbf24" : "#4ade80"),
    auditItem("P/L Memory", tracker?.error ? "DEGRADED" : `${tracker?.rows?.length || 0} rows`, tracker?.error ? "#fbbf24" : accent),
    auditItem("LSE Feed", lseHealth?.ok ? "ONLINE" : "DEGRADED", lseHealth?.ok ? "#4ade80" : "#fbbf24"),
  ];
  return (
    <div style={auditGrid}>
      {items.map(i => (
        <div key={i.label} style={{ border: hairline, padding: 14, background: "rgba(255,255,255,0.018)" }}>
          <div style={{ color: dim, fontSize: 9, letterSpacing: "0.14em", marginBottom: 8 }}>{i.label}</div>
          <div style={{ color: i.color, fontSize: 15, letterSpacing: "0.08em", fontWeight: 900 }}>{i.value}</div>
        </div>
      ))}
    </div>
  );
}

function normalizeBackendForecasts(kronos) {
  return (kronos?.forecasts || []).map((r) => {
    const ticker = normalizeTicker(r.ticker || r.symbol);
    const instrument = String(r.instrument || "EQUITY").toUpperCase();
    const bias = String(r.forecast_bias || r.bias || "CHOP").toUpperCase();
    const color = biasColors[bias] || accent;
    const baseMove = numberish(r.forecast_pct) ?? 0;
    const bearMove = numberish(r.bear_pct) ?? baseMove - 3;
    const bullMove = numberish(r.bull_pct) ?? baseMove + 5;
    const key = `${instrument}:${r.contract || ticker}`;
    return {
      ...r,
      key,
      ticker,
      instrument,
      contract: r.contract || null,
      score: numberish(r.case_score),
      pmAction: String(r.pm_action || "UNMAPPED").toUpperCase(),
      bias,
      color,
      confidence: numberish(r.confidence) ?? 0,
      aligned: Boolean(r.aligned_with_pm),
      horizon: instrument === "OPTION" ? "1-10 trading days" : "5-20 trading days",
      catalysts: r.catalysts || [],
      tripwires: r.tripwires || [],
      marketValue: numberish(r.market_value),
      unrealizedPct: normalizePct(r.unrealized_pct),
      baseMove,
      bearMove,
      bullMove,
      edgeScore: numberish(r.kronos_score) ?? 0,
      note: `Backend Kronos forecast from ${r.pm_action || "unmapped PM"} route, current exposure, scan evidence, and risk state.`,
      path: buildPath(baseMove),
    };
  }).filter(r => r.ticker).sort((a, b) => (b.edgeScore || 0) - (a.edgeScore || 0));
}

function buildForecasts({ scan, pm, equityPositions, optionPositions, optionRisk, optionTrades, tracker, macro }) {
  const scanRows = scan?.results || [];
  const scanByTicker = new Map(scanRows.map(r => [normalizeTicker(r.ticker || r.symbol), r]));
  const pmRows = normalizePmRows(pm);
  const pmByTicker = new Map(pmRows.map(r => [normalizeTicker(r.ticker || r.symbol), r]));
  const perfByTicker = new Map((tracker?.rows || []).map(r => [normalizeTicker(r.ticker || r.symbol), r]));
  const riskBySymbol = new Map((optionRisk?.checks || []).map(r => [String(r.symbol || "").toUpperCase(), r]));
  const tradeBySymbol = new Map((optionTrades?.trades || []).map(r => [String(r.symbol || "").toUpperCase(), r]));

  const equity = normalizeEquityPositions(equityPositions).map(p => ({
    ticker: normalizeTicker(p.ticker || p.symbol),
    instrument: "EQUITY",
    quantity: p.qty || p.quantity || p.shares || "-",
    marketValue: numberish(p.market_value || p.marketValue || p.notional),
    unrealizedPct: normalizePct(p.unrealized_plpc ?? p.unrealized_pct ?? p.unrealizedPnlPct),
    raw: p,
  }));

  const options = (optionPositions?.positions || []).map(p => {
    const symbol = String(p.symbol || "").toUpperCase();
    const risk = riskBySymbol.get(symbol) || {};
    const trade = tradeBySymbol.get(symbol) || {};
    return {
      ticker: normalizeTicker(p.underlying_symbol || p.underlying || trade.ticker || inferUnderlying(symbol)),
      instrument: "OPTION",
      contract: symbol,
      quantity: p.qty || p.quantity || "-",
      marketValue: numberish(p.market_value || p.marketValue || p.cost_basis),
      unrealizedPct: normalizePct(risk.pnl_pct ?? p.unrealized_plpc ?? p.unrealized_pct ?? p.unrealizedPnlPct),
      risk,
      trade,
      raw: p,
    };
  });

  return [...equity, ...options].filter(p => p.ticker).map((p) => {
    const signal = scanByTicker.get(p.ticker) || {};
    const decision = pmByTicker.get(p.ticker) || {};
    const perf = perfByTicker.get(p.ticker) || {};
    const rawScore = numberish(signal.trade_score ?? signal.signal_score ?? signal.case_score ?? signal.score ?? decision.pm_score ?? decision.score);
    const score = rawScore != null && rawScore > 10 ? rawScore / 10 : rawScore;
    const pmAction = String(decision.action || decision.route || decision.decision || signal.pm_action || signal.pm_route || "").toUpperCase() || "UNMAPPED";
    const catalysts = catalystTags(signal, perf, macro);
    const bias = forecastBias(score, pmAction, p.unrealizedPct, p.instrument, p.risk);
    const conf = confidence(score, decision, signal, p, perf);
    const tripwires = tripwireList(p, score, pmAction);
    const edgeScore = Math.round((score || 5) * 7 + conf * 0.3 - tripwires.length * 8);
    return {
      ...p,
      key: `${p.instrument}:${p.contract || p.ticker}`,
      score,
      pmAction,
      bias: bias.label,
      color: bias.color,
      confidence: conf,
      aligned: pmAlignment(pmAction, bias.label),
      horizon: p.instrument === "OPTION" ? "1-10 trading days" : "5-20 trading days",
      catalysts,
      tripwires,
      baseMove: bias.baseMove,
      bearMove: bias.bearMove,
      bullMove: bias.bullMove,
      edgeScore: Math.max(0, Math.min(100, edgeScore)),
      note: forecastNote(p, bias.label, score, pmAction, catalysts),
      path: buildPath(bias.baseMove),
    };
  }).sort((a, b) => (b.edgeScore || 0) - (a.edgeScore || 0));
}

function summarizeForecasts(forecasts, lseHealth) {
  const bullish = forecasts.filter(f => f.bias === "BULLISH").length;
  const bearish = forecasts.filter(f => f.bias === "BEARISH").length;
  const chop = forecasts.filter(f => f.bias === "CHOP").length;
  const aligned = forecasts.filter(f => f.aligned).length;
  const atRisk = forecasts.filter(f => f.tripwires.length).length;
  const thetaWatch = forecasts.filter(f => f.tripwires.some(t => t.includes("THETA"))).length;
  const netBias = bullish > bearish && bullish >= chop ? "BULLISH" : bearish > bullish ? "BEARISH" : "CHOP";
  return {
    underlyings: new Set(forecasts.map(f => f.ticker)).size,
    bullish,
    bearish,
    chop,
    netBias,
    aligned,
    alignedPct: forecasts.length ? Math.round(aligned / forecasts.length * 100) : 0,
    atRisk,
    thetaWatch,
    lseOnline: Boolean(lseHealth?.ok),
  };
}

function buildChartChoices(forecasts) {
  const seen = new Set(["SPY"]);
  const choices = [{ key: "SPY", ticker: "SPY", label: "SPY MARKET", type: "MARKET", color: accent2 }];
  forecasts.forEach(f => {
    const ticker = normalizeTicker(f.ticker);
    if (!ticker || seen.has(ticker)) return;
    seen.add(ticker);
    choices.push({
      key: ticker,
      ticker,
      label: `${ticker} ${f.instrument}`,
      type: f.instrument === "OPTION" ? "OPEN OPTION" : "OPEN EQUITY",
      color: f.color || accent,
    });
  });
  return choices;
}

function buildChartForecast(choice, forecasts, market) {
  if (!choice || choice.key === "SPY") {
    const base = numberish(market?.forecast_pct) ?? 0;
    const low = numberish(market?.cone_low_pct) ?? base - 0.8;
    const high = numberish(market?.cone_high_pct) ?? base + 0.8;
    return { basePct: base, lowPct: low, highPct: high, horizon: "TODAY", color: marketColor(market?.direction) || accent2 };
  }
  const tickerRows = forecasts.filter(f => f.ticker === choice.ticker);
  const best = tickerRows.sort((a, b) => (b.edgeScore || 0) - (a.edgeScore || 0))[0] || {};
  return {
    basePct: numberish(best.baseMove) ?? 0,
    lowPct: numberish(best.bearMove) ?? -3,
    highPct: numberish(best.bullMove) ?? 5,
    horizon: best.horizon || "POSITION WINDOW",
    color: best.color || choice.color || accent,
  };
}

function buildTerminalChartRows(payload, forecast) {
  const historical = normalizeRows(payload).map(normalizeCandle).filter(Boolean).slice(-95);
  if (!historical.length) return [];
  const last = historical[historical.length - 1];
  const basePct = numberish(forecast?.basePct) ?? 0;
  const lowPct = numberish(forecast?.lowPct) ?? basePct - 3;
  const highPct = numberish(forecast?.highPct) ?? basePct + 5;
  const anchor = { label: "NOW", actual: last.close, base: last.close, low: last.close, high: last.close, coneRange: [last.close, last.close] };
  const future = Array.from({ length: 10 }).map((_, i) => {
    const t = (i + 1) / 10;
    const wobble = Math.sin((i + 1) * 0.85) * 0.003;
    const base = last.close * (1 + (basePct / 100) * t + wobble);
    const low = last.close * (1 + (lowPct / 100) * t);
    const high = last.close * (1 + (highPct / 100) * t);
    return {
      label: `K+${i + 1}`,
      actual: null,
      base: Number(base.toFixed(2)),
      low: Number(low.toFixed(2)),
      high: Number(high.toFixed(2)),
      coneRange: [Number(low.toFixed(2)), Number(high.toFixed(2))],
    };
  });
  return [
    ...historical.map(r => ({ label: r.label, actual: r.close, base: null, low: null, high: null, coneRange: null })),
    anchor,
    ...future,
  ];
}

function normalizeCandle(row, i = 0) {
  if (!row) return null;
  const close = numberish(row.close ?? row.c ?? row.price ?? row.value ?? row.last);
  if (close == null) return null;
  return {
    label: shortDate(row.date || row.timestamp || row.time || row.datetime || row.t || i),
    close,
    volume: numberish(row.volume ?? row.v),
  };
}

function sandboxStats(candles, flow) {
  const last = candles.slice(-1)[0]?.close ?? null;
  const prev20 = candles.length > 20 ? candles[candles.length - 21]?.close : null;
  const closes = candles.map(c => c.close).filter(v => v != null).slice(-90);
  const high = closes.length ? Math.max(...closes) : null;
  const low = closes.length ? Math.min(...closes) : null;
  return {
    last,
    move20: last != null && prev20 ? ((last - prev20) / prev20) * 100 : null,
    range: last != null && high != null && low != null ? ((high - low) / last) * 100 : null,
    flowRows: flow.length,
  };
}

function buildCalendarCells(year, month, days) {
  const byDate = new Map(days.map(d => [d.date, d]));
  const first = new Date(year, month - 1, 1);
  const totalDays = new Date(year, month, 0).getDate();
  const cells = [];
  for (let i = 0; i < first.getDay(); i += 1) cells.push({ date: null });
  for (let d = 1; d <= totalDays; d += 1) {
    const date = `${year}-${String(month).padStart(2, "0")}-${String(d).padStart(2, "0")}`;
    cells.push({ date, dayNumber: d, day: byDate.get(date) || { date, status: "NO_FORECAST", has_prediction: false } });
  }
  while (cells.length % 7 !== 0) cells.push({ date: null });
  return cells;
}

function calendarSummary(days) {
  const scored = days.filter(d => d.has_prediction);
  const good = scored.filter(d => d.status === "GOOD").length;
  const bad = scored.filter(d => d.status === "BAD").length;
  const watch = scored.filter(d => !["GOOD", "BAD"].includes(d.status)).length;
  return {
    good,
    bad,
    watch,
    hitRate: good + bad ? Math.round((good / (good + bad)) * 100) : 0,
  };
}

function calendarWeekSummary(cells) {
  const weeks = [];
  for (let i = 0; i < cells.length; i += 7) {
    const chunk = cells.slice(i, i + 7);
    const days = chunk.map(c => c.day).filter(Boolean);
    const predicted = days.filter(d => d.has_prediction);
    const good = predicted.filter(d => d.status === "GOOD").length;
    const bad = predicted.filter(d => d.status === "BAD").length;
    const watch = predicted.filter(d => !["GOOD", "BAD"].includes(d.status)).length;
    weeks.push({ days: predicted.length, good, bad, watch });
  }
  return weeks;
}

function calendarWeekColor(week) {
  if (!week?.days) return "rgba(255,255,255,0.16)";
  if ((week.good || 0) > (week.bad || 0)) return "#4ade80";
  if ((week.bad || 0) > (week.good || 0)) return "#f87171";
  return "#fbbf24";
}

function calendarStatusColor(status) {
  if (status === "GOOD") return "#4ade80";
  if (status === "BAD") return "#f87171";
  if (status === "WATCH" || status === "PENDING") return "#fbbf24";
  return "rgba(255,255,255,0.16)";
}

function calendarTitle(day) {
  if (!day) return "";
  return [
    day.date,
    `Status: ${day.status || "NO_FORECAST"}`,
    `SPY predicted: ${fmtPct(day.spy_prediction_pct)}`,
    `SPY actual: ${fmtPct(day.spy_actual_pct)}`,
    `Fund actual: ${fmtPct(day.fund_actual_pct)}`,
  ].join("\n");
}

function buildCalendarDetailChart(day) {
  const low = numberish(day.spy_cone_low_pct) ?? 0;
  const base = numberish(day.spy_prediction_pct) ?? 0;
  const high = numberish(day.spy_cone_high_pct) ?? 0;
  const actual = numberish(day.spy_actual_pct);
  return [
    { label: "OPEN", low: 0, base: 0, high: 0, actual: 0 },
    { label: "MID", low: Number((low * 0.5).toFixed(2)), base: Number((base * 0.5).toFixed(2)), high: Number((high * 0.5).toFixed(2)), actual: actual == null ? null : Number((actual * 0.5).toFixed(2)) },
    { label: "CLOSE", low, base, high, actual },
  ];
}

function monthName(month) {
  return new Date(2026, Number(month) - 1, 1).toLocaleString("en-US", { month: "long" });
}

function legendDot(color) {
  return {
    display: "inline-block",
    width: 9,
    height: 9,
    borderRadius: 999,
    background: color,
    marginRight: 6,
    boxShadow: `0 0 10px ${color}66`,
  };
}

function shortDate(v) {
  if (typeof v === "number" && v < 1000) return `T-${v}`;
  const d = new Date(v);
  if (Number.isNaN(d.getTime())) return String(v || "").slice(0, 10);
  return d.toLocaleDateString("en-US", { month: "2-digit", day: "2-digit" });
}

function buildScenario(item) {
  return Array.from({ length: 16 }).map((_, i) => {
    const t = i / 15;
    const wobble = Math.sin(i * 1.15) * 0.7;
    return {
      step: `T+${i}`,
      bear: Number((item.bearMove * t + wobble * 0.45).toFixed(2)),
      base: Number((item.baseMove * t + wobble).toFixed(2)),
      bull: Number((item.bullMove * t + wobble * 0.7).toFixed(2)),
    };
  });
}

function buildRadar(rows) {
  return rows.slice(0, 10).map(r => ({
    ticker: r.ticker,
    alignment: r.aligned ? r.confidence : Math.max(12, r.confidence - 28),
    color: r.aligned ? "#4ade80" : "#fbbf24",
  }));
}

function inferMacroTone(macro) {
  const rows = normalizeRows(macro);
  const text = JSON.stringify(rows.slice(0, 20)).toLowerCase();
  if (text.includes("risk") || text.includes("inflation") || text.includes("yield")) return { label: "WATCH", detail: "macro flags", color: "#fbbf24" };
  if (rows.length) return { label: "ONLINE", detail: `${rows.length} rows`, color: accent2 };
  return { label: "UNKNOWN", detail: "no feed", color: muted };
}

function forecastBias(score, pmAction, unrealizedPct, instrument, risk = {}) {
  const pnl = unrealizedPct == null ? 0 : unrealizedPct;
  const thetaWatch = String(risk.theta_status || "").toUpperCase() === "WATCH";
  if (pmAction === "PASS") return { label: "BEARISH", color: "#f87171", baseMove: -2.2, bearMove: -6.5, bullMove: 2.8 };
  if (thetaWatch && instrument === "OPTION") return { label: "HEDGE", color: "#a78bfa", baseMove: 0.8, bearMove: -8.0, bullMove: 8.5 };
  if (pmAction === "ACCUMULATE") return { label: "BULLISH", color: "#4ade80", baseMove: 5.5, bearMove: -4.5, bullMove: 12 };
  if (pmAction === "STARTER") return { label: "BULLISH", color: "#86efac", baseMove: 3.8, bearMove: -3.5, bullMove: 8.5 };
  if (pmAction === "WATCH") return { label: "CHOP", color: "#fbbf24", baseMove: 1.2, bearMove: -3.8, bullMove: 4.5 };
  if (pmAction === "BOTH" || score >= 8.2) return { label: "BULLISH", color: "#4ade80", baseMove: instrument === "OPTION" ? 24 : 5.5, bearMove: instrument === "OPTION" ? -20 : -4.5, bullMove: instrument === "OPTION" ? 85 : 12 };
  if (pmAction === "OPTION" || score >= 7) return { label: "BULLISH", color: "#86efac", baseMove: instrument === "OPTION" ? 16 : 3.8, bearMove: instrument === "OPTION" ? -20 : -3.5, bullMove: instrument === "OPTION" ? 55 : 8.5 };
  if (score <= 4.5 || pnl <= -8) return { label: "BEARISH", color: "#f87171", baseMove: -3.2, bearMove: instrument === "OPTION" ? -20 : -7.5, bullMove: 3.2 };
  return { label: "CHOP", color: "#fbbf24", baseMove: instrument === "OPTION" ? 5 : 1.2, bearMove: instrument === "OPTION" ? -16 : -3.8, bullMove: instrument === "OPTION" ? 22 : 4.5 };
}

function confidence(score, decision, signal, position, perf) {
  let base = 38;
  if (score != null) base += Math.min(28, Math.max(0, score * 3));
  if (decision && Object.keys(decision).length) base += 12;
  if (signal && Object.keys(signal).length) base += 10;
  if (perf && Object.keys(perf).length) base += 6;
  if (position.instrument === "OPTION") base -= 5;
  if (position.risk?.theta_status === "WATCH") base -= 7;
  return Math.max(18, Math.min(92, Math.round(base)));
}

function pmAlignment(pmAction, bias) {
  if (pmAction === "UNMAPPED") return false;
  if (pmAction === "PASS") return bias === "BEARISH" || bias === "CHOP";
  if (pmAction === "EQUITY" || pmAction === "OPTION" || pmAction === "BOTH") return bias === "BULLISH" || bias === "HEDGE";
  return false;
}

function catalystTags(signal, perf, macro) {
  const tags = [];
  const blob = JSON.stringify({ signal, perf }).toLowerCase();
  if (blob.includes("earn")) tags.push("EARNINGS");
  if (blob.includes("contract") || blob.includes("sam")) tags.push("CONTRACT");
  if (blob.includes("fda") || blob.includes("pdufa") || blob.includes("clinical")) tags.push("PHARMA");
  if (blob.includes("x_factor") || blob.includes("trend") || blob.includes("stocktwits")) tags.push("RETAIL");
  if (normalizeRows(macro).length) tags.push("MACRO");
  return [...new Set(tags)].slice(0, 4);
}

function tripwireList(p, score, pmAction) {
  const flags = [];
  if (p.instrument === "OPTION" && p.risk?.hard_stop_triggered) flags.push("HARD STOP");
  if (p.instrument === "OPTION" && String(p.risk?.theta_status || "").toUpperCase() === "WATCH") flags.push("THETA WATCH");
  if (p.unrealizedPct != null && p.unrealizedPct <= -8) flags.push("DRAWDOWN");
  if (score == null) flags.push("NO CASE SCORE");
  if (pmAction === "UNMAPPED") flags.push("NO PM MAP");
  return flags;
}

function forecastNote(position, bias, score, pmAction, catalysts) {
  const scoreText = score == null ? "no mapped Case Score" : `Case Score ${score.toFixed(1)}`;
  const catalystText = catalysts.length ? `Catalysts: ${catalysts.join(", ")}.` : "No catalyst tag is mapped yet.";
  return `${position.instrument} forecast is derived from open exposure, ${scoreText}, PM route ${pmAction}, current P/L pressure, risk checks, and latest scan context. ${catalystText} Proxy mode is advisory only.`;
}

function buildPath(drift) {
  return Array.from({ length: 12 }).map((_, i) => {
    const wobble = Math.sin(i * 1.35) * 0.9;
    return { step: `T+${i}`, value: Number(((drift / 11) * i + wobble).toFixed(2)) };
  });
}

function auditItem(label, value, color) {
  return { label, value, color };
}

function normalizeRows(payload) {
  if (!payload) return [];
  if (Array.isArray(payload)) return payload;
  if (payload.economic_calendar || payload.bond_yields) {
    return [...(payload.economic_calendar || []), ...(payload.bond_yields || [])];
  }
  return payload.rows || payload.data || payload.events || payload.results || payload.flow || [];
}

function normalizeEquityPositions(data) {
  const live = data?.live_alpaca || [];
  if (live.length) return live;
  return data?.db_positions || [];
}

function normalizePmRows(data) {
  return data?.recommendations || data?.decisions || data?.plan || data?.rows || data?.candidates || data?.summary?.decisions || [];
}

function normalizeTicker(v) {
  return String(v || "").replace(/^\$/, "").trim().toUpperCase();
}

function inferUnderlying(symbol) {
  const s = String(symbol || "").toUpperCase();
  const match = s.match(/^([A-Z]{1,6})\d{6}[CP]\d+/);
  return match ? match[1] : s.split(/[ _-]/)[0];
}

function numberish(v) {
  if (v == null || v === "") return null;
  const n = Number(v);
  return Number.isFinite(n) ? n : null;
}

function normalizePct(v) {
  const n = numberish(v);
  if (n == null) return null;
  return Math.abs(n) <= 2 ? n * 100 : n;
}

function fmtMoney(v) {
  if (v == null) return "-";
  return `$${Number(v).toLocaleString(undefined, { maximumFractionDigits: 0 })}`;
}

function fmtPct(v) {
  if (v == null) return "-";
  const n = Number(v);
  return `${n >= 0 ? "+" : ""}${n.toFixed(1)}%`;
}

function fmtTime(v) {
  if (!v) return "--";
  return new Date(v).toLocaleTimeString("en-US", { timeZone: "America/New_York", hour: "2-digit", minute: "2-digit" });
}

function fmtDate(v) {
  if (!v) return "--";
  const d = new Date(v);
  if (Number.isNaN(d.getTime())) return String(v).slice(0, 16);
  return d.toLocaleString("en-US", { timeZone: "America/New_York", month: "short", day: "2-digit", hour: "2-digit", minute: "2-digit" });
}

function signed(v) {
  if (v == null || v === "") return "-";
  const n = Number(v);
  if (!Number.isFinite(n)) return "-";
  return `${n >= 0 ? "+" : ""}${n.toFixed(2)}`;
}

function num(v, digits = 2) {
  if (v == null || v === "") return "-";
  const n = Number(v);
  if (!Number.isFinite(n)) return "-";
  return n.toFixed(digits);
}

function pctColor(v) {
  if (v == null) return muted;
  return Number(v) >= 0 ? "#4ade80" : "#f87171";
}

function rateColor(v) {
  if (v == null || !Number.isFinite(Number(v))) return muted;
  const n = Number(v);
  if (n >= 60) return "#4ade80";
  if (n >= 45) return "#fbbf24";
  return "#f87171";
}

function errorColor(v) {
  if (v == null || !Number.isFinite(Number(v))) return muted;
  const n = Math.abs(Number(v));
  if (n <= 0.15) return "#4ade80";
  if (n <= 0.45) return "#fbbf24";
  return "#f87171";
}

function healthColor(v) {
  const h = String(v || "").toUpperCase();
  if (h === "LIVE") return "#4ade80";
  if (h === "AGING" || h === "DEGRADED") return "#fbbf24";
  if (h === "STALE" || h === "MISSING") return "#f87171";
  return muted;
}

function ageText(minutes) {
  if (minutes == null || !Number.isFinite(Number(minutes))) return "--";
  const n = Number(minutes);
  if (n < 1) return "<1M";
  if (n < 60) return `${Math.round(n)}M`;
  return `${(n / 60).toFixed(1)}H`;
}

function marketColor(direction) {
  if (direction === "UP") return "#4ade80";
  if (direction === "DOWN") return "#f87171";
  if (direction === "FLAT") return "#fbbf24";
  return muted;
}

function routeColor(v) {
  if (v === "BOTH") return "#4ade80";
  if (v === "OPTION") return "#c8a84b";
  if (v === "EQUITY") return accent2;
  if (v === "HELD_NOT_IN_LATEST_PM") return "#fbbf24";
  if (v === "PASS") return "#f87171";
  return muted;
}

function MiniProb({ label, value, color }) {
  return (
    <div style={{ border: hairline, padding: 10, background: "rgba(255,255,255,0.02)", minWidth: 0 }}>
      <div style={{ color: dim, fontSize: 9, letterSpacing: "0.14em" }}>{label}</div>
      <strong style={{ color, fontSize: 18, letterSpacing: "0.04em" }}>{value == null ? "-" : `${Number(value).toFixed(1)}%`}</strong>
    </div>
  );
}

function CandleHorizonTable({ rows }) {
  const clean = (rows || []).filter(r => r?.ok);
  if (!clean.length) {
    return <div style={{ ...explainText, border: hairline, padding: 14, marginTop: 14 }}>Kronos candle engine is waiting on enough OHLCV rows.</div>;
  }
  return (
    <div style={candleTableWrap}>
      <table style={{ width: "100%", borderCollapse: "collapse", tableLayout: "fixed" }}>
        <thead>
          <tr>
            <th style={th}>HORIZON</th>
            <th style={th}>UP</th>
            <th style={th}>DOWN</th>
            <th style={th}>FLAT</th>
            <th style={th}>FORECAST</th>
            <th style={th}>CONE</th>
          </tr>
        </thead>
        <tbody>
          {clean.map(row => (
            <tr key={row.timeframe}>
              <td style={{ ...td, color: accent2, fontWeight: 900 }}>{String(row.timeframe || "").toUpperCase()}</td>
              <td style={{ ...td, color: "#4ade80" }}>{num(row.probabilities?.up, 1)}%</td>
              <td style={{ ...td, color: "#f87171" }}>{num(row.probabilities?.down, 1)}%</td>
              <td style={{ ...td, color: "#fbbf24" }}>{num(row.probabilities?.flat, 1)}%</td>
              <td style={{ ...td, color: marketColor(row.direction), fontWeight: 900 }}>{row.direction} {signed(row.forecast_pct)}%</td>
              <td style={td}>{signed(row.cone_low_pct)}% to {signed(row.cone_high_pct)}%</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function PlanRow({ k, v, color = labelLight }) {
  return (
    <div style={{ display: "grid", gridTemplateColumns: "150px minmax(0, 1fr)", gap: 12, padding: "9px 0", borderTop: hairline }}>
      <span style={{ color: dim, fontSize: 10, letterSpacing: "0.14em" }}>{k}</span>
      <strong style={{ color, fontSize: 12, letterSpacing: "0.06em", overflowWrap: "anywhere" }}>{v}</strong>
    </div>
  );
}

function Mini({ label, value, color }) {
  return (
    <div style={{ border: hairline, padding: 12, background: "rgba(255,255,255,0.018)", minWidth: 0 }}>
      <div style={{ color: dim, fontSize: 9, letterSpacing: "0.14em", whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" }}>{label}</div>
      <div style={{ color, fontSize: 16, letterSpacing: "0.04em", marginTop: 7, fontWeight: 900, overflowWrap: "anywhere" }}>{value}</div>
    </div>
  );
}

function EmptyState({ compact = false }) {
  return (
    <div style={{ color: muted, padding: compact ? "28px 0" : 28, fontSize: 12, lineHeight: 1.7 }}>
      No open positions found. Kronos populates from the equity fund and options desk once positions are live.
    </div>
  );
}

function buttonStyle(color) {
  return {
    background: "rgba(255,255,255,0.02)",
    border: `0.5px solid ${color}`,
    color,
    padding: "10px 14px",
    fontSize: 10,
    letterSpacing: "0.14em",
    fontFamily: "JetBrains Mono, Courier New",
    fontWeight: 800,
    cursor: "pointer",
  };
}

function selectionCard(row, active) {
  return {
    width: "100%",
    display: "grid",
    gridTemplateColumns: "minmax(0, 1fr) auto 58px",
    alignItems: "center",
    gap: 12,
    textAlign: "left",
    border: `0.5px solid ${active ? row.color : "rgba(255,255,255,0.1)"}`,
    background: active ? `${row.color}12` : "rgba(255,255,255,0.018)",
    padding: 12,
    cursor: "pointer",
    boxShadow: active ? `0 0 16px ${row.color}18` : "none",
  };
}

function pill(color) {
  return {
    color,
    border: `0.5px solid ${color}88`,
    background: `${color}10`,
    padding: "6px 8px",
    fontSize: 9,
    letterSpacing: "0.12em",
    fontWeight: 900,
    whiteSpace: "nowrap",
  };
}

function flowRow(color) {
  return {
    display: "grid",
    gridTemplateColumns: "80px minmax(0, 1fr) 90px",
    gap: 10,
    alignItems: "center",
    border: `0.5px solid ${color}44`,
    background: `${color}08`,
    padding: "10px 12px",
    color: labelLight,
    fontSize: 11,
    letterSpacing: "0.06em",
  };
}

function chartBox(height) {
  return {
    height,
    border: hairline,
    background: "rgba(255,255,255,0.018)",
    padding: 10,
    minWidth: 0,
  };
}

const tooltipStyle = {
  background: "#07080d",
  border: hairline,
  color: labelLight,
  fontSize: 11,
};

const hero = {
  display: "grid",
  gridTemplateColumns: "minmax(0, 1fr) minmax(260px, 360px)",
  gap: 18,
  border: `0.5px solid ${accent2}55`,
  background: `linear-gradient(135deg, rgba(167,139,250,0.08), rgba(94,234,212,0.035) 45%, ${pageBg})`,
  padding: 18,
  marginBottom: 20,
};

const bootBox = { border: hairline, background: "rgba(0,0,0,0.22)", padding: 14, display: "grid", gap: 9 };
const bootRow = { display: "flex", justifyContent: "space-between", gap: 12, color: muted, fontSize: 10, letterSpacing: "0.13em" };
const marketBand = {
  display: "grid",
  gridTemplateColumns: "repeat(auto-fit, minmax(170px, 1fr))",
  gap: 10,
  marginTop: 16,
};
const marketCell = {
  border: hairline,
  background: "rgba(0,0,0,0.18)",
  padding: 10,
  display: "grid",
  gap: 6,
  color: muted,
  fontSize: 9,
  letterSpacing: "0.14em",
};

const topGrid = {
  display: "grid",
  gridTemplateColumns: "minmax(330px, 0.85fr) minmax(0, 1.15fr)",
  gap: 22,
};

const middleGrid = {
  display: "grid",
  gridTemplateColumns: "minmax(0, 1.1fr) minmax(320px, 0.9fr)",
  gap: 22,
};

const bottomGrid = {
  display: "grid",
  gridTemplateColumns: "minmax(0, 1fr) minmax(320px, 0.8fr)",
  gap: 22,
};

const miniGrid = {
  display: "grid",
  gridTemplateColumns: "repeat(auto-fit, minmax(130px, 1fr))",
  gap: 10,
  marginBottom: 14,
};

const commandRows = { marginTop: 14 };
const explainText = { marginTop: 12, color: muted, fontSize: 11, lineHeight: 1.65 };
const auditGrid = { display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(150px, 1fr))", gap: 12 };
const tabBar = { display: "flex", gap: 8, flexWrap: "wrap", marginBottom: 18 };
const memoryGrid = { display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(180px, 1fr))", gap: 12, marginBottom: 18 };
const proofGrid = { display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(135px, 1fr))", gap: 10 };
const proofTables = { display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(320px, 1fr))", gap: 12 };
const proofRow = {
  display: "grid",
  gridTemplateColumns: "minmax(80px, 1fr) 54px 72px 82px 80px",
  gap: 8,
  alignItems: "center",
  borderTop: hairline,
  padding: "8px 0",
  color: muted,
  fontSize: 10,
  letterSpacing: "0.06em",
};
const terminalChartLayout = { display: "grid", gridTemplateColumns: "minmax(0, 1fr) 210px", gap: 16, alignItems: "stretch" };
const terminalChartBody = { minWidth: 0 };
const chartHeaderRow = { display: "flex", justifyContent: "space-between", gap: 16, alignItems: "flex-start", marginBottom: 12, flexWrap: "wrap" };
const chartSelectorRail = { border: hairline, background: "rgba(0,0,0,0.18)", padding: 12, display: "flex", flexDirection: "column", gap: 8, maxHeight: 535, overflowY: "auto" };
const kronosCommandGrid = { display: "grid", gridTemplateColumns: "minmax(0, 1.55fr) minmax(300px, 0.75fr)", gap: 14, alignItems: "stretch", marginBottom: 14 };
const tradingViewShell = { border: hairline, background: "rgba(0,0,0,0.2)", minWidth: 0, overflow: "hidden" };
const candlePredictionPanel = { border: hairline, background: "rgba(255,255,255,0.018)", padding: 14, minWidth: 0 };
const probGrid = { display: "grid", gridTemplateColumns: "repeat(3, minmax(0, 1fr))", gap: 8, marginTop: 12 };
const ohlcGrid = { display: "grid", gridTemplateColumns: "repeat(2, minmax(0, 1fr))", gap: 8, marginTop: 12 };
const ohlcBox = { border: hairline, background: "rgba(0,0,0,0.16)", padding: 10, display: "grid", gap: 5, color: dim, fontSize: 9, letterSpacing: "0.14em" };
const candleFeatureStack = { marginTop: 12 };
const candleTableWrap = { border: hairline, background: "rgba(0,0,0,0.18)", marginBottom: 14, overflowX: "auto" };
const loadingText = { height: "100%", display: "grid", placeItems: "center", color: muted, fontSize: 12, letterSpacing: "0.12em" };
const sandboxControlRow = { display: "grid", gridTemplateColumns: "190px auto minmax(0, 1fr)", gap: 14, alignItems: "end" };
const sandboxInput = {
  width: "100%",
  marginTop: 8,
  background: "rgba(0,0,0,0.35)",
  border: hairline,
  color: labelLight,
  padding: "11px 12px",
  fontSize: 16,
  letterSpacing: "0.1em",
  fontFamily: "JetBrains Mono, Courier New",
  fontWeight: 900,
  outline: "none",
};
const sandboxGrid = { display: "grid", gridTemplateColumns: "minmax(0, 1.25fr) minmax(320px, 0.75fr)", gap: 22 };
const calendarShellHeader = { display: "flex", justifyContent: "space-between", gap: 14, alignItems: "center", flexWrap: "wrap", marginBottom: 16 };
const calendarHeroStats = { display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(145px, 1fr))", gap: 10, marginBottom: 18 };
const calendarHeroTile = (color) => ({
  border: `0.5px solid ${color}55`,
  background: `linear-gradient(145deg, ${color}16, rgba(255,255,255,0.025))`,
  borderRadius: 8,
  minHeight: 78,
  padding: 12,
  display: "grid",
  alignContent: "space-between",
  boxShadow: `inset 0 -2px 0 ${color}66`,
});
const calendarMonthButton = {
  background: "rgba(255,255,255,0.035)",
  border: "0.5px solid rgba(255,255,255,0.16)",
  color: labelLight,
  borderRadius: 7,
  padding: "9px 12px",
  fontSize: 10,
  letterSpacing: "0.1em",
  fontFamily: "JetBrains Mono, Courier New",
  fontWeight: 900,
  cursor: "pointer",
};
const calendarBoard = { display: "grid", gridTemplateColumns: "minmax(0, 1fr) 120px", gap: 12, alignItems: "stretch" };
const calendarToolbar = { display: "flex", justifyContent: "space-between", gap: 14, alignItems: "center", flexWrap: "wrap", marginBottom: 16 };
const calendarLegend = { display: "flex", gap: 14, flexWrap: "wrap", color: muted, fontSize: 10, letterSpacing: "0.12em" };
const calendarWeekHeader = { display: "grid", gridTemplateColumns: "repeat(7, minmax(0, 1fr))", gap: 5, color: dim, fontSize: 10, letterSpacing: "0.08em", margin: "12px 0 8px" };
const calendarGrid = { display: "grid", gridTemplateColumns: "repeat(7, minmax(0, 1fr))", gap: 5 };
const calendarDetailGrid = { display: "grid", gridTemplateColumns: "minmax(320px, 0.8fr) minmax(0, 1.2fr)", gap: 18, alignItems: "start" };
const calendarDayNumber = { alignSelf: "flex-end", color: dim, fontSize: 10, lineHeight: 1 };
const calendarDayPayload = { display: "grid", gap: 3, placeItems: "center", textAlign: "center", color: labelLight, minHeight: 54 };
const calendarWeekRail = { display: "grid", gap: 7, alignContent: "start", paddingTop: 24 };
const calendarWeekCard = (color) => ({
  minHeight: 72,
  border: `0.5px solid ${color}55`,
  background: `linear-gradient(145deg, ${color}14, rgba(255,255,255,0.02))`,
  borderRadius: 8,
  color: labelLight,
  padding: 10,
  display: "grid",
  gap: 4,
  textAlign: "left",
  fontFamily: "JetBrains Mono, Courier New",
  cursor: "default",
});
const selectStyle = {
  background: "#050509",
  border: hairline,
  color: accent2,
  padding: "10px 12px",
  fontSize: 11,
  letterSpacing: "0.1em",
  fontFamily: "JetBrains Mono, Courier New",
  fontWeight: 800,
};
const stackGrid = { display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(230px, 1fr))", gap: 12 };
const stackPanel = { border: hairline, background: "rgba(255,255,255,0.018)", padding: 12, minWidth: 0 };
const sectionLabel = { color: accent2, fontSize: 9, letterSpacing: "0.16em", fontWeight: 900, marginBottom: 10 };
const barRow = { display: "grid", gridTemplateColumns: "95px minmax(0, 1fr) 70px", gap: 8, alignItems: "center", color: muted, fontSize: 10, padding: "6px 0", borderTop: hairline };
const barTrack = { height: 5, background: "rgba(255,255,255,0.06)", overflow: "hidden" };
const barFill = { height: "100%", background: accent2, boxShadow: `0 0 8px ${accent2}55` };
const probRow = { display: "flex", justifyContent: "space-between", gap: 10, borderTop: hairline, padding: "8px 0", color: muted, fontSize: 11 };
const horizonRow = { display: "flex", justifyContent: "space-between", gap: 10, borderTop: hairline, padding: "8px 0", color: muted, fontSize: 11 };
const emptySmall = { color: muted, fontSize: 11, padding: "8px 0" };

function tabButton(active) {
  return {
    background: active ? "rgba(94,234,212,0.1)" : "rgba(255,255,255,0.018)",
    border: `0.5px solid ${active ? accent2 : "rgba(255,255,255,0.12)"}`,
    color: active ? accent2 : muted,
    padding: "9px 12px",
    fontSize: 10,
    letterSpacing: "0.13em",
    fontFamily: "JetBrains Mono, Courier New",
    fontWeight: 900,
    cursor: "pointer",
  };
}

function chartChoiceButton(active, color = accent2) {
  return {
    display: "grid",
    gap: 4,
    textAlign: "left",
    background: active ? `${color}18` : "rgba(255,255,255,0.02)",
    border: `0.5px solid ${active ? color : "rgba(255,255,255,0.11)"}`,
    color: active ? color : labelLight,
    padding: "10px 11px",
    cursor: "pointer",
    fontFamily: "JetBrains Mono, Courier New",
    letterSpacing: "0.08em",
    boxShadow: active ? `0 0 16px ${color}18` : "none",
  };
}

function calendarCell(cell, active) {
  const color = calendarStatusColor(cell.day?.status);
  const hasData = Boolean(cell.day?.has_prediction);
  return {
    aspectRatio: "1 / 1.2",
    minHeight: 96,
    display: "flex",
    flexDirection: "column",
    justifyContent: "space-between",
    alignItems: "stretch",
    background: !cell.date ? "transparent" : hasData ? `linear-gradient(135deg, ${color}20, rgba(255,255,255,0.025))` : "rgba(255,255,255,0.018)",
    border: !cell.date ? "0.5px solid transparent" : `0.5px solid ${active ? accent : hasData ? `${color}88` : "rgba(255,255,255,0.09)"}`,
    color: !cell.date ? "transparent" : hasData ? labelLight : dim,
    cursor: cell.date ? "pointer" : "default",
    textAlign: "left",
    padding: 9,
    fontFamily: "JetBrains Mono, Courier New",
    fontWeight: 900,
    borderRadius: 7,
    boxShadow: active ? `0 0 0 1px ${accent}, 0 0 22px ${accent}24` : hasData ? `inset 0 -3px 0 ${color}, 0 0 18px ${color}10` : "none",
    overflow: "hidden",
  };
}
