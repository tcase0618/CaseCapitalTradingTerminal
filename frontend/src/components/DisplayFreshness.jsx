import { useEffect, useState } from "react";
import { RotateCw } from "lucide-react";

const timestamp = value => {
  const parsed = value == null ? NaN : new Date(value).getTime();
  return Number.isFinite(parsed) ? parsed : null;
};
const clock = value => value == null ? "--" : new Date(value).toLocaleString("en-US", {
  timeZone: "America/New_York", month: "short", day: "numeric", hour: "2-digit", minute: "2-digit", second: "2-digit",
});

export default function DisplayFreshness({ updatedAt, data, error, refreshing, onRefresh, maxAge = 90000 }) {
  const [now, setNow] = useState(Date.now);
  useEffect(() => {
    const tick = () => { if (document.visibilityState !== "hidden") setNow(Date.now()); };
    const timer = setInterval(tick, 15000);
    document.addEventListener("visibilitychange", tick);
    return () => { clearInterval(timer); document.removeEventListener("visibilitychange", tick); };
  }, []);
  const retrieved = timestamp(updatedAt);
  const source = [data?.snapshot_at, data?.generated_at, data?.full_cycle_finished_at, data?.finished_at, data?.scanned_at]
    .map(timestamp).find(value => value != null);
  const overdue = retrieved != null && now - retrieved > maxAge;
  const status = error ? "REFRESH FAILED" : refreshing ? "REFRESHING" : retrieved == null ? "NOT RETRIEVED" : overdue ? "DISPLAY AGED" : "DISPLAY LOADED";
  return <div data-testid="display-freshness" style={{ display: "flex", flexWrap: "wrap", alignItems: "center", gap: "6px 18px", fontSize: 10, color: error || overdue ? "#fbbf24" : "#9ca3af", borderBottom: "1px solid #ffffff12", padding: "8px 0", marginBottom: 12 }}>
    <span>{status}</span><span>Retrieved {clock(retrieved)} ET</span>
    <span>Source stamped {clock(source)}{source != null ? " ET" : ""}</span>
    {onRefresh && <button type="button" title="Refresh displayed data" aria-label="Refresh displayed data" disabled={refreshing} onClick={onRefresh} className="workspace-icon-button"><RotateCw size={13} /></button>}
  </div>;
}
