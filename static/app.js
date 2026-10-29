// static/app.js
(() => {
  'use strict';

  const $ = (sel, root = document) => root.querySelector(sel);
  const $$ = (sel, root = document) => Array.from(root.querySelectorAll(sel));

  const esc = (value) =>
    String(value === null || value === undefined ? '' : value).replace(/[&<>"']/g, (ch) => ({
      '&': '&amp;',
      '<': '&lt;',
      '>': '&gt;',
      '"': '&quot;',
      "'": '&#39;',
    }[ch]));

  const TITLES = {
    dashboard: ['Dashboard', 'overview'],
    queue: ['Кезек', 'live queue'],
    history: ['Тарих', 'completed tasks'],
    settings: ['Параметрлер', 'configuration'],
    terminal: ['Терминал', 'live stdout'],
    logs: ['Логтар', 'system.log'],
  };

  const STATUS = {
    pending: ['Күтуде', 'b-pending'],
    downloading: ['Жүктелуде', 'b-active'],
    processing: ['Өңделуде', 'b-active'],
    done: ['Дайын', 'b-done'],
    failed: ['Сәтсіз', 'b-failed'],
    skipped: ['Өткізілді', 'b-skipped'],
  };

  const TYPE_LABEL = { string: 'STRING', number: 'NUMBER', boolean: 'BOOL' };

  const state = {
    data: null,
    view: (location.hash || '#dashboard').replace('#', ''),
    settings: null,
    historyFilter: 'all',
    logs: [],
    logsLoadedAt: 0,
    connected: false,
  };

  // ---------------------------------------------------------------- utils
  function fmtBytes(value) {
    let n = Number(value) || 0;
    if (n < 1024) return `${Math.round(n)} B`;
    const units = ['KB', 'MB', 'GB', 'TB'];
    let i = -1;
    do {
      n /= 1024;
      i += 1;
    } while (n >= 1024 && i < units.length - 1);
    return `${n.toFixed(1)} ${units[i]}`;
  }

  function fmtSpeed(value) {
    const n = Number(value) || 0;
    if (n <= 0) return '—';
    return `${fmtBytes(n)}/s`;
  }

  function fmtTime(iso) {
    if (!iso) return '—';
    const d = new Date(iso);
    if (Number.isNaN(d.getTime())) return '—';
    return d.toLocaleTimeString('kk-KZ', { hour12: false });
  }

  function shortUrl(url) {
    const value = String(url || '');
    return value.length > 64 ? `${value.slice(0, 61)}…` : value;
  }

  function badge(status) {
    const meta = STATUS[status] || ['—', 'b-pending'];
    return `<span class="badge ${meta[1]}">${meta[0]}</span>`;
  }

  function toast(message, isError = false) {
    const el = $('#toast');
    el.textContent = message;
    el.classList.toggle('error', !!isError);
    el.classList.remove('hidden');
    clearTimeout(toast._timer);
    toast._timer = setTimeout(() => el.classList.add('hidden'), 2600);
  }

  // ---------------------------------------------------------------- navigation
  function setView(view) {
    if (!TITLES[view]) view = 'dashboard';
    state.view = view;
    if (location.hash !== `#${view}`) location.hash = view;

    $$('.nav-item').forEach((btn) => btn.classList.toggle('active', btn.dataset.view === view));
    $$('.view').forEach((sec) => sec.classList.toggle('active', sec.id === `view-${view}`));

    const meta = TITLES[view];
    $('#page-title').textContent = meta[0];
    $('#page-sub').textContent = meta[1];

    if (view === 'settings' && !state.settings) loadSettings();
    if (view === 'logs') loadLogs();

    render();
  }

  // ---------------------------------------------------------------- websocket
  let socket = null;

  function connect() {
    const scheme = location.protocol === 'https:' ? 'wss' : 'ws';
    try {
      socket = new WebSocket(`${scheme}://${location.host}/ws`);
    } catch (err) {
      setTimeout(connect, 2000);
      return;
    }

    socket.onopen = () => {
      state.connected = true;
      $('#conn').classList.add('online');
      $('#conn-label').textContent = 'ONLINE';
    };

    socket.onmessage = (event) => {
      try {
        state.data = JSON.parse(event.data);
        render();
      } catch (err) {
        /* ignore malformed frames */
      }
    };

    socket.onclose = () => {
      state.connected = false;
      $('#conn').classList.remove('online');
      $('#conn-label').textContent = 'OFFLINE';
      setTimeout(connect, 2000);
    };

    socket.onerror = () => {
      if (socket) socket.close();
    };
  }

  // ---------------------------------------------------------------- rendering
  function render() {
    const d = state.data;
    if (!d) return;

    $('#clock').textContent = d.time || '--:--:--';
    $('#chip-active').textContent = (d.queue && d.queue.active) || 0;
    $('#chip-pending').textContent = (d.queue && d.queue.pending) || 0;
    $('#nav-queue-count').textContent =
      ((d.queue && (d.queue.pending + d.queue.active)) || 0);

    renderDashboard(d);
    renderWorkers(d.workers || []);
    renderQueue(d);
    renderHistory(d);

    if (state.view === 'terminal') renderTerminal(d.terminal || []);
  }

  function renderDashboard(d) {
    const stats = d.stats || {};
    $('#stat-total').textContent = stats.total || 0;
    $('#stat-bytes').textContent = fmtBytes(stats.bytes || 0);
    $('#stat-queue').textContent = (d.queue && d.queue.pending) || 0;
    $('#stat-active').textContent = (d.queue && d.queue.active) || 0;
    $('#stat-workers').textContent = `/${(d.workers || []).length || 0}`;
    $('#stat-failed').textContent = stats.failed || 0;
    $('#stat-speed').textContent = fmtSpeed(d.avg_speed || 0);
    $('#dash-ts').textContent = d.time || '—';

    const tasks = d.recent_tasks || [];
    const body = $('#dash-tasks');
    if (!tasks.length) {
      body.innerHTML = '<tr><td colspan="5" class="empty">Деректер жоқ</td></tr>';
    } else {
      body.innerHTML = tasks
        .map(
          (t) => `<tr>
            <td class="mono muted">#${t.id}</td>
            <td class="title-cell">
              <div class="cell-title">${esc(t.video_title || '—')}</div>
              <div class="cell-url mono">${esc(shortUrl(t.url))}</div>
            </td>
            <td>${badge(t.status)}</td>
            <td class="mono">${t.worker_id === null || t.worker_id === undefined ? '—' : t.worker_id}</td>
            <td class="mono muted">${esc(fmtTime(t.finished_at || t.created_at))}</td>
          </tr>`
        )
        .join('');
    }

    renderBars(d.daily || []);
  }

  function renderWorkers(workers) {
    const el = $('#workers-strip');
    if (!workers.length) {
      el.innerHTML = '<div class="worker-card"><div class="worker-title muted">Worker пулы күтуде…</div></div>';
      return;
    }
    el.innerHTML = workers
      .map((w) => {
        const pct = Math.max(0, Math.min(100, Number(w.progress) || 0));
        const busy = w.status !== 'idle';
        const label = w.status === 'idle' ? 'бос' : w.status === 'processing' ? 'өңдеу' : 'жүктеу';
        return `<div class="worker-card ${busy ? 'busy' : ''}">
          <div class="worker-top">
            <span class="mono w-id">WORKER ${w.id}</span>
            <span class="badge ${busy ? 'b-active' : 'b-pending'}">${label}</span>
          </div>
          <div class="worker-title">${esc(w.title || '—')}</div>
          <div class="worker-bar"><div class="worker-fill" style="width:${pct}%"></div></div>
          <div class="worker-meta mono">${pct.toFixed(0)}%</div>
        </div>`;
      })
      .join('');
  }

  function renderBars(daily) {
    const el = $('#dash-bars');
    if (!daily.length) {
      el.innerHTML = '<div class="empty" style="margin:auto">Деректер жоқ</div>';
      return;
    }
    const max = Math.max(1, ...daily.map((d) => Number(d.total) || 0));
    el.innerHTML = daily
      .map((d) => {
        const total = Number(d.total) || 0;
        const height = Math.max(2, Math.round((total / max) * 100));
        return `<div class="bar-col" title="${esc(d.date)}: ${total}">
          <div class="bar-value mono">${total}</div>
          <div class="bar-track">
            <div class="bar-fill ${total ? 'has' : ''}" style="height:${height}%"></div>
          </div>
          <div class="bar-label mono">${esc(String(d.date || '').slice(5))}</div>
        </div>`;
      })
      .join('');
  }

  function renderQueue(d) {
    const tasks = d.queue_tasks || [];
    $('#queue-count').textContent = tasks.length;
    const body = $('#queue-tasks');
    if (!tasks.length) {
      body.innerHTML = '<tr><td colspan="6" class="empty">Кезек бос</td></tr>';
      return;
    }
    body.innerHTML = tasks
      .map(
        (t) => `<tr>
          <td class="mono muted">#${t.id}</td>
          <td class="title-cell">
            <div class="cell-title">${esc(t.video_title || '—')}</div>
            <div class="cell-url mono">${esc(shortUrl(t.url))}</div>
          </td>
          <td>${badge(t.status)}</td>
          <td class="mono">${t.worker_id === null || t.worker_id === undefined ? '—' : t.worker_id}</td>
          <td class="mono muted">${t.attempts || 0}</td>
          <td class="mono muted">${esc(fmtTime(t.created_at))}</td>
        </tr>`
      )
      .join('');
  }

  function renderHistory(d) {
    const all = d.history || [];
    const filtered =
      state.historyFilter === 'all'
        ? all
        : all.filter((t) => t.status === state.historyFilter);

    const body = $('#history-tasks');
    if (!filtered.length) {
      body.innerHTML = '<tr><td colspan="6" class="empty">Жазбалар жоқ</td></tr>';
      return;
    }
    body.innerHTML = filtered
      .map(
        (t) => `<tr>
          <td class="mono muted">#${t.id}</td>
          <td class="title-cell">
            <div class="cell-title">${esc(t.video_title || '—')}</div>
            <div class="cell-url mono">${esc(shortUrl(t.url))}</div>
          </td>
          <td>${badge(t.status)}</td>
          <td class="mono">${t.worker_id === null || t.worker_id === undefined ? '—' : t.worker_id}</td>
          <td class="mono muted">${t.file_size ? esc(fmtBytes(t.file_size)) : '—'}</td>
          <td class="mono muted" title="${esc(t.error_msg || '')}">${esc(fmtTime(t.finished_at || t.created_at))}</td>
        </tr>`
      )
      .join('');
  }

  function renderTerminal(lines) {
    const el = $('#terminal');
    if (!lines.length) {
      el.innerHTML = '<div class="tline t-info"><span class="tmsg">Күтуде…</span></div>';
      return;
    }
    const atBottom = el.scrollTop + el.clientHeight >= el.scrollHeight - 48;
    el.innerHTML = lines
      .map(
        (line) => `<div class="tline t-${esc(line.level || 'info')}">
          <span class="tts">${esc(line.ts || '')}</span>
          <span class="tmsg">${esc(line.message || '')}</span>
        </div>`
      )
      .join('');
    if (atBottom) el.scrollTop = el.scrollHeight;
  }

  // ---------------------------------------------------------------- settings
  async function loadSettings() {
    try {
      const res = await fetch('/api/settings');
      const data = await res.json();
      state.settings = data.values || {};
      renderSettings();
    } catch (err) {
      toast('Параметрлерді жүктеу сәтсіз', true);
    }
  }

  function renderSettings() {
    const wrap = $('#settings-list');
    const settings = state.settings || {};
    const keys = Object.keys(settings);

    if (!keys.length) {
      wrap.innerHTML = '<div class="setting-row"><div class="muted">Параметрлер жоқ</div></div>';
      return;
    }

    wrap.innerHTML = keys
      .map((key) => {
        const value = settings[key];
        const type = typeof value;
        let control = '';

        if (type === 'boolean') {
          control = `<label class="switch">
            <input type="checkbox" data-key="${esc(key)}" ${value ? 'checked' : ''}>
            <span></span>
          </label>`;
        } else if (type === 'number') {
          control = `<input class="input" type="number" step="any" data-key="${esc(key)}" value="${esc(value)}">`;
        } else {
          control = `<input class="input" type="text" data-key="${esc(key)}" value="${esc(value)}">`;
        }

        return `<div class="setting-row">
          <div class="setting-key">
            <div class="k mono">${esc(key)}</div>
            <div class="t mono">${TYPE_LABEL[type] || type}</div>
          </div>
          <div class="setting-control">${control}</div>
          <button class="btn ghost small" data-save="${esc(key)}">Өзгерту</button>
        </div>`;
      })
      .join('');
  }

  async function saveSetting(key, value) {
    try {
      const res = await fetch('/api/settings', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ key, value }),
      });
      if (!res.ok) throw new Error('request failed');
      await loadSettings();
      toast(`Сақталды: ${key}`);
    } catch (err) {
      toast('Сақтау сәтсіз аяқталды', true);
    }
  }

  // ---------------------------------------------------------------- logs
  async function loadLogs() {
    try {
      const res = await fetch('/api/logs?lines=200');
      const data = await res.json();
      state.logs = data.entries || [];
      state.logsLoadedAt = Date.now();
      renderLogs();
    } catch (err) {
      toast('Логтарды жүктеу сәтсіз', true);
    }
  }

  function renderLogs() {
    const el = $('#loglist');
    if (!state.logs.length) {
      el.innerHTML = '<div class="logline"><span class="log-msg muted">Жазбалар жоқ</span></div>';
      return;
    }
    el.innerHTML = state.logs
      .map((entry) => {
        const level = String(entry.level || 'INFO').toLowerCase();
        return `<div class="logline lvl-${esc(level)}">
          <span class="log-ts mono">${esc(entry.ts || '—')}</span>
          <span class="log-lvl mono">${esc(entry.level || 'INFO')}</span>
          <span class="log-mod mono">${esc(entry.module || 'system')}</span>
          <span class="log-msg">${esc(entry.message || '')}</span>
        </div>`;
      })
      .join('');
  }

  // ---------------------------------------------------------------- modal
  let modalConfirmHandler = null;

  function closeModal() {
    $('#modal').classList.add('hidden');
    modalConfirmHandler = null;
  }

  function confirmAction(title, text, onConfirm) {
    $('#modal-title').textContent = title;
    $('#modal-text').textContent = text;
    modalConfirmHandler = onConfirm;
    $('#modal').classList.remove('hidden');
  }

  // ---------------------------------------------------------------- events
  function bindEvents() {
    $$('.nav-item').forEach((btn) =>
      btn.addEventListener('click', () => setView(btn.dataset.view))
    );

    $$('#history-filters .filter').forEach((btn) =>
      btn.addEventListener('click', () => {
        state.historyFilter = btn.dataset.filter;
        $$('#history-filters .filter').forEach((other) =>
          other.classList.toggle('active', other === btn)
        );
        render();
      })
    );

    document.addEventListener('click', (event) => {
      const saveBtn = event.target.closest('[data-save]');
      if (!saveBtn) return;

      const key = saveBtn.dataset.save;
      const control = document.querySelector(`[data-key="${key}"]`);
      if (!control) return;

      let value;
      if (control.type === 'checkbox') {
        value = control.checked;
      } else if (control.type === 'number') {
        value = Number(control.value);
        if (Number.isNaN(value)) {
          toast('Сандық мән қате', true);
          return;
        }
      } else {
        value = control.value;
      }

      const previous = state.settings ? state.settings[key] : undefined;
      confirmAction(
        'Параметрді өзгерту',
        `${key}: ${JSON.stringify(previous)} → ${JSON.stringify(value)}`,
        () => saveSetting(key, value)
      );
    });

    $('#modal-cancel').addEventListener('click', closeModal);
    $('#modal-confirm').addEventListener('click', async () => {
      const handler = modalConfirmHandler;
      closeModal();
      if (handler) await handler();
    });
    $('#modal').addEventListener('click', (event) => {
      if (event.target === $('#modal')) closeModal();
    });
    document.addEventListener('keydown', (event) => {
      if (event.key === 'Escape') closeModal();
    });

    $('#logs-refresh').addEventListener('click', () => loadLogs());

    window.addEventListener('hashchange', () => {
      const view = (location.hash || '#dashboard').replace('#', '');
      if (view !== state.view) setView(view);
    });
  }

  // ---------------------------------------------------------------- boot
  function boot() {
    bindEvents();
    setView(state.view);
    connect();

    setInterval(() => {
      if (state.view === 'logs' && Date.now() - state.logsLoadedAt > 5000) {
        loadLogs();
      }
    }, 2500);
  }

  document.addEventListener('DOMContentLoaded', boot);
})();