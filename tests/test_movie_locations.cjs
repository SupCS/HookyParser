const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const test = require('node:test');
const source = fs.readFileSync('static/app.js', 'utf8');
function setup(term) {
  const nodes = new Map();
  const $ = (key) => { if (!nodes.has(key)) nodes.set(key, { value: '', textContent: '', innerHTML: '' }); return nodes.get(key); };
  $('#search').value = term;
  const movie = { title: 'Clayface', dates: [{date:'2026-11-05',showings:[{time:'7 PM',url:'https://example.com'}]}] };
  const context = { $, escapeHtml: String, state: { locationRequestedDays: 2, scheduleLocations: [
    {name:'Hutto',available_days:2,movies:[movie]},
    {name:'Addison',available_days:1,movies:[movie]},
    {name:'Empty',available_days:2,movies:[]},
    {name:'Missing',available_days:0,movies:[]},
  ] } };
  vm.createContext(context);
  vm.runInContext(source.slice(source.indexOf('function renderMovieLocations()'), source.indexOf('async function loadMovieLocations()')), context);
  context.renderMovieLocations();
  return $;
}
test('movie search lists matching locations and counts unique movies', () => {
  const $ = setup('cLaYfAcE');
  assert.equal($('#movieCount').textContent, 1);
  assert.equal($('#showingCount').textContent, 2);
  assert.match($('#movieList').innerHTML, /2 of 4 locations/);
  assert.match($('#movieList').innerHTML, /Hutto/);
  assert.match($('#movieList').innerHTML, /Addison/);
  assert.match($('#movieList').innerHTML, /7 PM/);
  assert.match($('#movieList').innerHTML, /No matching showtimes in saved schedules/);
  assert.match($('#movieList').innerHTML, /No saved schedule for these dates/);
});
test('unmatched movie reports zero results and incomplete coverage', () => {
  const $ = setup('unknown movie');
  assert.equal($('#movieCount').textContent, 0);
  assert.equal($('#showingCount').textContent, 0);
  assert.match($('#movieList').innerHTML, /0 of 4 locations/);
  assert.match($('#movieList').innerHTML, /incomplete data/);
  assert.doesNotMatch($('#movieList').innerHTML, /<details/);
});
