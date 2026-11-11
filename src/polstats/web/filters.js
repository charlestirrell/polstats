'use strict';
/* Global filters for the explorer: state, predicates, URL-hash persistence and the live filter bar.

   Loaded before app.js. Nothing here runs at load time; everything is called from app.js once the data is in.
   Members and contests are filtered by the same bar. Some filters apply to only one kind of record (age is
   members-only, margin is contests-only) and the bar labels which.                                              */

const newFilters = () => ({
  q: '', chambers: new Set(), groups: new Set(), states: new Set(),             // both
  gender: '', age: null, tenure: null,                                          // members only
  years: null, kinds: new Set(), margin: null, unc: '', linked: false,          // elections only
});
const F = newFilters();
const BOUNDS = { age: [25, 95], tenure: [0, 50], years: [1976, 2024], margin: [-100, 100] };
const KINDS = ['general', 'special', 'runoff', 'primary'];
let filterChanged = () => {};   // set by buildFilterBar

const resetFilters = () => Object.assign(F, newFilters());
const inRange = (v, r) => v != null && v >= r[0] && v <= r[1];

/* ---------- predicates ---------- */
function memberMatches(r) {
  if (F.chambers.size && !F.chambers.has(r.chamber)) return false;
  if (F.groups.size && !F.groups.has(r.group)) return false;
  if (F.states.size && !F.states.has(r.state)) return false;
  if (F.gender && (r.person.gender || '') !== F.gender) return false;
  if (F.age && !inRange(r.age, F.age)) return false;
  if (F.tenure && !inRange(r.tenure, F.tenure)) return false;
  if (F.q) {
    const q = F.q.toLowerCase();
    if (!(r.name.toLowerCase().includes(q) || r.state.toLowerCase() === q || r.stateName.toLowerCase().includes(q) || r.bioguide.toLowerCase() === q)) return false;
  }
  return true;
}
function contestMatches(c) {
  if (F.chambers.size && !F.chambers.has(c.office === 'senator' ? 'senate' : 'house')) return false;
  if (F.states.size && !F.states.has(c.state)) return false;
  if (F.groups.size && !(c.winner && F.groups.has(groupOf(c.winner.party)))) return false;   // party of the winner
  if (F.years && !inRange(c.year, F.years)) return false;
  if (F.kinds.size && !F.kinds.has(c.kind)) return false;
  if (F.margin && !inRange(c.margin, F.margin)) return false;
  if (F.unc === 'only' && !c.uncontested) return false;
  if (F.unc === 'exclude' && c.uncontested) return false;
  if (F.linked && !(c.winner && c.winner.cand.person)) return false;
  if (F.q) {
    if (c._hay === undefined) c._hay = (c.stateName + ' ' + c.state + ' ' + c.named.map(x => x.name).join(' ')).toLowerCase();
    if (!c._hay.includes(F.q.toLowerCase())) return false;
  }
  return true;
}
const sharedActive = () => !!(F.q || F.chambers.size || F.groups.size || F.states.size);
const memberFiltersActive = () => sharedActive() || !!(F.gender || F.age || F.tenure);
const contestFiltersActive = () => sharedActive() || !!(F.years || F.kinds.size || F.margin || F.unc || F.linked);

/* ---------- toggles used by the filter bar and by click-to-filter on charts ---------- */
function toggleIn(set, v) { set.has(v) ? set.delete(v) : set.add(v); filterChanged(); }
function pickRange(key, range) {   // same range again clears it
  const cur = F[key];
  F[key] = cur && cur[0] === range[0] && cur[1] === range[1] ? null : range;
  filterChanged();
}

