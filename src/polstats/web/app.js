'use strict';
/* polstats data explorer: a dependency-free verification UI over the REST API.
   All text enters the DOM via textContent (API data is untrusted). */

const SYS_PARTY = 'urn:polstats:party';
const SYS_USPS = 'https://www.usps.com/state';
const SYS_BIOGUIDE = 'https://bioguide.congress.gov';
const GROUPS = [
  { key: 'D', name: 'Democratic' },
  { key: 'R', name: 'Republican' },
  { key: 'I', name: 'Independent / other' },
  { key: 'U', name: 'No party on record' },
];
const GNAME = Object.fromEntries(GROUPS.map(g => [g.key, g.name]));
const YEAR = 365.2425 * 864e5;

const S = {
  persons: new Map(), locs: new Map(), orgs: new Map(),
  allTerms: [], termsByPerson: new Map(), counts: {},
  asOf: null, active: [], affils: [], rows: [],
  view: {}, sort: { k: 'name', dir: 1 }, filter: { q: '', chamber: '', group: '', state: '' },
  elec: null, elecYear: null, elecError: null,
};

/* ---------- tiny DOM helpers ---------- */
function el(tag, props = {}, ...kids) {
  const n = document.createElement(tag);
  for (const [k, v] of Object.entries(props)) {
    if (k === 'class') n.className = v;
    else if (k === 'text') n.textContent = v;
    else if (k.startsWith('on')) n.addEventListener(k.slice(2), v);
    else if (v !== false && v != null) n.setAttribute(k, v);
  }
  for (const c of kids.flat()) if (c != null && c !== false) n.append(c);
  return n;
}
function sv(tag, props = {}, ...kids) {
  const n = document.createElementNS('http://www.w3.org/2000/svg', tag);
  for (const [k, v] of Object.entries(props)) if (v != null) n.setAttribute(k, v);
  for (const c of kids.flat()) if (c) n.append(c);
  return n;
}
const $ = (s, r = document) => r.querySelector(s);
const fmt = n => n.toLocaleString('en-US');
const refId = ref => ref.split('/')[1];

/* ---------- API ---------- */
async function api(path) {
  const r = await fetch(path, { headers: { Accept: 'application/json' } });
  if (!r.ok) {
    let msg = r.statusText;
    try { msg = (await r.json()).issue[0].diagnostics; } catch (_) { /* keep statusText */ }
    throw new Error(`${r.status} ${path}: ${msg}`);
  }
  return r.json();
}
async function fetchAll(path) {
  let url = path + (path.includes('?') ? '&' : '?') + '_count=500';
  const items = [];
  while (url) {
    const b = await api(url);
    for (const e of b.entry || []) if (!e.search || e.search.mode !== 'include') items.push(e.resource);
    const next = (b.link || []).find(l => l.relation === 'next');
    url = next ? new URL(next.url).pathname + new URL(next.url).search : null;
  }
  return items;
}

/* ---------- loading ---------- */
async function loadStatic() {
  const [persons, locs, orgs, terms] = await Promise.all([
    fetchAll('/Person'), fetchAll('/Location'), fetchAll('/Organization'), fetchAll('/OfficeTerm')]);
  S.persons = new Map(persons.map(p => [p.id, p]));
  S.locs = new Map(locs.map(p => [p.id, p]));
  S.orgs = new Map(orgs.map(p => [p.id, p]));
  S.allTerms = terms;
  S.termsByPerson = new Map();
  for (const t of terms) {
    const k = refId(t.person.reference);
    if (!S.termsByPerson.has(k)) S.termsByPerson.set(k, []);
    S.termsByPerson.get(k).push(t);
  }
  const types = ['Person', 'Organization', 'Location', 'OfficeTerm', 'PartyAffiliation', 'Provenance',
    'Election', 'Contest', 'Candidacy', 'ContestResult'];
  const totals = await Promise.all(types.map(t => api(`/${t}?_count=0`)));
  S.counts = Object.fromEntries(types.map((t, i) => [t, totals[i].total]));
}
/* Election data is large (thousands of results), so it loads after the first render. */
async function loadElections() {
  const [elections, contests, cands, results] = await Promise.all([
    fetchAll('/Election'), fetchAll('/Contest'), fetchAll('/Candidacy'), fetchAll('/ContestResult')]);
  const E = {
    elections: new Map(elections.map(e => [e.id, e])), contests: new Map(contests.map(c => [c.id, c])),
    cands: new Map(cands.map(c => [c.id, c])), results, byContest: new Map(),
  };
  for (const r of results) {
    const k = refId(r.contest.reference);
    if (!E.byContest.has(k)) E.byContest.set(k, []);
    E.byContest.get(k).push(r);
  }
  S.elec = E;
}
async function loadAsOf(d) {
  const [active, affils] = await Promise.all([
    fetchAll(`/OfficeTerm?active-on=${d}`), fetchAll(`/PartyAffiliation?active-on=${d}`)]);
  S.asOf = d; S.active = active; S.affils = affils;
  derive();
}

/* ---------- derivation ---------- */
const code = cc => (cc && cc.coding && cc.coding[0] && cc.coding[0].code) || '';
const identVal = (res, system) => ((res.identifier || []).find(i => i.system === system) || {}).value;
function groupOf(partyCode) {
  if (!partyCode) return 'U';
  return partyCode === 'D' || partyCode === 'R' ? partyCode : 'I';
}
function derive() {
  const asOf = new Date(S.asOf + 'T00:00:00Z');
  const partyByPerson = new Map();
  for (const a of S.affils) {
    const k = refId(a.person.reference);
    if (!partyByPerson.has(k)) partyByPerson.set(k, []);
    const org = S.orgs.get(refId(a.party.reference));
    partyByPerson.get(k).push({ code: org ? identVal(org, SYS_PARTY) : null, name: org ? org.name : '?' });
  }
  S.rows = S.active.map(t => {
    const pid = refId(t.person.reference);
    const p = S.persons.get(pid) || { name: [{ text: '(missing person)' }], identifier: [] };
    const state = S.locs.get(refId(t.state.reference));
    const district = t.district ? S.locs.get(refId(t.district.reference)) : null;
    const parties = partyByPerson.get(pid) || [];
    const first = (S.termsByPerson.get(pid) || []).map(x => x.period.start).filter(Boolean).sort()[0];
    const birth = p.birthDate ? new Date(p.birthDate + 'T00:00:00Z') : null;
    const name = (p.name && (p.name[0].text || [p.name[0].given, p.name[0].family].flat().join(' '))) || pid;
    return {
      pid, term: t, person: p, name,
      chamber: code(t.chamber), office: code(t.office),
      state: state ? identVal(state, SYS_USPS) || state.name : '?', stateName: state ? state.name : '?',
      district: district ? district.name : (t.senateClass ? `Class ${t.senateClass}` : ''),
      partyCode: parties[0] ? parties[0].code : null, partyName: parties[0] ? parties[0].name : '',
      partyCount: parties.length,
      group: groupOf(parties[0] && parties[0].code),
      birth: p.birthDate || '', age: birth ? Math.floor((asOf - birth) / YEAR) : null,
      firstStart: first || '', tenure: first ? (asOf - new Date(first + 'T00:00:00Z')) / YEAR : null,
      bioguide: identVal(p, SYS_BIOGUIDE) || '',
    };
  });
}
const median = a => {
  const s = a.filter(x => x != null).sort((x, y) => x - y);
  if (!s.length) return null;
  const m = s.length >> 1;
  return s.length % 2 ? s[m] : (s[m - 1] + s[m]) / 2;
};
const countBy = (rows, f) => {
  const o = {}; for (const r of rows) { const k = f(r); o[k] = (o[k] || 0) + 1; } return o;
};

