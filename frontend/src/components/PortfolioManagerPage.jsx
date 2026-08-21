import { useCallback, useEffect, useMemo, useState } from "react";
import axios from "axios";
import { API } from "../config";
import { CartesianGrid, Cell, Line, LineChart, Pie, PieChart, ReferenceLine, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { CrtShell, Card, Stat, tokens } from "./CrtShell";
import { TradeJournalView } from "./TradeJournalPage";

const { accent, accent2, dim, muted, labelLight, hairline, cardBg } = tokens;

const th = { padding: "10px 8px", fontSize: 10, color: dim, letterSpacing: "0.14em", fontWeight: 400, textAlign: "left" };
const td = { padding: "10px 8px", color: labelLight, letterSpacing: "0.04em", fontSize: 12, verticalAlign: "top" };

const ACTION_COLOR = {
  ACCUMULATE: "#4ade80",
  STARTER: "#5eead4",
  WATCH: "#fbbf24",
  REJECT: "#f87171",
};

const PIE_COLORS = ["#c8a84b", "#5eead4", "#4ade80", "#fbbf24", "#f87171", "#a78bfa", "#60a5fa", "#f472b6"];

export default function PortfolioManagerPage() {
  const [plan, setPlan] = useState(null);
  const [equity, setEquity] = useState("");
  const [mode, setMode] = useState("AUTO");
  const [filter, setFilter] = useState("ACTIVE");
  const [subtab, setSubtab] = useState("FUND");
  const [fundTab, setFundTab] = useState("TOTAL");
  const [learningTab, setLearningTab] = useState("EQUITIES");
  const [backtestTab, setBacktestTab] = useState("EQUITIES");
  const [learning, setLearning] = useState(null);
  const [optionsLearning, setOptionsLearning] = useState(null);
  const [backtest, setBacktest] = useState(null);
  const [optionsBacktest, setOptionsBacktest] = useState(null);
  const [optionsPlan, setOptionsPlan] = useState(null);
  const [optionsAccount, setOptionsAccount] = useState(null);
  const [optionsPositions, setOptionsPositions] = useState([]);
  const [optionsOrders, setOptionsOrders] = useState([]);
  const [plCalendar, setPlCalendar] = useState(null);
  const [plCalendarLoading, setPlCalendarLoading] = useState(false);
  const today = useMemo(() => new Date(), []);
  const [plMonth, setPlMonth] = useState(today.getMonth() + 1);
  const [plYear, setPlYear] = useState(today.getFullYear());
  const [selectedPlDay, setSelectedPlDay] = useState(null);
  const [rulesets, setRulesets] = useState(null);
  const [health, setHealth] = useState(null);
  const [backtestLoading, setBacktestLoading] = useState(false);
  const [sandbox, setSandbox] = useState({
    mode: "BALANCED",
    equity: "1000",
    max_gross_deployment_pct: "0.35",
    max_position_pct: "0.08",
    max_single_name_risk_pct: "0.0125",
    accumulate_score: "70",
    accumulate_rr: "1.8",
    starter_score: "58",
    starter_rr: "1.3",
    watch_score: "45",
  });
  const [loading, setLoading] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const params = { mode };
      if (equity !== "" && Number(equity) > 0) params.equity = Number(equity);
      const r = await axios.get(`${API}/portfolio_manager/latest`, {
        params,
      });
      setPlan(r.data);
    } finally {
      setLoading(false);
    }
  }, [equity, mode]);

  useEffect(() => { load(); }, [load]);

  useEffect(() => {
    let cancelled = false;
    axios.get(`${API}/portfolio_manager/learning/status`)
      .then(r => { if (!cancelled) setLearning(r.data); })
      .catch(() => { if (!cancelled) setLearning(null); });
    return () => { cancelled = true; };
  }, []);

  const loadBacktest = useCallback(async () => {
    setBacktestLoading(true);
    try {
      const params = { limit_scans: 120, mode: sandbox.mode };
      Object.entries(sandbox).forEach(([k, v]) => {
        if (k === "mode") return;
        if (v !== "" && Number.isFinite(Number(v))) params[k] = Number(v);
      });
      const r = await axios.get(`${API}/portfolio_manager/backtest`, { params });
      setBacktest(r.data);
    } finally {
      setBacktestLoading(false);
    }
  }, [sandbox]);

  useEffect(() => {
    if (subtab !== "BACKTEST" || backtestTab !== "EQUITIES" || backtest) return;
    loadBacktest();
  }, [subtab, backtestTab, backtest, loadBacktest]);

  useEffect(() => {
    if (subtab !== "LEARNING" || learningTab !== "OPTIONS" || optionsLearning) return;
    axios.get(`${API}/portfolio_manager/options/learning/status`)
      .then(r => setOptionsLearning(r.data))
      .catch(() => setOptionsLearning(null));
  }, [subtab, learningTab, optionsLearning]);

  useEffect(() => {
    if (subtab !== "BACKTEST" || backtestTab !== "OPTIONS" || optionsBacktest) return;
    axios.get(`${API}/portfolio_manager/options/backtest`)
      .then(r => setOptionsBacktest(r.data))
      .catch(() => setOptionsBacktest(null));
  }, [subtab, backtestTab, optionsBacktest]);

  useEffect(() => {
    axios.get(`${API}/portfolio_manager/options/latest`)
      .then(r => setOptionsPlan(r.data))
      .catch(() => setOptionsPlan(null));
  }, []);

  useEffect(() => {
    axios.get(`${API}/portfolio_manager/rulesets`)
      .then(r => setRulesets(r.data))
      .catch(() => setRulesets(null));
  }, []);

  useEffect(() => {
    axios.get(`${API}/system/health`)
      .then(r => setHealth(r.data))
      .catch(() => setHealth(null));
  }, []);

  const loadPlCalendar = useCallback(async () => {
    setPlCalendarLoading(true);
    try {
      const r = await axios.get(`${API}/signals/benchmark_curve`, { params: { days: 900 }, timeout: 20000 });
      setPlCalendar(r.data);
      const rows = r.data?.curve || [];
      setSelectedPlDay(rows.slice().reverse().find(row => dateParts(row.date).month === plMonth && dateParts(row.date).year === plYear) || null);
    } catch (e) {
      setPlCalendar({ curve: [], error: e.message });
      setSelectedPlDay(null);
    } finally {
      setPlCalendarLoading(false);
    }
  }, [plMonth, plYear]);

  useEffect(() => {
    if (subtab !== "P/L CALENDAR" || plCalendar || plCalendarLoading) return;
    loadPlCalendar();
  }, [subtab, plCalendar, plCalendarLoading, loadPlCalendar]);

  useEffect(() => {
    if (subtab !== "P/L CALENDAR" || !plCalendar?.curve?.length) return;
    const rows = plCalendar.curve || [];
    setSelectedPlDay(rows.slice().reverse().find(row => dateParts(row.date).month === plMonth && dateParts(row.date).year === plYear) || null);
  }, [subtab, plCalendar, plMonth, plYear]);

  useEffect(() => {
    let cancelled = false;
    Promise.all([
      axios.get(`${API}/options_desk/account`).catch(e => ({ data: { ok: false, reason: e.message } })),
      axios.get(`${API}/options_desk/positions`).catch(() => ({ data: { positions: [] } })),
      axios.get(`${API}/options_desk/orders`).catch(() => ({ data: { orders: [] } })),
    ]).then(([accountRes, positionsRes, ordersRes]) => {
      if (cancelled) return;
      setOptionsAccount(accountRes.data);
      setOptionsPositions(positionsRes.data.positions || []);
      setOptionsOrders(ordersRes.data.orders || []);
    });
    return () => { cancelled = true; };
  }, []);

  const rows = useMemo(() => {
    const all = plan?.recommendations || [];
    if (filter === "ALL") return all;
    if (filter === "ACTIVE") return all.filter(r => r.action === "ACCUMULATE" || r.action === "STARTER");
    return all.filter(r => r.action === filter);
  }, [plan, filter]);

  const summary = plan?.summary || {};
  const exposure = plan?.exposure || {};
  const regime = summary.regime || {};

  return (
    <CrtShell title="PORTFOLIO MANAGER"
      headerRight={
        <div style={{ display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap", justifyContent: "flex-end" }}>
          <select value={mode} onChange={e => setMode(e.target.value)}
            aria-label="PM mode"
            style={{
              background: "#050509", border: `0.5px solid ${dim}`,
              color: accent2, padding: "8px 10px", fontSize: 11,
              fontFamily: "JetBrains Mono", letterSpacing: "0.08em",
            }}>
            <option value="AUTO">AUTO</option>
            <option value="RISK_OFF">RISK OFF</option>
            <option value="CONSERVATIVE">CONSERVATIVE</option>
            <option value="BALANCED">BALANCED</option>
            <option value="AGGRESSIVE">AGGRESSIVE</option>
          </select>
          <input
            value={equity}
            onChange={e => setEquity(e.target.value)}
            placeholder="AUTO $"
            aria-label="Equity override"
            style={{
              width: 96, background: "#050509", border: `0.5px solid ${dim}`,
              color: accent, padding: "8px 10px", fontSize: 11,
              fontFamily: "JetBrains Mono", letterSpacing: "0.08em",
            }}
          />
          <button onClick={load} disabled={loading}
            style={{
              background: "transparent", border: `0.5px solid ${accent}`,
              color: loading ? muted : accent, fontSize: 11, padding: "8px 16px",
              cursor: loading ? "default" : "pointer", letterSpacing: "0.14em",
              fontFamily: "JetBrains Mono", fontWeight: 700,
            }}>
            {loading ? "RUNNING" : "REPRICE PLAN"}
          </button>
        </div>
      }>
      <div style={{
        padding: "14px 18px", border: `0.5px solid ${accent2}`,
        background: `${accent2}10`, color: accent2, fontSize: 11,
        letterSpacing: "0.1em", marginBottom: 16,
      }}>
        PM CONTROLS TRADE FLOOR: AUTO mode follows market regime, no Claude, no discretionary text.
      </div>

      <div style={{ display: "flex", borderBottom: hairline, marginBottom: 16, flexWrap: "wrap" }}>
        {["FUND", "P/L CALENDAR", "LEARNING", "BACKTEST", "TRADE JOURNAL"].map(k => (
          <button key={k} onClick={() => setSubtab(k)}
            style={{
              background: "transparent", border: "none", padding: "10px 22px",
              color: subtab === k ? accent : muted, cursor: "pointer",
              borderBottom: subtab === k ? `2px solid ${accent}` : "2px solid transparent",
              fontSize: 11, letterSpacing: "0.14em", fontFamily: "JetBrains Mono", fontWeight: 700,
            }}>
            {k}
          </button>
        ))}
      </div>

      {subtab === "FUND" && <FundHubView
        active={fundTab}
        setActive={setFundTab}
        plan={plan}
        optionsPlan={optionsPlan}
        rows={rows}
        filter={filter}
        setFilter={setFilter}
        summary={summary}
        exposure={exposure}
        regime={regime}
        rulesets={rulesets}
        health={health}
        optionsAccount={optionsAccount}
        optionsPositions={optionsPositions}
        optionsOrders={optionsOrders}
        refreshRules={() => {
          axios.get(`${API}/portfolio_manager/rulesets`).then(r => setRulesets(r.data));
          load();
        }}
        refreshHealth={() => {
          axios.get(`${API}/system/health`).then(r => setHealth(r.data));
        }}
        refreshOptions={() => {
          Promise.all([
            axios.get(`${API}/portfolio_manager/options/latest`).catch(() => ({ data: null })),
            axios.get(`${API}/options_desk/account`).catch(e => ({ data: { ok: false, reason: e.message } })),
            axios.get(`${API}/options_desk/positions`).catch(() => ({ data: { positions: [] } })),
            axios.get(`${API}/options_desk/orders`).catch(() => ({ data: { orders: [] } })),
          ]).then(([planRes, accountRes, positionsRes, ordersRes]) => {
            setOptionsPlan(planRes.data);
            setOptionsAccount(accountRes.data);
            setOptionsPositions(positionsRes.data.positions || []);
            setOptionsOrders(ordersRes.data.orders || []);
          });
        }}
      />}

      {subtab === "P/L CALENDAR" && <PMCalendarView
        data={plCalendar}
        loading={plCalendarLoading}
        month={plMonth}
        year={plYear}
        selected={selectedPlDay}
        setSelected={setSelectedPlDay}
        setMonth={setPlMonth}
        setYear={setPlYear}
        refresh={loadPlCalendar}
      />}

      {subtab === "LEARNING" && <LearningHubView
        active={learningTab}
        setActive={setLearningTab}
        equityLearning={learning}
        optionsLearning={optionsLearning}
      />}

      {subtab === "BACKTEST" && <BacktestHubView
        active={backtestTab}
        setActive={setBacktestTab}
        equityBacktest={backtest}
        optionsBacktest={optionsBacktest}
        sandbox={sandbox}
        setSandbox={setSandbox}
        loadBacktest={loadBacktest}
        loading={backtestLoading}
      />}

      {subtab === "TRADE JOURNAL" && <TradeJournalView />}

    </CrtShell>
  );
}

function FundHubView({
  active,
  setActive,
  plan,
  optionsPlan,
  rows,
  filter,
  setFilter,
  summary,
  exposure,
  regime,
  rulesets,
  health,
  optionsAccount,
  optionsPositions,
  optionsOrders,
  refreshRules,
  refreshHealth,
  refreshOptions,
}) {
  const optionSummary = optionsPlan?.summary || {};
  const optionAccount = optionsAccount?.account || {};
  const equityBasis = Number(summary.equity_basis || 0);
  const optionEquity = Number(optionAccount.equity || optionsPlan?.options_equity_basis || 20000);
  const totalCapital = equityBasis + optionEquity;
  const plannedEquityRisk = Number(summary.planned_risk || 0);
  const optionReadyRisk = (optionsPlan?.candidates || [])
    .filter(c => c.manual_fire_ready)
    .reduce((acc, c) => acc + Number(c.risk_budget || 0), 0);
  const totalSummary = {
    equityBasis,
    optionEquity,
    totalCapital,
    plannedEquityRisk,
    optionReadyRisk,
    optionSummary,
    optionAccount,
    optionsPositions,
    optionsOrders,
    summary,
    health,
    optionsAccount,
  };
  return (
    <>
      <div style={{
        display: "flex",
        border: hairline,
        background: cardBg,
        marginBottom: 16,
        width: "fit-content",
        maxWidth: "100%",
        flexWrap: "wrap",
      }}>
        {["TOTAL", "EQUITIES", "OPTIONS"].map(k => (
          <button key={k} onClick={() => setActive(k)}
            style={{
              background: active === k ? `${accent}12` : "transparent",
              border: "none",
              borderRight: k !== "OPTIONS" ? hairline : "none",
              color: active === k ? accent : muted,
              cursor: "pointer",
              padding: "10px 22px",
              fontSize: 11,
              letterSpacing: "0.14em",
              fontFamily: "JetBrains Mono",
              fontWeight: 800,
            }}>
            {k}
          </button>
        ))}
      </div>

      {active === "TOTAL" ? (
        <TotalFundView data={totalSummary} />
      ) : active === "EQUITIES" ? (
        <EquitiesFundView
          plan={plan}
          optionsPlan={optionsPlan}
          rows={rows}
          filter={filter}
          setFilter={setFilter}
          summary={summary}
          exposure={exposure}
          regime={regime}
          rulesets={rulesets}
          health={health}
          refreshRules={refreshRules}
          refreshHealth={refreshHealth}
        />
      ) : (
        <OptionsFundView
          optionsPlan={optionsPlan}
          optionsAccount={optionsAccount}
          positions={optionsPositions}
          orders={optionsOrders}
          rulesets={rulesets}
          health={health}
          refreshOptions={refreshOptions}
        />
      )}
    </>
  );
}

function PMCalendarView({ data, loading, month, year, selected, setSelected, setMonth, setYear, refresh }) {
  const rows = data?.curve || [];
  const years = calendarYears(rows, year);
  const cells = buildPmCalendarCells(year, month, rows);
  const monthRows = rows.filter(r => dateParts(r.date).year === year && dateParts(r.date).month === month);
  const summary = pmCalendarSummary(monthRows);
  const weeks = pmWeekSummary(cells);
  return (
    <div style={{ display: "grid", gap: 18 }}>
      <Card title="PM P/L CALENDAR" accentColor={accent}>
        <div style={calendarShellHeader}>
          <div style={{ display: "flex", gap: 10, flexWrap: "wrap" }}>
            <select value={month} onChange={e => setMonth(Number(e.target.value))} style={selectStyle}>
              {Array.from({ length: 12 }).map((_, i) => <option key={i + 1} value={i + 1}>{pmMonthName(i + 1)}</option>)}
            </select>
            <select value={year} onChange={e => setYear(Number(e.target.value))} style={selectStyle}>
              {years.map(y => <option key={y} value={y}>{y}</option>)}
            </select>
            <button onClick={refresh} disabled={loading} style={outlineButton(accent)}>{loading ? "LOADING" : "REFRESH P/L"}</button>
          </div>
          <button onClick={() => { setMonth(new Date().getMonth() + 1); setYear(new Date().getFullYear()); }} style={calendarMonthButton}>THIS MONTH</button>
        </div>
        <div style={calendarHeroStats}>
          <div style={calendarHeroTile(pctColor(summary.avg))}><span>Month Avg</span><strong>{fmtPct(summary.avg)}</strong><small>{monthRows.length} trading days</small></div>
          <div style={calendarHeroTile("#4ade80")}><span>Green Days</span><strong>{summary.green}</strong><small>positive fund days</small></div>
          <div style={calendarHeroTile("#f87171")}><span>Red Days</span><strong>{summary.red}</strong><small>negative fund days</small></div>
          <div style={calendarHeroTile(summary.spyWins >= summary.spyLosses ? "#4ade80" : "#f87171")}><span>Vs SPY</span><strong>{summary.spyWins}/{summary.spyLosses}</strong><small>{summary.spyWinRate}% win rate</small></div>
          <div style={calendarHeroTile(pctColor(summary.relativeAvg))}><span>Return Diff</span><strong>{fmtPct(summary.relativeAvg)}</strong><small>fund minus SPY</small></div>
          <div style={calendarHeroTile("#a78bfa")}><span>Range</span><strong>{fmtPct(summary.best)}</strong><small>worst {fmtPct(summary.worst)}</small></div>
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
                  onClick={() => cell.row && setSelected(cell.row)}
                  title={pmCalendarTitle(cell.row)}
                  style={pmCalendarCell(cell, selected?.date === cell.date)}
                >
                  <span style={calendarDayNumber}>{cell.dayNumber || ""}</span>
                  {cell.row && (
                    <span style={calendarDayPayload}>
                      <strong>{fmtPct(cell.row.terminal_total_pct)}</strong>
                      <small>SPY {fmtPct(cell.row.spy_return_pct)}</small>
                      <small style={{ color: pctColor(cell.row.relative_pct) }}>DIFF {fmtPct(cell.row.relative_pct)}</small>
                    </span>
                  )}
                </button>
              ))}
            </div>
          </div>
          <PMWeekRail weeks={weeks} />
        </div>
      </Card>

      <Card title={selected ? `PM DAY DETAIL / ${selected.date}` : "PM DAY DETAIL"} accentColor={pctColor(selected?.terminal_total_pct)}>
        {selected ? (
          <div style={pmDetailGrid}>
            <div>
              <div style={{ display: "flex", background: cardBg, border: hairline, marginBottom: 12, flexWrap: "wrap" }}>
                <Stat label="TOTAL FUND" value={fmtPct(selected.terminal_total_pct)} sub="equities + options" color={pctColor(selected.terminal_total_pct)} accentBar />
                <Stat label="EQUITIES" value={fmtPct(selected.stock_gain_pct)} sub={`${selected.stock_positions || 0} tracked`} color={pctColor(selected.stock_gain_pct)} />
                <Stat label="OPTIONS" value={fmtPct(selected.options_gain_pct)} sub={`${selected.options_positions || 0} tracked`} color={pctColor(selected.options_gain_pct)} />
                <Stat label="VS SPY" value={fmtPct(selected.relative_pct)} sub={`SPY ${fmtPct(selected.spy_return_pct)}`} color={pctColor(selected.relative_pct)} />
              </div>
              <PlanRow k="SOURCE" v={data?.source || "performance curve"} color={accent2} />
              <PlanRow k="BENCHMARK" v={data?.benchmark || "SPY"} />
            </div>
            <div style={{ height: 300, border: hairline, background: "rgba(255,255,255,0.018)", padding: 10 }}>
              <ResponsiveContainer width="100%" height="100%">
                <LineChart data={pmDetailChartRows(rows, selected.date)} margin={{ top: 10, right: 12, left: 0, bottom: 0 }}>
                  <CartesianGrid stroke="rgba(255,255,255,0.055)" vertical={false} />
                  <XAxis dataKey="label" stroke={dim} tick={{ fill: muted, fontSize: 10 }} axisLine={false} tickLine={false} />
                  <YAxis stroke={dim} tick={{ fill: muted, fontSize: 10 }} axisLine={false} tickLine={false} tickFormatter={v => `${v}%`} />
                  <Tooltip contentStyle={{ background: "#050509", border: hairline, color: labelLight, fontSize: 11 }} formatter={(v, name) => [`${Number(v).toFixed(2)}%`, name]} />
                  <ReferenceLine y={0} stroke={dim} strokeDasharray="3 3" />
                  <Line type="monotone" dataKey="terminal" name="total fund" stroke={accent} strokeWidth={3} dot />
                  <Line type="monotone" dataKey="spy" name="SPY" stroke={accent2} strokeWidth={2} strokeDasharray="5 5" dot={false} />
                </LineChart>
              </ResponsiveContainer>
            </div>
          </div>
        ) : (
          <div style={{ color: muted, padding: 20 }}>Select a calendar day to inspect fund P/L.</div>
        )}
      </Card>
    </div>
  );
}

