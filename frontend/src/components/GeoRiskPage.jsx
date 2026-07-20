import { useEffect, useMemo, useRef, useState } from "react";
import axios from "axios";
import { CrtShell, Stat, tokens } from "./CrtShell";

const API = `${(process.env.REACT_APP_BACKEND_URL || "").replace(/\/$/, "")}/api`;
const { accent, accent2, dim, muted, labelLight, hairline, cardBg, pageBg } = tokens;

const MAP_MODES = [
  { key: "live", label: "Live Intel" },
  { key: "impact", label: "Market Impact" },
  { key: "portfolio", label: "Portfolio Risk" },
  { key: "chokepoints", label: "Trade Routes" },
  { key: "battle", label: "Battle Cards" },
];

const WATCHLISTS = [
  { key: "all", label: "All Baskets", terms: [] },
  { key: "war", label: "War / Conflict", terms: ["conflict"] },
  { key: "stateside", label: "Stateside", terms: ["us_policy", "us_macro", "us_infrastructure", "us_weather"] },
  { key: "energy", label: "Energy Shock", terms: ["energy", "shipping"] },
  { key: "cyber", label: "Cyber Attack", terms: ["cyber"] },
  { key: "shipping", label: "Shipping Stress", terms: ["shipping"] },
  { key: "china", label: "China / Taiwan", text: ["china", "taiwan", "south china sea", "taiwan strait"] },
  { key: "food", label: "Food Inflation", terms: ["food"] },
];

const SOURCE_FILTERS = [
  { key: "all", label: "All Sources" },
  { key: "stateside", label: "Stateside" },
  { key: "gdelt", label: "GDELT" },
  { key: "fox", label: "Fox" },
  { key: "wsj", label: "WSJ" },
  { key: "bloomberg", label: "Bloomberg Free" },
];

const severityColor = (s) =>
  s === "CRITICAL" ? "#ef4444" : s === "HIGH" ? "#fb923c" : s === "WATCH" ? "#facc15" : "#5eead4";

const severityRank = (s) => ({ CRITICAL: 4, HIGH: 3, WATCH: 2, LOW: 1 }[s] || 0);

