import { useCallback, useEffect, useRef, useState } from "react";
import axios from "axios";
import { API } from "../config";
import { CrtShell, Card, Stat, tokens } from "./CrtShell";

const { accent, accent2, dim, muted, labelLight, hairline, cardBg, pageBg } = tokens;
const record = value => value && typeof value === "object" && !Array.isArray(value);
const list = value => Array.isArray(value) ? value.filter(record) : [];
const textList = value => Array.isArray(value) ? value.filter(item => typeof item === "string") : [];
const num = value => (typeof value === "number" || (typeof value === "string" && value.trim())) && Number.isFinite(Number(value)) ? Number(value) : null;
const metric = (value, suffix = "") => num(value) == null ? "-" : `${num(value)}${suffix}`;
const text = value => typeof value === "string" || typeof value === "number" ? value : "-";
const returnColor = value => num(value) == null ? muted : num(value) >= 0 ? "#4ade80" : "#f87171";

const TIER = {
  JACKPOT: "#d7bd68",
  HOT: "#fb923c",
  WATCH: "#5eead4",
  REJECT: "#6b7280",
};

const VARIANTS = [
  ["V1_DAY2_CONTINUATION", "DAY-2 CONTINUATION"],
  ["V2_OPENING_BREAK_SHADOW", "OPENING BREAK SHADOW"],
  ["V3_RED_TO_GREEN", "RED-TO-GREEN"],
  ["V4_OPTIONS_TICKET", "OPTIONS TICKET"],
];

