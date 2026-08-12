const $ = (selector) => document.querySelector(selector);
const state = { movies: [], loading: false, rangeView: false, undefinedReleases: [] };

function escapeHtml(value) {
  const node = document.createElement('span');
  node.textContent = value ?? '';
  return node.innerHTML;
}

function selectedLocationName() {
  return $('#location').selectedOptions[0]?.textContent || 'Hutto';
}

function updateLocationHeading() {
  $('#heroLocation').textContent = selectedLocationName();
  document.title = `Hooky Parser for ${selectedLocationName()}`;
}

function renderMovies() {
  const term = $('#search').value.trim().toLowerCase();
  const movies = state.movies.filter((movie) => movie.title.toLowerCase().includes(term));
  if (state.rangeView) {
    $('#movieList').innerHTML = movies.length ? movies.map((movie, index) => {
      const popularity = Number.isInteger(movie.imdb_popularity)
        ? `<span class="popularity" title="Current IMDb Popularity rank">IMDb popularity <b>#${movie.imdb_popularity.toLocaleString('en-US')}</b></span>`
        : '<span class="popularity unavailable">IMDb popularity —</span>';
      const totalShowings = movie.dates.reduce((sum, day) => sum + day.showings.length, 0);
      const days = movie.dates.map((day) => {
        const date = new Date(`${day.date}T00:00:00Z`);
        const label = date.toLocaleDateString('en-US', { weekday: 'short', month: 'short', day: 'numeric', timeZone: 'UTC' });
        return `<button class="range-day" type="button" data-show-date="${day.date}" title="Open this day's showtimes"><span class="range-day-label"><b>${escapeHtml(label)}</b></span><span class="range-day-count">${day.showings.length} showtimes · view day →</span></button>`;
      }).join('');
      return `<details class="movie" ${movies.length === 1 || index === 0 ? 'open' : ''}><summary><span class="rank">${String(index + 1).padStart(2, '0')}</span><div><h3>${escapeHtml(movie.title)}</h3><p>${movie.dates.length} day${movie.dates.length === 1 ? '' : 's'} in this window</p></div><div class="movie-badges">${popularity}<span class="count">${totalShowings} showtimes</span></div></summary><div class="range-dates">${days}</div></details>`;
    }).join('') : '<div class="empty">No movies match your search in this 14-day window.</div>';
    return;
  }
  $('#movieList').innerHTML = movies.length ? movies.map((movie, index) => {
    const popularity = Number.isInteger(movie.imdb_popularity)
      ? `<span class="popularity" title="Current IMDb Popularity rank">IMDb popularity <b>#${movie.imdb_popularity.toLocaleString('en-US')}</b></span>`
      : '<span class="popularity unavailable" title="The IMDb rank has not been fetched yet">IMDb popularity —</span>';
    return `
    <details class="movie" ${index === 0 ? 'open' : ''}>
      <summary>
        <span class="rank">${String(index + 1).padStart(2, '0')}</span>
        <div><h3>${escapeHtml(movie.title)}</h3><p>${escapeHtml(movie.showings[0]?.time)} — ${escapeHtml(movie.showings.at(-1)?.time)}</p></div>
        <div class="movie-badges">${popularity}<span class="count">${movie.showings.length} showtimes</span></div>
      </summary>
      <div class="times">${movie.showings.map((show) => `<a href="${show.url}" target="_blank" rel="noreferrer">${escapeHtml(show.time)}</a>`).join('')}</div>
    </details>`;
  }).join('') : '<div class="empty">No movies match your search.</div>';
}