export default function GeoRiskPage() {
  const [data, setData] = useState(null);
  const [selected, setSelected] = useState(null);
  const [loading, setLoading] = useState(true);
  const [mode, setMode] = useState("live");
  const [source, setSource] = useState("all");
  const [watchlist, setWatchlist] = useState("all");
  const [showChokepoints, setShowChokepoints] = useState(true);
  const [showHoldings, setShowHoldings] = useState(true);
  const [tradeFloor, setTradeFloor] = useState(null);
  const [lseMacro, setLseMacro] = useState(null);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    axios.get(`${API}/georisk/live`).then(r => {
      if (cancelled) return;
      const payload = r.data || {};
      setData(payload);
      setSelected(null);
      setLoading(false);
    }).catch(() => {
      if (!cancelled) setLoading(false);
    });
    axios.get(`${API}/trade_floor/positions`).then(r => {
      if (!cancelled) setTradeFloor(r.data || null);
    }).catch(() => {});
    axios.get(`${API}/data/lse/macro?limit=150`).then(r => {
      if (!cancelled) setLseMacro(r.data || null);
    }).catch(() => {});
    return () => { cancelled = true; };
  }, []);

  const events = useMemo(() => data?.events || [], [data]);
  const holdings = useMemo(() => normalizeHoldings(tradeFloor), [tradeFloor]);
  const heldTickers = useMemo(() => new Set(holdings.map(h => h.ticker)), [holdings]);

  const filteredEvents = useMemo(() => {
    const activeWatchlist = WATCHLISTS.find(w => w.key === watchlist) || WATCHLISTS[0];
    return events.filter(event => {
      const eventSource = event.source_name || event.source || "Unknown";
      if (source === "stateside" && !String(event.data_lane || "").includes("stateside")) return false;
      if (source === "gdelt" && !String(event.data_lane || "").includes("gdelt")) return false;
      if (source === "fox" && !eventSource.startsWith("Fox News")) return false;
      if (source === "wsj" && !eventSource.startsWith("WSJ")) return false;
      if (source === "bloomberg" && !eventSource.startsWith("Bloomberg Free")) return false;
      if (activeWatchlist.terms?.length && !activeWatchlist.terms.includes(event.theme)) return false;
      if (activeWatchlist.text?.length) {
        const haystack = `${event.title || ""} ${event.location || ""}`.toLowerCase();
        if (!activeWatchlist.text.some(term => haystack.includes(term))) return false;
      }
      return true;
    });
  }, [events, source, watchlist]);
  const filterEmpty = !loading && events.length > 0 && filteredEvents.length === 0;
  const visibleEvents = filterEmpty ? events : filteredEvents;

  const severityCounts = useMemo(() => {
    const counts = { CRITICAL: 0, HIGH: 0, WATCH: 0, LOW: 0 };
    visibleEvents.forEach(event => { counts[event.severity] = (counts[event.severity] || 0) + 1; });
    return counts;
  }, [visibleEvents]);

  const tickerStack = useMemo(() => {
    const counts = {};
    visibleEvents.forEach(e => (e.tickers || []).forEach(t => { counts[t] = (counts[t] || 0) + 1; }));
    return Object.entries(counts).sort((a, b) => b[1] - a[1]).slice(0, 14);
  }, [visibleEvents]);

  const timeline = useMemo(() => {
    return [...visibleEvents]
      .sort((a, b) => String(b.seen_at || "").localeCompare(String(a.seen_at || "")))
      .slice(0, 10);
  }, [visibleEvents]);

  const impactStack = useMemo(() => {
    const counts = {};
    visibleEvents.forEach(event => {
      (event.sectors || []).forEach(sector => {
        counts[sector] = (counts[sector] || 0) + severityRank(event.severity);
      });
    });
    return Object.entries(counts).sort((a, b) => b[1] - a[1]).slice(0, 8);
  }, [visibleEvents]);

  const hotChokepoint = (data?.chokepoints || []).find(point => point.active_events) || (data?.chokepoints || [])[0];
  const battle = selected?.battle_card || {};

  return (
    <CrtShell title="GEORISK COMMAND">
      <div style={heroHeader}>
        <div>
          <div style={eyebrow}>GLOBAL CATALYST MAP</div>
          <div style={heroTitle}>Pinpointed News Action</div>
        </div>
        <div style={heroControls}>
          <SelectBox label="MODE" value={mode} onChange={setMode} options={MAP_MODES} />
          <SelectBox label="BASKET" value={watchlist} onChange={setWatchlist} options={WATCHLISTS} />
          <SelectBox
            label="SOURCE"
            value={source}
            onChange={setSource}
            options={SOURCE_FILTERS}
          />
          <button onClick={() => setShowChokepoints(v => !v)} style={showChokepoints ? activeToggle : toggleButton}>
            ROUTES {showChokepoints ? "ON" : "OFF"}
          </button>
          <button onClick={() => setShowHoldings(v => !v)} style={showHoldings ? activeToggle : toggleButton}>
            HOLDINGS {showHoldings ? "ON" : "OFF"}
          </button>
        </div>
      </div>

      <InteractiveMap
        events={visibleEvents}
        selected={selected}
        onSelect={setSelected}
        chokepoints={showChokepoints ? data?.chokepoints || [] : []}
        holdings={showHoldings ? holdings : []}
        heldTickers={showHoldings ? heldTickers : new Set()}
        mode={mode}
        loading={loading}
      />

      <div style={statsBar}>
        <Stat label="PINNED EVENTS" value={loading ? "..." : visibleEvents.length} color={accent} accentBar />
        <Stat label="CRITICAL" value={severityCounts.CRITICAL} color="#ef4444" />
        <Stat label="HIGH" value={severityCounts.HIGH} color="#fb923c" />
        <Stat label="WATCH" value={severityCounts.WATCH} color="#facc15" />
        <Stat label="LSE MACRO" value={(lseMacro?.economic_calendar || []).length} sub={(lseMacro?.bond_yields || []).length ? `${lseMacro.bond_yields.length} YIELD ROWS` : "CROSS-ASSET"} color={lseMacro?.provider ? "#38bdf8" : muted} />
        <Stat label="HOT ROUTE" value={hotChokepoint?.active_events ? hotChokepoint.name : "CLEAR"} sub={hotChokepoint?.active_events ? `${hotChokepoint.active_events} EVENTS` : "NO ACTIVE MATCH"} color={accent2} />
        <Stat label="CACHE" value={data?.cache_status || "-"} sub={data?.cache_age_minutes != null ? `${data.cache_age_minutes}M OLD` : ""} color={accent2} />
      </div>
      {filterEmpty && (
        <div style={filterNotice}>
          CURRENT FILTERS RETURNED 0 EVENTS. SHOWING ALL LIVE GEORISK NEWS.
          <button
            type="button"
            onClick={() => { setSource("all"); setWatchlist("all"); }}
            style={filterResetButton}
          >
            RESET FILTERS
          </button>
        </div>
      )}

      <div style={missionGrid}>
        <Panel title="BATTLE CARD">
          {selected ? (
            <>
              <div style={cardHeadline}>{battle.next_scan || "Cross-asset risk scan"}</div>
              <div style={headlineBlock}>{selected.title}</div>
              <ScoreBar score={selected.score} severity={selected.severity} />
              <div style={subTitle}>WHY MARKETS CARE</div>
              <div style={bodyCopy}>{battle.why_it_matters || selected.market_bias}</div>
              <div style={subTitle}>IMPACT NOTES</div>
              {(battle.impact_notes || []).slice(0, 4).map(note => <div key={note} style={noteRow}>{note}</div>)}
            </>
          ) : <div style={emptyText}>Select a map pin to load the battle card.</div>}
        </Panel>

        <Panel title="LIVE TIMELINE">
          {timeline.map(event => (
            <button key={event.id} onClick={() => setSelected(event)} style={timelineRow}>
              <span style={{ ...dotMini, background: severityColor(event.severity), boxShadow: `0 0 10px ${severityColor(event.severity)}99` }} />
              <span style={{ minWidth: 0 }}>
                <span style={timelineTitle}>{event.title}</span>
                <span style={timelineMeta}>{event.location} | {event.source_name || event.source || "Source"}</span>
              </span>
            </button>
          ))}
        </Panel>

        <Panel title="MARKET IMPACT">
          <div style={impactGrid}>
            {impactStack.map(([sector, score]) => (
              <div key={sector} style={impactCard}>
                <div style={{ color: labelLight, fontSize: 11, fontWeight: 900 }}>{sector}</div>
                <div style={{ color: accent, fontSize: 20, fontWeight: 900 }}>{score}</div>
              </div>
            ))}
          </div>
        </Panel>

        <Panel title="EXPOSED TICKERS">
          <div style={tickerGrid}>
            {tickerStack.map(([ticker, count]) => (
              <div key={ticker} style={tickerCard}>
                <div style={{ color: accent, fontWeight: 900 }}>${ticker}</div>
                <div style={{ color: muted, fontSize: 10 }}>{count} EVENTS</div>
              </div>
            ))}
          </div>
        </Panel>

        <Panel title="TRADE FLOOR HOLDINGS">
          {holdings.length ? (
            <div style={holdingGrid}>
              {holdings.map(h => {
                const hitCount = visibleEvents.filter(e => (e.tickers || []).includes(h.ticker)).length;
                return (
                  <div key={h.ticker} style={holdingCard}>
                    <div style={{ color: accent2, fontWeight: 900 }}>${h.ticker}</div>
                    <div style={{ color: labelLight, fontSize: 10 }}>{h.status || h.fill_status || "OPEN"}</div>
                    <div style={{ color: hitCount ? "#fb923c" : muted, fontSize: 10 }}>{hitCount} GEORISK HITS</div>
                  </div>
                );
              })}
            </div>
          ) : <div style={emptyText}>No active Trade Floor holdings returned.</div>}
        </Panel>
      </div>

      <Panel title="HOTSPOTS">
        <div style={hotspotGrid}>
          {visibleEvents.slice(0, 12).map(e => (
            <button key={e.id} onClick={() => setSelected(e)} style={eventRow}>
              <span style={{ ...dotMini, background: severityColor(e.severity), boxShadow: `0 0 10px ${severityColor(e.severity)}99` }} />
              <span style={{ flex: 1, minWidth: 0 }}>
                <span style={eventTitle}>{e.title}</span>
                <span style={eventMeta}>{e.location} | {e.theme_label} | {e.source_name || e.source}</span>
              </span>
              <span style={{ color: severityColor(e.severity), fontSize: 11, fontWeight: 900 }}>{e.score}</span>
            </button>
          ))}
        </div>
      </Panel>

      <div style={sourceNote}>{data?.source_note || "Live source pending."}</div>
    </CrtShell>
  );
}

