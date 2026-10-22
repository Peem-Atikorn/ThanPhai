const fs = require('node:fs');
const path = require('node:path');
const assert = require('node:assert/strict');
const ts = require('typescript');
const root = path.resolve(__dirname, '..');

// Execute the actual components and event handlers with deterministic hooks/API fixtures.
// This checks state transitions; it is not browser/layout verification.
let current;
const hooks = {
  useState(initial) { const i = current.cursor++; const host = current; if (!(i in host.state)) host.state[i] = typeof initial === 'function' ? initial() : initial; return [host.state[i], value => { host.state[i] = typeof value === 'function' ? value(host.state[i]) : value; }]; },
  useRef(initial) { const i = current.cursor++; return current.state[i] ??= { current: initial }; },
  useMemo(fn) { current.cursor++; return fn(); },
  useCallback(fn) { current.cursor++; return fn; },
  useEffect() { current.cursor++; },
};
const jsx = (type, props) => ({ type, props: props || {} });
const cache = {};
const observed = { search: 0 };
function load(id) {
  if (id === 'react') return hooks;
  if (id === 'react/jsx-runtime') return { jsx, jsxs: jsx, Fragment: 'fragment' };
  if (id === 'react-dom') return { createPortal: node => node };
  if (id === 'next/dynamic') return { __esModule: true, default: () => () => null };
  if (cache[id]) return cache[id].exports;
  let file = path.join(root, 'src', id.slice(2));
  file += fs.existsSync(file + '.tsx') ? '.tsx' : '.ts';
  const js = ts.transpileModule(fs.readFileSync(file, 'utf8'), { fileName: file, compilerOptions: { jsx: ts.JsxEmit.ReactJSX, module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2020, esModuleInterop: true } }).outputText;
  const mod = cache[id] = { exports: {} };
  new Function('module', 'exports', 'require', js)(mod, mod.exports, load);
  return mod.exports;
}
function instance(Component) {
  const state = [];
  return { state, render(props = {}) { current = { state, cursor: 0 }; this.tree = Component(props); return this.tree; } };
}
function nodes(node, result = []) {
  if (Array.isArray(node)) { node.forEach(n => nodes(n, result)); return result; }
  if (!node || typeof node !== 'object') return result;
  result.push(node); nodes(node.props?.children, result); return result;
}
function content(node) { if (Array.isArray(node)) return node.map(content).join(''); if (typeof node === 'string' || typeof node === 'number') return String(node); return node && typeof node === 'object' ? content(node.props?.children) : ''; }
const tick = () => new Promise(resolve => setImmediate(resolve));
global.requestAnimationFrame = fn => fn();
global.window = { matchMedia: () => ({ matches: true }) };
global.document = { getElementById: () => ({ focus() {}, scrollIntoView() {} }) };

(async () => {
  const lib = load('@/lib/api');
  lib.api.search = async () => { observed.search++; return [{ code: '13', level: 'province', name_th: 'ปทุมธานี', parents: [], lat: 14, lon: 100 }]; };
  lib.api.area = async () => ({ area: { code: '13', level: 'province', name_th: 'ปทุมธานี', parents: [] }, assessments: [], forecast: [], rivers: [{ status: 'NORMAL' }], river_province: { code: '13', name_th: 'ปทุมธานี' }, official_warnings: [], degraded_services: [] });
  lib.api.boundary = async () => null;
  const Dashboard = load('@/components/Dashboard').default;
  let opened = 0;
  const app = instance(Dashboard);
  let props = { activeView: 'hazard', searchInHeader: true, searchTarget: {}, onOpenAreaSearch: () => opened++, riverViewRequest: 0 };
  let tree = app.render(props);
  const starter = nodes(tree).find(n => n.type === 'button' && content(n).startsWith('ค้นหาพื้นที่'));
  assert(starter, 'empty-area search button remains available in header-search mode');
  starter.props.onClick();
  assert.equal(opened, 1, 'empty-area search opens the same dialog');

  // User searches/selects a province, then opens the province station list.
  nodes(tree).find(n => n.type === 'input' && n.props.id === 'area-search').props.onChange({ target: { value: 'ปทุมธานี' } });
  tree = app.render(props);
  await nodes(tree).find(n => n.type === 'form' && n.props.role === 'search').props.onSubmit({ preventDefault() {} });
  tree = app.render(props);
  nodes(tree).find(n => n.type === 'button' && content(n).includes('ปทุมธานี')).props.onClick();
  await tick();
  tree = app.render(props);
  const callout = nodes(tree).find(n => n.type === 'button' && /river-callout/.test(n.props.className || ''));
  assert(callout, 'province river callout available');
  callout.props.onClick();
  props = { ...props, activeView: 'rivers' };
  tree = app.render(props);
  assert(nodes(tree).some(n => n.props.className === 'filter-chip'), 'province shortcut keeps its province filter');
  props = { ...props, activeView: 'hazard' }; app.render(props);
  props = { ...props, activeView: 'rivers', riverViewRequest: 1 };
  tree = app.render(props);
  assert(!nodes(tree).some(n => n.props.className === 'filter-chip'), 'top river category clears stale province filter');
  props = { ...props, activeView: 'hazard' }; tree = app.render(props);
  nodes(tree).find(n => n.type === 'button' && /river-callout/.test(n.props.className || '')).props.onClick();
  props = { ...props, activeView: 'rivers' }; tree = app.render(props);
  assert(nodes(tree).some(n => n.props.className === 'filter-chip'), 'a new province shortcut still works after reset');
  props = { ...props, riverViewRequest: 2 }; tree = app.render(props);
  assert(!nodes(tree).some(n => n.props.className === 'filter-chip'), 'reselecting rivers also clears the province filter');

  const landing = instance(load('@/components/LandingExperience').default);
  tree = landing.render();
  const headerSearch = nodes(tree).find(n => n.type === 'button' && n.props.className === 'journey-search-icon');
  assert.equal(headerSearch, undefined, 'header search entry removed');
  const riverButton = nodes(tree).find(n => n.props['data-category'] === 'rivers');
  riverButton.props.onClick(); tree = landing.render();
  assert.equal(nodes(tree).find(n => n.type === Dashboard).props.riverViewRequest, 1, 'top button emits explicit river reset request');
  const source = fs.readFileSync(path.join(root, 'src/components/Dashboard.tsx'), 'utf8');
  for (const unused of ['initialQuery', 'initialHazard', 'initialView']) assert(!source.includes(unused), 'unused prop removed: ' + unused);
  assert(!content(tree).includes('ผลการค้นหาในแดชบอร์ด'), 'guide describes current dialog flow');
  console.log('PASS: dialog entry, province river reset (including reselect), header search entry removed, guide, unused props; actual component handlers with mocked hooks/API.');
})().catch(error => { console.error(error); process.exit(1); });