export default function LotteryPage() {
  const [board, setBoard] = useState(null);
  const [tab, setTab] = useState("candidates");
  const [page, setPage] = useState(0);
  const [loading, setLoading] = useState(true);
  const [scanning, setScanning] = useState(false);
  const [toast, setToast] = useState(null);
  const requestRef = useRef(null);
  const toastTimer = useRef(null);
  const [loadError, setLoadError] = useState(false);
  const mountedRef = useRef(false);

  const showToast = (kind, msg) => {
    if (!mountedRef.current) return;
    setToast({ kind, msg });
    clearTimeout(toastTimer.current);
    toastTimer.current = setTimeout(() => setToast(null), 4500);
  };

  const load = useCallback(async () => {
    if (!mountedRef.current) return;
    requestRef.current?.abort();
    const controller = new AbortController(); requestRef.current = controller;
    setLoading(true);
    setLoadError(false);
    try {
      const { data } = await axios.get(`${API}/lottery/board`, { signal: controller.signal, timeout: 20000 });
      if (!controller.signal.aborted) { setBoard(record(data) ? data : null); setLoadError(!record(data)); }
    } catch (e) {
      if (!controller.signal.aborted) { setLoadError(true); showToast("err", `Lottery League load failed: ${e?.message || "unknown"}`); }
    } finally {
      if (!controller.signal.aborted) setLoading(false);
    }
  }, []);

  // Board reads grade closed tickets in the backend; do not introduce polling.
  useEffect(() => { mountedRef.current = true; load(); return () => { mountedRef.current = false; requestRef.current?.abort(); clearTimeout(toastTimer.current); }; }, [load]);

  const runScan = async () => {
    setScanning(true);
    try {
      const { data } = await axios.post(`${API}/lottery/scan`);
      showToast("ok", `Lottery League scan complete - ${metric(data.count)} candidates`);
      await load();
      setTab("candidates");
    } catch (e) {
      showToast("err", `Scan failed: ${e?.response?.data?.detail || e?.message || "unknown"}`);
    } finally {
      setScanning(false);
    }
  };

  const issueTicket = async (candidate) => {
    const defaultPrice = candidate.price ? String(candidate.price) : "";
    const entry = window.prompt(`Entry price for ${candidate.ticker}?`, defaultPrice);
    if (!entry) return;
    const variant = window.prompt("Variant", "V1_DAY2_CONTINUATION") || "V1_DAY2_CONTINUATION";
    try {
      const { data } = await axios.post(`${API}/lottery/ticket`, {
        ticker: candidate.ticker,
        entry_price: Number(entry),
        variant,
        score: candidate.score,
        reason: "operator_candidate_board",
      });
      if (!data.ok) {
        showToast("err", `Ticket refused: ${data.reason}`);
      } else {
        showToast("ok", `${candidate.ticker} ticket opened`);
        await load();
        setTab("tickets");
      }
    } catch (e) {
      showToast("err", `Ticket failed: ${e?.response?.data?.detail || e?.message || "unknown"}`);
    }
  };

  const settleTicket = async (ticket) => {
    const exit = window.prompt(`Exit price for ${ticket.ticker}?`, ticket.current_price || ticket.entry_price || "");
    if (!exit) return;
    try {
      const { data } = await axios.post(`${API}/lottery/ticket/settle`, null, {
        params: { ticket_id: ticket.ticket_id, exit_price: Number(exit), reason: "operator_settle" },
      });
      showToast(data.ok ? "ok" : "err", data.ok ? `${ticket.ticker} settled` : `Settle failed: ${data.reason}`);
      await load();
    } catch (e) {
      showToast("err", `Settle failed: ${e?.response?.data?.detail || e?.message || "unknown"}`);
    }
  };

  const candidates = list(board?.candidates);
  const tickets = list(board?.tickets).map(row => ({ ...row, ladder: list(row.ladder) }));
  const grades = board?.jackpot_board || {};
  const truth = board?.truth_board || {};
  const truthCombined = truth?.combined || {};
  const learning = truth?.learning || {};
  const learnedConfig = truth?.learned_config || {};
  const book = board?.book || {};
  const gate = board?.gate || {};
  const top = candidates[0] || null;
  const candidatesAvailable = Array.isArray(board?.candidates);
  const ticketsAvailable = Array.isArray(board?.tickets);
  const incomplete = board != null && (!candidatesAvailable || !ticketsAvailable || candidates.length !== board.candidates.length || tickets.length !== board.tickets.length || !record(board.book) || !record(board.gate) || !record(board.jackpot_board) || !record(board.truth_board) || board.truth_board.ok === false || [board.truth_board.segments?.variant, board.truth_board.segments?.catalyst_class, board.truth_board.segments?.float_tier, board.truth_board.segments?.score_bucket, board.truth_board.latest_grades].some(value => value != null && (!Array.isArray(value) || list(value).length !== value.length)));
  const pageCount = Math.max(1, Math.ceil(candidates.length / 30));
  const activePage = Math.min(page, pageCount - 1);
  useEffect(() => { setPage(0); }, [board]);
  const eligible = candidates.filter(c => c.eligible).length;
  const jackpot = candidates.filter(c => c.tier === "JACKPOT").length;

  return (
    <CrtShell
      title="LOTTERY LEAGUE"
      headerRight={
        <button data-testid="lottery-scan-btn" onClick={runScan} disabled={scanning} style={primaryBtn(scanning)}>
          {scanning ? "SCANNING..." : "RUN LEAGUE SCAN"}
        </button>
      }
    >
      {loading && <div role="status" style={{ color: muted, marginBottom: 12 }}>Loading Lottery board...</div>}
      {loadError && <div role="status" style={{ color: "#fbbf24", marginBottom: 12 }}>Lottery display unavailable. Any retained rows are from the previous load, not a new scan.</div>}
      {incomplete && <div role="status" style={{ color: "#fbbf24", marginBottom: 12 }}>Lottery response is incomplete. Missing observations are unavailable, not zero performance.</div>}
      <section className="lottery-hero" style={hero}>
        <div style={{ minWidth: 0 }}>
          <div style={eyebrow}>MOONSHOT DESK / SEPARATE BOOK / HAIRCUT TRUTH</div>
          <h1 style={h1}>Conditional tail-lifting, not fantasy probability.</h1>
          <p style={lede}>
            Low-float runners underperform on average. The League only tests whether catalyst,
            rotation, structure, and strict ticket sizing can lift the right tail enough to matter.
          </p>
        </div>
        <div style={gateBox}>
          <span style={{ color: gate.color === "red" ? "#f87171" : gate.color === "green" ? "#4ade80" : muted }}>{text(gate.status || "UNKNOWN")}</span>
          <small>{gate.reason || "No regime gate loaded."}</small>
        </div>
      </section>

      <div className="lottery-stat-strip" style={statStrip}>
        <Stat label="CANDIDATES" value={candidatesAvailable ? candidates.length : "-"} sub={`${candidatesAvailable ? eligible : "-"} ELIGIBLE`} color={accent2} accentBar />
        <Stat label="JACKPOT TIER" value={candidatesAvailable ? jackpot : "-"} sub="SCORE >= 80" color={TIER.JACKPOT} />
        <Stat label="OPEN TICKETS" value={ticketsAvailable ? tickets.length : "-"} sub={`${metric(book.max_open_tickets)} MAX`} color="#4ade80" />
        <Stat label="CAP USAGE" value={fmtPct(book.cap_usage_pct)} sub={`${fmtMoney(book.deployed_dollars)} DEPLOYED`} color={accent} />
        <Stat label="EV/TICKET" value={fmtPct(truthCombined.ev_per_ticket_pct_haircut ?? grades.ev_per_ticket_pct_haircut)} sub="CLOSED / HAIRCUT" color={returnColor(truthCombined.ev_per_ticket_pct_haircut ?? grades.ev_per_ticket_pct_haircut)} />
        <Stat label="KILL STATUS" value={text(truthCombined.decision_status || grades.kill_status || "UNKNOWN")} sub={`${metric(truthCombined.n_to_decision ?? grades.n_to_variant_decision)} TO DECISION`} color={truthCombined.decision_status === "RETIRE" || grades.kill_status === "RETIRE_VARIANT" ? "#f87171" : accent2} />
      </div>

      <div className="lottery-league-grid" style={leagueGrid}>
        <Card title="LEAGUE BANNER">
          <div className="lottery-banner-grid" style={bannerGrid}>
            <Info label="Ticket Unit" value={fmtMoney(book.ticket_notional)} note="Priced as total-loss risk" />
            <Info label="Daily Budget" value={metric(book.max_daily_tickets, " ticket(s)")} note="Regime adjusted" />
            <Info label="Book Cap" value={fmtMoney(book.cap_dollars)} note="5% of paper equity fence" />
            <Info label="Latest Scan" value={shortTime(board?.scan?.scanned_at)} note={board?.scan?.rubric_version || board?.rubric_version} />
          </div>
          {top && (
            <div style={topTicket}>
              <div>
                <div style={eyebrow}>CURRENT LEADER</div>
                <strong>${top.ticker}</strong>
                <span>{top.company || top.ticker}</span>
              </div>
              <div style={{ textAlign: "right" }}>
                <b style={{ color: TIER[top.tier] || accent }}>{metric(top.score, "/100")}</b>
                <small>{textList(top.triggers).slice(0, 4).join(" / ") || "TRIGGERS UNAVAILABLE"}</small>
              </div>
            </div>
          )}
        </Card>

        <Card title="JACKPOT BOARD">
          <div className="lottery-jackpot-grid" style={jackpotGrid}>
            <Proof label="Closed" value={metric(grades.closed)} />
            <Proof label="Hit +30%" value={fmtRate(grades.hit_rate_30)} />
            <Proof label="Hit +100%" value={fmtRate(grades.hit_rate_100)} />
            <Proof label="Hit +300%" value={fmtRate(grades.hit_rate_300)} />
            <Proof label="Median" value={fmtPct(grades.median_ticket_pct)} />
            <Proof label="Top-1 P/L" value={fmtPct(grades.top1_concentration_pct)} />
          </div>
          <div style={haircutLine}>
            HEADLINE GRADES INCLUDE {metric(grades.haircut?.round_trip_pct, "%")} ROUND-TRIP HAIRCUT
          </div>
        </Card>
      </div>

      <div style={tabs}>
        {[
          ["candidates", "CANDIDATE BOARD"],
          ["tickets", "LIVE TICKETS"],
          ["truth", "TRUTH BOARD"],
          ["learning", "LEARNING ENGINE"],
          ["variants", "VARIANT BOOK"],
          ["method", "METHODOLOGY"],
        ].map(([key, label]) => (
          <button key={key} onClick={() => setTab(key)} style={tabBtn(tab === key)}>{label}</button>
        ))}
      </div>

      {tab === "candidates" && (
        <Card title={`CANDIDATE BOARD - ${candidates.length} SCORED`}>
          <div style={candidateGrid}>
            {candidates.slice(activePage * 30, (activePage + 1) * 30).map(c => (
              <CandidateCard key={c.ticker} candidate={c} onTicket={() => issueTicket(c)} />
            ))}
            {!candidates.length && <Empty text={candidatesAvailable ? "No candidates returned by the current board." : "Candidate board unavailable."} />}
          </div>
          {candidates.length > 30 && <div style={{ display: "flex", flexWrap: "wrap", gap: 12, alignItems: "center", marginTop: 12 }}>
            <button aria-label="Previous Lottery page" disabled={activePage === 0} onClick={() => setPage(activePage - 1)} style={tabBtn(false)}>PREVIOUS</button>
            <span>{activePage * 30 + 1}-{Math.min((activePage + 1) * 30, candidates.length)} OF {candidates.length}</span>
            <button aria-label="Next Lottery page" disabled={activePage >= pageCount - 1} onClick={() => setPage(activePage + 1)} style={tabBtn(false)}>NEXT</button>
          </div>}
        </Card>
      )}

      {tab === "tickets" && (
        <Card title={`LIVE TICKETS - ${tickets.length}`}>
          <div style={ticketGrid}>
            {tickets.map(t => <TicketCard key={t.ticket_id} ticket={t} onSettle={() => settleTicket(t)} />)}
            {!tickets.length && <Empty text={ticketsAvailable ? "No open Lottery League tickets returned." : "Ticket board unavailable."} />}
          </div>
        </Card>
      )}

      {tab === "truth" && (
        <div style={truthStack}>
          <Card title="TRUTH BOARD - CLOSED TICKETS ONLY">
            <div className="lottery-jackpot-grid" style={jackpotGrid}>
              <Proof label="Closed" value={metric(truthCombined.n)} />
              <Proof label="EV Haircut" value={fmtPct(truthCombined.ev_per_ticket_pct_haircut)} />
              <Proof label="Median" value={fmtPct(truthCombined.median_ticket_pct)} />
              <Proof label="Hit +30%" value={fmtRate(truthCombined.hit_rate_30)} />
              <Proof label="Hit +100%" value={fmtRate(truthCombined.hit_rate_100)} />
              <Proof label="MFE Gap" value={fmtPct(truthCombined.mfe_vs_realized_gap_pct)} />
            </div>
            <div style={haircutLine}>
              RAW IS DIAGNOSTIC. HEADLINE TRUTH USES +1.0% ENTRY / -1.5% EXIT HAIRCUT.
            </div>
          </Card>
          <SegmentTable title="VARIANT TRUTH" rows={list(truth?.segments?.variant)} available={Array.isArray(truth?.segments?.variant)} />
          <SegmentTable title="CATALYST / FLOAT / SCORE BUCKETS" rows={[
            ...list(truth?.segments?.catalyst_class),
            ...list(truth?.segments?.float_tier),
            ...list(truth?.segments?.score_bucket),
          ]} available={[truth?.segments?.catalyst_class, truth?.segments?.float_tier, truth?.segments?.score_bucket].every(Array.isArray)} />
          <GradeRows rows={list(truth?.latest_grades)} available={Array.isArray(truth?.latest_grades)} />
        </div>
      )}

      {tab === "learning" && (
        <LearningPanel learning={learning} learnedConfig={learnedConfig} truth={truthCombined} reload={load} showToast={showToast} />
      )}

      {tab === "variants" && (
        <Card title="VARIANT BOOK - PRE-REGISTERED">
          <div style={variantGrid}>
            {VARIANTS.map(([key, label]) => (
              <div key={key} style={variantCard}>
                <div style={eyebrow}>{key}</div>
                <strong>{label}</strong>
                <p>{variantCopy(key)}</p>
                <span>{key.includes("SHADOW") ? "RESEARCH ONLY" : key.includes("OPTIONS") ? "DEFINED RISK ONLY" : "AUTO-ELIGIBLE AFTER GATE"}</span>
              </div>
            ))}
          </div>
        </Card>
      )}

      {tab === "method" && (
        <Card title="HONESTY FOOTER - NON-REMOVABLE">
          <div style={methodGrid}>
            {Object.entries(record(board?.honesty) ? board.honesty : {}).map(([key, value]) => (
              <div key={key} style={methodCard}>
                <div style={eyebrow}>{key.replace(/_/g, " ").toUpperCase()}</div>
                <p>{text(value)}</p>
              </div>
            ))}
            {!record(board?.honesty) && <Empty text="Methodology data unavailable." />}
          </div>
        </Card>
      )}

      <Toast toast={toast} />
    </CrtShell>
  );
}