async function loadSchedule(refresh = false) {
  state.rangeView = $('#date').value !== $('#dateTo').value;
  if (state.rangeView && !refresh) return loadScheduleRange();
  if (state.loading) return;
  state.loading = true;
  const button = $('#refresh');
  button.disabled = true;
  $('#status').textContent = 'Loading…';
  const query = new URLSearchParams({ location: $('#location').value, date: $('#date').value });
  if (refresh) query.set('refresh', '1');

  try {
    const response = await fetch(`/api/schedule?${query}`);
    const contentType = response.headers.get('content-type') || '';
    const data = contentType.includes('application/json')
      ? await response.json()
      : { error: `The server returned HTTP ${response.status} instead of JSON` };
    if (!response.ok) throw new Error(data.error || 'Failed to load');
    state.movies = data.movies || [];
    const showings = state.movies.reduce((sum, movie) => sum + movie.showings.length, 0);
    $('#movieCount').textContent = state.movies.length;
    $('#showingCount').textContent = showings;
    $('#average').textContent = state.movies.length ? (showings / state.movies.length).toFixed(1) : '0';
    $('#captured').textContent = data.run ? new Date(data.run.captured_at).toLocaleTimeString('en-US', { hour: '2-digit', minute: '2-digit' }) : '—';
    $('#status').textContent = refresh ? 'New snapshot saved' : 'Saved data';
    renderMovies();
    await loadHistory();
  } catch (error) {
    state.movies = [];
    $('#movieList').innerHTML = `<div class="empty">${escapeHtml(error.message)}</div>`;
    $('#status').textContent = 'Failed to load';
  } finally {
    state.loading = false;
    button.disabled = false;
  }
}

async function loadScheduleRange() {
  if (state.loading) return;
  state.loading = true;
  const button = $('#refresh');
  button.disabled = true;
  $('#status').textContent = 'Loading date range…';
  const query = new URLSearchParams({ location: $('#location').value, date_from: $('#date').value, date_to: $('#dateTo').value });
  try {
    const response = await fetch(`/api/schedule-range?${query}`);
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || 'Failed to load date range');
    state.movies = data.movies || [];
    const showings = state.movies.reduce((total, movie) => total + movie.dates.reduce((sum, day) => sum + day.showings.length, 0), 0);
    $('#movieCount').textContent = state.movies.length;
    $('#showingCount').textContent = showings;
    $('#average').textContent = state.movies.length ? (showings / state.movies.length).toFixed(1) : '0';
    $('#captured').textContent = data.captured_at ? new Date(data.captured_at).toLocaleTimeString('en-US', { hour: '2-digit', minute: '2-digit' }) : '—';
    $('#status').textContent = `${data.available_days}/${data.requested_days} days with saved data`;
    renderMovies();
  } catch (error) {
    state.movies = [];
    $('#movieList').innerHTML = `<div class="empty">${escapeHtml(error.message)}</div>`;
    $('#status').textContent = 'Failed to load';
  } finally {
    state.loading = false;
    button.disabled = false;
  }
}

async function refreshSchedules(locations, button, scopeLabel) {
  if (state.loading) return;
  state.loading = true;
  const originalHtml = button.innerHTML;
  const actionButtons = [$('#refresh'), $('#refreshAll')];
  actionButtons.forEach((item) => { item.disabled = true; });
  const config = window.HOOKY_CONFIG || { manualFutureDays: 13, todayByLocation: {} };
  const jobs = [];
  locations.forEach((location) => {
    const startValue = config.todayByLocation[location];
    if (!startValue) return;
    const [year, month, day] = startValue.split('-').map(Number);
    for (let offset = 0; offset <= config.manualFutureDays; offset += 1) {
      const target = new Date(Date.UTC(year, month - 1, day + offset));
      jobs.push({ location, date: target.toISOString().slice(0, 10) });
    }
  });

  let completed = 0;
  const failures = [];
  const queue = [...jobs];
  async function worker() {
    while (queue.length) {
      const job = queue.shift();
      try {
        const query = new URLSearchParams({ location: job.location, date: job.date, refresh: '1' });
        const response = await fetch(`/api/schedule?${query}`);
        const data = await response.json();
        if (!response.ok) throw new Error(data.error || `HTTP ${response.status}`);
      } catch (error) {
        failures.push({ ...job, error: error.message });
      } finally {
        completed += 1;
        button.textContent = `${completed}/${jobs.length}`;
        $('#status').textContent = `${scopeLabel}: ${completed} of ${jobs.length}`;
      }
    }
  }

  try {
    await Promise.all(Array.from({ length: Math.min(4, jobs.length) }, worker));
  } finally {
    state.loading = false;
    actionButtons.forEach((item) => { item.disabled = false; });
    button.innerHTML = originalHtml;
  }
  await loadSchedule(false);
  if (document.querySelector('[data-tab="comparison"]').classList.contains('active')) await loadComparison();
  if (failures.length) {
    $('#status').textContent = `Done with ${failures.length} errors`;
    console.error('Hooky refresh failures', failures);
  } else {
    $('#status').textContent = `${scopeLabel}: ${jobs.length} updated`;
  }
}

