const assert = require('node:assert/strict');
const { chromium } = require(process.env.PLAYWRIGHT_PATH || 'playwright');
(async () => {
  const browser = await chromium.launch({headless:true,...(process.env.CHROME_PATH ? {executablePath:process.env.CHROME_PATH} : {})});
  try {
    const page=await browser.newPage({viewport:{width:1440,height:900}});
    const errors=[]; page.on('pageerror',e=>errors.push(e.message));
    const place={code:'TH130303',level:'tambon',name_th:'รังสิต',parents:['ธัญบุรี','ปทุมธานี'],lat:14.02,lon:100.73};
    const dam={id:'d1',name_th:'เขื่อนในจังหวัด',province_code:'TH13',province_th:'ปทุมธานี',storage_percent:90,status:'HIGH',status_th:'น้ำใกล้เต็มอ่าง',lat:14,lon:100.7};
    const river={id:'r1',code:'R.1',name_th:'สถานีในจังหวัด',province_code:'TH13',province_th:'ปทุมธานี',status:'NORMAL',status_th:'น้ำต่ำกว่าตลิ่ง',lat:14,lon:100.7,chain_order:null};
    const station={id:'q1',name_th:'คุณภาพน้ำในจังหวัด',province_code:'TH13',province_th:'ปทุมธานี',lat:14,lon:100.7,exceeds:{}};
    let localQuality=true;
    let releaseArea;
    const areaReady=new Promise(resolve=>{releaseArea=resolve;});
    await page.route('**/api/v1/areas/search?*',r=>r.fulfill({json:[place]}));
    await page.route('**/api/v1/areas/TH130303/hazards',async r=>{await areaReady; return r.fulfill({json:{area:place,
      assessments:[['heat','WATCH'],['flood','WARNING'],['air_quality','CRITICAL'],['drought','NORMAL']].map(([hazard,level])=>({hazard,level,observations:{},notices:[],sources:['ทดสอบ']})),
      official_warnings:[{title:'ประกาศทดสอบฝน',hazards:['rain_storm']}],forecast:[],rivers:[],dams:[],degraded_services:[]}});});
    await page.route('**/api/v1/areas/TH130303/boundary',r=>r.fulfill({json:{type:'Feature',geometry:{type:'Polygon',coordinates:[[[100.7,14],[100.8,14],[100.8,14.1],[100.7,14.1],[100.7,14]]]},properties:{},bbox:[100.7,14,100.8,14.1]}}));
    await page.route('**/api/v1/dams',r=>r.fulfill({json:{dams:[dam,{...dam,id:'d2',name_th:'เขื่อนต่างจังหวัด',province_code:'TH21'}]}}));
    await page.route('**/api/v1/rivers',r=>r.fulfill({json:{rivers:[river,{...river,id:'r2',code:'R.2',name_th:'สถานีต่างจังหวัด',province_code:'TH21',chain_order:1}]}}));
    await page.route('**/api/v1/water-quality',r=>r.fulfill({json:{stations:[...(localQuality ? [station] : []),{...station,id:'q2',name_th:'คุณภาพน้ำต่างจังหวัด',province_code:'TH21'}],source:'ทดสอบ',degraded_services:[]}}));
    await page.goto(process.env.THANPHAI_VERIFY_URL || 'http://localhost:3000');
    await page.locator('[data-category=water]').click();
    await page.getByRole('button',{name:'เขื่อน',exact:true}).click();
    await page.getByRole('button',{name:/เขื่อนต่างจังหวัด/}).waitFor();
    await page.getByRole('button',{name:'พื้นที่ที่คุณอยากรู้',exact:true}).click();
    await page.getByRole('combobox',{name:'ค้นหาพื้นที่',exact:true}).fill('รังสิต');
    await page.getByRole('search').evaluate(el=>el.requestSubmit());
    await page.getByRole('option',{name:/รังสิต.*ธัญบุรี/}).click();
    assert.equal(await page.locator('[data-category=overview]').getAttribute('aria-pressed'),'true','Search opens alert overview');
    await page.locator('[data-category=water]').click();
    const pendingPanel=page.getByRole('region',{name:'ข้อมูลเขื่อน',exact:true});
    assert.match(await pendingPanel.innerText(),/รังสิต/,'Scope exists while area details are loading');
    assert.doesNotMatch(await pendingPanel.innerText(),/ต่างจังหวัด/,'Pending area never falls back to national data');
    releaseArea();
    await page.locator('[data-category=overview]').click();
    const area=page.getByRole('region',{name:'รายละเอียดพื้นที่',exact:true});
    await area.locator('.assessment-list li').first().waitFor();
    assert.deepEqual(await area.locator('.assessment-heading .risk-badge').allTextContents(),['วิกฤต','เตือนภัย','เฝ้าระวัง','ปกติ'],'Search starts with all risks in priority order');
    assert(await area.locator('.official-warning').isVisible(),'Official notice comes before assessments');
    assert(await area.evaluate(el=>!!(el.querySelector('.official-warning').compareDocumentPosition(el.querySelector('.assessment-list')) & Node.DOCUMENT_POSITION_FOLLOWING)));
    await page.locator('[data-category=water]').click();
    await page.getByRole('button',{name:'น้ำท่วม',exact:true}).click();
    assert.equal(await area.locator('.assessment-list li').count(),1,'Selected hazard only');
    assert.equal(await area.locator('.official-warning').count(),0,'No unrelated weather alert');
    for(const [tab,region,name] of [['เขื่อน','ข้อมูลเขื่อน','เขื่อนในจังหวัด'],['แม่น้ำ','ข้อมูลแม่น้ำ','สถานีในจังหวัด'],['คุณภาพน้ำ','ข้อมูลคุณภาพน้ำ','คุณภาพน้ำในจังหวัด']]) {
      await page.locator('[data-category=water]').click();
      await page.getByRole('button',{name:tab,exact:true}).click();
      const panel=page.getByRole('region',{name:region,exact:true});
      await panel.locator('.dam-list button').filter({hasText:name}).waitFor();
      assert.match(await panel.innerText(),/รังสิต/,'Selected location stays visible');
      assert.doesNotMatch(await panel.innerText(),/ต่างจังหวัด/,'Only province data');
      assert.equal(await panel.locator('.official-warning,.assessment-list').count(),0,'Only selected topic');
    }
    localQuality=false;
    await page.getByRole('button',{name:'โหลดข้อมูลแผนที่ใหม่',exact:true}).click();
    const qualityPanel=page.getByRole('region',{name:'ข้อมูลคุณภาพน้ำ',exact:true});
    await qualityPanel.getByText('ยังไม่มีข้อมูลคุณภาพน้ำล่าสุด',{exact:true}).waitFor();
    assert.match(await qualityPanel.innerText(),/รังสิต/,'Empty province keeps location');
    assert.doesNotMatch(await qualityPanel.innerText(),/ต่างจังหวัด/,'Empty province does not fall back to national data');
    await page.locator('[data-category=weather]').click();
    await area.locator('.official-warning').waitFor();
    assert.equal(await area.locator('.assessment-list li').count(),0,'Weather topic does not show unrelated assessments');
    await page.locator('[data-category=hot]').click();
    assert.match(await area.innerText(),/รังสิต/);
    assert.equal(await area.locator('.assessment-list li').count(),1);
    await page.locator('[data-category=water]').click();
    await page.getByRole('button',{name:'เขื่อน',exact:true}).click();
    await page.getByRole('button',{name:'ดูทั่วประเทศ',exact:true}).click();
    await page.getByRole('button',{name:/เขื่อนต่างจังหวัด/}).waitFor();
    assert.equal(await page.locator('.journey-footer .disclaimer').count(),1,'Disclaimer moved to footer');
    assert.deepEqual(errors,[]);
    console.log('PASS: searched area persists across topics, scoped stations, relevant assessments and notices, risk order, explicit reset, footer disclaimer.');
  } finally {await browser.close();}
})().catch(e=>{console.error(e);process.exitCode=1;});
