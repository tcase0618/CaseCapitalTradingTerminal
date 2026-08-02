import { Activity, AlertTriangle, CheckCircle2, DatabaseZap, Gauge, ShieldCheck } from "lucide-react";
import { tokens } from "./CrtShell";

const { accent, accent2, dim, muted, labelLight, hairline, cardBg } = tokens;

function toneForStatus(value) {
  const s = String(value || "").toUpperCase();
  if (["PASS", "ALLOW", "READY", "LIVE", "ARMED", "ONLINE", "OK", "TRUE"].includes(s)) return "#4ade80";
  if (["WATCH", "WARN", "DEGRADED", "FALLBACK", "STALE", "SYNCING"].includes(s)) return "#fbbf24";
  if (["BLOCK", "DOWN", "OFFLINE", "DISABLED", "ERROR", "FALSE"].includes(s)) return "#f87171";
  return muted;
}

export function StatusPill({ value, label = null, color = null }) {
  const tone = color || toneForStatus(value);
  return (
    <span className="inst-status-pill" style={{ color: tone, borderColor: `${tone}66`, background: `${tone}14` }}>
      <span className="dot" style={{ background: tone, boxShadow: `0 0 9px ${tone}88` }} />
      {label ? `${label}: ` : ""}{String(value || "--").toUpperCase()}
    </span>
  );
}

export function DataConfidenceStrip({ items = [], title = "DATA CONFIDENCE" }) {
  const rows = items.filter(Boolean);
  if (!rows.length) return null;
  return (
    <section className="inst-confidence-strip">
      <div className="inst-confidence-title">
        <DatabaseZap size={15} color={accent2} />
        <span>{title}</span>
      </div>
      <div className="inst-confidence-items">
        {rows.map((item, idx) => {
          const color = item.color || toneForStatus(item.status || item.value);
          return (
            <div className="inst-confidence-item" key={`${item.label || "row"}-${idx}`}>
              <div className="inst-confidence-label">{item.label}</div>
              <div className="inst-confidence-value" style={{ color }}>{item.value ?? item.status ?? "--"}</div>
              {item.detail && <div className="inst-confidence-detail">{item.detail}</div>}
            </div>
          );
        })}
      </div>
    </section>
  );
}

export function OpsHero({ eyebrow, title, detail, metrics = [], status = null, actions = null }) {
  return (
    <section className="inst-ops-hero">
      <div className="inst-ops-copy">
        {eyebrow && <div className="inst-eyebrow">{eyebrow}</div>}
        <h1>{title}</h1>
        {detail && <p>{detail}</p>}
      </div>
      <div className="inst-ops-side">
        {status && (
          <div className="inst-ops-status">
            <ShieldCheck size={18} color={status.color || toneForStatus(status.value)} />
            <div>
              <div className="inst-mini-label">{status.label || "STATUS"}</div>
              <strong style={{ color: status.color || toneForStatus(status.value) }}>{status.value || "--"}</strong>
            </div>
          </div>
        )}
        {!!metrics.length && (
          <div className="inst-mini-grid">
            {metrics.map((m, idx) => <MiniReadout key={`${m.label}-${idx}`} {...m} />)}
          </div>
        )}
        {actions && <div className="inst-hero-actions">{actions}</div>}
      </div>
    </section>
  );
}

export function MiniReadout({ label, value, detail, color = accent }) {
  return (
    <div className="inst-mini-readout">
      <div className="inst-mini-label">{label}</div>
      <div className="inst-mini-value" style={{ color }}>{value ?? "--"}</div>
      {detail && <div className="inst-mini-detail">{detail}</div>}
    </div>
  );
}

export function InstitutionalEmpty({ title = "No data returned.", detail = "Refresh the page or run the relevant sync to repull the source." }) {
  return (
    <div className="inst-empty-state">
      <AlertTriangle size={18} color={accent} />
      <div>
        <div>{title}</div>
        <p>{detail}</p>
      </div>
    </div>
  );
}

export function ActionRail({ items = [] }) {
  const rows = items.filter(Boolean);
  if (!rows.length) return null;
  return (
    <div className="inst-action-rail">
      {rows.map((item, idx) => (
        <div className="inst-action-item" key={`${item.label}-${idx}`}>
          {item.ok === false ? <AlertTriangle size={15} color="#f87171" /> : item.ok === true ? <CheckCircle2 size={15} color="#4ade80" /> : <Activity size={15} color={accent2} />}
          <div>
            <div className="inst-action-label">{item.label}</div>
            <div className="inst-action-detail">{item.detail || "--"}</div>
          </div>
        </div>
      ))}
    </div>
  );
}

export function HealthFrame({ children, title, right = null }) {
  return (
    <section className="inst-health-frame" style={{ background: cardBg, border: hairline }}>
      <div className="inst-health-header">
        <div>
          <Gauge size={14} color={accent2} />
          <span>{title}</span>
        </div>
        {right}
      </div>
      {children}
    </section>
  );
}

export { toneForStatus };