/* ---------- tooltip ---------- */
const tip = el('div', { class: 'tip', role: 'tooltip', hidden: '' });
document.body.append(tip);
function showTip(title, rows, x, y) {
  tip.replaceChildren(
    el('div', { class: 'tip-title', text: title }),
    ...rows.map(r => el('div', { class: 'tip-row' },
      el('span', { class: 'key', style: `background:var(--c-${r.key})` }),
      el('strong', { text: String(r.value) }), el('span', { class: 'tip-name', text: r.name }))));
  tip.hidden = false;
  const w = tip.offsetWidth, h = tip.offsetHeight;
  tip.style.left = Math.max(8, Math.min(x + 14, innerWidth - w - 8)) + 'px';
  tip.style.top = Math.max(8, Math.min(y + 14, innerHeight - h - 8)) + 'px';
}
const hideTip = () => { tip.hidden = true; };
function bindTip(node, hotEl, title, rows) {
  const at = e => showTip(title, rows, e.clientX, e.clientY);
  const atFocus = () => { const b = node.getBoundingClientRect(); showTip(title, rows, b.left + b.width / 2, b.top); };
  node.addEventListener('pointermove', e => { hotEl.classList.add('hot'); at(e); });
  node.addEventListener('pointerleave', () => { hotEl.classList.remove('hot'); hideTip(); });
  node.addEventListener('focus', () => { hotEl.classList.add('hot'); atFocus(); });
  node.addEventListener('blur', () => { hotEl.classList.remove('hot'); hideTip(); });
}

/* ---------- chart primitives ---------- */
const topRounded = (x, y, w, h, r) => { r = Math.min(r, w / 2, h);
  return `M${x},${y + h}V${y + r}Q${x},${y} ${x + r},${y}H${x + w - r}Q${x + w},${y} ${x + w},${y + r}V${y + h}Z`; };
const rightRounded = (x, y, w, h, r) => { r = Math.min(r, h / 2, w);
  return `M${x},${y}H${x + w - r}Q${x + w},${y} ${x + w},${y + r}V${y + h - r}Q${x + w},${y + h} ${x + w - r},${y + h}H${x}Z`; };
function niceTicks(max) {
  const raw = max / 4, mag = Math.pow(10, Math.floor(Math.log10(raw))), n = raw / mag;
  const step = (n <= 1 ? 1 : n <= 2 ? 2 : n <= 5 ? 5 : 10) * mag;
  const top = Math.ceil(max / step) * step, ticks = [];
  for (let v = 0; v <= top + 1e-9; v += step) ticks.push(v);
  return { ticks, top };
}
const plotWidth = host => Math.max(280, host.clientWidth || (host.parentElement && host.parentElement.clientWidth - 32) || 560);
const tipRows = (vals, groups) => groups.filter(g => vals[g.key] > 0).map(g => ({ key: g.key, name: g.name, value: vals[g.key] }));

/* categories: [{label, vals:{D,R,I,U}}] ; vertical stacked columns */
function stackedColumns(host, cats, groups, ariaLabel) {
  const W = plotWidth(host), H = 240, m = { l: 34, r: 8, t: 10, b: 26 };
  const iw = W - m.l - m.r, ih = H - m.t - m.b;
  const totals = cats.map(c => groups.reduce((s, g) => s + (c.vals[g.key] || 0), 0));
  const { ticks, top } = niceTicks(Math.max(1, ...totals));
  const y = v => m.t + ih - (v / top) * ih;
  const svgEl = sv('svg', { viewBox: `0 0 ${W} ${H}`, width: W, height: H, role: 'img', 'aria-label': ariaLabel });
  for (const t of ticks) {
    svgEl.append(sv('line', { x1: m.l, x2: W - m.r, y1: y(t), y2: y(t), stroke: t === 0 ? 'var(--axis)' : 'var(--grid)', 'stroke-width': 1 }));
    svgEl.append(sv('text', { x: m.l - 6, y: y(t) + 4, 'text-anchor': 'end' }, document.createTextNode(String(t))));
  }
  const slot = iw / cats.length, bw = Math.min(24, Math.max(4, slot - 6));
  const every = Math.max(1, Math.ceil(40 / slot));
  cats.forEach((c, i) => {
    const x = m.l + slot * i + (slot - bw) / 2, col = sv('g', { class: 'col' });
    const present = groups.filter(g => c.vals[g.key] > 0);
    let acc = 0;
    present.forEach((g, gi) => {
      const v = c.vals[g.key], y1 = y(acc + v), y0 = y(acc);
      const bottom = gi > 0 ? y0 - 2 : y0, h = Math.max(1, bottom - y1);
      const style = `fill:var(--c-${g.key})`;
      col.append(gi === present.length - 1 ? sv('path', { d: topRounded(x, y1, bw, h, 4), style }) : sv('rect', { x, y: y1, width: bw, height: h, style }));
      acc += v;
    });
    if (i % every === 0) svgEl.append(sv('text', { x: m.l + slot * i + slot / 2, y: H - 8, 'text-anchor': 'middle' }, document.createTextNode(c.label)));
    svgEl.append(col);
    const rows = tipRows(c.vals, groups), total = totals[i];
    const hit = sv('rect', { x: m.l + slot * i, y: m.t, width: slot, height: ih, fill: 'transparent', tabindex: 0,
      'aria-label': `${c.label}: ${total} total. ${rows.map(r => `${r.name} ${r.value}`).join(', ')}` });
    bindTip(hit, col, `${c.label} (${total} total)`, rows);
    svgEl.append(hit);
  });
  host.replaceChildren(svgEl);
}