function CandidateCard({ candidate, onTicket }) {
  const color = TIER[candidate.tier] || muted;
  const scoreItems = Object.entries(record(candidate.components) ? candidate.components : {});
  return (
    <div data-testid={`lottery-candidate-${candidate.ticker}`} style={candidateCard(color)}>
      <div style={candidateHead}>
        <div>
          <div style={eyebrow}>{candidate.tier}</div>
          <strong style={{ color }}>${candidate.ticker}</strong>
          <span>{candidate.company || candidate.sector || "-"}</span>
        </div>
        <b>{metric(candidate.score, "/100")}</b>
      </div>
      <div style={chipRow}>
        {textList(candidate.triggers).slice(0, 5).map(x => <span key={x} style={chip}>{x}</span>)}
        {!Array.isArray(candidate.triggers) && <span style={chip}>TRIGGERS UNAVAILABLE</span>}
      </div>
      <div style={scoreBars}>
        {scoreItems.map(([key, value]) => <ScoreBar key={key} label={key} value={value} max={key === "catalyst" ? 25 : key === "gap_surge" ? 20 : 15} />)}
      </div>
              <div className="lottery-micro-grid" style={microGrid}>
        <Info label="Price" value={fmtMoney(candidate.price)} />
        <Info label="Change" value={fmtPct(candidate.change_pct)} />
        <Info label="RVOL" value={metric(candidate.relative_volume)} />
        <Info label="Rotation" value={metric(candidate.rotation)} />
        <Info label="Float" value={candidate.float_confidence || "UNKNOWN"} />
        <Info label="Dilution" value={candidate.dilution?.label || "UNKNOWN"} color={candidate.dilution?.active === true ? "#f87171" : candidate.dilution?.active === false ? "#4ade80" : muted} />
      </div>
      {candidate.penalties?.length ? (
        <div style={penaltyLine}>{list(candidate.penalties).map(p => `${text(p.label)} -${metric(p.points)}`).join(" / ")}</div>
      ) : (
        <div style={{ ...penaltyLine, color: muted }}>{Array.isArray(candidate.penalties) && candidate.penalties.length === 0 ? "NO ACTIVE PENALTY" : "PENALTY DATA UNAVAILABLE"}</div>
      )}
      <button onClick={onTicket} disabled={!candidate.eligible} style={ticketBtn(candidate.eligible)}>
        {candidate.eligible ? "OPEN PAPER TICKET" : "NOT ELIGIBLE"}
      </button>
    </div>
  );
}