function refreshSelectedLocation() {
  return refreshSchedules([$('#location').value], $('#refresh'), 'Refreshing location');
}

function refreshAllLocations() {
  const locations = Array.from($('#location').options).map((option) => option.value);
  return refreshSchedules(locations, $('#refreshAll'), 'Refreshing all locations');
}

function renderChart(rows) {
  const chart = $('#chart');
  const points = rows;
  if (!points.length) {
    chart.innerHTML = '<div class="single-state">Refresh the data to create the first snapshot.</div>';
    return;
  }
  if (points.length === 1) {
    chart.innerHTML = `<div class="single-state"><div><b>${points[0].showing_count}</b>showtimes on ${escapeHtml(points[0].show_date)}<br>The trend line will appear once another day is available.</div></div>`;
    return;
  }

  const width = 1000, height = 230, left = 42, right = 20, top = 20, bottom = 35;
  const values = points.map((row) => row.showing_count);
  const max = Math.max(...values, 1), min = Math.min(...values, 0);
  const range = Math.max(max - min, 4);
  const x = (index) => left + index * ((width - left - right) / (points.length - 1));
  const y = (value) => top + (max - value) * ((height - top - bottom) / range);
  const coordinates = points.map((row, index) => [x(index), y(row.showing_count)]);
  const line = coordinates.map(([px, py]) => `${px},${py}`).join(' ');
  const area = `${left},${height - bottom} ${line} ${coordinates.at(-1)[0]},${height - bottom}`;
  const grids = [0, .5, 1].map((ratio) => {
    const gy = top + ratio * (height - top - bottom);
    const value = Math.round(max - ratio * range);
    return `<line class="grid-line" x1="${left}" y1="${gy}" x2="${width - right}" y2="${gy}"/><text class="chart-label" x="0" y="${gy + 4}">${value}</text>`;
  }).join('');
  const dots = coordinates.map(([px, py], index) => `<g><title>${points[index].show_date} · ${points[index].showing_count} showtimes</title><circle class="chart-dot" cx="${px}" cy="${py}" r="5"/></g>`).join('');
  const labels = points.map((row, index) => {
    if (index !== 0 && index !== points.length - 1 && index % Math.ceil(points.length / 5)) return '';
    const [year, month, day] = row.show_date.split('-');
    const label = `${day}.${month}`;
    return `<text class="chart-label" text-anchor="middle" x="${x(index)}" y="${height - 8}">${label}</text>`;
  }).join('');
  chart.innerHTML = `<svg viewBox="0 0 ${width} ${height}" preserveAspectRatio="none" role="img" aria-label="Showtime count trend">
    <defs><linearGradient id="areaGradient" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="#0009dc" stop-opacity=".16"/><stop offset="1" stop-color="#0009dc" stop-opacity="0"/></linearGradient></defs>
    ${grids}<polygon class="chart-area" points="${area}"/><polyline class="chart-line" points="${line}"/>${dots}${labels}
  </svg>`;
}