/* horizontal stacked bars with the row total at the tip */
function stackedBars(host, cats, groups, ariaLabel) {
  const W = plotWidth(host), rowH = 26, barH = 16, m = { l: 118, r: 40, t: 4, b: 22 };
  const H = m.t + m.b + cats.length * rowH, iw = W - m.l - m.r;
  const totals = cats.map(c => groups.reduce((s, g) => s + (c.vals[g.key] || 0), 0));
  const { ticks, top } = niceTicks(Math.max(1, ...totals));
  const x = v => m.l + (v / top) * iw;
  const svgEl = sv('svg', { viewBox: `0 0 ${W} ${H}`, width: W, height: H, role: 'img', 'aria-label': ariaLabel });
  for (const t of ticks) {
    svgEl.append(sv('line', { x1: x(t), x2: x(t), y1: m.t, y2: H - m.b, stroke: t === 0 ? 'var(--axis)' : 'var(--grid)', 'stroke-width': 1 }));
    svgEl.append(sv('text', { x: x(t), y: H - 6, 'text-anchor': 'middle' }, document.createTextNode(String(t))));
  }
  cats.forEach((c, i) => {
    const cy = m.t + i * rowH + (rowH - barH) / 2, col = sv('g', { class: 'col' });
    svgEl.append(sv('text', { x: m.l - 8, y: cy + barH - 4, 'text-anchor': 'end', class: 'vlabel' }, document.createTextNode(c.label)));
    const present = groups.filter(g => c.vals[g.key] > 0);
    let acc = 0;
    present.forEach((g, gi) => {
      const v = c.vals[g.key], x0 = x(acc), x1 = x(acc + v);
      const left = gi > 0 ? x0 + 2 : x0, w = Math.max(1, x1 - left), style = `fill:var(--c-${g.key})`;
      col.append(gi === present.length - 1 ? sv('path', { d: rightRounded(left, cy, w, barH, 4), style }) : sv('rect', { x: left, y: cy, width: w, height: barH, style }));
      acc += v;
    });
    svgEl.append(col);
    svgEl.append(sv('text', { x: x(totals[i]) + 6, y: cy + barH - 4, class: 'vlabel' }, document.createTextNode(String(totals[i]))));
    const rows = tipRows(c.vals, groups);
    const hit = sv('rect', { x: 0, y: m.t + i * rowH, width: W, height: rowH, fill: 'transparent', tabindex: 0,
      'aria-label': `${c.label}: ${totals[i]} total. ${rows.map(r => `${r.name} ${r.value}`).join(', ')}` });
    bindTip(hit, col, `${c.label} (${totals[i]} total)`, rows);
    svgEl.append(hit);
  });
  host.replaceChildren(svgEl);
}

/* tall segmented bar (one whole split into <= 4 parts) */
function segmentedBar(title, counts, groups) {
  const total = groups.reduce((s, g) => s + (counts[g.key] || 0), 0);
  const present = groups.filter(g => counts[g.key] > 0);
  const label = `${title}: ${present.map(g => `${g.name} ${counts[g.key]}`).join(', ')}`;
  const bar = el('div', { class: 'seg', role: 'img', 'aria-label': label });
  for (const g of present) {
    const pct = Math.round((counts[g.key] / total) * 100);
    const part = el('div', { class: 'seg-part', tabindex: 0, style: `flex:${counts[g.key]} 1 0;background:var(--c-${g.key})`, 'aria-label': `${g.name}: ${counts[g.key]} (${pct}%)` });
    part.dataset.label = String(counts[g.key]);
    bindTip(part, part, title, [{ key: g.key, name: `${g.name} (${pct}%)`, value: counts[g.key] }]);
    bar.append(part);
  }
  const node = el('div', { class: 'seg-row' }, el('div', { class: 'seg-row-head' }, el('span', { text: title }), el('span', { text: `${total} total` })), bar);
  node._afterAttach = () => { for (const p of bar.children) if (p.offsetWidth >= 40) p.textContent = p.dataset.label; };
  return node;
}

/* ---------- cards ---------- */
function legend(groups) {
  return el('ul', { class: 'legend', 'aria-label': 'Legend' },
    groups.map(g => el('li', {}, el('span', { class: 'swatch', style: `background:var(--c-${g.key})` }), g.name)));
}
function tableEl(cols, rows) {
  return el('table', {},
    el('thead', {}, el('tr', {}, cols.map(c => el('th', { class: c.num ? 'num' : '', scope: 'col', text: c.h })))),
    el('tbody', {}, rows.map(r => el('tr', {}, r.map((v, i) => el('td', { class: cols[i].num ? 'num' : '', text: String(v) }))))));
}
/* card with Chart/Table twin toggle. draw(host) renders the chart once the card is in the DOM. */
function card(id, title, sub, legendEl, draw, table) {
  const plot = el('div', { class: 'plot' }), tbl = el('div', { class: 'tablewrap', hidden: '' }, table);
  const bC = el('button', { type: 'button', text: 'Chart' }), bT = el('button', { type: 'button', text: 'Table' });
  const set = v => {
    S.view[id] = v; plot.hidden = v !== 'chart'; tbl.hidden = v !== 'table';
    bC.setAttribute('aria-pressed', v === 'chart'); bT.setAttribute('aria-pressed', v === 'table');
  };
  bC.onclick = () => set('chart'); bT.onclick = () => set('table');
  const node = el('section', { class: 'card' },
    el('div', { class: 'card-head' }, el('div', {}, el('h2', { text: title }), el('p', { class: 'card-sub', text: sub })),
      el('div', { class: 'seg-toggle', role: 'group', 'aria-label': `${title} view` }, bC, bT)),
    legendEl, plot, tbl);
  set(S.view[id] || 'chart');
  return { node, draw: () => draw(plot) };
}

/* ---------- sections ---------- */
const groupsIn = rows => GROUPS.filter(g => g.key !== 'U' || rows.some(r => r.group === 'U'));
const histogram = (rows, value, lo, step, hiLabelFrom, count) => {
  const cats = [];
  for (let i = 0; i < count; i++) {
    const a = lo + i * step, last = i === count - 1;
    cats.push({ label: last ? `${a}+` : step === 1 ? String(a) : `${a}–${a + step - 1}`, vals: { D: 0, R: 0, I: 0, U: 0 } });
  }
  for (const r of rows) {
    const v = value(r); if (v == null) continue;
    const i = Math.min(count - 1, Math.max(0, Math.floor((v - lo) / step)));
    cats[i].vals[r.group]++;
  }
  return cats;
};
const catTable = (cats, groups, first) => tableEl(
  [{ h: first }, ...groups.map(g => ({ h: g.name, num: 1 })), { h: 'Total', num: 1 }],
  cats.map(c => [c.label, ...groups.map(g => c.vals[g.key] || 0), groups.reduce((s, g) => s + (c.vals[g.key] || 0), 0)]));