function TotalFundView({ data }) {
  const optionSummary = data.optionSummary || {};
  const equityRisk = Number(data.plannedEquityRisk || 0);
  const optionRisk = Number(data.optionReadyRisk || 0);
  const totalRisk = equityRisk + optionRisk;
  const totalCapital = Number(data.totalCapital || 0);
  const riskPct = totalCapital > 0 ? (totalRisk / totalCapital) * 100 : 0;
  const fundReady = Boolean(data.health?.ready_for_pm && data.optionsAccount?.ok);
  return (
    <>
      <div style={{ display: "flex", background: cardBg, border: hairline, marginBottom: 22, flexWrap: "wrap" }}>
        <Stat label="TOTAL FUND CAPITAL" value={`$${Math.round(totalCapital).toLocaleString()}`} sub="EQUITIES + OPTIONS" color={accent} accentBar />
        <Stat label="EQUITIES FUND" value={`$${Math.round(data.equityBasis || 0).toLocaleString()}`} sub={`${data.summary?.accumulate || 0} ACC / ${data.summary?.starter || 0} START`} color={accent2} />
        <Stat label="OPTIONS FUND" value={`$${Math.round(data.optionEquity || 0).toLocaleString()}`} sub={`${optionSummary.ready || 0} READY / ${optionSummary.total || 0} CAND`} color={accent} />
        <Stat label="TOTAL PLANNED RISK" value={`$${Math.round(totalRisk).toLocaleString()}`} sub={`${riskPct.toFixed(2)}% OF CAPITAL`} color="#fbbf24" />
        <Stat label="OPTIONS ROUTED" value={(optionSummary.option || 0) + (optionSummary.both || 0)} sub={`${optionSummary.both || 0} BOTH`} color="#4ade80" />
        <Stat label="FUND HEALTH" value={fundReady ? "READY" : "CHECK"} sub="PM / DESKS" color={fundReady ? "#4ade80" : "#f87171"} />
      </div>

      <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(320px, 1fr))", gap: 18 }}>
        <Card title="TOTAL CAPITAL SPLIT" accentColor={accent}>
          <PlanRow k="TOTAL CAPITAL" v={`$${Number(totalCapital || 0).toFixed(2)}`} color={accent} />
          <PlanRow k="EQUITIES CAPITAL" v={`$${Number(data.equityBasis || 0).toFixed(2)}`} color={accent2} />
          <PlanRow k="OPTIONS CAPITAL" v={`$${Number(data.optionEquity || 0).toFixed(2)}`} color={accent} />
          <PlanRow k="EQUITIES SHARE" v={`${totalCapital ? ((Number(data.equityBasis || 0) / totalCapital) * 100).toFixed(1) : "0.0"}%`} />
          <PlanRow k="OPTIONS SHARE" v={`${totalCapital ? ((Number(data.optionEquity || 0) / totalCapital) * 100).toFixed(1) : "0.0"}%`} />
        </Card>

        <Card title="TOTAL RISK ALLOCATION" accentColor="#fbbf24">
          <PlanRow k="TOTAL PLANNED RISK" v={`$${Number(totalRisk || 0).toFixed(2)}`} color="#fbbf24" />
          <PlanRow k="EQUITIES PLANNED RISK" v={`$${Number(equityRisk || 0).toFixed(2)}`} color={accent2} />
          <PlanRow k="OPTIONS READY RISK" v={`$${Number(optionRisk || 0).toFixed(2)}`} color={accent} />
          <PlanRow k="RISK / TOTAL CAPITAL" v={`${riskPct.toFixed(2)}%`} color={riskPct <= 3 ? "#4ade80" : riskPct <= 5 ? "#fbbf24" : "#f87171"} />
          <PlanRow k="OPTIONS OPEN POSITIONS" v={data.optionsPositions?.length || 0} />
        </Card>

        <Card title="TOTAL PM ROUTING" accentColor={accent2}>
          <PlanRow k="EQUITY ACCUMULATE" v={data.summary?.accumulate || 0} color="#4ade80" />
          <PlanRow k="EQUITY STARTERS" v={data.summary?.starter || 0} color={accent2} />
          <PlanRow k="OPTIONS ONLY" v={optionSummary.option || 0} color={accent} />
          <PlanRow k="BOTH DESKS" v={optionSummary.both || 0} color="#4ade80" />
          <PlanRow k="OPTIONS READY" v={optionSummary.ready || 0} color="#fbbf24" />
        </Card>

        <Card title="TOTAL SYSTEM CHECK" accentColor={fundReady ? "#4ade80" : "#f87171"}>
          <PlanRow k="PM ENGINE" v={data.health?.ready_for_pm ? "READY" : "BLOCKED"} color={data.health?.ready_for_pm ? "#4ade80" : "#f87171"} />
          <PlanRow k="TRADE FLOOR" v={data.health?.ready_for_trade_floor ? "READY" : "BLOCKED"} color={data.health?.ready_for_trade_floor ? "#4ade80" : "#f87171"} />
          <PlanRow k="OPTIONS DESK" v={data.optionsAccount?.ok ? "READY" : "BLOCKED"} color={data.optionsAccount?.ok ? "#4ade80" : "#f87171"} />
          <PlanRow k="OPTIONS PAPER" v={data.optionsAccount?.paper_only ? "YES" : "NO"} color={data.optionsAccount?.paper_only ? "#4ade80" : "#f87171"} />
          <PlanRow k="LATEST SCAN" v={data.health?.database?.latest_scan_at || "--"} color={accent2} />
        </Card>
      </div>
    </>
  );
}

