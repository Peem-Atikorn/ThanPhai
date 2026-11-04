const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const Module = require('node:module');
const ts = require('typescript');
const filename = path.resolve(__dirname, '../src/lib/mascot.ts');
assert.ok(fs.existsSync(filename), 'The draggable mascot geometry and topic registry must exist');
const compiled = ts.transpileModule(fs.readFileSync(filename, 'utf8'), {
  compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2020 },
}).outputText;
const mod = new Module(filename, module);
mod._compile(compiled, filename);
const { MASCOT_TOPICS, MASCOT_SIZE, clampMascotPosition, placeMascotChat, readMascotViewport } = mod.exports;
assert.equal(typeof readMascotViewport, 'function', 'Mobile keyboard bounds must use the visual viewport');
const keyboardBounds = readMascotViewport({ innerWidth: 375, innerHeight: 812, visualViewport: { width: 375, height: 390, offsetLeft: 0, offsetTop: 30 } });
assert.equal(keyboardBounds.height, 390);
assert.equal(keyboardBounds.offsetTop, 30);
const keyboardPanel = placeMascotChat({ x: 100, y: 8 }, keyboardBounds, 360);
assert.ok(keyboardPanel.top + keyboardPanel.maxHeight <= 382, 'Chat stays within visible height above phone keyboard');
const topics = ['overview', 'flood', 'rain_storm', 'fire', 'drought', 'air_quality', 'heat', 'earthquake', 'marine', 'tropical_cyclone', 'landslide', 'dams', 'rivers', 'water-quality'];
assert.deepEqual(Object.keys(MASCOT_TOPICS).sort(), topics.sort());
assert.equal(new Set(Object.values(MASCOT_TOPICS).map(topic => topic.src)).size, 14, 'Every topic has distinct artwork');
for (const viewport of [{ width: 1440, height: 900 }, { width: 375, height: 667 }, { width: 320, height: 360 }]) {
  for (const saved of [null, {}, { x: NaN, y: Infinity }, { x: -500, y: 99999 }, { x: '50', y: null }]) {
    const p = clampMascotPosition(saved, viewport);
    assert.ok(Number.isFinite(p.x) && Number.isFinite(p.y), 'Malformed storage never produces invalid coordinates');
    assert.ok(p.x >= 8 && p.x + MASCOT_SIZE.width <= viewport.width - 8);
    assert.ok(p.y >= 8 && p.y + MASCOT_SIZE.height <= viewport.height - 8);
    const panel = placeMascotChat(p, viewport, 360);
    assert.ok(panel.left >= 8 && panel.top >= 8);
    assert.ok(panel.left + panel.width <= viewport.width - 8);
    assert.ok(panel.top + panel.maxHeight <= viewport.height - 8);
  }
}
const left = placeMascotChat({ x: 8, y: 200 }, { width: 1440, height: 900 }, 360);
assert.equal(left.width, 420, 'Desktop chat uses the wider conversation layout');
assert.equal(placeMascotChat({ x: 8, y: 200 }, { width: 1440, height: 900 }, 2000).maxHeight, 765, 'Long answers scroll within 85% of the viewport');
assert.ok(left.left >= 8 + MASCOT_SIZE.width, 'Chat opens to the right when the character is at the left');
const right = placeMascotChat({ x: 1280, y: 200 }, { width: 1440, height: 900 }, 360);
assert.ok(right.left + right.width <= 1280, 'Chat opens to the left when the character is at the right');
if (process.argv.includes('--assets')) {
  for (const topic of Object.values(MASCOT_TOPICS)) {
    assert.ok(fs.existsSync(path.resolve(__dirname, '../public' + topic.src)), 'Missing sprite: ' + topic.src);
  }
}
console.log('Mascot checks passed: 14 distinct topics, malformed storage, viewport bounds, adaptive chat placement' + (process.argv.includes('--assets') ? ', all sprites present' : ''));