function TicketCard({ ticket, onSettle }) {
  const entry = num(ticket.entry_price);
  const current = num(ticket.current_price);
  const move = entry != null && entry > 0 && current != null ? ((current - entry) / entry) * 100 : null;
  return (
    <div data-testid={`lottery-ticket-${ticket.ticker}`} style={ticketCard}>
      <div style={candidateHead}>
        <div>
          <div style={eyebrow}>{ticket.variant || "V1"}</div>
          <strong style={{ color: accent }}>${ticket.ticker}</strong>
          <span>{text(ticket.status || "UNKNOWN")} / {text(ticket.ticket_id)}</span>
        </div>
        <b style={{ color: returnColor(move) }}>{fmtPct(move)}</b>
      </div>
      <div style={ladder}>
        {(ticket.ladder || []).map((leg, idx) => (
          <div key={idx} style={ladderLeg(typeof leg.status === "string" && leg.status !== "WAITING")}>
            <span>{typeof leg.level === "number" ? `+${Math.round(leg.level * 100)}%` : "TRAIL"}</span>
            <small>{text(leg.status || "UNKNOWN")}</small>
          </div>
        ))}
      </div>
        <div className="lottery-micro-grid" style={microGrid}>
        <Info label="Entry" value={fmtMoney(ticket.entry_price)} />
        <Info label="Current" value={fmtMoney(ticket.current_price)} />
        <Info label="Stop" value={fmtMoney(ticket.stop_price)} />
        <Info label="Time Stop" value={ticket.time_stop_date || "-"} />
      </div>
      <button onClick={onSettle} style={ticketBtn(true, "#f87171")}>SETTLE TICKET</button>
    </div>
  );
}

