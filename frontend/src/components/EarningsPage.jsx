import { useEffect, useMemo, useState } from "react";
import axios from "axios";
import { API } from "../config";
import { CrtShell, Card, Stat, tokens } from "./CrtShell";

const { accent, accent2, dim, muted, labelLight, hairline } = tokens;
const DAYS = ["MONDAY", "TUESDAY", "WEDNESDAY", "THURSDAY", "FRIDAY"];

const beatColor = (p) =>
  p == null ? muted : p >= 65 ? "#4ade80" : p >= 45 ? "#facc15" : "#f87171";

const ratingColor = (s) =>
  s === "TRADEABLE" ? "#4ade80" : s === "WATCH" ? "#facc15" : s === "AVOID" ? "#f87171" : muted;

const pricingColor = (s) =>
  s === "OPTIONS UNDERPRICED" ? "#4ade80"
    : s === "OPTIONS OVERPRICED" ? "#f87171"
      : s === "FAIRLY PRICED" ? "#facc15"
        : muted;

const stratColor = (s) =>
  s?.includes("CALL") ? "#4ade80"
    : s?.includes("PUT") ? "#f87171"
      : s?.includes("WAIT") || s?.includes("NO TRADE") ? muted
        : accent;

const toneColor = (s) =>
  s === "BULLISH" ? "#4ade80" : s === "BEARISH" ? "#f87171" : s === "MIXED" ? "#facc15" : muted;

const reactionColor = (v) =>
  v == null ? muted : v > 0 ? "#4ade80" : v < 0 ? "#f87171" : "#facc15";

const divergenceColor = (s) =>
  s === "HIGH" ? "#f87171" : s === "MEDIUM" ? "#fb923c" : "#facc15";

