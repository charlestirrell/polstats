/* UI smoke test for the explorer. There is no JS test runner in this project, so this runs inside the page.

   How to run (with the API up and the data loaded):
     1. Open http://localhost:8000/ui/ and wait until the page has finished loading.
     2. Paste this whole file into the browser console (or load it with a <script> tag, see tests/ui/README.md).
     3. Read the result:   await window.__smoke   ->  { passed, failed: [...] }

   The checks drive the real controls and compare with the REST API where a number can be cross-checked.
   Written against the "Congress now / Election history" layout.                                              */
window.__smoke = (async () => {
  const sleep = ms => new Promise(r => setTimeout(r, ms));
  // Wait for conditions, never fixed delays: timers and rendering can be slow in a background window.
  const waitFor = async (cond, ms = 8000) => { const t0 = Date.now(); while (Date.now() - t0 < ms) { try { if (cond()) return true; } catch (_) { /* not ready */ } await sleep(40); } return false; };
  const settle = async () => { await sleep(60); await waitFor(() => !_pending); await sleep(60); };   // _pending: a render is queued
  const results = [];
  const check = (name, ok, detail) => results.push({ name, ok: !!ok, detail: ok ? undefined : detail });
  const click = el => el.dispatchEvent(new MouseEvent('click', { bubbles: true }));
  const api = async p => (await fetch(p)).json();
  const q = (s, root = document) => root.querySelector(s);          // both helpers honour a root element
  const qa = (s, root = document) => [...root.querySelectorAll(s)];
  const titles = () => qa('main h2').map(h => h.textContent);
  const cardOf = t => qa('.card').find(c => { const h = (c.querySelector('h2') || {}).textContent || ''; return t instanceof RegExp ? t.test(h) : h.startsWith(t); });
  const reset = async () => { resetFilters(); filterChanged(); await settle(); };
  const goTab = async k => { click(q(`#tab-${k}`)); await settle(); };
  const typeSearch = async v => {
    const s = q('.fb-search'); s.value = v; s.dispatchEvent(new Event('input', { bubbles: true }));
    await waitFor(() => F.q === v || F.seat);   // the search box debounces, then applies the query
    await settle();
  };
  const segs = card => qa('.seg-part', card).map(p => p.getAttribute('aria-label'));

  // wait for the page, including the (lazily loaded) election data
  for (let i = 0; i < 90 && !(typeof S !== 'undefined' && S.elec && S.rows.length); i++) await sleep(1000);
  await goTab('now'); await reset();

  // ---------------------------------------------------------------- two views
  check('V1 opens on "Congress now"', S.tab === 'now' && q('#tab-now').getAttribute('aria-selected') === 'true', S.tab);
  check('V2 current view shows current-member sections only', /CURRENT/.test(q('.pill').textContent) && !!cardOf('Directory of current members') && !cardOf('Election results') && !titles().some(t => /seats won/.test(t)), titles());
  await goTab('hist');
  check('V3 history view: tab, URL and sections', S.tab === 'hist' && /v=hist/.test(location.hash) && /HISTORY/.test(q('.pill').textContent) && !!cardOf('Election results') && !cardOf('Directory of current members'), { tab: S.tab, hash: location.hash });
  check('V4 the bar shows only the controls of the active view (history)', !q('[data-scope=now]').offsetParent && !!q('[data-scope=hist]').offsetParent);
  await goTab('now');
  check('V5 ...and of the current view', !!q('[data-scope=now]').offsetParent && !q('[data-scope=hist]').offsetParent);
  check('V6 count lines say what they count', /current members$/.test(q('.fb-count').textContent), q('.fb-count').textContent);
  F.age = [40, 60]; filterChanged(); await settle();
  const chipsNow = qa('.fchip').map(c => c.textContent);
  await goTab('hist'); const chipsHist = qa('.fchip').map(c => c.textContent);
  await goTab('now'); const chipsBack = qa('.fchip').map(c => c.textContent);
  check('V7 a members-only filter chip appears only in the current view', chipsNow.some(c => /Age/.test(c)) && !chipsHist.some(c => /Age/.test(c)) && chipsBack.some(c => /Age/.test(c)), { chipsNow, chipsHist, chipsBack });
  await reset();
  check('V8 the page title says what it is', /Elections Explorer/.test(document.title) && /Elections Explorer/.test(q('h1').textContent) && !/Verification view/.test(document.body.innerText));

  // ---------------------------------------------------------------- Senate numbers
  const sen = S.rows.filter(r => r.chamber === 'senate'), by = {}; sen.forEach(r => by[r.group] = (by[r.group] || 0) + 1);
  const held = S.rows.filter(r => r.holdover);
  check('S1 the Senate has 100 members: 53 R, 45 D, 2 I', sen.length === 100 && by.R === 53 && by.D === 45 && by.I === 2, by);
  check('S2 held-over senators are named and flagged', held.length >= 1 && held.every(r => r.chamber === 'senate' && r.holdover), held.map(r => r.name));
  const apiSen = (await api(`/OfficeTerm?chamber=senate&active-on=${S.asOf}&_count=0`)).total;
  check('S3 the API counts only the terms in force; the UI adds exactly the held-over members', apiSen + held.length === sen.length, { api: apiSen, held: held.length, ui: sen.length });
  const comp = cardOf('Chamber composition');
  check('S4 majority markers (51 / 218) are drawn', qa('.majority', comp).map(m => m.textContent).join('|') === 'Majority: 51|Majority: 218', qa('.majority', comp).map(m => m.textContent));
  check('S5 vacant House seats are drawn and counted', segs(comp).some(a => /^Vacant: \d+/.test(a)), segs(comp));
  check('S6 a note explains the held-over seats', /held-over/.test(comp.textContent), comp.textContent.slice(0, 200));
  click(qa('button', comp).find(b => b.textContent === 'By caucus')); await settle();
  const caucusSegs = segs(cardOf('Chamber composition'));
  check('S7 "By caucus" counts independents with the party they join (47 D, 53 R)', caucusSegs.some(a => /^Democratic: 47/.test(a)) && caucusSegs.some(a => /^Republican: 53/.test(a)), caucusSegs);
  click(qa('button', cardOf('Chamber composition')).find(b => b.textContent === 'By party')); await settle();
  check('S8 the tile reports the hold-overs', /held over/.test(qa('.tile').map(t => t.textContent).join(' ')));
  check('S9 the directory badges held-over members', qa('.badge').some(b => b.textContent === 'held over'));

  // ---------------------------------------------------------------- seats and district codes
  const seatOf = async text => { await reset(); await typeSearch(text); return F.seat; };
  check('T1 "TX-12" is understood as a seat', (await seatOf('TX-12')) === 'TX-12' && F.q === '', F.seat);
  for (const t of ['tx12', 'TX 12', 'Texas 12th', 'texas district 12']) check(`T2 "${t}" -> TX-12`, (await seatOf(t)) === 'TX-12', F.seat);
  check('T3 at-large seats: "vt-al" and "Vermont at-large"', (await seatOf('vt-al')) === 'VT-AL' && (await seatOf('Vermont at-large')) === 'VT-AL', F.seat);
  check('T4 "TX senate" -> the state\'s Senate seats', (await seatOf('TX senate')) === 'TX-S', F.seat);
  await reset(); await typeSearch('TX-99');
  check('T5 a district that does not exist falls back to text search, not a phantom seat', F.seat === '' && F.q === 'TX-99', { seat: F.seat, q: F.q });
  await reset(); await typeSearch('Cruz');
  check('T6 plain-text search still finds people and candidates', S.rows.filter(memberMatches).some(r => r.name.includes('Cruz')) && F.seat === '', F.seat);

  await reset(); await typeSearch('TX-12');
  const seatCard = cardOf('Seat: TX-12');
  check('T7 the seat card shows the current holder and a members count of 1', !!seatCard && qa('.seat-holder', seatCard).length === 1 && /^1 of \d+ current members$/.test(q('.fb-count').textContent), q('.fb-count').textContent);
  check('T8 recent results for the seat are listed', qa('tbody tr', seatCard).length >= 3, qa('tbody tr', seatCard).length);
  click(qa('.link-btn', seatCard).find(b => /full election history/.test(b.textContent))); await settle();
  check('T9 "full election history" opens the history view filtered to that seat only', S.tab === 'hist' && S.fsums.length > 5 && S.fsums.every(c => c.district === 'TX-12'), { tab: S.tab, n: S.fsums.length });

  // member profile <-> elections <-> seat
  await goTab('now'); await reset(); await typeSearch('Cruz');
  const cruz = S.rows.find(r => r.name.includes('Cruz')); openDetail(cruz);
  const dlg = q('#detail');
  await waitFor(() => /Terms \(/.test(dlg.textContent));
  check('P1 a member profile lists the elections they won', /Elections won in the data \(\d+\)/.test(dlg.textContent) && qa('tbody tr.clickable', dlg).length >= 2, dlg.textContent.slice(0, 160));
  click(qa('tbody tr.clickable', dlg)[0]); await waitFor(() => /Candidates \(/.test(dlg.textContent));
  check('P2 a row opens that contest', /general/.test(dlg.querySelector('h2').textContent) && /Candidates \(\d+\)/.test(dlg.textContent), dlg.querySelector('h2').textContent);
  check('P3 the contest links back to the winner and to the seat', qa('.bridges .link-btn', dlg).length === 2, qa('.bridges .link-btn', dlg).map(b => b.textContent));
  click(qa('.bridges .link-btn', dlg)[1]); await settle();
  check('P4 "see every election for TX Senate" sets the seat and the view', F.seat === 'TX-S' && S.tab === 'hist' && !dlg.open, { seat: F.seat, tab: S.tab });
  await goTab('now'); await reset();

  // find-a-seat picker
  const picker = qa('.picker').find(p => /seat/i.test(p.querySelector('summary').textContent));
  const [stSel, seatSel] = qa('select', picker);
  stSel.value = 'PA'; stSel.dispatchEvent(new Event('change', { bubbles: true }));
  seatSel.value = 'PA-7'; seatSel.dispatchEvent(new Event('change', { bubbles: true })); await settle();
  check('T10 the seat picker sets a seat', F.seat === 'PA-7' && /Seat: PA-7/.test(picker.querySelector('summary').textContent), F.seat);
  await reset();
  check('T11 Reset all clears the seat', F.seat === '' && /Find a seat/.test(picker.querySelector('summary').textContent));

  // ---------------------------------------------------------------- history view details
  await goTab('hist'); await reset();
  check('B1 one card per chamber, no chamber dropdown inside any card', cardOf('Senate: seats won') && cardOf('House: seats won') && qa('.card select[aria-label=Chamber]').length === 0, titles());
  const ys = q('select[aria-label="Election year"]'); ys.value = '2022'; ys.dispatchEvent(new Event('change', { bubbles: true })); await settle();
  check('B2 the single Election year control drives the margin charts', F.years && F.years[0] === 2022 && !!cardOf(/^2022 Senate contests/) && !!cardOf(/^2022 House districts/), titles());
  check('B3 there is no second year selector inside the cards', qa('.card select[aria-label="Election year"]').length === 0);
  await reset();
  check('B4 Reset all returns the year control to "All years"', q('select[aria-label="Election year"]').value === '' && F.years === null);
  check('B5 ...and the margin charts follow the latest year, not a stale choice', !!cardOf(/^2024 Senate contests/), titles());
  click(qa('.fb-row .seg-toggle button').find(b => b.textContent === 'Senate')); await settle();
  check('B6 the Senate filter leaves only Senate cards', !titles().some(t => /^House/.test(t) || /House districts/.test(t)) && titles().some(t => /^Senate/.test(t)), titles());
  await reset();
  const rows = qa('tbody tr', cardOf('Election results')).slice(0, 8).map(r => [r.children[0].textContent, r.children[1].textContent, r.children[2].textContent]);
  const sameYear = rows.filter(r => r[0] === '2024');
  const senateFirst = sameYear.findIndex(r => r[1] === 'house') === -1 || sameYear.findIndex(r => r[1] === 'senate') < sameYear.findIndex(r => r[1] === 'house');
  const senStates = sameYear.filter(r => r[1] === 'senate').map(r => r[2]);
  check('B7 the results table opens in a natural order: newest first, Senate before House, states A to Z', sameYear.length >= 6 && senateFirst && senStates.every((s, i) => i === 0 || senStates[i - 1] <= s), rows);
  const y0 = qa('th button', cardOf('Election results')).find(b => b.textContent.startsWith('Year')); click(y0); await settle();
  const asc = qa('tbody tr', cardOf('Election results')).slice(0, 3).map(r => r.children[0].textContent);
  check('B8 sorting by year flips direction', asc.every(y => y === '1976'), asc);
  await reset();

  // ---------------------------------------------------------------- notes, coverage and wording
  const notes = q('#data-notes');
  check('N1 data notes sit at the bottom, closed by default', !!notes && !notes.querySelector('details').open && notes === q('main').lastElementChild, notes && notes.querySelector('details').open);
  check('N2 no quality-check banner above the content', !qa('main > *').slice(0, 5).some(n => n.id === 'data-notes'));
  click(q('#notes-link')); await settle();
  check('N3 the header link opens the notes', notes.querySelector('details').open);
  const nt = notes.textContent;
  check('N4 the notes state sources, coverage and the "computed, not official" caveat', /congress-legislators/.test(nt) && /MIT Election Data/.test(nt) && /fetched/.test(nt) && /not official or certified/.test(nt) && /held over/.test(nt), nt.slice(0, 200));
  check('N5 all automated checks are present', qa('.check', notes).length >= 12, qa('.check', notes).length);
  notes.querySelector('details').open = false;
  await goTab('now');
  check('C1 the current view says where it comes from and what it leaves out', /Source:/.test(q('.coverage').textContent) && /fetched/.test(q('.coverage').textContent) && /not reflected/.test(q('.coverage').textContent), q('.coverage').textContent);
  await goTab('hist');
  check('C2 the history view states its range and what is NOT included', /Not included:/.test(q('.coverage').textContent) && /after 2024/.test(q('.coverage').textContent) && /not official or certified/.test(q('.coverage').textContent), q('.coverage').textContent);
  check('C3 history tiles reconcile: D + R + others + (no-winner) <= contests', (() => { const t = qa('.tile .tile-value').map(x => Number(x.textContent.replace(/,/g, ''))); return t[2] + t[3] + t[4] <= t[0]; })());
  await goTab('now');

  // ---------------------------------------------------------------- hash and sanitising
  const st = filtersFromHash('#v=hist&seat=TX-12&yrs=2020:2024&ch=house');
  check('H1 a shared link restores view, seat and filters', st.view === 'hist' && F.seat === 'TX-12' && F.years.join() === '2020,2024' && [...F.chambers].join() === 'house', { st, seat: F.seat });
  filtersFromHash('#v=evil&seat=<img src=x>&ch=<script>&seat=TX-1;drop');
  check('H2 hostile values in the link are ignored', F.seat === '' && F.chambers.size === 0, { seat: F.seat });
  resetFilters(); filterChanged(); await settle();

  // ---------------------------------------------------------------- a past date
  const asof = q('#asof'); asof.value = '2020-01-15'; asof.dispatchEvent(new Event('change', { bubbles: true })); await waitFor(() => S.asOf === '2020-01-15' && !!q('.scope-note')); await settle();
  check('D1 a past date warns that only today\'s members are loaded, and points to the history view', /Past date/.test(q('.scope-note').textContent) && !!q('.scope-note .link-btn'), q('.scope-note') && q('.scope-note').textContent);
  check('D2 no vacancy or majority claims are made for a past date', qa('.majority').length === 0, qa('.majority').length);
  asof.value = todayISO(); asof.dispatchEvent(new Event('change', { bubbles: true })); await waitFor(() => S.asOf === todayISO() && qa('.majority').length === 2); await settle();
  check('D3 back to today restores them', qa('.majority').length === 2);

  await reset();
  const failed = results.filter(r => !r.ok);
  return { passed: results.length - failed.length, total: results.length, failed };
})();