async function loadHistory() {
  try {
    const query = new URLSearchParams({ location: $('#location').value });
    if ($('#historyFrom').value) query.set('date_from', $('#historyFrom').value);
    if ($('#historyTo').value) query.set('date_to', $('#historyTo').value);
    const response = await fetch(`/api/history?${query}`);
    const rows = await response.json();
    if (!response.ok) throw new Error(rows.error || 'Could not load history');
    $('#historyRows').innerHTML = rows.slice().reverse().map((row) => {
      const movies = row.movies || [];
      const movieList = movies.map((movie) => `<li>${escapeHtml(movie.title)}</li>`).join('') || '<li>No data</li>';
      const showingBreakdown = movies.map((movie) => `<li><span>${escapeHtml(movie.title)}</span><b>${movie.showing_count}</b></li>`).join('') || '<li>No data</li>';
      return `<tr><td>${row.show_date}</td><td>${new Date(row.captured_at).toLocaleString('en-US')}</td>
        <td><span class="detail-trigger" tabindex="0">${row.movie_count}<span class="data-popover"><strong>Movies</strong><ul>${movieList}</ul></span></span></td>
        <td><span class="detail-trigger" tabindex="0">${row.showing_count}<span class="data-popover breakdown"><strong>Showtimes by movie</strong><ul>${showingBreakdown}</ul></span></span></td></tr>`;
    }).join('') || '<tr><td colspan="4">History is empty</td></tr>';
    if (rows.length) {
      const first = rows[0].show_date;
      const last = rows.at(-1).show_date;
      $('#historyRangeLabel').textContent = `${rows.length} days · ${first} — ${last}`;
    } else {
      $('#historyRangeLabel').textContent = 'No data in the selected range';
    }
    renderChart(rows);
  } catch (_) {
    $('#chart').innerHTML = '<div class="single-state">Could not load history.</div>';
  }
}

function updateCronCountdown() {
  const now = new Date();
  const next = new Date(now);
  next.setUTCMinutes(0, 0, 0);
  if (now.getUTCHours() < 12) {
    next.setUTCHours(12);
  } else {
    next.setUTCDate(next.getUTCDate() + 1);
    next.setUTCHours(0);
  }
  const totalSeconds = Math.max(0, Math.floor((next - now) / 1000));
  const hours = Math.floor(totalSeconds / 3600);
  const minutes = Math.floor((totalSeconds % 3600) / 60);
  const seconds = totalSeconds % 60;
  $('#nextRefresh').textContent = `in ${hours}:${String(minutes).padStart(2, '0')}:${String(seconds).padStart(2, '0')}`;
}

function toIsoDate(value) {
  const year = value.getFullYear();
  const month = String(value.getMonth() + 1).padStart(2, '0');
  const day = String(value.getDate()).padStart(2, '0');
  return `${year}-${month}-${day}`;
}

function setComparisonRange(type) {
  const today = new Date();
  let start = new Date(today), end = new Date(today);
  if (type === 'week') {
    const mondayOffset = (today.getDay() + 6) % 7;
    start.setDate(today.getDate() - mondayOffset);
    end = new Date(start); end.setDate(start.getDate() + 6);
  } else if (type === 'last7') {
    start.setDate(today.getDate() - 6);
  } else if (type === 'next7') {
    end.setDate(today.getDate() + 6);
  }
  $('#compareFrom').value = toIsoDate(start);
  $('#compareTo').value = toIsoDate(end);
  document.querySelectorAll('.preset').forEach((button) => button.classList.toggle('active', button.dataset.range === type));
}

