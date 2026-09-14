const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const test = require('node:test');

const source = fs.readFileSync('static/app.js', 'utf8');
const start = source.indexOf('let timelineRequest = null;');
const end = source.indexOf("$('#timeline .section-head').insertAdjacentHTML", start);

function setup() {
  const nodes = new Map();
  const node = (selector) => {
    if (!nodes.has(selector)) nodes.set(selector, {
      value: '2026-09-14', innerHTML: 'previous data', textContent: '',
      classList: { add() {}, toggle() {} },
      insertAdjacentHTML(_, html) { this.innerHTML += html; },
    });
    return nodes.get(selector);
  };
  const calls = [];
  const context = {
    AbortController, Date, URLSearchParams,
    $: node, state: {}, escapeHtml: String,
    brandParams: (values) => new URLSearchParams(values),
    document: {
      hidden: false,
      querySelectorAll: () => [{ value: 'hutto' }],
      querySelector: () => ({ dataset: { tab: 'timeline' } }),
      addEventListener(event, handler) { this[event] = handler; },
    },
    setInterval(handler, delay) { context.timer = { handler, delay }; },
    fetch: async (url, options) => {
      calls.push({ url, options });
      return {
        ok: true, headers: { get: () => 'application/json' },
        json: async () => ({ releases: [], undefined_releases: [], omdb_configured: true }),
      };
    },
  };
  vm.createContext(context);
  vm.runInContext(source.slice(start, end), context);
  return { context, calls, node };
}

test('visible timeline polls cached data and clears releases removed by cron', async () => {
  const { context, calls, node } = setup();
  assert.equal(context.timer.delay, 60000);
  await context.loadReleaseTimeline(true);
  assert.match(calls[0].url, /refresh_metadata=0/);
  assert.equal(calls[0].options.cache, 'no-store');
  assert.equal(node('#releaseList').innerHTML, '');
  context.document.hidden = true;
  context.timer.handler();
  assert.equal(calls.length, 1);
  context.document.hidden = false;
  context.document.visibilitychange();
  assert.equal(calls.length, 2);
});

test('background errors preserve displayed data and allow retry', async () => {
  const { context, node } = setup();
  let attempts = 0;
  context.fetch = async () => { attempts++; throw new Error('offline'); };
  await context.loadReleaseTimeline(true);
  await context.loadReleaseTimeline(true);
  assert.equal(attempts, 2);
  assert.equal(node('#releaseChart').innerHTML, 'previous data');
});

test('background poll does not overlap an active request', async () => {
  const { context } = setup();
  let finish;
  let attempts = 0;
  context.fetch = () => {
    attempts++;
    return new Promise((resolve) => { finish = resolve; });
  };
  const pending = context.loadReleaseTimeline(true);
  await context.loadReleaseTimeline(true);
  assert.equal(attempts, 1);
  finish({ ok: true, headers: { get: () => 'application/json' }, json: async () => ({}) });
  await pending;
});