function ScoreBar({ label, value, max }) {
  const pct = num(value) == null ? null : Math.max(0, Math.min(100, (num(value) / max) * 100));
  return (
    <div style={scoreRow}>
      <span>{label.replace(/_/g, " ")}</span>
      <div style={barTrack}>{pct != null && <i style={{ ...barFill, width: `${pct}%` }} />}</div>
      <b>{metric(value)}</b>
    </div>
  );
}

function Info({ label, value, note, color = labelLight }) {
  return (
    <div style={infoBox}>
      <span>{label}</span>
      <strong style={{ color }}>{text(value)}</strong>
      {note && <small>{text(note)}</small>}
    </div>
  );
}

function Proof({ label, value }) {
  return (
    <div style={proofBox}>
      <span>{label}</span>
      <strong>{value ?? "-"}</strong>
    </div>
  );
}

function SegmentTable({ title, rows, available }) {
  return (
    <Card title={title}>
      <div style={tableWrap}>
        <table className="lottery-truth-table" style={truthTable}>
          <thead>
            <tr>
              <th>SEGMENT</th>
              <th>DIM</th>
              <th>N</th>
              <th>EV</th>
              <th>MEDIAN</th>
              <th>+30</th>
              <th>+100</th>
              <th>MFE GAP</th>
              <th>STATUS</th>
            </tr>
          </thead>
          <tbody>
            {(rows || []).slice(0, 36).map((r, idx) => (
              <tr key={`${r.dimension}-${r.segment}-${idx}`}>
                <td>{r.segment}</td>
                <td>{r.dimension}</td>
                <td>{r.n}</td>
                <td style={{ color: returnColor(r.ev_per_ticket_pct_haircut) }}>{fmtPct(r.ev_per_ticket_pct_haircut)}</td>
                <td>{fmtPct(r.median_ticket_pct)}</td>
                <td>{fmtRate(r.hit_rate_30)}</td>
                <td>{fmtRate(r.hit_rate_100)}</td>
                <td>{fmtPct(r.mfe_vs_realized_gap_pct)}</td>
                <td style={{ color: r.decision_status === "RETIRE" ? "#f87171" : accent2 }}>{text(r.decision_status || "UNKNOWN")}</td>
              </tr>
            ))}
            {!rows?.length && <tr><td colSpan="9">{available ? "NO SEGMENTS RETURNED" : "SEGMENT DATA UNAVAILABLE"}</td></tr>}
          </tbody>
        </table>
      </div>
    </Card>
  );
}

