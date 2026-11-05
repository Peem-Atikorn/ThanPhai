const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const ts = require('typescript');
const root = path.resolve(__dirname, '..');
const slots = [];
let cursor = 0, dirty = false, effects = [], captured = false;
const storage = new Map();
const listeners = new Map();
global.localStorage = { getItem: key => storage.get(key) ?? null, setItem: (key, value) => storage.set(key, value) };
global.window = { innerWidth: 1280, innerHeight: 800, matchMedia: () => ({ matches: true }), requestAnimationFrame: () => 1, cancelAnimationFrame() {}, setTimeout: () => 1, clearTimeout() {}, addEventListener: (name, fn) => listeners.set('window:' + name, fn), removeEventListener() {} };
global.document = { body: {}, addEventListener: (name, fn) => listeners.set(name, fn), removeEventListener: name => listeners.delete(name) };
global.ResizeObserver = class { observe() {} disconnect() {} };
global.MutationObserver = class { observe() {} disconnect() {} };
const react = {
  useState(initial) { const slot = cursor++; if (!(slot in slots)) slots[slot] = { value: initial }; return [slots[slot].value, next => { const value = typeof next === 'function' ? next(slots[slot].value) : next; if (!Object.is(value, slots[slot].value)) { slots[slot].value = value; dirty = true; } }]; },
  useRef(value) { const slot = cursor++; return (slots[slot] ??= { current: value }); },
  useEffect(fn, deps) { const slot = cursor++; const old = slots[slot]; if (!old || deps.some((value, i) => !Object.is(value, old.deps[i]))) { slots[slot] = { deps }; effects.push(() => { old?.cleanup?.(); slots[slot].cleanup = fn(); }); } },
};
const jsx = (type, props) => ({ type, props: props ?? {} });
const cache = {};
function load(id) {
  if (id === 'react') return react;
  if (id === 'react/jsx-runtime') return { jsx, jsxs: jsx, Fragment: 'fragment' };
  if (id === 'react-dom') return { createPortal: node => node };
  if (id === '@/components/Icon') return { default: () => null };
  if (id === '@/lib/i18n') return { t: text => text };
  if (cache[id]) return cache[id].exports;
  const file = path.join(root, 'src', id.slice(2)) + (id.includes('components/') ? '.tsx' : '.ts');
  const code = ts.transpileModule(fs.readFileSync(file, 'utf8'), { fileName: file, compilerOptions: { jsx: ts.JsxEmit.ReactJSX, module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2020, esModuleInterop: true } }).outputText;
  const result = cache[id] = { exports: {} };
  new Function('module', 'exports', 'require', code)(result, result.exports, load);
  return result.exports;
}
function all(node) { return !node || typeof node !== 'object' ? [] : [node, ...[node.props?.children].flat(Infinity).flatMap(all)]; }
const FloatingMascot = load('@/components/FloatingMascot').default;
const fakeButton = { focus() {}, setPointerCapture() { captured = true; }, hasPointerCapture: () => captured, releasePointerCapture() { captured = false; } };
const fakePanel = { scrollHeight: 340, querySelector: selector => selector === '.chat-log' ? { scrollHeight: 240, clientHeight: 240 } : ({ focus() {} }) };
const fakeHome = { getBoundingClientRect: () => ({ left: 980, top: 550, width: 280, height: 220 }) };
const props = { topic: 'flood', status: 'idle', children: jsx('form', { 'data-preserved': 'existing-chat' }) };
let tree;
const rootTypes = [];
function render() {
  for (let i = 0; i < 8; i++) {
    dirty = false; cursor = 0; tree = FloatingMascot(props); rootTypes.push(tree.type);
    for (const element of all(tree)) if (element.props.ref) element.props.ref.current = element.props.className === 'mascot-character' ? fakeButton : element.props.className === 'mascot-home' ? fakeHome : fakePanel;
    const pending = effects; effects = []; pending.forEach(effect => effect());
    if (!dirty) return;
  }
  throw new Error('Render did not settle');
}
const character = () => all(tree).find(node => node.props.className === 'mascot-character');
const dialog = () => all(tree).find(node => node.props.id === 'thanphai-mascot-chat');
const event = (x, y, type = 'pointerup') => ({ clientX: x, clientY: y, pointerId: 1, isPrimary: true, button: 0, currentTarget: fakeButton, type });
storage.set('thanphai-mascot-position-v1', JSON.stringify({ x: 900, y: 500 }));
render();
assert.ok(rootTypes.every(type => type === rootTypes[0]), 'The home keeps the same React parent through hydration so its ResizeObserver remains attached');
assert.ok(all(tree).some(node => node.props.className === 'mascot-home'), 'Every topic reserves a framed home separate from the data list');
assert.equal(character().props.style.left, 1060, 'The mascot starts in the center of its right-sidebar home');
assert.equal(character().props.style.top, 594, 'The mascot stands inside its home, above the lower border');
assert.equal(dialog().props.hidden, true);
character().props.onClick(); render();
assert.equal(dialog().props.hidden, false, 'A click opens chat');
listeners.get('keydown')({ key: 'Escape' }); render();
assert.equal(dialog().props.hidden, true, 'Escape closes chat');
character().props.onPointerDown(event(1100, 600, 'pointerdown'));
character().props.onPointerMove(event(900, 400, 'pointermove')); render();
character().props.onPointerUp(event(900, 400)); render();
character().props.onClick(); render();
assert.equal(dialog().props.hidden, true, 'Finishing a drag must not open chat');
assert.ok(character().props.style.top < 594, 'Dragging still moves the mascot within this visit');
character().props.onPointerDown(event(900, 400, 'pointerdown'));
character().props.onPointerMove(event(800, 300, 'pointermove')); render();
character().props.onPointerCancel(event(800, 300, 'pointercancel')); render();
character().props.onKeyDown({ key: 'Enter', preventDefault() {} });
character().props.onClick(); render();
assert.equal(dialog().props.hidden, false, 'Keyboard activation still works after a cancelled drag');
props.topic = 'fire'; props.status = 'waiting'; render();
assert.equal(dialog().props.hidden, false, 'Topic changes keep the conversation open');
assert.ok(all(tree).some(node => node === props.children), 'The existing chat form is preserved');
slots.length = 0; listeners.clear(); props.topic = 'overview'; props.status = 'idle'; render();
assert.equal(character().props.style.left, 1060, 'Reopening the page returns the mascot to its framed home');
assert.equal(character().props.style.top, 594);
console.log('Mascot interaction checks passed: framed sidebar home, drag, Escape, cancellation, keyboard activation and topic changes');