function EquitiesFundView({ plan, optionsPlan, rows, filter, setFilter, summary, exposure, regime, rulesets, health, refreshRules, refreshHealth }) {
  const routeSummary = optionsPlan?.summary || {};
  return (
    <>
      <div style={{ display: "flex", background: cardBg, border: hairline, marginBottom: 22, flexWrap: "wrap" }}>
        <Stat label="EQUITY BASIS" value={`$${Math.round(summary.equity_basis || 0)}`} sub={(summary.equity_source || "fallback").toUpperCase()} color={accent} accentBar />
        <Stat label="PM MODE" value={summary.mode || plan?.mode || "—"} sub={`REGIME ${(regime.status || "UNKNOWN").toUpperCase()}`} color={regime.status === "red" || regime.status === "doomsday" ? "#f87171" : regime.status === "yellow" || regime.status === "downtrend" ? "#fbbf24" : accent2} />
        <Stat label="RULESET" value={plan?.ruleset?.name || "DEFAULT"} sub={plan?.ruleset?.ruleset_id || "PM"} color={accent} />
        <Stat label="ACCUMULATE" value={summary.accumulate || 0} sub="FULL RULE PASS" color="#4ade80" />
        <Stat label="STARTERS" value={summary.starter || 0} sub="PARTIAL SIZE" color={accent2} />
        <Stat label="PLANNED DEPLOY" value={`$${Math.round(summary.planned_deployment || 0)}`} sub="NOT ORDERS" color={labelLight} />
        <Stat label="PLANNED RISK" value={`$${Math.round(summary.planned_risk || 0)}`} sub="STOP-BASED" color="#fbbf24" />
      </div>

      <Card title="PM ROUTING SUMMARY - EQUITY VS OPTIONS" accentColor={accent2}>
        <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(160px, 1fr))", gap: 10 }}>
          <RouteTile label="EQUITY" value={routeSummary.equity || 0} color={accent2} />
          <RouteTile label="OPTION" value={routeSummary.option || 0} color={accent} />
          <RouteTile label="BOTH" value={routeSummary.both || 0} color="#4ade80" />
          <RouteTile label="PASS" value={routeSummary.pass || 0} color="#f87171" />
          <RouteTile label="MANUAL READY" value={routeSummary.ready || 0} color="#fbbf24" />
        </div>
      </Card>

      <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(280px, 1fr))", gap: 18, marginBottom: 22 }}>
        <Card title="EXPOSURE PIE - SECTOR" accentColor={accent2}>
          <ExposurePie data={exposure.by_sector || []} />
        </Card>
        <Card title="EXPOSURE PIE - ACTION" accentColor={accent}>
          <ExposurePie data={exposure.by_action || []} />
        </Card>
        <Card title="CASH PLAN" accentColor="#fbbf24">
          <PlanRow k="CAPITAL" v={`$${Number(summary.equity_basis || 0).toFixed(2)}`} />
          <PlanRow k="DEPLOYED PLAN" v={`$${Number(summary.planned_deployment || 0).toFixed(2)}`} />
          <PlanRow k="RESERVED CASH" v={`$${Number(summary.cash_reserved || 0).toFixed(2)}`} />
          <PlanRow k="MAX STOP RISK" v={`$${Number(summary.planned_risk || 0).toFixed(2)}`} color="#fbbf24" />
          <PlanRow k="TARGET UPSIDE" v={`$${Number(summary.target_upside_usd || 0).toFixed(2)}`} color="#4ade80" />
        </Card>
        <Card title="RISK SHOCK TEST" accentColor="#f87171">
          {(summary.shock_tests || []).length ? (
            (summary.shock_tests || []).map(test => (
              <PlanRow
                key={test.name}
                k={test.name}
                v={`-$${Number(test.loss_usd || 0).toFixed(2)} / ${Number(test.equity_pct || 0).toFixed(2)}%`}
                color="#f87171"
              />
            ))
          ) : (
            <div style={{ color: muted, padding: 20 }}>No active exposure to shock.</div>
          )}
        </Card>
      </div>

      <div style={{ display: "flex", borderBottom: hairline, marginBottom: 16, flexWrap: "wrap" }}>
        {["ACTIVE", "ACCUMULATE", "STARTER", "WATCH", "REJECT", "ALL"].map(k => (
          <button key={k} onClick={() => setFilter(k)}
            style={{
              background: "transparent", border: "none", padding: "10px 18px",
              color: filter === k ? accent : muted, cursor: "pointer",
              borderBottom: filter === k ? `2px solid ${accent}` : "2px solid transparent",
              fontSize: 11, letterSpacing: "0.14em", fontFamily: "JetBrains Mono", fontWeight: 700,
            }}>
            {k}
          </button>
        ))}
      </div>

      <Card title={`EQUITIES FUND - ${rows.length} ROWS`}>
        {!plan ? (
          <div style={{ color: muted, padding: 20 }}>Loading portfolio plan...</div>
        ) : !rows.length ? (
          <div style={{ color: muted, padding: 20 }}>No rows in this filter.</div>
        ) : (
          <div style={{ overflowX: "auto" }}>
            <table style={{ width: "100%", borderCollapse: "collapse", minWidth: 1120 }}>
              <thead>
                <tr>
                  <th style={th}>TICKER</th>
                  <th style={th}>ACTION</th>
                  <th style={th}>PM SCORE</th>
                  <th style={th}>ALLOC</th>
                  <th style={th}>RISK</th>
                  <th style={th}>PRICE</th>
                  <th style={th}>TARGET</th>
                  <th style={th}>STOP</th>
                  <th style={th}>RATCHET</th>
                  <th style={th}>R/R</th>
                  <th style={th}>OPTIONS</th>
                  <th style={th}>REASONS</th>
                </tr>
              </thead>
              <tbody>
                {rows.map(r => (
                  <tr key={r.ticker} style={{ borderTop: hairline }}>
                    <td style={{ ...td, color: accent, fontWeight: 800 }}>${r.ticker}</td>
                    <td style={{ ...td, color: ACTION_COLOR[r.action] || labelLight, fontWeight: 800 }}>{r.action}</td>
                    <td style={{ ...td, color: r.pm_score >= 70 ? "#4ade80" : r.pm_score >= 58 ? accent2 : r.pm_score >= 45 ? "#fbbf24" : "#f87171", fontWeight: 800 }}>
                      {r.pm_score?.toFixed ? r.pm_score.toFixed(1) : r.pm_score}
                    </td>
                    <td style={td}>${Number(r.allocation_usd || 0).toFixed(2)}<br /><span style={{ color: muted }}>{Number(r.shares || 0).toFixed(4)} sh</span></td>
                    <td style={{ ...td, color: "#fbbf24", fontWeight: 700 }}>${Number(r.risk_usd || 0).toFixed(2)}<br /><span style={{ color: muted }}>{Number(r.position_pct || 0).toFixed(2)}%</span></td>
                    <td style={td}>${Number(r.price || 0).toFixed(2)}</td>
                    <td style={{ ...td, color: "#4ade80" }}>${Number(r.target || 0).toFixed(2)}<br /><span style={{ color: muted }}>{Number(r.upside_pct || 0).toFixed(1)}%</span></td>
                    <td style={{ ...td, color: "#f87171" }}>${Number(r.stop || 0).toFixed(2)}<br /><span style={{ color: muted }}>{Number(r.downside_pct || 0).toFixed(1)}%</span></td>
                    <td style={{ ...td, color: r.ratchet_plan?.enabled ? accent : muted, fontWeight: 700 }}>
                      {r.ratchet_plan?.enabled ? r.ratchet_plan.profile : "OFF"}
                      {r.ratchet_plan?.enabled && (
                        <><br /><span style={{ color: muted }}>
                          TP +{Number(r.ratchet_plan.initial_target_pct || 0).toFixed(1)}% / SL -{Number(r.ratchet_plan.initial_stop_pct || 0).toFixed(1)}%
                        </span></>
                      )}
                    </td>
                    <td style={{ ...td, color: Number(r.risk_reward || 0) >= 1.8 ? "#4ade80" : "#fbbf24", fontWeight: 800 }}>{Number(r.risk_reward || 0).toFixed(2)}</td>
                    <td style={{ ...td, color: accent2, fontWeight: 700 }}>{r.option_view}</td>
                    <td style={{ ...td, minWidth: 260 }}>
                      {(r.reasons || []).slice(0, 3).map((x, i) => (
                        <div key={`r-${i}`} style={{ color: i === 0 ? labelLight : muted, marginBottom: 4 }}>{x}</div>
                      ))}
                      {(r.cautions || []).slice(0, 2).map((x, i) => (
                        <div key={`c-${i}`} style={{ color: "#fbbf24", marginBottom: 4 }}>{x}</div>
                      ))}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Card>

      <Card title="EQUITIES FUND RULE BOOK">
        <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(220px, 1fr))", gap: 12 }}>
          {Object.entries(plan?.rules?.actions || {}).map(([k, v]) => (
            <div key={k} style={{ border: hairline, padding: 12, background: "#050509" }}>
              <div style={{ color: ACTION_COLOR[k] || accent, fontSize: 11, fontWeight: 800, letterSpacing: "0.14em" }}>{k}</div>
              <div style={{ color: muted, fontSize: 11, marginTop: 8, lineHeight: 1.6 }}>{v}</div>
            </div>
          ))}
        </div>
      </Card>

      <Card title="RATCHET RULES">
        <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(220px, 1fr))", gap: 12 }}>
          <RatchetRule name="TACTICAL" text="Lower-upside names: tighter +10% TP / -7% SL, then 3% price steps raise stop 3% and target 5%." />
          <RatchetRule name="CORE" text="Mid/high-quality names: +15% TP / -10% SL, then every +5% move raises stop 5% and target 10%." />
          <RatchetRule name="RUNNER" text="High-upside or volatile names: +25% TP / -15% SL, then +10% steps raise stop 7.5% and target 18%." />
        </div>
      </Card>

      <FundGovernanceView
        fund="EQUITIES"
        rulesets={rulesets}
        health={health}
        refreshRules={refreshRules}
        refreshHealth={refreshHealth}
      />
    </>
  );
}

function OptionsFundView({ optionsPlan, optionsAccount, positions, orders, rulesets, health, refreshOptions }) {
  const summary = optionsPlan?.summary || {};
  const candidates = optionsPlan?.candidates || [];
  const account = optionsAccount?.account || {};
  const riskPolicy = candidates.find(c => c.risk_policy)?.risk_policy || {};
  const optionsHealthOk = Boolean(optionsAccount?.ok && optionsAccount?.paper_only);
  return (
    <>
      <div style={{ display: "flex", background: cardBg, border: hairline, marginBottom: 22, flexWrap: "wrap" }}>
        <Stat label="OPTIONS EQUITY" value={`$${Number(account.equity || 20000).toLocaleString()}`} sub={optionsAccount?.configured ? "SEPARATE PAPER" : "KEYS MISSING"} color={accent} accentBar />
        <Stat label="BUYING POWER" value={`$${Number(account.buying_power || 0).toLocaleString()}`} sub={(account.status || "UNKNOWN").toUpperCase()} color={optionsHealthOk ? "#4ade80" : "#f87171"} />
        <Stat label="CANDIDATES" value={summary.total || 0} sub={`${summary.ready || 0} MANUAL READY`} color={accent2} />
        <Stat label="OPTION" value={summary.option || 0} sub="PM ROUTED" color={accent} />
        <Stat label="BOTH" value={summary.both || 0} sub="HIGH CONVICTION" color="#4ade80" />
        <Stat label="OPEN POS" value={positions.length || 0} sub={`${orders.length || 0} ORDERS`} color="#fbbf24" />
      </div>

      <Card title="OPTIONS FUND ROUTING" accentColor={accent2}>
        <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(160px, 1fr))", gap: 10 }}>
          <RouteTile label="EQUITY ONLY" value={summary.equity || 0} color={accent2} />
          <RouteTile label="OPTION" value={summary.option || 0} color={accent} />
          <RouteTile label="BOTH" value={summary.both || 0} color="#4ade80" />
          <RouteTile label="PASS" value={summary.pass || 0} color="#f87171" />
          <RouteTile label="MANUAL READY" value={summary.ready || 0} color="#fbbf24" />
        </div>
      </Card>

      <div style={{ display: "grid", gridTemplateColumns: "minmax(0, 1.25fr) minmax(320px, 0.75fr)", gap: 18, alignItems: "start" }}>
        <Card title="OPTIONS FUND CANDIDATE QUEUE">
          <OptionsDecisionTable rows={candidates} />
        </Card>
        <Card title="OPTIONS FUND RULES + HEALTH" accentColor={optionsHealthOk ? "#4ade80" : "#f87171"}>
          <PlanRow k="ACCOUNT" v={optionsHealthOk ? "PAPER READY" : "BLOCKED"} color={optionsHealthOk ? "#4ade80" : "#f87171"} />
          <PlanRow k="BASE" v={optionsAccount?.paper_only ? "PAPER ONLY" : "NOT PAPER"} color={optionsAccount?.paper_only ? "#4ade80" : "#f87171"} />
          <PlanRow k="MAX RISK" v={`$${Number(riskPolicy.max_risk_usd || 400).toFixed(0)} / ${Number((riskPolicy.max_risk_pct || 0.02) * 100).toFixed(1)}%`} color="#fbbf24" />
          <PlanRow k="SCOUT RISK" v={`$${Number(riskPolicy.scout_risk_usd || 100).toFixed(0)}`} />
          <PlanRow k="STARTER RISK" v={`$100-$${Number(riskPolicy.starter_risk_usd || 175).toFixed(0)}`} />
          <PlanRow k="STANDARD RISK" v={`$${Number(riskPolicy.standard_risk_usd || 250).toFixed(0)}`} />
          <PlanRow k="HIGH CONVICTION" v={`$${Number(riskPolicy.max_risk_usd || 400).toFixed(0)}`} />
          <PlanRow k="MANUAL FIRE" v="REQUIRED" color={accent} />
          <PlanRow k="PM ROUTE" v="OPTION OR BOTH ONLY" color={accent2} />
          <PlanRow k="LIQUIDITY GATES" v="SPREAD % / OI / VOLUME" />
          <PlanRow k="REASON" v={optionsAccount?.reason || "OK"} color={optionsHealthOk ? "#4ade80" : "#f87171"} />
          <button onClick={refreshOptions} style={{
            width: "100%", marginTop: 12,
            background: "transparent", border: `0.5px solid ${accent}`,
            color: accent, fontSize: 11, padding: "8px 16px",
            cursor: "pointer", letterSpacing: "0.14em",
            fontFamily: "JetBrains Mono", fontWeight: 700,
          }}>REFRESH OPTIONS FUND</button>
        </Card>
      </div>

      <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(320px, 1fr))", gap: 18 }}>
        <Card title="OPTIONS OPEN POSITIONS" accentColor={accent}>
          <SimpleOptionsList rows={positions} empty="No open options positions." />
        </Card>
        <Card title="OPTIONS ORDERS" accentColor={accent2}>
          <SimpleOptionsList rows={orders} empty="No options orders yet." />
        </Card>
      </div>

      <FundGovernanceView fund="OPTIONS" rulesets={rulesets} health={health} optionsAccount={optionsAccount} />
    </>
  );
}

