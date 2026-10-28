/* In-memory DOM integration checks. No browser, layout engine or network access. */
const fs = require('node:fs');
const path = require('node:path');
const assert = require('node:assert/strict');
const {JSDOM, ResourceLoader, VirtualConsole} = require('jsdom');
class NoNetwork extends ResourceLoader {
  fetch(url) { throw new Error('Unexpected resource request: ' + url); }
}
function load(html, url = 'https://dashboard.invalid/') {
  const errors = [];
  const vc = new VirtualConsole();
  vc.on('jsdomError', error => errors.push(error.detail && error.detail.stack || error.message));
  const dom = new JSDOM(html, {url, runScripts:'dangerously', resources:new NoNetwork(),
    pretendToBeVisual:true, virtualConsole:vc, beforeParse(window) {
      window.matchMedia = () => ({matches:false, addEventListener(){}, removeEventListener(){}, addListener(){}, removeListener(){}});
      window.HTMLElement.prototype.scrollIntoView = function() {};
      window.scrollTo = function() {};
      // jsdom has no modal top layer; this only exercises application state.
      window.HTMLDialogElement.prototype.showModal = function() {this.open = true;};
      window.HTMLDialogElement.prototype.close = function() {this.open = false;};
      window.fetch = () => {throw new Error('Network disabled in DOM tests');};
      window.open = () => {throw new Error('Unexpected navigation in DOM tests');};
      window.CSS = window.CSS || {};
      window.CSS.escape = value => String(value).replace(/[^a-zA-Z0-9_-]/g, '\\$&');
    }});
  return {dom, errors};
}
function click(window, node) {
  assert.ok(node, 'expected an interactive element');
  node.dispatchEvent(new window.MouseEvent('click',{bubbles:true}));
}
const source = process.argv[2] || path.join(__dirname,'../.local/demo/工具/求职进度面板/dashboard.html');
const html = fs.readFileSync(source,'utf8');
const {dom, errors} = load(html);
const w = dom.window, d = w.document;
const payload = JSON.parse(d.getElementById('data').textContent);
assert.ok(payload.apps.length, 'demo must include applications');
assert.ok(d.querySelectorAll('#tabs [role="tab"]').length >= 4, 'navigation initialized');
for (const tab of ['today','discovered','progress','internships','todo']) {
  click(w,d.querySelector('#tabs [data-tab="'+tab+'"]'));
  assert.equal(d.getElementById('tab-'+tab).hidden,false, 'tab opens: '+tab);
}
// Whole-page event wiring: subviews, exact search result and focus restoration.
click(w,d.querySelector('#tabs [data-tab="progress"]'));
for (const [index,view] of ['current','records','statistics'].entries()) {
  click(w,d.querySelectorAll('#progress-nav button')[index]);
  assert.equal(d.getElementById('tab-progress').dataset.view,view);
  if(view==='current') {
    assert.equal(d.querySelector('#chips .g-rejected'),null,'ended stage control belongs in records view');
    assert.ok(d.getElementById('progress-filter-summary').textContent.includes('当前看板显示'),'current view has scoped count');
  }
  for (const section of d.querySelectorAll('[data-progress-view]')) {
    const expected = !section.dataset.progressView.split(' ').includes(view);
    // Data-quality cards are also hidden when empty.
    if (!['missing-card','checks-card'].includes(section.id)) assert.equal(section.hidden,expected,section.id || view);
  }
}
const query = d.getElementById('q'); query.value='retain this filter';
query.dispatchEvent(new w.Event('input',{bubbles:true}));
const search = d.getElementById('global-q');
for (const req of [payload.apps.find(x=>x.req)?.req, ...(payload.today_panel?.todo || []).slice(0,1).map(x=>x.req), ...(payload.internships?.items || []).slice(0,1).map(x=>x.req)].filter(Boolean)) {
  search.value=req; search.dispatchEvent(new w.Event('input',{bubbles:true}));
  const result = d.querySelector('#global-results button'); assert.ok(result,'record found: '+req);
  search.focus(); click(w,result);
  assert.equal(d.getElementById('detail-drawer').open,true,'drawer opens');
  assert.ok(d.getElementById('drawer-title').textContent.includes(req),'exact req selected');
  const app = payload.apps.find(x=>x.req===req);
  if(app) assert.ok(d.getElementById('detail-drawer').textContent.includes(app.title),'current record retains full job title');
  assert.equal(query.value,'retain this filter','global detail preserves page filter');
  d.getElementById('detail-drawer').dispatchEvent(new w.KeyboardEvent('keydown',{key:'Escape',bubbles:true}));
  assert.equal(d.getElementById('detail-drawer').open,false);
  assert.equal(d.activeElement,search,'focus returns to search');
}
assert.equal(errors.length,0,errors.join('\n'));
if (payload.board_html) {
  const board = load(payload.board_html, 'https://opportunities.invalid/');
  assert.equal(board.errors.length,0,board.errors.join('\n'));
  board.dom.window.close();
}
dom.window.close();
console.log('Full-page DOM initialization, navigation and embedded opportunity-board checks passed (no rendering or network).');
