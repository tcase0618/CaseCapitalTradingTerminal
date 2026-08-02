import { useEffect, useMemo, useState } from "react";
import axios from "axios";
import { API } from "../config";
import { CrtShell, Card, Stat, tokens } from "./CrtShell";

const { accent, accent2, dim, muted, labelLight, hairline, cardBg } = tokens;

const th = { padding: "10px 8px", fontSize: 10, color: dim, letterSpacing: "0.14em", fontWeight: 400, textAlign: "left" };
const td = { padding: "10px 8px", color: labelLight, letterSpacing: "0.04em", fontSize: 12, verticalAlign: "top" };

const ACTION_COLOR = {
  ACCUMULATE: "#4ade80",
  STARTER: "#5eead4",
  WATCH: "#fbbf24",
  REJECT: "#f87171",
};

const TABS = ["CAPSULES", "GRAVEYARD", "ALT UNIVERSE", "EVIDENCE", "DNA"];

export function TradeJournalView() {
  const [journal, setJournal] = useState(null);
  const [optionsJournal, setOptionsJournal] = useState(null);
  const [instrument, setInstrument] = useState("EQUITIES");
  const [tab, setTab] = useState("CAPSULES");
  const [loading, setLoading] = useState(false);

  const load = async () => {
    setLoading(true);
    try {
      const r = await axios.get(`${API}/trade_journal/overview`);
      setJournal(r.data);
      const [orders, candidates] = await Promise.all([
        axios.get(`${API}/options_desk/orders`).catch(() => ({ data: { orders: [] } })),
        axios.get(`${API}/options_desk/candidates`).catch(() => ({ data: { candidates: [], summary: {} } })),
      ]);
      setOptionsJournal({ orders: orders.data.orders || [], candidates: candidates.data.candidates || [], summary: candidates.data.summary || {} });
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => { load(); }, []);

  const summary = journal?.summary || {};
  const counts = journal?.source_counts || {};

  return (
    <>
      <div style={{
        display: "flex", justifyContent: "space-between", alignItems: "center",
        gap: 12, flexWrap: "wrap",
        padding: "14px 18px", border: `0.5px solid ${accent2}`,
        background: `${accent2}10`, color: accent2, fontSize: 11,
        letterSpacing: "0.1em", marginBottom: 16,
      }}>
        <span>ALGORITHMIC JOURNAL: PM DECISIONS, REJECTS, EXECUTION EVIDENCE, RATCHETS, OUTCOMES. NO CLAUDE REQUIRED.</span>
        <button onClick={load} disabled={loading}
          style={{
            background: "transparent", border: `0.5px solid ${accent}`,
            color: loading ? muted : accent, fontSize: 11, padding: "8px 16px",
            cursor: loading ? "default" : "pointer", letterSpacing: "0.14em",
            fontFamily: "JetBrains Mono", fontWeight: 700,
          }}>
          {loading ? "READING" : "REFRESH JOURNAL"}
        </button>
      </div>

      <div style={{ display: "flex", borderBottom: hairline, marginBottom: 16, flexWrap: "wrap" }}>
        {["EQUITIES", "OPTIONS"].map(k => (
          <button key={k} onClick={() => setInstrument(k)}
            style={{
              background: "transparent", border: "none", padding: "10px 22px",
              color: instrument === k ? accent : muted, cursor: "pointer",
              borderBottom: instrument === k ? `2px solid ${accent}` : "2px solid transparent",
              fontSize: 11, letterSpacing: "0.14em", fontFamily: "JetBrains Mono", fontWeight: 700,
            }}>
            {k}
          </button>
        ))}
      </div>

      {instrument === "OPTIONS" && <OptionsJournalView data={optionsJournal} />}

      {instrument === "EQUITIES" && (
        <>
      <div style={{ display: "flex", background: cardBg, border: hairline, marginBottom: 22, flexWrap: "wrap" }}>
        <Stat label="PM DECISIONS" value={summary.decision_count || 0} sub={`${summary.accepted_count || 0} ACCEPTED`} color={accent} accentBar />
        <Stat label="REJECT/WATCH" value={summary.rejected_or_watch || 0} sub="FALSE-NEGATIVE WATCH" color="#fbbf24" />
        <Stat label="MATURED" value={summary.matured_outcomes || 0} sub={`${summary.pending_outcomes || 0} PENDING`} color={accent2} />
        <Stat label="TRADES" value={summary.actual_trades || 0} sub={`${summary.open_trades || 0} OPEN / ${summary.closed_trades || 0} CLOSED`} color={labelLight} />
        <Stat label="MISSED WINNERS" value={summary.missed_winners || 0} sub="REJECTED +5%" color="#f87171" />
        <Stat label="AVOIDED LOSERS" value={summary.avoided_losers || 0} sub="GOOD PASSES" color="#4ade80" />
      </div>

      <Card title="DATA SOURCE LEDGER" accentColor={accent2}>
        <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(170px, 1fr))", gap: 10 }}>
          {Object.entries(counts).map(([k, v]) => (
            <div key={k} style={{ border: hairline, padding: 10, background: "#050509" }}>
              <div style={{ color: dim, fontSize: 10, letterSpacing: "0.14em" }}>{k.toUpperCase()}</div>
              <div className="num" style={{ color: accent, fontSize: 20, fontWeight: 800, marginTop: 4 }}>{v}</div>
            </div>
          ))}
        </div>
      </Card>

      <div style={{ display: "flex", borderBottom: hairline, marginBottom: 16, flexWrap: "wrap" }}>
        {TABS.map(k => (
          <button key={k} onClick={() => setTab(k)}
            style={{
              background: "transparent", border: "none", padding: "10px 18px",
              color: tab === k ? accent : muted, cursor: "pointer",
              borderBottom: tab === k ? `2px solid ${accent}` : "2px solid transparent",
              fontSize: 11, letterSpacing: "0.14em", fontFamily: "JetBrains Mono", fontWeight: 700,
            }}>
            {k}
          </button>
        ))}
      </div>

      {!journal ? (
        <Card title="LOADING"><div style={{ color: muted, padding: 20 }}>Loading journal...</div></Card>
      ) : (
        <>
          {tab === "CAPSULES" && <Capsules rows={journal.decision_time_capsules || []} />}
          {tab === "GRAVEYARD" && <Graveyard rows={journal.rejected_graveyard || []} />}
          {tab === "ALT UNIVERSE" && <AltUniverse rows={journal.alternate_universe || []} />}
          {tab === "EVIDENCE" && <Evidence rows={journal.evidence_locker || []} sources={journal.credible_data_sources || []} feedback={journal.rule_feedback || []} />}
          {tab === "DNA" && <Dna dna={journal.trade_dna || []} pain={journal.pain_map || []} />}
        </>
      )}
        </>
      )}
    </>
  );
}

