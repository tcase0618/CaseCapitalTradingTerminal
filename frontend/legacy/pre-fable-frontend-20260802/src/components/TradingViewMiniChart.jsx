import { useMemo } from "react";
import { tokens } from "./CrtShell";

const { accent, dim, muted, cardBg, pageBg, hairline } = tokens;

function cleanTicker(ticker) {
  return String(ticker || "")
    .trim()
    .replace(/^\$/, "")
    .toUpperCase();
}

export default function TradingViewMiniChart({ ticker, companyName = "", height = 460 }) {
  const symbol = useMemo(() => cleanTicker(ticker), [ticker]);
  const displayName = companyName || `$${symbol}`;
  const frameUrl = useMemo(() => {
    if (!symbol) return "";
    const params = new URLSearchParams({
      frameElementId: `casecap-tv-${symbol}`,
      symbol,
      interval: "15",
      range: "1D",
      hidetoptoolbar: "1",
      hidesidetoolbar: "1",
      symboledit: "0",
      saveimage: "0",
      toolbarbg: "0c0c12",
      studies: "[]",
      theme: "dark",
      style: "1",
      timezone: "America/New_York",
      withdateranges: "0",
      hideideas: "1",
      locale: "en",
    });
    return `https://s.tradingview.com/widgetembed/?${params.toString()}`;
  }, [symbol]);

  if (!symbol) return null;

  return (
    <div
      className="corner-brackets fade-in"
      style={{
        height: `clamp(360px, 52vh, ${height}px)`,
        marginBottom: 20,
        background: `linear-gradient(180deg, ${cardBg} 0%, ${pageBg} 180%)`,
        border: hairline,
        position: "relative",
        overflow: "hidden",
        display: "flex",
        flexDirection: "column",
      }}
    >
      <div
        style={{
          position: "absolute",
          top: 0,
          left: 0,
          right: 0,
          height: 1,
          background: `linear-gradient(90deg, ${accent} 0%, ${accent}33 35%, transparent 100%)`,
          zIndex: 2,
        }}
      />
      <div
        style={{
          display: "flex",
          gap: 10,
          alignItems: "center",
          minHeight: 34,
          padding: "0 16px",
          borderBottom: hairline,
          fontSize: 9,
          letterSpacing: "0.18em",
          color: muted,
          background: "rgba(3,3,6,0.55)",
          position: "relative",
          zIndex: 2,
        }}
      >
        <span style={{ color: accent }}>LIVE CHART</span>
        <span style={{ color: dim }}>/</span>
        <span
          style={{
            color: "#e5e7eb",
            fontWeight: 700,
            overflow: "hidden",
            textOverflow: "ellipsis",
            whiteSpace: "nowrap",
          }}
        >
          {displayName}
        </span>
        <span style={{ color: dim, marginLeft: "auto" }}>${symbol}</span>
      </div>
      <iframe
        id={`casecap-tv-${symbol}`}
        title={`${symbol} TradingView chart`}
        src={frameUrl}
        scrolling="no"
        frameBorder="0"
        style={{
          border: 0,
          flex: 1,
          minHeight: 0,
          width: "100%",
        }}
      />
    </div>
  );
}