async function loadComparison() {
  const dateFrom = $('#compareFrom').value;
  const dateTo = $('#compareTo').value;
  if (!dateFrom || !dateTo) return;
  const list = $('#comparisonList');
  list.innerHTML = '<div class="empty">Counting movies and showtimes…</div>';
  try {
    const query = new URLSearchParams({ date_from: dateFrom, date_to: dateTo });
    const response = await fetch(`/api/compare?${query}`);
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || 'Could not build comparison');
    const locations = data.locations || [];
    const totalShowings = locations.reduce((sum, location) => sum + location.showing_count, 0);
    const uniqueTitles = new Set(locations.flatMap((location) => location.movies.map((movie) => movie.title)));
    const activeLocations = locations.filter((location) => location.days_available > 0).length;
    $('#compareSummary').innerHTML = `<article><span>Total showtimes</span><strong>${totalShowings}</strong></article><article><span>Unique movies</span><strong>${uniqueTitles.size}</strong></article><article><span>Locations with data</span><strong>${activeLocations}/${locations.length}</strong></article>`;
    const maxShowings = Math.max(1, ...locations.map((location) => location.showing_count));
    list.innerHTML = locations.map((location) => {
      const movies = location.movies.map((movie) => `<li><span>${escapeHtml(movie.title)}</span><b>${movie.showing_count}</b>${data.single_day && movie.times.length ? `<div class="comparison-times">${movie.times.map((time) => `<em>${escapeHtml(time)}</em>`).join('')}</div>` : ''}</li>`).join('') || '<li><span>No schedule for this period</span></li>';
      return `<article class="location-card ${location.showing_count ? '' : 'no-data'}"><div class="location-card-header"><div class="location-title"><h3>${escapeHtml(location.name)}</h3><strong>${location.showing_count}<small>showtimes</small></strong></div><div class="location-meta"><span>${location.unique_movie_count} movies</span><span>${location.days_available}/${data.requested_days} days with data</span></div><div class="comparison-bar"><i style="width:${location.showing_count / maxShowings * 100}%"></i></div></div><details><summary>Movies in this period · ${location.unique_movie_count}</summary><ul class="comparison-movies">${movies}</ul></details></article>`;
    }).join('');
  } catch (error) {
    list.innerHTML = `<div class="empty">${escapeHtml(error.message)}</div>`;
    $('#compareSummary').innerHTML = '';
  }
}