export default function TradeJournalPage() {
  return (
    <CrtShell title="TRADE JOURNAL">
      <TradeJournalView />
    </CrtShell>
  );
}

function Capsules({ rows }) {
  return (
    <Card title="DECISION TIME CAPSULES">
      {!rows.length ? <Empty text="No PM decisions recorded yet." /> : (
        <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(310px, 1fr))", gap: 12 }}>
          {rows.map(r => <Capsule key={`${r.date}-${r.ticker}`} row={r} />)}
        </div>
      )}
    </Card>
  );
}

function Capsule({ row }) {
  return (
    <div style={{ border: hairline, background: "#050509", padding: 12 }}>
      <div style={{ display: "flex", justifyContent: "space-between", gap: 10 }}>
        <div style={{ color: accent, fontWeight: 900, fontSize: 18 }}>${row.ticker}</div>
        <div style={{ color: ACTION_COLOR[row.action] || labelLight, fontWeight: 900, fontSize: 11, letterSpacing: "0.14em" }}>{row.action}</div>
      </div>
      <div style={{ display: "grid", gridTemplateColumns: "repeat(4, 1fr)", gap: 8, marginTop: 10 }}>
        <Mini label="SCORE" value={Number(row.pm_score || 0).toFixed(1)} />
        <Mini label="R/R" value={Number(row.risk_reward || 0).toFixed(2)} />
        <Mini label="ALLOC" value={`$${Number(row.allocation_usd || 0).toFixed(0)}`} />
        <Mini label="RATCHET" value={row.ratchet_profile || "OFF"} />
      </div>
      <div style={{ color: dim, fontSize: 10, letterSpacing: "0.1em", marginTop: 8 }}>
        RULESET: <span style={{ color: accent2 }}>{row.ruleset_name || row.ruleset_id || "PM Default"}</span>
      </div>
      <div style={{ color: muted, fontSize: 11, lineHeight: 1.6, marginTop: 10 }}>
        {(row.reasons || []).slice(0, 2).map((x, i) => <div key={i}>{x}</div>)}
        {(row.cautions || []).slice(0, 1).map((x, i) => <div key={`c-${i}`} style={{ color: "#fbbf24" }}>{x}</div>)}
      </div>
      <div style={{ marginTop: 10, paddingTop: 10, borderTop: hairline, color: labelLight, fontSize: 11, lineHeight: 1.6 }}>
        {row.lesson}
      </div>
    </div>
  );
}