function tilesSection(rows) {
  const sen = rows.filter(r => r.chamber === 'senate'), rep = rows.filter(r => r.office === 'representative');
  const nonvoting = rows.filter(r => r.chamber === 'house' && r.office !== 'representative');
  const tile = (label, value, sub) => el('div', { class: 'tile' }, el('div', { class: 'tile-label', text: label }),
    el('div', { class: 'tile-value', text: value }), sub ? el('div', { class: 'tile-sub', text: sub }) : null);
  const ma = median(rows.map(r => r.age)), mt = median(rows.map(r => r.tenure));
  return el('div', { class: 'tiles' },
    tile('Members', fmt(rows.length), `as of ${S.asOf}`),
    tile('Senators', fmt(sen.length), 'of 100 seats'),
    tile('House voting members', fmt(rep.length), 'of 435 seats'),
    tile('Delegates / commissioner', fmt(nonvoting.length), 'non-voting'),
    tile('Median age', ma == null ? '–' : ma.toFixed(0), 'years'),
    tile('Median time since first term', mt == null ? '–' : mt.toFixed(1), 'years (first term start)'));
}

function compositionCard(rows) {
  const groups = groupsIn(rows);
  const sen = countBy(rows.filter(r => r.chamber === 'senate'), r => r.group);
  const rep = countBy(rows.filter(r => r.office === 'representative'), r => r.group);
  const sBar = segmentedBar('Senate', sen, groups), hBar = segmentedBar('House (voting members)', rep, groups);
  const nodes = [sBar, hBar];
  const table = tableEl([{ h: 'Chamber' }, ...groups.map(g => ({ h: g.name, num: 1 })), { h: 'Total', num: 1 }],
    [['Senate', sen], ['House (voting)', rep]].map(([n, c]) => [n, ...groups.map(g => c[g.key] || 0), groups.reduce((s, g) => s + (c[g.key] || 0), 0)]));
  const c = card('comp', 'Chamber composition', 'Party of each member on the selected date. Independents are shown separately.', legend(groups),
    host => { host.replaceChildren(...nodes); nodes.forEach(n => n._afterAttach && n._afterAttach()); }, table);
  return c;
}

function stateCard(rows) {
  const groups = groupsIn(rows), byState = new Map();
  for (const r of rows) {
    if (!byState.has(r.stateName)) byState.set(r.stateName, { label: r.stateName, vals: { D: 0, R: 0, I: 0, U: 0 } });
    byState.get(r.stateName).vals[r.group]++;
  }
  const all = [...byState.values()].map(c => ({ ...c, total: groups.reduce((s, g) => s + c.vals[g.key], 0) }))
    .sort((a, b) => b.total - a.total || a.label.localeCompare(b.label));
  const top = all.slice(0, 15);
  return card('state', 'Delegation size by state', `Top 15 of ${all.length} states and territories by seats held (House + Senate). Table view lists all.`,
    legend(groups), host => stackedBars(host, top, groups, 'Stacked bars: seats by state and party'),
    catTable(all, groups, 'State / territory'));
}

function ageCard(rows) {
  const groups = groupsIn(rows), cats = histogram(rows, r => r.age, 25, 5, null, 14);
  return card('age', 'Age distribution', 'Members by age on the selected date, 5-year bins, stacked by party.', legend(groups),
    host => stackedColumns(host, cats, groups, 'Stacked columns: members by age band and party'), catTable(cats, groups, 'Age'));
}
function tenureCard(rows) {
  const groups = groupsIn(rows), cats = histogram(rows, r => r.tenure, 0, 4, null, 11);
  return card('tenure', 'Years since first term began', 'Time since the start of the member\'s earliest recorded term (not continuous service), 4-year bins.', legend(groups),
    host => stackedColumns(host, cats, groups, 'Stacked columns: members by years since first term and party'), catTable(cats, groups, 'Years'));
}

/* ---------- elections ---------- */
const PARTY_NAME = { D: 'Democratic', R: 'Republican', I: 'Independent', L: 'Libertarian', G: 'Green', other: 'Other' };
const BUCKET = /^(|OTHER|OTHERS|SCATTERING|WRITE-IN|\(WRITE-IN, AGGREGATED\)|\(UNNAMED\))$/;
const leftRounded = (x, y, w, h, r) => { r = Math.min(r, h / 2, w);
  return `M${x + w},${y}H${x + r}Q${x},${y} ${x},${y + r}V${y + h - r}Q${x},${y + h} ${x + r},${y + h}H${x + w}Z`; };
const pct = v => (v * 100).toFixed(1) + '%';
const signed = v => (v > 0 ? '+' : v < 0 ? '−' : '') + Math.abs(v).toFixed(1);

/* one summary per contest, derived from the API's Contest + ContestResult + Candidacy resources */
function contestSummaries() {
  const E = S.elec, out = [];
  for (const c of E.contests.values()) {
    const el_ = E.elections.get(refId(c.election.reference));
    const st = S.locs.get(refId(c.state.reference));
    const dist = c.district ? S.locs.get(refId(c.district.reference)) : null;
    const rs = (E.byContest.get(c.id) || []).map(r => {
      const cand = E.cands.get(refId(r.candidacy.reference)) || { name: '?' };
      return { r, cand, name: cand.name, votes: r.votes, share: r.voteShare, party: r.partyCode || 'other', outcome: code(r.outcome) };
    }).sort((a, b) => b.votes - a.votes);
    const named = rs.filter(x => !BUCKET.test(x.name.toUpperCase()));
    const topOf = p => named.find(x => x.party === p);
    const d = topOf('D'), rep = topOf('R');
    out.push({
      id: c.id, year: el_ ? el_.year : 0, kind: el_ ? code(el_.kind) : '', special: !!c.special, office: code(c.office),
      state: st ? identVal(st, SYS_USPS) || st.name : '?', stateName: st ? st.name : '?', district: dist ? dist.name : '',
      total: c.totalVotes, reporting: c.reporting, results: rs, named,
      winner: rs.find(x => x.outcome === 'won') || null, advanced: rs.filter(x => x.outcome === 'advanced-to-runoff'),
      margin: d && rep && d.share != null && rep.share != null ? (d.share - rep.share) * 100 : null, d, rep,
    });
  }
  return out;
}