function SimpleOptionsList({ rows, empty }) {
  if (!rows.length) return <div style={{ color: muted, padding: 20 }}>{empty}</div>;
  return (
    <div style={{ display: "grid", gap: 8 }}>
      {rows.slice(0, 12).map((r, i) => (
        <div key={r.id || r.symbol || i} style={{ border: hairline, background: "#050509", padding: 10 }}>
          <div style={{ display: "flex", justifyContent: "space-between", gap: 12 }}>
            <span style={{ color: accent, fontWeight: 900 }}>{r.symbol || r.asset_id || "OPTION"}</span>
            <span style={{ color: labelLight }}>{r.status || r.qty || r.side || "-"}</span>
          </div>
          <div style={{ color: muted, fontSize: 11, marginTop: 6 }}>
            {r.type || r.order_type || r.asset_class || "options"} / {r.filled_avg_price || r.avg_entry_price || r.limit_price || "-"}
          </div>
        </div>
      ))}
    </div>
  );
}

function FundGovernanceView({ fund, rulesets, health, optionsAccount, refreshRules, refreshHealth }) {
  const isOptions = fund === "OPTIONS";
  const active = rulesets?.rulesets?.find(r => r.active) || null;
  const healthOk = isOptions ? Boolean(optionsAccount?.ok && optionsAccount?.paper_only) : Boolean(health?.alpaca?.ok);
  return (
    <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(320px, 1fr))", gap: 18 }}>
      <Card title={`${fund} PM RULES`} accentColor={accent}>
        {!rulesets ? (
          <div style={{ color: muted, padding: 20 }}>Loading PM rules...</div>
        ) : (
          <>
            <PlanRow k="ACTIVE RULESET" v={active?.name || rulesets.active_ruleset_id || "DEFAULT"} color={accent} />
            <PlanRow k="RULESET ID" v={active?.ruleset_id || rulesets.active_ruleset_id || "--"} />
            <PlanRow k="SCOPE" v={isOptions ? "ROUTING + OPTIONS PREFLIGHT" : "EQUITY SIZING + TRADE FLOOR"} color={accent2} />
            <PlanRow k="MODE SOURCE" v="PM AUTO / OVERRIDE" />
            <PlanRow k="DESCRIPTION" v={active?.description || "Default PM rules"} />
            {refreshRules && (
              <button onClick={refreshRules} style={{
                width: "100%", marginTop: 12,
                background: "transparent", border: `0.5px solid ${accent}`,
                color: accent, fontSize: 11, padding: "8px 16px",
                cursor: "pointer", letterSpacing: "0.14em",
                fontFamily: "JetBrains Mono", fontWeight: 700,
              }}>REFRESH PM RULES</button>
            )}
          </>
        )}
      </Card>
      <Card title={`${fund} SYSTEM HEALTH`} accentColor={healthOk ? "#4ade80" : "#f87171"}>
        {isOptions ? (
          <>
            <PlanRow k="OPTIONS ACCOUNT" v={optionsAccount?.ok ? "READY" : "BLOCKED"} color={optionsAccount?.ok ? "#4ade80" : "#f87171"} />
            <PlanRow k="PAPER ONLY" v={optionsAccount?.paper_only ? "YES" : "NO"} color={optionsAccount?.paper_only ? "#4ade80" : "#f87171"} />
            <PlanRow k="CONFIGURED" v={optionsAccount?.configured ? "YES" : "NO"} color={optionsAccount?.configured ? "#4ade80" : "#f87171"} />
            <PlanRow k="ACCOUNT STATUS" v={optionsAccount?.account?.status || "--"} />
            <PlanRow k="REASON" v={optionsAccount?.reason || "OK"} color={healthOk ? "#4ade80" : "#f87171"} />
          </>
        ) : !health ? (
          <div style={{ color: muted, padding: 20 }}>Loading equity health...</div>
        ) : (
          <>
            <PlanRow k="TRADE FLOOR" v={health.ready_for_trade_floor ? "READY" : "BLOCKED"} color={health.ready_for_trade_floor ? "#4ade80" : "#f87171"} />
            <PlanRow k="ALPACA" v={health.alpaca?.ok ? "ACCOUNT OK" : "BLOCKED"} color={health.alpaca?.ok ? "#4ade80" : "#f87171"} />
            <PlanRow k="SCANNING" v={health.ready_for_scanning ? "READY" : "BLOCKED"} color={health.ready_for_scanning ? "#4ade80" : "#f87171"} />
            <PlanRow k="PM" v={health.ready_for_pm ? "READY" : "BLOCKED"} color={health.ready_for_pm ? "#4ade80" : "#f87171"} />
            <PlanRow k="LATEST SCAN" v={health.database?.latest_scan_at || "--"} color={accent2} />
            {refreshHealth && (
              <button onClick={refreshHealth} style={{
                width: "100%", marginTop: 12,
                background: "transparent", border: `0.5px solid ${accent2}`,
                color: accent2, fontSize: 11, padding: "8px 16px",
                cursor: "pointer", letterSpacing: "0.14em",
                fontFamily: "JetBrains Mono", fontWeight: 700,
              }}>REFRESH HEALTH</button>
            )}
          </>
        )}
      </Card>
    </div>
  );
}

function RouteTile({ label, value, color }) {
  return (
    <div style={{ border: `0.5px solid ${color}55`, background: `${color}0d`, padding: 12 }}>
      <div style={{ color: dim, fontSize: 10, letterSpacing: "0.14em" }}>{label}</div>
      <div style={{ color, fontSize: 24, fontWeight: 900, marginTop: 6 }}>{value}</div>
    </div>
  );
}

function ExposurePie({ data }) {
  if (!data.length) return <div style={{ color: muted, padding: 20 }}>No active exposure.</div>;
  return (
    <div style={{ display: "grid", gridTemplateColumns: "160px 1fr", minHeight: 160, gap: 14, alignItems: "center" }}>
      <ResponsiveContainer width="100%" height={160}>
        <PieChart>
          <Pie data={data} dataKey="value" nameKey="name" innerRadius={42} outerRadius={68} stroke="#050509" strokeWidth={2}>
            {data.map((_, i) => <Cell key={i} fill={PIE_COLORS[i % PIE_COLORS.length]} />)}
          </Pie>
          <Tooltip
            contentStyle={{ background: "#050509", border: `0.5px solid ${dim}`, color: labelLight, fontSize: 11 }}
            formatter={(value, name) => [`$${Number(value).toFixed(2)}`, name]}
          />
        </PieChart>
      </ResponsiveContainer>
      <div>
        {data.map((d, i) => (
          <div key={d.name} style={{ display: "grid", gridTemplateColumns: "10px 1fr auto", gap: 8, alignItems: "center", padding: "5px 0", borderBottom: hairline }}>
            <span style={{ width: 9, height: 9, background: PIE_COLORS[i % PIE_COLORS.length], display: "inline-block" }} />
            <span style={{ color: labelLight, fontSize: 11 }}>{d.name}</span>
            <span style={{ color: muted, fontSize: 11 }}>{d.pct}%</span>
          </div>
        ))}
      </div>
    </div>
  );
}

function LearningHubView({ active, setActive, equityLearning, optionsLearning }) {
  return (
    <>
      <div style={{
        display: "flex",
        border: hairline,
        background: cardBg,
        marginBottom: 16,
        width: "fit-content",
        maxWidth: "100%",
        flexWrap: "wrap",
      }}>
        {["EQUITIES", "OPTIONS"].map(k => (
          <button key={k} onClick={() => setActive(k)}
            style={{
              background: active === k ? `${accent}12` : "transparent",
              border: "none",
              borderRight: k === "EQUITIES" ? hairline : "none",
              color: active === k ? accent : muted,
              cursor: "pointer",
              padding: "10px 22px",
              fontSize: 11,
              letterSpacing: "0.14em",
              fontFamily: "JetBrains Mono",
              fontWeight: 800,
            }}>
            {k}
          </button>
        ))}
      </div>
      {active === "EQUITIES"
        ? <PMLearningView learning={equityLearning} />
        : <OptionsLearningView learning={optionsLearning} />}
    </>
  );
}

