const assert=require('node:assert/strict');
const {chromium}=require(process.env.PLAYWRIGHT_PATH||'playwright');
(async()=>{
 const browser=await chromium.launch({headless:true,...(process.env.CHROME_PATH?{executablePath:process.env.CHROME_PATH}:{})});
 try{
  const page=await browser.newPage({viewport:{width:1440,height:900}}); const errors=[];page.on('pageerror',e=>errors.push(e.message));
  let empty=false,fail=false;
  const feature=(i,level,hazard)=>({type:'Feature',properties:{code:'TH100'+i,name_th:'พื้นที่ '+i,province_th:'จังหวัดทดสอบ',level,worst_hazard:hazard,hazards_at_risk:1},geometry:{type:'Polygon',coordinates:[[[100+i*.1,14],[100.05+i*.1,14],[100.05+i*.1,14.05],[100+i*.1,14.05],[100+i*.1,14]]]}});
  const features=[feature(1,'CRITICAL','fire'),feature(2,'WARNING','flood'),feature(3,'WARNING','rain_storm'),feature(4,'WATCH','heat'),feature(5,'NORMAL',null)];
  await page.route('**/api/v1/map/layers/overview',r=>fail?r.fulfill({status:503,json:{detail:'unavailable'}}):r.fulfill({json:{type:'FeatureCollection',features:empty?features.filter(f=>f.properties.level==='NORMAL'):features,meta:{data_updated_at:'2026-11-03T00:00:00Z',degraded_services:[]}}}));
  await page.route('**/api/v1/areas/TH1002/hazards',r=>r.fulfill({json:{area:{code:'TH1002',name_th:'พื้นที่ 2',level:'amphoe',parents:['จังหวัดทดสอบ']},assessments:[{hazard:'flood',level:'WARNING',observations:{above_bank_m:1.2},notices:[],sources:['แหล่งทดสอบ']}],forecast:[],dams:[],rivers:[],official_warnings:[],degraded_services:[]}}));
  await page.goto(process.env.THANPHAI_VERIFY_URL||'http://localhost:3000');
  await page.waitForFunction(()=>document.querySelector('.header-snapshot strong')?.textContent==='5');
  await page.locator('[data-category=water]').click();
  await page.getByRole('button',{name:'เขื่อน',exact:true}).click();
  for(const [label,n] of [['วิกฤต',1],['เตือนภัย',2],['เฝ้าระวัง',1]]){
   await page.getByRole('button',{name:'ดูพื้นที่ระดับ'+label,exact:true}).click();
   assert.equal(await page.locator('[data-category=overview]').getAttribute('aria-pressed'),'true');
   const panel=page.getByRole('region',{name:label+'ล่าสุด',exact:true});
   await panel.waitFor();
   assert.equal(await panel.locator('.risk-alert-row').count(),n);
   await page.getByRole('region',{name:'แผนที่พื้นที่ระดับ'+label,exact:true}).waitFor();
   assert.equal(await page.getByRole('button',{name:'ดูพื้นที่ระดับ'+label,exact:true}).getAttribute('aria-pressed'),'true');
   assert.deepEqual(await page.locator('.header-snapshot strong').allTextContents(),['5','1','2','1'],'National counts stay unchanged by filter');
   if(label==='เตือนภัย'){
    await panel.getByRole('button',{name:/น้ำท่วม.*พื้นที่ 2/}).click();
    await panel.getByText(/แหล่งทดสอบ/).waitFor();
    assert.match(await panel.innerText(),/จังหวัดทดสอบ/);
    if(process.env.SCREENSHOT_PREFIX)await page.screenshot({path:process.env.SCREENSHOT_PREFIX+'-desktop.png'});
   }
  }
  await page.getByRole('button',{name:'ดูภาพรวมทุกระดับ',exact:true}).click();
  assert.equal(await page.locator('.risk-alert-panel').count(),0);
  await page.getByRole('button',{name:'ดูพื้นที่ระดับเตือนภัย',exact:true}).click();
  await page.locator('[data-category=hot]').click();
  assert.equal(await page.locator('.risk-alert-panel').count(),0,'Navigation exits level filter');
  await page.getByRole('button',{name:'ดูพื้นที่ระดับเตือนภัย',exact:true}).click();
  const card=page.locator('.header-snapshot article').nth(2);
  await page.mouse.move(10,10);await page.waitForTimeout(500);
  const before=await card.boundingBox();await card.hover();await page.waitForTimeout(500);const hovered=await card.boundingBox();
  assert(hovered.y<before.y-1 && hovered.width>before.width*1.04,'Whole card lifts and enlarges on hover');
  await page.locator('.header-snapshot article').nth(1).hover();await page.waitForTimeout(500);const after=await card.boundingBox();assert(Math.abs(after.y-before.y)<1 && Math.abs(after.width-before.width)<1,'Previous card restores when pointer moves to another card');
  await page.mouse.move(10,10);
  await page.emulateMedia({reducedMotion:'reduce'});
  await card.hover({position:{x:5,y:5}});
  assert((await card.boundingBox()).width>before.width*1.1,'Reduced motion still gives visible enlargement');
  await page.locator('.header-snapshot article').nth(1).hover();
  assert(Math.abs((await card.boundingBox()).width-before.width)<1,'Reduced motion restores previous card');
  await page.emulateMedia({reducedMotion:'no-preference'});
  await page.mouse.move(10,10);
  for(const width of [375,320]){
   await page.setViewportSize({width,height:667});
   assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth&&document.documentElement.scrollHeight<=innerHeight+1));
   await page.getByRole('button',{name:'ดูพื้นที่ระดับเฝ้าระวัง',exact:true}).click();
   await page.getByRole('region',{name:'เฝ้าระวังล่าสุด',exact:true}).waitFor();
   if(width===375&&process.env.SCREENSHOT_PREFIX)await page.screenshot({path:process.env.SCREENSHOT_PREFIX+'-mobile.png'});
  }
  empty=true;await page.getByRole('button',{name:'โหลดข้อมูลแผนที่ใหม่',exact:true}).click();
  await page.getByText('ยังไม่มีอำเภอระดับเฝ้าระวังในข้อมูลชุดล่าสุด',{exact:true}).waitFor();
  fail=true;await page.getByRole('button',{name:'โหลดข้อมูลแผนที่ใหม่',exact:true}).click();
  await page.getByRole('region',{name:'เฝ้าระวังล่าสุด',exact:true}).getByRole('alert').waitFor();
  assert.deepEqual(errors,[]);
  const hybrid=await browser.newContext({hasTouch:true,viewport:{width:1440,height:900}});
  const hybridPage=await hybrid.newPage();
  await hybridPage.route('**/api/v1/map/layers/overview',r=>r.fulfill({json:{type:'FeatureCollection',features,meta:{data_updated_at:'2026-11-03T00:00:00Z',degraded_services:[]}}}));
  await hybridPage.goto(process.env.THANPHAI_VERIFY_URL||'http://localhost:3000');
  const hybridCard=hybridPage.locator('.header-snapshot article').nth(1);
  await hybridCard.waitFor();const hybridBefore=await hybridCard.boundingBox();
  await hybridCard.hover();await hybridPage.waitForTimeout(520);
  assert((await hybridCard.boundingBox()).width>hybridBefore.width*1.1,'Mouse hover works even when primary pointer is touch');
  await hybrid.close();
  console.log('PASS: three national severity filters, overview navigation, current counts, alert types/locations, details, reset, hover, mobile, empty and error states.');
 }finally{await browser.close();}
})().catch(e=>{console.error(e);process.exitCode=1;});
