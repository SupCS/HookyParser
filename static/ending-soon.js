let endingRequest = null;
let endingBrand = null;

function endingRows(items, uncertain = false) {
  return items.map((item) => {
    const date = new Date(`${item.last_date}T00:00:00Z`).toLocaleDateString('en-US', {
      weekday: 'short', month: 'short', day: 'numeric', year: 'numeric', timeZone: 'UTC',
    });
    const urgency = item.days_left === 0 ? 'Today' : item.days_left === 1 ? 'Tomorrow' : `In ${item.days_left} days`;
    const captured = new Date(item.captured_at).toLocaleString('en-US', { dateStyle: 'short', timeStyle: 'short' });
    return `<li class="ending-row${uncertain ? ' ending-uncertain' : ''}"><div><strong>${escapeHtml(item.title)}</strong><span>${escapeHtml(item.location_name)}</span><small>${uncertain ? escapeHtml(item.reason) : 'Likely ending · later programming is available'} · snapshot ${escapeHtml(captured)}</small></div><div class="ending-date"><b>${urgency}</b><span>Last scheduled: ${escapeHtml(date)}</span></div></li>`;
  }).join('');
}

function renderEndingSoon(data) {
  $('#endingCount').textContent = `${data.alerts.length} alert${data.alerts.length === 1 ? '' : 's'}${data.unconfirmed.length ? ` · ${data.unconfirmed.length} to verify` : ''}`;
  const alerts = data.alerts.length
    ? `<ul class="ending-list">${endingRows(data.alerts)}</ul>`
    : '<p class="ending-empty">No likely endings found for the next 7 days in saved schedules.</p>';
  const uncertain = data.unconfirmed.length
    ? `<details class="ending-verification"><summary>${data.unconfirmed.length} last scheduled date${data.unconfirmed.length === 1 ? '' : 's'} to verify</summary><p>The later schedule is incomplete, unpublished, or stale. These dates may be the limit of available data; do not treat them as confirmed endings.</p><ul class="ending-list">${endingRows(data.unconfirmed, true)}</ul></details>` : '';
  const missing = data.missing_locations?.length
    ? `<p class="ending-empty">No upcoming saved schedules: ${data.missing_locations.map(escapeHtml).join(', ')}.</p>` : '';
  $('#endingAlerts').innerHTML = alerts + uncertain + missing;
}

async function loadEndingSoon() {
  endingRequest?.abort();
  const controller = new AbortController();
  endingRequest = controller;
  const brand = state.brand;
  if (endingBrand !== brand) {
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
    $('#endingCount').textContent = 'Check unavailable';
    $('#endingAlerts').innerHTML = `<p class="ending-empty">${escapeHtml(error.message)}. The next automatic check will retry.</p>`;
  } finally {
    if (endingRequest === controller) endingRequest = null;
  }
}

loadEndingSoon();
setInterval(() => { if (!document.hidden && !endingRequest) loadEndingSoon(); }, 60000);
document.addEventListener('visibilitychange', () => { if (!document.hidden) loadEndingSoon(); });
