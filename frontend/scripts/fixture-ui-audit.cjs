const { chromium } = require(process.env.PLAYWRIGHT_MODULE_PATH || 'playwright');
const fs = require('node:fs');
const path = require('node:path');

const routes = ['/', '/scanner', '/lottery', '/pharma', '/portfolio-manager', '/trade-floor', '/options-desk', '/performance', '/learning', '/tf-engine', '/intel', '/contracts', '/sec', '/earnings', '/georisk', '/macro', '/kronos', '/case-court', '/audit-logs', '/quality', '/truth-review', '/settings', '/ticker/AAA'];
const tabs = {
  '/lottery': ['LIVE TICKETS', 'TRUTH BOARD', 'LEARNING ENGINE', 'VARIANT BOOK', 'METHODOLOGY'],
  '/portfolio-manager': ['TOTAL', 'EQUITIES', 'OPTIONS', 'P/L CALENDAR', 'LEARNING', 'EQUITIES', 'OPTIONS', 'BACKTEST', 'EQUITIES', 'OPTIONS', 'TRADE JOURNAL', 'GRAVEYARD', 'ALT UNIVERSE', 'EVIDENCE', 'DNA', 'CAPSULES', 'OPTIONS', 'EQUITIES'],
  '/options-desk': ['TAIL HUNTER', 'LEAPS SLEEVE', 'OPTIONS DESK'],
  '/case-court': ['COURT DOCS', 'ADVISORY ALIGNMENT', 'DOCKET'],
  '/pharma': ['Command', 'Track Record', 'FDA Calendar'],
  '/kronos': ['SANDBOX', 'CALENDAR', 'PM DISAGREEMENTS', 'FORECAST MEMORY', 'FORECAST'],
  '/quality': ['SCHEDULER', 'QC'],
  '/trade-floor': ['SIGNAL FILTER', 'ACCOUNT PERFORMANCE', 'RISK DASHBOARD', 'JOURNAL', 'LIVE POSITIONS'],
  '/macro': ['EVENTS'],
};
const fixtures = {
  '/status': {}, '/activity': [], '/watchlist': [], '/alerts': [], '/congress/recent': [], '/squeeze/leaderboard/top': [],
  '/scan/latest': { results: [] }, '/scan/tabs': { tabs: {}, errors: {} }, '/scheduler/overview': { jobs: [], rows: [] },
  '/contracts': { contracts: Array.from({ length: 3 }, (_, i) => ({ ticker: 'TEST' + i, recipient: 'Fixture recipient ' + i, agency: 'Fixture agency', amount: 125000000 * (i + 1), award_id: 'fixture-' + i, period_start: '2026-10-08', description: 'Synthetic contract, not a real award.', sub_awards: [{ ticker: 'SUB', recipient: 'Fixture subcontractor', amount: 10000000 }] })) }, '/learning/combos': { combos: [] }, '/trade_floor/engine/combos': { combos: [] },
  '/trade_floor/positions': { db_positions: [], live_alpaca: [] }, '/trade_floor/history': { trades: [] },
  '/lottery/board': { candidates: Array.from({ length: 65 }, (_, i) => ({ ticker: 'TEST' + i, score: 50, signals: [], triggers: [], penalties: [] })), tickets: [], truth_board: { segments: {}, latest_grades: [], learning: { notes: [] }, learned_config: {} } },
  '/macro/overview': { ok: true, regions: [{ key: 'WORLD', label: 'World', proxy: 'SPY', signal: { color: '#5eead4', label: 'NEUTRAL', score: 50, reason: 'Synthetic fixture' }, coverage: { fresh: 3, stale: 0, missing: 0, total: 3 }, categories: [{ key: 'growth', label: 'Growth', indicators: Array.from({ length: 3 }, (_, i) => ({ key: 'fixture' + i, label: 'Fixture indicator ' + i, value: 2, unit: '%', bias: 'neutral', freshness: 'fresh', trend: 'flat', date: '2026-10-08' })) }] }] }, '/v32/macro': { events: [] }, '/georisk/live': { events: [], chokepoints: [] },
  '/ticker/AAA': { ticker: 'AAA', price: '12.5', signals: [], targets: {} }, '/pm/company/AAA': { profile: null, decisions: [] },
  '/research/strategy-evidence': { ok: true, scorecards: [] },
  '/kronos/disagreements': { rows: Array.from({ length: 81 }, (_, i) => ({ ticker: 'ROW' + i, status: 'OPEN_AUDIT' })), summary: [] },
};

