import { lazy } from "react";

// One import promise per page: pointer/focus prefetch and routing share the same chunk.
const definitions = [
  ["/", "Command Center", "Operations", () => import("../components/CommandCenterPage")],
  ["/scanner", "Scanner", "Discovery", () => import("../components/Dashboard")],
  ["/lottery", "Lottery", "Discovery", () => import("../components/LotteryPage")],
  ["/pharma", "Pharma", "Discovery", () => import("../components/PharmaPage")],
  ["/portfolio-manager", "Portfolio Manager", "Operations", () => import("../components/PortfolioManagerPage")],
  ["/trade-floor", "Trade Floor", "Operations", () => import("../components/TradeFloorPage")],
  ["/options-desk", "Options Desk", "Operations", () => import("../components/OptionsDeskPage")],
  ["/performance", "Performance", "Research", () => import("../components/PerformancePage")],
  ["/learning", "Learning", "Research", () => import("../components/LearningPage")],
  ["/tf-engine", "Trade Engine", "Research", () => import("../components/TFEnginePage")],
  ["/intel", "Intelligence", "Research", () => import("../components/IntelPage")],
  ["/contracts", "Contracts", "Research", () => import("../components/ContractsPage")],
  ["/sec", "SEC Filings", "Research", () => import("../components/SECPage")],
  ["/earnings", "Earnings", "Research", () => import("../components/EarningsPage")],
  ["/georisk", "Geopolitical Risk", "Research", () => import("../components/GeoRiskPage")],
  ["/macro", "Macro", "Research", () => import("../components/MacroPage")],
  ["/kronos", "Kronos", "Research", () => import("../components/KronosPage")],
  ["/case-court", "Case Court", "Research", () => import("../components/CaseCourtPage")],
  ["/audit-logs", "Audit Logs", "System", () => import("../components/AuditLogsPage")],
  ["/quality", "Data Quality", "System", () => import("../components/QualityPage")],
  ["/truth-review", "Truth Review", "System", () => import("../components/TruthReviewPage")],
  ["/settings", "Settings", "System", () => import("../components/SettingsPage")],
  ["/ticker/:ticker", "Company Profile", "Research", () => import("../components/TickerPage")],
];

export const pages = definitions.map(([path, label, group, importer]) => {
  let pending;
  const load = () => {
    if (!pending) pending = importer().catch(error => { pending = null; throw error; });
    return pending;
  };
  return { path, label, group, load, Component: lazy(load) };
});

export function prefetchPage(path) {
  const page = pages.find(item => item.path === path || (item.path === "/ticker/:ticker" && path.startsWith("/ticker/")));
  if (page) page.load().catch(() => {});
}