function Graveyard({ rows }) {
  const sorted = useMemo(() => {
    return [...rows].sort((a, b) => Number(b.outcome_return || -999) - Number(a.outcome_return || -999));
  }, [rows]);
  return (
    <Card title="REJECTED TRADE GRAVEYARD">
      {!sorted.length ? <Empty text="No rejected/watch PM decisions yet." /> : (
        <DataTable minWidth={920}
          head={["DATE", "TICKER", "ACTION", "SCORE", "R/R", "OUTCOME", "LESSON"]}
          rows={sorted.map(r => [
            r.date || "--",
            `$${r.ticker}`,
            r.action,
            Number(r.pm_score || 0).toFixed(1),
            Number(r.risk_reward || 0).toFixed(2),
            r.outcome_return == null ? "PENDING" : `${r.outcome_return >= 0 ? "+" : ""}${Number(r.outcome_return).toFixed(2)}% ${r.outcome_basis || ""}`,
            r.lesson,
          ])}
        />
      )}
    </Card>
  );
}

function AltUniverse({ rows }) {
  return (
    <Card title="ALTERNATE UNIVERSE REPLAY">
      {!rows.length ? <Empty text="No replay rows available." /> : (
        <div style={{ overflowX: "auto" }}>
          <table style={{ width: "100%", borderCollapse: "collapse", minWidth: 1040 }}>
            <thead>
              <tr>
                <th style={th}>TICKER</th>
                {["RISK_OFF", "CONSERVATIVE", "BALANCED", "AGGRESSIVE"].map(m => <th key={m} style={th}>{m}</th>)}
              </tr>
            </thead>
            <tbody>
              {rows.map(r => (
                <tr key={r.ticker} style={{ borderTop: hairline }}>
                  <td style={{ ...td, color: accent, fontWeight: 900 }}>${r.ticker}</td>
                  {["RISK_OFF", "CONSERVATIVE", "BALANCED", "AGGRESSIVE"].map(m => {
                    const x = r.modes?.[m] || {};
                    return (
                      <td key={m} style={td}>
                        <div style={{ color: ACTION_COLOR[x.action] || labelLight, fontWeight: 800 }}>{x.action || "--"}</div>
                        <div style={{ color: muted, marginTop: 4 }}>${Number(x.allocation_usd || 0).toFixed(0)} risk ${Number(x.risk_usd || 0).toFixed(0)}</div>
                        <div style={{ color: accent2, marginTop: 4 }}>{x.ratchet || "OFF"}</div>
                      </td>
                    );
                  })}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </Card>
  );
}

function Evidence({ rows, sources, feedback }) {
  return (
    <>
      <Card title="EVIDENCE LOCKER">
        {!rows.length ? <Empty text="No evidence rows yet." /> : (
          <DataTable minWidth={980}
            head={["TYPE", "DATE", "TICKER", "STATUS", "PM", "ENTRY", "EXIT", "RESULT", "LESSON"]}
            rows={rows.map(r => [
              r.type,
              r.date ? String(r.date).slice(0, 10) : "--",
              `$${r.ticker}`,
              r.status || "--",
              `${r.pm_action || "--"} ${r.pm_score ? Number(r.pm_score).toFixed(1) : ""}`,
              r.entry == null ? "--" : `$${Number(r.entry).toFixed(2)}`,
              r.exit == null ? "--" : `$${Number(r.exit).toFixed(2)}`,
              r.realized_pct == null ? "PENDING" : `${r.realized_pct >= 0 ? "+" : ""}${Number(r.realized_pct).toFixed(2)}%`,
              r.lesson,
            ])}
          />
        )}
      </Card>
      <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(300px, 1fr))", gap: 18 }}>
        <Card title="RULE FEEDBACK" accentColor="#fbbf24">
          {(feedback || []).map((x, i) => <div key={i} style={{ color: labelLight, padding: "8px 0", borderBottom: hairline, fontSize: 12 }}>{x}</div>)}
        </Card>
        <Card title="FREE CREDIBLE DATA STACK" accentColor={accent2}>
          {(sources || []).map(s => (
            <div key={s.name} style={{ padding: "8px 0", borderBottom: hairline }}>
              <div style={{ color: accent, fontWeight: 800, fontSize: 12 }}>{s.name} <span style={{ color: muted, fontWeight: 500 }}>({s.cost})</span></div>
              <div style={{ color: muted, fontSize: 11, marginTop: 4, lineHeight: 1.5 }}>{s.use}</div>
            </div>
          ))}
        </Card>
      </div>
    </>
  );
}

function Dna({ dna, pain }) {
  return (
    <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(360px, 1fr))", gap: 18 }}>
      <Card title="TRADE DNA">
        <MetricRows rows={dna} empty="No matured trade DNA yet." />
      </Card>
      <Card title="HEAT MAP OF PAIN">
        <MetricRows rows={pain} empty="No pain-map samples yet." />
      </Card>
    </div>
  );
}

function OptionsJournalView({ data }) {
  const candidates = data?.candidates || [];
  const orders = data?.orders || [];
  const summary = data?.summary || {};
  return (
    <>
      <div style={{ display: "flex", background: cardBg, border: hairline, marginBottom: 22, flexWrap: "wrap" }}>
        <Stat label="OPTIONS CANDIDATES" value={summary.total || candidates.length || 0} sub={`${summary.ready || 0} READY`} color={accent} accentBar />
        <Stat label="OPTION/BOTH" value={(summary.option || 0) + (summary.both || 0)} sub="PM ROUTED" color={accent2} />
        <Stat label="ORDERS" value={orders.length} sub="PAPER DESK" color="#fbbf24" />
        <Stat label="PASS/EQUITY" value={(summary.pass || 0) + (summary.equity || 0)} sub="NOT OPTIONS" color={labelLight} />
      </div>
      <Card title="OPTIONS EXECUTION LEDGER">
        <DataTable minWidth={980}
          head={["SUBMITTED", "SYMBOL", "SIDE", "QTY", "TYPE", "LIMIT", "STATUS"]}
          rows={orders.map(o => [
            o.submitted_at || "--",
            o.symbol || "--",
            o.side || "--",
            o.qty || "--",
            o.type || "--",
            o.limit_price || "--",
            o.status || "--",
          ])}
        />
        {!orders.length && <Empty text="No paper options orders logged yet." />}
      </Card>
      <Card title="OPTIONS PM DECISION CAPSULES">
        <DataTable minWidth={1060}
          head={["TICKER", "ROUTE", "STRATEGY", "PM", "R/R", "RISK", "READY", "LESSON"]}
          rows={candidates.slice(0, 40).map(c => [
            `$${c.ticker}`,
            c.route,
            c.strategy || "--",
            Number(c.pm_score || 0).toFixed(1),
            Number(c.risk_reward || 0).toFixed(2),
            `$${Number(c.risk_budget || 0).toFixed(2)}`,
            c.manual_fire_ready ? "YES" : "NO",
            (c.blocked_reasons || c.route_reasons || ["Collect paper outcome data."])[0],
          ])}
        />
        {!candidates.length && <Empty text="No options candidates recorded yet." />}
      </Card>
    </>
  );
}

function MetricRows({ rows, empty }) {
  if (!rows.length) return <Empty text={empty} />;
  return (
    <DataTable minWidth={620}
      head={["KEY", "N", "WIN", "AVG", "P/L"]}
      rows={rows.map(r => [
        r.key,
        r.samples,
        r.win_rate == null ? "--" : `${(r.win_rate * 100).toFixed(0)}%`,
        `${Number(r.avg_return || 0) >= 0 ? "+" : ""}${Number(r.avg_return || 0).toFixed(2)}%`,
        `${Number(r.pnl || 0) >= 0 ? "+" : "-"}$${Math.abs(Number(r.pnl || 0)).toFixed(2)}`,
      ])}
    />
  );
}

function DataTable({ head, rows, minWidth = 760 }) {
  return (
    <div style={{ overflowX: "auto" }}>
      <table style={{ width: "100%", borderCollapse: "collapse", minWidth }}>
        <thead><tr>{head.map(h => <th key={h} style={th}>{h}</th>)}</tr></thead>
        <tbody>
          {rows.map((r, i) => (
            <tr key={i} style={{ borderTop: hairline }}>
              {r.map((c, j) => <td key={j} style={{ ...td, color: j === 1 ? accent : labelLight }}>{c}</td>)}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function Mini({ label, value }) {
  return (
    <div>
      <div style={{ color: dim, fontSize: 9, letterSpacing: "0.12em" }}>{label}</div>
      <div className="num" style={{ color: labelLight, fontSize: 12, fontWeight: 800, marginTop: 3 }}>{value}</div>
    </div>
  );
}

function Empty({ text }) {
  return <div style={{ color: muted, padding: 20 }}>{text}</div>;
}