(async () => {
  const base = path.resolve('build');
  const server = require('node:http').createServer((req, res) => {
    let file = path.resolve(base, '.' + decodeURIComponent(new URL(req.url, 'http://localhost').pathname));
    if (!file.startsWith(base + path.sep) && file !== base) { res.writeHead(403); return res.end(); }
    if (!fs.existsSync(file) || fs.statSync(file).isDirectory()) file = path.join(base, 'index.html');
    res.setHeader('Content-Type', ({ '.html': 'text/html', '.js': 'application/javascript', '.css': 'text/css', '.svg': 'image/svg+xml' })[path.extname(file)] || 'application/octet-stream');
    fs.createReadStream(file).pipe(res);
  });
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  const previewURL = 'http://127.0.0.1:' + server.address().port;
  const browser = await chromium.launch({ headless: true, executablePath: process.env.CHROME_PATH || 'C:/Program Files/Google/Chrome/Application/chrome.exe' });
  const checks = [], mutations = [], errors = [];
  try {
    for (const width of [390, 1440]) {
      const context = await browser.newContext({ viewport: { width, height: 900 } });
      await context.addInitScript(() => { try { sessionStorage.setItem('case_capital_terminal_session_v1', JSON.stringify({ mode: 'preview', token: 'fixture-only' })); } catch {} });
      await context.route('**/*', async route => {
        const request = route.request(), url = new URL(request.url());
        if (url.pathname.startsWith('/api/')) {
          if (request.method() !== 'GET') { mutations.push(url.pathname); return route.abort(); }
          return route.fulfill({ status: 200, contentType: 'application/json', headers: { 'Access-Control-Allow-Origin': '*' }, body: JSON.stringify(fixtures[url.pathname.slice(4)] ?? {}) });
        }
        if (['localhost', '127.0.0.1'].includes(url.hostname)) return route.continue();
        return route.abort();
      });
      const page = await context.newPage();
      page.on('pageerror', error => errors.push(String(error)));
      const check = async (route, tab, start) => {
        checks.push({ width, route, tab, rendered: await page.locator('.terminal-page-body').count() === 1,
          overflow: await page.evaluate(() => document.documentElement.scrollWidth > innerWidth),
          boundary: await page.getByText('Terminal view failed to render', { exact: false }).count() > 0,
          fragmentedMetric: route === '/macro' && tab === 'initial' && width === 390 ? await page.locator('.terminal-stat .num').first().evaluate(node => node.getBoundingClientRect().height > 30) : false,
          errors: errors.slice(start) });
      };
      for (const route of routes) {
        let start = errors.length;
        await page.goto(previewURL + route, { waitUntil: 'domcontentloaded', timeout: 15000 });
        await page.waitForTimeout(2200);
        await check(route, 'initial', start);
        if (process.env.UI_AUDIT_SCREENSHOTS && ['/contracts', '/macro', '/kronos', '/portfolio-manager'].includes(route)) {
          fs.mkdirSync(process.env.UI_AUDIT_SCREENSHOTS, { recursive: true });
          await page.screenshot({ path: path.join(process.env.UI_AUDIT_SCREENSHOTS, route.slice(1) + '-' + width + '.png'), fullPage: true });
        }
        for (const name of tabs[route] || []) {
          start = errors.length;
          const button = page.getByRole('button', { name, exact: true });
          if (await button.count() !== 1) { checks.push({ width, route, tab: name, missing: true }); continue; }
          await button.click({ timeout: 4000 }); await page.waitForTimeout(250);
          await check(route, name, start);
        }
      }
      await context.close();
    }
  } finally { await browser.close(); await new Promise(resolve => server.close(resolve)); }
  const failures = checks.filter(check => check.missing || !check.rendered || check.overflow || check.boundary || check.fragmentedMetric || check.errors.length);
  const result = { fixture_only: true, checks, failures, mutations };
  const output = process.env.UI_AUDIT_OUTPUT || path.join(require('node:os').tmpdir(), 'case-capital-ui-audit.json');
  fs.writeFileSync(output, JSON.stringify(result, null, 2));
  console.log(JSON.stringify({ checks: checks.length, failures, mutations, output }));
  if (failures.length || mutations.length) process.exitCode = 1;
})().catch(error => { console.error(error); process.exitCode = 1; });