function GradeRows({ rows, available }) {
  return (
    <Card title="LATEST COURT-ADMISSIBLE GRADES">
      <div style={tableWrap}>
        <table className="lottery-truth-table" style={truthTable}>
          <thead>
            <tr>
              <th>TICKER</th>
              <th>VARIANT</th>
              <th>EXIT</th>
              <th>REALIZED</th>
              <th>PEAK</th>
              <th>MAE</th>
              <th>RAW</th>
              <th>HAIRCUT</th>
            </tr>
          </thead>
          <tbody>
            {(rows || []).slice(0, 24).map(g => {
              const truth = g.truth || {};
              const exit = g.exit || {};
              return (
                <tr key={g.ticket_id}>
                  <td>${g.ticker}</td>
                  <td>{g.variant}</td>
                  <td>{exit.exit_reason}</td>
                  <td>{metric(truth.realized_multiple, "x")}</td>
                  <td>{metric(truth.peak_multiple, "x")}</td>
                  <td>{fmtPct(truth.mae_pct)}</td>
                  <td>{fmtPct(truth.raw_return_pct)}</td>
                  <td style={{ color: returnColor(truth.haircut_return_pct) }}>{fmtPct(truth.haircut_return_pct)}</td>
                </tr>
              );
            })}
            {!rows?.length && <tr><td colSpan="8">{available ? "NO CLOSED GRADES RETURNED. OPEN TICKETS DO NOT COUNT." : "CLOSED GRADES UNAVAILABLE"}</td></tr>}
          </tbody>
        </table>
      </div>
    </Card>
  );
}

function LearningPanel({ learning, learnedConfig, truth, reload, showToast }) {
  const [running, setRunning] = useState(false);
  const run = async () => {
    setRunning(true);
    try {
      const { data } = await axios.post(`${API}/lottery/learning/run`);
      showToast(data.ok ? "ok" : "err", data.ok ? `Lottery learning complete - ${data.changes?.length || 0} changes` : "Lottery learning failed");
      await reload();
    } catch (e) {
      showToast("err", `Learning failed: ${e?.response?.data?.detail || e?.message || "unknown"}`);
    } finally {
      setRunning(false);
    }
  };
  return (
    <div style={truthStack}>
      <Card title="LOTTERY LEARNING ENGINE">
        <div style={learningGrid}>
          <Info label="Status" value={learnedConfig?.status || "UNKNOWN"} color={learnedConfig?.status === "RETIRE_LEAGUE" ? "#f87171" : accent2} />
          <Info label="Sample" value={metric(learnedConfig?.sample_count ?? truth?.n, "/150")} note="closed graded tickets" />
          <Info label="Min Score" value={metric(learnedConfig?.min_ticket_score)} note="learned gate recommendation" />
          <Info label="Ladder Bias" value={learnedConfig?.ladder_bias || "UNKNOWN"} />
        </div>
        <p style={learningNote}>{learnedConfig?.reason || "Learning waits for closed, graded Lottery tickets before changing thresholds."}</p>
        <button onClick={run} disabled={running} style={primaryBtn(running)}>{running ? "RUNNING..." : "RUN LOTTERY LEARNING"}</button>
      </Card>
      <Card title="LATEST LEARNING NOTES">
        <div style={methodGrid}>
          {textList(learning?.notes).map((note, idx) => (
            <div key={idx} style={methodCard}><p>{note}</p></div>
          ))}
          {!textList(learning?.notes).length && <Empty text="No learning notes available." />}
        </div>
      </Card>
      <Card title="PREFERRED / PENALIZED SEGMENTS">
        <div style={variantGrid}>
          <LearnList title="PREFERRED" rows={list(learnedConfig?.preferred_segments)} color="#4ade80" />
          <LearnList title="PENALIZED" rows={list(learnedConfig?.penalized_segments)} color="#f87171" />
          <LearnList title="RETIRED VARIANTS" rows={textList(learnedConfig?.retired_variants).map(x => ({ segment: x, dimension: "variant" }))} color="#f87171" />
        </div>
      </Card>
    </div>
  );
}