/* ---------- URL hash: filters are shareable and survive reloads ---------- */
const nice = s => s.replace(/%3A/g, ':').replace(/%2C/g, ',');
function filtersToHash(extra = {}) {
  const p = new URLSearchParams();
  if (F.q) p.set('q', F.q);
  if (F.chambers.size) p.set('ch', [...F.chambers].join(','));
  if (F.groups.size) p.set('p', [...F.groups].join(','));
  if (F.states.size) p.set('st', [...F.states].sort().join(','));
  if (F.gender) p.set('g', F.gender);
  if (F.age) p.set('age', F.age.join(':'));
  if (F.tenure) p.set('ten', F.tenure.join(':'));
  if (F.years) p.set('yrs', F.years.join(':'));
  if (F.kinds.size) p.set('kind', [...F.kinds].join(','));
  if (F.margin) p.set('m', F.margin.join(':'));
  if (F.unc) p.set('unc', F.unc);
  if (F.linked) p.set('lk', '1');
  for (const [k, v] of Object.entries(extra)) if (v) p.set(k, v);
  return nice(p.toString());
}
function filtersFromHash(hash) {
  const p = new URLSearchParams(String(hash).replace(/^#/, ''));
  resetFilters();
  const list = k => (p.get(k) || '').split(',').map(s => s.trim()).filter(Boolean);
  const range = k => {
    const m = /^(-?\d+(?:\.\d+)?):(-?\d+(?:\.\d+)?)$/.exec(p.get(k) || '');
    return m ? [Number(m[1]), Number(m[2])].sort((a, b) => a - b) : null;
  };
  F.q = (p.get('q') || '').slice(0, 80);
  F.chambers = new Set(list('ch').filter(x => x === 'senate' || x === 'house'));
  F.groups = new Set(list('p').filter(x => 'DRIU'.includes(x) && x.length === 1));
  F.states = new Set(list('st').map(s => s.toUpperCase()).filter(s => /^[A-Z]{2}$/.test(s)));
  F.gender = ['female', 'male'].includes(p.get('g')) ? p.get('g') : '';
  F.age = range('age'); F.tenure = range('ten'); F.years = range('yrs'); F.margin = range('m');
  F.kinds = new Set(list('kind').filter(k => KINDS.includes(k)));
  F.unc = ['only', 'exclude'].includes(p.get('unc')) ? p.get('unc') : '';
  F.linked = p.get('lk') === '1';
  const asof = p.get('asof');
  return { asof: /^\d{4}-\d{2}-\d{2}$/.test(asof || '') ? asof : null };
}

/* ---------- active-filter chips ---------- */
function activeChips() {
  const chips = [], add = (label, scope, remove) => chips.push({ label, scope, remove });
  const fmtR = (r, u = '') => `${r[0]}–${r[1]}${u}`;
  if (F.q) add(`Search “${F.q}”`, 'both', () => { F.q = ''; });
  F.chambers.forEach(c => add(`Chamber: ${c === 'senate' ? 'Senate' : 'House'}`, 'both', () => F.chambers.delete(c)));
  F.groups.forEach(g => add(`Party: ${GNAME[g]}`, 'both', () => F.groups.delete(g)));
  F.states.forEach(s => add(`State: ${s}`, 'both', () => F.states.delete(s)));
  if (F.gender) add(`Gender: ${F.gender}`, 'members', () => { F.gender = ''; });
  if (F.age) add(`Age ${fmtR(F.age)}`, 'members', () => { F.age = null; });
  if (F.tenure) add(`Years since first term ${fmtR(F.tenure)}`, 'members', () => { F.tenure = null; });
  if (F.years) add(`Election years ${fmtR(F.years)}`, 'elections', () => { F.years = null; });
  F.kinds.forEach(k => add(`Kind: ${k}`, 'elections', () => F.kinds.delete(k)));
  if (F.margin) add(`Margin D−R ${F.margin[0]} to ${F.margin[1]}`, 'elections', () => { F.margin = null; });
  if (F.unc) add(F.unc === 'only' ? 'Uncontested only' : 'Contested only', 'elections', () => { F.unc = ''; });
  if (F.linked) add('Winner is a current member', 'elections', () => { F.linked = false; });
  return chips;
}

/* ---------- the live filter bar ---------- */
const FB = { sync: [], countEl: null, chipsEl: null };

function rangeControl(key, label, step, format) {
  const out = el('output', { 'aria-live': 'off' });
  const lo = el('input', { type: 'range', step, 'aria-label': `${label}, minimum` });
  const hi = el('input', { type: 'range', step, 'aria-label': `${label}, maximum` });
  const show = () => { out.textContent = format(Number(lo.value)) + ' – ' + format(Number(hi.value)); };
  const apply = movedLow => {
    let a = Number(lo.value), b = Number(hi.value);
    if (a > b) { if (movedLow) b = a; else a = b; lo.value = a; hi.value = b; }
    const [bmin, bmax] = BOUNDS[key];
    F[key] = a <= bmin && b >= bmax ? null : [a, b];
    show(); filterChanged();
  };
  lo.addEventListener('input', () => apply(true));
  hi.addEventListener('input', () => apply(false));
  FB.sync.push(() => {
    const [bmin, bmax] = BOUNDS[key], r = F[key] || [bmin, bmax];
    for (const i of [lo, hi]) { i.min = bmin; i.max = bmax; }
    lo.value = r[0]; hi.value = r[1]; show();
  });
  return el('div', { class: 'range' }, el('div', { class: 'range-head' }, el('label', { text: label }), out), el('div', { class: 'range-inputs' }, lo, hi));
}

function chipToggle(label, pressed, onClick, dotKey) {
  const b = el('button', { type: 'button', class: 'chip', 'aria-pressed': 'false' },
    dotKey ? el('span', { class: 'dot', style: `background:var(--c-${dotKey})` }) : null, label);
  b.addEventListener('click', onClick);
  FB.sync.push(() => b.setAttribute('aria-pressed', String(pressed())));
  return b;
}

function statePicker() {
  const det = el('details', { class: 'picker' }), sum = el('summary');
  const search = el('input', { type: 'search', placeholder: 'Find a state…', 'aria-label': 'Find a state' });
  const list = el('div', { class: 'picker-list' });
  const clear = el('button', { type: 'button', text: 'Clear states', onclick: () => { F.states.clear(); filterChanged(); } });
  det.append(sum, el('div', { class: 'picker-panel' }, search, list, clear));
  let built = false;
  const build = () => {
    const states = [...S.locs.values()].filter(l => code(l.type) === 'state').map(l => ({ code: identVal(l, SYS_USPS), name: l.name }))
      .filter(s => s.code).sort((a, b) => a.name.localeCompare(b.name));
    if (!states.length) return;
    list.replaceChildren(...states.map(s => {
      const cb = el('input', { type: 'checkbox', value: s.code });
      cb.addEventListener('change', () => toggleIn(F.states, s.code));
      const lab = el('label', {}, cb, el('b', { text: s.code }), ' ' + s.name);
      lab.dataset.hay = (s.code + ' ' + s.name).toLowerCase();
      return lab;
    }));
    built = true;
  };
  search.addEventListener('input', () => {
    const q = search.value.trim().toLowerCase();
    for (const l of list.children) l.hidden = !!q && !l.dataset.hay.includes(q);
  });
  document.addEventListener('click', e => { if (!det.contains(e.target)) det.open = false; });
  det.addEventListener('keydown', e => { if (e.key === 'Escape') { det.open = false; sum.focus(); } });
  FB.sync.push(() => {
    if (!built) build();
    for (const l of list.children) l.firstChild.checked = F.states.has(l.firstChild.value);
    const n = F.states.size;
    sum.textContent = n === 0 ? 'States: all' : n <= 3 ? `States: ${[...F.states].sort().join(', ')}` : `States: ${n} selected`;
  });
  return det;
}

/* Builds the bar once; handlers.onChange runs after every filter change, handlers.onDate after the date changes. */
function buildFilterBar(handlers) {
  const bar = $('#filterbar');
  filterChanged = () => { S.mLimit = 150; S.cLimit = 200; handlers.onChange(); };   // paging restarts with each query

  let timer;
  const search = el('input', { type: 'search', placeholder: 'Search name, state or candidate…', 'aria-label': 'Search members and candidates', class: 'fb-search' });
  search.addEventListener('input', () => { clearTimeout(timer); timer = setTimeout(() => { F.q = search.value.trim(); filterChanged(); }, 120); });
  FB.sync.push(() => { if (document.activeElement !== search && search.value.trim() !== F.q) search.value = F.q; });

  const seg = el('div', { class: 'seg-toggle', role: 'group', 'aria-label': 'Chamber' },
    [['', 'All'], ['senate', 'Senate'], ['house', 'House']].map(([v, t]) => {
      const b = el('button', { type: 'button', text: t, 'aria-pressed': 'false' });
      b.addEventListener('click', () => { F.chambers = new Set(v ? [v] : []); filterChanged(); });
      FB.sync.push(() => b.setAttribute('aria-pressed', String(v ? F.chambers.size === 1 && F.chambers.has(v) : F.chambers.size !== 1)));
      return b;
    }));

  const parties = el('div', { class: 'chips', role: 'group', 'aria-label': 'Party' },
    ['D', 'R', 'I'].map(g => chipToggle(GNAME[g], () => F.groups.has(g), () => toggleIn(F.groups, g), g)));

  const asof = el('input', { type: 'date', id: 'asof', 'aria-label': 'As of date' });
  asof.addEventListener('change', () => asof.value && handlers.onDate(asof.value));
  const today = el('button', { type: 'button', text: 'Today', onclick: () => { asof.value = todayISO(); handlers.onDate(asof.value); } });

  const reset = el('button', { type: 'button', class: 'fb-reset', text: 'Reset all', onclick: () => { resetFilters(); filterChanged(); } });

  const genderSel = el('select', { 'aria-label': 'Gender' }, [['', 'Any'], ['female', 'Female'], ['male', 'Male']].map(([v, t]) => el('option', { value: v, text: t })));
  genderSel.addEventListener('change', () => { F.gender = genderSel.value; filterChanged(); });
  FB.sync.push(() => { genderSel.value = F.gender; });

  const uncSel = el('select', { 'aria-label': 'Contested or uncontested' }, [['', 'All contests'], ['exclude', 'Contested only'], ['only', 'Uncontested only']].map(([v, t]) => el('option', { value: v, text: t })));
  uncSel.addEventListener('change', () => { F.unc = uncSel.value; filterChanged(); });
  FB.sync.push(() => { uncSel.value = F.unc; });

  const linked = el('input', { type: 'checkbox', id: 'fb-linked' });
  linked.addEventListener('change', () => { F.linked = linked.checked; filterChanged(); });
  FB.sync.push(() => { linked.checked = F.linked; });

  const kinds = el('div', { class: 'chips', role: 'group', 'aria-label': 'Election kind' },
    KINDS.map(k => chipToggle(k, () => F.kinds.has(k), () => toggleIn(F.kinds, k))));

  const electionsFs = el('fieldset', { class: 'fb-group' }, el('legend', { text: 'Elections only' }),
    rangeControl('years', 'Election years', 1, v => String(v)),
    el('div', {}, el('div', { class: 'range-head' }, el('label', { text: 'Kind' })), kinds),
    rangeControl('margin', 'Margin D−R (points)', 1, v => (v > 0 ? '+' : v < 0 ? '−' : '') + Math.abs(v)),
    el('div', { class: 'fb-pair' }, uncSel, el('label', { class: 'check-inline', for: 'fb-linked' }, linked, ' Winner is a current member')));
  const membersFs = el('fieldset', { class: 'fb-group' }, el('legend', { text: 'Members only' }),
    el('div', {}, el('div', { class: 'range-head' }, el('label', { text: 'Gender' })), genderSel),
    rangeControl('age', 'Age', 1, v => String(v)),
    rangeControl('tenure', 'Years since first term', 1, v => String(v)));
  FB.sync.push(() => { electionsFs.disabled = !S.elec; });

  // "More filters" is a toggle button + a panel in the bar's normal flow (below the count line), so the live
  // counts stay visible while sliders move.
  const panel = el('div', { id: 'fb-more-panel', class: 'fb-more-grid', hidden: '' }, membersFs, electionsFs);
  const advancedCount = () => [F.gender, F.age, F.tenure, F.years, F.kinds.size, F.margin, F.unc, F.linked].filter(Boolean).length;
  const more = el('button', { type: 'button', class: 'fb-morebtn', 'aria-expanded': 'false', 'aria-controls': 'fb-more-panel' });
  const label = () => { const n = advancedCount(), open = !panel.hidden; more.textContent = `More filters${n ? ` (${n})` : ''} ${open ? '▴' : '▾'}`; more.setAttribute('aria-expanded', String(open)); };
  more.addEventListener('click', () => { panel.hidden = !panel.hidden; label(); });
  bar.addEventListener('keydown', e => { if (e.key === 'Escape' && !panel.hidden) { panel.hidden = true; label(); more.focus(); } });
  FB.sync.push(label);

  FB.countEl = el('span', { class: 'fb-count', 'aria-live': 'polite' });
  FB.chipsEl = el('div', { class: 'fb-chips' });

  bar.replaceChildren(
    el('div', { class: 'fb-row' }, search, seg, parties, statePicker(),
      el('span', { class: 'fb-date' }, el('label', { for: 'asof', text: 'As of' }), asof, today), more, reset),
    el('div', { class: 'fb-row fb-status' }, FB.countEl, FB.chipsEl), panel);
  FB.asof = asof;
}

/* Brings every control and the chip strip in line with F. Cheap; called after each change. */
function syncFilterBar(counts) {
  FB.sync.forEach(f => f());
  const chips = activeChips();
  FB.chipsEl.replaceChildren(...chips.map(c => {
    const b = el('button', { type: 'button', class: 'fchip', title: c.scope === 'both' ? 'Applies to members and elections' : `Applies to ${c.scope} only`,
      'aria-label': `Remove filter: ${c.label}` }, c.label, el('span', { 'aria-hidden': 'true', text: ' ✕' }));
    b.addEventListener('click', () => { c.remove(); filterChanged(); });
    return b;
  }));
  if (counts) {
    const m = `${fmt(counts.m)} of ${fmt(counts.mt)} members`;
    const c = counts.ct ? ` · ${fmt(counts.c)} of ${fmt(counts.ct)} contests` : '';
    FB.countEl.textContent = m + c;
  }
}
