import { useCallback, useEffect, useMemo, useState } from "react";
import axios from "axios";
import { CrtShell, Card, Stat, tokens } from "./CrtShell";

const API = `${(process.env.REACT_APP_BACKEND_URL || "").replace(/\/$/, "")}/api`;
const { accent, accent2, dim, muted, labelLight, hairline, cardBg } = tokens;

const SOURCES = ["all", "system", "scanner", "trade_floor", "options_desk", "telegram"];
const TYPES = ["all", "activity", "scan_complete", "equity_decision", "option_order", "option_fill", "option_exit", "risk_check", "telegram_report"];
const SEVERITY = { success: "#4ade80", info: accent2, warn: "#fbbf24", error: "#f87171" };

export default function AuditLogsPage() {
  const [data, setData] = useState(null);
  const [source, setSource] = useState("all");
  const [type, setType] = useState("all");
  const [ticker, setTicker] = useState("");
  const [selected, setSelected] = useState(null);
  const [busy, setBusy] = useState(false);

  const load = useCallback(async () => {
    setBusy(true);
    try {
      const params = new URLSearchParams({ limit: "300", source, event_type: type });
      if (ticker.trim()) params.set("ticker", ticker.trim().replace("$", "").toUpperCase());
      const r = await axios.get(`${API}/audit_logs?${params.toString()}`);
      setData(r.data);
      setSelected(prev => prev && (r.data.events || []).find(e => e.ref_id === prev.ref_id) ? prev : (r.data.events || [])[0] || null);
    } finally {
      setBusy(false);
    }
  }, [source, type, ticker]);

  useEffect(() => { load(); }, [load]);

  const events = data?.events || [];
  const critical = events.filter(e => e.severity === "error" || e.severity === "warn").length;
  const executions = events.filter(e => ["equity_decision", "option_order", "option_fill", "option_exit"].includes(e.event_type)).length;
  const sourceRows = useMemo(() => Object.entries(data?.source_counts || {}).sort((a, b) => b[1] - a[1]), [data]);

  return (
    <CrtShell
      title="AUDIT LOGS"
      headerRight={<button onClick={load} disabled={busy} style={buttonStyle(accent)}>{busy ? "SYNCING" : "REFRESH"}</button>}
    >
      <div style={{ display: "flex", background: cardBg, border: hairline, marginBottom: 18, flexWrap: "wrap" }}>
        <Stat label="EVENTS" value={data?.count ?? "-"} sub="READ MODEL" color={accent} accentBar />
        <Stat label="EXECUTION TRAIL" value={executions} sub="ORDERS/FILLS/EXITS" color={accent2} />
        <Stat label="WARNINGS" value={critical} sub="WARN + ERROR" color={critical ? "#fbbf24" : "#4ade80"} />
        <Stat label="SOURCES" value={sourceRows.length || 0} sub="SYSTEMS MERGED" color="#4ade80" />
      </div>

      <Card title="FILTERS" accentColor={accent2}>
        <div style={filterGrid}>
          <Field label="SOURCE">
            <select value={source} onChange={e => setSource(e.target.value)} style={inputStyle}>
              {SOURCES.map(s => <option key={s} value={s}>{s.toUpperCase()}</option>)}
            </select>
          </Field>
          <Field label="EVENT TYPE">
            <select value={type} onChange={e => setType(e.target.value)} style={inputStyle}>
              {TYPES.map(t => <option key={t} value={t}>{t.toUpperCase()}</option>)}
            </select>
          </Field>
          <Field label="TICKER">
            <input value={ticker} onChange={e => setTicker(e.target.value)} placeholder="LDOS" style={inputStyle} />
          </Field>
        </div>
      </Card>

      <div style={mainGrid}>
        <Card title="EVENT STREAM" accentColor={accent}>
          <EventTable events={events} selected={selected} onSelect={setSelected} />
        </Card>
        <Card title="EVENT PAYLOAD" accentColor="#4ade80">
          <PayloadPanel event={selected} />
        </Card>
      </div>
    </CrtShell>
  );
}

