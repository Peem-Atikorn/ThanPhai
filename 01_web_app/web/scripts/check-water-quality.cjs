const assert = require('node:assert/strict');
const { chromium } = require(process.env.PLAYWRIGHT_PATH || 'playwright');

(async () => {
  const browser = await chromium.launch({ headless: true, ...(process.env.CHROME_PATH ? { executablePath: process.env.CHROME_PATH } : {}) });
  try {
    const page = await browser.newPage({ viewport: { width: 1440, height: 1000 } });
    const errors = [];
    page.on('pageerror', e => errors.push(e.message));
    let mode = 'success';
    const station = { id: 'q1', name_th: 'สถานีทดสอบ', river_th: 'แม่น้ำทดสอบ', province_th: 'นนทบุรี', amphoe_th: 'เมือง', agency_th: 'กปน.', province_code: 'TH12', lat: 13.9, lon: 100.5, salinity_gl: 0.54, do_mgl: null, ph: 7.1, turbidity_ntu: 12, tds_mgl: 400, conductivity_uscm: 800, temp_c: 29, time: '2026-11-03T10:00:00+07:00', exceeds: { salinity: { text: '> 0.5', color: '#ff0000' } } };
    await page.route('**/api/v1/water-quality', route => mode === 'error'
      ? route.fulfill({ status: 503, json: { detail: 'unavailable' } })
      : route.fulfill({ json: { stations: mode === 'empty' ? [] : [station, { ...station, id: 'q2', name_th: 'สถานีศูนย์', salinity_gl: 0, do_mgl: 6, exceeds: {} }, { ...station, id: 'q3', name_th: 'สถานีไม่มีค่า', salinity_gl: null, lat: null, lon: null, exceeds: {} }], source: 'แหล่งข้อมูลทดสอบ', data_updated_at: station.time, degraded_services: mode === 'stale' ? ['thaiwater_waterquality'] : [] } }));
    await page.goto(process.env.THANPHAI_VERIFY_URL || 'http://localhost:3000', { waitUntil: 'domcontentloaded' });
    await page.locator('[data-category="water"]').click();
    await page.getByRole('button', { name: 'คุณภาพน้ำ', exact: true }).click({ timeout: 5000 });
    const panel = page.getByRole('region', { name: 'ข้อมูลคุณภาพน้ำ', exact: true });
    assert.equal(await page.locator('.command-shell.landscape-shell').count(), 1, 'Original landscape theme must be preserved');
    await panel.getByRole('button', { name: /สถานีทดสอบ/ }).click();
    await page.getByRole('heading', { name: 'สถานีทดสอบ', exact: true }).waitFor();
    assert.match(await panel.innerText(), /ความเค็ม/);
    assert.match(await panel.innerText(), /0.54/);
    assert.match(await panel.innerText(), /เกินเกณฑ์/);
    assert.match(await panel.innerText(), /ออกซิเจนละลายน้ำ/);
    assert.match(await panel.innerText(), /เวลาไทย/);
    assert.match(await panel.innerText(), /แหล่งข้อมูลทดสอบ/);
    await panel.getByRole('button', { name: /สถานีศูนย์/ }).click();
    assert.match(await panel.locator('.quality-metrics').innerText(), /0.00/);
    await panel.getByRole('button', { name: /สถานีไม่มีค่า/ }).click();
    assert.match(await panel.locator('.quality-metrics').innerText(), /—/);
    for (const width of [1440, 390]) {
      await page.setViewportSize({ width, height: 1000 });
      assert(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth), 'No horizontal page overflow at ' + width);
    }
    for (const next of ['empty', 'error', 'stale']) {
      mode = next;
      await page.getByRole('button', { name: 'โหลดข้อมูลแผนที่ใหม่', exact: true }).click();
      await panel.getByText(next === 'empty' ? 'ยังไม่มีข้อมูลคุณภาพน้ำล่าสุด' : next === 'error' ? 'โหลดข้อมูลคุณภาพน้ำไม่สำเร็จ' : 'แหล่งข้อมูลคุณภาพน้ำไม่พร้อมใช้งาน อาจเป็นข้อมูลที่เก็บไว้ก่อนหน้า', { exact: true }).waitFor();
    }
    mode = 'success';
    await page.getByRole('button', { name: 'โหลดข้อมูลแผนที่ใหม่', exact: true }).click();
    await panel.getByRole('button', { name: /สถานีทดสอบ/ }).waitFor();
    assert.deepEqual(errors, [], 'No client runtime errors');
    if (process.env.SCREENSHOT_PATH) await page.screenshot({ path: process.env.SCREENSHOT_PATH, fullPage: true });
    console.log('PASS: water-quality navigation, measurements, zero/missing values, source/time, empty/error/stale/retry, and mobile layout.');
  } finally { await browser.close(); }
})().catch(e => { console.error(e); process.exitCode = 1; });