function divergingBars(host, items, ariaLabel) {
  const W = plotWidth(host), rowH = 22, barH = 14, m = { l: 118, r: 48, t: 4, b: 22 };
  const H = m.t + m.b + items.length * rowH, iw = W - m.l - m.r;
  const maxAbs = Math.max(20, Math.ceil(Math.max(...items.map(i => Math.abs(i.value))) / 20) * 20);
  const step = maxAbs <= 40 ? 10 : 20;
  const x = v => m.l + ((v + maxAbs) / (2 * maxAbs)) * iw, x0 = x(0);
  const svgEl = sv('svg', { viewBox: `0 0 ${W} ${H}`, width: W, height: H, role: 'img', 'aria-label': ariaLabel });
  for (let t = -maxAbs; t <= maxAbs; t += step) {
    svgEl.append(sv('line', { x1: x(t), x2: x(t), y1: m.t, y2: H - m.b, stroke: t === 0 ? 'var(--axis)' : 'var(--grid)', 'stroke-width': 1 }));
    svgEl.append(sv('text', { x: x(t), y: H - 6, 'text-anchor': 'middle' }, document.createTextNode(t === 0 ? '0' : (t > 0 ? '+' : '−') + Math.abs(t))));
  }
  items.forEach((it, i) => {
    const cy = m.t + i * rowH + (rowH - barH) / 2, col = sv('g', { class: 'col' }), pos = it.value >= 0;
    svgEl.append(sv('text', { x: m.l - 8, y: cy + barH - 3, 'text-anchor': 'end', class: 'vlabel' }, document.createTextNode(it.label)));
    const w = Math.max(1, Math.abs(x(it.value) - x0)), style = `fill:var(--c-${pos ? 'D' : 'R'})`;
    col.append(sv('path', { d: pos ? rightRounded(x0, cy, w, barH, 4) : leftRounded(x0 - w, cy, w, barH, 4), style }));
    svgEl.append(col);
    svgEl.append(sv('text', { x: pos ? x(it.value) + 6 : x(it.value) - 6, y: cy + barH - 3, 'text-anchor': pos ? 'start' : 'end', class: 'vlabel' }, document.createTextNode(signed(it.value))));
    const hit = sv('rect', { x: 0, y: m.t + i * rowH, width: W, height: rowH, fill: 'transparent', tabindex: 0, 'aria-label': it.aria });
    bindTip(hit, col, it.label, it.tip);
    svgEl.append(hit);
  });
  host.replaceChildren(svgEl);
}

function elecYears() {
  return [...new Set(contestSummaries().filter(c => c.winner || c.advanced.length).map(c => c.year))].sort((a, b) => a - b);
}

function seatsWonCard() {
  const sums = contestSummaries().filter(c => c.winner);
  const years = [...new Set(sums.map(c => c.year))].sort((a, b) => a - b);
  const groups = GROUPS.filter(g => g.key !== 'U');
  const cats = years.map(y => ({ label: String(y), vals: { D: 0, R: 0, I: 0, U: 0 } }));
  for (const c of sums) cats[years.indexOf(c.year)].vals[groupOf(c.winner.party)]++;
  const office = [...new Set(sums.map(c => c.office))].map(o => (o === 'senator' ? 'Senate' : 'House')).join(' + ');
  return card('seatswon', `${office} seats won, by party and election year`,
    'Regular, special and runoff elections in the data. Winners are derived (plurality; runoff pairs excluded).', legend(groups),
    host => stackedColumns(host, cats, groups, 'Stacked columns: seats won per election year by party'), catTable(cats, groups, 'Year'));
}

function marginsCard() {
  const years = elecYears();
  if (!S.elecYear || !years.includes(S.elecYear)) S.elecYear = years[years.length - 1];
  const sums = contestSummaries().filter(c => c.year === S.elecYear)
    .sort((a, b) => a.stateName.localeCompare(b.stateName) || a.kind.localeCompare(b.kind));
  const tag = c => (c.kind === 'runoff' ? ' (runoff)' : c.special ? ' (special)' : '') + (c.office === 'representative' ? ` ${c.district.replace(/^[A-Z]{2}-/, '')}` : '');
  const items = sums.filter(c => c.margin != null)
    .sort((a, b) => a.margin - b.margin)
    .map(c => ({
      label: c.stateName + tag(c), value: c.margin,
      aria: `${c.stateName}: ${c.d.name} ${pct(c.d.share)}, ${c.rep.name} ${pct(c.rep.share)}`,
      tip: [{ key: 'D', name: c.d.name, value: pct(c.d.share) }, { key: 'R', name: c.rep.name, value: pct(c.rep.share) }],
    }));
  const select = el('select', { 'aria-label': 'Election year', onchange: e => { S.elecYear = Number(e.target.value); render(); } },
    years.slice().reverse().map(y => el('option', { value: y, text: String(y), selected: y === S.elecYear ? '' : null })));
  const lg = el('div', { class: 'members-controls' }, el('label', { text: 'Election year' }), select,
    el('ul', { class: 'legend', 'aria-label': 'Legend' },
      el('li', {}, el('span', { class: 'swatch', style: 'background:var(--c-R)' }), 'Republican lead'),
      el('li', {}, el('span', { class: 'swatch', style: 'background:var(--c-D)' }), 'Democratic lead')));
  const rows = sums.map(c => [c.stateName + tag(c), c.kind + (c.special && c.kind !== 'special' ? ' (special)' : ''),
    c.winner ? c.winner.name : (c.advanced.length ? 'Runoff: ' + c.advanced.map(a => a.name).join(' / ') : '—'),
    c.winner ? PARTY_NAME[c.winner.party] || c.winner.party : '', c.winner && c.winner.share != null ? pct(c.winner.share) : '',
    c.margin == null ? 'n/a' : signed(c.margin), c.total == null ? '' : fmt(c.total), c.winner && c.winner.cand.person ? 'yes' : '']);
  return card('margins', `${S.elecYear} contests: two-party vote margin`,
    'Democratic share minus Republican share, in percentage points (top candidate of each party). Table lists every contest incl. winner and uncontested races.',
    lg, host => items.length ? divergingBars(host, items, `Diverging bars: ${S.elecYear} margins by contest`) : host.replaceChildren(el('p', { class: 'muted', text: 'No contests with both a Democratic and a Republican candidate.' })),
    tableEl([{ h: 'Contest' }, { h: 'Kind' }, { h: 'Winner' }, { h: 'Party' }, { h: 'Share', num: 1 }, { h: 'Margin D−R (pts)', num: 1 }, { h: 'Total votes', num: 1 }, { h: 'Linked to a person' }], rows));
}

function electionsSection() {
  if (S.elecError) return [el('p', { class: 'scope-note', role: 'alert' }, 'Could not load election results: ' + S.elecError)];
  if (!S.elec) return [el('section', { class: 'card' }, el('h2', { text: 'Election results' }), el('p', { class: 'card-sub', text: 'Loading election results…' }))];
  if (!S.elec.contests.size) return [el('section', { class: 'card' }, el('h2', { text: 'Election results' }),
    el('p', { class: 'card-sub', text: 'No election data loaded yet. Run the MEDSL loader (see README).' }))];
  return [seatsWonCard(), marginsCard()];
}