export default function EarningsPage() {
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(true);
  const [selected, setSelected] = useState(null);
  const [weekOffset, setWeekOffset] = useState(0);
  const [dispatching, setDispatching] = useState(false);
  const [dispatchStatus, setDispatchStatus] = useState("");
  const [routing, setRouting] = useState(false);
  const [routeStatus, setRouteStatus] = useState("");
  const [lseHealth, setLseHealth] = useState(null);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    axios.get(`${API}/v32/earnings_week`, { params: { week_offset: weekOffset } }).then(r => {
      if (!cancelled) {
        setData(r.data);
        setLoading(false);
      }
    }).catch(() => {
      if (!cancelled) setLoading(false);
    });
    return () => { cancelled = true; };
  }, [weekOffset]);

  useEffect(() => {
    let cancelled = false;
    axios.get(`${API}/data/lse/health`).then(r => {
      if (!cancelled) setLseHealth(r.data);
    }).catch(() => {
      if (!cancelled) setLseHealth({ ok: false });
    });
    return () => { cancelled = true; };
  }, []);

  const byDay = useMemo(() => data?.by_day || {}, [data]);
  const allRows = useMemo(() => Object.values(byDay).flat(), [byDay]);
  const total = data?.total || 0;
  const sourceCounts = data?.calendar_source_counts || {};
  const sourceSummary = Object.entries(sourceCounts)
    .filter(([, count]) => Number(count) > 0)
    .map(([source, count]) => `${shortSource(source)} ${count}`)
    .join(" | ");
  const sourceSummaryWithLse = [lseHealth?.ok ? "LSE LIVE" : "LSE DEGRADED", sourceSummary].filter(Boolean).join(" | ");
  const tradeable = allRows.filter(r => r.earnings_setup_rating === "TRADEABLE").length;
  const avoid = allRows.filter(r => r.earnings_setup_rating === "AVOID").length;
  const underpriced = allRows.filter(r => r.options_pricing_signal === "OPTIONS UNDERPRICED").length;
  const caseMatches = allRows.filter(r => r.axiom_match).length;
  const divergences = data?.earnings_divergences || [];
  const topSetups = [...allRows]
    .sort((a, b) => (b.earnings_setup_score || 0) - (a.earnings_setup_score || 0))
    .slice(0, 5);
  const avoidZone = allRows
    .filter(r => (r.avoid_flags || []).length || r.earnings_setup_rating === "AVOID")
    .sort((a, b) => (a.earnings_setup_score || 0) - (b.earnings_setup_score || 0))
    .slice(0, 6);
  const postPrint = allRows
    .filter(r => isPastDate(r.earnings_date))
    .sort((a, b) => Math.abs(b.post_earnings_reaction?.reaction_pct || 0) - Math.abs(a.post_earnings_reaction?.reaction_pct || 0));
  const sendDivergences = async () => {
    setDispatching(true);
    setDispatchStatus("");
    try {
      const res = await axios.post(`${API}/v32/earnings_divergences/dispatch`, null, { params: { week_offset: weekOffset } });
      setDispatchStatus(res.data?.ok ? `SENT ${res.data.divergence_count || 0}` : "SEND FAILED");
    } catch {
      setDispatchStatus("SEND FAILED");
    } finally {
      setDispatching(false);
    }
  };

  const routeToPm = async () => {
    setRouting(true);
    setRouteStatus("");
    try {
      const res = await axios.post(`${API}/v32/earnings_pm/route`, null, { params: { week_offset: weekOffset, min_score: 58 } });
      setRouteStatus(res.data?.ok ? `PM ROUTED ${res.data.routed || 0}` : "PM ROUTE FAILED");
    } catch {
      setRouteStatus("PM ROUTE FAILED");
    } finally {
      setRouting(false);
    }
  };

  return (
    <CrtShell title="EARNINGS WAR ROOM">
      <div className="earnings-war-room">
      <div style={weekNav}>
        <button style={weekButton} onClick={() => setWeekOffset(v => v - 1)}>{"<"} PRIOR WEEK</button>
        <div style={{ textAlign: "center" }}>
          <div style={{ color: dim, fontSize: 10, letterSpacing: "0.16em" }}>EARNINGS WEEK</div>
          <div style={{ color: accent, fontSize: 18, fontWeight: 900 }}>
            {data?.week_of || "-"} TO {data?.week_end || "-"}
          </div>
        </div>
        <div style={{ display: "flex", gap: 8 }}>
          <button style={weekButton} onClick={routeToPm} disabled={routing}>
            {routing ? "ROUTING..." : "ROUTE TO PM"}
          </button>
          <button style={weekButton} onClick={() => setWeekOffset(0)}>CURRENT</button>
          <button style={weekButton} onClick={() => setWeekOffset(v => v + 1)}>NEXT WEEK {">"}</button>
        </div>
      </div>

      <div className="earnings-stat-strip" style={{ display: "flex", background: tokens.cardBg, border: hairline, marginBottom: 22, flexWrap: "wrap" }}>
        <Stat label="WEEK OF" value={data?.week_of?.slice(5) || "-"} sub={data?.week_end?.slice(5) || ""} accentBar />
        <Stat label="ENRICHED PRINTS" value={total} sub={data?.calendar_limited ? `${data.raw_calendar_total} RAW` : "FULL BOARD"} color={accent} />
        <Stat label="TRADEABLE" value={tradeable} color="#4ade80" />
        <Stat label="AVOID ZONE" value={avoid} color="#f87171" />
        <Stat label="UNDERPRICED IV" value={underpriced} color={accent2} />
        <Stat label="CASE MATCHES" value={caseMatches} color={accent2} />
        <Stat label="DIVERGENCES" value={data?.earnings_divergence_count || 0} color="#fb923c" />
      </div>

      <div style={sourceStrip}>
        <span>SOURCES: {sourceSummaryWithLse || "NO LIVE SOURCE ROWS"}</span>
        <span>CACHE: {data?.cache_status || "PENDING"}{data?.cache_age_minutes != null ? ` | ${data.cache_age_minutes}M OLD` : ""}</span>
        <span>PM: {routeStatus || "ADVISORY READY"}</span>
      </div>

      {loading && (
        <div style={{ color: muted, padding: 30, textAlign: "center" }}>
          LOADING EARNINGS WAR ROOM...
        </div>
      )}

      {!loading && total === 0 && (
        <Card title="NO EARNINGS THIS WEEK">
          <div style={{ color: muted, padding: 20 }}>
            No reporting tickers found for the current Monday-Friday window.
            <div style={{ marginTop: 12, color: dim, fontSize: 12 }}>
              Source checks: {sourceSummary || "Yahoo/Nasdaq/Alpha Vantage returned no rows for this week."}
            </div>
          </div>
        </Card>
      )}

      {topSetups.length > 0 && (
        <Card title="SETUP LEADERBOARD">
          <div className="earnings-setup-grid" style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(210px, 1fr))", gap: 12 }}>
            {topSetups.map((r, i) => (
              <button key={`${r.ticker}-${r.earnings_date}-top`} onClick={() => setSelected(r)}
                className="corner-brackets" style={setupCardStyle(r)}>
                <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", gap: 8 }}>
                  <span style={{ color: muted, fontSize: 9, letterSpacing: "0.16em" }}>#{i + 1} | {r.am_pm || "TBD"}</span>
                  <span style={{ color: ratingColor(r.earnings_setup_rating), fontSize: 9, fontWeight: 900 }}>
                    {r.earnings_setup_rating || "UNKNOWN"}
                  </span>
                </div>
                <div style={{ display: "flex", justifyContent: "space-between", alignItems: "end", marginTop: 8 }}>
                  <div>
                    <div style={{ color: accent, fontSize: 28, fontWeight: 900 }}>${r.ticker}</div>
                    <div style={{ color: muted, fontSize: 10 }}>{(r.sector || "-").slice(0, 26)}</div>
                  </div>
                  <div style={{ color: ratingColor(r.earnings_setup_rating), fontSize: 32, fontWeight: 900 }}>
                    {fmt0(r.earnings_setup_score)}
                  </div>
                </div>
                <div style={{ marginTop: 10, display: "grid", gridTemplateColumns: "1fr 1fr", gap: 8 }}>
                  <MiniMetric label="BEAT" value={`${fmt0(r.beat_probability_pct)}%`} color={beatColor(r.beat_probability_pct)} />
                  <MiniMetric label="IV SIGNAL" value={shortPricing(r.options_pricing_signal)} color={pricingColor(r.options_pricing_signal)} />
                  <MiniMetric label="IMPL MOVE" value={pct(r.options?.implied_move_pct)} />
                  <MiniMetric label="HIST MOVE" value={pct(r.historical_moves?.avg_abs_move_pct)} />
                </div>
              </button>
            ))}
          </div>
        </Card>
      )}

      <Card title="THIS WEEK'S EARNINGS BOARD">
        {DAYS.map(day => {
          const rows = byDay[day] || [];
          if (!rows.length) return null;
          return (
            <div key={day} style={{ marginBottom: 18 }}>
              <div style={{ color: dim, fontSize: 10, letterSpacing: "0.16em", marginBottom: 8 }}>
                {day} | {rows.length} REPORTING
              </div>
              <div className="earnings-table-wrap" style={{ width: "100%" }}>
                <table className="earnings-table" style={earningsTable}>
                  <thead>
                    <tr style={{ color: dim, letterSpacing: "0.08em", textAlign: "left" }}>
                      <th style={{ ...th, width: "10%" }}>TICKER</th>
                      <th style={{ ...th, width: "8%" }}>WHEN</th>
                      <th style={{ ...th, width: "13%" }}>SETUP</th>
                      <th style={{ ...th, width: "10%" }}>BEAT</th>
                      <th style={{ ...th, width: "15%" }}>MOVE</th>
                      <th style={{ ...th, width: "11%" }}>IV</th>
                      <th style={{ ...th, width: "9%" }}>20D</th>
                      <th style={{ ...th, width: "16%" }}>STRUCTURE</th>
                      <th style={{ ...th, width: "8%" }}>CARD</th>
                    </tr>
                  </thead>
                  <tbody>
                    {rows.map(r => (
                      <tr key={`${r.ticker}-${r.earnings_date}`} data-testid={`earnings-${r.ticker}`}
                        className="row-hover" style={{ borderTop: hairline }}>
                        <td style={{ ...td, color: accent, fontWeight: 900 }}>${r.ticker}</td>
                        <td style={{ ...td, color: r.am_pm === "AM" ? "#fb923c" : "#60a5fa", fontWeight: 800 }}>
                          {r.am_pm || "TBD"}
                        </td>
                        <td style={{ ...td, color: ratingColor(r.earnings_setup_rating), fontWeight: 900 }}>
                          {fmt0(r.earnings_setup_score)} | {shortRating(r.earnings_setup_rating)}
                        </td>
                        <td style={{ ...td, color: beatColor(r.beat_probability_pct), fontWeight: 900 }}>
                          {fmt0(r.beat_probability_pct)}%
                        </td>
                        <td style={{ ...td, color: pricingColor(r.options_pricing_signal), fontWeight: 800 }}>
                          {pct(r.options?.implied_move_pct)} / {pct(r.historical_moves?.avg_abs_move_pct)}
                        </td>
                        <td style={td}>{r.options?.iv_rank != null ? `${r.options.iv_rank}% ${shortIv(r.options.iv_label)}` : "-"}</td>
                        <td style={{ ...td, color: pctColor(r.momentum_20d_pct) }}>{pct(r.momentum_20d_pct)}</td>
                        <td style={{ ...td, color: stratColor(r.option_strategy?.name), fontWeight: 800 }}>
                          {shortStrategy(r.option_strategy?.name || r.strategy)}
                        </td>
                        <td style={td}>
                          <button onClick={() => setSelected(r)} style={battleIconButton}>OPEN</button>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
          );
        })}
      </Card>

      <div className="earnings-below-grid" style={belowBoardGrid}>
          <Card title="DIVERGENCE RADAR">
            <div style={{ display: "flex", justifyContent: "space-between", gap: 12, alignItems: "center", marginBottom: 10 }}>
              <div style={{ color: muted, fontSize: 12 }}>
                Call tone versus price reaction mismatches for the selected week.
              </div>
              <button onClick={sendDivergences} disabled={dispatching} style={telegramButton}>
                {dispatching ? "SENDING" : "SEND TO TELEGRAM"}
              </button>
            </div>
            {dispatchStatus && <div style={{ color: dispatchStatus.includes("FAILED") ? "#f87171" : "#4ade80", fontSize: 11, marginBottom: 8 }}>{dispatchStatus}</div>}
            {divergences.length === 0 ? (
              <div style={{ color: muted, fontSize: 13 }}>No active divergences in this selected week.</div>
            ) : divergences.map(r => {
              const div = r.earnings_divergence || {};
              return (
                <button key={`${r.ticker}-divergence`} onClick={() => setSelected(r)} style={divergenceRow}>
                  <div style={{ display: "flex", justifyContent: "space-between", gap: 10, alignItems: "center" }}>
                    <div>
                      <div style={{ color: accent, fontWeight: 900 }}>${r.ticker}</div>
                      <div style={{ color: muted, fontSize: 10 }}>{div.label || "-"}</div>
                    </div>
                    <div style={{ color: divergenceColor(div.severity), fontWeight: 900 }}>{div.severity || "-"}</div>
                  </div>
                  <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 8, marginTop: 8 }}>
                    <MiniMetric label="TONE" value={r.earnings_call_tone?.tone || "-"} color={toneColor(r.earnings_call_tone?.tone)} />
                    <MiniMetric label="REACTION" value={pct(r.post_earnings_reaction?.reaction_pct)} color={reactionColor(r.post_earnings_reaction?.reaction_pct)} />
                  </div>
                  <div style={{ color: labelLight, fontSize: 11, marginTop: 8, lineHeight: 1.45 }}>{div.read || "-"}</div>
                </button>
              );
            })}
          </Card>

          <Card title="AVOID ZONE">
            {avoidZone.length === 0 ? (
              <div style={{ color: muted, fontSize: 13 }}>No hard avoid flags in the current week.</div>
            ) : avoidZone.map(r => (
              <button key={`${r.ticker}-avoid`} onClick={() => setSelected(r)} style={sideRow}>
                <div>
                  <div style={{ color: accent, fontWeight: 900 }}>${r.ticker}</div>
                  <div style={{ color: muted, fontSize: 11 }}>{(r.avoid_flags || ["Low setup score"])[0]}</div>
                </div>
                <div style={{ color: ratingColor(r.earnings_setup_rating), fontWeight: 900 }}>{fmt0(r.earnings_setup_score)}</div>
              </button>
            ))}
          </Card>

          <Card title="POST-EARNINGS TRACKER">
            {postPrint.length === 0 ? (
              <div style={{ color: muted, fontSize: 13 }}>
                No completed earnings events in this selected week yet. Flip to a prior week once earnings season is underway.
              </div>
            ) : postPrint.map(r => (
              <button key={`${r.ticker}-post`} onClick={() => setSelected(r)} style={postRow}>
                <div style={{ display: "flex", justifyContent: "space-between", gap: 10, alignItems: "center" }}>
                  <div>
                    <div style={{ color: accent, fontWeight: 900 }}>${r.ticker}</div>
                    <div style={{ color: muted, fontSize: 10 }}>{r.earnings_date} | {r.am_pm || "TBD"}</div>
                  </div>
                  <div style={{ color: reactionColor(r.post_earnings_reaction?.reaction_pct), fontWeight: 900, fontSize: 18 }}>
                    {pct(r.post_earnings_reaction?.reaction_pct)}
                  </div>
                </div>
                <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 8, marginTop: 8 }}>
                  <MiniMetric label="CALL TONE" value={r.earnings_call_tone?.tone || "-"} color={toneColor(r.earnings_call_tone?.tone)} />
                  <MiniMetric label="REACTION" value={r.post_earnings_reaction?.reaction_label || "-"} color={reactionColor(r.post_earnings_reaction?.reaction_pct)} />
                </div>
                <div style={{ color: muted, fontSize: 10, marginTop: 8, lineHeight: 1.4 }}>
                  {(r.battle_card?.earnings_call_synopsis?.text || "No call context available.").slice(0, 120)}
                </div>
              </button>
            ))}
          </Card>
      </div>

      {selected && <BattleCard row={selected} onClose={() => setSelected(null)} />}
      </div>
    </CrtShell>
  );
}

