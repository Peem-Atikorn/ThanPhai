const assert = require('node:assert/strict');
const { chromium } = require(process.env.PLAYWRIGHT_PATH || 'playwright');
(async () => {
  const browser = await chromium.launch({ headless: true, ...(process.env.CHROME_PATH ? { executablePath: process.env.CHROME_PATH } : {}) });
  try {
    const page = await browser.newPage({ viewport: { width: 1440, height: 900 } });
    const errors = [];
    page.on('pageerror', error => errors.push(error.message));
    await page.goto(process.env.THANPHAI_VERIFY_URL || 'http://localhost:3000', { waitUntil: 'domcontentloaded' });
    assert.equal(await page.getByRole('link', { name: /สำรวจพื้นที่/ }).count(), 0, 'Remove Explore button');
    let views = 0;
    for (const width of [1440, 1920, 768, 375, 320]) {
      await page.setViewportSize({ width, height: 900 });
      for (const group of ['overview','water','weather','smoke','hot','geo']) {
        await page.locator(`[data-category="${group}"]`).click();
        const tabs = page.locator('.category-flyout[data-open=true] [data-subcategory]');
        const count = await tabs.count();
        for (let i=0; i<Math.max(count,1); i++) {
          if(count) { await page.locator(`[data-category="${group}"]`).click(); await tabs.nth(i).click(); }
          assert(await page.evaluate(() => document.documentElement.scrollHeight <= window.innerHeight + 1), `No page vertical overflow: ${width}/${group}/${i}`);
          assert(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth), 'No horizontal page overflow');
          for(const selector of ['.map-canvas','.detail-sidebar','.journey-footer']) {
            const box = await page.locator(selector).boundingBox();
            assert(box && box.y >= -1 && box.y+box.height <= 901, `${selector} stays on screen: ${width}/${group}/${i}: ${JSON.stringify(box)}`);
          }
          views++;
        }
      }
      if(process.env.SCREENSHOT_PREFIX && [1440,375].includes(width)) await page.screenshot({path:process.env.SCREENSHOT_PREFIX+'-'+width+'.png'});
    }
    for (const [width,height] of [[1366,768],[1280,600],[375,667],[320,568]]) {
      await page.setViewportSize({width,height});
      for(const group of ['overview','water','weather','smoke','hot','geo']) {
        await page.locator(`[data-category="${group}"]`).click();
        for(const selector of ['.map-canvas','.detail-sidebar','.journey-footer']) {
          const box=await page.locator(selector).boundingBox();
          assert(box && box.height>0 && box.y>=-1 && box.y+box.height<=height+1, `${selector} fits ${width}x${height}`);
        }
        assert(await page.evaluate(()=>document.documentElement.scrollHeight<=innerHeight+1),'Short screen does not scroll');
      }
      await page.getByRole('button',{name:'พื้นที่ที่คุณอยากรู้',exact:true}).click();
      await page.getByRole('dialog',{name:'ค้นหาพื้นที่',exact:true}).waitFor();
      await page.getByRole('button',{name:'ปิดหน้าค้นหาพื้นที่',exact:true}).click();
      await page.getByRole('button',{name:'วิธีใช้งานทันภัย',exact:false}).click();
      await page.getByRole('dialog',{name:'เริ่มต้นใช้งานทันภัย'}).waitFor();
      await page.getByRole('button',{name:'ปิดคำแนะนำ',exact:true}).click();
    }
    await page.setViewportSize({width:1440,height:900});
    await page.locator('[data-category="water"]').click();
    await page.getByRole('button',{name:'เขื่อน',exact:true}).click();
    const panel=page.getByRole('region',{name:'ข้อมูลเขื่อน',exact:true});
    await panel.locator('.dam-list button').first().waitFor();
    const box=await panel.boundingBox();
    await page.mouse.move(box.x+box.width/2,box.y+Math.min(180,box.height/2));
    await page.mouse.wheel(0,500);
    await page.waitForTimeout(250);
    assert(await panel.evaluate(el=>el.scrollTop>0),'Dam panel still scrolls');
    assert.equal(await page.evaluate(()=>window.scrollY),0);
    await page.locator('.assistant-panel > summary').click();
    await page.getByRole('textbox',{name:'อยากรู้สถานการณ์เรื่องไหน?'}).waitFor();
    await page.locator('.map-supplement > summary').click();
    assert(await page.locator('.map-supplement .map-summary').isVisible(),'Map legend can still be opened');
    assert(await page.evaluate(()=>document.documentElement.scrollHeight<=window.innerHeight+1),'Disclosures do not grow page');
    assert.deepEqual(errors,[]);
    console.log(JSON.stringify({passed:true,viewportsAndViews:views,viewportHeight:900}));
  } finally { await browser.close(); }
})().catch(error=>{console.error(error);process.exitCode=1;});