function InteractiveMap({ events, selected, onSelect, chokepoints, holdings, heldTickers, mode, loading }) {
  const mapRef = useRef(null);
  const dragRef = useRef(null);
  const [view, setView] = useState({ scale: 0.72, x: 0, y: 0 });
  const [hoveredId, setHoveredId] = useState(null);

  const tiles = useMemo(() => {
    const zoom = 3;
    const side = 2 ** zoom;
    const rows = [];
    for (let y = 0; y < side; y += 1) {
      for (let x = 0; x < side; x += 1) {
        rows.push({
          x,
          y,
          url: `https://server.arcgisonline.com/ArcGIS/rest/services/Ocean/World_Ocean_Base/MapServer/tile/${zoom}/${y}/${x}`,
          labelUrl: `https://server.arcgisonline.com/ArcGIS/rest/services/Ocean/World_Ocean_Reference/MapServer/tile/${zoom}/${y}/${x}`,
        });
      }
    }
    return rows;
  }, []);

  const selectedPoint = selected ? project(selected.lat, selected.lon) : null;
  const mapEvents = useMemo(() => groupMapEvents(events || []), [events]);
  const sortedEvents = useMemo(() => {
    return layoutMapEvents([...mapEvents].sort((a, b) => severityRank(a.severity) - severityRank(b.severity)));
  }, [mapEvents]);

  function getCoverScale() {
    const bounds = mapRef.current?.getBoundingClientRect();
    if (!bounds) return 0.72;
    const scale = Math.max(bounds.width / WORLD_MAP_SIZE, bounds.height / WORLD_MAP_SIZE) * 1.01;
    return Math.max(0.28, Math.min(0.72, scale));
  }

  function zoomBy(delta) {
    setView(v => ({ ...v, scale: Math.max(getCoverScale(), Math.min(2.6, v.scale + delta)) }));
  }

  function resetView() {
    setView({ scale: getCoverScale(), x: 0, y: 0 });
  }

  function onPointerDown(event) {
    if (event.button !== 0) return;
    if (event.target.closest?.("[data-map-control='true']")) return;
    dragRef.current = { x: event.clientX, y: event.clientY, startX: view.x, startY: view.y };
    mapRef.current?.setPointerCapture?.(event.pointerId);
  }

  function onPointerMove(event) {
    const drag = dragRef.current;
    if (!drag) return;
    const dx = event.clientX - drag.x;
    const dy = event.clientY - drag.y;
    const nextX = drag.startX + dx;
    const nextY = drag.startY + dy;
    setView(v => ({ ...v, x: nextX, y: nextY }));
  }

  function onPointerUp(event) {
    dragRef.current = null;
    mapRef.current?.releasePointerCapture?.(event.pointerId);
  }

  function onWheel(event) {
    if (event.target.closest?.("[data-map-scroll='true']")) return;
    event.preventDefault();
    const magnitude = Math.min(1, Math.abs(event.deltaY) / 500);
    if (magnitude < 0.04) return;
    zoomBy((event.deltaY > 0 ? -1 : 1) * magnitude * 0.08);
  }

  return (
    <div
      ref={mapRef}
      style={mapShell}
      onPointerDown={onPointerDown}
      onPointerMove={onPointerMove}
      onPointerUp={onPointerUp}
      onPointerCancel={onPointerUp}
      onWheel={onWheel}
    >
      <div style={{ ...worldLayer, transform: `translate(calc(-50% + ${view.x}px), calc(-50% + ${view.y}px)) scale(${view.scale})` }}>
        <div style={tileLayer}>
          {tiles.map(tile => (
            <span key={`${tile.x}-${tile.y}`}>
              <img
                src={tile.url}
                alt=""
                draggable="false"
                style={{ ...mapTile, left: `${tile.x * 12.5}%`, top: `${tile.y * 12.5}%` }}
              />
              <img
                src={tile.labelUrl}
                alt=""
                draggable="false"
                style={{ ...mapTile, left: `${tile.x * 12.5}%`, top: `${tile.y * 12.5}%`, opacity: 0.84 }}
              />
            </span>
          ))}
        </div>
        {loading && (
          <div style={loadingOverlay}>
            <div style={loadingBox}>
              <div style={loadingTitle}>SYNCING LIVE NEWS FEEDS</div>
              <div style={loadingLine} />
              <div style={loadingMeta}>FOX | WSJ | BLOOMBERG | GDELT | STATESIDE</div>
            </div>
          </div>
        )}
        <div style={mapTint(mode)} />
        <div style={gridOverlay} />

        {sortedEvents.map(event => {
          const color = severityColor(event.severity);
          const active = selected?.id === event.id;
          const hovered = hoveredId === event.id;
          const holdingHit = (event.tickers || []).some(t => heldTickers.has(t));
          const clustered = event.map_count > 1;
          const size = active ? 16 : hovered ? 14 : clustered ? 12 : holdingHit ? 10 : event.severity === "CRITICAL" ? 9 : event.severity === "HIGH" ? 8 : 7;
          return (
            <button
              key={event.id}
              data-map-control="true"
              type="button"
              onClick={(e) => {
                e.stopPropagation();
                setHoveredId(null);
                onSelect(event);
              }}
              onPointerDown={(e) => e.stopPropagation()}
              onMouseEnter={() => setHoveredId(event.id)}
              onMouseLeave={() => setHoveredId(null)}
              style={{
                ...newsPin,
                left: `${event.map_x}%`,
                top: `${event.map_y}%`,
                width: size,
                height: size,
                background: color,
                borderColor: holdingHit ? accent2 : "#05060a",
                boxShadow: holdingHit
                  ? `0 0 ${active || hovered ? 32 : 18}px ${accent2}aa`
                  : `0 0 ${active || hovered ? 28 : 12}px ${color}88`,
                zIndex: active ? 40 : severityRank(event.severity) + 10,
              }}
              title={event.title}
            >
              {(active || hovered || holdingHit) && <span style={{ ...pinPulse, borderColor: holdingHit ? accent2 : color }} />}
              {event.map_count > 1 && <span style={pinCount}>{event.map_count}</span>}
              {hovered && event.map_count > 1 && <MapStackPreview event={event} color={color} />}
              {active && <span style={{ ...pinLabel, borderColor: `${color}66` }}>{event.location}</span>}
            </button>
          );
        })}
        {!loading && sortedEvents.length === 0 && (
          <div style={mapEmptyState}>
            <div style={loadingTitle}>NO PINPOINTED NEWS RETURNED</div>
            <div style={loadingMeta}>CHECK SOURCE / BASKET FILTERS OR REFRESH DATA</div>
          </div>
        )}
      </div>

      <div style={mapTopRail}>
        <div>
          <div style={mapKicker}>INTERACTIVE GLOBAL MAP</div>
          <div style={mapTitle}>{modeLabel(mode)}</div>
        </div>
        <div style={sourceChips}>
          {["Fox News World", "WSJ World", "Bloomberg Free World", "Stateside"].map(name => (
            <span key={name} style={sourceChip}>{name}</span>
          ))}
        </div>
      </div>

      <div style={mapTools}>
        <button data-map-control="true" onPointerDown={(e) => e.stopPropagation()} onClick={() => zoomBy(0.16)} style={toolButton}>+</button>
        <button data-map-control="true" onPointerDown={(e) => e.stopPropagation()} onClick={() => zoomBy(-0.16)} style={toolButton}>-</button>
        <button data-map-control="true" onPointerDown={(e) => e.stopPropagation()} onClick={resetView} style={resetButton}>RESET</button>
      </div>

      <div style={mapLegend}>
        {["CRITICAL", "HIGH", "WATCH", "LOW"].map(s => (
          <span key={s} style={{ display: "flex", alignItems: "center", gap: 6 }}>
            <span style={{ width: 8, height: 8, borderRadius: 99, background: severityColor(s), boxShadow: `0 0 8px ${severityColor(s)}` }} />
            {s}
          </span>
        ))}
      </div>

      <EventDrawer event={selected} onClose={() => onSelect(null)} />

      {!!holdings.length && (
        <div style={holdingsOverlay}>
          <div style={panelTitle}>TRADE FLOOR OVERLAY</div>
          {holdings.map(h => <span key={h.ticker} style={holdingPill}>${h.ticker}</span>)}
          <div style={overlayHint}>Cyan-ring pins intersect current holdings.</div>
        </div>
      )}

      <div style={attribution}>
        Map tiles by <a href="https://www.esri.com/" target="_blank" rel="noreferrer" style={{ color: "inherit" }}>Esri</a>
      </div>
    </div>
  );
}