/* ---------- members table ---------- */
const COLS = [
  { k: 'name', h: 'Name' }, { k: 'chamber', h: 'Chamber' }, { k: 'state', h: 'State' }, { k: 'district', h: 'District / class' },
  { k: 'partyName', h: 'Party' }, { k: 'age', h: 'Age', num: 1 }, { k: 'firstStart', h: 'First term began' }, { k: 'bioguide', h: 'Bioguide' },
];
function membersCard(rows) {
  const f = S.filter;
  const states = [...new Set(rows.map(r => r.state))].sort();
  const search = el('input', { type: 'search', placeholder: 'Search name or state', 'aria-label': 'Search members', value: f.q });
  const selChamber = el('select', { 'aria-label': 'Chamber' }, el('option', { value: '', text: 'All chambers' }), el('option', { value: 'senate', text: 'Senate' }), el('option', { value: 'house', text: 'House' }));
  const selGroup = el('select', { 'aria-label': 'Party' }, el('option', { value: '', text: 'All parties' }), groupsIn(rows).map(g => el('option', { value: g.key, text: g.name })));
  const selState = el('select', { 'aria-label': 'State' }, el('option', { value: '', text: 'All states' }), states.map(s => el('option', { value: s, text: s })));
  selChamber.value = f.chamber; selGroup.value = f.group; selState.value = f.state;
  const count = el('span', { class: 'count' });
  const wrap = el('div', { class: 'tablewrap members-wrap' });

  function paint() {
    const q = f.q.trim().toLowerCase();
    let list = rows.filter(r => (!f.chamber || r.chamber === f.chamber) && (!f.group || r.group === f.group) && (!f.state || r.state === f.state)
      && (!q || r.name.toLowerCase().includes(q) || r.state.toLowerCase() === q || r.stateName.toLowerCase().includes(q)));
    const { k, dir } = S.sort;
    list = list.slice().sort((a, b) => {
      const x = a[k], y = b[k];
      if (x === y) return a.name.localeCompare(b.name);
      if (x == null || x === '') return 1;
      if (y == null || y === '') return -1;
      return (typeof x === 'number' ? x - y : String(x).localeCompare(String(y))) * dir;
    });
    count.textContent = `${fmt(list.length)} of ${fmt(rows.length)} members`;
    const head = el('tr', {}, COLS.map(c => el('th', { scope: 'col', class: c.num ? 'num' : '', 'aria-sort': S.sort.k === c.k ? (S.sort.dir > 0 ? 'ascending' : 'descending') : 'none' },
      el('button', { type: 'button', text: c.h + (S.sort.k === c.k ? (S.sort.dir > 0 ? ' ▲' : ' ▼') : ''), onclick: () => { S.sort = { k: c.k, dir: S.sort.k === c.k ? -S.sort.dir : 1 }; paint(); } }))));
    const body = list.map(r => {
      const tr = el('tr', { class: 'clickable', tabindex: 0, 'aria-label': `Open details for ${r.name}` },
        el('td', { text: r.name }), el('td', { text: r.chamber }), el('td', { text: r.state }), el('td', { text: r.district }),
        el('td', {}, el('span', { class: 'dot', style: `background:var(--c-${r.group})` }), r.partyName || '—'),
        el('td', { class: 'num', text: r.age == null ? '' : String(r.age) }), el('td', { text: r.firstStart }), el('td', { text: r.bioguide }));
      tr.onclick = () => openDetail(r);
      tr.onkeydown = e => { if (e.key === 'Enter') openDetail(r); };
      return tr;
    });
    wrap.replaceChildren(el('table', {}, el('thead', {}, head), el('tbody', {}, body)));
  }
  search.oninput = () => { f.q = search.value; paint(); };
  selChamber.onchange = () => { f.chamber = selChamber.value; paint(); };
  selGroup.onchange = () => { f.group = selGroup.value; paint(); };
  selState.onchange = () => { f.state = selState.value; paint(); };
  paint();
  return el('section', { class: 'card' },
    el('div', { class: 'card-head' }, el('div', {}, el('h2', { text: 'Members' }), el('p', { class: 'card-sub', text: 'Everyone holding a seat on the selected date. Select a row for full records.' }))),
    el('div', { class: 'members-controls' }, search, selChamber, selGroup, selState, count), wrap);
}

/* ---------- detail dialog ---------- */
async function openDetail(r) {
  const dlg = $('#detail');
  dlg.replaceChildren(el('div', { class: 'dlg' }, el('p', { text: 'Loading…' })));
  dlg.showModal();
  try {
    const affils = await fetchAll(`/PartyAffiliation?person=Person/${r.pid}`);
    const terms = (S.termsByPerson.get(r.pid) || []).slice().sort((a, b) => (a.period.start || '').localeCompare(b.period.start || ''));
    const locName = ref => { const l = ref && S.locs.get(refId(ref.reference)); return l ? l.name : ''; };
    const termTable = tableEl([{ h: 'Office' }, { h: 'Chamber' }, { h: 'State' }, { h: 'District / class' }, { h: 'Start' }, { h: 'End' }],
      terms.map(t => [code(t.office), code(t.chamber), locName(t.state), t.district ? locName(t.district) : (t.senateClass ? `Class ${t.senateClass}` : ''), t.period.start || '', t.period.end || '']));
    affils.sort((a, b) => (a.period.start || '').localeCompare(b.period.start || ''));
    const affTable = tableEl([{ h: 'Party' }, { h: 'Start' }, { h: 'End' }],
      affils.map(a => { const o = S.orgs.get(refId(a.party.reference)); return [o ? o.name : '?', a.period.start || '', a.period.end || '']; }));
    const idList = el('dl', { class: 'ids' }, (r.person.identifier || []).flatMap(i => [el('dt', { text: i.system.replace(/^https?:\/\/(www\.)?/, '') }), el('dd', { text: i.value })]));
    const links = el('p', {}, ...[[`/Person/${r.pid}`, 'Person'], [`/OfficeTerm?person=Person/${r.pid}`, 'Terms'], [`/PartyAffiliation?person=Person/${r.pid}`, 'Affiliations'], [`/Person/${r.pid}/_history`, 'History']]
      .flatMap(([h, t]) => [el('a', { href: h, target: '_blank', rel: 'noopener', text: `API: ${t}` }), ' · ']).slice(0, -1));
    dlg.replaceChildren(el('div', { class: 'dlg' },
      el('div', { class: 'dlg-head' }, el('div', {}, el('h2', { id: 'detail-title', text: r.name }),
        el('p', { class: 'card-sub', text: `${r.partyName || 'No party'} · ${r.chamber === 'senate' ? 'Senator' : 'House'} · ${r.state} ${r.district} · born ${r.birth || 'unknown'}${r.person.gender ? ' · ' + r.person.gender : ''}` })),
        el('button', { type: 'button', text: 'Close', onclick: () => dlg.close() })),
      el('div', {}, el('h3', { text: `Terms (${terms.length})` }), el('div', { class: 'tablewrap' }, termTable)),
      el('div', {}, el('h3', { text: `Party affiliations (${affils.length})` }), affTable),
      el('div', {}, el('h3', { text: 'Identifiers' }), idList), links,
      el('details', {}, el('summary', { text: 'Raw Person JSON' }), el('pre', { text: JSON.stringify(r.person, null, 2) }))));
  } catch (e) {
    dlg.replaceChildren(el('div', { class: 'dlg' }, el('p', { text: String(e.message) }), el('button', { type: 'button', text: 'Close', onclick: () => dlg.close() })));
  }
}

