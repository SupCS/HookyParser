let endingRequest = null;
let endingBrand = null;
let endingData = null;

function groupEndingMovies(data) {
  const groups = new Map();
  const items = [...data.alerts.map((item) => ({ ...item, uncertain: false })),
    ...data.unconfirmed.map((item) => ({ ...item, uncertain: true }))];
  items.forEach((item) => {
    const key = item.title.trim().toLowerCase();
    if (!groups.has(key)) groups.set(key, { title: item.title, locations: [] });
    groups.get(key).locations.push(item);
  });
  for (const group of groups.values()) group.locations.sort((a, b) => a.days_left - b.days_left || a.location_name.localeCompare(b.location_name));
  return [...groups.values()].sort((a, b) => a.locations[0].days_left - b.locations[0].days_left || a.title.localeCompare(b.title));
}

function endingRows(items) {
  return items.map((item) => {
    const date = new Date(`${item.last_date}T00:00:00Z`).toLocaleDateString('en-US', {
      weekday: 'short', month: 'short', day: 'numeric', timeZone: 'UTC',
    });
    const urgency = item.days_left === 0 ? 'Today' : item.days_left === 1 ? 'Tomorrow' : `In ${item.days_left} days`;
    const captured = new Date(item.captured_at).toLocaleString('en-US', { dateStyle: 'short', timeStyle: 'short' });
    const status = item.uncertain ? `<span class="ending-verify" title="${escapeHtml(item.reason)}">Verify date</span>` : '';
    return `<li class="ending-location${item.uncertain ? ' ending-uncertain' : ''}" title="Schedule saved: ${escapeHtml(captured)} (your local time)"><span class="ending-location-name">${escapeHtml(item.location_name)}${status}</span><time datetime="${escapeHtml(item.last_date)}">${escapeHtml(date)}</time><b class="ending-urgency">${urgency}</b></li>`;
  }).join('');
}

function renderEndingSoon(data) {
  endingData = data;
  const all = groupEndingMovies(data);
  const locationCount = new Set(all.flatMap((movie) => movie.locations.map((item) => item.location_name))).size;
  $('#endingCount').textContent = `${all.length} movie${all.length === 1 ? '' : 's'} · ${locationCount} location${locationCount === 1 ? '' : 's'}`;
  const term = $('#endingSearch').value.trim().toLowerCase();
  const matches = all.map((movie) => ({ ...movie, locations: movie.title.toLowerCase().includes(term)
    ? movie.locations : movie.locations.filter((item) => item.location_name.toLowerCase().includes(term))
  })).filter((movie) => movie.locations.length);
  $('#endingResults').textContent = term ? `${matches.length} of ${all.length} movies` : `${all.length} movie${all.length === 1 ? '' : 's'} with last shows in the next 7 days${data.unconfirmed.length ? ` · ${data.unconfirmed.length} date${data.unconfirmed.length === 1 ? '' : 's'} to verify` : ''}`;
  const cards = matches.length ? `<div class="ending-movies">${matches.map((movie) => `<article class="ending-movie"><header><h3>${escapeHtml(movie.title)}</h3><span>${movie.locations.length} location${movie.locations.length === 1 ? '' : 's'}</span></header><ul class="ending-locations">${endingRows(movie.locations)}</ul></article>`).join('')}</div>`
    : `<p class="ending-empty">${term ? 'No movies or locations match your search.' : 'No upcoming ending dates found in saved schedules.'}</p>`;
  const missing = data.missing_locations?.length
    ? `<p class="ending-empty">No upcoming saved schedules: ${data.missing_locations.map(escapeHtml).join(', ')}.</p>` : '';
  $('#endingAlerts').innerHTML = cards + missing;
}

async function loadEndingSoon() {
  endingRequest?.abort();
  const controller = new AbortController();
  endingRequest = controller;
  const brand = state.brand;
  if (endingBrand !== brand) {
    endingData = null;
    $('#endingResults').textContent = '';
    $('#endingCount').textContent = 'Checking…';
    $('#endingAlerts').innerHTML = '<p>Checking saved schedules…</p>';
    endingBrand = brand;
  }
  try {
    const response = await fetch(`/api/ending-soon?${new URLSearchParams({ brand })}`, { signal: controller.signal, cache: 'no-store' });
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || 'Could not check ending dates');
    if (controller.signal.aborted || state.brand !== brand) return;
    renderEndingSoon(data);
  } catch (error) {
    if (controller.signal.aborted) return;
    endingData = null;
    $('#endingResults').textContent = '';
    $('#endingCount').textContent = 'Check unavailable';
    $('#endingAlerts').innerHTML = `<p class="ending-empty">${escapeHtml(error.message)}. The next automatic check will retry.</p>`;
  } finally {
    if (endingRequest === controller) endingRequest = null;
  }
}

$('#endingSearch').addEventListener('input', () => { if (endingData) renderEndingSoon(endingData); });
loadEndingSoon();
setInterval(() => { if (!document.hidden && !endingRequest) loadEndingSoon(); }, 60000);
document.addEventListener('visibilitychange', () => { if (!document.hidden) loadEndingSoon(); });