function normalizeHoldings(tradeFloor) {
  const live = (tradeFloor?.live_alpaca || []).map(p => ({
    ticker: String(p.symbol || p.ticker || "").toUpperCase(),
    status: "LIVE",
    qty: p.qty,
    market_value: p.market_value,
  }));
  const db = (tradeFloor?.db_positions || []).map(p => ({
    ticker: String(p.ticker || p.symbol || "").toUpperCase(),
    status: p.status || p.fill_status || "DB",
    qty: p.qty_total || p.qty,
    market_value: p.notional,
  }));
  const byTicker = new Map();
  [...db, ...live].forEach(row => {
    if (row.ticker) byTicker.set(row.ticker, { ...(byTicker.get(row.ticker) || {}), ...row });
  });
  return Array.from(byTicker.values());
}

function groupMapEvents(events) {
  const groups = new Map();
  events.forEach(event => {
    const pos = project(event.lat, event.lon);
    const key = `${Math.round(pos.x / 4.6)}:${Math.round(pos.y / 5.8)}`;
    const existing = groups.get(key);
    if (!existing || Number(event.score || 0) > Number(existing.score || 0)) {
      groups.set(key, {
        ...event,
        map_count: existing ? (existing.map_count || 1) + 1 : 1,
        related_events: existing ? [...(existing.related_events || []), event] : [event],
      });
    } else {
      existing.map_count = (existing.map_count || 1) + 1;
      existing.related_events = [...(existing.related_events || []), event];
    }
  });
  return Array.from(groups.values()).map(event => ({
    ...event,
    related_events: normalizeRelatedEvents(event.related_events || [event]),
    title: event.map_count > 1 ? `${event.location} stack (${event.map_count} related headlines)` : event.title,
  }));
}