async function loadReleaseTimeline() {
  const chart = $('#releaseChart');
  const list = $('#releaseList');
  const selectedLocations = Array.from(document.querySelectorAll('input[name="timeline-location"]:checked')).map((input) => input.value);
  if (!selectedLocations.length) {
    chart.innerHTML = '<div class="empty">Select at least one location.</div>';
    list.innerHTML = '';
    $('#timelineSummary').innerHTML = '';
    $('#undefinedSection').classList.add('hidden');
    return;
  }
  chart.innerHTML = '<div class="empty">Building release timeline…</div>';
  list.innerHTML = '';
  try {
    const query = new URLSearchParams();
    selectedLocations.forEach((location) => query.append('location', location));
    const response = await fetch(`/api/releases?${query}`);
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || 'Could not build release timeline');
    const releases = data.releases || [];
    const undefinedReleases = data.undefined_releases || [];
    state.undefinedReleases = undefinedReleases;
    const ranked = releases.filter((item) => Number.isInteger(item.impact_score));
    const future = releases.filter((item) => item.release_date > data.today).length;
    $('#timelineSummary').innerHTML = `<span>${releases.length} ranked releases</span><span>${future} upcoming</span><span>${undefinedReleases.length} undefined</span><span>Dates from OMDb</span>`;
    $('#undefinedSection').classList.toggle('hidden', !undefinedReleases.length);
    $('#undefinedCount').textContent = `${undefinedReleases.length} movie${undefinedReleases.length === 1 ? '' : 's'}`;
    $('#undefinedList').innerHTML = undefinedReleases.map((item, index) => {
      const poster = item.poster_url ? `<img src="${escapeHtml(item.poster_url)}" alt="" loading="lazy">` : '<div class="undefined-poster">?</div>';
      const title = item.imdb_id ? `<a href="https://www.imdb.com/title/${escapeHtml(item.imdb_id)}/" target="_blank" rel="noreferrer">${escapeHtml(item.title)}</a>` : `<strong>${escapeHtml(item.title)}</strong>`;
      const date = item.release_date ? new Date(`${item.release_date}T00:00:00Z`).toLocaleDateString('en-US', { day: 'numeric', month: 'short', year: 'numeric', timeZone: 'UTC' }) : 'Release date unavailable';
      const override = item.reason === 'OMDb match unavailable'
        ? `<button class="imdb-override" type="button" data-undefined-index="${index}">Set IMDb link</button>` : '';
      return `<article class="undefined-item">${poster}<div>${title}<span>${escapeHtml(date)}</span><small>${escapeHtml(item.reason)}</small>${override}</div></article>`;
    }).join('');
    if (!releases.length) {
      chart.innerHTML = `<div class="empty">${data.omdb_configured ? 'No ranked releases with confirmed OMDb dates were found in this window.' : 'OMDb lookup is disabled: start the server with OMDB_API_KEY to build the release timeline.'}</div>`;
      return;
    }

    const width = 1100, height = 410, left = 58, right = 30, top = 48, bottom = 46;
    const start = new Date(`${data.date_from}T00:00:00Z`);
    const end = new Date(`${data.date_to}T00:00:00Z`);
    const today = new Date(`${data.today}T00:00:00Z`);
    const duration = Math.max(1, end - start);
    const x = (date) => left + (new Date(`${date}T00:00:00Z`) - start) / duration * (width - left - right);
    const y = (score) => top + (100 - (score ?? 3)) / 100 * (height - top - bottom);
    const grids = [0, 25, 50, 75, 100].map((score) => {
      const py = y(score);
      return `<line class="release-grid" x1="${left}" y1="${py}" x2="${width - right}" y2="${py}"/><text class="release-axis-label" x="4" y="${py + 4}">${score}</text>`;
    }).join('');
    const monthTicks = [];
    const cursor = new Date(Date.UTC(start.getUTCFullYear(), start.getUTCMonth() + 1, 1));
    while (cursor <= end) {
      const px = left + (cursor - start) / duration * (width - left - right);
      monthTicks.push(`<line class="release-grid" x1="${px}" y1="${top}" x2="${px}" y2="${height - bottom}"/><text class="release-axis-label" text-anchor="middle" x="${px}" y="${height - 14}">${cursor.toLocaleDateString('en-US', { month: 'short', year: '2-digit', timeZone: 'UTC' })}</text>`);
      cursor.setUTCMonth(cursor.getUTCMonth() + 1);
    }
    const todayX = x(data.today);
    const todayLine = today >= start && today <= end
      ? `<line class="release-today" x1="${todayX}" y1="${top}" x2="${todayX}" y2="${height - bottom}"/><text class="release-axis-label" text-anchor="middle" x="${todayX}" y="${top - 13}">TODAY</text>` : '';
    const placed = [];
    const points = releases.map((item, index) => {
      let px = x(item.release_date);
      const py = y(item.impact_score);
      let offset = 0;
      while (placed.some((point) => Math.abs(point.x - px) < 16 && Math.abs(point.y - py) < 16)) {
        offset += 1;
        px = x(item.release_date) + (offset % 2 ? 1 : -1) * Math.ceil(offset / 2) * 9;
      }
      placed.push({ x: px, y: py });
      const radius = Math.min(10, 6 + Math.sqrt(item.location_count || 1) / 2);
      const classes = `release-dot${item.release_date > data.today ? ' future' : ''}${item.impact_score === null ? ' unknown' : ''}`;
      const tooltip = `${item.title} · ${item.release_date} · ${item.imdb_popularity ? `IMDb #${item.imdb_popularity}, impact ${item.impact_score}` : 'IMDb rank unavailable'}`;
      return `<g><title>${escapeHtml(tooltip)}</title><line class="release-stem" x1="${px}" y1="${height - bottom}" x2="${px}" y2="${py}"/><a href="https://www.imdb.com/title/${escapeHtml(item.imdb_id)}/" target="_blank" rel="noreferrer"><circle class="${classes}" cx="${px}" cy="${py}" r="${radius}" tabindex="0" data-release-index="${index}"/></a></g>`;
    }).join('');
    chart.innerHTML = `<svg viewBox="0 0 ${width} ${height}" role="img" aria-label="Movie release impact timeline">${grids}${monthTicks.join('')}${todayLine}${points}</svg><div class="release-tooltip" id="releaseTooltip" role="tooltip"></div>`;
    const releaseTooltip = $('#releaseTooltip');
    function showReleaseTooltip(circle) {
      const item = releases[Number(circle.dataset.releaseIndex)];
      const image = item.poster_url ? `<img src="${escapeHtml(item.poster_url)}" alt="" loading="lazy">` : '<div class="tooltip-poster-empty">No poster</div>';
      const formattedDate = new Date(`${item.release_date}T00:00:00Z`).toLocaleDateString('en-US', { day: 'numeric', month: 'long', year: 'numeric', timeZone: 'UTC' });
      releaseTooltip.innerHTML = `${image}<div><strong>${escapeHtml(item.title)}</strong><span>${escapeHtml(formattedDate)}</span><b>IMDb #${item.imdb_popularity.toLocaleString('en-US')} · Impact ${item.impact_score}</b><small>Click the point to open IMDb ↗</small></div>`;
      releaseTooltip.classList.add('visible');
      const pointRect = circle.getBoundingClientRect();
      const chartRect = chart.getBoundingClientRect();
      let tooltipLeft = pointRect.left - chartRect.left + chart.scrollLeft + pointRect.width / 2;
      const pointTop = pointRect.top - chartRect.top + chart.scrollTop;
      const openBelow = pointRect.top - chartRect.top < releaseTooltip.offsetHeight + 24;
      releaseTooltip.classList.toggle('below', openBelow);
      const tooltipTop = pointTop + (openBelow ? pointRect.height : -12);
      const halfWidth = releaseTooltip.offsetWidth / 2;
      tooltipLeft = Math.max(halfWidth + 8, Math.min(chart.scrollWidth - halfWidth - 8, tooltipLeft));
      releaseTooltip.style.left = `${tooltipLeft}px`;
      releaseTooltip.style.top = `${tooltipTop}px`;
    }
    chart.querySelectorAll('.release-dot').forEach((circle) => {
      circle.addEventListener('pointerenter', () => showReleaseTooltip(circle));
      circle.addEventListener('focus', () => showReleaseTooltip(circle));
      circle.addEventListener('pointerleave', () => releaseTooltip.classList.remove('visible'));
      circle.addEventListener('blur', () => releaseTooltip.classList.remove('visible'));
    });
    list.innerHTML = releases.map((item) => {
      const [year, month, day] = item.release_date.split('-');
      const title = item.imdb_id ? `<a href="https://www.imdb.com/title/${escapeHtml(item.imdb_id)}/" target="_blank" rel="noreferrer"><h3>${escapeHtml(item.title)}</h3></a>` : `<h3>${escapeHtml(item.title)}</h3>`;
      const rank = item.imdb_popularity ? `IMDb #${item.imdb_popularity.toLocaleString('en-US')}` : 'IMDb rank unavailable';
      return `<article class="release-item"><span class="release-date">${day}.${month}<br>${year}</span><div>${title}<p>${rank} · ${item.location_count} location${item.location_count === 1 ? '' : 's'} on first day</p></div><strong class="release-score">${item.impact_score ?? '—'}<small>impact</small></strong></article>`;
    }).join('');
  } catch (error) {
    chart.innerHTML = `<div class="empty">${escapeHtml(error.message)}</div>`;
    $('#timelineSummary').innerHTML = '';
  }
}

