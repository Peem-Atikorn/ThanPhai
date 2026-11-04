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
  useEffect(fn, deps) {
    const i = current.cursor++;
    if (!current.trackEffects) { current.effects?.push(fn); return; }
    const previous = current.effectDeps[i];
    if (previous && deps?.length === previous.length && deps.every((value, index) => value === previous[index])) return;
    current.effectCleanups[i]?.();
    current.effectDeps[i] = deps;
    current.effectCleanups[i] = fn();
  },
  useSyncExternalStore(subscribe, getSnapshot, getServerSnapshot) { return (getServerSnapshot ?? getSnapshot)(); },
};
const jsx = (type, props) => ({ type, props: props || {} });
const cache = {};
const observed = { search: 0 };
function load(id) {
  if (id === 'react') return hooks;
  if (id === 'react/jsx-runtime') return { jsx, jsxs: jsx, Fragment: 'fragment' };
  if (id === 'react-dom') return { createPortal: node => node };
  if (id === 'next/dynamic') return { __esModule: true, default: () => () => null };
  if (id === 'next/link') return { __esModule: true, default: 'a' };
  if (id.endsWith('.json')) return JSON.parse(fs.readFileSync(path.join(root, 'src', id.slice(2)), 'utf8'));
  if (cache[id]) return cache[id].exports;
  let file = path.join(root, 'src', id.slice(2));
  file += fs.existsSync(file + '.tsx') ? '.tsx' : '.ts';
  const js = ts.transpileModule(fs.readFileSync(file, 'utf8'), { fileName: file, compilerOptions: { jsx: ts.JsxEmit.ReactJSX, module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2020, esModuleInterop: true } }).outputText;
  const mod = cache[id] = { exports: {} };
  new Function('module', 'exports', 'require', js)(mod, mod.exports, load);
  return mod.exports;
}
function instance(Component, effects, trackEffects = false) {
  const state = [];
  const effectDeps = [], effectCleanups = [];
  return { state, render(props = {}) { current = { state, cursor: 0, effects, trackEffects, effectDeps, effectCleanups }; this.tree = Component(props); return this.tree; } };
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
  const { areaWarnings } = load('@/lib/area-warnings');
  const notice = { title: 'ต้นฉบับประกาศ', hazards: ['rain_storm', 'flood'], provinces: ['TH50', 'TH90'],
    provinces_by_hazard: { rain_storm: ['TH50'], flood: ['TH90'] },
    impact_from: '2026-11-05', impact_to: '2026-11-09',
    impact_by_hazard: { rain_storm: { TH50: ['2026-11-05', '2026-11-07'] } } };
  const notices = (w, code = 'TH5001', hazard = 'overview', today = '2026-11-04') => areaWarnings([w], code, hazard, today);
  assert.equal(notices(notice, 'TH1301').length, 0, 'Warnings from other provinces are hidden');
  assert.equal(notices(notice, 'TH5001', 'flood').length, 0, 'Hazard-specific areas take precedence over all provinces');
  assert.deepEqual(notices(notice)[0].impacts, [{ hazard: 'rain_storm', from: '2026-11-05', to: '2026-11-07' }]);
  assert.equal(notices(notice, 'TH50', 'rain_storm').length, 1, 'Province selection uses its own code');
  assert.equal(notices(notice, 'TH5001', 'overview', '2026-11-08').length, 0, 'Expired provincial periods are hidden');
  assert.equal(notices({ ...notice, provinces_by_hazard: { rain_storm: [] } }).length, 0, 'Empty areas never fall back to all provinces');
  assert.equal(notices({ ...notice, provinces_by_hazard: undefined, impact_by_hazard: undefined })[0].impacts[0].to, '2026-11-09', 'Legacy warnings use whole impact dates');
  const periods = { ...notice, periods_by_hazard: { rain_storm: { TH50: [['2026-11-05', '2026-11-06', false], ['2026-11-08', '2026-11-09', 'heavy_rain']] } } };
  assert.deepEqual(notices(periods, 'TH5001', 'rain_storm', '2026-11-07')[0].impacts, [{ hazard: 'rain_storm', from: '2026-11-08', to: '2026-11-09' }], 'New period dates override merged legacy dates');
  const undated = { title: 'ไม่ระบุวัน', hazards: ['flood'], provinces: ['TH50'] };
  assert.deepEqual(notices(undated)[0].impacts, [{ hazard: 'flood', from: null, to: null }]);
  const StormsPanel = load('@/components/StormsPanel').default;
  let stormResponse = { storms: [{ name: 'CHAMPI', time: '2026-10-22T12:00:00+00:00', lat: 21.2, lon: 124.9, wind_kmh: 185, category: { category: 'Cat 3' } }], source: 'สสน.', degraded_services: [] };
  lib.api.storms = async () => stormResponse;
  const stormEffects = [], stormApp = instance(StormsPanel, stormEffects);
  assert(content(stormApp.render({ revision: 0 })).includes('กำลังโหลด'), 'Storm panel has a loading state');
  let cleanupStorm = stormEffects[0](); await tick();
  let stormTree = stormApp.render({ revision: 0 });
  assert(content(stormTree).includes('CHAMPI') && content(stormTree).includes('185') && content(stormTree).includes('Cat 3'));
  assert(content(stormTree).includes('19:00') || content(stormTree).includes('19.00'), 'UTC storm times show Bangkok time');
  assert(content(stormTree).includes('21.2') && content(stormTree).includes('124.9'), 'Storm coordinates are visible');
  cleanupStorm?.();
  stormResponse = { storms: [], source: 'สสน.', degraded_services: [], inputs_pending: ['thaiwater_storms'] };
  stormApp.render({ revision: 1 }); cleanupStorm = stormEffects.at(-1)(); await tick();
  assert(!content(stormApp.render({ revision: 1 })).includes('ไม่มีพายุ'), 'Pending data must not claim no storms'); cleanupStorm?.();
  stormResponse = { storms: [], source: 'สสน.', degraded_services: [] };
  stormApp.render({ revision: 2 }); cleanupStorm = stormEffects.at(-1)(); await tick();
  assert(content(stormApp.render({ revision: 2 })).includes('ไม่มีพายุ')); cleanupStorm?.();
  for (const metadata of [
    { inputs_pending: ['air_model', 'heat_model', 'marine_model'], degraded_services: [] },
    { inputs_pending: [], degraded_services: ['nasa_firms'] },
  ]) {
    stormResponse = { storms: [], source: 'สสน.', ...metadata };
    const effects = [], panel = instance(StormsPanel, effects);
    panel.render({ revision: 0 }); const cleanup = effects[0](); await tick();
    const tree = panel.render({ revision: 0 });
    assert(content(tree).includes('ไม่มีพายุ'), 'Unrelated sources do not hide a successful empty storm list');
    assert(!content(tree).includes('กำลังรอข้อมูลพายุ') && !content(tree).includes('ข้อมูลพายุบางแหล่งไม่พร้อมใช้งาน'));
    assert.equal(tree.props['aria-busy'], false, 'Unrelated pending inputs do not mark storms busy'); cleanup?.();
  }
  stormResponse = { storms: [], source: 'สสน.', degraded_services: ['thaiwater_storms'] };
  stormApp.render({ revision: 5 }); cleanupStorm = stormEffects.at(-1)(); await tick();
  stormTree = stormApp.render({ revision: 5 });
  assert(content(stormTree).includes('ข้อมูลพายุบางแหล่งไม่พร้อมใช้งาน') && !content(stormTree).includes('ไม่มีพายุ'), 'Storm source failures still show degraded data'); cleanupStorm?.();
  lib.api.storms = async () => { throw new Error('offline'); };
  stormApp.render({ revision: 3 }); cleanupStorm = stormEffects.at(-1)(); await tick();
  assert(content(stormApp.render({ revision: 3 })).includes('โหลดรายชื่อพายุไม่สำเร็จ')); cleanupStorm?.();
  lib.api.storms = async () => ({ storms: [{ name: 'EMPTY', time: '2026-11-04T00:00:00Z', wind_kmh: null, lat: null, lon: null, category: null }], source: 'สสน.' });
  stormApp.render({ revision: 4 }); cleanupStorm = stormEffects.at(-1)(); await tick();
  assert(content(stormApp.render({ revision: 4 })).includes('ความเร็วลม: —'), 'Missing wind is not rendered as zero'); cleanupStorm?.();
  const AreaWarnings = load('@/components/AreaWarnings').default;
  const warningTree = AreaWarnings({ warnings: [{ ...notice, impact_from: '2999-11-05', impact_to: '2999-11-09', impact_by_hazard: undefined }], code: 'TH5001', hazard: 'overview', assessments: [{ hazard: 'rain_storm', level: 'CRITICAL', rule_ids: ['OFFICIAL_WARNING_UPCOMING'] }] });
  assert(content(warningTree).includes(notice.title), 'The original bulletin title is preserved');
  assert(!content(warningTree).includes('น้ำท่วม'), 'Overview only shows hazards applicable to the chosen province');
  assert(nodes(warningTree).some(n => n.props.className === 'risk-badge level-CRITICAL'), 'Area warning badge uses the API assessment level');
  assert(!content(warningTree).includes('ประกาศระดับประเทศ'), 'Selected-area warning no longer claims a nationwide notice');
  lib.api.search = async () => { observed.search++; return [{ code: '13', level: 'province', name_th: 'ปทุมธานี', parents: [], lat: 14, lon: 100 }]; };
  lib.api.area = async () => ({ area: { code: '13', level: 'province', name_th: 'ปทุมธานี', parents: [] }, assessments: [], forecast: [], rivers: [{ status: 'NORMAL' }], river_province: { code: '13', name_th: 'ปทุมธานี' }, official_warnings: [], degraded_services: [] });
  lib.api.boundary = async () => null;
  const Dashboard = load('@/components/Dashboard').default;
  for (const activeHazard of ['overview', 'earthquake', 'tropical_cyclone']) {
    const tree = instance(Dashboard).render({ activeView: 'hazard', activeHazard });
    assert.equal(nodes(tree).filter(n => n.type === StormsPanel).length, activeHazard === 'tropical_cyclone' ? 1 : 0, 'HII storms mount only on the cyclone tab');
  }
  let radarRequests = 0;
  let radarEnabled = true;
  let radarFrames = [{ tiles: 'https://tiles.test/old/{z}/{x}/{y}.png', time: 1799996400 }, { tiles: 'https://tiles.test/new/{z}/{x}/{y}.png', time: 1800000000 }];
  lib.api.radar = async () => { radarRequests++; return { enabled: radarEnabled, frames: radarEnabled ? radarFrames : [] }; };
  for (const hazard of ['overview', 'flood', 'rain_storm', 'tropical_cyclone']) {
    const radarEffects = [];
    const radarApp = instance(Dashboard, radarEffects);
    const props = { activeView: 'hazard', activeHazard: hazard };
    const radarTree = radarApp.render(props);
    const before = radarRequests;
    const cleanupRadar = radarEffects.find(effect => /\.radar\(/.test(String(effect)))();
    assert.equal(radarRequests - before, ['overview', 'rain_storm', 'tropical_cyclone'].includes(hazard) ? 1 : 0, `Only overview and weather topics request radar: ${hazard}`);
    if (hazard === 'overview') {
      assert(!nodes(radarTree).some(n => n.props['data-layer'] === 'radar'), 'Radar stays hidden while availability is unknown');
      await tick();
      const enabledTree = radarApp.render(props);
      assert(nodes(enabledTree).some(n => n.props['data-layer'] === 'radar'), 'Overview radar toggle appears when backend enables it');
      const playback = nodes(enabledTree).find(n => n.type.name === 'RadarPlayback');
      assert(playback && playback.props.frames.length === 2 && playback.props.index === 1, 'Radar playback receives history and opens on the latest frame');
      assert.equal(nodes(enabledTree).find(n => 'radarNext' in n.props).props.radarNext, null, 'Idle radar playback does not preload another frame');
      playback.props.onPlaybackChange(true);
      const playingTree = radarApp.render(props);
      assert.equal(nodes(playingTree).find(n => 'radarNext' in n.props).props.radarNext, radarFrames[0].tiles, 'The next frame is preloaded only while playback is active');
      playback.props.onPlaybackChange(false);
      radarApp.render(props);

      let selectedFrame = 1, timerCallback, timerCount = 0, clearCount = 0;
      window.setInterval = callback => { timerCallback = callback; timerCount++; return timerCount; };
      window.clearInterval = () => { clearCount++; };
      const RadarPlayback = load('@/components/RadarPlayback').default;
      const playbackApp = instance(RadarPlayback, undefined, true);
      const playbackProps = () => ({ frames: radarFrames, index: selectedFrame, onChange: index => { selectedFrame = index; }, onPlaybackChange() {} });
      let playbackControl = nodes(playbackApp.render(playbackProps())).find(n => n.type === 'button');
      playbackControl.props.onClick();
      playbackApp.render(playbackProps());
      selectedFrame = 0;
      playbackApp.render(playbackProps());
      assert.equal(timerCount, 1, 'Parent renders and callback identity changes do not restart the playback timer');
      timerCallback();
      assert.equal(selectedFrame, 1, 'Playback advances from the latest selected frame after a parent render');
      playbackControl = nodes(playbackApp.render(playbackProps())).find(n => n.type === 'button');
      playbackControl.props.onClick();
      playbackApp.render(playbackProps());
      assert.equal(clearCount, 1, 'Pausing clears the playback timer');

      nodes(enabledTree).find(n => n.type.name === 'RadarPlayback').props.onChange(0);
      radarFrames = [
        { tiles: 'https://tiles.test/older/{z}/{x}/{y}.png', time: 1799992800 },
        { ...radarFrames[0], tiles: 'https://tiles.test/refreshed/{z}/{x}/{y}.png' },
        { tiles: 'https://tiles.test/latest/{z}/{x}/{y}.png', time: 1800003600 },
      ];
      radarApp.render(props);
      const refreshRadar = radarEffects.filter(effect => /\.radar\(/.test(String(effect))).at(-1)();
      await tick();
      refreshRadar?.();
      const refreshedTree = radarApp.render(props);
      assert.equal(nodes(refreshedTree).find(n => n.type.name === 'RadarPlayback').props.index, 1, 'Radar refresh preserves the selected frame timestamp when it remains available');
      nodes(refreshedTree).find(n => n.type.name === 'RadarPlayback').props.onChange(0);
      radarFrames = [radarFrames[1], radarFrames[2]];
      radarApp.render(props);
      const removedRadarFrame = radarEffects.filter(effect => /\.radar\(/.test(String(effect))).at(-1)();
      await tick();
      removedRadarFrame?.();
      const missingFrameTree = radarApp.render(props);
      assert.equal(nodes(missingFrameTree).find(n => n.type.name === 'RadarPlayback').props.index, 1, 'Radar refresh selects the latest frame if the selected timestamp expired');

      lib.api.radar = async () => { throw new Error('offline'); };
      radarApp.render(props);
      const failRadar = radarEffects.filter(effect => /\.radar\(/.test(String(effect))).at(-1)();
      await tick();
      failRadar?.();
      const failedTree = radarApp.render(props);
      assert(nodes(failedTree).some(n => String(n.props.className || '').split(/\s+/).includes('radar-note') && content(n).includes('ยังโหลดเรดาร์ไม่ได้')), 'Radar failure keeps its unavailable note visible');
      lib.api.radar = async () => { radarRequests++; return { enabled: radarEnabled, frames: radarEnabled ? radarFrames : [] }; };
    }
    cleanupRadar?.();
  }
  radarEnabled = false;
  const disabledRadarEffects = [];
  const disabledRadarApp = instance(Dashboard, disabledRadarEffects);
  const disabledRadarProps = { activeView: 'hazard', activeHazard: 'overview' };
  disabledRadarApp.render(disabledRadarProps);
  const cleanupDisabledRadar = disabledRadarEffects.find(effect => /\.radar\(/.test(String(effect)))();
  await tick();
  assert(!nodes(disabledRadarApp.render(disabledRadarProps)).some(n => n.props['data-layer'] === 'radar'), 'Overview radar toggle stays hidden when backend disables it');
  assert(!nodes(disabledRadarApp.render(disabledRadarProps)).some(n => String(n.props.className || '').split(/\s+/).includes('radar-note')), 'Radar note stays hidden when backend disables radar');
  cleanupDisabledRadar?.();
  // Issue 288/2569: the overview lists the TMD bulletin by region, with the level its wording gives each one.
  lib.api.warnings = async () => ({ warnings: [{ title: 'อากาศแปรปรวนบริเวณประเทศไทยตอนบน ฉบับที่ 5', url: 'https://tmd.go.th/x.pdf', hazards: ['rain_storm', 'flood'], by_region: [
    { from: '2026-11-05', to: '2026-11-07', level: 'WATCH', hazards: ['rain_storm'], areas: ['ประเทศไทยตอนบน'] },
    { from: '2026-11-07', to: '2026-11-09', level: 'WATCH', hazards: ['rain_storm', 'flood'], areas: ['ภาคใต้'] },
  ], rain_days: [
    { day: '2026-11-05', very_heavy: false, provinces: Array.from({ length: 45 }, (_, i) => 'จังหวัด' + i) },
    { day: '2026-11-08', very_heavy: true, provinces: ['นครศรีธรรมราช', 'สงขลา'] },
  ] }] });
  const bulletinEffects = [];
  const bulletinApp = instance(Dashboard, bulletinEffects);
  const bulletinProps = { activeView: 'hazard', activeHazard: 'overview' };
  bulletinApp.render(bulletinProps);
  const cleanupBulletin = bulletinEffects.find(effect => /\.warnings\(/.test(String(effect)))();
  await tick();
  const regionLines = nodes(bulletinApp.render(bulletinProps)).filter(n => n.type === 'li' && /ประเทศไทยตอนบน|ภาคใต้/.test(content(n)));
  assert.deepEqual(regionLines.map(n => [content(n).includes('ภาคใต้'), nodes(n).find(c => String(c.props.className || '').includes('risk-badge')).props.className]),
    [[false, 'risk-badge level-WATCH'], [true, 'risk-badge level-WATCH']], 'Overview lists the TMD bulletin by region with its level');
  // Daily tables: long lists as a count, short ones by name; very heavy rain is a Warning.
  const dayLines = nodes(bulletinApp.render(bulletinProps)).filter(n => n.type === 'li' && /ฝนตกหนัก/.test(content(n)));
  assert.deepEqual(dayLines.map(n => [content(n).includes('45 จังหวัด'), content(n).includes('นครศรีธรรมราช, สงขลา'), nodes(n).find(c => String(c.props.className || '').includes('risk-badge')).props.className]),
    [[true, false, 'risk-badge level-WATCH'], [false, true, 'risk-badge level-WARNING']], 'Overview lists the daily heavy-rain tables');
  cleanupBulletin?.();
  let opened = 0;
  const app = instance(Dashboard);
  let props = { activeView: 'hazard', searchInHeader: true, searchTarget: {}, riverViewRequest: 0 };
  let tree = app.render(props);
  const starter = nodes(tree).find(n => n.type === 'button' && content(n).startsWith('ค้นหาพื้นที่'));
  assert(!starter, 'header search replaces the empty-area search button');
  assert.equal(nodes(tree).find(n => n.props.mode === 'hazard' && 'dark' in n.props).props.dark, true, 'new browsers default to the dark basemap');

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
  assert(nodes(tree).some(n => n.props.className === 'location-scope'), 'province shortcut keeps its province filter');
  props = { ...props, activeView: 'hazard' }; app.render(props);
  props = { ...props, activeView: 'rivers', riverViewRequest: 1 };
  tree = app.render(props);
  assert(nodes(tree).some(n => n.props.className === 'location-scope'), 'top river category preserves selected location');
  props = { ...props, activeView: 'hazard' }; tree = app.render(props);
  nodes(tree).find(n => n.type === 'button' && /river-callout/.test(n.props.className || '')).props.onClick();
  props = { ...props, activeView: 'rivers' }; tree = app.render(props);
  assert(nodes(tree).some(n => n.props.className === 'location-scope'), 'a new province shortcut still works after reset');
  props = { ...props, riverViewRequest: 2 }; tree = app.render(props);
  assert(nodes(tree).some(n => n.props.className === 'location-scope'), 'reselecting rivers preserves the province filter');

  const landing = instance(load('@/components/LandingExperience').default);
  tree = landing.render();
  const headerSearch = nodes(tree).find(n => n.type === 'button' && n.props.className === 'journey-search-icon');
  assert(headerSearch, 'header search entry remains available');
  assert.equal(content(headerSearch), 'พื้นที่ที่คุณอยากรู้', 'visible search label is the accessible name');
  assert.equal(headerSearch.props['aria-label'], undefined, 'do not override the visible search label');
  const searchDialog = nodes(tree).find(n => n.type === 'dialog' && n.props.className === 'journey-search-dialog');
  assert(searchDialog, 'search dialog remains available');
  searchDialog.props.ref.current = { showModal() { opened++; } };
  headerSearch.props.onClick();
  assert.equal(opened, 1, 'header search button opens the search dialog');
  const groups = nodes(tree).filter(n => n.type === 'button' && /journey-category-button/.test(n.props.className || ''));
  assert.deepEqual(groups.map(n => n.props['data-category']), lib.GROUPS.map(g => g.id), 'top bar shows every map group');
  // Opening rivers from the dashboard's sub-tabs makes the water group return there, with the same selected location.
  nodes(tree).find(n => n.type === Dashboard).props.onViewChange('rivers'); tree = landing.render();
  nodes(tree).find(n => n.props['data-category'] === 'overview').props.onClick(); tree = landing.render();
  assert.equal(nodes(tree).find(n => n.type === Dashboard).props.activeView, 'hazard', 'overview group opens the overview map');
  nodes(tree).find(n => n.props['data-category'] === 'water').props.onClick(); tree = landing.render();
  const dashboard = nodes(tree).find(n => n.type === Dashboard).props;
  assert.equal(dashboard.activeView, 'rivers', 'water group returns to the last opened item');
  assert.equal(dashboard.riverViewRequest, undefined, 'category changes do not reset selected location');
  const source = fs.readFileSync(path.join(root, 'src/components/Dashboard.tsx'), 'utf8');
  for (const unused of ['initialQuery', 'initialHazard', 'initialView']) assert(!source.includes(unused), 'unused prop removed: ' + unused);
  assert(!content(tree).includes('ผลการค้นหาในแดชบอร์ด'), 'guide describes current dialog flow');
  const { OverlayControls, OverlayLegends, DEFAULT_OVERLAYS } = load('@/components/OverviewLayers');
  assert.deepEqual(DEFAULT_OVERLAYS, { flood: false, cyclones: true, waves: false });
  const overlay = (status, count = 1) => ({ type: 'FeatureCollection', features: Array.from({length: count}, () => ({type: 'Feature', geometry: null, properties: {}})), overlay: {status, source: 'Test provider', updated_at: '2026-10-22T12:00:00Z'}, degraded_services: [] });
  let toggled;
  const controls = instance(OverlayControls);
  const controlProps = { data: {flood: overlay('unavailable', 0), cyclones: overlay('ok', 0), waves: overlay('pending', 0)}, enabled: DEFAULT_OVERLAYS, onToggle: id => toggled = id };
  tree = controls.render(controlProps);
  assert(content(tree).includes('พายุหมุน') && content(tree).includes('ไม่พบพายุ'), 'cyclone control stays available and explains an empty successful layer');
  assert(nodes(tree).find(n => n.type === 'button' && content(n).includes('น้ำท่วม')).props.disabled, 'unavailable source disables layer with explanation');
  const waveToggle = nodes(tree).find(n => n.type === 'button' && content(n).includes('คลื่น'));
  assert(content(waveToggle).includes('กำลังโหลด'), 'pending layer has loading feedback');
  waveToggle.props.onClick();
  assert.equal(toggled, 'waves', 'toggle changes only the requested layer');
  tree = OverlayLegends({data: {flood: overlay('ok'), cyclones: overlay('ok', 0), waves: overlay('ok')}, enabled: {flood: true, cyclones: true, waves: false}});
  assert(content(tree).includes('Test provider') && content(tree).includes('เวลาไทย'), 'enabled layer credits include timestamp and source');
  assert(!content(tree).includes('เส้นทึบ') && !content(tree).includes('ลูกศร'), 'disabled and empty overlays have no legend');
  tree = OverlayLegends({data: {waves: {...overlay('ok'), degraded_services: ['open_meteo_waves']}}, enabled: {waves: true}});
  assert(content(tree).includes('ข้อมูลเดิม'), 'stale data is explicitly identified');
  const { useOverviewLayers } = load('@/components/OverviewLayers');
  const effects = [];
  const timers = [];
  const originalTimeout = global.setTimeout, originalClear = global.clearTimeout;
  global.setTimeout = (fn, delay) => { const timer = {fn, delay}; timers.push(timer); return timer; };
  global.clearTimeout = timer => { timer.cleared = true; };
  const calls = [];
  lib.api.overlay = async id => { calls.push(id); return overlay(id === 'waves' && calls.filter(x => x === id).length === 1 ? 'pending' : 'ok'); };
  const state = instance(() => useOverviewLayers(true, 0), effects);
  state.render();
  const cleanup = effects[0]();
  await tick();
  assert.deepEqual(calls, ['flood', 'cyclones', 'waves'], 'each overlay loads independently');
  assert.equal(state.render().data.waves.overlay.status, 'pending');
  assert.equal(timers[0].delay, 60000, 'pending overlay polls after one minute');
  await timers[0].fn(); await tick();
  assert.equal(state.render().data.waves.overlay.status, 'ok', 'pending layer recovers to ready');
  cleanup();
  assert(timers.every(timer => timer.cleared), 'leaving overview cancels polling');
  global.setTimeout = originalTimeout; global.clearTimeout = originalClear;
  const RiskSnapshot = load('@/components/RiskSnapshot').default;
  tree = RiskSnapshot({ total: null, counts: [] });
  assert.equal(nodes(tree).filter(n => n.type === 'strong' && content(n) === '—').length, 4, 'unloaded summary does not invent zero risk');
  tree = RiskSnapshot({ total: 928, counts: [{id:'CRITICAL',n:2,color:'#d7263d',th:'วิกฤต'}, {id:'WARNING',n:13,color:'#f77f00',th:'เตือนภัย'}, {id:'WATCH',n:41,color:'#f6c90e',th:'เฝ้าระวัง'}] });
  assert.deepEqual(nodes(tree).filter(n => n.type === 'strong').map(content), ['928','2','13','41'], 'summary retains the exact API-derived counts');
  assert(content(tree).includes('อำเภอในแผนที่') && content(tree).includes('เฝ้าระวัง'), 'summary uses explicit Thai labels');
  const scenic = instance(load('@/components/LandingExperience').default);
  const { riskAreasAtLevel } = load('@/lib/risk-overview');
  const polygon = {type:'Polygon',coordinates:[[[100,14],[102,14],[102,16],[100,16],[100,14]]]};
  const risks = {type:'FeatureCollection',features:[
    {type:'Feature',geometry:polygon,properties:{code:'TH2',name_th:'สอง',province_th:'ขอนแก่น',level:'WARNING',worst_hazard:'flood'}},
    {type:'Feature',geometry:polygon,properties:{code:'TH1',name_th:'หนึ่ง',province_th:'กระบี่',level:'WARNING',worst_hazard:'heat'}},
    {type:'Feature',geometry:polygon,properties:{code:'TH3',name_th:'สาม',level:'CRITICAL',worst_hazard:'fire'}}]};
  const warningAreas = riskAreasAtLevel(risks, 'WARNING');
  assert.deepEqual(warningAreas.map(a=>a.code), ['TH1','TH2'], 'risk list filters levels and sorts provinces');
  assert.deepEqual(warningAreas[0].center, [101,15], 'map points use district bounding centres');
  assert.equal(warningAreas[1].hazard, 'flood', 'list identifies the highest hazard');
  assert.deepEqual(riskAreasAtLevel(null, 'WATCH'), [], 'unavailable layers have no invented alerts');
  tree = scenic.render();
  assert(!nodes(tree).some(n => n.props.className === 'scenic-map-toggle'), 'map is always raised without an extra expand step');
  assert(nodes(tree).some(n => n.props.className === 'header-snapshot'), 'summary has its own header slot');
  assert(!nodes(tree).some(n => n.props.className === 'journey-top-cta'), 'Explore button is removed');
  assert(nodes(tree).some(n => n.props.className === 'journey-search-icon'), 'Area search stays available');
  const WaterQualityPanel = load('@/components/WaterQualityPanel').default;
  const i18n = load('@/lib/i18n');
  const previousDocument = global.document;
  global.document = {documentElement: {lang: 'th'}};
  const qualityStation = {id:'quality-1',name_th:'สถานีทดสอบ',river_th:'แม่น้ำทดสอบ',amphoe_th:'อำเภอทดสอบ',province_th:'จังหวัดทดสอบ',agency_th:'หน่วยงานทดสอบ',time:'2026-11-03T08:00:00Z',salinity_gl:0.8,do_mgl:2,ph:null,turbidity_ntu:null,tds_mgl:null,conductivity_uscm:null,temp_c:null,lat:13,lon:100,exceeds:{salinity:{text:'> 0.5'},do:{text:'< 3'}}};
  const qualityProps = {stations:[qualityStation],selected:qualityStation.id,onSelect(){},onClose(){},status:'success',degraded:false,source:'แหล่งข้อมูลทดสอบ',onRetry(){}};
  try {
    i18n.setLanguage('en');
    tree = WaterQualityPanel(qualityProps);
    const qualityStatus = nodes(tree).find(n => n.props.className === 'quality-status');
    assert.equal(content(qualityStatus), 'Above threshold: Salinity, Dissolved oxygen', 'dynamic threshold summary translates the prefix and each parameter');
    assert(content(tree).includes('สถานีทดสอบ') && content(tree).includes('แหล่งข้อมูลทดสอบ'), 'provider station/source names stay intact');
    assert(content(tree).includes('Province จังหวัดทดสอบ'), 'translated prefixes are separated from original place names');
    assert(content(tree).includes('0.80') && content(tree).includes('—'), 'measurements keep their values and missing metrics are not zero');
    assert(content(tree).includes('> 0.5') && content(tree).includes('< 3'), 'API threshold text stays intact');
    i18n.setLanguage('th');
    assert.equal(content(nodes(WaterQualityPanel(qualityProps)).find(n => n.props.className === 'quality-status')), 'เกินเกณฑ์: ความเค็ม, ออกซิเจนละลายน้ำ');
  } finally {
    i18n.setLanguage('th');
    global.document = previousDocument;
  }
  console.log('PASS: landing/search, overlays and polling, risk summaries, water-quality TH/EN thresholds and source/value preservation.');
})().catch(error => { console.error(error); process.exit(1); });