function normalizeRelatedEvents(events) {
  const byId = new Map();
  events.forEach(event => byId.set(event.id || `${event.title}:${event.url}`, event));
  return Array.from(byId.values()).sort((a, b) => Number(b.score || 0) - Number(a.score || 0));
}

function layoutMapEvents(events) {
  const placed = [];
  return events.map((event) => {
    const base = project(event.lat, event.lon);
    let x = base.x;
    let y = base.y;
    let attempts = 0;

    while (placed.some(pin => Math.abs(pin.x - x) < 4.2 && Math.abs(pin.y - y) < 5.2) && attempts < 20) {
      attempts += 1;
      const ring = Math.ceil(attempts / 8);
      const angle = ((attempts - 1) % 8) * (Math.PI / 4);
      x = base.x + Math.cos(angle) * ring * 4.8;
      y = base.y + Math.sin(angle) * ring * 6;
    }

    x = Math.max(2, Math.min(98, x));
    y = Math.max(3, Math.min(97, y));
    placed.push({ x, y });
    return { ...event, map_x: x, map_y: y };
  });
}

function MapStackPreview({ event, color }) {
  const related = (event.related_events || []).slice(0, 4);
  return (
    <span style={{ ...pinStackPreview, borderColor: `${color}55` }}>
      <span style={pinStackHeader}>
        <span style={{ ...dotMini, background: color, boxShadow: `0 0 10px ${color}88` }} />
        <span style={{ color }}>{event.map_count} HEADLINES</span>
      </span>
      <span style={pinStackLocation}>{event.location}</span>
      {related.map(item => (
        <span key={item.id || item.url || item.title} style={pinStackLine}>
          <span style={{ color: severityColor(item.severity), fontWeight: 900 }}>{item.score}</span>
          {item.title}
        </span>
      ))}
      <span style={pinStackHint}>CLICK TO OPEN FULL LOCATION STACK</span>
    </span>
  );
}

function EventDrawer({ event, onClose }) {
  if (!event) return null;
  const related = (event.related_events || []).filter(item => item.id !== event.id);
  function closeDrawer(e) {
    e.stopPropagation();
    onClose?.();
  }
  return (
    <div
      data-map-control="true"
      data-map-scroll="true"
      style={eventDrawer}
      onPointerDown={(e) => e.stopPropagation()}
      onWheel={(e) => e.stopPropagation()}
    >
      <div style={{ display: "flex", justifyContent: "space-between", gap: 12, alignItems: "center" }}>
        <div style={{ color: severityColor(event.severity), fontSize: 10, fontWeight: 900, letterSpacing: "0.16em" }}>
          {event.severity} | SCORE {event.score}/100
        </div>
        <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
          <div style={drawerSource}>{event.source_name || event.source || "Source"}</div>
          <button type="button" data-map-control="true" onClick={closeDrawer} style={drawerClose} aria-label="Close news action">X</button>
        </div>
      </div>
      <div style={drawerLocation}>{event.location}</div>
      <div style={drawerHeadline}>{event.title}</div>
      <div style={drawerBias}>{event.market_bias}</div>
      <div style={pillWrap}>
        {(event.sectors || []).slice(0, 5).map(s => <span key={s} style={sectorPill}>{s}</span>)}
      </div>
      <div style={pillWrap}>
        {(event.tickers || []).slice(0, 8).map(t => <span key={t} style={tickerPill}>${t}</span>)}
      </div>
      {related.length > 0 && (
        <div style={drawerStack}>
          <div style={drawerStackTitle}>LOCATION STACK | {event.map_count} HEADLINES</div>
          {related.map(item => (
            <a key={item.id || item.url || item.title} href={item.url} target="_blank" rel="noreferrer" style={drawerStackItem}>
              <span style={{ ...dotMini, background: severityColor(item.severity), boxShadow: `0 0 8px ${severityColor(item.severity)}66` }} />
              <span style={{ flex: 1 }}>{item.title}</span>
              <span style={{ color: severityColor(item.severity), fontWeight: 900 }}>{item.score}</span>
            </a>
          ))}
        </div>
      )}
      <a href={event.url} target="_blank" rel="noreferrer" style={sourceLink}>OPEN SOURCE</a>
    </div>
  );
}

function Panel({ title, children }) {
  return (
    <div style={panel}>
      <div style={panelTitle}>{title}</div>
      {children}
    </div>
  );
}

function SelectBox({ label, value, onChange, options }) {
  return (
    <label style={selectLabel}>
      <span>{label}</span>
      <select value={value} onChange={e => onChange(e.target.value)} style={selectBox}>
        {options.map(option => <option key={option.key} value={option.key}>{option.label}</option>)}
      </select>
    </label>
  );
}

function ScoreBar({ score, severity }) {
  const color = severityColor(severity);
  return (
    <div style={scoreShell}>
      <div style={{ ...scoreFill, width: `${Math.max(0, Math.min(100, Number(score) || 0))}%`, background: color, boxShadow: `0 0 14px ${color}88` }} />
    </div>
  );
}

