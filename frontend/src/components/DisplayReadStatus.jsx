import { tokens } from "./CrtShell";

const formatTime = value => Number.isFinite(value) && Number.isFinite(new Date(value).getTime())
  ? new Date(value).toLocaleTimeString("en-US", { timeZone: "America/New_York", hour: "2-digit", minute: "2-digit", second: "2-digit" })
  : "--";

export default function DisplayReadStatus({ sources = [], states = {} }) {
  const failures = sources.filter(([path]) => states[path]?.error).length;
  const loaded = sources.filter(([path]) => states[path]?.data != null).length;
  return (
    <details style={{ marginBottom: 12, borderBottom: tokens.hairline, fontSize: 11, color: tokens.muted }}>
      <summary style={{ padding: "8px 0", cursor: "pointer", color: failures ? "#fbbf24" : tokens.labelLight }}>
        DISPLAY SOURCES {loaded}/{sources.length} LOADED{failures ? ` / ${failures} FAILED` : ""}
      </summary>
      <div role="region" aria-label="Display source status" tabIndex={0} style={{ overflowX: "auto", maxWidth: "100%", paddingBottom: 8 }}>
        <table style={{ width: "100%", minWidth: 420, borderCollapse: "collapse" }}>
          <thead><tr>{["SOURCE", "DISPLAY STATE", "LAST RETRIEVED (ET)"].map(text => <th key={text} scope="col" style={{ textAlign: "left", padding: "6px 8px", fontWeight: 400 }}>{text}</th>)}</tr></thead>
          <tbody>{sources.map(([path, , label]) => {
            const state = states[path] || {};
            const status = state.error ? (state.data != null ? "LAST LOADED / FAILED" : "UNAVAILABLE")
              : state.refreshing ? "REFRESHING" : state.data != null ? "LOADED" : "WAITING";
            return <tr key={path}>
              <th scope="row" style={{ textAlign: "left", padding: "6px 8px", fontWeight: 400, color: tokens.labelLight }}>{label || path}</th>
              <td style={{ padding: "6px 8px", color: state.error ? "#fbbf24" : tokens.muted }}>{status}</td>
              <td style={{ padding: "6px 8px", whiteSpace: "nowrap" }}>{formatTime(state.updatedAt)}</td>
            </tr>;
          })}</tbody>
        </table>
      </div>
    </details>
  );
}