function PMLearningView({ learning }) {
  if (!learning) {
    return <Card title="PM LEARNING ENGINE"><div style={{ color: muted, padding: 20 }}>Loading PM learning state...</div></Card>;
  }
  const actionRows = learning.action_stats || [];
  const sectorRows = learning.sector_stats || [];
  const signalRows = learning.signal_stats || [];
  return (
    <>
      <div style={{ display: "flex", background: cardBg, border: hairline, marginBottom: 22, flexWrap: "wrap" }}>
        <Stat label="PHASE" value={(learning.phase || "—").toUpperCase()} sub="PM ENGINE" color={accent} accentBar />
        <Stat label="SAMPLES" value={learning.samples || 0} sub={`FULL ${learning.min_full_samples}`} color={accent2} />
        <Stat label="PENDING" value={learning.pending_outcomes || 0} sub="AWAIT RETURNS" color="#fbbf24" />
        <Stat label="DECISIONS" value={learning.reconstructed_decisions || 0} sub="REBUILT FROM SCANS" color={labelLight} />
      </div>

      <div style={{
        padding: "14px 18px", border: `0.5px solid ${accent}`,
        background: `${accent}10`, color: accent, fontSize: 11,
        letterSpacing: "0.1em", marginBottom: 16,
      }}>
        PM LEARNING IS SEPARATE FROM TRADE FLOOR LEARNING: it grades PM decisions, thresholds, modes, and sizing rules.
      </div>

      <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(320px, 1fr))", gap: 18 }}>
        <Card title="ACTION PERFORMANCE" accentColor={accent2}>
          <LearningTable rows={actionRows} label="ACTION" />
        </Card>
        <Card title="SECTOR PERFORMANCE" accentColor={accent}>
          <LearningTable rows={sectorRows.slice(0, 8)} label="SECTOR" />
        </Card>
      </div>

      <Card title="SIGNAL PERFORMANCE - PM CONTEXT">
        <LearningTable rows={signalRows.slice(0, 12)} label="SIGNAL" />
      </Card>

      <Card title="LATEST PM DECISIONS">
        <table style={{ width: "100%", borderCollapse: "collapse" }}>
          <thead>
            <tr>
              <th style={th}>TICKER</th>
              <th style={th}>ACTION</th>
              <th style={th}>PM SCORE</th>
              <th style={th}>R/R</th>
              <th style={th}>ALLOC</th>
              <th style={th}>OUTCOME</th>
            </tr>
          </thead>
          <tbody>
            {(learning.latest_decisions || []).map(r => (
              <tr key={`${r.ticker}-${r.action}`} style={{ borderTop: hairline }}>
                <td style={{ ...td, color: accent, fontWeight: 800 }}>${r.ticker}</td>
                <td style={{ ...td, color: ACTION_COLOR[r.action] || labelLight, fontWeight: 800 }}>{r.action}</td>
                <td style={td}>{Number(r.pm_score || 0).toFixed(1)}</td>
                <td style={{ ...td, color: r.allocated_risk_reward == null ? muted : Number(r.allocated_risk_reward) >= 1.8 ? "#4ade80" : "#fbbf24", fontWeight: 800 }}>
                  {r.allocated_risk_reward == null ? "—" : Number(r.allocated_risk_reward).toFixed(2)}
                </td>
                <td style={td}>${Number(r.allocation_usd || 0).toFixed(2)}</td>
                <td style={{ ...td, color: r.outcome_return == null ? muted : r.outcome_return >= 0 ? "#4ade80" : "#f87171", fontWeight: 700 }}>
                  {r.outcome_return == null ? "PENDING" : `${r.outcome_return >= 0 ? "+" : ""}${Number(r.outcome_return).toFixed(2)}% ${r.outcome_basis || ""}`}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </Card>

      <Card title="LEARNING RECOMMENDATIONS">
        {(learning.recommendations || []).length ? (
          (learning.recommendations || []).map((x, i) => (
            <div key={i} style={{ color: labelLight, padding: "8px 0", borderBottom: hairline, fontSize: 12 }}>{x}</div>
          ))
        ) : (
          <div style={{ color: muted, padding: 20 }}>
            No threshold changes recommended yet. The engine needs matured outcome samples before it should adjust PM behavior.
          </div>
        )}
      </Card>
    </>
  );
}

function OptionsLearningView({ learning }) {
  if (!learning) {
    return <Card title="OPTIONS LEARNING"><div style={{ color: muted, padding: 20 }}>Loading options learning state...</div></Card>;
  }
  const routes = learning.route_counts || {};
  return (
    <>
      <div style={{ display: "flex", background: cardBg, border: hairline, marginBottom: 22, flexWrap: "wrap" }}>
        <Stat label="PHASE" value={(learning.phase || "PRE_EXECUTION").toUpperCase()} sub="OPTIONS PM" color={accent} accentBar />
        <Stat label="ORDERS" value={learning.orders || 0} sub="PAPER DATA" color={accent2} />
        <Stat label="READY" value={learning.ready_candidates || 0} sub="MANUAL FIRE" color="#4ade80" />
        <Stat label="OPTION/BOTH" value={(routes.OPTION || 0) + (routes.BOTH || 0)} sub="PM ROUTED" color="#fbbf24" />
      </div>
      <Card title="OPTIONS ROUTE COUNTS" accentColor={accent2}>
        <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(160px, 1fr))", gap: 10 }}>
          <RouteTile label="EQUITY" value={routes.EQUITY || 0} color={accent2} />
          <RouteTile label="OPTION" value={routes.OPTION || 0} color={accent} />
          <RouteTile label="BOTH" value={routes.BOTH || 0} color="#4ade80" />
          <RouteTile label="PASS" value={routes.PASS || 0} color="#f87171" />
        </div>
      </Card>
      <Card title="LATEST OPTIONS PM DECISIONS">
        <OptionsDecisionTable rows={learning.latest_decisions || []} />
      </Card>
      <Card title="OPTIONS LEARNING RECOMMENDATIONS">
        {(learning.recommendations || []).map((x, i) => (
          <div key={i} style={{ color: labelLight, padding: "8px 0", borderBottom: hairline, fontSize: 12 }}>{x}</div>
        ))}
      </Card>
    </>
  );
}

function OptionsBacktestView({ backtest }) {
  const summary = backtest?.summary || {};
  return (
    <>
      <div style={{ display: "flex", background: cardBg, border: hairline, marginBottom: 22, flexWrap: "wrap" }}>
        <Stat label="METHOD" value={(backtest?.method || "V1").toUpperCase()} sub="OPTIONS REPLAY" color={accent} accentBar />
        <Stat label="CANDIDATES" value={summary.total || 0} sub={`${summary.ready || 0} READY`} color={accent2} />
        <Stat label="RISK READY" value={`$${Number(summary.risk_budget_ready || 0).toFixed(0)}`} sub={`${Number(summary.risk_pct || 0).toFixed(2)}% BASIS`} color="#fbbf24" />
        <Stat label="EQUITY BASIS" value={`$${Number(summary.equity_basis || 20000).toLocaleString()}`} sub="OPTIONS DESK" color={labelLight} />
      </div>
      <Card title="OPTIONS BACKTEST NOTE" accentColor="#fbbf24">
        <div style={{ color: muted, fontSize: 12, lineHeight: 1.7 }}>
          {backtest?.note || "Full historical options P&L requires stored option-chain snapshots or executed paper outcomes."}
        </div>
      </Card>
      <Card title="OPTIONS REPLAY SAMPLE">
        <OptionsDecisionTable rows={backtest?.sample_rows || []} />
      </Card>
    </>
  );
}

function OptionsDecisionTable({ rows }) {
  if (!rows.length) return <div style={{ color: muted, padding: 20 }}>No options decisions available.</div>;
  return (
    <div style={{ overflowX: "auto" }}>
      <table style={{ width: "100%", borderCollapse: "collapse", minWidth: 980 }}>
        <thead>
          <tr>
            <th style={th}>TICKER</th>
            <th style={th}>ROUTE</th>
            <th style={th}>STRATEGY</th>
            <th style={th}>PM</th>
            <th style={th}>R/R</th>
            <th style={th}>RISK</th>
            <th style={th}>DATA</th>
            <th style={th}>CONTRACTS</th>
            <th style={th}>READY</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((r, i) => (
            <tr key={`${r.candidate_id || r.ticker}-${i}`} style={{ borderTop: hairline }}>
              <td style={{ ...td, color: accent, fontWeight: 900 }}>${r.ticker}</td>
              <td style={{ ...td, color: r.route === "BOTH" ? "#4ade80" : r.route === "OPTION" ? accent : r.route === "EQUITY" ? accent2 : "#f87171", fontWeight: 900 }}>{r.route}</td>
              <td style={td}>{r.strategy || "-"}</td>
              <td style={td}>{Number(r.pm_score || 0).toFixed(1)}</td>
              <td style={td}>{Number(r.risk_reward || 0).toFixed(2)}</td>
              <td style={{ ...td, color: "#fbbf24" }}>${Number(r.risk_budget || 0).toFixed(2)}</td>
              <td style={{ ...td, color: r.data_provider === "ALPACA_OPTIONS" ? accent2 : "#fbbf24", fontWeight: 800 }}>
                {(r.data_provider || r.instrument?.data_provider || "UNKNOWN").replace("_OPTIONS", "")}
                <br /><span style={{ color: muted }}>{r.data_quality || r.instrument?.data_quality || "-"}</span>
              </td>
              <td style={td}>{r.contracts || 0}</td>
              <td style={{ ...td, color: r.manual_fire_ready ? "#4ade80" : "#f87171" }}>{r.manual_fire_ready ? "YES" : "NO"}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function BacktestHubView({ active, setActive, equityBacktest, optionsBacktest, sandbox, setSandbox, loadBacktest, loading }) {
  return (
    <>
      <div style={{
        display: "flex",
        border: hairline,
        background: cardBg,
        marginBottom: 16,
        width: "fit-content",
        maxWidth: "100%",
        flexWrap: "wrap",
      }}>
        {["EQUITIES", "OPTIONS"].map(k => (
          <button key={k} onClick={() => setActive(k)}
            style={{
              background: active === k ? `${accent}12` : "transparent",
              border: "none",
              borderRight: k === "EQUITIES" ? hairline : "none",
              color: active === k ? accent : muted,
              cursor: "pointer",
              padding: "10px 22px",
              fontSize: 11,
              letterSpacing: "0.14em",
              fontFamily: "JetBrains Mono",
              fontWeight: 800,
            }}>
            {k}
          </button>
        ))}
      </div>
      {active === "EQUITIES"
        ? <PMBacktestView
            backtest={equityBacktest}
            sandbox={sandbox}
            setSandbox={setSandbox}
            loadBacktest={loadBacktest}
            loading={loading}
          />
        : <OptionsBacktestView backtest={optionsBacktest} />}
    </>
  );
}

function PMBacktestView({ backtest, sandbox, setSandbox, loadBacktest, loading }) {
  const summary = backtest?.summary || {};
  const setField = (key, value) => setSandbox(prev => ({ ...prev, [key]: value }));
  return (
    <>
      <div style={{ display: "flex", background: cardBg, border: hairline, marginBottom: 22, flexWrap: "wrap" }}>
        <Stat label="SCANS" value={summary.scans || 0} sub="REPLAYED" color={accent} accentBar />
        <Stat label="DECISIONS" value={summary.decisions || 0} sub={`${summary.allocated || 0} ALLOCATED`} color={accent2} />
        <Stat label="MATURED" value={summary.matured || 0} sub={`${summary.pending || 0} PENDING`} color="#fbbf24" />
        <Stat label="WIN RATE" value={summary.win_rate == null ? "--" : `${(summary.win_rate * 100).toFixed(0)}%`} sub="MATURED ONLY" color={Number(summary.win_rate || 0) >= 0.5 ? "#4ade80" : "#f87171"} />
        <Stat label="AVG RETURN" value={`${Number(summary.avg_return || 0) >= 0 ? "+" : ""}${Number(summary.avg_return || 0).toFixed(2)}%`} sub="MATURED ONLY" color={Number(summary.avg_return || 0) >= 0 ? "#4ade80" : "#f87171"} />
        <Stat label="P/L TEST" value={`${Number(summary.pnl_usd || 0) >= 0 ? "+" : "-"}$${Math.abs(Number(summary.pnl_usd || 0)).toFixed(2)}`} sub="SANDBOX NOT LIVE" color={Number(summary.pnl_usd || 0) >= 0 ? "#4ade80" : "#f87171"} />
        <Stat label="SIM EXITS" value={summary.exit_simulation?.simulated || 0} sub={`${summary.exit_simulation?.target_hits || 0} TARGET / ${summary.exit_simulation?.stop_hits || 0} STOP`} color={accent} />
      </div>

      <Card title="SANDBOX RULE TESTER" accentColor={accent2}>
        <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(150px, 1fr))", gap: 10 }}>
          <SandboxSelect label="MODE" value={sandbox.mode} onChange={v => setField("mode", v)} />
          <SandboxInput label="EQUITY" value={sandbox.equity} onChange={v => setField("equity", v)} />
          <SandboxInput label="GROSS CAP" value={sandbox.max_gross_deployment_pct} onChange={v => setField("max_gross_deployment_pct", v)} />
          <SandboxInput label="MAX POS" value={sandbox.max_position_pct} onChange={v => setField("max_position_pct", v)} />
          <SandboxInput label="NAME RISK" value={sandbox.max_single_name_risk_pct} onChange={v => setField("max_single_name_risk_pct", v)} />
          <SandboxInput label="ACC SCORE" value={sandbox.accumulate_score} onChange={v => setField("accumulate_score", v)} />
          <SandboxInput label="ACC R/R" value={sandbox.accumulate_rr} onChange={v => setField("accumulate_rr", v)} />
          <SandboxInput label="START SCORE" value={sandbox.starter_score} onChange={v => setField("starter_score", v)} />
          <SandboxInput label="START R/R" value={sandbox.starter_rr} onChange={v => setField("starter_rr", v)} />
          <SandboxInput label="WATCH SCORE" value={sandbox.watch_score} onChange={v => setField("watch_score", v)} />
        </div>
        <div style={{ display: "flex", justifyContent: "space-between", gap: 12, alignItems: "center", marginTop: 14, flexWrap: "wrap" }}>
          <div style={{ color: muted, fontSize: 11, letterSpacing: "0.08em" }}>
            REPLAYS SAVED SCANS THROUGH PM LOGIC ONLY. NO ORDERS, NO CLAUDE.
          </div>
          <button onClick={loadBacktest} disabled={loading}
            style={{
              background: "transparent", border: `0.5px solid ${accent}`,
              color: loading ? muted : accent, fontSize: 11, padding: "8px 16px",
              cursor: loading ? "default" : "pointer", letterSpacing: "0.14em",
              fontFamily: "JetBrains Mono", fontWeight: 700,
            }}>
            {loading ? "TESTING" : "RUN SANDBOX"}
          </button>
        </div>
      </Card>

      <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(320px, 1fr))", gap: 18 }}>
        <Card title="ACTION BACKTEST" accentColor={accent}>
          <BacktestTable rows={backtest?.action_stats || []} label="ACTION" />
        </Card>
        <Card title="RATCHET BACKTEST" accentColor={accent2}>
          <BacktestTable rows={backtest?.ratchet_stats || []} label="RATCHET" />
        </Card>
      </div>

      <Card title="EXIT / RATCHET SIMULATION" accentColor="#4ade80">
        <div style={{ color: muted, fontSize: 11, marginBottom: 10, lineHeight: 1.6 }}>
          {summary.exit_simulation_note || "Simulation appears when matured returns are available."}
        </div>
        <SimTable rows={backtest?.exit_simulation_by_ratchet || []} />
      </Card>

      <Card title="SAMPLE PM BACKTEST DECISIONS">
        {!backtest ? (
          <div style={{ color: muted, padding: 20 }}>Run the sandbox to replay PM history.</div>
        ) : (
          <div style={{ overflowX: "auto" }}>
            <table style={{ width: "100%", borderCollapse: "collapse", minWidth: 980 }}>
              <thead>
                <tr>
                  <th style={th}>DATE</th>
                  <th style={th}>TICKER</th>
                  <th style={th}>ACTION</th>
                  <th style={th}>SCORE</th>
                  <th style={th}>R/R</th>
                  <th style={th}>ALLOC</th>
                  <th style={th}>RATCHET</th>
                  <th style={th}>OUTCOME</th>
                  <th style={th}>SIM EXIT</th>
                </tr>
              </thead>
              <tbody>
                {(backtest.sample_decisions || []).map((r, i) => (
                  <tr key={`${r.scan_date}-${r.ticker}-${i}`} style={{ borderTop: hairline }}>
                    <td style={td}>{r.scan_date}</td>
                    <td style={{ ...td, color: accent, fontWeight: 800 }}>${r.ticker}</td>
                    <td style={{ ...td, color: ACTION_COLOR[r.action] || labelLight, fontWeight: 800 }}>{r.action}</td>
                    <td style={td}>{Number(r.pm_score || 0).toFixed(1)}</td>
                    <td style={{ ...td, color: Number(r.risk_reward || 0) >= 1.8 ? "#4ade80" : "#fbbf24", fontWeight: 800 }}>{Number(r.risk_reward || 0).toFixed(2)}</td>
                    <td style={td}>${Number(r.allocation_usd || 0).toFixed(2)}</td>
                    <td style={{ ...td, color: r.ratchet_profile === "OFF" ? muted : accent2, fontWeight: 700 }}>{r.ratchet_profile}</td>
                    <td style={{ ...td, color: r.outcome_return == null ? muted : r.outcome_return >= 0 ? "#4ade80" : "#f87171", fontWeight: 700 }}>
                      {r.outcome_return == null ? "PENDING" : `${r.outcome_return >= 0 ? "+" : ""}${Number(r.outcome_return).toFixed(2)}% ${r.outcome_basis || ""}`}
                    </td>
                    <td style={{ ...td, color: r.sim_exit ? accent2 : muted, fontWeight: 700 }}>
                      {r.sim_exit ? `${r.sim_exit.exit_reason} ${Number(r.sim_exit.sim_return_pct || 0).toFixed(2)}%` : "PENDING"}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Card>
    </>
  );
}

function SandboxInput({ label, value, onChange }) {
  return (
    <label style={{ display: "grid", gap: 6 }}>
      <span style={{ color: dim, fontSize: 10, letterSpacing: "0.14em" }}>{label}</span>
      <input
        value={value}
        onChange={e => onChange(e.target.value)}
        style={{
          background: "#050509", border: `0.5px solid ${dim}`,
          color: labelLight, padding: "8px 10px", fontSize: 11,
          fontFamily: "JetBrains Mono", letterSpacing: "0.06em",
        }}
      />
    </label>
  );
}

function SandboxSelect({ label, value, onChange }) {
  return (
    <label style={{ display: "grid", gap: 6 }}>
      <span style={{ color: dim, fontSize: 10, letterSpacing: "0.14em" }}>{label}</span>
      <select
        value={value}
        onChange={e => onChange(e.target.value)}
        style={{
          background: "#050509", border: `0.5px solid ${dim}`,
          color: labelLight, padding: "8px 10px", fontSize: 11,
          fontFamily: "JetBrains Mono", letterSpacing: "0.06em",
        }}>
        <option value="RISK_OFF">RISK OFF</option>
        <option value="CONSERVATIVE">CONSERVATIVE</option>
        <option value="BALANCED">BALANCED</option>
        <option value="AGGRESSIVE">AGGRESSIVE</option>
      </select>
    </label>
  );
}

function BacktestTable({ rows, label }) {
  if (!rows.length) return <div style={{ color: muted, padding: 20 }}>No backtest rows yet.</div>;
  return (
    <table style={{ width: "100%", borderCollapse: "collapse" }}>
      <thead>
        <tr>
          <th style={th}>{label}</th>
          <th style={th}>ALLOC</th>
          <th style={th}>MAT</th>
          <th style={th}>WIN</th>
          <th style={th}>AVG</th>
          <th style={th}>P/L</th>
        </tr>
      </thead>
      <tbody>
        {rows.map(r => (
          <tr key={r.key} style={{ borderTop: hairline }}>
            <td style={{ ...td, color: accent2, fontWeight: 800 }}>{r.key}</td>
            <td style={td}>{r.allocated}</td>
            <td style={td}>{r.matured}</td>
            <td style={{ ...td, color: (r.win_rate || 0) >= 0.5 ? "#4ade80" : "#f87171", fontWeight: 800 }}>
              {r.win_rate == null ? "--" : `${(r.win_rate * 100).toFixed(0)}%`}
            </td>
            <td style={{ ...td, color: Number(r.avg_return || 0) >= 0 ? "#4ade80" : "#f87171", fontWeight: 800 }}>
              {Number(r.avg_return || 0) >= 0 ? "+" : ""}{Number(r.avg_return || 0).toFixed(2)}%
            </td>
            <td style={{ ...td, color: Number(r.pnl_usd || 0) >= 0 ? "#4ade80" : "#f87171", fontWeight: 800 }}>
              {Number(r.pnl_usd || 0) >= 0 ? "+" : "-"}${Math.abs(Number(r.pnl_usd || 0)).toFixed(2)}
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

function SimTable({ rows }) {
  if (!rows.length) return <div style={{ color: muted, padding: 20 }}>No simulated exits yet.</div>;
  return (
    <table style={{ width: "100%", borderCollapse: "collapse" }}>
      <thead>
        <tr>
          <th style={th}>RATCHET</th>
          <th style={th}>N</th>
          <th style={th}>TARGET</th>
          <th style={th}>STOP</th>
          <th style={th}>HOLD</th>
          <th style={th}>AVG SIM</th>
          <th style={th}>SIM P/L</th>
        </tr>
      </thead>
      <tbody>
        {rows.map(r => (
          <tr key={r.key} style={{ borderTop: hairline }}>
            <td style={{ ...td, color: accent2, fontWeight: 800 }}>{r.key}</td>
            <td style={td}>{r.simulated}</td>
            <td style={{ ...td, color: "#4ade80", fontWeight: 800 }}>{r.target_hits}</td>
            <td style={{ ...td, color: "#f87171", fontWeight: 800 }}>{r.stop_hits}</td>
            <td style={td}>{r.hold_exits}</td>
            <td style={{ ...td, color: Number(r.avg_sim_return || 0) >= 0 ? "#4ade80" : "#f87171", fontWeight: 800 }}>
              {Number(r.avg_sim_return || 0) >= 0 ? "+" : ""}{Number(r.avg_sim_return || 0).toFixed(2)}%
            </td>
            <td style={{ ...td, color: Number(r.sim_pnl_usd || 0) >= 0 ? "#4ade80" : "#f87171", fontWeight: 800 }}>
              {Number(r.sim_pnl_usd || 0) >= 0 ? "+" : "-"}${Math.abs(Number(r.sim_pnl_usd || 0)).toFixed(2)}
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

function PMRulesView({ rulesets, refresh }) {
  const [saving, setSaving] = useState(false);
  const [draftTest, setDraftTest] = useState(null);
  const [testingDraft, setTestingDraft] = useState(false);
  const [draft, setDraft] = useState({
    name: "Custom PM Rules",
    description: "Created from PM Rule Lab",
    mode: "BALANCED",
    max_gross_deployment_pct: "0.35",
    max_position_pct: "0.08",
    max_single_name_risk_pct: "0.0125",
    accumulate_score: "70",
    accumulate_rr: "1.8",
    starter_score: "58",
    starter_rr: "1.3",
    watch_score: "45",
  });
  const setDraftField = (key, value) => setDraft(prev => ({ ...prev, [key]: value }));
  const createTighter = async () => {
    setSaving(true);
    try {
      await axios.post(`${API}/portfolio_manager/rulesets`, {
        name: "Tighter Starter Risk",
        description: "Sandbox ruleset: smaller gross exposure, higher starter R/R, lower single-name risk.",
        activate: false,
        mode_overrides: {
          BALANCED: {
            max_gross_deployment_pct: 0.28,
            max_single_name_risk_pct: 0.01,
            starter_rr: 1.5,
            accumulate_rr: 2.0,
          },
        },
      });
      refresh();
    } finally {
      setSaving(false);
    }
  };
  const createCustom = async (activateNow = false) => {
    setSaving(true);
    try {
      const mode = draft.mode;
      const fields = {};
      [
        "max_gross_deployment_pct",
        "max_position_pct",
        "max_single_name_risk_pct",
        "accumulate_score",
        "accumulate_rr",
        "starter_score",
        "starter_rr",
        "watch_score",
      ].forEach(k => {
        if (draft[k] !== "" && Number.isFinite(Number(draft[k]))) fields[k] = Number(draft[k]);
      });
      await axios.post(`${API}/portfolio_manager/rulesets`, {
        name: draft.name,
        description: draft.description,
        activate: activateNow,
        mode_overrides: { [mode]: fields },
      });
      refresh();
    } finally {
      setSaving(false);
    }
  };
  const backtestDraft = async () => {
    setTestingDraft(true);
    try {
      const params = { limit_scans: 120, mode: draft.mode, equity: 1000 };
      [
        "max_gross_deployment_pct",
        "max_position_pct",
        "max_single_name_risk_pct",
        "accumulate_score",
        "accumulate_rr",
        "starter_score",
        "starter_rr",
        "watch_score",
      ].forEach(k => {
        if (draft[k] !== "" && Number.isFinite(Number(draft[k]))) params[k] = Number(draft[k]);
      });
      const r = await axios.get(`${API}/portfolio_manager/backtest`, { params });
      setDraftTest(r.data);
    } finally {
      setTestingDraft(false);
    }
  };
  const activate = async (rulesetId) => {
    setSaving(true);
    try {
      await axios.post(`${API}/portfolio_manager/rulesets/${rulesetId}/activate`);
      refresh();
    } finally {
      setSaving(false);
    }
  };
  if (!rulesets) return <Card title="PM RULE VERSIONS"><div style={{ color: muted, padding: 20 }}>Loading PM rulesets...</div></Card>;
  return (
    <>
      <div style={{
        padding: "14px 18px", border: `0.5px solid ${accent2}`,
        background: `${accent2}10`, color: accent2, fontSize: 11,
        letterSpacing: "0.1em", marginBottom: 16,
      }}>
        ACTIVE RULESET CONTROLS PM, TRADE FLOOR APPROVALS, BACKTEST REPLAYS, AND JOURNAL EVIDENCE TAGS.
      </div>
      <Card title="PM RULE VERSIONS" accentColor={accent}>
        <div style={{ display: "flex", justifyContent: "space-between", gap: 12, alignItems: "center", marginBottom: 14, flexWrap: "wrap" }}>
          <div style={{ color: muted, fontSize: 11 }}>ACTIVE: <span style={{ color: accent }}>{rulesets.active_ruleset_id}</span></div>
          <button onClick={createTighter} disabled={saving} style={{
            background: "transparent", border: `0.5px solid ${accent}`,
            color: saving ? muted : accent, fontSize: 11, padding: "8px 16px",
            cursor: saving ? "default" : "pointer", letterSpacing: "0.14em",
            fontFamily: "JetBrains Mono", fontWeight: 700,
          }}>
            CREATE TIGHTER TEST
          </button>
        </div>
        <div style={{ overflowX: "auto" }}>
          <table style={{ width: "100%", borderCollapse: "collapse", minWidth: 900 }}>
            <thead>
              <tr>
                <th style={th}>RULESET</th>
                <th style={th}>STATUS</th>
                <th style={th}>DESCRIPTION</th>
                <th style={th}>BALANCED OVERRIDES</th>
                <th style={th}>ACTION</th>
              </tr>
            </thead>
            <tbody>
              {(rulesets.rulesets || []).map(r => (
                <tr key={r.ruleset_id} style={{ borderTop: hairline }}>
                  <td style={{ ...td, color: accent, fontWeight: 800 }}>{r.name}<br /><span style={{ color: muted }}>{r.ruleset_id}</span></td>
                  <td style={{ ...td, color: r.active ? "#4ade80" : muted, fontWeight: 800 }}>{r.active ? "ACTIVE" : "INACTIVE"}</td>
                  <td style={td}>{r.description || "--"}</td>
                  <td style={td}>
                    {Object.entries((r.mode_overrides || {}).BALANCED || {}).length
                      ? Object.entries((r.mode_overrides || {}).BALANCED || {}).map(([k, v]) => <div key={k}>{k}: {v}</div>)
                      : <span style={{ color: muted }}>DEFAULT</span>}
                  </td>
                  <td style={td}>
                    <button onClick={() => activate(r.ruleset_id)} disabled={saving || r.active} style={{
                      background: "transparent", border: `0.5px solid ${r.active ? dim : accent2}`,
                      color: r.active ? muted : accent2, fontSize: 10, padding: "7px 11px",
                      cursor: r.active ? "default" : "pointer", letterSpacing: "0.12em",
                      fontFamily: "JetBrains Mono", fontWeight: 700,
                    }}>
                      {r.active ? "LIVE" : "ACTIVATE"}
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </Card>
      <Card title="PM RULE LAB - CREATE VERSION" accentColor={accent2}>
        <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(150px, 1fr))", gap: 10 }}>
          <SandboxInput label="NAME" value={draft.name} onChange={v => setDraftField("name", v)} />
          <SandboxSelect label="MODE" value={draft.mode} onChange={v => setDraftField("mode", v)} />
          <SandboxInput label="GROSS CAP" value={draft.max_gross_deployment_pct} onChange={v => setDraftField("max_gross_deployment_pct", v)} />
          <SandboxInput label="MAX POS" value={draft.max_position_pct} onChange={v => setDraftField("max_position_pct", v)} />
          <SandboxInput label="NAME RISK" value={draft.max_single_name_risk_pct} onChange={v => setDraftField("max_single_name_risk_pct", v)} />
          <SandboxInput label="ACC SCORE" value={draft.accumulate_score} onChange={v => setDraftField("accumulate_score", v)} />
          <SandboxInput label="ACC R/R" value={draft.accumulate_rr} onChange={v => setDraftField("accumulate_rr", v)} />
          <SandboxInput label="START SCORE" value={draft.starter_score} onChange={v => setDraftField("starter_score", v)} />
          <SandboxInput label="START R/R" value={draft.starter_rr} onChange={v => setDraftField("starter_rr", v)} />
          <SandboxInput label="WATCH SCORE" value={draft.watch_score} onChange={v => setDraftField("watch_score", v)} />
        </div>
        <label style={{ display: "grid", gap: 6, marginTop: 10 }}>
          <span style={{ color: dim, fontSize: 10, letterSpacing: "0.14em" }}>DESCRIPTION</span>
          <input value={draft.description} onChange={e => setDraftField("description", e.target.value)}
            style={{
              background: "#050509", border: `0.5px solid ${dim}`,
              color: labelLight, padding: "8px 10px", fontSize: 11,
              fontFamily: "JetBrains Mono", letterSpacing: "0.06em",
            }} />
        </label>
        <div style={{ display: "flex", justifyContent: "flex-end", gap: 10, marginTop: 14, flexWrap: "wrap" }}>
          <button onClick={backtestDraft} disabled={testingDraft || saving} style={{
            background: "transparent", border: `0.5px solid ${accent2}`,
            color: testingDraft ? muted : accent2, fontSize: 11, padding: "8px 16px",
            cursor: testingDraft ? "default" : "pointer", letterSpacing: "0.14em",
            fontFamily: "JetBrains Mono", fontWeight: 700,
          }}>{testingDraft ? "BACKTESTING" : "BACKTEST DRAFT"}</button>
          <button onClick={() => createCustom(false)} disabled={saving} style={{
            background: "transparent", border: `0.5px solid ${accent2}`,
            color: saving ? muted : accent2, fontSize: 11, padding: "8px 16px",
            cursor: saving ? "default" : "pointer", letterSpacing: "0.14em",
            fontFamily: "JetBrains Mono", fontWeight: 700,
          }}>SAVE VERSION</button>
          <button onClick={() => createCustom(true)} disabled={saving} style={{
            background: "transparent", border: `0.5px solid ${accent}`,
            color: saving ? muted : accent, fontSize: 11, padding: "8px 16px",
            cursor: saving ? "default" : "pointer", letterSpacing: "0.14em",
            fontFamily: "JetBrains Mono", fontWeight: 700,
          }}>SAVE + ACTIVATE</button>
        </div>
        {draftTest && (
          <div style={{ display: "flex", background: "#050509", border: hairline, marginTop: 14, flexWrap: "wrap" }}>
            <Stat label="DRAFT DECISIONS" value={draftTest.summary?.decisions || 0} sub={`${draftTest.summary?.allocated || 0} ALLOCATED`} color={accent2} accentBar />
            <Stat label="DRAFT DEPLOYED" value={`$${Number(draftTest.summary?.deployed_usd || 0).toFixed(0)}`} sub="REPLAY" color={accent} />
            <Stat label="MATURED" value={draftTest.summary?.matured || 0} sub={`${draftTest.summary?.pending || 0} PENDING`} color="#fbbf24" />
            <Stat label="SIM EXITS" value={draftTest.summary?.exit_simulation?.simulated || 0} sub={`${draftTest.summary?.exit_simulation?.target_hits || 0} TARGET / ${draftTest.summary?.exit_simulation?.stop_hits || 0} STOP`} color="#4ade80" />
          </div>
        )}
      </Card>
    </>
  );
}

function SystemHealthView({ health, refresh }) {
  const [probe, setProbe] = useState(null);
  const [syncResult, setSyncResult] = useState(null);
  const [probeBusy, setProbeBusy] = useState(false);
  const [probeTicker, setProbeTicker] = useState("AAPL");
  const runProbe = async (placeOrder = false) => {
    setProbeBusy(true);
    try {
      const r = await axios.post(`${API}/trade_floor/execution_probe`, null, {
        params: { ticker: probeTicker, notional: 1, place_order: placeOrder },
      });
      setProbe(r.data);
      refresh();
    } finally {
      setProbeBusy(false);
    }
  };
  const syncFills = async () => {
    setProbeBusy(true);
    try {
      const r = await axios.post(`${API}/trade_floor/sync`);
      setSyncResult(r.data);
      refresh();
    } finally {
      setProbeBusy(false);
    }
  };
  if (!health) return <Card title="SYSTEM HEALTH"><div style={{ color: muted, padding: 20 }}>Loading system health...</div></Card>;
  const readiness = [
    ["SCANNING", health.ready_for_scanning],
    ["PM", health.ready_for_pm],
    ["TRADE FLOOR", health.ready_for_trade_floor],
    ["JOURNAL LEARNING", health.ready_for_journal_learning],
  ];
  return (
    <>
      <div style={{ display: "flex", justifyContent: "flex-end", marginBottom: 12 }}>
        <button onClick={refresh} style={{
          background: "transparent", border: `0.5px solid ${accent}`,
          color: accent, fontSize: 11, padding: "8px 16px",
          cursor: "pointer", letterSpacing: "0.14em",
          fontFamily: "JetBrains Mono", fontWeight: 700,
        }}>REFRESH HEALTH</button>
      </div>
      <div style={{ display: "flex", background: cardBg, border: hairline, marginBottom: 22, flexWrap: "wrap" }}>
        {readiness.map(([label, ok], i) => (
          <Stat key={label} label={label} value={ok ? "READY" : "BLOCKED"} sub={i === 0 ? "SYSTEM" : "CHECK"} color={ok ? "#4ade80" : "#f87171"} accentBar={i === 0} />
        ))}
      </div>
      <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(320px, 1fr))", gap: 18 }}>
        <Card title="EXECUTION DIAGNOSTIC" accentColor={health.alpaca?.ok ? "#4ade80" : "#f87171"}>
          <PlanRow k="ALPACA" v={health.alpaca?.ok ? "ACCOUNT OK" : "BLOCKED"} color={health.alpaca?.ok ? "#4ade80" : "#f87171"} />
          <PlanRow k="BASE URL" v={health.alpaca?.base_url || "--"} />
          <PlanRow k="KEY" v={health.alpaca?.key_state || "--"} />
          <PlanRow k="SECRET" v={health.alpaca?.secret_state || "--"} />
          <PlanRow k="HTTP" v={health.alpaca?.status_code || "--"} />
          <PlanRow k="REASON" v={health.alpaca?.reason || "--"} color={health.alpaca?.ok ? "#4ade80" : "#f87171"} />
          <div style={{ display: "grid", gridTemplateColumns: "1fr auto auto", gap: 8, marginTop: 12 }}>
            <input value={probeTicker} onChange={e => setProbeTicker(e.target.value.toUpperCase())}
              style={{
                background: "#050509", border: `0.5px solid ${dim}`,
                color: labelLight, padding: "8px 10px", fontSize: 11,
                fontFamily: "JetBrains Mono", letterSpacing: "0.06em",
              }} />
            <button onClick={() => runProbe(false)} disabled={probeBusy} style={{
              background: "transparent", border: `0.5px solid ${accent2}`,
              color: probeBusy ? muted : accent2, fontSize: 10, padding: "8px 10px",
              cursor: probeBusy ? "default" : "pointer", letterSpacing: "0.12em",
              fontFamily: "JetBrains Mono", fontWeight: 700,
            }}>DRY PROBE</button>
            <button onClick={() => runProbe(true)} disabled={probeBusy} style={{
              background: "transparent", border: `0.5px solid ${accent}`,
              color: probeBusy ? muted : accent, fontSize: 10, padding: "8px 10px",
              cursor: probeBusy ? "default" : "pointer", letterSpacing: "0.12em",
              fontFamily: "JetBrains Mono", fontWeight: 700,
            }}>$1 PAPER TEST</button>
          </div>
          <button onClick={syncFills} disabled={probeBusy} style={{
            width: "100%", marginTop: 8,
            background: "transparent", border: `0.5px solid ${dim}`,
            color: probeBusy ? muted : labelLight, fontSize: 10, padding: "8px 10px",
            cursor: probeBusy ? "default" : "pointer", letterSpacing: "0.12em",
            fontFamily: "JetBrains Mono", fontWeight: 700,
          }}>SYNC FILLS / CLOSES</button>
          {probe && (
            <div style={{ marginTop: 12, borderTop: hairline, paddingTop: 10 }}>
              <PlanRow k="PROBE" v={probe.ok ? "OK" : "FAILED"} color={probe.ok ? "#4ade80" : "#f87171"} />
              <PlanRow k="ACCOUNT" v={probe.account_ok ? "OK" : "NO"} color={probe.account_ok ? "#4ade80" : "#f87171"} />
              <PlanRow k="QUOTE" v={probe.quote_ok ? `$${Number(probe.ask || 0).toFixed(2)}` : "NO"} color={probe.quote_ok ? "#4ade80" : "#f87171"} />
              <PlanRow k="ORDER" v={probe.order_ok ? (probe.order?.status || "SUBMITTED") : "NOT SENT"} color={probe.order_ok ? "#4ade80" : muted} />
              <PlanRow k="DETAIL" v={probe.reason || "--"} color={probe.ok ? "#4ade80" : "#f87171"} />
            </div>
          )}
          {syncResult && (
            <div style={{ marginTop: 12, borderTop: hairline, paddingTop: 10 }}>
              <PlanRow k="SYNC UPDATED" v={syncResult.updated ?? 0} color={accent2} />
              <PlanRow k="SYNC CLOSED" v={syncResult.closed ?? 0} color={accent} />
            </div>
          )}
        </Card>
        <Card title="DATABASE COUNTS" accentColor={accent2}>
          {Object.entries(health.database?.counts || {}).map(([k, v]) => (
            <PlanRow key={k} k={k.toUpperCase()} v={v} />
          ))}
          <PlanRow k="LATEST SCAN" v={health.database?.latest_scan_at || "--"} color={accent2} />
        </Card>
        <Card title="FREE DATA READINESS" accentColor={accent}>
          <PlanRow k="MONGODB" v={health.env?.mongodb || "--"} />
          <PlanRow k="FRED" v={health.env?.fred || "--"} />
          <PlanRow k="ALPHA VANTAGE" v={health.env?.alpha_vantage || "--"} />
          <PlanRow k="FMP" v={health.env?.fmp || "--"} />
          <PlanRow k="CLAUDE DISABLED" v={health.env?.claude_disabled ? "YES" : "NO"} color={health.env?.claude_disabled ? "#4ade80" : "#fbbf24"} />
        </Card>
      </div>
      <Card title="BLOCKERS">
        {(health.blockers || []).length ? (
          health.blockers.map((x, i) => <div key={i} style={{ color: "#f87171", padding: "8px 0", borderBottom: hairline, fontSize: 12 }}>{x}</div>)
        ) : (
          <div style={{ color: "#4ade80", padding: 20 }}>No blockers detected.</div>
        )}
      </Card>
    </>
  );
}

function LearningTable({ rows, label }) {
  if (!rows.length) return <div style={{ color: muted, padding: 20 }}>No matured outcomes yet.</div>;
  return (
    <table style={{ width: "100%", borderCollapse: "collapse" }}>
      <thead>
        <tr>
          <th style={th}>{label}</th>
          <th style={th}>N</th>
          <th style={th}>WIN</th>
          <th style={th}>AVG</th>
          <th style={th}>RANGE</th>
        </tr>
      </thead>
      <tbody>
        {rows.map(r => (
          <tr key={r.key} style={{ borderTop: hairline }}>
            <td style={{ ...td, color: accent2, fontWeight: 800 }}>{r.key}</td>
            <td style={td}>{r.samples}</td>
            <td style={{ ...td, color: (r.win_rate || 0) >= 0.5 ? "#4ade80" : "#f87171", fontWeight: 800 }}>
              {r.win_rate == null ? "—" : `${(r.win_rate * 100).toFixed(0)}%`}
            </td>
            <td style={{ ...td, color: Number(r.avg_return || 0) >= 0 ? "#4ade80" : "#f87171", fontWeight: 800 }}>
              {Number(r.avg_return || 0) >= 0 ? "+" : ""}{Number(r.avg_return || 0).toFixed(2)}%
            </td>
            <td style={td}>
              {r.worst_return == null ? "—" : `${Number(r.worst_return).toFixed(2)}% / ${Number(r.best_return).toFixed(2)}%`}
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

function buildPmCalendarCells(year, month, rows) {
  const byDate = new Map(rows.map(r => [r.date, r]));
  const first = new Date(year, month - 1, 1);
  const totalDays = new Date(year, month, 0).getDate();
  const cells = [];
  for (let i = 0; i < first.getDay(); i += 1) cells.push({ date: null });
  for (let d = 1; d <= totalDays; d += 1) {
    const date = `${year}-${String(month).padStart(2, "0")}-${String(d).padStart(2, "0")}`;
    cells.push({ date, dayNumber: d, row: byDate.get(date) || null });
  }
  while (cells.length % 7 !== 0) cells.push({ date: null });
  return cells;
}

function pmCalendarSummary(rows) {
  const vals = rows.map(r => Number(r.terminal_total_pct)).filter(Number.isFinite);
  const relativeVals = rows.map(r => Number(r.relative_pct)).filter(Number.isFinite);
  const spyWins = relativeVals.filter(v => v > 0).length;
  const spyLosses = relativeVals.filter(v => v < 0).length;
  const green = vals.filter(v => v > 0).length;
  const red = vals.filter(v => v < 0).length;
  return {
    green,
    red,
    spyWins,
    spyLosses,
    spyWinRate: spyWins + spyLosses ? Math.round((spyWins / (spyWins + spyLosses)) * 100) : 0,
    relativeAvg: relativeVals.length ? relativeVals.reduce((a, b) => a + b, 0) / relativeVals.length : null,
    avg: vals.length ? vals.reduce((a, b) => a + b, 0) / vals.length : null,
    best: vals.length ? Math.max(...vals) : null,
    worst: vals.length ? Math.min(...vals) : null,
  };
}

function pmWeekSummary(cells) {
  const weeks = [];
  for (let i = 0; i < cells.length; i += 7) {
    const rows = cells.slice(i, i + 7).map(c => c.row).filter(Boolean);
    const total = rows.reduce((sum, row) => sum + (Number(row.terminal_total_pct) || 0), 0);
    const spy = rows.reduce((sum, row) => sum + (Number(row.spy_return_pct) || 0), 0);
    const green = rows.filter(row => Number(row.terminal_total_pct) > 0).length;
    weeks.push({ days: rows.length, total, spy, green });
  }
  return weeks;
}

function pmDetailChartRows(rows, selectedDate) {
  const idx = rows.findIndex(r => r.date === selectedDate);
  const start = Math.max(0, idx - 8);
  const end = idx >= 0 ? idx + 1 : rows.length;
  return rows.slice(start, end).map(r => ({
    label: String(r.date || "").slice(5),
    terminal: r.terminal_total_pct,
    spy: r.spy_return_pct,
  }));
}

function calendarYears(rows, fallbackYear) {
  const years = [...new Set(rows.map(r => dateParts(r.date).year).filter(Boolean))].sort((a, b) => b - a);
  return years.length ? years : [fallbackYear, fallbackYear - 1, fallbackYear - 2];
}

function dateParts(date) {
  const [y, m] = String(date || "").split("-").map(Number);
  return { year: y || null, month: m || null };
}

function pmMonthName(month) {
  return new Date(2026, Number(month) - 1, 1).toLocaleString("en-US", { month: "long" });
}

function fmtPct(v) {
  if (v == null || v === "" || !Number.isFinite(Number(v))) return "-";
  const n = Number(v);
  return `${n >= 0 ? "+" : ""}${n.toFixed(1)}%`;
}

function pctColor(v) {
  if (v == null || v === "" || !Number.isFinite(Number(v))) return muted;
  const n = Number(v);
  if (n > 0) return "#4ade80";
  if (n < 0) return "#f87171";
  return "#fbbf24";
}

function pmCalendarTitle(row) {
  if (!row) return "No P/L row for this date.";
  return [
    row.date,
    `Total fund: ${fmtPct(row.terminal_total_pct)}`,
    `Equities: ${fmtPct(row.stock_gain_pct)}`,
    `Options: ${fmtPct(row.options_gain_pct)}`,
    `SPY: ${fmtPct(row.spy_return_pct)}`,
  ].join("\n");
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

function outlineButton(color) {
  return {
    background: "transparent",
    border: `0.5px solid ${color}`,
    color,
    fontSize: 11,
    padding: "9px 14px",
    cursor: "pointer",
    letterSpacing: "0.14em",
    fontFamily: "JetBrains Mono",
    fontWeight: 800,
  };
}

function pmCalendarCell(cell, active) {
  const value = cell.row?.terminal_total_pct;
  const color = pctColor(value);
  return {
    aspectRatio: "1 / 1.2",
    minHeight: 96,
    display: "flex",
    flexDirection: "column",
    justifyContent: "space-between",
    alignItems: "stretch",
    background: !cell.date ? "transparent" : cell.row ? `linear-gradient(135deg, ${color}20, rgba(255,255,255,0.025))` : "rgba(255,255,255,0.018)",
    border: !cell.date ? "0.5px solid transparent" : `0.5px solid ${active ? accent : cell.row ? `${color}88` : "rgba(255,255,255,0.09)"}`,
    color: !cell.date ? "transparent" : labelLight,
    cursor: cell.date && cell.row ? "pointer" : "default",
    padding: 9,
    fontFamily: "JetBrains Mono",
    fontWeight: 900,
    borderRadius: 7,
    boxShadow: active ? `0 0 0 1px ${accent}, 0 0 22px ${accent}24` : cell.row ? `inset 0 -3px 0 ${color}, 0 0 18px ${color}10` : "none",
    overflow: "hidden",
  };
}

function PMWeekRail({ weeks }) {
  return (
    <div style={calendarWeekRail}>
      {weeks.map((week, idx) => {
        const color = pctColor(week.total);
        return (
          <button key={idx} style={calendarWeekCard(color)} title={`Week ${idx + 1}\nFund: ${fmtPct(week.total)}\nSPY: ${fmtPct(week.spy)}\nGreen days: ${week.green}`}>
            <span>Week {idx + 1}</span>
            <strong>{fmtPct(week.total)}</strong>
            <small>{week.days} days</small>
          </button>
        );
      })}
    </div>
  );
}

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
  fontFamily: "JetBrains Mono",
  fontWeight: 900,
  cursor: "pointer",
};
const calendarBoard = { display: "grid", gridTemplateColumns: "minmax(0, 1fr) 120px", gap: 12, alignItems: "stretch" };
const calendarToolbar = { display: "flex", justifyContent: "space-between", gap: 14, alignItems: "center", flexWrap: "wrap", marginBottom: 16 };
const calendarLegend = { display: "flex", gap: 14, flexWrap: "wrap", color: muted, fontSize: 10, letterSpacing: "0.12em" };
const calendarWeekHeader = { display: "grid", gridTemplateColumns: "repeat(7, minmax(0, 1fr))", gap: 5, color: dim, fontSize: 10, letterSpacing: "0.08em", margin: "12px 0 8px" };
const calendarGrid = { display: "grid", gridTemplateColumns: "repeat(7, minmax(0, 1fr))", gap: 5 };
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
  fontFamily: "JetBrains Mono",
  cursor: "default",
});
const pmDetailGrid = { display: "grid", gridTemplateColumns: "minmax(330px, 0.9fr) minmax(0, 1.1fr)", gap: 18, alignItems: "start" };
const selectStyle = {
  background: "#050509",
  border: hairline,
  color: accent2,
  padding: "10px 12px",
  fontSize: 11,
  letterSpacing: "0.1em",
  fontFamily: "JetBrains Mono",
  fontWeight: 800,
};

function PlanRow({ k, v, color = labelLight }) {
  return (
    <div style={{ display: "flex", justifyContent: "space-between", gap: 12, padding: "8px 0", borderBottom: hairline }}>
      <span style={{ color: muted, fontSize: 10, letterSpacing: "0.14em" }}>{k}</span>
      <span className="num" style={{ color, fontSize: 12, fontWeight: 800 }}>{v}</span>
    </div>
  );
}

function RatchetRule({ name, text }) {
  return (
    <div style={{ border: hairline, padding: 12, background: "#050509" }}>
      <div style={{ color: accent, fontSize: 11, fontWeight: 800, letterSpacing: "0.14em" }}>{name}</div>
      <div style={{ color: muted, fontSize: 11, marginTop: 8, lineHeight: 1.6 }}>{text}</div>
    </div>
  );
}