function shortSource(source) {
  if (source.includes("Alpha Vantage")) return "AV";
  if (source.includes("Nasdaq")) return "NASDAQ";
  if (source.includes("Yahoo")) return "YAHOO";
  return source.toUpperCase();
}

function BattleCard({ row, onClose }) {
  const card = row.battle_card || {};
  const synopsis = card.earnings_call_synopsis || {};
  const divergence = row.earnings_divergence || card.divergence || {};
  return (
    <div className="earnings-battle-overlay" style={modalOverlay} onClick={onClose}>
      <div className="earnings-battle-panel" style={modalPanel} onClick={(e) => e.stopPropagation()}>
        <div style={{ display: "flex", justifyContent: "space-between", gap: 16, alignItems: "start" }}>
          <div>
            <div style={{ color: dim, fontSize: 11, letterSpacing: "0.16em" }}>
              EARNINGS BATTLE CARD | {row.earnings_date} | {row.am_pm || "TBD"}
            </div>
            <div style={{ color: accent, fontSize: 42, fontWeight: 900, marginTop: 4 }}>${row.ticker}</div>
            <div style={{ color: muted, fontSize: 12 }}>{row.sector || "-"} | {row.industry || "-"}</div>
          </div>
          <button onClick={onClose} style={closeButton}>CLOSE</button>
        </div>

        <div style={{ display: "grid", gridTemplateColumns: "repeat(4, 1fr)", gap: 10, marginTop: 18 }}>
          <MiniMetric label="SETUP SCORE" value={fmt0(row.earnings_setup_score)} color={ratingColor(row.earnings_setup_rating)} />
          <MiniMetric label="BEAT PROB" value={`${fmt0(row.beat_probability_pct)}%`} color={beatColor(row.beat_probability_pct)} />
          <MiniMetric label="IMPLIED MOVE" value={pct(row.options?.implied_move_pct)} color={pricingColor(row.options_pricing_signal)} />
          <MiniMetric label="HIST MOVE" value={pct(row.historical_moves?.avg_abs_move_pct)} />
        </div>

        <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 18, marginTop: 18 }}>
          <Panel title="BULL CASE" items={card.bull_case || []} color="#4ade80" />
          <Panel title="BEAR CASE" items={card.bear_case || []} color="#f87171" />
        </div>

        <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 18, marginTop: 18 }}>
          <InfoBlock title="CALL SYNOPSIS" text={synopsis.text || "No earnings-call synopsis available from current free sources."} sub={synopsis.source} />
          <InfoBlock title="TRADE STRUCTURE" text={`${card.best_structure?.name || row.option_strategy?.name || "-"}: ${card.best_structure?.reason || row.option_strategy?.reason || ""}`} sub={row.options_pricing_signal} />
        </div>

        <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr 1fr", gap: 10, marginTop: 18 }}>
          <MiniMetric label="KEY NUMBER" value={card.key_number_to_watch || "-"} />
          <MiniMetric label="GUIDANCE RISK" value={card.guidance_risk || "-"} color={card.guidance_risk === "LOW" ? "#4ade80" : card.guidance_risk === "HIGH" ? "#f87171" : "#facc15"} />
          <MiniMetric label="FINAL RATING" value={card.rating || row.earnings_setup_rating || "-"} color={ratingColor(card.rating || row.earnings_setup_rating)} />
        </div>

        {(row.post_earnings_reaction || row.earnings_call_tone) && (
          <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 10, marginTop: 18 }}>
            <MiniMetric label="CALL TONE" value={row.earnings_call_tone?.tone || "-"} color={toneColor(row.earnings_call_tone?.tone)} />
            <MiniMetric
              label="POST-PRINT REACTION"
              value={`${pct(row.post_earnings_reaction?.reaction_pct)} | ${row.post_earnings_reaction?.reaction_label || "-"}`}
              color={reactionColor(row.post_earnings_reaction?.reaction_pct)}
            />
          </div>
        )}

        {divergence?.label && (
          <div style={{ marginTop: 18 }}>
            <InfoBlock
              title={`DIVERGENCE RADAR | ${divergence.severity || "LOW"}`}
              text={`${divergence.label}: ${divergence.read || ""}`}
              sub={divergence.action}
            />
          </div>
        )}

        {(row.avoid_flags || []).length > 0 && (
          <div style={{ marginTop: 16, display: "flex", gap: 8, flexWrap: "wrap" }}>
            {row.avoid_flags.map(flag => (
              <span key={flag} style={flagPill}>{flag}</span>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}

function Panel({ title, items, color }) {
  return (
    <div style={{ border: `0.5px solid ${color}55`, padding: 12, background: "rgba(255,255,255,0.025)" }}>
      <div style={{ color, fontSize: 11, fontWeight: 900, letterSpacing: "0.14em", marginBottom: 8 }}>{title}</div>
      {(items || []).map(item => (
        <div key={item} style={{ color: labelLight, fontSize: 12, lineHeight: 1.6, marginBottom: 6 }}>{item}</div>
      ))}
    </div>
  );
}

function InfoBlock({ title, text, sub }) {
  return (
    <div style={{ border: hairline, padding: 12, background: "rgba(255,255,255,0.025)" }}>
      <div style={{ color: dim, fontSize: 11, letterSpacing: "0.14em", marginBottom: 8 }}>{title}</div>
      <div style={{ color: labelLight, fontSize: 13, lineHeight: 1.6 }}>{text}</div>
      {sub && <div style={{ color: muted, fontSize: 10, marginTop: 8, letterSpacing: "0.1em" }}>{sub}</div>}
    </div>
  );
}

function MiniMetric({ label, value, color = labelLight }) {
  return (
    <div style={{ border: hairline, padding: "8px 9px", minHeight: 50, background: "rgba(255,255,255,0.025)" }}>
      <div style={{ color: dim, fontSize: 9, letterSpacing: "0.12em" }}>{label}</div>
      <div style={{ color, fontSize: 15, fontWeight: 900, marginTop: 5, lineHeight: 1.2 }}>{value == null ? "-" : value}</div>
    </div>
  );
}

function setupCardStyle(r) {
  const color = ratingColor(r.earnings_setup_rating);
  return {
    textAlign: "left",
    padding: "14px 16px",
    border: `0.5px solid ${color}55`,
    background: `linear-gradient(135deg, ${color}12 0%, transparent 70%)`,
    cursor: "pointer",
  };
}

const th = { padding: "10px 8px", fontSize: 10, color: dim, letterSpacing: "0.12em", fontWeight: 400 };
const td = {
  padding: "10px 8px",
  color: labelLight,
  letterSpacing: "0.02em",
  verticalAlign: "middle",
  overflowWrap: "anywhere",
};
const earningsTable = {
  width: "100%",
  tableLayout: "fixed",
  borderCollapse: "collapse",
  fontSize: 11,
};
const battleIconButton = {
  border: `0.5px solid ${accent}`,
  color: accent,
  background: "rgba(200,168,75,0.08)",
  padding: "5px 6px",
  fontSize: 9,
  fontWeight: 900,
  letterSpacing: "0.04em",
  cursor: "pointer",
  width: "100%",
};
const sourceStrip = {
  display: "flex",
  justifyContent: "space-between",
  gap: 12,
  flexWrap: "wrap",
  border: hairline,
  background: "rgba(255,255,255,0.025)",
  color: dim,
  fontSize: 10,
  letterSpacing: "0.12em",
  padding: "9px 12px",
  marginTop: -12,
  marginBottom: 20,
};
const weekNav = {
  display: "flex",
  justifyContent: "space-between",
  gap: 14,
  alignItems: "center",
  border: hairline,
  background: "rgba(255,255,255,0.025)",
  padding: "12px 14px",
  marginBottom: 14,
  flexWrap: "wrap",
};
const weekButton = {
  border: `0.5px solid ${accent}`,
  color: accent,
  background: "rgba(200,168,75,0.08)",
  padding: "8px 10px",
  fontSize: 10,
  fontWeight: 900,
  letterSpacing: "0.1em",
  cursor: "pointer",
};
const telegramButton = {
  border: `0.5px solid ${accent2}`,
  color: accent2,
  background: "rgba(0,245,212,0.06)",
  padding: "7px 9px",
  fontSize: 9,
  fontWeight: 900,
  letterSpacing: "0.08em",
  cursor: "pointer",
};
const belowBoardGrid = {
  display: "grid",
  gridTemplateColumns: "repeat(auto-fit, minmax(320px, 1fr))",
  gap: 20,
  alignItems: "start",
};
const divergenceRow = {
  width: "100%",
  display: "block",
  border: "none",
  borderTop: hairline,
  background: "transparent",
  textAlign: "left",
  padding: "12px 0",
  cursor: "pointer",
};
const sideRow = {
  width: "100%",
  display: "flex",
  justifyContent: "space-between",
  gap: 12,
  alignItems: "center",
  border: "none",
  borderTop: hairline,
  background: "transparent",
  textAlign: "left",
  padding: "10px 0",
  cursor: "pointer",
};
const postRow = {
  width: "100%",
  display: "block",
  border: "none",
  borderTop: hairline,
  background: "transparent",
  textAlign: "left",
  padding: "12px 0",
  cursor: "pointer",
};
const modalOverlay = {
  position: "fixed",
  inset: 0,
  background: "rgba(0,0,0,0.78)",
  zIndex: 1000,
  display: "flex",
  alignItems: "center",
  justifyContent: "center",
  padding: 24,
};
const modalPanel = {
  width: "min(1020px, 96vw)",
  maxHeight: "90vh",
  overflowY: "auto",
  border: `1px solid ${accent}`,
  background: "#08090d",
  boxShadow: `0 0 34px rgba(200,168,75,0.18)`,
  padding: 22,
};
const closeButton = {
  border: `0.5px solid ${muted}`,
  color: muted,
  background: "transparent",
  padding: "7px 10px",
  fontSize: 10,
  fontWeight: 900,
  letterSpacing: "0.12em",
  cursor: "pointer",
};
const flagPill = {
  color: "#f87171",
  border: "0.5px solid #f87171",
  background: "rgba(248,113,113,0.07)",
  padding: "5px 8px",
  fontSize: 10,
  letterSpacing: "0.08em",
};

function fmt0(v) {
  const n = Number(v);
  return Number.isFinite(n) ? n.toFixed(0) : "-";
}

function pct(v) {
  const n = Number(v);
  if (!Number.isFinite(n)) return "-";
  return `${n >= 0 ? "+" : ""}${n.toFixed(1)}%`;
}

function pctColor(v) {
  const n = Number(v);
  if (!Number.isFinite(n)) return muted;
  return n >= 0 ? "#4ade80" : "#f87171";
}

function shortPricing(s) {
  if (s === "OPTIONS UNDERPRICED") return "UNDER";
  if (s === "OPTIONS OVERPRICED") return "OVER";
  if (s === "FAIRLY PRICED") return "FAIR";
  return "-";
}

function shortRating(s) {
  if (s === "TRADEABLE") return "TRADE";
  if (s === "WATCH") return "WATCH";
  if (s === "AVOID") return "AVOID";
  return "-";
}

function shortIv(s) {
  if (s === "EXPENSIVE") return "EXP";
  if (s === "ELEVATED") return "ELEV";
  if (s === "CHEAP") return "CHP";
  if (s === "FAIR") return "FAIR";
  return s || "";
}

function shortStrategy(s) {
  if (!s) return "-";
  if (s.includes("LONG CALL")) return "LONG CALL";
  if (s.includes("CALL DEBIT")) return "CALL SPRD";
  if (s.includes("PUT DEBIT")) return "PUT SPRD";
  if (s.includes("WAIT")) return "WAIT";
  if (s.includes("NO TRADE")) return "NO TRADE";
  if (s.includes("WATCHLIST")) return "WATCH";
  return s.replace(" / ", "/").slice(0, 14);
}

function isPastDate(iso) {
  if (!iso) return false;
  const today = new Date();
  today.setHours(0, 0, 0, 0);
  const d = new Date(`${iso}T00:00:00`);
  return d < today;
}
