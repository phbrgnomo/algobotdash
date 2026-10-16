/* Real browser coverage of the shipped renderer, API and shared selections. */
const assert = require("node:assert/strict");
const {chromium} = require(process.env.PLAYWRIGHT_MODULE || "playwright");

(async () => {
  const browser = await chromium.launch({headless: true});
  try {
    const page = await browser.newPage({viewport: {width: 1280, height: 960}, hasTouch: true});
    const errors = [];
    page.on("pageerror", error => errors.push(error.message));
    for (let attempt = 0; ; attempt++) {
      try { await page.goto(process.env.SMOKE_URL); break; }
      catch (error) {
        if (attempt === 49) throw error;
        await new Promise(resolve => setTimeout(resolve, 100));
      }
    }
    await page.waitForSelector("#performance-chart svg");
    const series = await page.evaluate(async () => (await fetch("/api/series")).json());
    assert.equal(series.strategies.length, 3);
    const final = series.periods.at(-1).cumulative;
    await page.waitForFunction(value => document.querySelector("#metric-net-pnl").textContent === value,
      final.toLocaleString("pt-BR", {minimumFractionDigits: 2, maximumFractionDigits: 2}));
    assert.equal(await page.locator("#performance-chart polyline").count(), 4);
    assert.equal(await page.locator("#monetary-chart svg").count(), 1);
    assert.equal(series.drawdown.monetary.points.every(point => point.depth <= 0), true);
    assert.match(await page.locator("#percentage-chart").innerText(), /Saldo de abertura/);
    for (const granularity of ["week", "month", "year", "day"]) {
      const loaded = page.waitForResponse(response => response.url().includes(`/api/series?`) && response.url().includes(`granularity=${granularity}`));
      await page.selectOption("#chart-granularity", granularity);
      assert.equal((await (await loaded).json()).periods.at(-1).cumulative, final);
      await page.waitForSelector("#performance-chart svg");
    }
    await page.selectOption("#filter-symbol-family", ["WIN"]);
    await page.selectOption("#filter-strategy", ["strategy:FVG", "strategy:Turtle"]);
    await page.waitForFunction(() => document.querySelector("#positions-body").rows.length === 2
      && document.querySelectorAll("#performance-chart polyline").length === 3);
    assert.match(await page.locator("#performance-chart .chart-inspection").innerText(), /Total selecionado/);
    assert.match(await page.locator("#performance-chart .chart-inspection").innerText(), /WIN FVG/);
    assert.match(await page.locator("#performance-chart .chart-inspection").innerText(), /WIN Turtle/);
    await page.locator("#performance-chart [tabindex]").focus();
    await page.keyboard.press("Home");
    const inspection = page.locator("#performance-chart .chart-inspection");
    const firstPeriod = await inspection.innerText();
    assert.match(firstPeriod, /01\/08\/2026/);
    assert.match(firstPeriod, /Resultado: 100,00 · Acumulado: 100,00/);
    assert.match(firstPeriod, /WIN Turtle: 0,00 · Acumulado: 0,00/);
    await page.keyboard.press("End");
    const lastPeriod = await inspection.innerText();
    assert.notEqual(lastPeriod, firstPeriod);
    assert.match(lastPeriod, /03\/08\/2026/);
    assert.match(lastPeriod, /Resultado: -30,00 · Acumulado: 70,00/);
    assert.match(lastPeriod, /WIN Turtle: -30,00 · Acumulado: -30,00/);
    await page.locator("#performance-chart [tabindex]").hover({position: {x: 2, y: 40}});
    assert.equal(await inspection.innerText(), firstPeriod);
    // Touch must change a multi-period panel, not merely hit a single-point chart.
    await page.setViewportSize({width: 390, height: 844});
    await page.waitForTimeout(150);
    await page.locator("#performance-chart [tabindex]").focus();
    await page.keyboard.press("Home");
    const hit = await page.locator("#performance-chart [tabindex]").boundingBox();
    await page.touchscreen.tap(hit.x + hit.width - 3, hit.y + hit.height / 2);
    assert.equal(await inspection.innerText(), lastPeriod);
    await page.setViewportSize({width: 1280, height: 960});
    await page.waitForTimeout(150);
    // Category-only selection is a real filtered sample in all three resources.
    await page.locator("#symbols-all").click();
    await page.selectOption("#filter-strategy", ["unclassified:"]);
    await page.waitForFunction(() => document.querySelector("#metric-net-pnl").textContent === "7,00"
      && document.querySelector("#positions-body").rows.length === 1
      && document.querySelectorAll("#performance-chart polyline").length === 2);
    await page.locator("#strategies-clear").click();
    await page.waitForFunction(() => document.querySelector("#performance-chart").textContent.includes("Nenhuma combinação selecionada"));
    assert.equal(await page.locator("#metric-net-pnl").innerText(), "—");
    assert.equal(await page.locator("#positions-body tr").count(), 0);
    await page.locator("#strategies-all").click();
    await page.waitForSelector("#performance-chart svg");
    await page.fill("#date-from", "2026-09-01");
    await page.waitForFunction(() => document.querySelector("#performance-chart").textContent.includes("Sem resultados realizados"));
    await page.fill("#date-from", "");
    await page.waitForSelector("#performance-chart svg");
    await page.selectOption("#filter-status", "open");
    await page.waitForFunction(() => document.querySelector("#performance-chart").textContent.includes("Somente posições realizadas"));
    await page.selectOption("#filter-status", "closed");
    await page.waitForSelector("#performance-chart svg");
    // Resolve a stale series response after a newer filter response.
    let release;
    let captured;
    const seen = new Promise(resolve => { captured = resolve; });
    await page.route("**/api/series?**", async route => {
      const response = await route.fetch();
      captured();
      await new Promise(resolve => { release = resolve; });
      await route.fulfill({response});
    }, {times: 1});
    await page.selectOption("#filter-symbol-family", "WDO");
    await seen;
    await page.selectOption("#filter-symbol-family", "WIN");
    await page.waitForFunction(() => document.querySelector("#performance-chart .chart-legend")?.textContent.includes("WIN FVG"));
    release();
    await page.waitForTimeout(100);
    assert.doesNotMatch(await page.locator("#performance-chart .chart-legend").innerText(), /WDO/);
    // A successful refresh removes the extra fixture rows and changes the revision.
    await page.locator("#refresh-button").click();
    await page.waitForFunction(() => document.querySelector("#refresh-state").textContent.includes("concluída"));
    await page.waitForFunction(() => document.querySelector("#positions-body").rows.length === 1
      && document.querySelectorAll("#performance-chart polyline").length === 2);
    if (process.env.SMOKE_ARTIFACT_DIR) {
      await page.screenshot({path: `${process.env.SMOKE_ARTIFACT_DIR}/charts-desktop.png`, fullPage: true});
    }
    await page.setViewportSize({width: 390, height: 844});
    await page.waitForTimeout(150);
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true);
    if (process.env.SMOKE_ARTIFACT_DIR) {
      await page.screenshot({path: `${process.env.SMOKE_ARTIFACT_DIR}/charts-mobile.png`, fullPage: true});
    }
    assert.deepEqual(errors, []);
    console.log("Browser smoke OK: desktop, mobile, touch/keyboard, aggregation, selections, empty/open states, stale response and refresh.");
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