function EventTable({ events, selected, onSelect }) {
  if (!events.length) return <div style={{ color: muted, padding: 20 }}>No audit events match the current filters.</div>;
  return (
    <div style={{ overflowX: "auto" }}>
      <table style={{ width: "100%", minWidth: 900, borderCollapse: "collapse" }}>
        <thead>
          <tr>{["TIME", "SOURCE", "TYPE", "TICKER", "STATUS", "SUMMARY"].map(h => <th key={h} style={th}>{h}</th>)}</tr>
        </thead>
        <tbody>
          {events.map((e, i) => {
            const key = `${e.ts}-${e.source}-${e.event_type}-${e.ref_id || i}`;
            const active = selected === e;
            return (
              <tr key={key} onClick={() => onSelect(e)} style={{ borderTop: hairline, cursor: "pointer", background: active ? "rgba(200,168,75,0.08)" : "transparent" }}>
                <td style={td}>{formatTime(e.ts)}</td>
                <td style={{ ...td, color: sourceColor(e.source), fontWeight: 900 }}>{e.source}</td>
                <td style={{ ...td, color: SEVERITY[e.severity] || labelLight }}>{e.event_type}</td>
                <td style={{ ...td, color: e.ticker ? accent : muted }}>{e.ticker ? `$${e.ticker}` : "-"}</td>
                <td style={{ ...td, color: SEVERITY[e.severity] || labelLight }}>{e.status || "-"}</td>
                <td style={{ ...td, color: labelLight }}>{e.summary || e.title || "-"}</td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

function PayloadPanel({ event }) {
  if (!event) return <div style={{ color: muted, padding: 20 }}>Select an audit event.</div>;
  return (
    <div>
      <div style={{ borderBottom: hairline, paddingBottom: 12, marginBottom: 12 }}>
        <div style={{ color: SEVERITY[event.severity] || accent, fontSize: 11, letterSpacing: "0.16em", marginBottom: 8 }}>{event.source} / {event.event_type}</div>
        <div style={{ color: accent, fontSize: 20, fontWeight: 900 }}>{event.title}</div>
        <div style={{ color: muted, fontSize: 11, marginTop: 6 }}>{formatTime(event.ts)} · {event.ref_id || "NO REF"}</div>
      </div>
      <Row k="Ticker" v={event.ticker ? `$${event.ticker}` : "-"} />
      <Row k="Status" v={event.status || "-"} />
      <Row k="Summary" v={event.summary || "-"} />
      <pre style={preStyle}>{JSON.stringify(event.payload || {}, null, 2)}</pre>
    </div>
  );
}

function Field({ label, children }) {
  return (
    <label style={{ display: "grid", gap: 6 }}>
      <span style={{ color: dim, fontSize: 9, letterSpacing: "0.16em" }}>{label}</span>
      {children}
    </label>
  );
}

function Row({ k, v }) {
  return (
    <div style={{ display: "grid", gridTemplateColumns: "90px 1fr", gap: 12, padding: "7px 0", borderBottom: hairline, fontSize: 11 }}>
      <span style={{ color: dim, letterSpacing: "0.12em" }}>{k}</span>
      <span style={{ color: labelLight }}>{v}</span>
    </div>
  );
}

function formatTime(value) {
  if (!value) return "-";
  try {
    return new Date(value).toLocaleString("en-US", {
      timeZone: "America/New_York",
      month: "2-digit",
      day: "2-digit",
      hour: "2-digit",
      minute: "2-digit",
      second: "2-digit",
      hour12: false,
    });
  } catch {
    return String(value).slice(0, 19);
  }
}

function sourceColor(source) {
  return source === "options_desk" ? accent : source === "trade_floor" ? accent2 : source === "telegram" ? "#4ade80" : labelLight;
}

function buttonStyle(color) {
  return { background: "transparent", border: `0.5px solid ${color}`, color, fontSize: 11, padding: "8px 16px", cursor: "pointer", letterSpacing: "0.14em", fontFamily: "JetBrains Mono", fontWeight: 700 };
}

const th = { padding: "10px 8px", fontSize: 10, color: dim, letterSpacing: "0.14em", fontWeight: 400, textAlign: "left" };
const td = { padding: "10px 8px", color: labelLight, letterSpacing: "0.04em", fontSize: 12, verticalAlign: "top" };
const filterGrid = { display: "grid", gridTemplateColumns: "180px 220px 140px", gap: 14 };
const mainGrid = { display: "grid", gridTemplateColumns: "minmax(0, 1.45fr) minmax(380px, 0.55fr)", gap: 18, marginTop: 18 };
const inputStyle = { background: "#050507", border: hairline, color: labelLight, padding: "9px 10px", fontFamily: "JetBrains Mono", fontSize: 11, outline: "none" };
const preStyle = { marginTop: 14, padding: 12, maxHeight: 520, overflow: "auto", background: "#050507", border: hairline, color: muted, fontSize: 10, lineHeight: 1.5, whiteSpace: "pre-wrap" };
