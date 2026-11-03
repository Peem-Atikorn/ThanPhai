const assert = require('node:assert/strict');
const { chromium } = require(process.env.PLAYWRIGHT_PATH || 'playwright');
(async () => {
  const browser = await chromium.launch({ headless: true, ...(process.env.CHROME_PATH ? { executablePath: process.env.CHROME_PATH } : {}) });
  try {
    const page = await browser.newPage({ viewport: { width: 1440, height: 1000 } });
    const errors = [];
    page.on('pageerror', error => errors.push(error.message));
    await page.goto(process.env.THANPHAI_VERIFY_URL || 'http://localhost:3000', { waitUntil: 'domcontentloaded' });
    const summary = page.getByRole('region', { name: /สรุปสถานการณ์/ });
    await summary.waitFor();
    const inHeader = await summary.evaluate(el => !!el.closest('.journey-header'));
    assert(inHeader, 'Four summary cards must be in the header');
    assert.equal(await summary.locator('article').count(), 4);
    await page.waitForFunction(() => [...document.querySelectorAll('.header-snapshot strong')].every(el => el.textContent !== '—'));
    const nationalCounts = await summary.locator('strong').allTextContents();
    const positions = [];
    for (const group of ['overview', 'water', 'weather', 'smoke', 'hot', 'geo']) {
      await page.locator(`[data-category="${group}"]`).click();
      const tabs = page.locator('.category-flyout[data-open=true] [data-subcategory]');
      const count = await tabs.count();
      for (let i = 0; i < Math.max(count, 1); i++) {
        if (count) await tabs.nth(i).click();
        const canvas = await page.locator('.map-canvas').boundingBox();
        positions.push(Math.round(canvas.y + await page.evaluate(() => window.scrollY)));
        assert(canvas.height >= 500, 'Map must be large enough on desktop');
      }
    }
    assert(Math.max(...positions) - Math.min(...positions) <= 2, `Map canvas tops align in every view: ${positions}`);
    await page.waitForFunction(() => [...document.querySelectorAll('.header-snapshot strong')].every(el => el.textContent !== '—'));
    assert.deepEqual(await summary.locator('strong').allTextContents(), nationalCounts, 'Header keeps national totals across map views');
    const panel = await page.locator('.map-panel').boundingBox();
    const sidebar = await page.locator('.detail-sidebar').boundingBox();
    assert(Math.abs(panel.y - sidebar.y) <= 2, 'Map and detail panel start at the same level');
    await page.locator('[data-category="water"]').click();
    await page.getByRole('button', { name: 'คุณภาพน้ำ', exact: true }).click();
    const quality = page.getByRole('region', { name: 'ข้อมูลคุณภาพน้ำ', exact: true });
    await quality.locator('.dam-list button').first().click({ timeout: 30000 });
    assert.equal(await quality.locator('.quality-metrics details').count(), 7, 'Every metric has an explanation');
    await quality.locator('.quality-metrics details').nth(1).locator('summary').click();
    assert.match(await quality.locator('.quality-metrics').innerText(), /6.*4.*2/);
    assert(await quality.getByRole('link', { name: /กรมควบคุมมลพิษ/ }).count());
    if (process.env.SCREENSHOT_PREFIX) await page.screenshot({ path: process.env.SCREENSHOT_PREFIX + '-desktop.png', fullPage: true });
    for (const width of [320, 375, 414, 768]) {
      await page.setViewportSize({ width, height: 900 });
      assert(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth), 'No page overflow at ' + width);
      const map = await page.locator('.map-canvas').boundingBox();
      assert(map.width >= width - (width > 600 ? 100 : 54), 'Map uses available mobile width: ' + width);
      if (width === 375 && process.env.SCREENSHOT_PREFIX) await page.screenshot({ path: process.env.SCREENSHOT_PREFIX + '-mobile.png', fullPage: true });
    }
    assert.deepEqual(errors, []);
    console.log(JSON.stringify({ passed: true, checkedViews: positions.length, mapTopRange: [Math.min(...positions), Math.max(...positions)], widths: [320,375,414,768] }));
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