const timelineLocationOptions = Array.from($('#location').options).map((option) =>
  `<label><input type="checkbox" name="timeline-location" value="${escapeHtml(option.value)}" checked><span>${escapeHtml(option.textContent)}</span></label>`
).join('');
$('#timeline .section-head').insertAdjacentHTML('afterend', `<fieldset class="timeline-locations"><legend>Locations</legend><div class="timeline-location-actions"><button type="button" id="timelineSelectAll">All</button><button type="button" id="timelineClearLocations">Clear</button></div><div class="timeline-location-options">${timelineLocationOptions}</div></fieldset>`);
document.querySelectorAll('input[name="timeline-location"]').forEach((input) => input.addEventListener('change', loadReleaseTimeline));
$('#timelineSelectAll').addEventListener('click', () => {
  document.querySelectorAll('input[name="timeline-location"]').forEach((input) => { input.checked = true; });
  loadReleaseTimeline();
});
$('#timelineClearLocations').addEventListener('click', () => {
  document.querySelectorAll('input[name="timeline-location"]').forEach((input) => { input.checked = false; });
  loadReleaseTimeline();
});

$('#search').addEventListener('input', renderMovies);
$('#location').addEventListener('change', () => { updateLocationHeading(); loadSchedule(); });
$('#date').addEventListener('change', () => {
  if ($('#date').value > $('#dateTo').value) $('#dateTo').value = $('#date').value;
  $('#dateTo').min = $('#date').value;
  loadSchedule();
});
$('#dateTo').addEventListener('change', () => {
  if ($('#dateTo').value < $('#date').value) $('#dateTo').value = $('#date').value;
  loadSchedule();
});
$('#movieList').addEventListener('click', (event) => {
  const day = event.target.closest('[data-show-date]');
  if (!day) return;
  $('#date').value = day.dataset.showDate;
  $('#dateTo').value = day.dataset.showDate;
  $('#dateTo').min = day.dataset.showDate;
  loadSchedule();
});
$('#undefinedList').addEventListener('click', async (event) => {
  const button = event.target.closest('[data-undefined-index]');
  if (!button) return;
  const item = state.undefinedReleases[Number(button.dataset.undefinedIndex)];
  const imdb = window.prompt(`Enter the exact IMDb title, IMDb URL, or tt ID for “${item.title}”:`);
  if (!imdb) return;
  button.disabled = true;
  button.textContent = 'Checking…';
  async function submit(editorKey) {
    const headers = { 'Content-Type': 'application/json' };
    if (editorKey) headers['X-Collector-Key'] = editorKey;
    return fetch('/api/releases/imdb-override', {
      method: 'POST', headers, body: JSON.stringify({ title: item.title, imdb }),
    });
  }
  try {
    let editorKey = sessionStorage.getItem('hookyEditorKey') || '';
    let response = await submit(editorKey);
    if (response.status === 401) {
      editorKey = window.prompt('Enter the collector/editor key:') || '';
      if (!editorKey) throw new Error('Editor key is required');
      response = await submit(editorKey);
      if (response.ok) sessionStorage.setItem('hookyEditorKey', editorKey);
    }
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || 'Could not save IMDb link');
    await loadReleaseTimeline();
  } catch (error) {
    window.alert(error.message);
    button.disabled = false;
    button.textContent = 'Set IMDb link';
  }
});
$('#refresh').addEventListener('click', refreshSelectedLocation);
$('#refreshAll').addEventListener('click', refreshAllLocations);
$('#historyFrom').addEventListener('change', loadHistory);
$('#historyTo').addEventListener('change', loadHistory);
$('#historyAll').addEventListener('click', () => {
  $('#historyFrom').value = '';
  $('#historyTo').value = '';
  loadHistory();
});
document.querySelectorAll('.preset').forEach((button) => button.addEventListener('click', () => {
  setComparisonRange(button.dataset.range);
  loadComparison();
}));
$('#compareApply').addEventListener('click', () => {
  document.querySelectorAll('.preset').forEach((button) => button.classList.remove('active'));
  loadComparison();
});
document.querySelectorAll('.tabs button').forEach((button) => button.addEventListener('click', () => {
  document.querySelectorAll('.tabs button').forEach((item) => item.classList.toggle('active', item === button));
  document.querySelectorAll('.panel').forEach((panel) => panel.classList.toggle('hidden', panel.id !== button.dataset.tab));
  document.body.classList.toggle('compare-mode', ['comparison', 'timeline'].includes(button.dataset.tab));
  if (button.dataset.tab === 'comparison') {
    $('#heroLocation').textContent = 'All Locations';
    document.title = 'Hooky Parser — location comparison';
    loadComparison();
  } else if (button.dataset.tab === 'timeline') {
    $('#heroLocation').textContent = 'Release Radar';
    document.title = 'Hooky Parser — release timeline';
    loadReleaseTimeline();
  } else {
    updateLocationHeading();
  }
  if (button.dataset.tab === 'history') loadHistory();
}));

updateLocationHeading();
$('#dateTo').min = $('#date').value;
setComparisonRange('week');
updateCronCountdown();
setInterval(updateCronCountdown, 1000);
loadSchedule();
