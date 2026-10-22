/* Node 22+; run against the real local app. No npm dependencies or mocked data. */
const fs = require('node:fs');
const path = require('node:path');
const os = require('node:os');
const { spawn } = require('node:child_process');
const assert = require('node:assert/strict');

const appUrl = process.argv[2] || 'http://localhost:3000';
const output = path.resolve(__dirname, '../docs/pr-screenshots');
const candidates = [process.env.CHROME_PATH,
  'C:/Program Files/Google/Chrome/Application/chrome.exe',
  'C:/Program Files (x86)/Google/Chrome/Application/chrome.exe',
  'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe',
  '/usr/bin/google-chrome', '/usr/bin/chromium',
].filter(Boolean);
const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));
let browser, socket;

(async () => {
  assert(typeof WebSocket !== 'undefined', 'Use Node.js 22 or newer.');
  const url = new URL(appUrl);
  assert(['http:', 'https:'].includes(url.protocol), 'Expected an HTTP(S) app URL.');
  const health = await fetch(url, { signal: AbortSignal.timeout(10000) });
  assert(health.ok, 'App is unavailable: ' + health.status);
  const executable = candidates.find(file => fs.existsSync(file));
  assert(executable, 'Chrome/Edge not found; set CHROME_PATH to its executable.');
  const profile = fs.mkdtempSync(path.join(os.tmpdir(), 'thanphai-pr-'));
  browser = spawn(executable, ['--headless=new', '--disable-gpu', '--no-first-run', '--no-default-browser-check', '--remote-debugging-address=127.0.0.1', '--remote-debugging-port=0', '--user-data-dir=' + profile, 'about:blank'], { windowsHide: true, stdio: 'ignore' });
  browser.on('error', error => { console.error(error.message); });
  let address;
  for (let i = 0; i < 60; i++) {
    const file = path.join(profile, 'DevToolsActivePort');
    if (fs.existsSync(file)) { const [port, endpoint] = fs.readFileSync(file, 'utf8').trim().split(/\r?\n/); if (endpoint) { address = `ws://127.0.0.1:${port}${endpoint}`; break; } }
    if (browser.exitCode != null) throw Error('Browser exited: ' + browser.exitCode);
    await sleep(250);
  }
  assert(address, 'Chrome did not start within 15 seconds.');
  socket = new WebSocket(address);
  const pending = new Map(); let id = 0;
  socket.addEventListener('message', event => {
    const message = JSON.parse(String(event.data));
    const call = pending.get(message.id);
    if (call) { pending.delete(message.id); clearTimeout(call.timer); message.error ? call.reject(Error(message.error.message)) : call.resolve(message.result); }
  });
  await new Promise((resolve, reject) => { socket.addEventListener('open', resolve, {once:true}); socket.addEventListener('error', reject, {once:true}); });
  function send(method, params = {}, sessionId) {
    return new Promise((resolve, reject) => {
      const requestId = ++id;
      const timer = setTimeout(() => { pending.delete(requestId); reject(Error('CDP timeout: ' + method)); }, 20000);
      pending.set(requestId, {resolve,reject,timer}); socket.send(JSON.stringify({id:requestId,method,params,...(sessionId?{sessionId}:{})}));
    });
  }
  const target = await send('Target.createTarget', { url: 'about:blank' });
  const { sessionId } = await send('Target.attachToTarget', { targetId: target.targetId, flatten: true });
  const page = (method, params) => send(method, params, sessionId);
  async function evaluate(expression) {
    const result = await page('Runtime.evaluate', {expression,returnByValue:true,awaitPromise:true});
    if (result.exceptionDetails) throw Error(result.exceptionDetails.exception?.description || result.exceptionDetails.text);
    return result.result.value;
  }
  async function until(expression) {
    for (let i=0;i<80;i++) { if (await evaluate(expression)) return; await sleep(250); }
    throw Error('Timed out waiting for ' + expression);
  }
  await page('Page.enable');
  await page('Runtime.enable');
  await page('Emulation.setDeviceMetricsOverride', {width:1440,height:1100,deviceScaleFactor:1,mobile:false});
  await page('Emulation.setEmulatedMedia', {features:[{name:'prefers-reduced-motion',value:'reduce'}]});
  await page('Page.navigate', {url: url.href});
  await until('document.readyState === "complete" && !!document.querySelector(".journey-category-bar")');
  await evaluate('document.fonts.ready.then(() => true)');
  await sleep(2000);
  const starter = await evaluate(`(() => { const panel=document.querySelector('.empty-area'); const button=[...(panel?.querySelectorAll('button')||[])].find(b=>b.textContent.includes('ค้นหาพื้นที่')); if(!button)return false; button.click();return true; })()`);
  assert(starter, 'Missing empty-state search entry.');
  await until('document.querySelector(".journey-search-dialog").open');
  await evaluate('document.querySelector(".journey-search-dialog").close()');
  assert(await evaluate('!document.querySelector(".journey-search-icon").hasAttribute("aria-label")'), 'Unexpected header-search aria-label override.');
  fs.mkdirSync(output, {recursive:true});
  const captured = [];
  for (const category of ['flood','rain_storm','fire','drought','dams','rivers']) {
    await evaluate(`document.querySelector('[data-category="${category}"]').click()`);
    await until(`document.querySelector('.journey-live-shell').dataset.activeCategory === '${category}'`);
    await evaluate('Promise.all([...document.images].filter(i=>!i.complete).map(i=>new Promise(r=>{i.onload=r;i.onerror=r;setTimeout(r,3000)}))).then(()=>true)');
    await sleep(800);
    await evaluate('window.scrollTo(0,0)');
    assert(await evaluate('document.documentElement.scrollWidth <= innerWidth'), 'Horizontal overflow: ' + category);
    const background = await evaluate('getComputedStyle(document.querySelector(".map-panel")).backgroundColor');
    assert.equal(background, 'rgb(255, 255, 255)', 'Panel content must be opaque white: ' + category);
    const {cssContentSize} = await page('Page.getLayoutMetrics');
    const {data} = await page('Page.captureScreenshot', {format:'png',captureBeyondViewport:true,clip:{x:0,y:0,width:1440,height:Math.min(6000,Math.ceil(cssContentSize.height)),scale:1}});
    fs.writeFileSync(path.join(output, category+'.png'), Buffer.from(data,'base64'));
    captured.push(category); console.log('Captured ' + category + '.png');
  }
  for (const width of [1024,768,390,320]) {
    await page('Emulation.setDeviceMetricsOverride', {width,height:900,deviceScaleFactor:1,mobile:false});
    await sleep(150);
    assert(await evaluate('document.documentElement.scrollWidth <= innerWidth'), 'Horizontal overflow at ' + width);
  }
  const {data} = await page('Page.captureScreenshot', {format:'png',captureBeyondViewport:false});
  fs.writeFileSync(path.join(output,'rivers-mobile.png'),Buffer.from(data,'base64'));
  const sections = captured.map(c=>`### ${c}\n\n![${c}](${c}.png)`).join('\n\n');
  fs.writeFileSync(path.join(output,'README.md'),`# PR screenshots\n\nCaptured from ${url.href} at ${new Date().toISOString()}. Actual running app and data; no API/map stubs. Desktop viewport: 1440 × 1100, full-page capture up to 6000 px high.\n\n${sections}\n\n### Mobile\n\n![Mobile](rivers-mobile.png)\n`);
  console.log('PASS: six categories, white panel bodies, both search entries, and viewport overflow checks.');
  console.log('Screenshots saved to: ' + output);
  await send('Browser.close').catch(()=>{});
})().catch(error => { console.error(error.message); process.exitCode=1; }).finally(() => { socket?.close(); if (browser && browser.exitCode == null) browser.kill(); });