function project(lat, lon) {
  const safeLat = Math.max(-85.05112878, Math.min(85.05112878, Number(lat) || 0));
  const safeLon = Math.max(-180, Math.min(180, Number(lon) || 0));
  const sin = Math.sin((safeLat * Math.PI) / 180);
  const x = ((safeLon + 180) / 360) * 100;
  const y = (0.5 - Math.log((1 + sin) / (1 - sin)) / (4 * Math.PI)) * 100;
  return { x: Math.max(0, Math.min(100, x)), y: Math.max(0, Math.min(100, y)) };
}

function modeLabel(mode) {
  return MAP_MODES.find(m => m.key === mode)?.label || "Live Intel";
}

function mapTint(mode) {
  return {
    position: "absolute",
    inset: 0,
    background: "transparent",
    pointerEvents: "none",
  };
}

const heroHeader = {
  display: "grid",
  gridTemplateColumns: "minmax(220px, 0.8fr) minmax(440px, 1.4fr)",
  gap: 14,
  alignItems: "end",
  marginBottom: 14,
};
const eyebrow = { color: accent2, fontSize: 10, letterSpacing: "0.22em", marginBottom: 7 };
const heroTitle = { color: accent, fontSize: 28, fontWeight: 900, letterSpacing: "0.09em" };
const heroControls = {
  display: "grid",
  gridTemplateColumns: "repeat(5, minmax(110px, 1fr))",
  gap: 10,
};
const statsBar = { display: "flex", background: cardBg, border: hairline, marginBottom: 14, flexWrap: "wrap" };
const filterNotice = {
  border: "1px solid rgba(250,204,21,0.28)",
  background: "rgba(250,204,21,0.06)",
  color: "#facc15",
  fontSize: 10,
  fontWeight: 900,
  letterSpacing: "0.12em",
  padding: "10px 12px",
  margin: "-4px 0 14px",
  display: "flex",
  alignItems: "center",
  justifyContent: "space-between",
  gap: 12,
  flexWrap: "wrap",
};
const filterResetButton = {
  border: "1px solid rgba(250,204,21,0.4)",
  background: "rgba(5,6,10,0.72)",
  color: "#facc15",
  fontSize: 10,
  fontWeight: 900,
  letterSpacing: "0.08em",
  padding: "6px 9px",
  cursor: "pointer",
};
const selectLabel = {
  border: hairline,
  background: cardBg,
  color: dim,
  fontSize: 9,
  letterSpacing: "0.14em",
  padding: "8px 10px",
  display: "grid",
  gap: 6,
};
const selectBox = {
  background: "#07080d",
  color: labelLight,
  border: "1px solid rgba(200,168,75,0.28)",
  padding: "7px 8px",
  fontSize: 12,
  outline: "none",
};
const toggleButton = {
  border: hairline,
  background: cardBg,
  color: muted,
  fontSize: 11,
  letterSpacing: "0.12em",
  cursor: "pointer",
};
const activeToggle = {
  ...toggleButton,
  color: accent,
  border: `1px solid ${accent}66`,
  boxShadow: `inset 0 0 18px ${accent}18`,
};
const WORLD_MAP_SIZE = 2048;
const mapShell = {
  position: "relative",
  height: "min(76vh, 760px)",
  minHeight: 560,
  border: `1px solid rgba(200,168,75,0.28)`,
  background: pageBg,
  marginBottom: 18,
  overflow: "hidden",
  cursor: "grab",
  boxShadow: "0 0 0 1px rgba(255,255,255,0.02), 0 28px 80px rgba(0,0,0,0.35)",
};
const worldLayer = {
  position: "absolute",
  left: "50%",
  top: "50%",
  width: `${WORLD_MAP_SIZE}px`,
  height: `${WORLD_MAP_SIZE}px`,
  transformOrigin: "center center",
  willChange: "transform",
};
const tileLayer = {
  position: "absolute",
  inset: 0,
  overflow: "hidden",
  background: "#2c3030",
  filter: "grayscale(1) sepia(0.1) saturate(0.08) contrast(0.98) brightness(1.02)",
};
const mapTile = {
  position: "absolute",
  width: "12.5%",
  height: "12.5%",
  objectFit: "fill",
  display: "block",
  userSelect: "none",
  pointerEvents: "none",
};
const gridOverlay = {
  position: "absolute",
  inset: 0,
  backgroundImage: "linear-gradient(rgba(180,180,168,0.24), rgba(180,180,168,0.24)), radial-gradient(circle at 50% 45%, rgba(255,255,245,0.1), transparent 56%), linear-gradient(rgba(8,10,12,0.05), rgba(8,10,12,0.05))",
  backgroundSize: "100% 100%",
  pointerEvents: "none",
};
const loadingOverlay = {
  position: "absolute",
  inset: 0,
  display: "grid",
  placeItems: "center",
  background: "radial-gradient(circle at center, rgba(5,6,10,0.06), rgba(5,6,10,0.18))",
  zIndex: 4,
  pointerEvents: "none",
};
const loadingBox = {
  border: "1px solid rgba(200,168,75,0.26)",
  background: "rgba(5,6,10,0.78)",
  padding: "16px 18px",
  minWidth: 280,
  textAlign: "center",
  boxShadow: "0 18px 45px rgba(0,0,0,0.35)",
};
const loadingTitle = { color: accent, fontSize: 12, fontWeight: 900, letterSpacing: "0.18em" };
const loadingLine = {
  height: 2,
  margin: "12px 0",
  background: `linear-gradient(90deg, transparent, ${accent2}, ${accent}, transparent)`,
};
const loadingMeta = { color: muted, fontSize: 9, letterSpacing: "0.14em" };
const mapEmptyState = {
  position: "absolute",
  left: "50%",
  top: "50%",
  transform: "translate(-50%, -50%)",
  display: "grid",
  gap: 10,
  minWidth: 280,
  border: "1px solid rgba(200,168,75,0.24)",
  background: "rgba(5,6,10,0.82)",
  padding: "16px 18px",
  textAlign: "center",
  boxShadow: "0 18px 42px rgba(0,0,0,0.4)",
  pointerEvents: "none",
};
const mapTopRail = {
  position: "absolute",
  top: 18,
  left: 18,
  right: 18,
  display: "flex",
  alignItems: "center",
  justifyContent: "space-between",
  gap: 14,
  pointerEvents: "none",
};
const mapKicker = { color: accent2, fontSize: 9, letterSpacing: "0.22em" };
const mapTitle = { color: accent, fontSize: 18, letterSpacing: "0.14em", fontWeight: 900, marginTop: 4 };
const sourceChips = { display: "flex", gap: 8, flexWrap: "wrap", justifyContent: "flex-end" };
const sourceChip = {
  color: labelLight,
  fontSize: 9,
  letterSpacing: "0.12em",
  background: "rgba(5,6,10,0.72)",
  border: "1px solid rgba(200,168,75,0.18)",
  padding: "7px 9px",
  backdropFilter: "blur(8px)",
};
const mapTools = {
  position: "absolute",
  left: 18,
  top: 88,
  display: "grid",
  gap: 7,
};
const toolButton = {
  width: 34,
  height: 34,
  border: `1px solid ${accent}66`,
  background: "rgba(5,6,10,0.78)",
  color: accent,
  fontSize: 18,
  fontWeight: 900,
  cursor: "pointer",
};
const resetButton = {
  ...toolButton,
  width: 58,
  fontSize: 10,
  letterSpacing: "0.08em",
};
const mapLegend = {
  position: "absolute",
  left: 18,
  bottom: 18,
  display: "flex",
  gap: 12,
  flexWrap: "wrap",
  color: muted,
  fontSize: 10,
  letterSpacing: "0.12em",
  background: "rgba(5,6,10,0.76)",
  border: "1px solid rgba(200,168,75,0.18)",
  padding: "9px 10px",
  backdropFilter: "blur(8px)",
};
const newsPin = {
  position: "absolute",
  transform: "translate(-50%, -50%)",
  border: "1.5px solid #05060a",
  borderRadius: 99,
  padding: 0,
  cursor: "pointer",
};
const pinPulse = {
  position: "absolute",
  inset: -8,
  border: "1px solid",
  borderRadius: 99,
  opacity: 0.55,
};
const pinCount = {
  position: "absolute",
  right: -8,
  top: -8,
  minWidth: 14,
  height: 14,
  padding: "0 3px",
  borderRadius: 99,
  background: "rgba(5,6,10,0.86)",
  border: `1px solid ${accent}66`,
  color: accent,
  fontSize: 8,
  lineHeight: "13px",
  fontWeight: 900,
};
const pinStackPreview = {
  position: "absolute",
  left: 20,
  top: -14,
  display: "grid",
  gap: 8,
  width: 340,
  maxWidth: "min(340px, 48vw)",
  padding: "12px 13px",
  border: "1px solid",
  background: "rgba(5,6,10,0.98)",
  boxShadow: "0 18px 44px rgba(0,0,0,0.62), 0 0 0 1px rgba(255,255,255,0.04)",
  textAlign: "left",
  pointerEvents: "none",
  zIndex: 80,
};
const pinStackHeader = {
  display: "flex",
  alignItems: "center",
  gap: 7,
  fontSize: 10,
  fontWeight: 900,
  letterSpacing: "0.12em",
};
const pinStackLocation = {
  color: accent,
  fontSize: 15,
  fontWeight: 900,
  letterSpacing: "0.06em",
  lineHeight: 1.15,
};
const pinStackLine = {
  color: labelLight,
  fontSize: 11,
  lineHeight: 1.4,
  whiteSpace: "normal",
  overflow: "hidden",
  display: "-webkit-box",
  WebkitLineClamp: 2,
  WebkitBoxOrient: "vertical",
  borderTop: "1px solid rgba(255,255,255,0.07)",
  paddingTop: 7,
};
const pinStackHint = {
  color: accent2,
  fontSize: 9,
  fontWeight: 900,
  letterSpacing: "0.12em",
  borderTop: "1px solid rgba(200,168,75,0.18)",
  paddingTop: 8,
};
const pinLabel = {
  position: "absolute",
  left: 22,
  top: -7,
  color: labelLight,
  background: "rgba(5,6,10,0.88)",
  border: "1px solid",
  padding: "4px 7px",
  fontSize: 10,
  letterSpacing: "0.08em",
  whiteSpace: "nowrap",
};
const routePin = {
  position: "absolute",
  transform: "translate(-50%, -50%)",
  width: 16,
  height: 16,
  border: "1px dashed",
  borderRadius: 99,
  background: "rgba(5,6,10,0.16)",
  cursor: "pointer",
  opacity: 0.78,
};
const routeCore = {
  position: "absolute",
  left: "50%",
  top: "50%",
  width: 4,
  height: 4,
  borderRadius: 99,
  transform: "translate(-50%, -50%)",
};
const eventDrawer = {
  position: "absolute",
  right: 18,
  bottom: 18,
  width: "min(440px, calc(100% - 36px))",
  maxHeight: "min(500px, calc(100% - 36px))",
  overflowY: "auto",
  overscrollBehavior: "contain",
  border: "1px solid rgba(200,168,75,0.24)",
  background: "rgba(5,6,10,0.9)",
  backdropFilter: "blur(10px)",
  padding: 16,
  boxShadow: "0 22px 50px rgba(0,0,0,0.38)",
  zIndex: 120,
};
const holdingsOverlay = {
  position: "absolute",
  left: 18,
  bottom: 66,
  maxWidth: 300,
  border: "1px solid rgba(94,234,212,0.28)",
  background: "rgba(5,6,10,0.72)",
  backdropFilter: "blur(8px)",
  padding: 12,
};
const holdingPill = {
  display: "inline-block",
  color: accent2,
  border: `1px solid ${accent2}55`,
  padding: "4px 7px",
  fontSize: 10,
  fontWeight: 900,
  margin: "0 6px 6px 0",
};
const overlayHint = { color: muted, fontSize: 9, lineHeight: 1.4, marginTop: 2 };
const drawerSource = { color: muted, fontSize: 9, letterSpacing: "0.12em" };
const drawerClose = {
  border: "1px solid rgba(255,255,255,0.12)",
  background: "rgba(255,255,255,0.04)",
  color: labelLight,
  width: 24,
  height: 24,
  fontSize: 10,
  fontWeight: 900,
  cursor: "pointer",
};
const drawerLocation = { color: accent, fontSize: 20, fontWeight: 900, marginTop: 10, letterSpacing: "0.08em" };
const drawerHeadline = { color: labelLight, fontSize: 13, lineHeight: 1.45, marginTop: 8 };
const drawerBias = { color: muted, fontSize: 11, lineHeight: 1.55, marginTop: 10 };
const drawerStack = {
  marginTop: 13,
  borderTop: "1px solid rgba(200,168,75,0.16)",
  paddingTop: 10,
  display: "grid",
  gap: 7,
  paddingRight: 4,
};
const drawerStackTitle = { color: accent2, fontSize: 9, letterSpacing: "0.16em", fontWeight: 900 };
const drawerStackItem = {
  display: "flex",
  alignItems: "flex-start",
  gap: 8,
  color: labelLight,
  textDecoration: "none",
  fontSize: 10,
  lineHeight: 1.35,
  border: "1px solid rgba(255,255,255,0.07)",
  background: "rgba(255,255,255,0.025)",
  padding: "7px 8px",
};
const attribution = {
  position: "absolute",
  right: 12,
  top: 12,
  color: "rgba(229,231,235,0.38)",
  fontSize: 9,
  background: "rgba(5,6,10,0.44)",
  padding: "4px 6px",
};
const missionGrid = { display: "grid", gridTemplateColumns: "repeat(2, minmax(0, 1fr))", gap: 18, alignItems: "start" };
const panel = { border: hairline, background: cardBg, padding: 14, marginBottom: 16 };
const panelTitle = { color: labelLight, fontSize: 10, letterSpacing: "0.18em", marginBottom: 10 };
const cardHeadline = { color: accent, fontSize: 15, fontWeight: 900, marginBottom: 9 };
const headlineBlock = { color: labelLight, fontSize: 12, lineHeight: 1.5, marginBottom: 10 };
const bodyCopy = { color: muted, fontSize: 11, lineHeight: 1.55, marginBottom: 10 };
const subTitle = { color: dim, fontSize: 9, letterSpacing: "0.14em", margin: "12px 0 7px" };
const noteRow = { color: labelLight, fontSize: 11, lineHeight: 1.45, borderTop: hairline, padding: "7px 0" };
const emptyText = { color: muted, fontSize: 12 };
const scoreShell = { width: "100%", height: 5, background: "rgba(255,255,255,0.08)", marginTop: 12, overflow: "hidden" };
const scoreFill = { height: "100%" };
const timelineRow = {
  width: "100%",
  display: "grid",
  gridTemplateColumns: "10px minmax(0, 1fr)",
  gap: 10,
  border: "none",
  borderTop: hairline,
  background: "transparent",
  padding: "9px 0",
  textAlign: "left",
  cursor: "pointer",
};
const timelineTitle = { display: "block", color: labelLight, fontSize: 11, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" };
const timelineMeta = { color: muted, fontSize: 9 };
const impactGrid = { display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(120px, 1fr))", gap: 8 };
const impactCard = { border: hairline, background: "rgba(255,255,255,0.02)", padding: 10 };
const tickerGrid = { display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(88px, 1fr))", gap: 8 };
const tickerCard = { border: hairline, background: "rgba(255,255,255,0.02)", padding: 10 };
const holdingGrid = { display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(104px, 1fr))", gap: 8 };
const holdingCard = { border: `1px solid ${accent2}33`, background: "rgba(94,234,212,0.035)", padding: 10 };
const hotspotGrid = { display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(310px, 1fr))", gap: 10 };
const eventRow = {
  width: "100%",
  display: "flex",
  alignItems: "center",
  gap: 10,
  border: hairline,
  background: "rgba(255,255,255,0.015)",
  padding: 10,
  textAlign: "left",
  cursor: "pointer",
};
const eventTitle = { display: "block", color: labelLight, fontSize: 11, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" };
const eventMeta = { color: muted, fontSize: 9 };
const dotMini = { width: 9, height: 9, flex: "0 0 auto", borderRadius: 99 };
const pillWrap = { display: "flex", gap: 7, flexWrap: "wrap", marginTop: 10 };
const sectorPill = { border: hairline, color: labelLight, padding: "4px 7px", fontSize: 10 };
const tickerPill = { border: `0.5px solid ${accent}66`, color: accent, padding: "4px 7px", fontSize: 10, fontWeight: 900 };
const sourceLink = { display: "inline-block", color: accent2, fontSize: 10, marginTop: 12, textDecoration: "none", letterSpacing: "0.1em" };
const sourceNote = { color: muted, fontSize: 10, letterSpacing: "0.08em", lineHeight: 1.5 };
