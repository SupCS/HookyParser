const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const test = require('node:test');

function setup() {
  const nodes = new Map();
  const node = (key) => {
    if (!nodes.has(key)) nodes.set(key, { textContent: '', innerHTML: '', value: '', addEventListener(event, handler) { this[event] = handler; } });
    return nodes.get(key);
  };
  const calls = [];
  const context = {
    AbortController, URLSearchParams, Date,
    $: node, state: { brand: 'hooky' },
    escapeHtml: (value) => String(value).replaceAll('&', '&amp;').replaceAll('<', '&lt;'),
    document: { hidden: false, addEventListener(event, handler) { this[event] = handler; } },
    setInterval(handler, delay) { context.timer = { handler, delay }; },
    fetch(url, options) { return new Promise((resolve) => calls.push({ url, options, resolve })); },
  };
  vm.createContext(context);
  vm.runInContext(fs.readFileSync('static/ending-soon.js', 'utf8'), context);
  return { context, calls, node };
}

test('ending list groups all locations and schedule limits into one movie card', () => {
  const { context, node } = setup();
  const item = { title: '<Clayface>', location_name: 'Baytown', last_date: '2026-11-06', days_left: 1, captured_at: '2026-11-05T12:00:00Z' };
  context.renderEndingSoon({ alerts: [item, { ...item, location_name: 'Hutto', days_left: 2 }],
    unconfirmed: [{ ...item, location_name: 'Waxahachie', reason: 'Later schedule missing' }], missing_locations: ['Cary'] });
  assert.equal(node('#endingCount').textContent, '1 movie · 3 locations');
  const html = node('#endingAlerts').innerHTML;
  assert.match(html, /Baytown/);
  assert.match(html, /Hutto/);
  assert.match(html, /Tomorrow/);
  assert.match(html, /In 2 days/);
  assert.match(html, /Verify date/);
  assert.equal((html.match(/class="ending-movie"/g) || []).length, 1);
  assert.equal((html.match(/<h3>/g) || []).length, 1);
  assert.match(html, /No upcoming saved schedules: Cary/);
  assert.match(html, /&lt;Clayface>/);
  assert.doesNotMatch(html, /<Clayface>/);
});

test('network change aborts old request and suppresses its late response', async () => {
  const { context, calls, node } = setup();
  context.state.brand = 'showbiz';
  const current = context.loadEndingSoon();
  assert.equal(calls[0].options.signal.aborted, true);
  assert.match(calls[1].url, /brand=showbiz/);
  calls[1].resolve({ ok: true, json: async () => ({ alerts: [], unconfirmed: [] }) });
  await current;
  calls[0].resolve({ ok: true, json: async () => ({ alerts: [{title:'OLD NETWORK'}], unconfirmed: [] }) });
  await new Promise(setImmediate);
  assert.equal(node('#endingCount').textContent, '0 movies · 0 locations');
  assert.doesNotMatch(node('#endingAlerts').innerHTML, /OLD NETWORK/);
});

test('search filters movies or locations and survives data refresh', () => {
  const { context, node } = setup();
  const item = { title: 'Clayface', location_name: 'Baytown', last_date: '2026-11-06', days_left: 1, captured_at: '2026-11-05T12:00:00Z' };
  const data = { alerts: [item, { ...item, location_name: 'Hutto' }, { ...item, title: 'Breeder' }], unconfirmed: [] };
  context.renderEndingSoon(data);
  node('#endingSearch').value = 'CLAY';
  node('#endingSearch').input();
  assert.match(node('#endingAlerts').innerHTML, /Baytown/);
  assert.match(node('#endingAlerts').innerHTML, /Hutto/);
  assert.doesNotMatch(node('#endingAlerts').innerHTML, /Breeder/);
  assert.equal(node('#endingResults').textContent, '1 of 2 movies');
  context.renderEndingSoon(data);
  assert.doesNotMatch(node('#endingAlerts').innerHTML, /Breeder/);
  node('#endingSearch').value = 'hutto';
  node('#endingSearch').input();
  assert.match(node('#endingAlerts').innerHTML, /Clayface/);
  assert.doesNotMatch(node('#endingAlerts').innerHTML, /Baytown/);
  node('#endingSearch').value = 'unknown';
  node('#endingSearch').input();
  assert.match(node('#endingAlerts').innerHTML, /No movies or locations match/);
  node('#endingSearch').value = '';
  node('#endingSearch').input();
  assert.match(node('#endingAlerts').innerHTML, /Breeder/);
});

test('failed check reports unavailable and visible timer retries', async () => {
  const { context, calls, node } = setup();
  calls[0].resolve({ ok: false, json: async () => ({ error: 'Offline' }) });
  await new Promise(setImmediate);
  assert.equal(node('#endingCount').textContent, 'Check unavailable');
  assert.equal(context.timer.delay, 60000);
  context.document.hidden = true;
  context.timer.handler();
  assert.equal(calls.length, 1);
  context.document.hidden = false;
  context.timer.handler();
  assert.equal(calls.length, 2);
});
