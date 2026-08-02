import React from "react";

const accent = "#c8a84b";
const accent2 = "#5eead4";
const bg = "#06060a";
const card = "#0c0c12";
const muted = "#6b7280";
const danger = "#f87171";
const hairline = "0.5px solid rgba(255,255,255,0.08)";

export default class TerminalErrorBoundary extends React.Component {
  constructor(props) {
    super(props);
    this.state = { error: null, info: null };
  }

  static getDerivedStateFromError(error) {
    return { error };
  }

  componentDidCatch(error, info) {
    this.setState({ info });
  }

  copyError = async () => {
    const { error, info } = this.state;
    const text = [
      "Case Capital frontend error",
      `message=${error?.message || "unknown"}`,
      `stack=${error?.stack || ""}`,
      `componentStack=${info?.componentStack || ""}`,
    ].join("\n");
    try {
      await navigator.clipboard.writeText(text);
    } catch {
      window.prompt("Copy diagnostics", text);
    }
  };

  render() {
    if (!this.state.error) return this.props.children;

    return (
      <div style={styles.root}>
        <div style={styles.panel}>
          <div style={styles.kicker}>FRONTEND FAULT CONTAINED</div>
          <h1 style={styles.title}>Terminal view failed to render</h1>
          <p style={styles.copy}>
            The app is still running. Retry the current view or return to Command Center.
          </p>
          <div style={styles.errorBox}>
            {this.state.error?.message || "Unknown frontend error"}
          </div>
          <div style={styles.actions}>
            <button style={styles.primary} onClick={() => window.location.reload()}>RETRY VIEW</button>
            <button style={styles.secondary} onClick={() => { window.location.href = "/"; }}>COMMAND CENTER</button>
            <button style={styles.secondary} onClick={this.copyError}>COPY ERROR</button>
          </div>
        </div>
      </div>
    );
  }
}

const styles = {
  root: {
    minHeight: "100vh",
    display: "grid",
    placeItems: "center",
    background: bg,
    color: "#e5e7eb",
    fontFamily: "JetBrains Mono, Courier New, monospace",
    padding: 24,
  },
  panel: {
    width: "min(680px, 94vw)",
    border: `1px solid ${danger}66`,
    background: card,
    boxShadow: "0 24px 90px rgba(0,0,0,0.55)",
    padding: 24,
  },
  kicker: {
    color: danger,
    fontSize: 11,
    letterSpacing: "0.2em",
    fontWeight: 900,
    marginBottom: 12,
  },
  title: {
    margin: 0,
    color: accent,
    fontSize: 28,
    letterSpacing: "0.08em",
  },
  copy: {
    color: muted,
    fontSize: 13,
    lineHeight: 1.6,
  },
  errorBox: {
    border: hairline,
    background: "rgba(248,113,113,0.08)",
    color: "#fecaca",
    padding: 14,
    marginTop: 16,
    fontSize: 12,
    whiteSpace: "pre-wrap",
  },
  actions: {
    display: "flex",
    gap: 10,
    flexWrap: "wrap",
    marginTop: 18,
  },
  primary: {
    background: accent,
    border: `1px solid ${accent}`,
    color: bg,
    fontFamily: "JetBrains Mono, Courier New, monospace",
    fontWeight: 900,
    letterSpacing: "0.1em",
    padding: "11px 16px",
    cursor: "pointer",
  },
  secondary: {
    background: "transparent",
    border: `1px solid ${accent2}66`,
    color: accent2,
    fontFamily: "JetBrains Mono, Courier New, monospace",
    fontWeight: 800,
    letterSpacing: "0.1em",
    padding: "11px 16px",
    cursor: "pointer",
  },
};
