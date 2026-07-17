const $ = (selector) => document.querySelector(selector);
const state = { movies: [], loading: false };

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
  $('#movieList').innerHTML = movies.length ? movies.map((movie, index) => `
    <details class="movie" ${index === 0 ? 'open' : ''}>
      <summary>
        <span class="rank">${String(index + 1).padStart(2, '0')}</span>
        <div><h3>${escapeHtml(movie.title)}</h3><p>${escapeHtml(movie.showings[0]?.time)} — ${escapeHtml(movie.showings.at(-1)?.time)}</p></div>
        <span class="count">${movie.showings.length} сеанс.</span>
      </summary>
      <div class="times">${movie.showings.map((show) => `<a href="${show.url}" target="_blank" rel="noreferrer">${escapeHtml(show.time)}</a>`).join('')}</div>
    </details>`).join('') : '<div class="empty">Фильмы по этому запросу не найдены.</div>';
}

async function loadSchedule(refresh = false) {
  if (state.loading) return;
  state.loading = true;
  const button = $('#refresh');
  button.disabled = true;
  $('#status').textContent = 'Загрузка…';
  const query = new URLSearchParams({ location: $('#location').value, date: $('#date').value });
  if (refresh) query.set('refresh', '1');

  try {
    const response = await fetch(`/api/schedule?${query}`);
    const contentType = response.headers.get('content-type') || '';
    const data = contentType.includes('application/json')
      ? await response.json()
      : { error: `Сервер вернул HTTP ${response.status} вместо JSON` };
    if (!response.ok) throw new Error(data.error || 'Ошибка загрузки');
    state.movies = data.movies || [];
    const showings = state.movies.reduce((sum, movie) => sum + movie.showings.length, 0);
    $('#movieCount').textContent = state.movies.length;
    $('#showingCount').textContent = showings;
    $('#average').textContent = state.movies.length ? (showings / state.movies.length).toFixed(1) : '0';
    $('#captured').textContent = data.run ? new Date(data.run.captured_at).toLocaleTimeString('ru', { hour: '2-digit', minute: '2-digit' }) : '—';
    $('#status').textContent = refresh ? 'Новый снимок сохранён' : 'Сохранённые данные';
    renderMovies();
    await loadHistory();
  } catch (error) {
    state.movies = [];
    $('#movieList').innerHTML = `<div class="empty">${escapeHtml(error.message)}</div>`;
    $('#status').textContent = 'Ошибка загрузки';
  } finally {
    state.loading = false;
    button.disabled = false;
  }
}

function renderChart(rows) {
  const chart = $('#chart');
  const points = rows;
  if (!points.length) {
    chart.innerHTML = '<div class="single-state">Обновите данные, чтобы появился первый снимок.</div>';
    return;
  }
  if (points.length === 1) {
    chart.innerHTML = `<div class="single-state"><div><b>${points[0].showing_count}</b>сеансов ${escapeHtml(points[0].show_date)}<br>Линия динамики появится, когда накопится следующий день.</div></div>`;
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
  const dots = coordinates.map(([px, py], index) => `<g><title>${points[index].show_date} · ${points[index].showing_count} сеансов</title><circle class="chart-dot" cx="${px}" cy="${py}" r="5"/></g>`).join('');
  const labels = points.map((row, index) => {
    if (index !== 0 && index !== points.length - 1 && index % Math.ceil(points.length / 5)) return '';
    const [year, month, day] = row.show_date.split('-');
    const label = `${day}.${month}`;
    return `<text class="chart-label" text-anchor="middle" x="${x(index)}" y="${height - 8}">${label}</text>`;
  }).join('');
  chart.innerHTML = `<svg viewBox="0 0 ${width} ${height}" preserveAspectRatio="none" role="img" aria-label="Динамика количества сеансов">
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
    if (!response.ok) throw new Error(rows.error || 'Не удалось загрузить историю');
    $('#historyRows').innerHTML = rows.slice().reverse().map((row) => {
      const movies = row.movies || [];
      const movieList = movies.map((movie) => `<li>${escapeHtml(movie.title)}</li>`).join('') || '<li>Нет данных</li>';
      const showingBreakdown = movies.map((movie) => `<li><span>${escapeHtml(movie.title)}</span><b>${movie.showing_count}</b></li>`).join('') || '<li>Нет данных</li>';
      return `<tr><td>${row.show_date}</td><td>${new Date(row.captured_at).toLocaleString('ru')}</td>
        <td><span class="detail-trigger" tabindex="0">${row.movie_count}<span class="data-popover"><strong>Фильмы</strong><ul>${movieList}</ul></span></span></td>
        <td><span class="detail-trigger" tabindex="0">${row.showing_count}<span class="data-popover breakdown"><strong>Сеансы по фильмам</strong><ul>${showingBreakdown}</ul></span></span></td></tr>`;
    }).join('') || '<tr><td colspan="4">История пока пуста</td></tr>';
    if (rows.length) {
      const first = rows[0].show_date;
      const last = rows.at(-1).show_date;
      $('#historyRangeLabel').textContent = `${rows.length} дн. · ${first} — ${last}`;
    } else {
      $('#historyRangeLabel').textContent = 'Нет данных в выбранном диапазоне';
    }
    renderChart(rows);
  } catch (_) {
    $('#chart').innerHTML = '<div class="single-state">Не удалось загрузить историю.</div>';
  }
}

function isCurrentOrFutureSelected() {
  const today = new Date();
  const localToday = `${today.getFullYear()}-${String(today.getMonth() + 1).padStart(2, '0')}-${String(today.getDate()).padStart(2, '0')}`;
  return $('#date').value >= localToday;
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
  $('#nextRefresh').textContent = `через ${hours}:${String(minutes).padStart(2, '0')}:${String(seconds).padStart(2, '0')}`;
}

$('#search').addEventListener('input', renderMovies);
$('#location').addEventListener('change', () => { updateLocationHeading(); loadSchedule(); });
$('#date').addEventListener('change', () => loadSchedule());
$('#refresh').addEventListener('click', () => loadSchedule(isCurrentOrFutureSelected()));
$('#historyFrom').addEventListener('change', loadHistory);
$('#historyTo').addEventListener('change', loadHistory);
$('#historyAll').addEventListener('click', () => {
  $('#historyFrom').value = '';
  $('#historyTo').value = '';
  loadHistory();
});
document.querySelectorAll('.tabs button').forEach((button) => button.addEventListener('click', () => {
  document.querySelectorAll('.tabs button').forEach((item) => item.classList.toggle('active', item === button));
  document.querySelectorAll('.panel').forEach((panel) => panel.classList.toggle('hidden', panel.id !== button.dataset.tab));
  if (button.dataset.tab === 'history') loadHistory();
}));

updateLocationHeading();
updateCronCountdown();
setInterval(updateCronCountdown, 1000);
loadSchedule();