/* ---------- data checks ---------- */
const ICON = {
  pass: () => sv('svg', { width: 18, height: 18, viewBox: '0 0 18 18', 'aria-hidden': 'true' }, sv('circle', { cx: 9, cy: 9, r: 8, fill: 'var(--good)' }), sv('path', { d: 'M5 9.5l2.7 2.7L13 6.8', stroke: '#fff', 'stroke-width': 2, fill: 'none', 'stroke-linecap': 'round', 'stroke-linejoin': 'round' })),
  warn: () => sv('svg', { width: 18, height: 18, viewBox: '0 0 18 18', 'aria-hidden': 'true' }, sv('path', { d: 'M9 1.5l8 14H1z', fill: 'var(--warn)' }), sv('path', { d: 'M9 6.5v4.2M9 12.6v.1', stroke: '#0b0b0b', 'stroke-width': 1.8, 'stroke-linecap': 'round' })),
  fail: () => sv('svg', { width: 18, height: 18, viewBox: '0 0 18 18', 'aria-hidden': 'true' }, sv('circle', { cx: 9, cy: 9, r: 8, fill: 'var(--crit)' }), sv('path', { d: 'M6 6l6 6M12 6l-6 6', stroke: '#fff', 'stroke-width': 2, 'stroke-linecap': 'round' })),
};
function runChecks(rows) {
  const out = [], add = (level, title, detail) => out.push({ level, title, detail });
  const list = a => a.length ? a.slice(0, 12).join(', ') + (a.length > 12 ? `, … (+${a.length - 12} more)` : '') : '';

  const sen = rows.filter(r => r.chamber === 'senate'), perState = countBy(sen, r => r.state);
  const badStates = Object.entries(perState).filter(([, n]) => n !== 2).map(([s, n]) => `${s}=${n}`);
  const senStates = new Set(sen.map(r => r.state));
  const allStates = [...S.locs.values()].filter(l => code(l.type) === 'state' && identVal(l, SYS_USPS) && !['DC', 'PR', 'GU', 'VI', 'AS', 'MP'].includes(identVal(l, SYS_USPS))).map(l => identVal(l, SYS_USPS));
  const missing = allStates.filter(s => !senStates.has(s)).map(s => `${s}=0`);
  const senIssues = [...badStates, ...missing];
  add(sen.length === 100 && !senIssues.length ? 'pass' : 'warn', `Senate seats filled: ${sen.length} of 100`,
    senIssues.length ? `States without exactly 2 active senators: ${list(senIssues)}. Often a term ending on a special-election date; check the source dates.` : 'Every state has exactly 2 senators.');

  const reps = rows.filter(r => r.office === 'representative');
  add(reps.length === 435 ? 'pass' : reps.length < 435 ? 'warn' : 'fail', `House voting members: ${reps.length} of 435`,
    reps.length < 435 ? `${435 - reps.length} vacant seat(s), or the source lags behind recent changes.` : reps.length > 435 ? 'More members than seats.' : 'All seats filled.');

  const seatKey = r => r.chamber === 'senate' ? `S:${r.state}:${r.term.senateClass}` : `H:${r.state}:${r.district}`;
  const seen = countBy(rows, seatKey), dup = Object.entries(seen).filter(([, n]) => n > 1).map(([k, n]) => `${k} (${n})`);
  add(dup.length ? 'fail' : 'pass', 'No seat held twice', dup.length ? `Seats with more than one active term: ${list(dup)}` : 'Every active seat has a single holder.');

  const noParty = rows.filter(r => r.partyCount === 0).map(r => r.name), multi = rows.filter(r => r.partyCount > 1).map(r => r.name);
  add(noParty.length || multi.length ? 'fail' : 'pass', 'Exactly one active party affiliation per member',
    noParty.length || multi.length ? `${noParty.length} with none (${list(noParty)}); ${multi.length} with several (${list(multi)}).` : 'All members have one party on this date.');

  const noBio = [...S.persons.values()].filter(p => !identVal(p, SYS_BIOGUIDE)).length;
  add(noBio ? 'fail' : 'pass', 'Every person has a bioguide ID', noBio ? `${noBio} people lack one.` : `All ${fmt(S.persons.size)} people carry the cross-walk key.`);

  const noBirth = [...S.persons.values()].filter(p => !p.birthDate).length;
  add(noBirth ? 'warn' : 'pass', 'Birth dates present', noBirth ? `${noBirth} people have no birth date.` : 'All people have a birth date.');

  const inverted = S.allTerms.filter(t => t.period.start && t.period.end && t.period.end < t.period.start).length;
  add(inverted ? 'fail' : 'pass', 'Term periods are well-formed', inverted ? `${inverted} terms end before they start.` : `All ${fmt(S.allTerms.length)} terms have start <= end.`);

  const local = S.allTerms.filter(t => (!t.period.start || t.period.start <= S.asOf) && (!t.period.end || t.period.end >= S.asOf)).length;
  add(local === S.active.length ? 'pass' : 'fail', 'API "active-on" agrees with client-side date logic',
    `API returned ${S.active.length} active terms; recomputing from all ${fmt(S.allTerms.length)} terms gives ${local}.`);

  const orphans = S.allTerms.filter(t => !S.persons.has(refId(t.person.reference))).length;
  add(orphans ? 'fail' : 'pass', 'Term references resolve to people', orphans ? `${orphans} terms point at a missing Person.` : 'No dangling Person references.');

  if (S.elec && S.elec.contests.size) {
    const E = S.elec, sums = contestSummaries();
    const badSum = sums.filter(c => c.results.length && Math.abs(c.results.reduce((s, x) => s + (x.share || 0), 0) - 1) > 0.002);
    add(badSum.length ? 'fail' : 'pass', 'Election: vote shares sum to 100% in every contest',
      badSum.length ? `${badSum.length} contests off, e.g. ${list(badSum.map(c => `${c.year} ${c.stateName}`))}` : `All ${fmt(sums.length)} contests reconcile with their total votes.`);

    const noRes = sums.filter(c => !c.results.length);
    const decisive = sums.filter(c => c.kind !== 'primary');
    const wrong = decisive.filter(c => c.results.length && (c.winner ? 1 : 0) + (c.advanced.length ? 1 : 0) !== 1);
    add(noRes.length || wrong.length ? 'fail' : 'pass', 'Election: every contest has results and exactly one outcome pattern',
      noRes.length || wrong.length ? `${noRes.length} without results; ${wrong.length} without a single winner/runoff pair: ${list(wrong.map(c => `${c.year} ${c.stateName}`))}` : `${fmt(decisive.length)} contests each have one winner or a runoff pair (${fmt(sums.filter(c => c.advanced.length).length)} sent to runoff).`);

    const multiW = sums.filter(c => c.results.filter(x => x.outcome === 'won').length > 1).length;
    const linkedByPerson = new Map();
    for (const c of E.cands.values()) if (c.person) {
      const k = c.person.reference + '|' + c.contest.reference;
      linkedByPerson.set(k, (linkedByPerson.get(k) || 0) + 1);
    }
    const dupLinks = [...linkedByPerson.values()].filter(n => n > 1).length;
    add(multiW || dupLinks ? 'fail' : 'pass', 'Election: no double winners or duplicate person links', multiW || dupLinks ? `${multiW} contests with several winners; ${dupLinks} duplicate person links.` : 'Each contest has at most one winner and each person links once per contest.');

    const winners = sums.filter(c => c.winner), linked = winners.filter(c => c.winner.cand.person).length;
    add('pass', `Election: ${fmt(linked)} of ${fmt(winners.length)} winners linked to a person`,
      'Only people serving today are loaded, so winners who have since left Congress cannot link. That is expected, not an error.');

    const have = new Set([...E.cands.values()].filter(c => c.person).map(c => refId(c.person.reference)));
    const offices = new Set(sums.map(c => c.office));
    const noElection = rows.filter(r => r.chamber === 'senate' && offices.has('senator') && !have.has(r.pid)).map(r => `${r.name} (${r.state})`);
    add(noElection.length ? 'warn' : 'pass', 'Election: every sitting senator has an election in the data',
      noElection.length ? `No election record for: ${list(noElection)}. Typically appointees who have not yet faced an election in the loaded data.` : 'Every sitting senator links to at least one winning candidacy.');
    if (!offices.has('representative')) add('warn', 'Election: House results not loaded', 'Only Senate returns are loaded so far. The House file needs a Dataverse download form.');
  }
  return out;
}
function checksCard(rows) {
  const counts = el('div', { class: 'counts' }, Object.entries(S.counts).map(([t, n]) => el('div', {}, el('b', { text: fmt(n) }), el('span', { text: t }))));
  const items = runChecks(rows);
  const label = { pass: 'Pass', warn: 'Check', fail: 'Fail' };
  const worst = items.some(i => i.level === 'fail') ? 'fail' : items.some(i => i.level === 'warn') ? 'warn' : 'pass';
  return el('section', { class: 'card' },
    el('div', { class: 'card-head' }, el('div', {}, el('h2', { text: 'Data checks' }),
      el('p', { class: 'card-sub', text: `${items.filter(i => i.level === 'pass').length} of ${items.length} pass · overall: ${label[worst]}. Resources in the store:` }))),
    counts,
    el('ul', { class: 'checks' }, items.map(i => el('li', { class: 'check' }, ICON[i.level](),
      el('div', {}, el('span', { class: 'state', text: label[i.level] }), el('span', { text: i.title }), el('span', { class: 'detail', text: i.detail }))))));
}

