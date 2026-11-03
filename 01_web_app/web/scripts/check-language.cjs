const assert=require('node:assert/strict');
const {chromium}=require(process.env.PLAYWRIGHT_PATH||'playwright');
(async()=>{
 const browser=await chromium.launch({headless:true,...(process.env.CHROME_PATH?{executablePath:process.env.CHROME_PATH}:{})});
 try{
  const page=await browser.newPage({viewport:{width:1440,height:900}});
  const errors=[];page.on('pageerror',error=>errors.push(error.message));
  const base=process.env.THANPHAI_VERIFY_URL||'http://localhost:3000';
  await page.goto(base);
  await page.waitForFunction(()=>!!document.querySelector('.landscape-shell:not([inert])'));
  await page.getByRole('button',{name:'English',exact:true}).click();
  await page.waitForFunction(()=>document.documentElement.lang==='en');
  await page.getByRole('heading',{name:'Know the risk. Stay safer.',exact:true}).waitFor();
  assert.equal(await page.locator('[data-category=water] strong').innerText(),'Water');
  assert.equal(await page.locator('.snapshot-label').nth(1).innerText(),'Critical');
  assert.equal(await page.locator('.map-options button').nth(1).innerText(),'Night map');
  await page.locator('[data-category=water]').click();
  await page.locator('[data-subcategory=water-quality]').click();
  await page.getByRole('heading',{name:'Water quality monitoring stations',exact:true}).waitFor();
  await page.getByRole('button',{name:'ภาษาไทย',exact:true}).focus();await page.getByRole('button',{name:'ภาษาไทย',exact:true}).press('Enter');
  await page.getByRole('heading',{name:'แผนที่สถานีวัดคุณภาพน้ำ',exact:true}).waitFor();
  assert.equal(await page.locator('.landscape-shell').getAttribute('data-active-category'),'water-quality','Switch preserves active view');
  await page.getByRole('button',{name:'English',exact:true}).click();
  await page.locator('.journey-search-icon').click();
  assert(await page.getByRole('combobox',{name:'Search areas',exact:true}).isVisible());
  assert(await page.getByRole('button',{name:'My location',exact:true}).isVisible());
  await page.getByRole('button',{name:'Close area search',exact:true}).click();
  await page.getByRole('button',{name:'How to use',exact:true}).click();
  assert(await page.getByRole('heading',{name:'Getting started with ThanPhai',exact:true}).isVisible());
  await page.getByRole('button',{name:'Close guide',exact:true}).click();
  await page.reload();await page.waitForFunction(()=>!!document.querySelector('.landscape-shell:not([inert])'));
  assert.equal(await page.locator('html').getAttribute('lang'),'en','English preference survives reload');
  for(const [width,height] of [[1920,900],[1440,900],[1280,600],[768,900],[375,667],[320,568]]){
   await page.setViewportSize({width,height});await page.waitForTimeout(450);
   const switchBounds=await page.locator('.language-switch').boundingBox();
   assert(switchBounds.x>=0&&switchBounds.y>=0&&switchBounds.x+switchBounds.width<=width&&switchBounds.y+switchBounds.height<=height,`Language buttons in viewport ${width}x${height}`);
   assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1&&document.documentElement.scrollHeight<=innerHeight+1),`No page overflow ${width}x${height}`);
   if(process.env.SCREENSHOT_PREFIX&&[1440,375].includes(width))await page.screenshot({path:process.env.SCREENSHOT_PREFIX+'-'+width+'.png'});
  }
  await page.getByRole('button',{name:'ภาษาไทย',exact:true}).focus();await page.getByRole('button',{name:'ภาษาไทย',exact:true}).press('Enter');
  await page.reload();await page.waitForFunction(()=>!!document.querySelector('.landscape-shell:not([inert])'));
  assert.equal(await page.locator('html').getAttribute('lang'),'th','Thai preference survives reload');
  assert.deepEqual(errors,[]);
  console.log('PASS: TH/EN UI, navigation, search, GPS controls, guide, view preservation, preference persistence and six responsive layouts.');
 }finally{await browser.close();}
})().catch(error=>{console.error(error);process.exitCode=1;});
