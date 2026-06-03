import { useEffect, useState } from "react";
import axios from "axios";
import { toast } from "sonner";
import { CrtShell, Card, tokens } from "./CrtShell";

const API = `${process.env.REACT_APP_BACKEND_URL}/api`;
const { accent, accent2, dim, muted, labelLight, hairline, cardBg } = tokens;

export default function SettingsPage() {
  const [status, setStatus] = useState(null);
  const [criteria, setCriteria] = useState(null);

  useEffect(() => {
    axios.get(`${API}/status`).then(r => setStatus(r.data)).catch(() => setStatus({}));
    axios.get(`${API}/admin/pipeline_criteria`).then(r => setCriteria(r.data)).catch(() => {});
  }, []);

  const runLearning = async () => {
    toast("LEARNING CYCLE INITIATED");
    try {
      const { data } = await axios.post(`${API}/learning/run`);
      toast(data.skipped ? `SKIPPED — ${data.reason}` : `COMPLETE — ${data.trades} TRADES`);
    } catch { toast("LEARNING FAILED"); }
  };
  const resetWeights = async () => {
    if (!window.confirm("Reset all weights to defaults?")) return;
    await axios.post(`${API}/learning/reset`);
    toast("WEIGHTS RESET");
  };
  const triggerPnl = async () => {
    toast("REFRESHING P&L RETURNS...");
    try {
      const { data } = await axios.post(`${API}/pnl/refresh`);
      toast(`P&L: ${data.signals_refreshed || 0} SIGNALS · ${data.options_rows_refreshed || 0} OPTIONS`);
    } catch { toast("P&L REFRESH FAILED"); }
  };
  const seedBacktest = async () => {
    toast("SEEDING BACKTEST...");
    try {
      const { data } = await axios.post(`${API}/backtest/seed`);
      toast(`SEEDED ${data.written || 0} ROWS`);
    } catch { toast("BACKTEST SEED FAILED"); }
  };

  return (
    <CrtShell title="SETTINGS & SYSTEM">
      {/* ── Pipeline Criteria ── */}
      <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 20, marginBottom: 4 }}>
        <Card title="PIPELINE CRITERIA · PRE-FILTER SCREENER" accentColor={accent2}>
          <div style={{ color: muted, fontSize: 11, marginBottom: 12, letterSpacing: "0.04em", lineHeight: 1.6 }}>
            What has to be true about a ticker for it to get flagged and passed to the scoring engine.
          </div>
          {!criteria ? (
            <div style={{ color: muted, padding: 10 }}>Loading...</div>
          ) : (
            criteria.pre_filter.map((r, i) => (
              <div key={i} style={{
                display: "grid", gridTemplateColumns: "180px 1fr",
                padding: "8px 0", borderBottom: hairline, fontSize: 12, gap: 12,
              }}>
                <span style={{ color: accent2, letterSpacing: "0.08em", fontWeight: 700 }}>
                  {r.rule}
                </span>
                <span style={{ color: labelLight, fontSize: 11, lineHeight: 1.5 }}>{r.detail}</span>
              </div>
            ))
          )}
        </Card>

        <Card title="PIPELINE CRITERIA · FINAL SCREENER · LIVE WEIGHTS" accentColor={accent}>
          <div style={{ color: muted, fontSize: 11, marginBottom: 12, letterSpacing: "0.04em", lineHeight: 1.6 }}>
            AXIOM score formula components — weights live from the learning engine.
          </div>
          {!criteria ? (
            <div style={{ color: muted, padding: 10 }}>Loading...</div>
          ) : (
            <>
              {criteria.final_screener.map((w, i) => (
                <div key={i} style={{
                  display: "grid", gridTemplateColumns: "1.6fr 60px 1fr",
                  padding: "8px 0", borderBottom: hairline, fontSize: 12, gap: 12, alignItems: "center",
                }}>
                  <span style={{ color: accent, letterSpacing: "0.06em", fontWeight: 700, fontSize: 11 }}>
                    {w.key}
                  </span>
                  <span className="num" style={{
                    color: accent, fontWeight: 700, fontSize: 14, textAlign: "right",
                    fontFamily: "JetBrains Mono",
                  }}>{w.weight?.toFixed(2)}</span>
                  <span style={{ color: muted, fontSize: 10, lineHeight: 1.5 }}>{w.description}</span>
                </div>
              ))}
              <div style={{ marginTop: 12, padding: "10px 0", borderTop: `0.5px solid ${accent}33`,
                              color: labelLight, fontSize: 10, letterSpacing: "0.08em", lineHeight: 1.6 }}>
                <span style={{ color: dim }}>FORMULA:</span> {criteria.axiom_score_formula}
              </div>
            </>
          )}
        </Card>
      </div>

      <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 20 }}>
        <div>
          <Card title="INTEGRATIONS STATUS">
            <Row k="MONGODB" v="[CONNECTED]" c="#4ade80" />
            <Row k="TELEGRAM BOT" v="[ACTIVE — @QuantNinjabot]" c="#4ade80" />
            <Row k="CLAUDE LLM" v="[EMERGENT KEY — 24H CACHE]" c="#4ade80" />
            <Row k="USASPENDING API" v="[PUBLIC ENDPOINT]" c="#4ade80" />
            <Row k="OPENINSIDER" v="[HTML SCRAPE]" c="#4ade80" />
            <Row k="FINVIZ" v="[HTML SCRAPE]" c="#4ade80" />
            <Row k="YAHOO FINANCE" v="[YFINANCE — DEFAULT PRICE SRC]" c="#4ade80" />
          </Card>

          <Card title="SCHEDULER JOBS">
            <Row k="08:00 ET MON-FRI" v="DAILY SCAN" />
            <Row k="12:01 ET MON-FRI" v="MID-DAY SCAN" />
            <Row k="15:30 ET MON-FRI" v="PRE-CLOSE SCAN" />
            <Row k="EVERY 15 MIN" v="UNUSUAL FLOW REFRESH" />
            <Row k="EVERY 5 MIN" v="PRICE ALERT CHECKS" />
            <Row k="23:00 ET DAILY" v="P&L REFRESH" />
            <Row k="02:00 ET DAILY" v="P&L SECOND PASS" />
            <Row k="02:00 ET SUNDAY" v="LEARNING CYCLE" c={accent} />
          </Card>

          <Card title="LEARNING ENGINE CONFIG">
            <Row k="MIN SAMPLES BEFORE ADJUST" v="10" />
            <Row k="MAX WEIGHT CHANGE PER CYCLE" v="±15%" />
            <Row k="BASELINE WIN RATE" v="50%" />
            <Row k="RETURN BASIS" v="30-DAY" />
            <Row k="HARD FLOOR/CEILING" v="ENFORCED" c={accent} />
            <Row k="FEATURE VERSION" v="3.0" />
          </Card>
        </div>

        <div>
          <Card title="SYSTEM STATUS">
            <Row k="BACKEND PORT" v="8001" />
            <Row k="BOT BACKEND VERSION" v="3.0.0" c={accent} />
            <Row k="LAST SCAN" v={status?.last_scan_at || "UNKNOWN"} />
            <Row k="NEXT SCHEDULED SCAN" v="08:00 ET" />
            <Row k="SCAN IN PROGRESS" v={status?.scan_in_progress ? "YES" : "NO"}
                 c={status?.scan_in_progress ? "#fb923c" : "#4ade80"} />
            <Row k="UNIVERSE SIZE" v="~25 PRE-FILTERED CANDIDATES" />
            <Row k="CACHE STRATEGY" v="24H CLAUDE · 6H NLQ" />
          </Card>

          <Card title="MANUAL TRIGGERS">
            <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 10 }}>
              <button data-testid="trigger-learning-btn" onClick={runLearning} style={btnGold}>
                [ RUN LEARNING CYCLE ]
              </button>
              <button data-testid="reset-weights-btn" onClick={resetWeights} style={btnDim}>
                [ RESET WEIGHTS ]
              </button>
              <button data-testid="trigger-pnl-btn" onClick={triggerPnl} style={btnGold}>
                [ REFRESH P&L ]
              </button>
              <button data-testid="seed-backtest-btn-settings" onClick={seedBacktest} style={btnGold}>
                [ SEED BACKTEST ]
              </button>
            </div>
            <div style={{ marginTop: 14, fontSize: 11, color: muted, lineHeight: 1.6 }}>
              All triggers also accessible from Telegram: /scan, /performance, /backtest_seed
            </div>
          </Card>

          <Card title="TELEGRAM COMMANDS">
            <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", fontSize: 12,
                            color: labelLight, gap: 6, letterSpacing: "0.04em" }}>
              <span style={{ color: accent }}>/scan</span><span>Daily scan</span>
              <span style={{ color: accent }}>/scan_gov</span><span>Gov-only scan</span>
              <span style={{ color: accent }}>/analyze TICKER</span><span>Deep dive</span>
              <span style={{ color: accent }}>/options TICKER</span><span>Options play</span>
              <span style={{ color: accent }}>/flow TICKER</span><span>Unusual flow</span>
              <span style={{ color: accent }}>/iv TICKER</span><span>IV rank</span>
              <span style={{ color: accent }}>/spread TICKER</span><span>Spread analysis</span>
              <span style={{ color: accent }}>/calls /puts</span><span>Filter today's scan</span>
              <span style={{ color: accent }}>/noiv</span><span>Low IV picks</span>
              <span style={{ color: accent }}>/performance</span><span>P&L summary</span>
              <span style={{ color: accent }}>/backtest</span><span>Backtest stats</span>
              <span style={{ color: accent }}>/backtest_seed</span><span>Seed congress data</span>
            </div>
          </Card>
        </div>
      </div>
    </CrtShell>
  );
}

function Row({ k, v, c = "#e5e7eb" }) {
  return (
    <div style={{
      display: "flex", justifyContent: "space-between", padding: "7px 0",
      fontSize: 13, letterSpacing: "0.04em",
    }}>
      <span style={{ color: dim, fontSize: 11, letterSpacing: "0.12em" }}>{k}</span>
      <span style={{ color: c, fontWeight: c === "#e5e7eb" ? 400 : 700 }}>{v}</span>
    </div>
  );
}

const btnGold = {
  background: "transparent", border: `0.5px solid ${accent}`,
  color: accent, fontSize: 12, padding: "10px 14px", cursor: "pointer",
  letterSpacing: "0.1em", fontFamily: "Courier New", fontWeight: 700,
};
const btnDim = {
  background: "transparent", border: `0.5px solid ${dim}`,
  color: muted, fontSize: 12, padding: "10px 14px", cursor: "pointer",
  letterSpacing: "0.1em", fontFamily: "Courier New",
};