/* ---------- render ---------- */
function render() {
  const main = $('#main'), rows = S.rows;
  main.replaceChildren();
  main.append(tilesSection(rows));
  if (S.asOf !== todayISO()) {
    main.append(el('p', { class: 'scope-note', role: 'note' }, ICON.warn(),
      el('span', {}, el('strong', { text: 'Data scope. ' }),
        'Only people serving today (and their earlier terms) are loaded. For other dates this page shows just those people, not the full Congress of that date, so seat counts and the checks below will look incomplete.')));
  }
  main.append(checksCard(rows));
  const g1 = el('div', { class: 'grid' }), g2 = el('div', { class: 'grid' });
  const cards = [compositionCard(rows), stateCard(rows), ageCard(rows), tenureCard(rows)];
  g1.append(cards[0].node, cards[1].node); g2.append(cards[2].node, cards[3].node);
  const g3 = el('div', { class: 'grid' });
  const elecCards = electionsSection();
  g3.append(...elecCards.map(c => c.node || c));
  main.append(g1, g2, g3, membersCard(rows));
  [...cards, ...elecCards.filter(c => c.draw)].forEach(c => c.draw());
}

/* ---------- boot ---------- */
const todayISO = () => { const d = new Date(); return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`; };
function banner(msg) { const b = $('#banner'); b.hidden = !msg; b.textContent = msg || ''; }
async function setDate(d) {
  const main = $('#main');
  main.classList.add('refetching'); banner('');
  try { await loadAsOf(d); render(); }
  catch (e) { banner(`Could not load data: ${e.message}`); }
  finally { main.classList.remove('refetching'); }
}
function initTheme() {
  const modes = ['auto', 'light', 'dark'], btn = $('#theme');
  let mode = 'auto';
  try { mode = localStorage.getItem('polstats-theme') || 'auto'; } catch (_) { /* storage blocked */ }
  const apply = () => {
    if (mode === 'auto') document.documentElement.removeAttribute('data-theme'); else document.documentElement.dataset.theme = mode;
    btn.textContent = `Theme: ${mode}`;
  };
  btn.onclick = () => {
    mode = modes[(modes.indexOf(mode) + 1) % 3];
    try { localStorage.setItem('polstats-theme', mode); } catch (_) { /* ignore */ }
    apply();
  };
  apply();
}
async function boot() {
  initTheme();
  const input = $('#asof'); input.value = todayISO();
  input.onchange = () => input.value && setDate(input.value);
  $('#today').onclick = () => { input.value = todayISO(); setDate(input.value); };
  $('#detail').addEventListener('click', e => { if (e.target === e.currentTarget) e.currentTarget.close(); });
  let t; addEventListener('resize', () => { clearTimeout(t); t = setTimeout(() => S.rows.length && render(), 150); });
  try { await loadStatic(); await loadAsOf(input.value); render(); }
  catch (e) { $('#main').replaceChildren(); banner(`Could not load data from the API: ${e.message}`); return; }
  try { await loadElections(); } catch (e) { S.elecError = e.message; }
  render();
}
boot();
