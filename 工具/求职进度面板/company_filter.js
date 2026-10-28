/* Shared local company preferences. Display groups do not change employer/legal-entity data. */
(function (root) {
  'use strict';
  const STORAGE_KEY = 'jobdash-company-filter-v1';
  const aliases = [
    ['tiktok_bytedance', 'TikTok / ByteDance', /tiktok|byte[\s_-]*dance|字节跳动|抖音|\busds\b/i],
    ['mayo_clinic', 'Mayo Clinic', /^mayo[\s_-]*clinic\b/i],
    ['nyu_langone', 'NYU Langone Health', /^nyu[\s_-]+langone(?:[\s_-]+health)?$/i],
    ['amazon', 'Amazon', /^amazon(?:\s*\/\s*aws)?$/i],
    ['cvs_health', 'CVS Health', /^cvs[\s_-]+health(?:\s*\(aetna\))?$/i],
    ['becton_dickinson', 'Becton Dickinson (BD)', /^(?:bd|becton[\s_-]+dickinson(?:\s*\(bd\))?)$/i]
  ];
  let memory = {mode: 'exclude', selected: []};
  const volatile = new WeakMap();
  let sequence = 0;
  const clean = value => String(value == null ? '' : value).normalize('NFKC').trim().replace(/\s+/g, ' ');
  const companyOf = row => row && typeof row === 'object' ? row.company : row;
  function keyFor(company) {
    const name = clean(company);
    if (!name) return '';
    const alias = aliases.find(([key, , pattern]) => name === key || pattern.test(name));
    return alias ? alias[0] : name.toLowerCase().replace(/[^\p{L}\p{N}]+/gu, '_').replace(/^_+|_+$/g, '');
  }
  function labelFor(company) {
    const name = clean(company), key = keyFor(name), alias = aliases.find(row => row[0] === key);
    if (alias) return alias[1];
    return name.includes('_') ? name.replace(/_/g, ' ').replace(/\b\p{L}/gu, char => char.toUpperCase()) : name;
  }
  function normalize(state) {
    const selected = state && Array.isArray(state.selected) ? state.selected : [];
    return {mode: state && state.mode === 'only' ? 'only' : 'exclude', selected: [...new Set(selected.filter(value => typeof value === 'string').map(keyFor).filter(Boolean))]};
  }
  function matches(row, state) {
    const value = normalize(state);
    if (!value.selected.length) return true;
    const chosen = value.selected.includes(keyFor(companyOf(row)));
    return value.mode === 'only' ? chosen : !chosen;
  }
  function catalog(items) {
    const groups = new Map(), seen = new Set();
    for (const row of items || []) {
      const company = companyOf(row), key = keyFor(company);
      if (!key) continue;
      const identity = key + '\u0000' + clean(row && typeof row === 'object' ? row.req || row.title : '');
      if (seen.has(identity)) continue;
      seen.add(identity);
      if (!groups.has(key)) groups.set(key, {key, label: labelFor(company), count: 0});
      groups.get(key).count++;
    }
    return [...groups.values()].sort((a, b) => a.label.localeCompare(b.label, 'en'));
  }
  function defaultStorage() { try { return root.localStorage; } catch (_) { return null; } }
  function save(state, storage = defaultStorage()) {
    memory = normalize(state);
    if (storage && typeof storage === 'object') {
      try { storage.setItem(STORAGE_KEY, JSON.stringify(memory)); volatile.delete(storage); }
      catch (_) { volatile.set(storage, normalize(memory)); }
    }
    return normalize(memory);
  }
  function load(storage = defaultStorage()) {
    if (!storage || typeof storage !== 'object') return normalize(memory);
    if (volatile.has(storage)) return normalize(volatile.get(storage));
    try {
      const current = storage.getItem(STORAGE_KEY);
      if (current !== null) {
        try { memory = normalize(JSON.parse(current)); }
        catch (_) { return save({mode: 'exclude', selected: []}, storage); }
        return normalize(memory);
      }
      let legacy = {};
      try { legacy = JSON.parse(storage.getItem('hcboard.filters') || '{}') || {}; } catch (_) {}
      let companies = Array.isArray(legacy.companies) ? legacy.companies : [];
      try { const direct = JSON.parse(storage.getItem('hcboard.filters.companies') || 'null'); if (Array.isArray(direct)) companies = companies.concat(direct); } catch (_) {}
      const oldMode = storage.getItem('jobdash-company-mode');
      const excluded = companies.includes('exclude_tiktok_bytedance') || oldMode === 'exclude';
      const only = companies.includes('tiktok_bytedance') || oldMode === 'only';
      // Always write even an empty migration, so clearing cannot revive legacy preferences.
      return save({mode: excluded ? 'exclude' : only ? 'only' : 'exclude', selected: excluded || only ? ['tiktok_bytedance'] : []}, storage);
    } catch (_) { return normalize(memory); }
  }
  function describe(state) {
    const value = normalize(state), count = value.selected.length;
    return count ? (value.mode === 'only' ? '只看 ' : '已隐藏 ') + count + ' 家公司' : '全部公司';
  }
  function same(a, b) { return a.mode === b.mode && a.selected.length === b.selected.length && a.selected.every(key => b.selected.includes(key)); }
  function create({container, items = [], onChange = () => {}}) {
    const doc = container.ownerDocument || root.document, id = 'jcf-' + (++sequence);
    let state = load();
    const companies = catalog(items);
    const node = (tag, className, text) => {
      const element = doc.createElement(tag);
      if (className) element.className = className;
      if (text != null) element.textContent = text;
      return element;
    };
    container.replaceChildren();
    container.className += ' jcf-root';
    const details = node('details', 'jcf-panel'), summary = node('summary', 'jcf-summary');
    const summaryTitle = node('span', '', '公司筛选'), summaryState = node('span', 'jcf-summary-state');
    summary.append(summaryTitle, summaryState); details.append(summary);
    const body = node('div', 'jcf-body'), modes = node('fieldset', 'jcf-modes');
    modes.append(node('legend', 'jcf-sr-only', '公司筛选方式'));
    const modeInputs = [];
    for (const [value, text] of [['exclude', '隐藏所选'], ['only', '只看所选']]) {
      const label = node('label', 'jcf-mode'), radio = node('input');
      radio.type = 'radio'; radio.name = id + '-mode'; radio.value = value;
      radio.addEventListener('change', () => { if (radio.checked) update({mode: value, selected: state.selected}); });
      label.append(radio, doc.createTextNode(text)); modes.append(label); modeInputs.push(radio);
    }
    const searchLabel = node('label', 'jcf-search-label', '搜索公司'), search = node('input', 'jcf-search');
    search.type = 'search'; search.id = id + '-search'; search.placeholder = '输入公司名'; searchLabel.setAttribute('for', search.id);
    const list = node('div', 'jcf-list'), empty = node('p', 'jcf-empty', '没有匹配的公司');
    const hint = node('p', 'jcf-hint', '可多选；未选择时显示全部。设置同时用于今天、待投岗位与投递进度。数字为全部记录中的岗位数，不随其他筛选变化。');
    const active = node('div', 'jcf-active'); active.setAttribute('aria-live', 'polite');
    const activeStatus = node('span', 'jcf-active-status'), tags = node('div', 'jcf-tags');
    const clearButton = node('button', 'jcf-clear', '恢复全部'); clearButton.type = 'button'; clearButton.addEventListener('click', clear);
    active.append(activeStatus, tags, clearButton);
    body.append(modes, searchLabel, search, list, empty, hint); details.append(body); container.append(details, active);
    let listRows = [];
    function filterList() {
      const query = clean(search.value).toLowerCase(), queryKey = keyFor(query);
      let visible = 0;
      for (const row of listRows) { row.element.hidden = !!query && !row.search.includes(query) && row.checkbox.value !== queryKey; if (!row.element.hidden) visible++; }
      empty.hidden = visible > 0;
    }
    function render() {
      const focusedRow = listRows.find(row => row.checkbox === doc.activeElement);
      const focusedKey = focusedRow && focusedRow.checkbox.value;
      summaryState.textContent = describe(state); activeStatus.textContent = describe(state);
      for (const radio of modeInputs) radio.checked = radio.value === state.mode;
      const rows = companies.slice();
      for (const key of state.selected) if (!rows.some(row => row.key === key)) rows.push({key, label: labelFor(key), count: 0});
      rows.sort((a, b) => Number(state.selected.includes(b.key)) - Number(state.selected.includes(a.key)) || a.label.localeCompare(b.label, 'en'));
      list.replaceChildren(); listRows = [];
      for (const row of rows) {
        const label = node('label', 'jcf-company'), checkbox = node('input');
        checkbox.type = 'checkbox'; checkbox.value = row.key; checkbox.checked = state.selected.includes(row.key);
        checkbox.addEventListener('change', () => update({mode: state.mode, selected: checkbox.checked ? state.selected.concat(row.key) : state.selected.filter(key => key !== row.key)}));
        label.append(checkbox, node('span', 'jcf-company-name', row.label), node('span', 'jcf-count', row.count ? String(row.count) : '当前无岗位'));
        list.append(label); listRows.push({element: label, checkbox, search: (row.label + ' ' + row.key).toLowerCase()});
      }
      tags.replaceChildren();
      for (const key of state.selected) {
        const label = rows.find(row => row.key === key).label, remove = node('button', 'jcf-tag', label + ' ×');
        remove.type = 'button'; remove.setAttribute('aria-label', '移除公司筛选：' + label);
        remove.addEventListener('click', () => update({mode: state.mode, selected: state.selected.filter(value => value !== key)})); tags.append(remove);
      }
      clearButton.hidden = !state.selected.length; active.hidden = !state.selected.length;
      filterList();
      const nextFocused = listRows.find(row => row.checkbox.value === focusedKey);
      if (nextFocused && !nextFocused.element.hidden) nextFocused.checkbox.focus();
    }
    function update(value) {
      const next = normalize(value);
      if (same(next, state)) return;
      state = save(next); render(); onChange(normalize(state));
    }
    function clear() { update({mode: 'exclude', selected: []}); }
    function reload() {
      const next = load();
      if (same(next, state)) return;
      state = next; render(); onChange(normalize(state));
    }
    search.addEventListener('input', filterList);
    root.addEventListener('storage', event => { if (event.key == null || event.key === STORAGE_KEY) reload(); });
    render();
    return {getState: () => normalize(state), matches: row => matches(row, state), reload, open: () => { details.open = true; summary.scrollIntoView?.({block: 'nearest'}); summary.focus?.(); }, clear};
  }
  root.JobCompanyFilter = {keyFor, labelFor, matches, catalog, normalize, load, save, describe, create};
})(window);