function LearnList({ title, rows, color }) {
  return (
    <div style={variantCard}>
      <div style={{ ...eyebrow, color }}>{title}</div>
      {(rows || []).length ? rows.slice(0, 10).map((r, idx) => (
        <p key={idx} style={learnRow}>{r.dimension || "-"} / {r.segment} {r.ev != null ? `EV ${fmtPct(r.ev)}` : ""} {r.n != null ? `n=${r.n}` : ""}</p>
      )) : <p style={learnRow}>No sample-size qualified segments yet.</p>}
    </div>
  );
}

function Empty({ text }) {
  return <div style={{ color: muted, padding: 18, fontSize: 12 }}>{text}</div>;
}

function Toast({ toast }) {
  if (!toast) return null;
  const color = toast.kind === "ok" ? "#4ade80" : "#f87171";
  return <div data-testid="lottery-toast" style={{ ...toastBox, color, borderColor: color }}>{toast.msg}</div>;
}

function fmtMoney(value) {
  if (num(value) == null) return "-";
  const n = Number(value);
  if (!Number.isFinite(n)) return "-";
  return `$${n.toLocaleString(undefined, { maximumFractionDigits: 2, minimumFractionDigits: n < 100 ? 2 : 0 })}`;
}

function fmtPct(value) {
  if (num(value) == null) return "-";
  const n = Number(value);
  if (!Number.isFinite(n)) return "-";
  return `${n >= 0 ? "+" : ""}${n.toFixed(1)}%`;
}

function fmtRate(value) {
  if (num(value) == null) return "-";
  const n = Number(value);
  if (!Number.isFinite(n)) return "-";
  return `${(n * 100).toFixed(0)}%`;
}

function shortTime(value) {
  if (!value) return "-";
  if (!Number.isFinite(new Date(value).getTime())) return "-";
  try { return new Date(value).toLocaleString(); } catch { return String(value).slice(0, 16); }
}

function variantCopy(key) {
  if (key.includes("OPENING")) return "Logs executable-gap evidence first. It does not graduate until enough slippage/divergence data exists.";
  if (key.includes("RED")) return "Day-2 runner opens red, reclaims prior close, and confirms volume before becoming ticket eligible.";
  if (key.includes("OPTIONS")) return "Defined-risk premium tickets only when the option chain is liquid enough to survive spread and OI checks.";
  return "Default League variant: high-rotation catalyst runner into the close, held for a 1-5 session continuation attempt.";
}

