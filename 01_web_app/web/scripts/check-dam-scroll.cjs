const assert = require('node:assert/strict');
const { chromium } = require(process.env.PLAYWRIGHT_PATH || 'playwright');
(async () => {
  const browser = await chromium.launch({ headless: true, ...(process.env.CHROME_PATH ? { executablePath: process.env.CHROME_PATH } : {}) });
  try {
    const page = await browser.newPage({ viewport: { width: 1440, height: 1000 } });
    const errors = [];
    page.on('pageerror', error => errors.push(error.message));
    await page.goto(process.env.THANPHAI_VERIFY_URL || 'http://localhost:3000', { waitUntil: 'domcontentloaded' });
    await page.locator('[data-category="water"]').click();
    await page.getByRole('button', { name: 'เขื่อน', exact: true }).click();
    const panel = page.getByRole('region', { name: 'ข้อมูลเขื่อน', exact: true });
    await panel.locator('.dam-list button').first().waitFor();
    const dims = await panel.evaluate(el => ({ height: el.clientHeight, scroll: el.scrollHeight, overflow: getComputedStyle(el).overflowY }));
    assert(dims.scroll > dims.height && ['auto', 'scroll'].includes(dims.overflow), 'Dam panel must scroll internally');
    await page.evaluate(() => { document.documentElement.style.scrollBehavior = 'auto'; window.scrollTo(0, 80); });
    const position = await panel.boundingBox();
    await page.mouse.move(position.x + position.width / 2, position.y + 230);
    const bodyY = await page.evaluate(() => window.scrollY);
    await page.mouse.wheel(0, 450);
    await page.waitForTimeout(350);
    assert(await panel.evaluate(el => el.scrollTop > 0), 'Wheel moves panel content');
    assert.equal(await page.evaluate(() => window.scrollY), bodyY, 'Wheel inside panel leaves page still');
    for (const edge of ['bottom', 'top']) {
      await panel.evaluate((el, edge) => { el.scrollTop = edge === 'bottom' ? el.scrollHeight : 0; }, edge);
      await page.mouse.wheel(0, edge === 'bottom' ? 1500 : -1500);
      await page.waitForTimeout(300);
      assert.equal(await page.evaluate(() => window.scrollY), bodyY, 'No scroll chaining at ' + edge);
    }
    const first = panel.locator('.dam-list button').first();
    assert(await first.locator('.dam-row-status').count(), 'Status has its own readable row');
    await first.click();
    await panel.locator('.dam-detail').waitFor();
    await page.waitForTimeout(150);
    assert.equal(await panel.evaluate(el => el.scrollTop), 0, 'Selected dam details open at panel top');
    await panel.getByRole('button', { name: 'ปิดรายละเอียดเขื่อน', exact: true }).click();
    await panel.getByRole('button', { name: 'ระบายน้ำมากสุด', exact: true }).click();
    assert.match(await panel.locator('.dam-list').innerText(), /ล้าน ลบ.ม.\/วัน/);
    await page.setViewportSize({ width: 375, height: 900 });
    await panel.scrollIntoViewIfNeeded();
    assert(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth));
    await panel.evaluate(el => el.scrollTop = 0);
    if (process.env.SCREENSHOT_PATH) await panel.screenshot({ path: process.env.SCREENSHOT_PATH });
    assert.deepEqual(errors, []);
    console.log('PASS: dam panel wheel scrolling, no page movement at either edge, readable rows, detail reset, sorting, mobile layout.');
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
