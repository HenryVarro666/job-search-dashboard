/* Synthetic flow checks execute the actual template functions. */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const html = fs.readFileSync(process.env.DASHBOARD_TEMPLATE || path.join(__dirname, 'dashboard_template.html'), 'utf8');
function fn(name) {
  const start = html.indexOf('  function ' + name + '(');
  assert.ok(start >= 0, 'missing template function: ' + name);
  let depth = 0, quote = '', escaped = false;
  for (let i = html.indexOf('{', start); i < html.length; i++) {
    const c = html[i];
    if (quote) { if (escaped) escaped = false; else if (c === '\\') escaped = true; else if (c === quote) quote = ''; continue; }
    if (c === '"' || c === "'") { quote = c; continue; }
    if (c === '{') depth++;
    if (c === '}' && --depth === 0) return html.slice(start, i + 1);
  }
  throw new Error('unclosed function: ' + name);
}
class Element {
  constructor(tag) { this.tag = tag; this.children = []; this.attrs = {}; this.style = {}; this.listeners = {}; this._text = ''; }
  set textContent(v) { this._text = String(v); this.children = []; }
  get textContent() { return this._text + this.children.map(c => typeof c === 'string' ? c : c.textContent).join(''); }
  appendChild(c) { this.children.push(c); return c; }
  setAttribute(k, v) { this.attrs[k] = String(v); }
  addEventListener(k, v) { this.listeners[k] = v; }
}
function walk(node, predicate) {
  if (predicate(node)) return node;
  for (const child of node.children || []) if (typeof child !== 'string') {
    const found = walk(child, predicate); if (found) return found;
  }
}
const nodes = new Map();
const document = {
  createElement: tag => new Element(tag), createElementNS: (ns, tag) => new Element(tag), createTextNode: String,
  getElementById(id) { if (!nodes.has(id)) nodes.set(id, new Element('div')); return nodes.get(id); },
};
function app(id, group, outcome = group) {
  return {id, company: 'Example', application_confirmed: true, group, flow_outcome: outcome, history_highest: 'application'};
}
const fixtures = [app('rejected', 'rejected'), app('closed', 'closed'), app('withdrawn', 'closed', 'withdrawn'),
  app('archived', 'archived'), app('paused', 'paused', 'reactivation_pending'), app('waiting', 'waiting'),
  app('active', 'progress', 'oa_active'), app('offer', 'progress', 'offer')];
const unconfirmed = {...app('referral', 'pending', 'unconfirmed'), application_confirmed: false};
let opened = null;
const context = vm.createContext({
  document, APPS: fixtures.concat(fixtures[0], unconfirmed), SVGNS: 'http://www.w3.org/2000/svg',
  matchesContext: () => true, openRecordList: (title, rows) => { opened = {title, rows}; }, exportApplicationFlow: () => {},
});
vm.runInContext(['el', 'sv', 'oaUniqueRows', 'oaContextRows', 'flowHistoryLabel', 'flowOutcomeLabel', 'flowColor',
  'flowKeys', 'applicationFlowModel', 'applicationFlowSvg', 'renderApplicationFlow'].map(fn).join('\n'), context);
const grouped = vm.runInContext('applicationFlowModel(null, true)', context);
assert.equal(grouped.total, 8, 'duplicate IDs and unconfirmed records do not inflate the cohort');
assert.equal(grouped.record_count, 9); assert.equal(grouped.unconfirmed_count, 1);
assert.deepEqual(JSON.parse(JSON.stringify(grouped.histories)), [{key: 'all', value: 8}]);
assert.deepEqual(Array.from(grouped.outcomes, r => r.key), ['offer', 'progress', 'waiting', 'paused', 'archived', 'ended', 'rejected']);
assert.equal(grouped.outcomes.find(r => r.key === 'ended').value, 2, 'closed and withdrawn share one current-result group');
assert.equal(grouped.links.filter(l => l.target.startsWith('outcome:')).reduce((n, l) => n + l.value, 0), 8);
vm.runInContext('renderApplicationFlow()', context);
const flow = nodes.get('application-flow');
assert.match(flow.textContent, /共 8 条已确认申请/);
assert.match(flow.textContent, /当前去向/);
assert.match(flow.textContent, /从好到坏/);
assert.ok(walk(flow, n => n.tag === 'table'), 'flow has an equivalent data table');
const expanded = walk(flow, n => n.tag === 'details' && n.className === 'flow-expanded');
assert.ok(expanded && Object.hasOwn(expanded.attrs, 'open'), 'current flow starts expanded');
const band = walk(flow, n => n.tag === 'path' && n.attrs['data-flow-count'] === '2');
assert.ok(band, 'closed/withdrawn band is available');
band.listeners.click();
assert.deepEqual(Array.from(opened.rows, r => r.id).sort(), ['closed', 'withdrawn'], 'band opens exactly its grouped records');
opened = null;
let prevented = false;
band.listeners.keydown({key: 'Enter', preventDefault() { prevented = true; }});
assert.ok(prevented); assert.equal(opened.rows.length, 2, 'keyboard opens the same records');
context.APPS = [];
vm.runInContext('renderApplicationFlow()', context);
assert.match(nodes.get('application-flow').textContent, /没有已确认申请/);
console.log('PASS flow: single origin, ordered groups, cohort deduplication, conserved counts, accessible table and exact click/keyboard selection');