const hero = {
  display: "grid",
  gridTemplateColumns: "repeat(auto-fit, minmax(min(100%, 300px), 1fr))",
  gap: 14,
  border: "1px solid rgba(215,189,104,0.24)",
  background: "linear-gradient(135deg, rgba(8,16,20,0.96), rgba(3,4,8,0.98))",
  padding: 18,
  marginBottom: 14,
};
const eyebrow = { color: accent2, fontSize: 9, letterSpacing: "0.18em", fontWeight: 900 };
const h1 = { color: accent, fontSize: 28, lineHeight: 1.12, margin: "8px 0", letterSpacing: "0.06em", fontWeight: 900 };
const lede = { color: muted, fontSize: 13, lineHeight: 1.55, maxWidth: 860, margin: 0 };
const gateBox = { border: hairline, background: pageBg, padding: 14, display: "flex", flexDirection: "column", justifyContent: "center", gap: 8 };
const statStrip = { display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(min(100%, 140px), 1fr))", background: cardBg, border: hairline, marginBottom: 14 };
const leagueGrid = { display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(min(100%, 300px), 1fr))", gap: 14, alignItems: "start" };
const bannerGrid = { display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(min(100%, 140px), 1fr))", gap: 10 };
const topTicket = { display: "flex", justifyContent: "space-between", gap: 14, marginTop: 14, paddingTop: 14, borderTop: hairline };
const tabs = { display: "flex", flexWrap: "wrap", gap: 6, margin: "14px 0" };
const candidateGrid = { display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(min(100%, 310px), 1fr))", gap: 12 };
const ticketGrid = { display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(min(100%, 330px), 1fr))", gap: 12 };
const variantGrid = { display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(min(100%, 260px), 1fr))", gap: 12 };
const methodGrid = { display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(min(100%, 260px), 1fr))", gap: 12 };
const truthStack = { display: "grid", gap: 14 };
const jackpotGrid = { display: "grid", gridTemplateColumns: "repeat(3, minmax(0, 1fr))", gap: 8 };
const learningGrid = { display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(160px, 1fr))", gap: 10 };
const chipRow = { display: "flex", flexWrap: "wrap", gap: 5, margin: "10px 0" };
const chip = { color: accent2, border: "1px solid rgba(94,234,212,0.22)", background: "rgba(94,234,212,0.05)", padding: "4px 7px", fontSize: 9, letterSpacing: "0.1em" };
const scoreBars = { display: "grid", gap: 6, margin: "10px 0" };
const scoreRow = { display: "grid", gridTemplateColumns: "82px 1fr 34px", gap: 8, alignItems: "center", color: muted, fontSize: 9, textTransform: "uppercase" };
const barTrack = { height: 6, background: "rgba(255,255,255,0.07)", overflow: "hidden" };
const barFill = { display: "block", height: "100%", background: "linear-gradient(90deg, #5eead4, #d7bd68)" };
const microGrid = { display: "grid", gridTemplateColumns: "repeat(3, minmax(0, 1fr))", gap: 7, marginTop: 10 };
const infoBox = { border: hairline, background: "rgba(255,255,255,0.015)", padding: "8px 9px", minWidth: 0 };
const proofBox = { border: hairline, background: "rgba(255,255,255,0.015)", padding: 10 };
const penaltyLine = { color: "#f87171", borderTop: hairline, marginTop: 10, paddingTop: 9, fontSize: 10, lineHeight: 1.35 };
const ticketCard = { border: "1px solid rgba(94,234,212,0.22)", background: "linear-gradient(180deg, rgba(8,16,20,0.94), rgba(5,7,11,0.98))", padding: 14 };
const candidateHead = { display: "flex", justifyContent: "space-between", gap: 10, minWidth: 0 };
const ladder = { display: "grid", gridTemplateColumns: "repeat(3, 1fr)", gap: 7, margin: "12px 0" };
const variantCard = { border: hairline, background: pageBg, padding: 14, minHeight: 170 };
const methodCard = { border: hairline, background: pageBg, padding: 14, minHeight: 140 };
const haircutLine = { marginTop: 10, paddingTop: 10, borderTop: hairline, color: accent, fontSize: 10, letterSpacing: "0.12em", fontWeight: 900 };
const tableWrap = { overflowX: "auto", border: hairline, background: pageBg };
const truthTable = { width: "100%", borderCollapse: "collapse", minWidth: 820, color: labelLight, fontSize: 11, letterSpacing: "0.06em" };
const learningNote = { color: muted, fontSize: 12, lineHeight: 1.55, margin: "12px 0" };
const learnRow = { color: muted, fontSize: 11, lineHeight: 1.45, margin: "10px 0 0" };
const toastBox = { position: "fixed", right: 22, bottom: 22, zIndex: 1000, border: "1px solid", background: "#030306e6", padding: "11px 16px", fontSize: 11, letterSpacing: "0.10em", fontFamily: "JetBrains Mono", fontWeight: 900, maxWidth: 440 };

function candidateCard(color) {
  return { border: `1px solid ${color}44`, background: "linear-gradient(180deg, rgba(8,16,20,0.95), rgba(4,5,9,0.99))", padding: 14, boxShadow: `inset 0 1px rgba(255,255,255,0.035), 0 0 16px ${color}14` };
}

function tabBtn(active) {
  return { background: active ? "rgba(215,189,104,0.12)" : "transparent", border: `1px solid ${active ? accent : "rgba(255,255,255,0.12)"}`, color: active ? accent : muted, padding: "9px 13px", fontSize: 10, letterSpacing: "0.12em", fontFamily: "JetBrains Mono", fontWeight: 900, cursor: "pointer" };
}

function primaryBtn(disabled) {
  return { background: disabled ? "rgba(215,189,104,0.10)" : "linear-gradient(180deg, rgba(215,189,104,0.28), rgba(78,62,18,0.52))", border: `1px solid ${accent}`, color: accent, padding: "10px 16px", fontSize: 11, letterSpacing: "0.13em", fontFamily: "JetBrains Mono", fontWeight: 900, cursor: disabled ? "wait" : "pointer" };
}

function ticketBtn(enabled, color = accent) {
  return { width: "100%", marginTop: 12, background: enabled ? `${color}18` : "rgba(255,255,255,0.03)", border: `1px solid ${enabled ? color : "rgba(255,255,255,0.10)"}`, color: enabled ? color : muted, padding: "9px 10px", fontSize: 10, letterSpacing: "0.12em", fontFamily: "JetBrains Mono", fontWeight: 900, cursor: enabled ? "pointer" : "not-allowed" };
}

function ladderLeg(done) {
  return { border: `1px solid ${done ? "#4ade80" : "rgba(255,255,255,0.12)"}`, background: done ? "rgba(74,222,128,0.08)" : "rgba(255,255,255,0.02)", color: done ? "#4ade80" : muted, padding: 9, display: "flex", flexDirection: "column", gap: 4, alignItems: "center", fontSize: 10, fontWeight: 900 };
}
