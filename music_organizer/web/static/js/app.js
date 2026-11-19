/* ==========================================================================
   本地音乐整理小插件 · 前端应用
   原生 JavaScript，无构建步骤。按视图渲染 + 事件委托，便于在树莓派上直接运行。
   ========================================================================== */

'use strict';

/* -------------------------------------------------------------------------
   基础工具
   ------------------------------------------------------------------------- */

const $ = (sel, root = document) => root.querySelector(sel);
const $$ = (sel, root = document) => Array.from(root.querySelectorAll(sel));

const esc = (value) =>
  String(value == null ? '' : value).replace(/[&<>"']/g, (ch) => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
  }[ch]));

const fmtTime = (seconds) => {
  const total = Math.max(0, Math.round(Number(seconds) || 0));
  const h = Math.floor(total / 3600);
  const m = Math.floor((total % 3600) / 60);
  const s = total % 60;
  if (h) return `${h}:${String(m).padStart(2, '0')}:${String(s).padStart(2, '0')}`;
  return `${String(m).padStart(2, '0')}:${String(s).padStart(2, '0')}`;
};

const fmtSize = (bytes) => {
  let value = Number(bytes) || 0;
  const units = ['B', 'KB', 'MB', 'GB', 'TB'];
  let i = 0;
  while (value >= 1024 && i < units.length - 1) { value /= 1024; i += 1; }
  return i === 0 ? `${Math.round(value)} B` : `${value.toFixed(1)} ${units[i]}`;
};

const pct = (part, total) => (total > 0 ? Math.round((part / total) * 100) : 0);

const relativeTime = (iso) => {
  if (!iso) return '';
  const then = new Date(iso).getTime();
  if (!then || Number.isNaN(then)) return iso;
  const diff = (Date.now() - then) / 1000;
  if (diff < -60) return timeUntil(iso);
  if (diff < 60) return '刚刚';
  if (diff < 3600) return `${Math.floor(diff / 60)} 分钟前`;
  if (diff < 86400) return `${Math.floor(diff / 3600)} 小时前`;
  if (diff < 172800) return '昨天';
  if (diff < 86400 * 30) return `${Math.floor(diff / 86400)} 天前`;
  const d = new Date(then);
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;
};

const timeUntil = (iso) => {
  if (!iso) return '未安排';
  const then = new Date(iso).getTime();
  if (!then || Number.isNaN(then)) return iso;
  const diff = then - Date.now();
  if (diff <= 0) return '即将';
  const mins = Math.round(diff / 60000);
  if (mins < 1) return '不到 1 分钟';
  if (mins < 60) return `${mins} 分钟后`;
  const hours = Math.round(mins / 60);
  if (hours < 24) return `${hours} 小时后`;
  const days = Math.round(hours / 24);
  if (days === 1) return '明天';
  return `${days} 天后`;
};

const debounce = (fn, ms) => {
  let timer = null;
  return (...args) => {
    clearTimeout(timer);
    timer = setTimeout(() => fn(...args), ms);
  };
};

const clone = (obj) => JSON.parse(JSON.stringify(obj || {}));

const setDeep = (obj, path, value) => {
  const parts = path.split('.');
  let node = obj;
  for (let i = 0; i < parts.length - 1; i += 1) {
    if (!node[parts[i]] || typeof node[parts[i]] !== 'object') node[parts[i]] = {};
    node = node[parts[i]];
  }
  node[parts[parts.length - 1]] = value;
};

const getDeep = (obj, path, fallback = undefined) => {
  let node = obj;
  for (const part of path.split('.')) {
    if (node == null || typeof node !== 'object' || !(part in node)) return fallback;
    node = node[part];
  }
  return node;
};

/* -------------------------------------------------------------------------
   图标
   ------------------------------------------------------------------------- */

const ICONS = {
  play: '<svg viewBox="0 0 24 24" fill="currentColor"><path d="M7 4.5v15l13-7.5z"/></svg>',
  pause: '<svg viewBox="0 0 24 24" fill="currentColor"><rect x="6" y="4" width="4" height="16" rx="1.2"/><rect x="14" y="4" width="4" height="16" rx="1.2"/></svg>',
  wrench: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M14.7 6.3a4 4 0 0 0-5.4 5.4L3 18l3 3 6.3-6.3a4 4 0 0 0 5.4-5.4l-2.6 2.6-2.4-2.4 2.6-2.6z"/></svg>',
  image: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="4" width="18" height="16" rx="2.5"/><circle cx="8.5" cy="9.5" r="1.6"/><path d="m21 16-5-5-6 6-3-3-4 4"/></svg>',
  more: '<svg viewBox="0 0 24 24" fill="currentColor"><circle cx="5" cy="12" r="1.8"/><circle cx="12" cy="12" r="1.8"/><circle cx="19" cy="12" r="1.8"/></svg>',
  list: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round"><path d="M4 6h16M4 12h16M4 18h10"/></svg>',
  check: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="m5 12 4.5 4.5L19 7"/></svg>',
  alert: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M12 3 2.5 20h19z"/><path d="M12 9v5M12 17.5v.1"/></svg>',
  lyrics: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M4 6h10M4 12h8M4 18h6"/><path d="M16 18V8l5-1v8"/><circle cx="14" cy="18" r="2"/></svg>',
  folder: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v9a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"/></svg>',
  shield: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M12 3 4 6v6c0 5 3.5 8 8 9 4.5-1 8-4 8-9V6z"/><path d="m9 12 2 2 4-4"/></svg>',
  clock: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="8.5"/><path d="M12 7v5l3 2"/></svg>',
  zap: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M13 2 4 14h6l-1 8 9-12h-6z"/></svg>',
  settings: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.6 1.6 0 0 0 .32 1.77l.06.06a2 2 0 1 1-2.83 2.83l-.06-.06A1.6 1.6 0 0 0 15 19.4a1.6 1.6 0 0 0-1 1.47V21a2 2 0 1 1-4 0v-.09A1.6 1.6 0 0 0 9 19.4a1.6 1.6 0 0 0-1.77.32l-.06.06a2 2 0 1 1-2.83-2.83l.06-.06A1.6 1.6 0 0 0 4.6 15a1.6 1.6 0 0 0-1.47-1H3a2 2 0 1 1 0-4h.09A1.6 1.6 0 0 0 4.6 9a1.6 1.6 0 0 0-.32-1.77l-.06-.06a2 2 0 1 1 2.83-2.83l.06.06A1.6 1.6 0 0 0 9 4.6a1.6 1.6 0 0 0 1-1.47V3a2 2 0 1 1 4 0v.09a1.6 1.6 0 0 0 1 1.47 1.6 1.6 0 0 0 1.77-.32l.06-.06a2 2 0 1 1 2.83 2.83l-.06.06A1.6 1.6 0 0 0 19.4 9v.09a1.6 1.6 0 0 0 1.47 1H21a2 2 0 1 1 0 4h-.09a1.6 1.6 0 0 0-1.51 1z"/></svg>',
  download: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M12 3v12"/><path d="m7 10 5 5 5-5"/><path d="M4 21h16"/></svg>',
  heart: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M20.8 4.6a5.5 5.5 0 0 0-7.8 0L12 5.7l-1.1-1.1a5.5 5.5 0 0 0-7.8 7.8l1.1 1.1L12 21l7.8-7.5 1.1-1.1a5.5 5.5 0 0 0-.1-7.8z"/></svg>',
  upload: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M12 21V9"/><path d="m7 14 5-5 5 5"/><path d="M4 3h16"/></svg>',
  refresh: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M21 12a9 9 0 1 1-2.64-6.36"/><path d="M21 3v6h-6"/></svg>',
  arrowLeft: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="m15 18-6-6 6-6"/></svg>',
  arrowRight: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="m9 18 6-6-6-6"/></svg>',
  arrowUp: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="m18 15-6-6-6 6"/></svg>',
  duplicate: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><rect x="8" y="8" width="12" height="12" rx="2"/><path d="M16 8V6a2 2 0 0 0-2-2H6a2 2 0 0 0-2 2v8a2 2 0 0 0 2 2h2"/></svg>',
  close: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M6 6l12 12M18 6 6 18"/></svg>',
};

/* -------------------------------------------------------------------------
   全局状态
   ------------------------------------------------------------------------- */

const state = {
  route: 'dashboard',
  overview: null,
  library: {
    items: [], total: 0, page: 1, page_size: 40, q: '', filter: 'all', sort: 'artist',
    counts: {}, loading: false, error: '', loaded: false, seq: 0,
  },
  artists: { items: [], loading: false, selected: null, tab: 'albums', album: null, sortSongs: 'default', sortAlbums: 'year' },
  selected: new Set(),
  settings: null,
  providersDraft: { lyrics: [], cover: [] },
  autoDraft: null,
  job: null,
  jobId: null,
  jobTimer: null,
  current: null,
  queue: [],
  queueIndex: -1,
  lyrics: { lines: [], text: '', offset: 0, active: -1, synced: false, source: '' },
  repeat: localStorage.getItem('morg.repeat') || 'list',
  shuffle: localStorage.getItem('morg.shuffle') === '1',
  folder: { path: '', parent: '', roots: [], dirs: [], audio_count: 0, mandatory: false },
  dirChoices: [],
  candidates: { data: null, kind: 'lyrics', trackId: 0, loading: false },
  trackDetail: null,
  files: {
    path: '', root: '', breadcrumbs: [], entries: [], disk: {},
    loading: false, error: '', q: '', sort: 'name', selected: new Set(),
    original: null, historyCount: 0, plan: null, dirs: [], seq: 0, searchTimer: null,
  },
  autoFixed: new Set(),
  lrcStats: null,
  lrcLoading: false,
  confirmResolve: null,
  playTimer: null,
  lastOverviewAt: 0,
  playRetry: 0,
  lastPlayTime: 0,
  retrying: false,
};

const audio = $('#audio');
const recentToasts = new Map();

/* -------------------------------------------------------------------------
   API
   ------------------------------------------------------------------------- */

async function api(path, options = {}) {
  const opts = { credentials: 'same-origin', ...options };
  if (opts.body && typeof opts.body === 'object' && !(opts.body instanceof FormData)) {
    opts.headers = { ...(opts.headers || {}), 'Content-Type': 'application/json' };
    opts.body = JSON.stringify(opts.body);
  }
  let response;
  try {
    response = await fetch(path, opts);
  } catch (err) {
    throw new Error('无法连接服务器，请检查服务是否在运行');
  }
  let data = null;
  try { data = await response.json(); } catch (err) { /* 非 JSON */ }
  if (response.status === 401) {
    const next = encodeURIComponent(location.pathname + location.hash);
    location.href = `/login?next=${next}`;
    throw new Error('需要登录');
  }
  if (!response.ok || (data && data.ok === false)) {
    throw new Error((data && (data.error || data.message)) || `请求失败（${response.status}）`);
  }
  return data;
}

/* -------------------------------------------------------------------------
   Toast / 弹窗 / 确认框
   ------------------------------------------------------------------------- */

function toast(message, type = 'ok', ms = 3600) {
  const key = `${type}:${message}`;
  const now = Date.now();
  const previous = recentToasts.get(key);
  if (previous && now - previous < 1800) return; // 同一个错误短时间只提示一次
  recentToasts.set(key, now);
  if (recentToasts.size > 30) {
    for (const [k, ts] of recentToasts) {
      if (now - ts > 60000) recentToasts.delete(k);
    }
  }
  const root = $('#toastRoot');
  while (root.children.length >= 3) root.firstElementChild.remove();
  const el = document.createElement('div');
  el.className = `toast ${type}`;
  el.innerHTML = `<span class="toast-ic">${type === 'err' ? '!' : '✓'}</span><span>${esc(message)}</span>`;
  root.appendChild(el);
  setTimeout(() => {
    el.style.opacity = '0';
    el.style.transform = 'translateY(6px)';
    el.style.transition = 'all .25s';
    setTimeout(() => el.remove(), 260);
  }, ms);
}

function openModal(html, { wide = false, closable = true, above = false } = {}) {
  const root = $('#modalRoot');
  root.innerHTML = `<div class="modal ${wide ? 'modal-lg' : ''}" role="dialog" aria-modal="true">${html}</div>`;
  root.dataset.closable = closable ? '1' : '0';
  root.classList.toggle('above', above);
  root.classList.add('open');
  root.setAttribute('aria-hidden', 'false');
  document.body.classList.add('no-scroll');
}

function closeModal() {
  const root = $('#modalRoot');
  root.classList.remove('open');
  root.setAttribute('aria-hidden', 'true');
  root.innerHTML = '';
  if (
    !$('#queueDrawer').classList.contains('open')
    && !$('#trackDrawer').classList.contains('open')
    && !$('#nowPlaying').classList.contains('open')
  ) {
    document.body.classList.remove('no-scroll');
  }
}

function confirmDialog(title, text, okText = '确认') {
  return new Promise((resolve) => {
    state.confirmResolve = resolve;
    openModal(`
      <div class="modal-head">
        <div><div class="modal-title">${esc(title)}</div></div>
        <button class="icon-btn modal-close" data-action="modal-cancel">${ICONS.close}</button>
      </div>
      <div class="modal-body"><p class="confirm-text">${text}</p></div>
      <div class="modal-foot">
        <div class="spacer"></div>
        <button class="btn btn-ghost" data-action="modal-cancel">取消</button>
        <button class="btn btn-primary" data-action="modal-ok">${esc(okText)}</button>
      </div>`, { above: true });
  });
}

function resolveConfirm(value) {
  const resolve = state.confirmResolve;
  state.confirmResolve = null;
  closeModal();
  if (resolve) resolve(value);
}

/* -------------------------------------------------------------------------
   概览 / 外壳
   ------------------------------------------------------------------------- */

async function loadOverview() {
  const data = await api('/api/overview');
  state.overview = data;
  state.lastOverviewAt = Date.now();
  updateShell();
  return data;
}

function updateShell() {
  const o = state.overview;
  if (!o) return;
  const s = o.stats || {};
  const filters = o.filters || {};
  const dir = o.music_dir || '';
  const pathEl = $('#sidebarPath');
  pathEl.textContent = dir ? (dir.length > 38 ? '…' + dir.slice(-36) : dir) : '未选择音乐文件夹';
  pathEl.title = dir;
  $('#sidebarStat').textContent = o.initialized
    ? `${s.total || 0} 首 · 缺词 ${filters.missing_lyrics || 0} · 缺图 ${filters.missing_cover || 0}`
    : '点击选择音乐文件夹';

  const auto = o.automation || {};
  const status = auto.status || {};
  const enabled = auto.watch_enabled || auto.schedule_enabled || auto.scan_on_start;
  const dot = $('#autoDot');
  dot.className = 'pulse-dot';
  if (status.scan_running || status.fix_running) {
    $('#autoText').textContent = status.fix_running ? '后台修复中' : '后台扫描中';
  } else if (status.running && enabled) {
    $('#autoText').textContent = '自动任务运行中';
    dot.classList.add('on');
  } else if (enabled) {
    $('#autoText').textContent = '自动任务未运行';
    dot.classList.add('warn');
  } else {
    $('#autoText').textContent = '自动任务已关闭';
    dot.classList.add('off');
  }
  updatePlayerShell();
}

const PAGE_META = {
  dashboard: ['概览', '欢迎回来，这里是你的本地曲库'],
  library: ['音乐库', '浏览、播放与批量修复'],
  artists: ['歌手', '按歌手浏览歌曲与专辑'],
  files: ['文件管理', '浏览、整理与导入当前音乐目录'],
  repair: ['修复中心', '让每一首歌都有正确的歌词与封面'],
  automation: ['自动化', '让整理工作在后台自动完成'],
  settings: ['设置', '目录、数据源与访问方式'],
};

function currentRoute() {
  const hash = (location.hash || '').replace(/^#\/?/, '').split('?')[0];
  return PAGE_META[hash] ? hash : 'dashboard';
}

async function route() {
  const r = currentRoute();
  state.route = r;
  const [title, subtitle] = PAGE_META[r];
  $('#pageTitle').textContent = title;
  $('#pageSubtitle').textContent = subtitle;
  const searchInput = $('#globalSearch');
  if (searchInput) searchInput.value = r === 'library' ? state.library.q : '';
  const topSave = $('#topSaveBtn');
  if (topSave) topSave.style.display = r === 'settings' ? '' : 'none';
  $$('.nav-item').forEach((el) => el.classList.toggle('active', el.dataset.route === r));
  if (!state.overview) return;
  if (!state.overview.initialized && r !== 'settings') {
    renderNotInitialized();
    return;
  }
  if (r === 'dashboard') renderDashboard();
  else if (r === 'library') { renderLibrary(); loadLibrary(); }
  else if (r === 'artists') { renderArtists(); loadArtists(); }
  else if (r === 'files') { await loadDirChoices(); renderFiles(); loadFiles(); }
  else if (r === 'repair') renderRepair();
  else if (r === 'automation') { await loadDirChoices(); renderAutomation(); }
  else if (r === 'settings') { await loadSettings(); renderSettings(); }
}

function navigate(r) {
  if (currentRoute() === r) { route(); return; }
  location.hash = `#/${r}`;
}

/* -------------------------------------------------------------------------
   概览视图
   ------------------------------------------------------------------------- */

function renderNotInitialized() {
  $('#view').innerHTML = `
    <div class="card card-pad" style="max-width:680px;margin:8vh auto;text-align:center;padding:52px 32px">
      <div class="empty-icon" style="font-size:56px">📁</div>
      <h2 style="font-size:24px;margin:10px 0 10px">先选择你的音乐文件夹</h2>
      <p class="muted" style="line-height:1.8;max-width:460px;margin:0 auto 24px">
        插件会在这个目录里扫描音频文件，并把歌词、封面直接写入歌曲标签。
        树莓派上的音乐目录通常位于 <code class="mono">/home/pi/Music</code> 或外接硬盘的挂载点。
      </p>
      <button class="btn btn-primary btn-lg" data-action="open-folder">选择音乐文件夹</button>
    </div>`;
}

function ringSvg(score) {
  const radius = 54;
  const circumference = 2 * Math.PI * radius;
  const offset = circumference * (1 - Math.max(0, Math.min(100, score)) / 100);
  return `
    <div class="health-ring">
      <svg viewBox="0 0 128 128">
        <defs>
          <linearGradient id="ringGrad" x1="0" y1="0" x2="1" y2="1">
            <stop offset="0" stop-color="#b388ff"/><stop offset=".55" stop-color="#7c5cff"/><stop offset="1" stop-color="#38e8ff"/>
          </linearGradient>
        </defs>
        <circle class="ring-bg" cx="64" cy="64" r="${radius}"/>
        <circle class="ring-fill" cx="64" cy="64" r="${radius}"
          stroke-dasharray="${circumference.toFixed(1)}" stroke-dashoffset="${offset.toFixed(1)}"/>
      </svg>
      <div class="ring-text"><span class="ring-score">${score}</span><span class="ring-label">健康度</span></div>
    </div>`;
}

function pendingRow(icon, cls, title, sub, count, action, filter) {
  return `
    <button class="pending-row" data-action="${action}" data-filter="${filter || ''}" style="text-align:left;border:1px solid transparent;width:100%">
      <span class="pending-ic ${cls}">${ICONS[icon]}</span>
      <span class="pending-text"><span class="pending-title">${esc(title)}</span><span class="pending-sub">${esc(sub)}</span></span>
      <span class="pending-count">${count}<small>首</small></span>
    </button>`;
}

function renderDashboard() {
  const o = state.overview;
  const s = o.stats || {};
  const f = o.filters || {};
  const auto = o.automation || {};
  const status = auto.status || {};
  const total = s.total || 0;
  const lyricCovered = Math.max(0, total - (s.missing_lyrics || 0));
  const coverCovered = Math.max(0, total - (s.missing_cover || 0));
  const hour = new Date().getHours();
  const hello = hour < 6 ? '夜深了' : hour < 12 ? '早上好' : hour < 18 ? '下午好' : '晚上好';
  const pendingTotal = (s.missing_lyrics || 0) + (s.missing_cover || 0) + (s.issues || 0) + (s.timing_issues || 0);

  const pendingRows = [];
  if (s.missing_lyrics) pendingRows.push(pendingRow('lyrics', 'bad', '缺少歌词', '从网易云 / QQ 音乐 / LRCLIB 自动补齐并内嵌', s.missing_lyrics, 'quick-fix-filter', 'missing_lyrics'));
  if (s.missing_cover) pendingRows.push(pendingRow('image', 'warn', '缺少封面', '自动抓取专辑封面并写入音频标签', s.missing_cover, 'quick-fix-filter', 'missing_cover'));
  if (s.timing_issues) pendingRows.push(pendingRow('alert', 'warn', '歌词时间轴异常', '逐行歌词与音频时长/位置不匹配，可自动重新匹配', s.timing_issues, 'quick-fix-filter', 'bad_lyrics'));
  if (s.issues) pendingRows.push(pendingRow('alert', 'warn', '标签异常', '标题、歌手或专辑存在占位值或缺失', s.issues, 'goto-library-filter', 'issues'));
  if (f.duplicates) pendingRows.push(pendingRow('duplicate', 'info', '重复文件', '同名同曲或内容完全相同的文件', f.duplicates, 'goto-library-filter', 'duplicates'));
  if (!pendingRows.length) {
    pendingRows.push(`<div class="empty" style="padding:36px 10px">
      <span class="empty-icon" style="color:var(--green)">${ICONS.check}</span>
      <h3>曲库很健康</h3><p>所有曲目都有歌词和封面，暂时没有需要处理的内容。</p></div>`);
  }

  const autoRows = [
    ['启动时扫描', auto.scan_on_start],
    ['目录变化监听', auto.watch_enabled],
    ['播放时自动修复', auto.fix_on_play],
    ['每日自动修复', auto.schedule_enabled],
  ].map(([label, value]) => `
      <div class="auto-summary-row">
        <span class="label">${label}</span>
        <span class="right"><span class="status-pill ${value ? '' : 'off'}"><span class="dot"></span>${value ? '已开启' : '已关闭'}</span></span>
      </div>`).join('');

  const recentRows = (o.recent || []).slice(0, 6).map((t) => `
    <div class="recent-row" data-action="play" data-id="${t.id}" role="button" tabindex="0">
      <img class="recent-cover" src="${esc(t.cover_url)}" alt="" loading="lazy" onerror="this.src='/static/img/icon.svg'">
      <span class="recent-meta"><span class="recent-title">${esc(t.title)}</span><span class="recent-sub">${esc(t.artist_display)} · ${esc(t.album_display)}</span></span>
      <span class="recent-time">${relativeTime(t.added_at)}</span>
    </div>`).join('') || '<div class="empty" style="padding:28px">还没有最近添加的歌曲</div>';

  const activityRows = (o.activity || []).slice(0, 8).map((a) => {
    const cls = a.status === 'ok' ? 'ok' : a.status === 'error' ? 'bad' : 'warn';
    return `<div class="activity-item">
      <span class="activity-dot ${cls}"></span>
      <span class="activity-body"><span class="activity-title">${esc(a.action)}${a.track_title ? ' · ' + esc(a.track_title) : ''}</span>
      <span class="activity-detail">${esc(a.detail)}</span></span>
      <span class="activity-time">${relativeTime(a.ts)}</span>
    </div>`;
  }).join('') || '<div class="empty" style="padding:28px">还没有活动记录</div>';

  const jobs = o.jobs || [];
  const runningJob = jobs.find((j) => j.status === 'running' || j.status === 'queued');

  $('#view').innerHTML = `
    <div class="dash">
      <section class="hero">
        <div class="hero-main">
          <div>
            <div class="hero-eyebrow">CURATED LOCALLY · 本地音乐整理</div>
            <h2>${hello}。你的曲库健康度 <em>${s.health_score || 0}</em> 分</h2>
            <p class="hero-desc">${
              total === 0
                ? '还没有扫描到曲目。选择一个音乐文件夹，插件会自动建立索引并检查歌词与封面。'
                : pendingTotal > 0
                  ? `共 ${total} 首曲目，还有 ${s.missing_lyrics || 0} 首缺歌词、${s.missing_cover || 0} 首缺封面${s.timing_issues ? `、${s.timing_issues} 首时间轴异常` : ''}。一次批量修复就能把它们补齐。`
                  : `共 ${total} 首曲目，歌词与封面都已齐全。偶尔点一次扫描，就能自动发现新加入的音乐。`
            }</p>
          </div>
          <div class="hero-actions">
            <button class="btn btn-primary" data-action="start-quick-fix">${ICONS.wrench}<span>一键修复缺失内容</span></button>
            <button class="btn btn-ghost" data-action="check-providers">${ICONS.shield}<span>检查数据源</span></button>
            <button class="btn btn-ghost" data-action="export-m3u">${ICONS.download}<span>导出 M3U</span></button>
          </div>
        </div>
        <div class="hero-side">
          ${ringSvg(s.health_score || 0)}
          <div class="hero-mini">
            <div class="hero-mini-item"><span class="hero-mini-value">${total}</span><span class="hero-mini-label">首曲目</span></div>
            <div class="hero-mini-item"><span class="hero-mini-value">${pct(lyricCovered, total)}%</span><span class="hero-mini-label">歌词覆盖</span></div>
            <div class="hero-mini-item"><span class="hero-mini-value">${pct(coverCovered, total)}%</span><span class="hero-mini-label">封面覆盖</span></div>
          </div>
        </div>
      </section>

      <div class="stats-grid">
        <div class="card stat-card">
          <div class="stat-head"><span class="stat-label">曲目总数</span><span class="stat-icon">${ICONS.play}</span></div>
          <div class="stat-value">${total}<small>首</small></div>
          <div class="stat-foot"><span class="stat-note">${fmtSize(s.size)} · ${fmtTime(s.duration).replace(/^00:/, '')}</span></div>
        </div>
        <div class="card stat-card">
          <div class="stat-head"><span class="stat-label">专辑 / 歌手</span><span class="stat-icon">${ICONS.list}</span></div>
          <div class="stat-value">${s.albums || 0}<small>张</small></div>
          <div class="stat-foot"><span class="stat-note">${s.artists || 0} 位歌手 · ${(s.formats || []).length} 种格式</span></div>
        </div>
        <div class="card stat-card">
          <div class="stat-head"><span class="stat-label">歌词覆盖</span><span class="stat-icon">${ICONS.lyrics}</span></div>
          <div class="stat-value">${pct(lyricCovered, total)}<small>%</small></div>
          <div class="stat-foot"><div class="mini-bar"><span style="width:${pct(lyricCovered, total)}%"></span></div><span class="stat-note">${s.missing_lyrics || 0} 首待补</span></div>
        </div>
        <div class="card stat-card">
          <div class="stat-head"><span class="stat-label">封面覆盖</span><span class="stat-icon">${ICONS.image}</span></div>
          <div class="stat-value">${pct(coverCovered, total)}<small>%</small></div>
          <div class="stat-foot"><div class="mini-bar"><span style="width:${pct(coverCovered, total)}%"></span></div><span class="stat-note">${s.missing_cover || 0} 首待补</span></div>
        </div>
      </div>

      <div class="dash-grid">
        <section class="card card-pad">
          <div class="card-head">
            <div><div class="card-title">待处理</div><div class="card-sub">按优先级整理你的曲库</div></div>
            <button class="link-btn" data-action="goto-repair">打开修复中心 →</button>
          </div>
          <div class="pending-list">${pendingRows.join('')}</div>
          <div class="pending-foot">
            <span>匹配策略：标题 + 歌手 + 时长 + 版本</span>
            <button class="link-btn" data-action="test-providers">数据源检测 →</button>
          </div>
        </section>
        <section class="card card-pad">
          <div class="card-head">
            <div><div class="card-title">自动任务</div><div class="card-sub">${status.running ? '调度器运行中' : '调度器未运行'} · ${status.next_fix_at ? '下次修复 ' + timeUntil(status.next_fix_at) : '未设置每日修复'}</div></div>
            <span class="status-pill ${status.running ? '' : 'off'}"><span class="dot"></span>${status.running ? '运行中' : '已停止'}</span>
          </div>
          <div class="auto-summary">${autoRows}</div>
          ${runningJob ? `<div class="pending-row" style="margin-top:12px">
            <span class="pending-ic info">${ICONS.zap}</span>
            <span class="pending-text"><span class="pending-title">${esc(runningJob.title)}</span><span class="pending-sub">${esc(runningJob.message)}</span></span>
            <span class="pending-count">${runningJob.percent || 0}<small>%</small></span>
          </div>` : ''}
          <div style="margin-top:14px;display:flex;gap:8px">
            <button class="btn btn-ghost btn-sm" data-action="goto-automation">管理自动化</button>
            <button class="btn btn-soft btn-sm" data-action="scan">立即扫描</button>
          </div>
        </section>
      </div>

      <div class="dash-grid">
        <section class="card card-pad">
          <div class="card-head">
            <div><div class="card-title">最近添加</div><div class="card-sub">新加入曲库的歌曲</div></div>
            <button class="link-btn" data-action="goto-library">查看全部 →</button>
          </div>
          <div class="recent-list">${recentRows}</div>
        </section>
        <section class="card card-pad">
          <div class="card-head">
            <div><div class="card-title">最近活动</div><div class="card-sub">扫描、修复与设置变更</div></div>
            <button class="link-btn" data-action="refresh">刷新</button>
          </div>
          <div class="activity-list">${activityRows}</div>
        </section>
      </div>
    </div>`;
}

/* -------------------------------------------------------------------------
   音乐库
   ------------------------------------------------------------------------- */

function libParams() {
  const l = state.library;
  const params = new URLSearchParams({
    q: l.q || '', filter: l.filter || 'all', sort: l.sort || 'artist',
    page: String(l.page || 1), page_size: String(l.page_size || 40),
  });
  return params;
}

async function loadLibrary() {
  const l = state.library;
  const seq = ++l.seq;
  l.loading = true;
  if (state.route === 'library') renderLibrary();
  try {
    const data = await api(`/api/tracks?${libParams().toString()}`);
    if (seq !== l.seq) return;
    l.items = data.items || [];
    l.total = data.total || 0;
    l.counts = data.counts || {};
    l.loading = false;
    l.loaded = true;
    l.error = '';
  } catch (err) {
    if (seq !== l.seq) return;
    l.loading = false;
    l.error = err.message;
  }
  if (state.route === 'library') renderLibrary();
}

function statusBadges(t) {
  const badges = [];
  if (t.no_lyrics) {
    badges.push('<span class="status-mini ok" title="已标记为纯音乐，不需要歌词">纯</span>');
  } else if (t.has_lyrics) {
    badges.push(`<span class="status-mini ok" title="${t.lyrics_synced ? '逐行歌词已内嵌' : '歌词已内嵌（无时间轴）'}">词</span>`);
  } else {
    badges.push('<span class="status-mini bad" title="缺少歌词">缺词</span>');
  }
  if (t.has_cover) {
    badges.push(`<span class="status-mini ok" title="${t.cover_source === 'embed' ? '封面已内嵌' : '封面来自同目录图片'}">图</span>`);
  } else {
    badges.push('<span class="status-mini bad" title="缺少封面">缺图</span>');
  }
  if (['broken', 'suspect'].includes(t.lyrics_timing_status)) {
    const detail = (t.lyrics_timing_issues || []).map((i) => i.label || '').filter(Boolean).join('；');
    badges.push(`<span class="status-mini warn" title="${esc(detail || '歌词时间轴异常')}">轴</span>`);
  }
  if ((t.tag_issues || []).length) {
    badges.push(`<span class="status-mini warn" title="${esc(t.tag_issues.map((i) => i.label).join('；'))}">签</span>`);
  }
  return badges.join('');
}

function trackRow(t) {
  const playing = state.current && state.current.id === t.id;
  const checked = state.selected.has(t.id);
  const duplicateRow = state.library.filter === 'duplicates';
  return `
    <div class="track-row ${playing ? 'playing' : ''}" data-track-id="${t.id}" data-action="open-track" data-id="${t.id}" tabindex="0">
      <div class="track-check"><input type="checkbox" data-select-id="${t.id}" ${checked ? 'checked' : ''} aria-label="选择 ${esc(t.title)}"></div>
      <div class="track-cover-wrap">
        <img class="track-cover" src="${esc(t.cover_url)}" alt="" loading="lazy" onerror="this.src='/static/img/icon.svg'">
        <button class="track-play" data-action="play" data-id="${t.id}" title="播放">${ICONS.play}</button>
        <span class="track-eq"><span></span><span></span><span></span></span>
      </div>
      <div class="track-main">
        <div class="track-title">${esc(t.title)}</div>
        <div class="track-sub">${esc(t.artist_display)}${t.album ? ' · ' + esc(t.album) : ''}</div>
      </div>
      <div class="track-album">${esc(t.album_display)}</div>
      <div class="track-duration">${esc(t.duration_text)}</div>
      <div class="track-format">${esc((t.ext || '').toUpperCase())}${t.bits ? ' · ' + t.bits + 'bit' : ''}</div>
      <div class="track-status">${statusBadges(t)}</div>
      <div class="track-actions">
        ${duplicateRow ? `<button class="btn btn-ghost btn-xs dup-ignore" data-action="ignore-duplicate" data-id="${t.id}" title="这组不是重复，忽略">不是重复</button>` : ''}
        <button class="icon-btn primary-action" data-action="repair-track" data-id="${t.id}" data-targets="lyrics,cover" title="一键修复">${ICONS.wrench}</button>
        <button class="icon-btn" data-action="candidates" data-id="${t.id}" data-kind="cover" title="更换封面">${ICONS.image}</button>
        <button class="icon-btn" data-action="open-track" data-id="${t.id}" title="详情">${ICONS.more}</button>
      </div>
    </div>`;
}

function skeletonRows(n) {
  return Array.from({ length: n }).map(() => `
    <div class="track-row" style="cursor:default">
      <div></div><div class="skeleton" style="width:44px;height:44px"></div>
      <div><div class="skeleton" style="height:13px;width:55%;margin-bottom:8px"></div><div class="skeleton" style="height:10px;width:32%"></div></div>
      <div class="skeleton" style="height:11px;width:70%"></div>
      <div class="skeleton" style="height:11px;width:40%"></div>
      <div class="skeleton" style="height:11px;width:40%"></div>
      <div></div><div></div>
    </div>`).join('');
}

async function loadArtists() {
  const a = state.artists;
  a.loading = true;
  if (state.route === 'artists') renderArtists();
  try {
    const data = await api('/api/artists');
    a.items = data.items || [];
  } catch (err) {
    a.items = [];
    toast(err.message, 'err');
  } finally {
    a.loading = false;
    if (state.route === 'artists') renderArtists();
  }
}

async function artistOpen(name) {
  const a = state.artists;
  a.loading = true;
  a.selected = { name };
  a.album = null;
  a.tab = 'albums';
  if (state.route === 'artists') renderArtists();
  try {
    a.selected = (await api('/api/artist?name=' + encodeURIComponent(name))).artist;
  } catch (err) {
    toast(err.message, 'err');
    a.selected = null;
  } finally {
    a.loading = false;
    if (state.route === 'artists') renderArtists();
  }
}

function artistSortSongs(tracks) {
  const rows = [...(tracks || [])];
  const mode = state.artists.sortSongs || 'default';
  if (mode === 'title') rows.sort((a, b) => String(a.title || '').localeCompare(String(b.title || ''), 'zh-CN'));
  else if (mode === 'album') rows.sort((a, b) => String(a.album || '').localeCompare(String(b.album || ''), 'zh-CN') || String(a.title || '').localeCompare(String(b.title || ''), 'zh-CN'));
  else if (mode === 'year') rows.sort((a, b) => String(b.year || '').localeCompare(String(a.year || '')) || String(a.title || '').localeCompare(String(b.title || ''), 'zh-CN'));
  else if (mode === 'duration') rows.sort((a, b) => Number(b.duration || 0) - Number(a.duration || 0));
  else if (mode === 'added') rows.sort((a, b) => String(b.added_at || '').localeCompare(String(a.added_at || '')));
  return rows;
}

function artistSortAlbums(albums) {
  const rows = [...(albums || [])];
  const mode = state.artists.sortAlbums || 'year';
  if (mode === 'name') rows.sort((a, b) => String(a.name || '').localeCompare(String(b.name || ''), 'zh-CN'));
  else if (mode === 'tracks') rows.sort((a, b) => Number(b.track_count || 0) - Number(a.track_count || 0));
  else rows.sort((a, b) => String(b.year || '').localeCompare(String(a.year || '')) || String(a.name || '').localeCompare(String(b.name || ''), 'zh-CN'));
  return rows;
}

function artistSongRows(tracks, showAlbum = true) {
  return artistSortSongs(tracks).map((t, index) => `
    <div class="artist-song-row" data-action="play" data-id="${t.id}">
      <span class="idx">${String(index + 1).padStart(2, '0')}</span>
      <img class="artist-song-cover" src="${esc(t.cover_url)}" alt="">
      <div class="artist-song-main"><b>${esc(t.title)}</b>${showAlbum ? `<span>${esc(t.album_display || t.album || '')}</span>` : ''}</div>
      <span class="artist-song-dur">${esc(t.duration_text || '')}</span>
      <button class="icon-btn" data-action="open-track" data-id="${t.id}" title="详情">···</button>
    </div>`).join('');
}

function artistSortBar(kind) {
  if (kind === 'albums') {
    const value = state.artists.sortAlbums || 'year';
    return `<label class="artist-sort"><span>排序</span><select class="select" id="artistAlbumSort">
      <option value="year" ${value === 'year' ? 'selected' : ''}>按年份</option>
      <option value="name" ${value === 'name' ? 'selected' : ''}>按专辑名</option>
      <option value="tracks" ${value === 'tracks' ? 'selected' : ''}>按歌曲数</option>
    </select></label>`;
  }
  const value = state.artists.sortSongs || 'default';
  return `<label class="artist-sort"><span>排序</span><select class="select" id="artistSongSort">
    <option value="default" ${value === 'default' ? 'selected' : ''}>默认顺序</option>
    <option value="title" ${value === 'title' ? 'selected' : ''}>按歌名</option>
    <option value="album" ${value === 'album' ? 'selected' : ''}>按专辑</option>
    <option value="year" ${value === 'year' ? 'selected' : ''}>按年份</option>
    <option value="duration" ${value === 'duration' ? 'selected' : ''}>按时长</option>
    <option value="added" ${value === 'added' ? 'selected' : ''}>最近加入</option>
  </select></label>`;
}

function renderArtists() {
  const a = state.artists;
  if (a.selected) {
    const artist = a.selected;
    const album = a.album;
    const tabs = `<div class="artist-tabs">
      <button class="artist-tab ${a.tab === 'songs' ? 'active' : ''}" data-action="artist-tab" data-tab="songs">歌曲 <em>${artist.track_count || 0}</em></button>
      <button class="artist-tab ${a.tab === 'albums' ? 'active' : ''}" data-action="artist-tab" data-tab="albums">专辑 <em>${artist.album_count || 0}</em></button>
    </div>`;
    let body = '';
    if (album) {
      body = `<div class="artist-subhead"><button class="artist-back artist-back-sm" data-action="artist-album-back">← 返回专辑</button><div><div class="artist-subtitle">${esc(artist.name)}</div><div class="artist-title-sm">${esc(album.name)}</div></div><span>${album.track_count || 0} 首</span></div>
        <div class="artist-toolbar">${artistSortBar('songs')}</div>
        <div class="artist-song-list">${artistSongRows(album.tracks)}</div>`;
    } else if (a.tab === 'albums') {
      body = `<div class="artist-toolbar">${artistSortBar('albums')}</div>
        <div class="artist-album-grid">${artistSortAlbums(artist.albums).map((item) => `
        <button class="artist-album-card" data-action="artist-album" data-album="${esc(item.name)}">
          <img src="${esc(item.cover_url)}" alt="">
          <b>${esc(item.name)}</b><span>${item.track_count || 0} 首${item.year ? ' · ' + esc(item.year) : ''}</span>
        </button>`).join('')}</div>`;
    } else {
      body = `<div class="artist-toolbar">${artistSortBar('songs')}</div>
        <div class="artist-song-list">${artistSongRows(artist.tracks)}</div>`;
    }
    $('#view').innerHTML = `
      <div class="artist-page">
        <div class="artist-hero">
          <div class="artist-hero-bg" style="background-image:url('${esc(artist.cover_url)}')"></div>
          <div class="artist-hero-glow"></div>
          <div class="artist-hero-inner">
            <button class="artist-back" data-action="artist-back">← 返回歌手</button>
            <div class="artist-hero-main">
              <div class="artist-avatar"><img src="${esc(artist.cover_url)}" alt=""></div>
              <div class="artist-hero-copy">
                <div class="artist-kicker">ARTIST</div>
                <h1>${esc(artist.name)}</h1>
                <div class="artist-meta">${artist.track_count || 0} 首歌曲 · ${artist.album_count || 0} 张专辑</div>
              </div>
            </div>
          </div>
        </div>
        <div class="artist-tabs-row">${tabs}</div>
        ${body}
      </div>`;
    return;
  }
  const cards = (a.items || []).map((artist) => `
    <button class="artist-card" data-action="artist-open" data-name="${esc(artist.name)}">
      <div class="artist-card-img"><img src="${esc(artist.cover_url)}" alt=""><span class="artist-card-ring"></span></div>
      <b>${esc(artist.name)}</b>
      <span>${artist.track_count || 0} 首 · ${artist.album_count || 0} 张专辑</span>
    </button>`).join('');
  $('#view').innerHTML = `
    <div class="artists-page">
      <div class="artists-head"><div><div class="artist-kicker">ARTISTS</div><h1>歌手</h1><p>${(a.items || []).length} 位歌手 · 按歌曲数量排序</p></div></div>
      <div class="artist-grid">${cards || `<div class="empty-note">${a.loading ? '正在加载歌手…' : '还没有歌手'}</div>`}</div>
    </div>`;
}

function renderLibrary() {
  const l = state.library;
  const o = state.overview || {};
  const counts = l.counts || {};
  const dupCount = (o.filters && o.filters.duplicates) || 0;
  const filters = [
    ['all', '全部', counts.total || l.total || 0],
    ['missing_lyrics', '缺歌词', counts.missing_lyrics || 0],
    ['missing_cover', '缺封面', counts.missing_cover || 0],
    ['bad_lyrics', '时间轴', counts.timing_issues || 0],
    ['issues', '标签异常', counts.issues || 0],
    ['duplicates', '重复', dupCount],
    ['instrumental', '纯音乐', counts.instrumental || 0],
    ['favorite', '收藏', counts.favorite || 0],
  ];
  const chips = filters.map(([key, label, count]) => `
    <button class="chip ${l.filter === key ? 'active' : ''}" data-action="filter" data-filter="${key}">${label}<span class="count">${count}</span></button>`).join('');

  let body = '';
  if (l.error) {
    body = `<div class="empty"><span class="empty-icon">${ICONS.alert}</span><h3>加载失败</h3><p>${esc(l.error)}</p><button class="btn btn-soft" data-action="refresh">重试</button></div>`;
  } else if (l.loading && !l.loaded) {
    body = skeletonRows(7);
  } else if (!l.items.length) {
    body = `<div class="empty"><span class="empty-icon">${ICONS.list}</span>
      <h3>${l.q ? '没有匹配的歌曲' : '这个筛选下没有歌曲'}</h3>
      <p>${l.q ? `换一个关键词试试，或者清空搜索条件。` : '曲库很干净，没有需要展示的曲目。'}</p>
      ${l.q ? '<button class="btn btn-soft" data-action="clear-search">清空搜索</button>' : ''}</div>`;
  } else {
    body = l.items.map(trackRow).join('');
  }

  const totalPages = Math.max(1, Math.ceil(l.total / l.page_size));
  const selectedCount = state.selected.size;
  const selBar = selectedCount ? `
    <div class="selection-bar">
      <span>已选择 <strong>${selectedCount}</strong> 首</span>
      <button class="btn btn-ghost btn-sm" data-action="batch-repair" data-targets="lyrics">修复歌词</button>
      <button class="btn btn-ghost btn-sm" data-action="batch-repair" data-targets="cover">修复封面</button>
      <button class="btn btn-soft btn-sm" data-action="batch-repair" data-targets="lyrics,cover">一键修复</button>
      <span class="spacer"></span>
      <button class="btn btn-ghost btn-sm" data-action="clear-selection">取消选择</button>
    </div>` : '';

  $('#view').innerHTML = `
    <div class="library">
      <div class="lib-toolbar">
        <div class="chips">${chips}</div>
        <div class="lib-toolbar-right">
          <select class="select input-sm" id="libSort" aria-label="排序">
            <option value="artist" ${l.sort === 'artist' ? 'selected' : ''}>按歌手排序</option>
            <option value="title" ${l.sort === 'title' ? 'selected' : ''}>按标题排序</option>
            <option value="album" ${l.sort === 'album' ? 'selected' : ''}>按专辑排序</option>
            <option value="added" ${l.sort === 'added' ? 'selected' : ''}>最近添加</option>
            <option value="duration" ${l.sort === 'duration' ? 'selected' : ''}>按时长排序</option>
          </select>
          <button class="btn btn-ghost btn-sm" data-action="select-all">${selectedCount ? '取消全选' : '全选本页'}</button>
        </div>
      </div>
      ${l.filter === 'duplicates' ? `<div class="dup-note"><span>按「歌手 + 歌名 + 时长 + 专辑」识别；同一歌名不同歌手不会算重复。若仍误判，点右侧「不是重复」忽略整组。</span><button class="btn btn-ghost btn-xs" data-action="clear-ignored-duplicates">恢复所有忽略（${o.duplicate_ignored || 0}）</button></div>` : ''}
      ${selBar}
      <div class="card track-table">
        <div class="track-head">
          <span></span><span>封面</span><span>歌曲</span><span>专辑</span><span>时长</span><span>格式</span><span>状态</span><span style="text-align:right">操作</span>
        </div>
        ${body}
      </div>
      <div class="pager">
        <span>共 ${l.total} 首${l.q ? ` · 搜索“${esc(l.q)}”` : ''} · 第 ${l.page} / ${totalPages} 页</span>
        <div class="pager-btns">
          <button class="page-btn" data-action="page-prev" ${l.page <= 1 ? 'disabled' : ''}>${ICONS.arrowLeft}</button>
          <button class="page-btn" data-action="page-next" ${l.page >= totalPages ? 'disabled' : ''}>${ICONS.arrowRight}</button>
        </div>
      </div>
    </div>`;
}

/* -------------------------------------------------------------------------
   修复中心
   ------------------------------------------------------------------------- */

function jobStatusText(status) {
  return { queued: '排队中', running: '进行中', done: '已完成', error: '失败', cancelled: '已取消' }[status] || status;
}

function renderJobPanel(job) {
  if (!job) {
    const hint = state.route === 'automation'
      ? '自动任务运行后会在这里显示实时进度'
      : '设置好范围后点击「开始修复」';
    return `<div class="job-empty">
      <span class="empty-icon" style="font-size:38px;opacity:.4">${ICONS.zap}</span>
      <div><strong style="color:var(--text)">当前没有运行中的任务</strong><br>${hint}</div>
      <div class="job-steps">
        <div><b>1</b><span>搜索候选</span></div>
        <div><b>2</b><span>匹配打分</span></div>
        <div><b>3</b><span>备份写入</span></div>
      </div>
      <div class="job-empty-tip">写入前自动备份 · 支持逐首撤销</div>
    </div>`;
  }
  const running = job.status === 'running' || job.status === 'queued';
  const logs = (job.logs || []).slice(-80).map((line) => {
    const cls = /失败|错误|error/i.test(line) ? 'bad' : /完成|修复|成功/i.test(line) ? 'ok' : '';
    return `<div class="${cls}">${esc(line)}</div>`;
  }).join('');
  const items = (job.items || []).slice(-30).reverse().map((item) => {
    const st = ['fixed', 'partial'].includes(item.status) ? 'ok' : ['error'].includes(item.status) ? 'bad' : 'skip';
    return `<div class="job-item"><span class="name">${esc(item.title)}</span><span class="st ${st}">${esc(item.message || jobStatusText(item.status))}</span></div>`;
  }).join('');
  return `
    <div class="job-head">
      <div><div class="job-title">${esc(job.title)}</div><div class="job-sub">${jobStatusText(job.status)}${job.current ? ' · ' + esc(job.current) : ''}</div></div>
      <div style="text-align:right"><div class="job-percent">${job.percent || 0}%</div></div>
    </div>
    <div class="job-bar"><span style="width:${job.percent || 0}%"></span></div>
    <div class="job-meta">
      <span><b>${job.done || 0}</b> / ${job.total || 0}</span>
      <span>成功 <b>${job.succeeded || 0}</b></span>
      <span>跳过 <b>${job.skipped || 0}</b></span>
      <span>失败 <b>${job.failed || 0}</b></span>
      <span>${job.elapsed ? job.elapsed + 's' : ''}</span>
    </div>
    ${running ? `<div style="margin-top:12px"><button class="btn btn-ghost btn-sm" data-action="cancel-job">取消任务</button></div>` : ''}
    <div class="job-log">${logs || '<span class="dim">等待日志…</span>'}</div>
    ${items ? `<div class="job-items">${items}</div>` : ''}`;
}

/* -------------------------------------------------------------------------
   文件管理
   ------------------------------------------------------------------------- */

const FILE_ICONS = {
  folder: ICONS.folder,
  audio: ICONS.play,
  image: ICONS.image,
  text: ICONS.lyrics,
  other: ICONS.duplicate,
};

function fileLabel(kind) {
  return ({ folder: '文件夹', audio: '音频', image: '图片', text: '文本', other: '文件' })[kind] || '文件';
}

function fileSelectedPaths() {
  return Array.from(state.files.selected);
}

function fileEntryRow(entry) {
  const selected = state.files.selected.has(entry.path);
  const isAudio = entry.kind === 'audio';
  const isText = entry.kind === 'text';
  return `<div class="file-row ${selected ? 'selected' : ''}" data-file-path="${esc(entry.path)}">
    <div class="file-check"><input type="checkbox" data-file-select="${esc(entry.path)}" ${selected ? 'checked' : ''} aria-label="选择 ${esc(entry.name)}"></div>
    <div class="file-main">
      <span class="file-ic ${entry.kind}">${FILE_ICONS[entry.kind] || FILE_ICONS.other}</span>
      <div style="min-width:0">
        <button class="file-name" data-action="${entry.is_dir ? 'file-open' : (isAudio && entry.track_id ? 'file-track' : isText ? 'file-edit-text' : 'file-download')}" data-path="${esc(entry.path)}" data-id="${entry.track_id || 0}" title="${esc(entry.name)}">${esc(entry.name)}</button>
        <div class="file-sub">${entry.is_dir ? '文件夹' : (entry.ext || '文件')}${isAudio && entry.track_id ? ' · 已入库' : ''}${entry.writable === false ? ' · 只读' : ''}</div>
      </div>
    </div>
    <div class="file-col file-col-kind">${fileLabel(entry.kind)}</div>
    <div class="file-size file-col-size">${entry.is_dir ? '—' : fmtSize(entry.size)}</div>
    <div class="file-col file-col-modified">${entry.modified ? relativeTime(entry.modified) : ''}</div>
    <div class="file-actions">
      ${entry.is_dir ? `<button class="file-mini" data-action="file-open" data-path="${esc(entry.path)}">打开</button>` : ''}
      ${isAudio && entry.track_id ? `<button class="file-mini" data-action="file-track" data-id="${entry.track_id}">详情</button>` : ''}
      ${isText ? `<button class="file-mini" data-action="file-edit-text" data-path="${esc(entry.path)}">编辑</button>` : ''}
      ${!entry.is_dir ? `<button class="file-mini" data-action="file-download" data-path="${esc(entry.path)}">下载</button>` : ''}
      <button class="file-mini" data-action="file-rename" data-path="${esc(entry.path)}" data-name="${esc(entry.name)}">改名</button>
      <button class="file-mini danger" data-action="file-delete" data-path="${esc(entry.path)}">删除</button>
    </div>
  </div>`;
}

function renderFiles() {
  const f = state.files;
  const root = f.root || state.overview?.music_dir || '';
  const crumbs = (f.breadcrumbs || []).map((crumb, index, list) => {
    const current = index === list.length - 1;
    return `${index ? '<span class="files-crumb-sep">/</span>' : ''}<button class="files-crumb ${current ? 'current' : ''}" data-action="file-open" data-path="${esc(crumb.path)}">${esc(crumb.name)}</button>`;
  }).join('');
  const selected = f.selected.size;
  const entries = f.entries || [];
  const error = f.error ? `<div class="empty" style="padding:20px"><p style="color:var(--red)">${esc(f.error)}</p><button class="btn btn-ghost btn-sm" data-action="file-open" data-path="">回到根目录</button> <button class="btn btn-ghost btn-sm" data-action="file-refresh">重试</button></div>` : '';
  $('#view').innerHTML = `
    <div class="files-page">
      <div class="files-actions">
        <button class="btn btn-primary btn-sm" data-action="file-upload">${ICONS.upload}<span>导入文件</span></button>
        <button class="btn btn-ghost btn-sm" data-action="file-export">导出/转换</button>
        <button class="btn btn-ghost btn-sm" data-action="file-mkdir">新建文件夹</button>
        <button class="btn btn-ghost btn-sm" data-action="file-organize">一键整理</button>
        <button class="btn btn-ghost btn-sm" data-action="file-rename-batch">批量命名</button>
        <button class="btn btn-ghost btn-sm" data-action="file-repair">一键修复</button>
        <button class="btn btn-ghost btn-sm" data-action="file-history">${ICONS.clock}<span>历史</span></button>
        <button class="btn btn-ghost btn-sm" data-action="file-restore-original">${ICONS.shield}<span>恢复原始</span></button>
      </div>

      <div class="files-root">
        <span class="files-root-icon">${ICONS.folder}</span>
        <div class="files-root-main">
          <div class="files-root-label">音乐目录</div>
          <div class="files-root-path">${esc(root || '未选择')}</div>
          <div class="files-root-meta">${f.disk?.free ? `剩余 ${fmtSize(f.disk.free)} / ${fmtSize(f.disk.total)}` : '正在读取磁盘信息…'}${f.original?.exists ? ` · 原始快照 ${esc(f.original.created_at || '')}` : ' · 尚未修改'}</div>
        </div>
        <button class="btn btn-ghost btn-sm" data-action="file-refresh">${ICONS.refresh}<span>刷新</span></button>
      </div>

      <div class="files-toolbar card card-pad" style="padding:12px 14px">
        <div class="files-breadcrumbs">${crumbs}</div>
        <div class="file-selection">${selected ? `已选 ${selected} 项` : `共 ${f.entries?.length || 0} 项`}</div>
        <input class="input files-search" id="fileSearch" value="${esc(f.q)}" placeholder="搜索当前文件夹">
        <select class="select files-select" id="fileSort">
          <option value="name" ${f.sort === 'name' ? 'selected' : ''}>名称</option>
          <option value="modified" ${f.sort === 'modified' ? 'selected' : ''}>修改时间</option>
          <option value="size" ${f.sort === 'size' ? 'selected' : ''}>大小</option>
          <option value="type" ${f.sort === 'type' ? 'selected' : ''}>类型</option>
        </select>
        ${selected ? `<button class="btn btn-ghost btn-sm" data-action="file-copy">复制到</button><button class="btn btn-ghost btn-sm" data-action="file-move">移动到</button><button class="btn btn-danger btn-sm" data-action="file-delete-selected">删除</button>` : ''}
        ${f.loading ? '<span class="dim" style="font-size:10px">加载中…</span>' : ''}
      </div>

      ${error || `
      <div class="files-list" id="fileDrop">
        <div class="files-list-head">
          <div class="file-check"><input type="checkbox" id="fileSelectAll" ${entries.length && selected === entries.length ? 'checked' : ''} aria-label="全选"></div>
          <div>名称</div><div class="file-col-kind">类型</div><div class="file-col-size">大小</div><div class="file-col-modified">修改时间</div><div style="text-align:right">操作</div>
        </div>
        ${entries.length ? entries.map(fileEntryRow).join('') : `<div class="file-empty"><span class="empty-icon">${ICONS.folder}</span><div>这个文件夹是空的</div><div style="margin-top:6px">可以导入文件，或新建文件夹。</div></div>`}
      </div>`}

      <div class="file-footer">
        <span>${f.dirs || 0} 个文件夹 · ${f.files || 0} 个文件</span>
        ${f.readonly ? `<span style="color:var(--warn)">⚠ ${f.readonly} 项只读；修复会失败，请先修正文件所有者/权限</span>` : '<span>删除进入应用回收站，可撤销</span>'}
        <span class="spacer"></span>
        <span>${f.original?.exists ? `原始快照：${f.original.file_count} 个文件` : '原始快照将在第一次修改前建立'}</span>
        <span>历史：${f.historyCount || 0} 次</span>
      </div>
    </div>`;
}

async function loadFiles() {
  const f = state.files;
  const seq = ++f.seq;
  const keepSearch = document.activeElement && document.activeElement.id === 'fileSearch';
  f.loading = true;
  f.error = '';
  if (state.route === 'files') {
    renderFiles();
    if (keepSearch) {
      const input = $('#fileSearch');
      if (input) { input.focus(); input.setSelectionRange(input.value.length, input.value.length); }
    }
  }
  try {
    const params = new URLSearchParams({ path: f.path || '', q: f.q || '', sort: f.sort || 'name' });
    const data = await api(`/api/files?${params.toString()}`);
    if (seq !== f.seq) return;
    f.root = data.root || '';
    f.path = data.path || '';
    f.breadcrumbs = data.breadcrumbs || [];
    f.entries = data.entries || [];
    f.disk = data.disk || {};
    f.dirs = data.dirs || 0;
    f.files = data.files || 0;
    f.original = data.original || null;
    f.historyCount = data.history_count || 0;
    const existing = new Set(f.entries.map((item) => item.path));
    f.selected = new Set(Array.from(f.selected).filter((path) => existing.has(path)));
  } catch (err) {
    if (seq !== f.seq) return;
    f.error = err.message;
  } finally {
    if (seq === f.seq) f.loading = false;
  }
  if (state.route === 'files') {
    renderFiles();
    if (keepSearch) {
      const input = $('#fileSearch');
      if (input) { input.focus(); input.setSelectionRange(input.value.length, input.value.length); }
    }
  }
}

async function fileOpen(path) {
  state.files.path = path || '';
  state.files.selected.clear();
  state.files.q = '';
  await loadFiles();
}

async function fileRefresh() {
  await loadFiles();
  toast('已刷新', 'ok', 1200);
}

function fileAskName(title, value, action) {
  openModal(`
    <div class="modal-head"><div><div class="modal-title">${esc(title)}</div></div><button class="icon-btn modal-close" data-action="modal-cancel">${ICONS.close}</button></div>
    <div class="modal-body">
      <label class="field"><span>名称</span><input class="input" id="fileNameInput" value="${esc(value || '')}" autofocus></label>
    </div>
    <div class="modal-foot"><div class="spacer"></div><button class="btn btn-ghost" data-action="modal-cancel">取消</button><button class="btn btn-primary" data-action="${action}">确定</button></div>`);
}

async function fileMkdir() {
  fileAskName('新建文件夹', '', 'file-mkdir-submit');
}

async function fileRenamePrompt(path, name) {
  state.files.renamePath = path;
  fileAskName('重命名', name, 'file-rename-submit');
}

async function fileMkdirSubmit() {
  const name = ($('#fileNameInput')?.value || '').trim();
  if (!name) { toast('请输入文件夹名称', 'err'); return; }
  try {
    await api('/api/files/mkdir', { method: 'POST', body: { path: state.files.path, name } });
    closeModal(); toast('文件夹已创建', 'ok'); await loadFiles();
  } catch (err) { toast(err.message, 'err'); }
}

async function fileRenameSubmit() {
  const name = ($('#fileNameInput')?.value || '').trim();
  if (!name) { toast('请输入新名称', 'err'); return; }
  try {
    await api('/api/files/rename', { method: 'POST', body: { path: state.files.renamePath, name } });
    closeModal(); toast('已重命名', 'ok'); await loadFiles(); loadOverview().catch(() => {});
  } catch (err) { toast(err.message, 'err'); }
}

async function fileDelete(paths) {
  if (!paths || !paths.length) return;
  const ok = await confirmDialog('删除到回收站？', `将移动 ${paths.length} 项到应用回收站，可在历史里撤销。`, '删除');
  if (!ok) return;
  try {
    await api('/api/files/delete', { method: 'POST', body: { paths } });
    state.files.selected.clear();
    toast('已移入回收站', 'ok'); await loadFiles(); loadOverview().catch(() => {});
  } catch (err) { toast(err.message, 'err'); }
}

async function fileTransfer(copy) {
  const paths = fileSelectedPaths();
  if (!paths.length) { toast('请先选择文件', 'err'); return; }
  try {
    const data = await api('/api/files/dirs');
    state.files.dirs = data.dirs || [];
  } catch (err) { toast(err.message, 'err'); return; }
  const options = state.files.dirs.map((item) => `<option value="${esc(item.path)}" ${item.path === state.files.path ? 'selected' : ''}>${esc(item.name)}</option>`).join('');
  openModal(`
    <div class="modal-head"><div><div class="modal-title">${copy ? '复制到' : '移动到'}</div><div class="modal-sub">${paths.length} 项</div></div><button class="icon-btn modal-close" data-action="modal-cancel">${ICONS.close}</button></div>
    <div class="modal-body">
      <label class="field"><span>目标文件夹</span><select class="select" id="fileDest">${options}</select></label>
      <p class="file-modal-note">目标文件夹必须在当前音乐目录内；同名文件会自动加序号，不会覆盖。</p>
    </div>
    <div class="modal-foot"><div class="spacer"></div><button class="btn btn-ghost" data-action="modal-cancel">取消</button><button class="btn btn-primary" data-action="${copy ? 'file-copy-submit' : 'file-move-submit'}">${copy ? '复制' : '移动'}</button></div>`);
  state.files.transferCopy = copy;
}

async function fileTransferSubmit(copy) {
  const dest = $('#fileDest')?.value || '';
  const paths = fileSelectedPaths();
  try {
    await api(copy ? '/api/files/copy' : '/api/files/move', { method: 'POST', body: { paths, dest } });
    closeModal(); state.files.selected.clear(); toast(copy ? '复制完成' : '移动完成', 'ok');
    await loadFiles(); loadOverview().catch(() => {});
  } catch (err) { toast(err.message, 'err'); }
}

function fileUploadPrompt() {
  openModal(`
    <div class="modal-head"><div><div class="modal-title">导入文件</div><div class="modal-sub">上传到 ${esc(state.files.path || '根目录')}</div></div><button class="icon-btn modal-close" data-action="modal-cancel">${ICONS.close}</button></div>
    <div class="modal-body">
      <input type="file" id="fileUploadInput" multiple accept="audio/*,.flac,.mp3,.m4a,.ogg,.opus,.wav,.aiff,.ape,.wv,.lrc,.jpg,.jpeg,.png,.webp,.txt,.nfo">
      <p class="file-modal-note">支持音频、封面、.lrc 和文本；上传后会自动进入曲库索引。大文件请保持页面打开。</p>
      <label class="checkline" style="margin-top:12px"><input type="checkbox" id="uploadAutoClassify" checked> 上传音频后自动分类</label>
      <label class="checkline"><input type="checkbox" id="uploadOnlyExisting" checked> 只放入已有文件夹，缺失时进入「其他」</label>
      <label class="field" id="uploadClassifyWrap" style="margin-top:8px"><span>分类方式</span>
        <select class="select" id="uploadClassifyMode">
          <option value="artist_album">歌手 / 专辑</option>
          <option value="artist">按歌手</option>
          <option value="album">按专辑</option>
          <option value="genre">按流派</option>
          <option value="year">按年份</option>
          <option value="keep">保持当前文件夹</option>
        </select>
      </label>
      <div class="file-progress" id="fileUploadProgress" style="display:none"><span></span></div>
      <div id="fileUploadStatus" class="file-modal-note"></div>
    </div>
    <div class="modal-foot"><div class="spacer"></div><button class="btn btn-ghost" data-action="modal-cancel">取消</button><button class="btn btn-primary" data-action="file-upload-submit">开始上传</button></div>`);
}

function fileUploadSubmit() {
  const input = $('#fileUploadInput');
  if (!input || !input.files || !input.files.length) { toast('请选择文件', 'err'); return; }
  const form = new FormData();
  form.append('path', state.files.path || '');
  form.append('auto_classify', $('#uploadAutoClassify')?.checked ? '1' : '0');
  form.append('only_existing', $('#uploadOnlyExisting')?.checked ? '1' : '0');
  form.append('classify_mode', $('#uploadClassifyMode')?.value || 'artist_album');
  Array.from(input.files).forEach((file) => form.append('files', file));
  const bar = $('#fileUploadProgress');
  const status = $('#fileUploadStatus');
  if (bar) bar.style.display = '';
  if (status) status.textContent = '正在上传…';
  const xhr = new XMLHttpRequest();
  xhr.open('POST', '/api/files/upload');
  xhr.upload.onprogress = (event) => {
    if (event.lengthComputable && bar) {
      bar.firstElementChild.style.width = `${Math.round((event.loaded / event.total) * 100)}%`;
    }
  };
  xhr.onload = async () => {
    let data = {};
    try { data = JSON.parse(xhr.responseText || '{}'); } catch (err) { /* ignore */ }
    if (xhr.status >= 200 && xhr.status < 300 && data.ok !== false) {
      const saved = (data.saved || []).length;
      const errors = data.errors || [];
      const warnings = data.warnings || [];
      closeModal();
      toast(`已导入 ${saved} 个文件${errors.length ? `，${errors.length} 个失败` : ''}${warnings.length ? `，${warnings.length} 个未分类` : ''}`, (errors.length || warnings.length) ? 'err' : 'ok');
      await loadFiles(); loadOverview().catch(() => {});
    } else {
      if (status) status.textContent = data.error || '上传失败';
      toast(data.error || '上传失败', 'err');
    }
  };
  xhr.onerror = () => { if (status) status.textContent = '网络错误'; toast('上传失败', 'err'); };
  xhr.send(form);
}

function fileExportModal() {
  const paths = fileSelectedPaths();
  if (!paths.length) { toast('请先选择要导出的音频', 'err'); return; }
  openModal(`
    <div class="modal-head"><div><div class="modal-title">导出 / 转换</div><div class="modal-sub">${paths.length} 个文件 · 源文件不会被修改</div></div><button class="icon-btn modal-close" data-action="modal-cancel">${ICONS.close}</button></div>
    <div class="modal-body">
      <label class="field"><span>输出格式</span>
        <select class="select" id="exportFormat">
          <option value="original">保持原格式（复制）</option>
          <option value="flac">FLAC（无损）</option>
          <option value="mp3">MP3</option>
          <option value="m4a">M4A / AAC</option>
          <option value="ogg">OGG Vorbis</option>
          <option value="opus">Opus</option>
          <option value="wav">WAV（体积较大）</option>
        </select>
      </label>
      <label class="field" id="exportBitrateWrap" style="display:none"><span>码率</span>
        <select class="select" id="exportBitrate">
          <option value="128">128 kbps</option>
          <option value="192">192 kbps</option>
          <option value="256">256 kbps</option>
          <option value="320" selected>320 kbps</option>
        </select>
      </label>
      <label class="field"><span>输出文件夹</span><input class="input" id="exportDest" value="导出"></label>
      <p class="file-modal-note">默认「保持原格式」只复制，不转码。FLAC/WAV 为无损；MP3/M4A/OGG/Opus 会重新编码。转换在后台完成，大文件请保持页面打开。</p>
    </div>
    <div class="modal-foot"><div class="spacer"></div><button class="btn btn-ghost" data-action="modal-cancel">取消</button><button class="btn btn-primary" data-action="file-export-submit">开始导出</button></div>`);
}

async function fileExportSubmit() {
  const format = $('#exportFormat')?.value || 'original';
  const bitrate = $('#exportBitrate')?.value || '320';
  const dest = $('#exportDest')?.value || '导出';
  const paths = fileSelectedPaths();
  if (!paths.length) { toast('请先选择要导出的音频', 'err'); return; }
  try {
    toast('正在导出，请稍候…', 'ok', 3000);
    const data = await api('/api/files/export', { method: 'POST', body: { paths, format, bitrate, dest } });
    closeModal(); state.files.selected.clear();
    toast(`已导出 ${data.saved?.length || 0} 个文件${data.errors?.length ? `，${data.errors.length} 个失败` : ''}`, data.errors?.length ? 'err' : 'ok');
    await loadFiles(); loadOverview().catch(() => {});
  } catch (err) { toast(err.message, 'err'); }
}

async function fileEditText(path) {
  try {
    const data = await api(`/api/files/text?path=${encodeURIComponent(path)}`);
    openModal(`
      <div class="modal-head"><div><div class="modal-title">编辑 ${esc(path.split('/').pop())}</div><div class="modal-sub">${esc(path)}</div></div><button class="icon-btn modal-close" data-action="modal-cancel">${ICONS.close}</button></div>
      <div class="modal-body">
        <textarea class="textarea file-edit-area" id="fileTextArea">${esc(data.text || '')}</textarea>
        <p class="file-modal-note">保存会覆盖原文件；历史里可以撤销。最大 2 MB。</p>
      </div>
      <div class="modal-foot"><div class="spacer"></div><button class="btn btn-ghost" data-action="modal-cancel">取消</button><button class="btn btn-primary" data-action="file-save-text" data-path="${esc(path)}">保存</button></div>`, { wide: true });
  } catch (err) { toast(err.message, 'err'); }
}

async function fileSaveText(path) {
  const text = $('#fileTextArea')?.value || '';
  try {
    await api('/api/files/text', { method: 'PUT', body: { path, text } });
    closeModal(); toast('文本已保存', 'ok'); await loadFiles();
  } catch (err) { toast(err.message, 'err'); }
}

async function fileOpenHistory() {
  try {
    const data = await api('/api/files/history?limit=30');
    const items = data.items || [];
    const rows = items.map((item) => `<div class="file-history-item ${item.undone ? 'undone' : ''}">
      <div class="file-history-main"><div class="file-history-title">${esc(item.action || '操作')}</div><div class="file-history-sub">${esc(item.summary || '')} · ${esc(item.ts || '')}${item.undone ? ' · 已撤销' : ''}</div></div>
      <div class="file-history-actions">${item.can_undo ? `<button class="file-mini" data-action="file-undo-to" data-id="${esc(item.id)}">撤销到此</button>` : ''}</div>
    </div>`).join('') || '<div class="file-empty" style="padding:24px">还没有操作记录</div>';
    openModal(`
      <div class="modal-head"><div><div class="modal-title">操作历史</div><div class="modal-sub">保留最近 ${data.limit} 次 · 原始快照永久保留</div></div><button class="icon-btn modal-close" data-action="modal-cancel">${ICONS.close}</button></div>
      <div class="modal-body">
        <div style="display:flex;gap:8px;flex-wrap:wrap;margin-bottom:12px">
          <button class="btn btn-soft btn-sm" data-action="file-undo" ${data.undoable ? '' : 'disabled'}>撤销上一步</button>
          <button class="btn btn-danger btn-sm" data-action="file-restore-original">恢复到原始目录</button>
        </div>
        <p class="file-modal-note">原始快照：${data.original?.exists ? `${esc(data.original.created_at)} · ${data.original.file_count} 个文件` : '尚未建立（第一次修改前会自动建立）'}</p>
        <div class="file-history" style="margin-top:12px">${rows}</div>
      </div>
      <div class="modal-foot"><div class="spacer"></div><button class="btn btn-ghost" data-action="modal-cancel">关闭</button></div>`, { wide: true });
  } catch (err) { toast(err.message, 'err'); }
}

async function fileUndo(operationId = '') {
  try {
    const data = await api('/api/files/undo', { method: 'POST', body: operationId ? { operation_id: operationId } : {} });
    const errors = data.errors || [];
    toast(errors.length ? `已撤销，${errors.length} 项失败` : '已撤销', errors.length ? 'err' : 'ok');
    closeModal(); await loadFiles(); loadOverview().catch(() => {});
  } catch (err) { toast(err.message, 'err'); }
}

async function fileRestoreOriginal() {
  const ok = await confirmDialog('恢复到原始目录？', '将撤销所有文件操作，并按第一次修改前的快照恢复路径；多余文件会移入回收站。音频内容不会被重新编码。', '恢复');
  if (!ok) return;
  try {
    const data = await api('/api/files/restore-original', { method: 'POST', body: {} });
    closeModal();
    toast(`已恢复 ${data.restored || 0} 个文件${data.missing?.length ? `，${data.missing.length} 个缺失` : ''}`, data.missing?.length ? 'err' : 'ok');
    await loadFiles(); loadOverview().catch(() => {});
  } catch (err) { toast(err.message, 'err'); }
}

function fileOrganizePreviewHtml(data, kind) {
  const rows = (data.items || []).slice(0, 30).map((item) => `<div class="file-plan-row"><span class="from">${esc(item.source)}</span><span>→</span><span class="to">${esc(item.target || item.reason || '')}</span></div>`).join('');
  return `<div class="file-plan">
    <div class="file-plan-summary"><span>可处理 ${data.planned || 0}</span><span>跳过 ${data.skipped || 0}</span><span>错误 ${data.errors || 0}</span><span>只预览，尚未修改</span></div>
    <div class="file-plan-list">${rows || '<div class="dim" style="font-size:11px">没有需要移动的文件</div>'}${(data.items || []).length > 30 ? '<div class="dim" style="font-size:10px;margin-top:8px">只显示前 30 项…</div>' : ''}</div>
  </div>`;
}

async function fileOrganizeModal() {
  await loadDirChoices();
  openModal(`
    <div class="modal-head"><div><div class="modal-title">一键整理</div><div class="modal-sub">可选择范围、目标根目录、歌手/专辑层级和文件名</div></div><button class="icon-btn modal-close" data-action="modal-cancel">${ICONS.close}</button></div>
    <div class="modal-body">
      <div class="form-inline">
        <label class="field"><span>整理范围</span>
          <select class="select" id="organizeScope">
            <option value="all">整个音乐库</option>
            <option value="folder">指定的当前文件夹</option>
            <option value="artist">指定的歌手</option>
          </select>
        </label>
        <label class="field"><span>目标根目录</span>
          <select class="select" id="organizeBaseDir">${dirOptionsHtml('', '音乐库根目录')}</select>
        </label>
      </div>
      <div class="form-row" id="organizeScopeFolderWrap" style="display:none"><div class="form-label">要整理的文件夹 <span class="hint">从当前音乐目录读取</span></div><select class="select" id="organizeScopeFolder">${dirOptionsHtml(state.files.path, '音乐库根目录')}</select></div>
      <div class="form-row" id="organizeScopeArtistWrap" style="display:none"><div class="form-label">要整理的歌手</div><input class="input" id="organizeScopeArtist" placeholder="例如：周杰伦"></div>
      <div class="form-row"><div class="form-label">目录结构</div>
        <select class="select" id="organizeMode">
          <option value="keep" selected>保持当前目录结构（只改文件名）</option>
          <option value="artist_album">歌手 / 专辑</option>
          <option value="artist">歌手</option>
          <option value="album">专辑</option>
          <option value="genre">流派</option>
          <option value="year">年份</option>
          <option value="custom">自定义目录模板</option>
        </select>
      </div>
      <div class="form-row" id="organizeDirWrap"><div class="form-label">目录模板 <span class="hint">例如 {artist}/{album}</span></div><input class="input" id="organizeDirTemplate" value="{artist}/{album}"></div>
      <div class="form-row"><div class="form-label">文件名</div>
        <select class="select" id="organizeFileMode">
          <option value="keep">保持原文件名</option>
          <option value="artist_title">歌手 - 歌名</option>
          <option value="title_artist">歌名 - 歌手</option>
          <option value="track_title">序号 - 歌名</option>
          <option value="title">只使用歌名</option>
          <option value="custom">自定义文件名模板</option>
        </select>
      </div>
      <div class="form-row" id="organizeFileWrap" style="display:none"><div class="form-label">文件名模板</div><input class="input" id="organizeFileTemplate" value="{artist} - {title}"></div>
      <div class="form-row"><div class="form-label">保持不动的歌手 <span class="hint">可选，逗号分隔</span></div><input class="input" id="organizeKeepArtists" placeholder="例如：周杰伦, 陈奕迅"></div>
      <p class="file-modal-note">可用变量：{artist} {album} {title} {track} {year} {genre}。预览确认后才会移动；同名文件会自动加序号。只整理「其他」时可把范围设为当前文件夹、目标根目录设为「其他」。</p>
      <div id="organizePreview"></div>
    </div>
    <div class="modal-foot"><div class="spacer"></div><button class="btn btn-ghost" data-action="modal-cancel">取消</button><button class="btn btn-soft" data-action="file-organize-preview">预览</button><button class="btn btn-primary" data-action="file-organize-apply" id="organizeApply" disabled>开始整理</button></div>`, { wide: true });
  setTimeout(() => { const wrap = $('#organizeDirWrap'); if (wrap) wrap.style.display = 'none'; }, 0);
}

function fileRenameBatchModal() {
  openModal(`
    <div class="modal-head"><div><div class="modal-title">批量命名</div><div class="modal-sub">只改文件名，不移动文件夹</div></div><button class="icon-btn modal-close" data-action="modal-cancel">${ICONS.close}</button></div>
    <div class="modal-body">
      <div class="form-row"><div class="form-label">命名规则</div>
        <select class="select" id="renameMode">
          <option value="artist_title">歌手 - 歌名</option>
          <option value="title_artist">歌名 - 歌手</option>
          <option value="track_title">序号 - 歌名</option>
          <option value="title">只使用歌名</option>
          <option value="custom">自定义模板</option>
        </select>
      </div>
      <div class="form-row" id="renameTemplateWrap" style="display:none"><div class="form-label">文件名模板</div><input class="input" id="renameTemplate" value="{artist} - {title}"></div>
      <p class="file-modal-note">可用变量：{artist} {album} {title} {track} {year} {genre}。预览确认后才会修改。</p>
      <div id="renamePreview"></div>
    </div>
    <div class="modal-foot"><div class="spacer"></div><button class="btn btn-ghost" data-action="modal-cancel">取消</button><button class="btn btn-soft" data-action="file-rename-preview">预览</button><button class="btn btn-primary" data-action="file-rename-apply" id="renameApply" disabled>开始命名</button></div>`, { wide: true });
  setTimeout(() => { const wrap = $('#renameTemplateWrap'); if (wrap) wrap.style.display = 'none'; }, 0);
}

async function fileOrganizePreview() {
  const mode = $('#organizeMode')?.value || 'keep';
  const fileMode = $('#organizeFileMode')?.value || 'keep';
  const fileTemplates = {
    keep: '',
    artist_title: '{artist} - {title}',
    title_artist: '{title} - {artist}',
    track_title: '{track:02d} - {title}',
    title: '{title}',
  };
  const fileTemplate = fileMode === 'custom' ? ($('#organizeFileTemplate')?.value || '') : (fileTemplates[fileMode] || '');
  try {
    const data = await api('/api/files/organize/preview', {
      method: 'POST',
      body: {
        mode,
        dir_template: $('#organizeDirTemplate')?.value || '',
        file_template: fileTemplate,
        keep_artists: $('#organizeKeepArtists')?.value || '',
        scope: $('#organizeScope')?.value || 'all',
        scope_path: $('#organizeScopeFolder')?.value || '',
        scope_artist: $('#organizeScopeArtist')?.value || '',
        base_dir: $('#organizeBaseDir')?.value || '',
      },
    });
    state.files.plan = data.plan_id;
    $('#organizePreview').innerHTML = fileOrganizePreviewHtml(data, 'organize');
    $('#organizeApply').disabled = !data.planned;
  } catch (err) { toast(err.message, 'err'); }
}

async function fileOrganizeApply() {
  if (!state.files.plan) return;
  const ok = await confirmDialog('开始整理？', '会按预览移动文件；原始目录和这次操作都可以在历史里恢复。', '开始整理');
  if (!ok) return;
  try {
    const data = await api('/api/files/plan/apply', { method: 'POST', body: { plan_id: state.files.plan } });
    state.files.plan = null; closeModal();
    toast(`整理完成：${data.applied || 0} 个文件${data.errors?.length ? `，${data.errors.length} 个失败` : ''}`, data.errors?.length ? 'err' : 'ok');
    await loadFiles(); loadOverview().catch(() => {});
  } catch (err) { toast(err.message, 'err'); }
}

async function fileRenamePreview() {
  const mode = $('#renameMode')?.value || 'artist_title';
  const template = mode === 'custom' ? ($('#renameTemplate')?.value || '') : '';
  try {
    const data = await api('/api/files/rename/preview', { method: 'POST', body: { mode, file_template: template } });
    state.files.plan = data.plan_id;
    $('#renamePreview').innerHTML = fileOrganizePreviewHtml(data, 'rename');
    $('#renameApply').disabled = !data.planned;
  } catch (err) { toast(err.message, 'err'); }
}

async function fileRenameApply() {
  if (!state.files.plan) return;
  const ok = await confirmDialog('开始批量命名？', '会按预览修改文件名；原始名称可以在历史里恢复。', '开始命名');
  if (!ok) return;
  try {
    const data = await api('/api/files/plan/apply', { method: 'POST', body: { plan_id: state.files.plan } });
    state.files.plan = null; closeModal();
    toast(`命名完成：${data.applied || 0} 个文件${data.errors?.length ? `，${data.errors.length} 个失败` : ''}`, data.errors?.length ? 'err' : 'ok');
    await loadFiles(); loadOverview().catch(() => {});
  } catch (err) { toast(err.message, 'err'); }
}

async function fileRepairLibrary() {
  try {
    const data = await api('/api/files/repair', { method: 'POST', body: {} });
    if (!data.job_id) { toast(data.reason || '没有需要修复的内容', 'ok'); return; }
    toast(`已提交修复：${data.count || ''} 首`, 'ok');
    watchJob(data.job_id, { onDone: () => { loadFiles().catch(() => {}); loadOverview().catch(() => {}); } });
  } catch (err) { toast(err.message, 'err'); }
}

function fileDownload(path) {
  window.location.href = `/api/files/download?path=${encodeURIComponent(path)}`;
}


function renderRepair() {
  const o = state.overview;
  const s = o.stats || {};
  const status = o.automation.status || {};
  const timingCount = Number(s.timing_issues || 0);
  const job = state.job;
  const recentJobs = (o.jobs || []).slice(0, 6).map((j) => `
    <div class="recent-job">
      <span class="jtitle">${esc(j.title)}</span>
      <span class="jtime">${relativeTime(j.finished_at || j.created_at)}</span>
      <span class="jstatus badge ${j.status === 'done' ? 'badge-ok' : j.status === 'error' ? 'badge-bad' : 'badge-accent'}">${jobStatusText(j.status)}</span>
    </div>`).join('') || '<div class="dim" style="font-size:12px">还没有任务记录</div>';

  $('#view').innerHTML = `
    <div class="repair">
      <div class="repair-cards">
        <div class="card repair-stat">
          <div class="top"><span>待补歌词</span><span class="badge badge-bad">待补</span></div>
          <div class="num">${s.missing_lyrics || 0}<small>首</small></div>
          <div class="stat-foot"><div class="mini-bar"><span style="width:${pct((s.total || 0) - (s.missing_lyrics || 0), s.total || 0)}%"></span></div><span class="stat-note">覆盖 ${pct((s.total || 0) - (s.missing_lyrics || 0), s.total || 0)}%</span></div>
        </div>
        <div class="card repair-stat">
          <div class="top"><span>待补封面</span><span class="badge badge-warn">待补</span></div>
          <div class="num">${s.missing_cover || 0}<small>首</small></div>
          <div class="stat-foot"><div class="mini-bar"><span style="width:${pct((s.total || 0) - (s.missing_cover || 0), s.total || 0)}%"></span></div><span class="stat-note">覆盖 ${pct((s.total || 0) - (s.missing_cover || 0), s.total || 0)}%</span></div>
        </div>
        <div class="card repair-stat">
          <div class="top"><span>调度器</span><span class="badge ${status.running ? 'badge-ok' : 'badge'}">${status.running ? '运行中' : '未运行'}</span></div>
          <div class="num" style="font-size:18px;line-height:1.9">${status.next_fix_at ? timeUntil(status.next_fix_at) : '未设置'}<small>下次自动修复</small></div>
          <div class="stat-foot"><span class="stat-note">${status.last_tick ? '上次检查 ' + relativeTime(status.last_tick) : '等待首次检查'}</span></div>
        </div>
      </div>

      ${timingCount ? `<div class="timing-banner">
        <div><b>检测到 ${timingCount} 首歌词时间轴异常</b><span>歌词时间戳与音频时长/位置对不上，可自动重新匹配，不会动音频文件。</span></div>
        <button class="btn btn-soft btn-sm" data-action="fix-timing">自动校准时间轴</button>
      </div>` : ''}

      <div class="repair-grid">
        <section class="card form-card">
          <div class="card-head"><div><div class="card-title">开始修复</div><div class="card-sub">自动搜索最匹配的歌词与封面，并写入音频标签</div></div></div>
          <div class="form-row">
            <div class="form-label">处理范围 <span class="hint">选择要处理的曲目</span></div>
            <div class="segmented" id="scopeChips">
              <button class="seg active" data-scope="missing_any">所有缺失</button>
              <button class="seg" data-scope="missing_lyrics">仅缺歌词</button>
              <button class="seg" data-scope="missing_cover">仅缺封面</button>
              <button class="seg" data-scope="issues">标签异常</button>
              <button class="seg" data-scope="bad_lyrics">时间轴异常</button>
              <button class="seg" data-scope="all">全部重做</button>
            </div>
          </div>
          <div class="form-row">
            <div class="form-label">修复内容</div>
            <div class="segmented">
              <label class="seg active"><input type="checkbox" id="targetLyrics" checked>歌词</label>
              <label class="seg active"><input type="checkbox" id="targetCover" checked>封面</label>
              <label class="seg"><input type="checkbox" id="targetTags">补全标签</label>
            </div>
          </div>
          <div class="form-inline">
            <label class="field"><span>最多处理（0 = 不限制）</span><input class="input" id="repairLimit" type="number" min="0" value="0"></label>
            <div class="form-row" style="margin:0">
              <span class="form-label">选项</span>
              <label class="checkline"><input type="checkbox" id="repairForce">已有内容也重新获取</label>
              <label class="checkline"><input type="checkbox" id="repairDry">只预览，不写入</label>
            </div>
          </div>
          <div style="display:flex;gap:8px;margin-top:20px">
            <button class="btn btn-primary" data-action="start-batch">${ICONS.wrench}<span>开始修复</span></button>
            <button class="btn btn-ghost" data-action="goto-library">先浏览曲库</button>
          </div>
          <p class="modal-note" style="margin-top:14px">修复前会自动备份原标签，可在单曲详情里撤销。数据源按「设置 → 在线数据源」中的顺序依次尝试。</p>
        </section>

        <section class="card job-panel" id="jobPanel">${renderJobPanel(job)}</section>
      </div>

      <div class="dash-grid">
        <section class="card card-pad">
          <div class="card-head"><div><div class="card-title">最近任务</div><div class="card-sub">扫描与批量修复记录</div></div><button class="link-btn" data-action="refresh">刷新</button></div>
          <div class="recent-jobs">${recentJobs}</div>
        </section>
        <section class="card card-pad">
          <div class="card-head"><div><div class="card-title">操作提示</div><div class="card-sub">关于匹配与写入</div></div></div>
          <div class="auto-summary">
            <div class="auto-summary-row"><span class="label">匹配策略</span><span class="value">标题优先 + 歌手/时长/版本加权，低于 ${state.overview?.stats ? '' : ''}55 分不会自动写入</span></div>
            <div class="auto-summary-row"><span class="label">写入位置</span><span class="value">歌词写入音频内嵌标签；可选同时写同名 .lrc</span></div>
            <div class="auto-summary-row"><span class="label">封面来源</span><span class="value">网易云 / QQ 音乐 / iTunes，自动压缩为 JPEG</span></div>
            <div class="auto-summary-row"><span class="label">Navidrome</span><span class="value">${o.navidrome.enabled ? '已启用联动' : '未启用'}</span></div>
          </div>
        </section>
      </div>
    </div>`;
}

/* -------------------------------------------------------------------------
   自动化
   ------------------------------------------------------------------------- */

function autoSwitch(key, title, desc, checked) {
  return `<div class="switch-row">
    <div class="switch-text"><div class="switch-title">${esc(title)}</div><div class="switch-sub">${esc(desc)}</div></div>
    <label class="toggle"><input type="checkbox" data-auto-key="${key}" ${checked ? 'checked' : ''}><span></span></label>
  </div>`;
}

function conversionRuleHtml(from = 'wav', to = 'flac') {
  const formatOptions = (selected, formats) => formats.map((f) => `<option value="${f}" ${f === selected ? 'selected' : ''}>${f.toUpperCase()}</option>`).join('');
  return `<div class="conv-rule" data-conv-rule>
    <select class="select conv-from">${formatOptions(from, ['flac','mp3','m4a','ogg','opus','wav','aiff','ape','wv'])}</select>
    <span class="conv-arrow">→</span>
    <select class="select conv-to">${formatOptions(to, ['flac','mp3','m4a','ogg','opus','wav'])}</select>
    <button class="icon-btn" data-action="remove-conv-rule" title="删除规则">×</button>
  </div>`;
}

function collectConversion() {
  const rules = $$('#convRules .conv-rule').map((row) => ({
    from: ($('.conv-from', row) || {}).value || '',
    to: ($('.conv-to', row) || {}).value || '',
  })).filter((rule) => rule.from && rule.to && rule.from !== rule.to);
  return {
    enabled: Boolean($('#convEnabled') && $('#convEnabled').checked),
    mode: ($('#convMode') || {}).value || 'preserve',
    target: ($('#convTarget') || {}).value || 'flac',
    rules,
    replace_original: Boolean($('#convReplace') && $('#convReplace').checked),
    keep_backup: Boolean($('#convBackup') && $('#convBackup').checked),
    lossless_only: Boolean($('#convLosslessOnly') && $('#convLosslessOnly').checked),
  };
}

async function saveConversion(showToast = true) {
  const data = await api('/api/settings', { method: 'PUT', body: { config: { conversion: collectConversion() } } });
  if (state.overview) state.overview.conversion = data.config.conversion;
  if (showToast) toast('格式转换设置已保存');
  return data.config.conversion;
}

async function previewConversion() {
  const box = $('#convPreview');
  if (box) box.textContent = '检测中…';
  try {
    const data = await api('/api/convert/preview');
    if (box) box.textContent = `${data.count} 首待转换${data.items && data.items[0] ? ' · ' + data.items[0].title : ''}`;
    toast(data.count ? `检测到 ${data.count} 首待转换` : '没有符合规则的待转换文件', 'ok');
  } catch (err) {
    if (box) box.textContent = '检测失败';
    toast(err.message, 'err');
  }
}

async function runConversion() {
  try {
    await saveConversion(false);
    const data = await api('/api/convert/run', { method: 'POST', body: {} });
    if (!data.job_id) {
      toast('没有符合规则的待转换文件', 'ok');
      return;
    }
    toast('已开始格式转换，进度可在下方任务面板查看');
    watchJob(data.job_id, {
      onDone: async () => {
        await loadOverview();
        renderAutomation();
      },
    });
  } catch (err) {
    toast(err.message, 'err');
  }
}

async function saveOrganization() {
  const org = {
    enabled: Boolean($('#orgEnabled') && $('#orgEnabled').checked),
    mode: ($('#orgMode') || {}).value || 'artist_album',
    base_dir: ($('#orgBaseDir') || {}).value || '',
    file_template: ($('#orgFileTemplate') || {}).value || '{artist} - {title}',
    keep_artists: ($('#orgKeepArtists') || {}).value || '',
  };
  const data = await api('/api/settings', { method: 'PUT', body: { config: { organization: org } } });
  if (state.overview) state.overview.organization = data.config.organization;
  toast('自动整理设置已保存');
}

async function loadDirChoices() {
  try {
    const data = await api('/api/files/dirs');
    state.dirChoices = data.dirs || [];
  } catch (err) {
    state.dirChoices = [];
  }
  return state.dirChoices;
}

function dirOptionsHtml(selected = '', rootLabel = '音乐库根目录') {
  const choices = state.dirChoices || [];
  const options = [`<option value="" ${selected === '' ? 'selected' : ''}>${esc(rootLabel)}</option>`];
  choices.forEach((item) => {
    if (!item.path) return;
    options.push(`<option value="${esc(item.path)}" ${item.path === selected ? 'selected' : ''}>${esc(item.name)}</option>`);
  });
  return options.join('');
}

async function saveDuplicates() {
  const enabled = Boolean($('#dupAutoClean') && $('#dupAutoClean').checked);
  const data = await api('/api/settings', { method: 'PUT', body: { config: { duplicates: { auto_clean: enabled } } } });
  if (state.overview) state.overview.duplicates = data.config.duplicates;
  toast(enabled ? '已开启自动清理确认重复' : '已关闭自动清理');
}

async function autoCleanDuplicates() {
  if (!(await confirmDialog('清理确认重复？', '只会清理高置信重复组，并移入回收站；误判的组请先点「不是重复」。', '开始清理'))) return;
  try {
    const data = await api('/api/duplicates/auto-clean', { method: 'POST' });
    toast(`已清理 ${(data.moved || []).length} 首重复歌曲`);
    await loadOverview();
    if (state.route === 'library') loadLibrary();
    renderAutomation();
  } catch (err) { toast(err.message, 'err'); }
}

async function ignoreDuplicate(trackId) {
  try {
    await api('/api/duplicates/ignore', { method: 'POST', body: { track_id: trackId } });
    toast('已忽略这组误判');
    await loadOverview();
    loadLibrary();
  } catch (err) { toast(err.message, 'err'); }
}

function renderAutomation() {
  const o = state.overview;
  const a = o.automation || {};
  const c = o.conversion || {};
  const org = o.organization || {};
  const dup = o.duplicates || {};
  const status = a.status || {};
  const days = [1, 2, 3, 4, 5, 6, 7];
  const dayNames = ['一', '二', '三', '四', '五', '六', '日'];
  const activeDays = a.schedule_days || days;
  const job = state.job;
  const convMode = c.mode || 'preserve';
  const ruleRows = (c.rules && c.rules.length ? c.rules : [{ from: 'wav', to: 'flac' }]);
  const convFormats = ['flac', 'mp3', 'm4a', 'ogg', 'opus', 'wav'];
  const convOptions = (selected, list) => list.map((f) => `<option value="${f}" ${f === selected ? 'selected' : ''}>${f.toUpperCase()}</option>`).join('');
  $('#view').innerHTML = `
    <div class="automation">
      <div class="auto-grid">
        <section class="card card-pad">
          <div class="card-head"><div><div class="card-title">自动任务开关</div><div class="card-sub">所有任务都在后台执行，不会打断播放</div></div></div>
          <div class="switch-list">
            ${autoSwitch('scan_on_start', '启动时扫描', '服务启动后自动扫描一次音乐目录，发现新歌', a.scan_on_start)}
            ${autoSwitch('watch_enabled', '监听目录变化', '新音乐放进文件夹后，最多 1 分钟内自动扫描', a.watch_enabled)}
            ${autoSwitch('watch_changes', '变化检测', '定期检查文件数量与修改时间，比全量扫描更轻量', a.watch_changes !== false)}
            ${autoSwitch('auto_fix_new', '新歌自动修复', '扫描到新歌且缺少歌词/封面时，自动排队修复', a.auto_fix_new)}
            ${autoSwitch('fix_on_play', '播放时自动修复', '在 Web 播放器里播放缺内容的歌时自动补齐', a.fix_on_play)}
            ${autoSwitch('auto_fix_timing', '时间轴自动校准', '扫描发现歌词与音频时间对不上时自动重新匹配', a.auto_fix_timing !== false)}
            ${autoSwitch('schedule_enabled', '每日自动修复', '每天固定时间批量处理缺失的歌词与封面', a.schedule_enabled)}
          </div>
        </section>

        <section class="card card-pad">
          <div class="card-head"><div><div class="card-title">调度参数</div><div class="card-sub">保存后立即生效</div></div></div>
          <div class="form-inline">
            <label class="field"><span>扫描间隔（分钟）</span><input class="input" id="autoInterval" type="number" min="0" max="1440" value="${a.watch_interval_min ?? 30}"></label>
            <label class="field"><span>每日修复时间</span><input class="input" id="autoTime" type="time" value="${esc(a.schedule_time || '03:30')}"></label>
          </div>
          <div class="form-row" style="margin-top:16px">
            <span class="form-label">每周执行日</span>
            <div class="days" id="autoDays">
              ${days.map((d) => `<button class="day-chip ${activeDays.includes(d) ? 'active' : ''}" data-action="day-chip" data-day="${d}">周${dayNames[d - 1]}</button>`).join('')}
            </div>
          </div>
          <div class="form-inline" style="margin-top:16px">
            <label class="field"><span>每轮最多修复</span><input class="input" id="autoMax" type="number" min="1" max="500" value="${a.max_fix_per_run || 40}"></label>
            <label class="field"><span>自动修复内容</span>
              <select class="select" id="autoTargets">
                <option value="lyrics,cover" ${(a.targets || []).includes('lyrics') && (a.targets || []).includes('cover') ? 'selected' : ''}>歌词 + 封面</option>
                <option value="lyrics" ${(a.targets || []).join(',') === 'lyrics' ? 'selected' : ''}>仅歌词</option>
                <option value="cover" ${(a.targets || []).join(',') === 'cover' ? 'selected' : ''}>仅封面</option>
              </select>
            </label>
          </div>
          <div style="display:flex;gap:8px;margin-top:20px">
            <button class="btn btn-primary" data-action="save-automation">${ICONS.check}<span>保存设置</span></button>
            <button class="btn btn-ghost" data-action="auto-scan-now">立即扫描</button>
            <button class="btn btn-ghost" data-action="auto-fix-now">立即修复一轮</button>
          </div>
          <p class="modal-note" style="margin-top:12px">仅用命令行部署时，可运行 <code class="mono">morg auto --daemon</code> 作为常驻调度器。</p>
        </section>
      </div>

      <div class="auto-grid">
        <section class="card auto-status-card">
          <div class="card-head"><div><div class="card-title">调度器状态</div><div class="card-sub">本次 Web 服务运行期间</div></div>
            <span class="status-pill ${status.running ? '' : 'off'}"><span class="dot"></span>${status.running ? '运行中' : '未运行'}</span></div>
          <div class="auto-status-list">
            <div class="auto-status-row"><span class="k">下次扫描</span><span class="v ${status.next_scan_at ? '' : 'dim'}">${status.next_scan_at ? timeUntil(status.next_scan_at) : '未安排'}</span></div>
            <div class="auto-status-row"><span class="k">下次修复</span><span class="v ${status.next_fix_at ? '' : 'dim'}">${status.next_fix_at ? timeUntil(status.next_fix_at) : '未安排'}</span></div>
            <div class="auto-status-row"><span class="k">上次检查</span><span class="v ${status.last_tick ? '' : 'dim'}">${status.last_tick ? relativeTime(status.last_tick) : '等待首次检查'}</span></div>
            <div class="auto-status-row"><span class="k">扫描任务</span><span class="v ${status.scan_running ? '' : 'dim'}">${status.scan_running ? '正在运行' : '空闲'}</span></div>
            <div class="auto-status-row"><span class="k">修复任务</span><span class="v ${status.fix_running ? '' : 'dim'}">${status.fix_running ? '正在运行' : '空闲'}</span></div>
            <div class="auto-status-row"><span class="k">最后错误</span><span class="v ${status.last_error ? '' : 'dim'}">${status.last_error ? esc(status.last_error) : '无'}</span></div>
          </div>
        </section>
        <section class="card job-panel" id="jobPanel">${renderJobPanel(job)}</section>
      </div>

      <div class="auto-grid">
        <section class="card card-pad">
          <div class="card-head"><div><div class="card-title">格式转换自动化</div><div class="card-sub">默认保持原格式；可整库转、按规则转，替换前自动备份</div></div>
            <span class="status-pill ${c.enabled && convMode !== 'preserve' ? '' : 'off'}"><span class="dot"></span>${c.enabled && convMode !== 'preserve' ? '已启用' : '未启用'}</span></div>
          <div class="switch-list">
            <div class="switch-row"><div class="switch-copy"><b>启用自动转换</b><span>扫描到符合规则的文件后自动排队转换</span></div><label class="toggle"><input type="checkbox" id="convEnabled" ${c.enabled ? 'checked' : ''}><span></span></label></div>
            <div class="switch-row"><div class="switch-copy"><b>替换原文件</b><span>关闭时输出到「转换输出」文件夹，不修改原文件</span></div><label class="toggle"><input type="checkbox" id="convReplace" ${c.replace_original ? 'checked' : ''}><span></span></label></div>
            <div class="switch-row"><div class="switch-copy"><b>保留备份</b><span>替换原文件前保存标签、歌词、封面快照</span></div><label class="toggle"><input type="checkbox" id="convBackup" ${c.keep_backup !== false ? 'checked' : ''}><span></span></label></div>
            <div class="switch-row"><div class="switch-copy"><b>无损源优先</b><span>目标为 FLAC/WAV 时，跳过 MP3/M4A/OGG/Opus 等有损源，避免“假无损”</span></div><label class="toggle"><input type="checkbox" id="convLosslessOnly" ${c.lossless_only !== false ? 'checked' : ''}><span></span></label></div>
          </div>
          <div class="form-inline" style="margin-top:16px">
            <label class="field"><span>转换模式</span>
              <select class="select" id="convMode">
                <option value="preserve" ${convMode === 'preserve' ? 'selected' : ''}>保持原格式（默认）</option>
                <option value="all" ${convMode === 'all' ? 'selected' : ''}>全部转为目标格式</option>
                <option value="rules" ${convMode === 'rules' ? 'selected' : ''}>按来源规则转换</option>
              </select>
            </label>
            <label class="field"><span>目标格式</span><select class="select" id="convTarget">${convOptions(c.target || 'flac', convFormats)}</select></label>
          </div>
          <div style="display:flex;gap:8px;margin-top:18px;flex-wrap:wrap">
            <button class="btn btn-primary" data-action="save-conversion">${ICONS.check}<span>保存转换设置</span></button>
            <button class="btn btn-ghost" data-action="convert-preview">检测待转换</button>
            <button class="btn btn-ghost" data-action="convert-now">立即转换</button>
          </div>
          <p class="modal-note" style="margin-top:12px">FLAC/WAV 为无损；MP3/M4A/OGG/Opus 会重新编码。转换后标签、歌词、封面会复制到新文件。自动转换需开启「替换原文件」；关闭时请手动点「立即转换」输出到转换输出文件夹。「无损源优先」会跳过有损源转 FLAC/WAV，避免假无损。</p>
        </section>

        <section class="card card-pad">
          <div class="card-head"><div><div class="card-title">转换规则与状态</div><div class="card-sub">按来源格式决定目标格式</div></div></div>
          <div id="convRules" class="conv-rules">
            ${ruleRows.map((rule) => `<div class="conv-rule" data-conv-rule>
              <select class="select conv-from">${convOptions(rule.from || 'wav', ['flac','mp3','m4a','ogg','opus','wav','aiff','ape','wv'])}</select>
              <span class="conv-arrow">→</span>
              <select class="select conv-to">${convOptions(rule.to || 'flac', convFormats)}</select>
              <button class="icon-btn" data-action="remove-conv-rule" title="删除规则">×</button>
            </div>`).join('')}
          </div>
          <button class="btn btn-ghost btn-sm" data-action="add-conv-rule" style="margin-top:10px">＋ 添加规则</button>
          <div class="auto-status-list" style="margin-top:16px">
            <div class="auto-status-row"><span class="k">上次转换</span><span class="v ${c.last_run_at ? '' : 'dim'}">${c.last_run_at ? relativeTime(c.last_run_at) : '尚未运行'}</span></div>
            <div class="auto-status-row"><span class="k">上次结果</span><span class="v ${c.last_result ? '' : 'dim'}">${c.last_result ? esc(c.last_result) : '—'}</span></div>
            <div class="auto-status-row"><span class="k">待转换</span><span class="v" id="convPreview">点「检测待转换」</span></div>
          </div>
          <p class="modal-note" style="margin-top:12px">规则模式只转换命中的来源格式；保持原格式模式不会做任何转换。</p>
        </section>
      </div>

      <div class="auto-grid">
        <section class="card card-pad">
          <div class="card-head"><div><div class="card-title">新歌自动建文件夹</div><div class="card-sub">默认关闭；新导入歌曲按歌手/专辑自动归档</div></div>
            <span class="status-pill ${org.enabled ? '' : 'off'}"><span class="dot"></span>${org.enabled ? '已启用' : '未启用'}</span></div>
          <div class="switch-list">
            <div class="switch-row"><div class="switch-copy"><b>启用自动整理</b><span>只在扫描到新歌时整理，不动已有歌曲</span></div><label class="toggle"><input type="checkbox" id="orgEnabled" ${org.enabled ? 'checked' : ''}><span></span></label></div>
          </div>
          <div class="form-inline" style="margin-top:16px">
            <label class="field"><span>目录层级</span>
              <select class="select" id="orgMode">
                <option value="artist_album" ${(org.mode || 'artist_album') === 'artist_album' ? 'selected' : ''}>歌手 / 专辑</option>
                <option value="artist" ${org.mode === 'artist' ? 'selected' : ''}>仅歌手</option>
                <option value="album" ${org.mode === 'album' ? 'selected' : ''}>仅专辑</option>
              </select>
            </label>
            <label class="field"><span>目标根目录</span>
              <select class="select" id="orgBaseDir">${dirOptionsHtml(org.base_dir || '其他', '音乐库根目录')}</select>
            </label>
          </div>
          <div class="form-row" style="margin-top:10px"><div class="form-label">文件名模板</div><input class="input" id="orgFileTemplate" value="${esc(org.file_template || '{artist} - {title}')}"></div>
          <div class="form-row"><div class="form-label">保持不动的歌手 <span class="hint">逗号分隔</span></div><input class="input" id="orgKeepArtists" value="${esc(org.keep_artists || '')}" placeholder="例如：周杰伦"></div>
          <div style="display:flex;gap:8px;margin-top:16px"><button class="btn btn-primary" data-action="save-organization">${ICONS.check}<span>保存自动整理</span></button></div>
        </section>

        <section class="card card-pad">
          <div class="card-head"><div><div class="card-title">自动整理规则与状态</div><div class="card-sub">新歌扫描后执行一次</div></div></div>
          <div class="about-rows">
            <div>示例：新歌 <code>/其他/某首歌.flac</code> → <code>/其他/歌手/专辑/歌手 - 歌名.flac</code></div>
            <div>侧车歌词、封面会跟随移动；已有歌曲不会被重新整理。</div>
          </div>
          <div class="auto-status-list" style="margin-top:16px">
            <div class="auto-status-row"><span class="k">上次整理</span><span class="v ${org.last_run_at ? '' : 'dim'}">${org.last_run_at ? relativeTime(org.last_run_at) : '尚未运行'}</span></div>
            <div class="auto-status-row"><span class="k">上次结果</span><span class="v ${org.last_result ? '' : 'dim'}">${org.last_result ? esc(org.last_result) : '—'}</span></div>
            <div class="auto-status-row"><span class="k">整理数量</span><span class="v">${org.last_count || 0} 首</span></div>
          </div>
          <p class="modal-note" style="margin-top:12px">默认关闭。开启后只作用于扫描发现的新歌；想手动处理某一文件夹或歌手，用「文件管理 → 一键整理」。</p>
        </section>
      </div>

      <div class="auto-grid">
        <section class="card card-pad">
          <div class="card-head"><div><div class="card-title">重复歌曲清理</div><div class="card-sub">同名不同歌手不再误判；只清理确认重复</div></div>
            <span class="status-pill ${dup.auto_clean ? '' : 'off'}"><span class="dot"></span>${dup.auto_clean ? '自动清理已开启' : '默认关闭'}</span></div>
          <div class="switch-list">
            <div class="switch-row"><div class="switch-copy"><b>扫描后自动清理确认重复</b><span>只处理“内容相同 / 同歌手同专辑同名同时长”的高置信组</span></div><label class="toggle"><input type="checkbox" id="dupAutoClean" ${dup.auto_clean ? 'checked' : ''}><span></span></label></div>
          </div>
          <div style="display:flex;gap:8px;margin-top:16px;flex-wrap:wrap">
            <button class="btn btn-primary" data-action="save-duplicates">${ICONS.check}<span>保存自动清理</span></button>
            <button class="btn btn-soft" data-action="auto-clean-duplicates">立即清理确认重复</button>
          </div>
          <p class="modal-note" style="margin-top:12px">清理会把重复文件移到回收站，不直接删除；每组保留体积/位深/码率最好的一份。误判请到音乐库「重复」筛选里点「不是重复」。</p>
        </section>

        <section class="card card-pad">
          <div class="card-head"><div><div class="card-title">重复识别状态</div><div class="card-sub">按歌手 + 歌名 + 时长 + 专辑判断</div></div></div>
          <div class="auto-status-list">
            <div class="auto-status-row"><span class="k">当前重复组</span><span class="v">${o.duplicate_groups || 0} 组</span></div>
            <div class="auto-status-row"><span class="k">涉及歌曲</span><span class="v">${(o.filters && o.filters.duplicates) || 0} 首</span></div>
            <div class="auto-status-row"><span class="k">已忽略误判</span><span class="v">${o.duplicate_ignored || 0} 组</span></div>
            <div class="auto-status-row"><span class="k">上次清理</span><span class="v ${dup.last_run_at ? '' : 'dim'}">${dup.last_run_at ? relativeTime(dup.last_run_at) : '尚未运行'}</span></div>
            <div class="auto-status-row"><span class="k">上次结果</span><span class="v ${dup.last_result ? '' : 'dim'}">${dup.last_result ? esc(dup.last_result) : '—'}</span></div>
          </div>
          <div style="display:flex;gap:8px;margin-top:16px;flex-wrap:wrap">
            <button class="btn btn-ghost btn-sm" data-action="clear-ignored-duplicates">恢复所有忽略</button>
          </div>
        </section>
      </div>
    </div>`;
}

/* -------------------------------------------------------------------------
   设置
   ------------------------------------------------------------------------- */

function providerListHtml(kind) {
  const catalog = (state.settings && state.settings.providers) || [];
  const items = catalog.filter((p) => p[kind]);
  const enabled = state.providersDraft[kind] || [];
  const ordered = [...enabled, ...items.map((p) => p.name).filter((n) => !enabled.includes(n))];
  return ordered.map((name) => {
    const info = items.find((p) => p.name === name) || { label: name };
    const on = enabled.includes(name);
    const pos = enabled.indexOf(name);
    return `<div class="provider-item">
      <label class="checkline" style="flex:1;min-width:0;gap:8px"><input type="checkbox" data-provider-toggle="${kind}" value="${esc(name)}" ${on ? 'checked' : ''}><span class="name">${esc(info.label)}</span></label>
      <span class="kind">${on ? '已启用 · 第 ' + (pos + 1) + ' 位' : '未启用'}</span>
      <span class="arrows">
        <button data-action="provider-move" data-kind="${kind}" data-name="${esc(name)}" data-dir="-1" ${!on || pos <= 0 ? 'disabled' : ''} title="上移">↑</button>
        <button data-action="provider-move" data-kind="${kind}" data-name="${esc(name)}" data-dir="1" ${!on || pos >= enabled.length - 1 ? 'disabled' : ''} title="下移">↓</button>
      </span>
    </div>`;
  }).join('');
}

function settingsCard(icon, title, sub, body, full = false) {
  return `<section class="card settings-card ${full ? 'full' : ''}">
    <div class="settings-head"><span class="settings-icon">${ICONS[icon]}</span><div><div class="settings-title">${esc(title)}</div><div class="settings-sub">${esc(sub)}</div></div></div>
    ${body}
  </section>`;
}

function settingRow(name, desc, control) {
  return `<div class="setting-row"><div class="info"><div class="setting-name">${esc(name)}</div><div class="setting-desc">${esc(desc)}</div></div><div class="setting-control">${control}</div></div>`;
}

function integrationSyncHtml(cfg, key) {
  const data = (cfg && cfg[key]) || {};
  if (!data.enabled) return `<div class="integration-sync muted" data-sync-status="${key}">未启用联动</div>`;
  if (!data.last_sync_at) return `<div class="integration-sync muted" data-sync-status="${key}">尚未同步 · 修复或点「立即刷新」后显示结果</div>`;
  const ok = Boolean(data.last_sync_ok);
  return `<div class="integration-sync ${ok ? 'sync-ok' : 'sync-fail'}" data-sync-status="${key}">
    <span class="sync-dot"></span>
    <div><b>上次同步：${ok ? '成功' : '失败'}</b><span>${esc(relativeTime(data.last_sync_at))} · ${esc(data.last_sync_message || '')}</span></div>
  </div>`;
}

function updateIntegrationSync(key, lastSync) {
  const cfg = state.settings && state.settings.config;
  if (!cfg || !cfg[key] || !lastSync) return;
  cfg[key].last_sync_at = lastSync.at;
  cfg[key].last_sync_ok = lastSync.ok;
  cfg[key].last_sync_message = lastSync.message;
  const old = document.querySelector(`[data-sync-status="${key}"]`);
  if (old) old.outerHTML = integrationSyncHtml(cfg, key);
}

function renderSettings() {
  const s = state.settings;
  if (!s) { $('#view').innerHTML = '<div class="boot"><div class="boot-orb"></div><p>正在读取设置…</p></div>'; return; }
  const cfg = s.config;
  const hasPassword = s.has_password;
  const hasJellyKey = Boolean(s.has_jellyfin_key);
  const stats = (state.overview && state.overview.stats) || {};
  const totalTracks = Number(stats.total || 0);
  const lyricsCoverage = totalTracks ? Math.round(((totalTracks - Number(stats.missing_lyrics || 0)) * 100) / totalTracks) : 0;
  const coverCoverage = totalTracks ? Math.round(((totalTracks - Number(stats.missing_cover || 0)) * 100) / totalTracks) : 0;
  const playbackQualityValue = (cfg.playback && cfg.playback.quality) || 'original';
  const playbackIsOriginal = playbackQualityValue === 'original';
  const playbackLabel = playbackQualityLabel(playbackQualityValue);
  const path = s.music_dir || '';
  const hasPath = Boolean(path);
  $('#view').innerHTML = `
    <div class="settings">
      <div class="settings-grid">
        ${settingsCard('folder', '音乐库', '所有歌曲、歌词与封面都保存在这个目录', `
          <div class="path-box"><span>${esc(path || '尚未选择音乐文件夹')}</span><span class="actions">
            <button class="btn btn-ghost btn-sm" data-action="open-folder">${hasPath ? '更换' : '选择目录'}</button>
            <button class="btn btn-soft btn-sm" data-action="rescan" ${hasPath ? '' : 'disabled'}>增量扫描</button>
            <button class="btn btn-ghost btn-sm" data-action="full-scan" ${hasPath ? '' : 'disabled'}>全量重扫</button>
          </span></div>
          <p class="modal-note" style="margin-top:12px">更换目录会清空当前索引并重新扫描；音频文件本身不会被移动或删除。</p>
          <div class="settings-mini-stats">
            <div><b>${stats.total ?? '—'}</b><span>曲目</span></div>
            <div><b>${stats.albums ?? '—'}</b><span>专辑</span></div>
            <div><b>${stats.health_score ?? '—'}</b><span>健康度</span></div>
          </div>
          <p class="settings-note">启动扫描 + 目录变化监听 + 每 30 分钟增量扫描；曲库变化会自动进入索引。</p>
          <div class="format-grid">
            <span>FLAC</span><span>MP3</span><span>M4A</span><span>OGG</span>
            <span>WAV</span><span>AIFF</span><span>APE</span><span>WV</span>
          </div>`)}

        ${settingsCard('list', '外置歌词清理', '找出已有内嵌歌词后多余的 .lrc，以及没有对应音频的失联歌词', `
          <div id="lrcSummary" class="lrc-summary"><div class="lrc-loading">正在检查 .lrc 文件…</div></div>
          <div class="lrc-options">
            <label class="checkline"><input type="checkbox" id="lrcStatusRedundant" checked> 已有内嵌歌词（可安全清理）</label>
            <label class="checkline"><input type="checkbox" id="lrcStatusOrphan" checked> 没有对应音频（可安全清理）</label>
            <label class="checkline warn"><input type="checkbox" id="lrcStatusSidecarOnly"> 仅外置歌词（删除后该曲将没有歌词）</label>
          </div>
          <div style="display:flex;gap:8px;margin-top:14px;flex-wrap:wrap">
            <button class="btn btn-ghost btn-sm" data-action="lrc-scan">重新扫描</button>
            <button class="btn btn-soft btn-sm" data-action="lrc-embed">把外置歌词写入内嵌</button>
            <button class="btn btn-ghost btn-sm" data-action="lrc-clean">清理选中</button>
          </div>
          <p class="modal-note" style="margin-top:10px">清理会移动到数据目录的 <code class="mono">trash/lrc-*</code>，不是直接删除，可随时找回。</p>
          <p class="settings-note">推荐顺序：重新扫描 → 把外置歌词写入内嵌 → 再清理冗余/失联文件。</p>`)}

        ${settingsCard('lyrics', '歌词', '内嵌优先，可选同时写 .lrc 文件', `
          ${settingRow('繁转简', '把繁体歌词转换为简体中文（需要 zhconv）', `<label class="toggle"><input type="checkbox" data-setting="repair.simplified" ${cfg.repair.simplified ? 'checked' : ''}><span></span></label>`)}
          ${settingRow('允许纯文本歌词', '没有时间轴的歌词也写入（播放器将静态显示）', `<label class="toggle"><input type="checkbox" data-setting="repair.allow_plain_lyrics" ${cfg.repair.allow_plain_lyrics ? 'checked' : ''}><span></span></label>`)}
          ${settingRow('最少字符数', '低于该长度的歌词视为无效', `<input class="input" type="number" min="5" max="500" data-setting="repair.min_lyrics_chars" value="${cfg.repair.min_lyrics_chars}">`)}
          ${settingRow('同时写入 .lrc', '在歌曲旁边生成同名外置歌词；Jellyfin 显示歌词需要（默认开启）', `<label class="toggle"><input type="checkbox" data-setting="repair.write_lrc" ${cfg.repair.write_lrc ? 'checked' : ''}><span></span></label>`)}
          ${settingRow('自动修正占位标题', 'track 04 / Audio 3 等占位标题按候选自动修正', `<label class="toggle"><input type="checkbox" data-setting="repair.fix_placeholder_tags" ${cfg.repair.fix_placeholder_tags ? 'checked' : ''}><span></span></label>`)}
          ${settingRow('覆盖已有歌词', '修复时替换已有歌词（手动修复总是可以覆盖）', `<label class="toggle"><input type="checkbox" data-setting="repair.overwrite_lyrics" ${cfg.repair.overwrite_lyrics ? 'checked' : ''}><span></span></label>`)}
          <p class="settings-note">当前 ${stats.synced_lyrics ?? '—'} 首逐行歌词 · ${lyricsCoverage}% 歌词覆盖；内嵌优先，播放器会直接读取音频标签。</p>`)}

        ${settingsCard('image', '封面', '统一转为 JPEG，兼顾兼容性与体积', `
          ${settingRow('最小尺寸', '低于该尺寸的封面不使用（像素）', `<input class="input" type="number" min="100" max="2000" data-setting="repair.min_cover_size" value="${cfg.repair.min_cover_size}">`)}
          ${settingRow('最大尺寸', '超过后等比缩小（像素）', `<input class="input" type="number" min="200" max="4000" data-setting="repair.max_cover_size" value="${cfg.repair.max_cover_size}">`)}
          ${settingRow('JPEG 质量', '范围 60–100', `<input class="input" type="number" min="60" max="100" data-setting="repair.jpeg_quality" value="${cfg.repair.jpeg_quality}">`)}
          ${settingRow('覆盖已有封面', '修复时替换已有封面', `<label class="toggle"><input type="checkbox" data-setting="repair.overwrite_cover" ${cfg.repair.overwrite_cover ? 'checked' : ''}><span></span></label>`)}
          ${settingRow('同时写入封面文件', '生成与歌曲同名的 .jpg 文件', `<label class="toggle"><input type="checkbox" data-setting="repair.write_cover_file" ${cfg.repair.write_cover_file ? 'checked' : ''}><span></span></label>`)}
          <div class="settings-mini-stats">
            <div><b>${stats.embedded_covers ?? '—'}</b><span>已内嵌</span></div>
            <div><b>${stats.missing_cover ?? '—'}</b><span>缺失</span></div>
            <div><b>${coverCoverage}%</b><span>覆盖率</span></div>
          </div>`)}

        ${settingsCard('zap', '在线数据源', '按顺序尝试，失败会自动降级', `
          <div class="provider-columns">
            <div class="form-row"><div class="form-label">歌词数据源 <span class="hint">拖动顺序即优先级</span></div><div class="provider-order" id="providerLyrics">${providerListHtml('lyrics')}</div></div>
            <div class="form-row"><div class="form-label">封面数据源</div><div class="provider-order" id="providerCover">${providerListHtml('cover')}</div></div>
          </div>
          <div class="form-inline">
            <label class="field"><span>请求超时（秒）</span><input class="input" type="number" min="3" max="30" data-setting="providers.timeout" value="${cfg.providers.timeout}"></label>
            <label class="field"><span>自动应用最低分</span><input class="input" type="number" min="0" max="100" data-setting="providers.min_score" value="${cfg.providers.min_score}"></label>
          </div>
          <div style="display:flex;gap:8px;margin-top:14px"><button class="btn btn-ghost btn-sm" data-action="test-providers">测试连通性</button></div>
          <div id="providerTestResult" class="modal-note" style="margin-top:10px"></div>`)}

        ${settingsCard('shield', '备份与撤销', '每次写入标签前都会保存快照', `
          ${settingRow('自动备份', '关闭后无法从插件内撤销修复', `<label class="toggle"><input type="checkbox" data-setting="repair.backup" ${cfg.repair.backup ? 'checked' : ''}><span></span></label>`)}
          ${settingRow('最多保留备份', '超出后自动清理最旧的快照', `<input class="input" type="number" min="20" max="5000" data-setting="repair.max_backups" value="${cfg.repair.max_backups}">`)}
          <p class="modal-note" style="margin-top:12px">备份位于 <code class="mono">${esc(s.paths.backups)}</code>，按文件哈希分目录保存。</p>
          <div class="settings-bullets">
            <span>每首歌曲独立目录，包含标签、歌词和封面快照</span>
            <span>按时间保留最近 ${cfg.repair.max_backups} 份，自动清理旧快照</span>
            <span>关闭自动备份后，已写入的歌曲将无法从插件内撤销</span>
          </div>`)}

        ${settingsCard('settings', 'Web 访问', '局域网内可直接访问；建议设置访问密码', `
          ${settingRow('访问密码', hasPassword ? '已开启密码保护' : '当前未设置密码，同网段设备可直接打开', `<span class="badge ${hasPassword ? 'badge-ok' : 'badge-warn'}">${hasPassword ? '已启用' : '未启用'}</span>`)}
          <div class="setting-input-row" style="margin-top:10px"><input class="input" id="newPassword" type="password" placeholder="设置新密码（至少 4 位）" autocomplete="new-password"><button class="btn btn-soft btn-sm" data-action="save-password">保存</button></div>
          ${hasPassword ? '<label class="checkline" style="margin-top:10px"><input type="checkbox" id="clearPassword">关闭密码保护</label>' : ''}
          ${settingRow('服务端口', '修改后需要重启 Web 服务', `<input class="input input-wide" type="number" min="1" max="65535" data-setting="web.port" value="${cfg.web.port}">`)}
          <div class="setting-row"><div class="info"><div class="setting-name">访问地址</div><div class="setting-desc">手机 / 平板与树莓派在同一局域网即可</div></div><div class="setting-control"><code class="mono" style="font-size:11px">http://${esc(location.hostname)}:${cfg.web.port}</code></div></div>
          <p class="settings-note">${hasPassword ? '密码保护已开启，登录状态保持 7 天。' : '当前无密码，仅建议在可信局域网内使用；如需外网访问请配置 HTTPS 反向代理。'}</p>`)}

        ${settingsCard('play', 'Navidrome 联动', '修复完成后让音乐服务器重新扫描', `
          ${settingRow('启用联动', '通过 Subsonic API 通知 Navidrome', `<label class="toggle"><input type="checkbox" data-setting="navidrome.enabled" ${cfg.navidrome.enabled ? 'checked' : ''}><span></span></label>`)}
          <label class="field" style="margin-bottom:10px"><span>服务器地址</span><input class="input" data-setting="navidrome.url" value="${esc(cfg.navidrome.url || '')}" placeholder="http://192.168.1.100:4533"></label>
          <div class="form-inline">
            <label class="field"><span>用户名</span><input class="input" data-setting="navidrome.username" value="${esc(cfg.navidrome.username || '')}"></label>
            <label class="field"><span>密码</span><input class="input" type="password" data-setting="navidrome.password" value="" placeholder="留空则不修改"></label>
          </div>
          ${settingRow('修复后自动扫描', '每轮批量修复结束后自动触发一次扫描', `<label class="toggle"><input type="checkbox" data-setting="navidrome.rescan_after_fix" ${cfg.navidrome.rescan_after_fix ? 'checked' : ''}><span></span></label>`)}
          ${settingRow('刷新缺失封面', '通过 docker 调用 Navidrome artwork reprocess；需要服务用户有 docker 权限', `<label class="toggle"><input type="checkbox" data-setting="navidrome.refresh_artwork" ${cfg.navidrome.refresh_artwork ? 'checked' : ''}><span></span></label>`)}
          ${settingRow('清理缺失记录', '自动把移动/改标签后留下的幽灵记录重映射到现有文件', `<label class="toggle"><input type="checkbox" data-setting="navidrome.remap_missing" ${cfg.navidrome.remap_missing ? 'checked' : ''}><span></span></label>`)}
          <div style="display:flex;gap:8px;margin-top:14px"><button class="btn btn-ghost btn-sm" data-action="test-navidrome">测试连接</button><button class="btn btn-ghost btn-sm" data-action="navidrome-rescan">立即扫描</button></div>
          <div id="navidromeResult" class="modal-note" style="margin-top:10px"></div>
          ${integrationSyncHtml(cfg, 'navidrome')}
          <p class="settings-note">${cfg.navidrome.enabled ? '联动已启用；修复完成后会自动触发一次扫描。' : '当前未启用；保存并测试连接后可开启自动扫描。'}</p>`)}

        ${settingsCard('play', 'Jellyfin 联动', '修复完成后让 Jellyfin 刷新媒体库', `
          <div class="settings-head" style="margin-bottom:12px"><div><div class="settings-title" style="font-size:13px">API Key 状态</div><div class="settings-sub">在 Jellyfin 控制台 → 高级 → API 密钥中创建</div></div><span class="badge ${hasJellyKey ? 'badge-ok' : 'badge-warn'}" style="margin-left:auto">${hasJellyKey ? '已配置' : '未配置'}</span></div>
          ${settingRow('启用联动', '通过 Jellyfin Web API 刷新媒体库', `<label class="toggle"><input type="checkbox" data-setting="jellyfin.enabled" ${cfg.jellyfin.enabled ? 'checked' : ''}><span></span></label>`)}
          <label class="field" style="margin-bottom:10px"><span>服务器地址</span><input class="input" data-setting="jellyfin.url" value="${esc(cfg.jellyfin.url || '')}" placeholder="http://192.168.1.100:8096"></label>
          <label class="field" style="margin-bottom:10px"><span>API Key</span><input class="input" type="password" data-setting="jellyfin.api_key" value="" placeholder="${hasJellyKey ? '已保存，留空则不修改' : '粘贴 API Key'}"></label>
          ${settingRow('修复后自动刷新', '每轮批量修复结束后自动触发一次刷新', `<label class="toggle"><input type="checkbox" data-setting="jellyfin.refresh_after_fix" ${cfg.jellyfin.refresh_after_fix ? 'checked' : ''}><span></span></label>`)}
          <div style="display:flex;gap:8px;margin-top:14px"><button class="btn btn-ghost btn-sm" data-action="test-jellyfin">测试连接</button><button class="btn btn-ghost btn-sm" data-action="jellyfin-refresh">立即刷新</button></div>
          <div id="jellyfinResult" class="modal-note" style="margin-top:10px"></div>
          ${integrationSyncHtml(cfg, 'jellyfin')}`)}

        ${settingsCard('check', '播放音质', playbackIsOriginal ? '原始文件直通，不做二次编码' : '服务端实时转码，默认仍然直通', `
          ${settingRow('播放模式', '默认原始文件直通；修改后下一首生效', `<select class="select" data-setting="playback.quality" style="width:150px">
            <option value="original" ${playbackIsOriginal ? 'selected' : ''}>原始文件直通</option>
            <option value="mp3_320" ${playbackQualityValue === 'mp3_320' ? 'selected' : ''}>MP3 320 kbps</option>
            <option value="mp3_192" ${playbackQualityValue === 'mp3_192' ? 'selected' : ''}>MP3 192 kbps</option>
            <option value="mp3_128" ${playbackQualityValue === 'mp3_128' ? 'selected' : ''}>MP3 128 kbps</option>
            <option value="aac_256" ${playbackQualityValue === 'aac_256' ? 'selected' : ''}>AAC 256 kbps</option>
            <option value="opus_192" ${playbackQualityValue === 'opus_192' ? 'selected' : ''}>Opus 192 kbps</option>
          </select>`)}
          <div class="about-rows">
            <div>当前模式：<span style="color:${playbackIsOriginal ? 'var(--green)' : 'var(--warn)'}">${esc(playbackLabel)}</span></div>
            <div>传输方式：${playbackIsOriginal ? 'HTTP Range 直读原始文件' : 'HTTP 实时转码流（不支持拖动）'}</div>
            <div>转码 / 降采样：<span style="color:${playbackIsOriginal ? 'var(--green)' : 'var(--warn)'}">${playbackIsOriginal ? '关闭' : '开启'}</span></div>
            <div>支持格式：FLAC / MP3 / M4A / OGG / WAV / AIFF</div>
          </div>
          <div style="display:flex;gap:6px;margin-top:12px;flex-wrap:wrap">
            <span class="badge ${playbackIsOriginal ? 'badge-ok' : 'badge-warn'}">${playbackIsOriginal ? '无损优先' : '服务端转码'}</span>
            <span class="badge badge-accent">${esc(playbackLabel)}</span>
          </div>
          <div class="format-grid">
            <span>FLAC</span><span>MP3</span><span>M4A</span><span>OGG</span>
            <span>WAV</span><span>AIFF</span><span>APE</span><span>WV</span>
          </div>
          <p class="settings-note">${playbackIsOriginal ? '浏览器最终输出受系统音频设备限制；插件不转码、不降采样。' : '转码仅影响浏览器播放，不修改文件；转码流不支持拖动进度。'}</p>`)}

        ${settingsCard('check', '关于', '本地音乐整理小插件', `
          <div class="about-rows">
            <div>版本：<code>v${esc(s.version || '')}</code></div>
            <div>数据目录：<code>${esc(s.paths.home)}</code></div>
            <div>配置文件：<code>${esc(s.paths.config)}</code></div>
            <div>数据库：<code>${esc(s.paths.database)}</code></div>
            <div>命令行：<code>morg --help</code> · Web 与 CLI 共用同一份曲库</div>
          </div>`, true)}
      </div>

      <div class="settings-save-bar">
        <span class="text">修改后点击保存，部分设置立即生效</span>
        <span class="spacer"></span>
        <button class="btn btn-ghost" data-action="reload-settings">还原</button>
        <button class="btn btn-primary" data-action="save-settings">${ICONS.check}<span>保存设置</span></button>
      </div>
    </div>`;
  if (state.overview && state.overview.initialized) loadLrcStats();
}

async function loadSettings() {
  if (state.settings) return;
  const data = await api('/api/settings');
  state.settings = data;
  state.settings.version = state.overview ? state.overview.version : '';
  state.providersDraft = {
    lyrics: [...(data.config.providers.lyrics || [])],
    cover: [...(data.config.providers.cover || [])],
  };
}

function lrcSummaryHtml() {
  if (state.lrcLoading) {
    return '<div class="lrc-loading"><span class="boot-orb" style="width:24px;height:24px;border-width:2px"></span> 正在统计 .lrc 文件…</div>';
  }
  const data = state.lrcStats;
  if (!data) return '<div class="lrc-loading">点击「重新扫描」检查外置歌词</div>';
  const c = data.counts || {};
  const inst = (state.overview && state.overview.stats && state.overview.stats.instrumental) || 0;
  return `
    <div class="lrc-stat"><span class="num">${c.total || 0}</span><span class="lbl">全部 .lrc</span><span class="hint">${fmtSize(c.size)}</span></div>
    <div class="lrc-stat ok"><span class="num">${c.redundant || 0}</span><span class="lbl">已有内嵌</span><span class="hint">${inst ? '另有 ' + inst + ' 首纯音乐' : '可安全清理'}</span></div>
    <div class="lrc-stat warn"><span class="num">${c.orphan || 0}</span><span class="lbl">失联歌词</span><span class="hint">没有对应音频</span></div>
    <div class="lrc-stat ${c.sidecar_only ? 'danger' : ''}"><span class="num">${c.sidecar_only || 0}</span><span class="lbl">仅外置</span><span class="hint">删除会丢歌词</span></div>`;
}

function updateLrcSummary() {
  const box = $('#lrcSummary');
  if (box) box.innerHTML = lrcSummaryHtml();
}

async function loadLrcStats() {
  if (state.lrcLoading) return;
  state.lrcLoading = true;
  updateLrcSummary();
  try {
    const data = await api('/api/lrc/scan');
    state.lrcStats = data;
  } catch (err) {
    state.lrcStats = null;
    const box = $('#lrcSummary');
    if (box) box.innerHTML = `<div class="lrc-loading" style="color:var(--red)">${esc(err.message)}</div>`;
  } finally {
    state.lrcLoading = false;
    updateLrcSummary();
  }
}

async function cleanLrc() {
  const statuses = [];
  if ($('#lrcStatusRedundant') && $('#lrcStatusRedundant').checked) statuses.push('redundant');
  if ($('#lrcStatusOrphan') && $('#lrcStatusOrphan').checked) statuses.push('orphan');
  if ($('#lrcStatusSidecarOnly') && $('#lrcStatusSidecarOnly').checked) statuses.push('sidecar_only');
  if (!statuses.length) { toast('请先选择要清理的类型', 'err'); return; }
  const c = (state.lrcStats && state.lrcStats.counts) || {};
  let total = 0;
  if (statuses.includes('redundant')) total += c.redundant || 0;
  if (statuses.includes('orphan')) total += c.orphan || 0;
  if (statuses.includes('sidecar_only')) total += c.sidecar_only || 0;
  if (!total) { toast('当前选择没有可清理的文件', 'ok', 2200); return; }
  const warning = statuses.includes('sidecar_only')
    ? `<br><br><strong style="color:var(--red)">你勾选了「仅外置歌词」：${c.sidecar_only || 0} 首歌删除后将失去歌词。</strong>`
    : '';
  const ok = await confirmDialog(
    '清理外置歌词',
    `将把 <strong>${total}</strong> 个 .lrc 移动到数据目录的回收站（lrc-*），不会直接删除。${warning}`,
    '开始清理'
  );
  if (!ok) return;
  try {
    const res = await api('/api/lrc/clean', { method: 'POST', body: { statuses } });
    const count = (res.moved || []).length;
    toast(`已移动 ${count} 个 .lrc 到回收站`);
    if ((res.errors || []).length) toast(`有 ${res.errors.length} 个文件未能移动`, 'err');
    state.lrcStats = null;
    await loadLrcStats();
    loadOverview().catch(() => {});
  } catch (err) {
    toast(err.message, 'err');
  }
}

async function testJellyfin() {
  const box = $('#jellyfinResult');
  if (box) box.textContent = '正在连接…';
  try {
    const body = {
      enabled: true,
      url: ($('[data-setting="jellyfin.url"]') || {}).value || '',
      api_key: ($('[data-setting="jellyfin.api_key"]') || {}).value || '',
    };
    const res = await api('/api/jellyfin/test', { method: 'POST', body });
    const info = res.info || {};
    if (box) {
      box.innerHTML = res.connected
        ? `<span style="color:var(--green)">连接成功：${esc(info.name || 'Jellyfin')} ${esc(info.version || '')}</span>`
        : `<span style="color:var(--red)">${esc(info.error || '连接失败')}</span>`;
    }
  } catch (err) {
    if (box) box.innerHTML = `<span style="color:var(--red)">${esc(err.message)}</span>`;
  }
}

async function refreshJellyfin() {
  const box = $('#jellyfinResult');
  if (box) box.textContent = '正在通知 Jellyfin…';
  try {
    const body = {
      enabled: true,
      url: ($('[data-setting="jellyfin.url"]') || {}).value || '',
      api_key: ($('[data-setting="jellyfin.api_key"]') || {}).value || '',
    };
    const res = await api('/api/jellyfin/refresh', { method: 'POST', body });
    if (box) {
      box.innerHTML = `<span style="color:${res.refreshed ? 'var(--green)' : 'var(--red)'}">${esc(res.message)}</span>`;
    }
    updateIntegrationSync('jellyfin', res.last_sync);
  } catch (err) {
    if (box) box.innerHTML = `<span style="color:var(--red)">${esc(err.message)}</span>`;
  }
}

function collectSettings() {
  const cfg = clone(state.settings.config);
  $$('[data-setting]').forEach((el) => {
    let value;
    if (el.type === 'checkbox') value = el.checked;
    else if (el.type === 'number') value = Number(el.value || 0);
    else value = el.value;
    setDeep(cfg, el.dataset.setting, value);
  });
  cfg.providers.lyrics = [...state.providersDraft.lyrics];
  cfg.providers.cover = [...state.providersDraft.cover];
  return cfg;
}

async function saveSettings() {
  try {
    const cfg = collectSettings();
    const payload = { config: cfg };
    const password = $('#newPassword') ? $('#newPassword').value.trim() : '';
    if (password) payload.new_password = password;
    const clear = $('#clearPassword') ? $('#clearPassword').checked : false;
    if (clear) payload.clear_password = true;
    const data = await api('/api/settings', { method: 'PUT', body: payload });
    state.settings.config = data.config;
    toast((data.errors && data.errors.length) ? '设置已保存（有提示：' + data.errors[0] + '）' : '设置已保存');
    await loadOverview();
    state.settings = null;
    await loadSettings();
    renderSettings();
  } catch (err) {
    toast(err.message, 'err');
  }
}

/* -------------------------------------------------------------------------
   播放器
   ------------------------------------------------------------------------- */

function updatePlayerShell() {
  const t = state.current;
  if (!t) return;
  $('#pbCover').src = t.cover_url;
  $('#pbTitle').textContent = t.title;
  $('#pbArtist').textContent = t.artist_display + (t.album ? ' · ' + t.album : '');
  const fav = $('#pbFav');
  fav.classList.toggle('active', !!t.favorite);
  $('#pbQuality').textContent = t.quality || (t.ext || '').toUpperCase();
  $('#pbQuality').title = playbackQuality() === 'original'
    ? '原始文件直通，不做转码'
    : `${playbackQualityLabel()} 实时转码，不支持拖动`;
  $$('.track-row').forEach((row) => {
    const isCurrent = Number(row.dataset.trackId) === t.id;
    row.classList.toggle('playing', isCurrent);
  });
}

function renderNowPlaying() {
  const t = state.current;
  if (!t) return;
  $('#npTitle').textContent = t.title;
  $('#npArtist').textContent = t.artist_display + (t.album ? ' · ' + t.album : '');
  $('#npCover').src = t.cover_url;
  $('#npGlow').style.backgroundImage = `url("${t.cover_url}")`;
  $('#npBackdrop').style.backgroundImage = `url("${t.cover_url}")`;
  $('#npMeta').innerHTML = `
    <span class="badge badge-accent">${esc(t.quality || t.ext)}</span>
    <span class="badge ${playbackQuality() === 'original' ? 'badge-ok' : 'badge-warn'}">${esc(playbackQualityLabel())}</span>
    ${t.lyrics_synced ? '<span class="badge badge-ok">逐行歌词</span>' : t.has_lyrics ? '<span class="badge">纯文本歌词</span>' : '<span class="badge badge-bad">缺歌词</span>'}
    ${t.has_cover ? '' : '<span class="badge badge-bad">缺封面</span>'}`;
  $('#npDur').textContent = t.duration_text;
  $('#pbDur').textContent = t.duration_text;
  updatePlayIcons();
  updatePlayModeUI();
  updateOffsetLabel();
}

function updatePlayIcons() {
  const playing = !audio.paused && !!state.current;
  document.body.classList.toggle('is-playing', playing);
  const html = playing ? ICONS.pause : ICONS.play;
  const pb = $('#pbPlayIcon');
  const np = $('#npPlayIcon');
  if (pb) pb.outerHTML = html.replace('<svg', '<svg id="pbPlayIcon"');
  if (np) np.outerHTML = html.replace('<svg', '<svg id="npPlayIcon"');
}

function buildEqBars() {
  const make = (root, count) => {
    if (!root) return;
    root.innerHTML = '';
    for (let i = 0; i < count; i += 1) {
      const bar = document.createElement('span');
      bar.style.animationDelay = `${(i % 7) * -0.12}s`;
      bar.style.animationDuration = `${0.72 + ((i * 37) % 50) / 100}s`;
      root.appendChild(bar);
    }
  };
  make($('#pbEq'), 22);
  make($('#npEq'), 30);
}

function updatePlayModeUI() {
  const shuffleBtn = $('#pbShuffle');
  if (shuffleBtn) shuffleBtn.classList.toggle('active', state.shuffle);
  const repeatBtn = $('#pbRepeat');
  if (repeatBtn) {
    repeatBtn.classList.toggle('active', state.repeat !== 'off');
    repeatBtn.title = state.repeat === 'one' ? '单曲循环' : state.repeat === 'off' ? '不循环' : '列表循环';
    repeatBtn.style.opacity = state.repeat === 'off' ? '0.45' : '1';
  }
}

const PLAYBACK_QUALITY_LABELS = {
  original: '原始文件直通',
  mp3_320: 'MP3 320 kbps',
  mp3_192: 'MP3 192 kbps',
  mp3_128: 'MP3 128 kbps',
  aac_256: 'AAC 256 kbps',
  opus_192: 'Opus 192 kbps',
};

function playbackQuality() {
  return (state.overview && state.overview.playback && state.overview.playback.quality) || 'original';
}

function playbackQualityLabel(value = playbackQuality()) {
  return PLAYBACK_QUALITY_LABELS[value] || PLAYBACK_QUALITY_LABELS.original;
}

function playbackStreamUrl(track) {
  const base = track.stream_url || `/media/stream/${track.id}`;
  try {
    const url = new URL(base, location.origin);
    const quality = playbackQuality();
    if (quality !== 'original') url.searchParams.set('quality', quality);
    else url.searchParams.delete('quality');
    return `${url.pathname}${url.search}`;
  } catch (err) {
    return base;
  }
}

async function playTracks(queue, id) {
  const list = (queue || []).filter(Boolean);
  const index = list.findIndex((t) => Number(t.id) === Number(id));
  if (index < 0) return;
  state.queue = list;
  state.queueIndex = index;
  await loadTrack(list[index]);
  renderQueue();
}

async function loadTrack(track) {
  state.current = track;
  state.lyrics = { lines: [], text: '', offset: track.lyrics_offset_ms || 0, active: -1, synced: false, source: '' };
  state.playRetry = 0;
  state.retrying = false;
  state.lastPlayTime = 0;
  audio.preload = 'auto';
  updatePlayerShell();
  renderNowPlaying();
  if (!track.stream_url) {
    track.stream_url = `/media/stream/${track.id}`;
  }
  audio.src = playbackStreamUrl(track);
  audio.play().catch(() => toast('浏览器阻止了自动播放，请再点一次播放', 'err'));
  document.title = `${track.title} · 本地音乐整理小插件`;
  startPlayTimer();
  await loadLyrics(track.id);
  api(`/api/tracks/${track.id}/play`, { method: 'POST' }).catch(() => {});
  updateMediaSession(track);
  // 播放时自动修复
  if ((!track.has_lyrics || !track.has_cover) && state.overview && state.overview.automation
      && state.overview.automation.fix_on_play && !state.autoFixed.has(track.id)) {
    state.autoFixed.add(track.id);
    try {
      const res = await api(`/api/tracks/${track.id}/auto-fix`, { method: 'POST' });
      if (res.queued) {
        toast('正在后台补齐歌词 / 封面…');
        watchJob(res.job_id, { silent: true, onDone: () => refreshCurrentMedia() });
      }
    } catch (err) { /* 忽略播放时自动修复的失败 */ }
  }
}

async function loadLyrics(trackId) {
  try {
    const data = await api(`/api/tracks/${trackId}/lyrics`);
    if (!state.current || state.current.id !== trackId) return;
    state.lyrics = {
      lines: data.lines || [],
      text: data.text || '',
      offset: Number(data.offset_ms) || 0,
      active: -1,
      synced: data.synced,
      source: data.source,
    };
    renderLyrics();
    updateOffsetLabel();
  } catch (err) {
    toast('读取歌词失败：' + err.message, 'err');
  }
}

function renderLyrics() {
  const box = $('#lyricsScroll');
  if (!box) return;
  const l = state.lyrics;
  if (!l.lines.length) {
    if (l.text.trim()) {
      box.innerHTML = `<div class="lyric-line active" style="white-space:pre-wrap">${esc(l.text)}</div>`;
    } else {
      box.innerHTML = `<div class="lyrics-empty">
        <span class="lyrics-empty-icon">♪</span>
        <p>${state.current ? '这首歌还没有歌词' : '选一首歌开始播放'}</p>
        ${state.current ? '<button class="btn btn-soft btn-sm" data-action="candidates-current">在线搜索歌词</button>' : ''}
      </div>`;
    }
    return;
  }
  box.innerHTML = l.lines.map((line, index) => `
    <div class="lyric-line" data-index="${index}" data-time="${line.time}" data-action="seek-lyric">
      ${esc(line.text)}${line.translation ? `<span class="lyric-trans">${esc(line.translation)}</span>` : ''}
    </div>`).join('');
  l.active = -1;
  box.scrollTop = 0;
  updateActiveLyric();
}

function updateActiveLyric() {
  const l = state.lyrics;
  const box = $('#lyricsScroll');
  if (!box || !l.lines.length) return;
  const t = (audio.currentTime || 0) - (l.offset || 0) / 1000;
  let index = -1;
  for (let i = 0; i < l.lines.length; i += 1) {
    if (l.lines[i].time <= t + 0.05) index = i; else break;
  }
  if (index === l.active) return;
  const prev = box.querySelector('.lyric-line.active');
  if (prev) prev.classList.remove('active');
  l.active = index;
  if (index >= 0) {
    const el = box.querySelector(`.lyric-line[data-index="${index}"]`);
    if (el) {
      el.classList.add('active');
      const boxRect = box.getBoundingClientRect();
      const elRect = el.getBoundingClientRect();
      const elTop = elRect.top - boxRect.top + box.scrollTop;
      const top = elTop - box.clientHeight / 2 + el.clientHeight / 2;
      box.scrollTo({ top: Math.max(0, top), behavior: 'smooth' });
    }
  }
}

function updateOffsetLabel() {
  const el = $('#offsetVal');
  if (el) el.textContent = `${(state.lyrics.offset / 1000).toFixed(1)}s`;
}

function updateProgress() {
  const dur = audio.duration || (state.current ? state.current.duration : 0) || 0;
  const cur = audio.currentTime || 0;
  const percent = dur > 0 ? Math.min(100, (cur / dur) * 100) : 0;
  ['#pbFill', '#npFill'].forEach((sel) => { const el = $(sel); if (el) el.style.width = `${percent}%`; });
  ['#pbCur', '#npCur'].forEach((sel) => { const el = $(sel); if (el) el.textContent = fmtTime(cur); });
  ['#pbDur', '#npDur'].forEach((sel) => { const el = $(sel); if (el) el.textContent = fmtTime(dur); });
  updateActiveLyric();
}

function startPlayTimer() {
  clearInterval(state.playTimer);
  state.playTimer = setInterval(() => { if (!audio.paused) updateProgress(); }, 250);
  updateProgress();
}

function seekFromEvent(event, element) {
  const rect = element.getBoundingClientRect();
  const ratio = Math.max(0, Math.min(1, (event.clientX - rect.left) / rect.width));
  const dur = audio.duration || (state.current ? state.current.duration : 0) || 0;
  if (dur > 0) {
    audio.currentTime = ratio * dur;
    updateProgress();
  }
}

function nextTrack(auto = false) {
  if (!state.queue.length) return;
  if (auto && state.repeat === 'one') {
    audio.currentTime = 0;
    audio.play().catch(() => {});
    return;
  }
  let index;
  if (state.shuffle && state.queue.length > 1) {
    do { index = Math.floor(Math.random() * state.queue.length); } while (index === state.queueIndex);
  } else {
    index = state.queueIndex + 1;
  }
  if (index >= state.queue.length) {
    if (state.repeat === 'list') index = 0;
    else { audio.pause(); return; }
  }
  state.queueIndex = index;
  loadTrack(state.queue[index]);
  renderQueue();
}

function prevTrack() {
  if (audio.currentTime > 3) { audio.currentTime = 0; return; }
  if (!state.queue.length) return;
  let index = state.queueIndex - 1;
  if (index < 0) index = state.repeat === 'list' ? state.queue.length - 1 : 0;
  state.queueIndex = index;
  loadTrack(state.queue[index]);
  renderQueue();
}

function togglePlay() {
  if (!state.current) {
    if (state.library.items.length) playTracks(state.library.items, state.library.items[0].id);
    return;
  }
  if (audio.paused) audio.play().catch(() => toast('无法播放这个文件', 'err'));
  else audio.pause();
}

function cycleRepeat() {
  state.repeat = state.repeat === 'list' ? 'one' : state.repeat === 'one' ? 'off' : 'list';
  localStorage.setItem('morg.repeat', state.repeat);
  updatePlayModeUI();
  toast(state.repeat === 'one' ? '单曲循环' : state.repeat === 'off' ? '不循环' : '列表循环', 'ok', 1500);
}

function toggleShuffle() {
  state.shuffle = !state.shuffle;
  localStorage.setItem('morg.shuffle', state.shuffle ? '1' : '0');
  updatePlayModeUI();
  toast(state.shuffle ? '随机播放已开启' : '随机播放已关闭', 'ok', 1500);
}

function updateMediaSession(track) {
  if (!('mediaSession' in navigator) || !track) return;
  try {
    navigator.mediaSession.metadata = new MediaMetadata({
      title: track.title,
      artist: track.artist_display,
      album: track.album_display,
      artwork: [{ src: new URL(track.cover_url, location.href).href, sizes: '512x512', type: 'image/jpeg' }],
    });
  } catch (err) { /* 旧浏览器 */ }
}

async function refreshCurrentMedia() {
  if (!state.current) return;
  try {
    const detail = await api(`/api/tracks/${state.current.id}`);
    const updated = { ...state.current, ...detail, cover_url: `${detail.cover_url}&t=${Date.now()}` };
    state.current = updated;
    updatePlayerShell();
    renderNowPlaying();
    await loadLyrics(updated.id);
    if (state.route === 'library') loadLibrary();
    if ($('#trackDrawer').classList.contains('open') && state.trackDetail && state.trackDetail.id === updated.id) {
      openTrack(updated.id);
    }
  } catch (err) { /* 忽略 */ }
}

/* -------------------------------------------------------------------------
   队列 / 正在播放
   ------------------------------------------------------------------------- */

function renderQueue() {
  const list = state.queue;
  $('#queueSub').textContent = `${list.length} 首${state.shuffle ? ' · 随机' : ''}`;
  if (!list.length) {
    $('#queueList').innerHTML = '<div class="empty" style="padding:48px 16px"><span class="empty-icon">♪</span><p>队列是空的，去音乐库点一首歌吧</p></div>';
    return;
  }
  $('#queueList').innerHTML = list.map((t, index) => `
    <div class="queue-item ${state.current && state.current.id === t.id ? 'active' : ''}" data-action="play-queue" data-index="${index}">
      <span class="idx">${index + 1}</span>
      <img src="${esc(t.cover_url)}" alt="" loading="lazy" onerror="this.src='/static/img/icon.svg'">
      <span class="qm"><span class="qt">${esc(t.title)}</span><span class="qa">${esc(t.artist_display)}</span></span>
      <span class="qd">${esc(t.duration_text)}</span>
    </div>`).join('');
}

function syncDrawerBackdrop() {
  const any = $('#queueDrawer').classList.contains('open') || $('#trackDrawer').classList.contains('open');
  $('#drawerBackdrop').classList.toggle('open', any);
}

function openQueue() {
  renderQueue();
  $('#queueDrawer').classList.add('open');
  $('#queueDrawer').setAttribute('aria-hidden', 'false');
  syncDrawerBackdrop();
  document.body.classList.add('no-scroll');
}

function closeQueue() {
  $('#queueDrawer').classList.remove('open');
  $('#queueDrawer').setAttribute('aria-hidden', 'true');
  syncDrawerBackdrop();
  if (!$('#trackDrawer').classList.contains('open') && !$('#nowPlaying').classList.contains('open')) {
    document.body.classList.remove('no-scroll');
  }
}

function openPlayer() {
  $('#nowPlaying').classList.add('open');
  $('#nowPlaying').setAttribute('aria-hidden', 'false');
  document.body.classList.add('no-scroll');
  if (state.current) {
    renderNowPlaying();
    renderLyrics();
    updateProgress();
  }
}

function closePlayer() {
  $('#nowPlaying').classList.remove('open');
  $('#nowPlaying').setAttribute('aria-hidden', 'true');
  if (!$('#queueDrawer').classList.contains('open') && !$('#trackDrawer').classList.contains('open')) {
    document.body.classList.remove('no-scroll');
  }
}

/* -------------------------------------------------------------------------
   单曲详情抽屉
   ------------------------------------------------------------------------- */

async function openTrack(id) {
  try {
    const t = await api(`/api/tracks/${id}`);
    state.trackDetail = t;
    const issues = t.tag_issues || [];
    const lyricsText = t.lyrics && t.lyrics.text ? t.lyrics.text : '';
    $('#trackDrawerBody').innerHTML = `
      <div class="td-hero" style="--cover:url('${esc(t.cover_url)}')">
        <div class="td-hero-inner">
          <img class="td-cover" src="${esc(t.cover_url)}" alt="" onerror="this.src='/static/img/icon.svg'">
          <div class="td-hero-meta">
            <div class="td-title">${esc(t.title)}</div>
            <div class="td-artist">${esc(t.artist_display)} · ${esc(t.album_display)}</div>
            <div class="np-meta" style="margin-top:10px">
              <span class="badge badge-accent">${esc(t.quality || t.ext)}</span>
              ${t.has_lyrics ? `<span class="badge ${t.lyrics_synced ? 'badge-ok' : ''}">${t.lyrics_synced ? '逐行歌词' : '纯文本歌词'}</span>` : '<span class="badge badge-bad">缺歌词</span>'}
              ${t.has_cover ? `<span class="badge ${t.cover_source === 'embed' ? 'badge-ok' : ''}">${t.cover_source === 'embed' ? '内嵌封面' : '封面文件'}</span>` : '<span class="badge badge-bad">缺封面</span>'}
            </div>
          </div>
        </div>
      </div>
      <div class="td-body">
        <div class="td-actions">
          <button class="btn btn-primary btn-sm" data-action="play" data-id="${t.id}">${ICONS.play}<span>播放</span></button>
          <button class="btn btn-soft btn-sm" data-action="repair-track" data-id="${t.id}" data-targets="lyrics,cover">${ICONS.wrench}<span>一键修复</span></button>
          <button class="btn btn-ghost btn-sm" data-action="candidates" data-id="${t.id}" data-kind="lyrics">歌词候选</button>
          <button class="btn btn-ghost btn-sm" data-action="candidates" data-id="${t.id}" data-kind="cover">封面候选</button>
          <button class="btn btn-ghost btn-sm" data-action="favorite-track" data-id="${t.id}">${ICONS.heart}<span>${t.favorite ? '取消收藏' : '收藏'}</span></button>
        </div>
        ${issues.length ? `<div class="td-section"><div class="td-section-title">标签问题</div><div style="display:flex;gap:6px;flex-wrap:wrap">${issues.map((i) => `<span class="badge badge-warn">${esc(i.label)}</span>`).join('')}</div></div>` : ''}
        <div class="td-section">
          <div class="td-section-title">文件</div>
          <div class="kv-list">
            <span class="k">路径</span><span class="v mono">${esc(t.path)}</span>
            <span class="k">大小</span><span class="v">${esc(t.size_text)} · ${esc(t.duration_text)}</span>
            <span class="k">规格</span><span class="v">${esc(t.quality || '—')}</span>
            <span class="k">加入时间</span><span class="v">${relativeTime(t.added_at)}</span>
            <span class="k">播放次数</span><span class="v">${t.play_count || 0}</span>
            <span class="k">修复尝试</span><span class="v">${t.repair_attempts || 0} 次${t.last_repair_status ? ' · ' + esc(t.last_repair_status) : ''}</span>
          </div>
        </div>
        <div class="td-section">
          <div class="td-section-title">标签</div>
          <div class="td-tags-form">
            <label class="field"><span>标题</span><input class="input" id="tagTitle" value="${esc(t.title_raw || t.title)}"></label>
            <label class="field"><span>歌手</span><input class="input" id="tagArtist" value="${esc(t.artist)}"></label>
            <label class="field"><span>专辑</span><input class="input" id="tagAlbum" value="${esc(t.album)}"></label>
            <label class="field"><span>年份</span><input class="input" id="tagYear" value="${esc(t.year || '')}"></label>
          </div>
          <button class="btn btn-soft btn-sm" style="margin-top:10px" data-action="save-tags" data-id="${t.id}">保存标签</button>
        </div>
        <div class="td-section">
          <div class="td-section-title">歌词 ${t.lyrics && t.lyrics.source ? '· ' + (t.lyrics.source === 'embed' ? '内嵌标签' : t.lyrics.source === 'lrc' ? '外挂 LRC' : esc(t.lyrics.source)) : ''}</div>
          <div class="td-lyrics">${t.no_lyrics ? '<span class="dim">已标记为纯音乐，不需要歌词</span>' : (lyricsText ? esc(lyricsText.slice(0, 3000)) : '<span class="dim">暂无歌词</span>')}</div>
          ${['broken', 'suspect'].includes(t.lyrics_timing_status) ? `<div class="modal-note" style="color:var(--warn);margin-top:8px">时间轴异常：${esc((t.lyrics_timing_issues || []).map((i) => i.label || '').filter(Boolean).join('；') || '歌词与音频时长不匹配')}</div>` : ''}
          <div style="display:flex;gap:8px;margin-top:10px">
            <button class="btn btn-ghost btn-sm" data-action="toggle-no-lyrics" data-id="${t.id}">${t.no_lyrics ? '恢复需要歌词' : '标记为纯音乐'}</button>
            <button class="btn btn-ghost btn-sm" data-action="edit-lyrics" data-id="${t.id}">编辑歌词</button>
            <button class="btn btn-ghost btn-sm" data-action="repair-track" data-targets="lyrics" data-id="${t.id}">重新匹配时间轴</button>
            <button class="btn btn-ghost btn-sm" data-action="offset-reset" data-id="${t.id}">重置偏移</button>
          </div>
        </div>
        <div class="td-section">
          <div class="td-section-title">封面</div>
          <div style="display:flex;align-items:center;gap:14px">
            <img src="${esc(t.cover_url)}" style="width:84px;height:84px;border-radius:10px;object-fit:cover" alt="" onerror="this.src='/static/img/icon.svg'">
            <div>
              <div class="modal-note">${t.cover && t.cover.present ? `${t.cover.width} × ${t.cover.height} 像素 · ${fmtSize(t.cover.bytes)}` : '暂无封面'}</div>
              <div style="display:flex;gap:8px;margin-top:8px">
                <button class="btn btn-ghost btn-sm" data-action="upload-cover" data-id="${t.id}">${ICONS.upload}上传</button>
                <button class="btn btn-ghost btn-sm" data-action="candidates" data-id="${t.id}" data-kind="cover">在线查找</button>
              </div>
            </div>
          </div>
        </div>
        <div class="td-section">
          <div class="td-section-title">危险操作</div>
          <div style="display:flex;gap:8px;flex-wrap:wrap">
            <button class="btn btn-ghost btn-sm" data-action="undo-track" data-id="${t.id}" ${t.backup ? '' : 'disabled'}>撤销最近修复</button>
            <button class="btn btn-danger btn-sm" data-action="trash-track" data-id="${t.id}">移入回收站</button>
          </div>
          <p class="modal-note" style="margin-top:8px">移入回收站不会直接删除，文件会保存在数据目录的 trash 文件夹中。</p>
        </div>
      </div>`;
    $('#trackDrawer').classList.add('open');
    $('#trackDrawer').setAttribute('aria-hidden', 'false');
    syncDrawerBackdrop();
    document.body.classList.add('no-scroll');
  } catch (err) {
    toast(err.message, 'err');
  }
}

function closeTrack() {
  $('#trackDrawer').classList.remove('open');
  $('#trackDrawer').setAttribute('aria-hidden', 'true');
  syncDrawerBackdrop();
  if (!$('#queueDrawer').classList.contains('open') && !$('#nowPlaying').classList.contains('open')) {
    document.body.classList.remove('no-scroll');
  }
}

async function saveTags(id) {
  const tags = {
    title: $('#tagTitle') ? $('#tagTitle').value.trim() : '',
    artist: $('#tagArtist') ? $('#tagArtist').value.trim() : '',
    album: $('#tagAlbum') ? $('#tagAlbum').value.trim() : '',
    year: $('#tagYear') ? $('#tagYear').value.trim() : '',
  };
  try {
    await api(`/api/tracks/${id}/tags`, { method: 'POST', body: { tags } });
    toast('标签已保存');
    await openTrack(id);
    if (state.current && state.current.id === id) refreshCurrentMedia();
    loadOverview().catch(() => {});
    if (state.route === 'library') loadLibrary();
  } catch (err) {
    toast(err.message, 'err');
  }
}

/* -------------------------------------------------------------------------
   候选选择
   ------------------------------------------------------------------------- */

function candidateScoreClass(score) {
  if (score >= 80) return '';
  if (score >= 62) return 'medium';
  return 'low';
}

function renderCandidates() {
  const c = state.candidates;
  const box = $('#candidateBody');
  if (!box) return;
  if (c.loading) {
    box.innerHTML = '<div class="empty" style="padding:36px"><div class="boot-orb" style="width:38px;height:38px"></div><p>正在向在线数据源检索…</p></div>';
    return;
  }
  if (!c.data) { box.innerHTML = ''; return; }
  const data = c.data;
  const hits = data.hits || [];
  const errors = (data.errors || []).map((e) => `<span class="badge badge-warn">${esc(e.label)}：${esc(e.error)}</span>`).join(' ');
  if (!hits.length) {
    box.innerHTML = `<div class="empty" style="padding:34px"><span class="empty-icon">${ICONS.alert}</span><h3>没有找到候选</h3><p>可以换一个数据源，或手动粘贴歌词 / 上传封面。</p><div style="display:flex;gap:6px;flex-wrap:wrap;justify-content:center">${errors}</div></div>`;
    return;
  }
  box.innerHTML = `
    <div class="candidate-list">
      ${hits.map((hit, index) => c.kind === 'lyrics' ? `
        <div class="candidate">
          <div class="candidate-main">
            <div class="candidate-top"><span class="candidate-title">${esc(hit.title)}</span><span class="badge badge-accent">${esc(hit.provider_label)}</span>${hit.synced ? '<span class="badge badge-ok">逐行</span>' : '<span class="badge">纯文本</span>'}</div>
            <div class="candidate-sub">${esc(hit.artist)}${hit.album ? ' · ' + esc(hit.album) : ''}${hit.duration ? ' · ' + fmtTime(hit.duration) : ''} · ${hit.lines} 行</div>
            <div class="candidate-lyrics">${esc((hit.text || '').slice(0, 220))}</div>
          </div>
          <div class="candidate-side">
            <span class="score ${candidateScoreClass(hit.score)}">${Math.round(hit.score)} 分</span>
            <button class="btn btn-soft btn-sm" data-action="apply-candidate" data-index="${index}">应用</button>
          </div>
        </div>` : `
        <div class="candidate">
          <img class="candidate-cover" src="${esc(hit.preview_url)}" alt="" loading="lazy" onerror="this.src='/static/img/icon.svg'">
          <div class="candidate-main">
            <div class="candidate-top"><span class="candidate-title">${esc(hit.title)}</span><span class="badge badge-accent">${esc(hit.provider_label)}</span></div>
            <div class="candidate-sub">${esc(hit.artist)}${hit.album ? ' · ' + esc(hit.album) : ''}${hit.duration ? ' · ' + fmtTime(hit.duration) : ''}</div>
            <div class="candidate-sub" style="margin-top:6px">点击应用后会下载、压缩并写入音频标签</div>
          </div>
          <div class="candidate-side">
            <span class="score ${candidateScoreClass(hit.score)}">${Math.round(hit.score)} 分</span>
            <button class="btn btn-soft btn-sm" data-action="apply-candidate" data-index="${index}">应用</button>
          </div>
        </div>`).join('')}
    </div>
    <div style="margin-top:12px;display:flex;gap:6px;flex-wrap:wrap">${errors}</div>`;
}

async function openCandidates(kind, trackId = 0) {
  const id = trackId || (state.current ? state.current.id : 0);
  if (!id) { toast('请先选择一首歌', 'err'); return; }
  state.candidates = { data: null, kind, trackId: id, loading: true };
  const title = kind === 'lyrics' ? '选择歌词' : '选择封面';
  openModal(`
    <div class="modal-head">
      <div><div class="modal-title">${title}</div><div class="modal-sub">按匹配度排序，分数越高越可信</div></div>
      <button class="icon-btn modal-close" data-action="close-modal">${ICONS.close}</button>
    </div>
    <div class="modal-body">
      <div class="tabs" style="margin-bottom:14px">
        <button class="tab ${kind === 'lyrics' ? 'active' : ''}" data-action="candidate-tab" data-kind="lyrics">歌词</button>
        <button class="tab ${kind === 'cover' ? 'active' : ''}" data-action="candidate-tab" data-kind="cover">封面</button>
      </div>
      <div id="candidateBody"></div>
    </div>`, { wide: true });
  renderCandidates();
  await fetchCandidates();
}

async function fetchCandidates() {
  const c = state.candidates;
  try {
    const data = await api(`/api/tracks/${c.trackId}/candidates?kind=${c.kind}`);
    c.data = data;
    c.loading = false;
    renderCandidates();
  } catch (err) {
    c.loading = false;
    const box = $('#candidateBody');
    if (box) box.innerHTML = `<div class="empty" style="padding:30px"><p>${esc(err.message)}</p></div>`;
  }
}

async function applyCandidate(index) {
  const c = state.candidates;
  const hit = c.data && c.data.hits ? c.data.hits[index] : null;
  if (!hit) return;
  try {
    const body = { kind: c.kind, candidate: hit };
    await api(`/api/tracks/${c.trackId}/apply`, { method: 'POST', body });
    closeModal();
    toast(c.kind === 'lyrics' ? '歌词已写入' : '封面已写入');
    if (state.current && state.current.id === c.trackId) await refreshCurrentMedia();
    if ($('#trackDrawer').classList.contains('open')) await openTrack(c.trackId);
    loadOverview().catch(() => {});
    if (state.route === 'library') loadLibrary();
  } catch (err) {
    toast(err.message, 'err');
  }
}

/* -------------------------------------------------------------------------
   歌词编辑 / 封面上传
   ------------------------------------------------------------------------- */

async function openLyricsEditor(trackId = 0) {
  const id = trackId || (state.current ? state.current.id : 0);
  if (!id) return;
  let data = { text: '', offset_ms: 0 };
  try { data = await api(`/api/tracks/${id}/lyrics`); } catch (err) { /* 允许为空 */ }
  openModal(`
    <div class="modal-head">
      <div><div class="modal-title">编辑歌词</div><div class="modal-sub">支持 LRC 时间轴格式，例如 [00:12.34]歌词</div></div>
      <button class="icon-btn modal-close" data-action="close-modal">${ICONS.close}</button>
    </div>
    <div class="modal-body">
      <textarea class="textarea" id="lyricsEditor" style="min-height:360px" placeholder="[00:00.00]歌词…">${esc(data.text || '')}</textarea>
      <label class="field" style="margin-top:12px;max-width:220px"><span>时间偏移（毫秒，正数表示歌词延后）</span><input class="input" id="lyricsOffsetInput" type="number" value="${Number(data.offset_ms) || 0}"></label>
    </div>
    <div class="modal-foot">
      <span class="modal-note">保存后会重新写入音频内嵌歌词</span>
      <span class="spacer"></span>
      <button class="btn btn-ghost" data-action="close-modal">取消</button>
      <button class="btn btn-primary" data-action="save-lyrics" data-id="${id}">保存歌词</button>
    </div>`, { wide: true });
}

async function saveLyrics(id) {
  const text = $('#lyricsEditor') ? $('#lyricsEditor').value : '';
  const offset = $('#lyricsOffsetInput') ? Number($('#lyricsOffsetInput').value || 0) : 0;
  try {
    await api(`/api/tracks/${id}/lyrics`, { method: 'PUT', body: { text, offset_ms: offset } });
    closeModal();
    toast('歌词已保存');
    if (state.current && state.current.id === id) await refreshCurrentMedia();
    if ($('#trackDrawer').classList.contains('open')) await openTrack(id);
    if (state.route === 'library') loadLibrary();
  } catch (err) {
    toast(err.message, 'err');
  }
}

function openCoverUpload(id) {
  const input = document.createElement('input');
  input.type = 'file';
  input.accept = 'image/jpeg,image/png,image/webp';
  input.onchange = async () => {
    const file = input.files && input.files[0];
    if (!file) return;
    const form = new FormData();
    form.append('file', file);
    try {
      await api(`/api/tracks/${id}/cover-upload`, { method: 'POST', body: form });
      toast('封面已更新');
      if (state.current && state.current.id === id) await refreshCurrentMedia();
      await openTrack(id);
      if (state.route === 'library') loadLibrary();
    } catch (err) {
      toast(err.message, 'err');
    }
  };
  input.click();
}

/* -------------------------------------------------------------------------
   文件夹选择
   ------------------------------------------------------------------------- */

async function openFolderPicker(mandatory = false) {
  state.folder.mandatory = mandatory;
  try {
    const data = await api('/api/fs/list');
    state.folder = { ...state.folder, roots: data.roots || [], dirs: [], path: '', parent: '', audio_count: 0, mandatory };
    renderFolderModal();
    if (state.overview && state.overview.music_dir) {
      await loadFolder(state.overview.music_dir);
    }
  } catch (err) {
    toast(err.message, 'err');
  }
}

async function loadFolder(path) {
  try {
    const data = await api(`/api/fs/list?path=${encodeURIComponent(path)}`);
    state.folder = { ...state.folder, ...data };
    renderFolderModal();
  } catch (err) {
    toast(err.message, 'err');
  }
}

function renderFolderModal() {
  const f = state.folder;
  const existing = state.overview && state.overview.initialized;
  const roots = (f.roots || []).map((r) => `<button class="folder-root" data-action="folder-root" data-path="${esc(r.path)}">${esc(r.label)}</button>`).join('');
  const dirs = (f.dirs || []).map((d) => `
    <div class="folder-entry" data-action="folder-enter" data-path="${esc(d.path)}">
      ${ICONS.folder}
      <span class="fname">${esc(d.name)}</span>
      <span class="fcount">${d.audio_count ? d.audio_count + ' 首音乐' : ''}</span>
    </div>`).join('') || '<div class="folder-empty">这里没有子文件夹</div>';
  openModal(`
    <div class="modal-head">
      <div><div class="modal-title">${existing ? '更换音乐文件夹' : '选择音乐文件夹'}</div><div class="modal-sub">插件会扫描这个目录，并把歌词与封面写入歌曲文件</div></div>
      ${f.mandatory ? '' : `<button class="icon-btn modal-close" data-action="close-modal">${ICONS.close}</button>`}
    </div>
    <div class="modal-body">
      <div class="folder-current">${esc(f.path || '请选择下方的快捷位置，或直接输入路径')}</div>
      <div class="folder-roots">${roots}</div>
      <div class="folder-list">${dirs}</div>
      <div style="display:flex;gap:8px;align-items:center">
        <input class="input" id="folderManual" placeholder="也可以直接输入绝对路径，例如 /home/pi/Music" value="${esc(f.path || '')}">
        <button class="btn btn-ghost btn-sm" data-action="folder-up" ${f.parent ? '' : 'disabled'}>上级</button>
      </div>
      <div id="setupProgress" class="modal-note" style="margin-top:12px">
        ${f.path ? `当前目录直接包含 <strong style="color:var(--text)">${f.audio_count || 0}</strong> 首音乐（子文件夹需进入查看）。` : ''}
      </div>
    </div>
    <div class="modal-foot">
      <span class="modal-note">${existing ? '更换目录会重建索引，不会动你的音频文件。' : '建议先在树莓派上建好音乐目录，再从这里选择。'}</span>
      <span class="spacer"></span>
      ${f.mandatory ? '' : '<button class="btn btn-ghost" data-action="close-modal">取消</button>'}
      <button class="btn btn-primary" data-action="folder-use" ${f.path ? '' : 'disabled'}>使用此文件夹</button>
    </div>`, { wide: true, closable: !f.mandatory });
}

async function useFolder() {
  const manual = $('#folderManual') ? $('#folderManual').value.trim() : '';
  const path = manual || state.folder.path;
  if (!path) return;
  if (state.folder.mandatory === false && state.overview && state.overview.initialized && state.overview.music_dir !== path) {
    const ok = await confirmDialog('更换音乐文件夹', `将把音乐目录切换为 <strong>${esc(path)}</strong>，并重新建立索引。音频文件不会被移动。`, '切换并扫描');
    if (!ok) return;
  }
  $('#setupProgress').innerHTML = '正在保存并扫描，请稍候…';
  try {
    const res = await api('/api/setup', { method: 'POST', body: { path } });
    state.overview.music_dir = res.music_dir;
    if (res.job_id) {
      watchJob(res.job_id, {
        silent: true,
        onProgress: (job) => {
          const el = $('#setupProgress');
          if (el) el.innerHTML = `扫描中 ${job.percent || 0}% · ${job.done || 0}/${job.total || 0} · ${esc(job.current || '')}`;
        },
        onDone: async (job) => {
          closeModal();
          await loadOverview();
          toast(job.status === 'done' ? '音乐库已就绪' : '扫描结束：' + job.message, job.status === 'done' ? 'ok' : 'err');
          navigate('dashboard');
        },
      });
    } else {
      closeModal();
      await loadOverview();
      navigate('dashboard');
    }
  } catch (err) {
    const el = $('#setupProgress');
    if (el) el.innerHTML = `<span style="color:var(--red)">${esc(err.message)}</span>`;
  }
}

/* -------------------------------------------------------------------------
   任务轮询
   ------------------------------------------------------------------------- */

function isJobDone(job) {
  return ['done', 'error', 'cancelled'].includes(job.status);
}

function watchJob(jobId, { onDone, onProgress, silent = false } = {}) {
  state.jobId = jobId;
  clearInterval(state.jobTimer);
  const tick = async () => {
    try {
      const job = await api(`/api/jobs/${jobId}`);
      state.job = job;
      if (onProgress) onProgress(job);
      updateJobPanel();
      if (isJobDone(job)) {
        clearInterval(state.jobTimer);
        state.jobTimer = null;
        if (!silent) {
          if (job.status === 'done') toast(`任务完成：${job.message}`, 'ok', 5000);
          else if (job.status === 'cancelled') toast('任务已取消', 'ok');
          else toast('任务失败：' + (job.error || job.message), 'err', 6000);
        }
        try { await loadOverview(); } catch (err) { /* 忽略刷新失败 */ }
        if (state.route === 'library') loadLibrary();
        if (!silent) {
          if (state.route === 'dashboard') renderDashboard();
          else if (state.route === 'repair') renderRepair();
          else if (state.route === 'automation') renderAutomation();
        }
        if (onDone) onDone(job);
      }
    } catch (err) {
      clearInterval(state.jobTimer);
      state.jobTimer = null;
      if (!silent) toast(err.message, 'err');
    }
  };
  tick();
  state.jobTimer = setInterval(tick, 900);
}

function updateJobPanel() {
  if (state.route !== 'repair' && state.route !== 'automation') return;
  const panel = $('#jobPanel');
  if (panel) panel.innerHTML = renderJobPanel(state.job);
}

/* -------------------------------------------------------------------------
   批量修复 / 扫描
   ------------------------------------------------------------------------- */

async function startScan(full = false) {
  try {
    const res = await api('/api/scan', { method: 'POST', body: { full } });
    toast(full ? '正在全量扫描…' : '正在扫描曲库…');
    watchJob(res.job_id, { onDone: () => { if (state.route === 'library') loadLibrary(); } });
  } catch (err) {
    toast(err.message, 'err');
  }
}

async function startBatch(filter, targets, { limit = 0, force = false, dryRun = false } = {}) {
  try {
    const res = await api('/api/repair/batch', {
      method: 'POST',
      body: { filter, targets, limit, force, dry_run: dryRun },
    });
    if (state.route !== 'repair') navigate('repair');
    else updateJobPanel();
    toast('修复任务已提交');
    watchJob(res.job_id);
  } catch (err) {
    toast(err.message, 'err');
  }
}

async function startSingleRepair(id, targets) {
  try {
    const res = await api(`/api/tracks/${id}/repair`, { method: 'POST', body: { targets } });
    toast('修复任务已提交');
    watchJob(res.job_id, { silent: false, onDone: () => { if (state.current && state.current.id === id) refreshCurrentMedia(); } });
  } catch (err) {
    toast(err.message, 'err');
  }
}

/* -------------------------------------------------------------------------
   事件处理
   ------------------------------------------------------------------------- */

async function handleAction(action, el, event) {
  const id = Number(el.dataset.id || 0);
  switch (action) {
    case 'refresh':
      await loadOverview();
      if (state.route === 'library') await loadLibrary();
      else route();
      toast('已刷新', 'ok', 1400);
      break;
    case 'scan':
      await startScan(false);
      break;
    case 'full-scan':
      await startScan(true);
      break;
    case 'open-folder':
      await openFolderPicker(false);
      break;
    case 'close-modal':
      closeModal();
      break;
    case 'modal-cancel':
      resolveConfirm(false);
      break;
    case 'modal-ok':
      resolveConfirm(true);
      break;
    case 'goto-repair':
      navigate('repair');
      break;
    case 'goto-library':
      navigate('library');
      break;
    case 'goto-library-filter':
      state.library.filter = el.dataset.filter || 'all';
      state.library.page = 1;
      state.selected.clear();
      navigate('library');
      break;
    case 'goto-automation':
      navigate('automation');
      break;
    case 'quick-fix-filter': {
      const filter = el.dataset.filter || 'missing_any';
      if (filter === 'bad_lyrics') await startBatch('bad_lyrics', ['lyrics'], { force: true });
      else await startBatch(filter, ['lyrics', 'cover']);
      break;
    }
    case 'fix-timing':
      await startBatch('bad_lyrics', ['lyrics'], { force: true });
      break;
    case 'start-quick-fix':
      await startBatch('missing_any', ['lyrics', 'cover']);
      break;
    case 'check-providers':
      await testProviders(true);
      break;
    case 'export-m3u':
      window.open('/api/export/m3u', '_blank');
      break;
    case 'filter':
      state.library.filter = el.dataset.filter || 'all';
      state.library.page = 1;
      state.selected.clear();
      renderLibrary();
      await loadLibrary();
      break;
    case 'clear-search':
      state.library.q = '';
      $('#globalSearch').value = '';
      state.library.page = 1;
      await loadLibrary();
      break;
    case 'page-prev':
      if (state.library.page > 1) { state.library.page -= 1; state.selected.clear(); await loadLibrary(); }
      break;
    case 'page-next':
      state.library.page += 1; state.selected.clear(); await loadLibrary();
      break;
    case 'select-all': {
      const ids = state.library.items.map((t) => t.id);
      const all = ids.every((x) => state.selected.has(x));
      if (all) ids.forEach((x) => state.selected.delete(x));
      else ids.forEach((x) => state.selected.add(x));
      renderLibrary();
      break;
    }
    case 'clear-selection':
      state.selected.clear();
      renderLibrary();
      break;
    case 'batch-repair': {
      const targets = (el.dataset.targets || 'lyrics,cover').split(',');
      const ids = Array.from(state.selected);
      if (!ids.length) return;
      try {
        const res = await api('/api/repair/batch', { method: 'POST', body: { ids, targets } });
        navigate('repair');
        toast('批量修复已提交');
        watchJob(res.job_id);
      } catch (err) { toast(err.message, 'err'); }
      break;
    }
    case 'artist-open':
      await artistOpen(el.dataset.name || '');
      break;
    case 'artist-back':
      state.artists.selected = null;
      state.artists.album = null;
      renderArtists();
      break;
    case 'artist-tab':
      state.artists.tab = el.dataset.tab || 'songs';
      state.artists.album = null;
      renderArtists();
      break;
    case 'artist-album': {
      const artist = state.artists.selected || {};
      const album = (artist.albums || []).find((item) => item.name === el.dataset.album);
      state.artists.album = album || null;
      renderArtists();
      break;
    }
    case 'artist-album-back':
      state.artists.album = null;
      renderArtists();
      break;
    case 'play': {
      if (state.route === 'artists' && state.artists.selected) {
        const artist = state.artists.selected;
        const queue = state.artists.album ? state.artists.album.tracks : artist.tracks;
        if (queue && queue.length) await playTracks(queue, id);
      } else if (state.route === 'library') await playTracks(state.library.items, id);
      else {
        const track = (state.queue || []).find((t) => t.id === id) || (state.library.items || []).find((t) => t.id === id);
        if (track) await playTracks(state.library.items.length ? state.library.items : [track], id);
      }
      break;
    }
    case 'open-track':
      await openTrack(id);
      break;
    case 'close-track':
      closeTrack();
      break;
    case 'open-player':
      openPlayer();
      break;
    case 'close-player':
      closePlayer();
      break;
    case 'toggle-play':
      togglePlay();
      break;
    case 'prev':
      prevTrack();
      break;
    case 'next':
      nextTrack();
      break;
    case 'seek':
      seekFromEvent(event, el);
      break;
    case 'seek-lyric': {
      const time = Number(el.dataset.time || 0);
      audio.currentTime = time + (state.lyrics.offset || 0) / 1000;
      updateProgress();
      break;
    }
    case 'toggle-shuffle':
      toggleShuffle();
      break;
    case 'cycle-repeat':
      cycleRepeat();
      break;
    case 'day-chip':
      el.classList.toggle('active');
      break;
    case 'open-queue':
      openQueue();
      break;
    case 'close-queue':
      closeQueue();
      break;
    case 'play-queue': {
      const index = Number(el.dataset.index || 0);
      if (state.queue[index]) {
        state.queueIndex = index;
        await loadTrack(state.queue[index]);
        renderQueue();
      }
      break;
    }
    case 'favorite-current':
      if (state.current) await toggleFavorite(state.current.id);
      break;
    case 'favorite-track':
      await toggleFavorite(id);
      if ($('#trackDrawer').classList.contains('open')) await openTrack(id);
      break;
    case 'repair-current':
      if (state.current) await startSingleRepair(state.current.id, ['lyrics', 'cover']);
      break;
    case 'candidates-current':
      if (state.current) await openCandidates('lyrics', state.current.id);
      break;
    case 'open-track-current':
      if (state.current) await openTrack(state.current.id);
      break;
    case 'edit-lyrics-current':
      if (state.current) await openLyricsEditor(state.current.id);
      break;
    case 'offset-up':
    case 'offset-down': {
      if (!state.current) return;
      const delta = action === 'offset-up' ? 500 : -500;
      state.lyrics.offset = Math.max(-60000, Math.min(60000, (state.lyrics.offset || 0) + delta));
      updateOffsetLabel();
      updateActiveLyric();
      const trackId = state.current.id;
      clearTimeout(state.lyricTimer);
      state.lyricTimer = setTimeout(() => {
        api(`/api/tracks/${trackId}`, { method: 'PATCH', body: { offset_ms: state.lyrics.offset } }).catch(() => {});
      }, 700);
      break;
    }
    case 'offset-reset': {
      try {
        await api(`/api/tracks/${id}`, { method: 'PATCH', body: { offset_ms: 0 } });
        if (state.current && state.current.id === id) {
          state.lyrics.offset = 0;
          updateOffsetLabel();
          updateActiveLyric();
        }
        toast('歌词偏移已重置', 'ok', 1600);
      } catch (err) { toast(err.message, 'err'); }
      break;
    }
    case 'repair-track':
      await startSingleRepair(id, (el.dataset.targets || 'lyrics,cover').split(','));
      break;
    case 'toggle-no-lyrics': {
      try {
        const value = !(state.trackDetail && state.trackDetail.no_lyrics);
        await api(`/api/tracks/${id}/no-lyrics`, { method: 'POST', body: { value } });
        toast(value ? '已标记为纯音乐' : '已恢复为需要歌词');
        await loadOverview();
        await openTrack(id);
        if (state.route === 'library') loadLibrary();
      } catch (err) { toast(err.message, 'err'); }
      break;
    }
    case 'candidates':
      await openCandidates(el.dataset.kind || 'cover', id);
      break;
    case 'apply-candidate':
      await applyCandidate(Number(el.dataset.index || 0));
      break;
    case 'candidate-tab':
      state.candidates.kind = el.dataset.kind || 'lyrics';
      state.candidates.loading = true;
      $$('.tab', $('#modalRoot')).forEach((t) => t.classList.toggle('active', t.dataset.kind === state.candidates.kind));
      renderCandidates();
      await fetchCandidates();
      break;
    case 'edit-lyrics':
      await openLyricsEditor(id);
      break;
    case 'save-lyrics':
      await saveLyrics(id);
      break;
    case 'save-tags':
      await saveTags(id);
      break;
    case 'upload-cover':
      openCoverUpload(id);
      break;
    case 'undo-track': {
      const ok = await confirmDialog('撤销修复', '将把这首歌的标签、歌词和封面恢复到最近一次修复之前的状态。', '撤销');
      if (!ok) return;
      try {
        await api(`/api/tracks/${id}/undo`, { method: 'POST' });
        toast('已撤销');
        await openTrack(id);
        if (state.current && state.current.id === id) refreshCurrentMedia();
        if (state.route === 'library') loadLibrary();
      } catch (err) { toast(err.message, 'err'); }
      break;
    }
    case 'trash-track': {
      const ok = await confirmDialog('移入回收站', '文件会移动到数据目录的 trash 文件夹，不会直接删除。确认继续？', '移入回收站');
      if (!ok) return;
      try {
        await api('/api/duplicates/trash', { method: 'POST', body: { remove_ids: [id] } });
        toast('已移入回收站');
        closeTrack();
        await loadOverview();
        if (state.current && state.current.id === id) { audio.pause(); state.current = null; }
        if (state.route === 'library') loadLibrary();
      } catch (err) { toast(err.message, 'err'); }
      break;
    }
    case 'cancel-job':
      if (state.jobId) {
        try { await api(`/api/jobs/${state.jobId}/cancel`, { method: 'POST' }); toast('已请求取消'); }
        catch (err) { toast(err.message, 'err'); }
      }
      break;
    case 'start-batch': {
      const scope = ($('#scopeChips .seg.active') || {}).dataset ? $('#scopeChips .seg.active').dataset.scope : 'missing_any';
      const targets = [];
      if ($('#targetLyrics') && $('#targetLyrics').checked) targets.push('lyrics');
      if ($('#targetCover') && $('#targetCover').checked) targets.push('cover');
      if ($('#targetTags') && $('#targetTags').checked) targets.push('tags');
      if (!targets.length) { toast('请至少选择一个修复目标', 'err'); return; }
      const limit = Number($('#repairLimit') ? $('#repairLimit').value : 0) || 0;
      const finalTargets = scope === 'bad_lyrics'
        ? ['lyrics']
        : scope === 'issues'
          ? ['tags']
          : scope === 'all'
            ? ['lyrics', 'cover', 'tags']
            : targets;
      await startBatch(scope, finalTargets, {
        limit,
        force: scope === 'bad_lyrics' || scope === 'all' || ($('#repairForce') ? $('#repairForce').checked : false),
        dryRun: $('#repairDry') ? $('#repairDry').checked : false,
      });
      break;
    }
    case 'save-conversion':
      try { await saveConversion(); } catch (err) { toast(err.message, 'err'); }
      break;
    case 'convert-preview':
      await previewConversion();
      break;
    case 'convert-now':
      await runConversion();
      break;
    case 'add-conv-rule': {
      const box = $('#convRules');
      if (box) box.insertAdjacentHTML('beforeend', conversionRuleHtml('wav', 'flac'));
      break;
    }
    case 'remove-conv-rule': {
      const row = el.closest('[data-conv-rule]');
      if (row) row.remove();
      break;
    }
    case 'save-duplicates':
      try { await saveDuplicates(); } catch (err) { toast(err.message, 'err'); }
      break;
    case 'auto-clean-duplicates':
      await autoCleanDuplicates();
      break;
    case 'ignore-duplicate':
      await ignoreDuplicate(id);
      break;
    case 'clear-ignored-duplicates':
      try {
        await api('/api/duplicates/ignored', { method: 'DELETE' });
        toast('已恢复所有忽略');
        await loadOverview(); loadLibrary();
      } catch (err) { toast(err.message, 'err'); }
      break;
    case 'save-organization':
      try { await saveOrganization(); } catch (err) { toast(err.message, 'err'); }
      break;
    case 'save-automation': {
      const days = $$('#autoDays .day-chip.active').map((el) => Number(el.dataset.day));
      const auto = {
        scan_on_start: $('[data-auto-key="scan_on_start"]').checked,
        watch_enabled: $('[data-auto-key="watch_enabled"]').checked,
        watch_changes: $('[data-auto-key="watch_changes"]').checked,
        auto_fix_new: $('[data-auto-key="auto_fix_new"]').checked,
        fix_on_play: $('[data-auto-key="fix_on_play"]').checked,
        auto_fix_timing: $('[data-auto-key="auto_fix_timing"]').checked,
        schedule_enabled: $('[data-auto-key="schedule_enabled"]').checked,
        watch_interval_min: Number($('#autoInterval').value || 0),
        schedule_time: $('#autoTime').value || '03:30',
        schedule_days: days.length ? days : [1, 2, 3, 4, 5, 6, 7],
        max_fix_per_run: Number($('#autoMax').value || 40),
        targets: ($('#autoTargets').value || 'lyrics,cover').split(','),
      };
      try {
        await api('/api/settings', { method: 'PUT', body: { config: { automation: auto } } });
        toast('自动化设置已保存');
        await loadOverview();
        renderAutomation();
      } catch (err) { toast(err.message, 'err'); }
      break;
    }
    case 'auto-scan-now':
      await startScan(false);
      break;
    case 'auto-fix-now': {
      const auto = state.overview.automation || {};
      await startBatch('missing_any', auto.targets || ['lyrics', 'cover'], { limit: auto.max_fix_per_run || 40 });
      break;
    }
    case 'save-settings':
      await saveSettings();
      break;
    case 'reload-settings':
      state.settings = null;
      await loadSettings();
      renderSettings();
      toast('已还原未保存的修改', 'ok', 1600);
      break;
    case 'save-password': {
      const input = $('#newPassword');
      const password = input ? input.value.trim() : '';
      const clear = $('#clearPassword') && $('#clearPassword').checked;
      if (!password && !clear) { toast('请输入新密码', 'err'); return; }
      try {
        const res = await api('/api/settings', { method: 'PUT', body: clear ? { clear_password: true } : { new_password: password } });
        state.settings = null;
        await loadSettings();
        renderSettings();
        toast(clear ? '已关闭密码保护' : '密码已更新，下次访问需要登录');
      } catch (err) { toast(err.message, 'err'); }
      break;
    }
    case 'provider-move': {
      const kind = el.dataset.kind;
      const name = el.dataset.name;
      const dir = Number(el.dataset.dir);
      const list = state.providersDraft[kind];
      const index = list.indexOf(name);
      const target = index + dir;
      if (index < 0 || target < 0 || target >= list.length) return;
      [list[index], list[target]] = [list[target], list[index]];
      const container = kind === 'lyrics' ? $('#providerLyrics') : $('#providerCover');
      if (container) container.innerHTML = providerListHtml(kind);
      break;
    }
    case 'test-providers':
      await testProviders(false);
      break;
    case 'test-navidrome': {
      const box = $('#navidromeResult');
      if (box) box.textContent = '正在连接…';
      try {
        const res = await api('/api/navidrome/test', { method: 'POST' });
        if (box) box.innerHTML = res.connected ? '<span style="color:var(--green)">连接成功</span>' : `<span style="color:var(--red)">${esc(res.info && res.info.error ? res.info.error : '连接失败')}</span>`;
      } catch (err) { if (box) box.textContent = err.message; }
      break;
    }
    case 'navidrome-rescan': {
      const box = $('#navidromeResult');
      try {
        const res = await api('/api/navidrome/rescan', { method: 'POST' });
        if (box) box.innerHTML = `<span style="color:${res.ok ? 'var(--green)' : 'var(--red)'}">${esc(res.message)}</span>`;
        updateIntegrationSync('navidrome', res.last_sync);
      } catch (err) { if (box) box.textContent = err.message; }
      break;
    }
    case 'test-jellyfin':
      await testJellyfin();
      break;
    case 'jellyfin-refresh':
      await refreshJellyfin();
      break;
    case 'lrc-scan':
      state.lrcStats = null;
      await loadLrcStats();
      break;
    case 'lrc-embed':
      try {
        const res = await api('/api/lrc/embed', { method: 'POST' });
        if (!res.job_id) {
          toast(res.reason || '没有需要嵌入的 .lrc', 'ok');
        } else {
          toast('已开始把外置歌词写入音频标签，进度可在修复中心查看');
          watchJob(res.job_id, {
            silent: true,
            onDone: async () => {
              state.lrcStats = null;
              await loadLrcStats();
              loadOverview().catch(() => {});
            },
          });
        }
      } catch (err) { toast(err.message, 'err'); }
      break;
    case 'lrc-clean':
      await cleanLrc();
      break;
    case 'rescan':
      await startScan(false);
      break;
    case 'folder-root':
      await loadFolder(el.dataset.path);
      break;
    case 'folder-enter':
      await loadFolder(el.dataset.path);
      break;
    case 'folder-up':
      if (state.folder.parent) await loadFolder(state.folder.parent);
      break;
    case 'folder-use':
      await useFolder();
      break;
    case 'provider-toggle':
      break;
    case 'file-open':
      await fileOpen(el.dataset.path || '');
      break;
    case 'file-refresh':
      await fileRefresh();
      break;
    case 'file-upload':
      fileUploadPrompt();
      break;
    case 'file-upload-submit':
      fileUploadSubmit();
      break;
    case 'file-export':
      fileExportModal();
      break;
    case 'file-export-submit':
      await fileExportSubmit();
      break;
    case 'file-mkdir':
      await fileMkdir();
      break;
    case 'file-mkdir-submit':
      await fileMkdirSubmit();
      break;
    case 'file-rename':
      await fileRenamePrompt(el.dataset.path || '', el.dataset.name || '');
      break;
    case 'file-rename-submit':
      await fileRenameSubmit();
      break;
    case 'file-delete':
      await fileDelete([el.dataset.path || '']);
      break;
    case 'file-delete-selected':
      await fileDelete(fileSelectedPaths());
      break;
    case 'file-copy':
      await fileTransfer(true);
      break;
    case 'file-move':
      await fileTransfer(false);
      break;
    case 'file-copy-submit':
      await fileTransferSubmit(true);
      break;
    case 'file-move-submit':
      await fileTransferSubmit(false);
      break;
    case 'file-track':
      await openTrack(id);
      break;
    case 'file-download':
      fileDownload(el.dataset.path || '');
      break;
    case 'file-edit-text':
      await fileEditText(el.dataset.path || '');
      break;
    case 'file-save-text':
      await fileSaveText(el.dataset.path || '');
      break;
    case 'file-history':
      await fileOpenHistory();
      break;
    case 'file-undo':
      await fileUndo();
      break;
    case 'file-undo-to':
      await fileUndo(el.dataset.id || '');
      break;
    case 'file-restore-original':
      await fileRestoreOriginal();
      break;
    case 'file-organize':
      await fileOrganizeModal();
      break;
    case 'file-organize-preview':
      await fileOrganizePreview();
      break;
    case 'file-organize-apply':
      await fileOrganizeApply();
      break;
    case 'file-rename-batch':
      fileRenameBatchModal();
      break;
    case 'file-rename-preview':
      await fileRenamePreview();
      break;
    case 'file-rename-apply':
      await fileRenameApply();
      break;
    case 'file-repair':
      await fileRepairLibrary();
      break;
    default:
      break;
  }
}

async function toggleFavorite(id) {
  const track = (state.current && state.current.id === id) ? state.current
    : (state.library.items || []).find((t) => t.id === id);
  if (!track) return;
  const value = !track.favorite;
  try {
    await api(`/api/tracks/${id}`, { method: 'PATCH', body: { favorite: value } });
    track.favorite = value;
    if (state.current && state.current.id === id) {
      state.current.favorite = value;
      $('#pbFav').classList.toggle('active', value);
    }
    toast(value ? '已收藏' : '已取消收藏', 'ok', 1400);
    loadOverview().catch(() => {});
    if (state.route === 'library') loadLibrary();
  } catch (err) {
    toast(err.message, 'err');
  }
}

async function testProviders(showModalResult) {
  toast('正在测试数据源，请稍候…', 'ok', 2000);
  try {
    const res = await api('/api/providers/test', { method: 'POST' });
    const rows = (res.results || []).map((r) => `<div class="candidate">
      <div class="candidate-main">
        <div class="candidate-top"><span class="candidate-title">${esc(r.label)}</span><span class="badge ${r.ok ? 'badge-ok' : 'badge-bad'}">${r.ok ? '可用' : '不可用'}</span></div>
        <div class="candidate-sub">${r.count} 条结果 · ${r.elapsed}s ${r.error ? '· ' + esc(r.error) : ''}</div>
      </div>
    </div>`).join('');
    const box = $('#providerTestResult');
    if (box) box.innerHTML = (res.results || []).map((r) => `<div>${r.ok ? '✓' : '✗'} ${esc(r.label)} · ${r.count} 条 · ${r.elapsed}s${r.error ? ' · ' + esc(r.error) : ''}</div>`).join('');
    if (showModalResult) {
      openModal(`
        <div class="modal-head"><div><div class="modal-title">数据源连通性</div><div class="modal-sub">使用《晴天》做一次试搜</div></div><button class="icon-btn modal-close" data-action="close-modal">${ICONS.close}</button></div>
        <div class="modal-body"><div class="candidate-list">${rows}</div></div>
        <div class="modal-foot"><span class="spacer"></span><button class="btn btn-primary" data-action="close-modal">知道了</button></div>`);
    } else {
      const ok = (res.results || []).filter((r) => r.ok).length;
      toast(`数据源测试完成：${ok}/${(res.results || []).length} 个可用`);
    }
  } catch (err) {
    toast(err.message, 'err');
  }
}

/* -------------------------------------------------------------------------
   事件绑定
   ------------------------------------------------------------------------- */

function setBuffering(value) {
  document.body.classList.toggle('buffering', Boolean(value));
}

function handleAudioError() {
  if (!state.current || state.retrying) return;
  setBuffering(false);
  const resumeAt = Math.max(audio.currentTime || 0, state.lastPlayTime || 0);
  // 播放到一半断开：自动续播最多两次；开头就失败则直接提示
  if (resumeAt > 1 && state.playRetry < 2) {
    state.playRetry += 1;
    state.retrying = true;
    setBuffering(true);
    toast('播放中断，正在自动续播…', 'err', 2200);
    const recovery = new URL(playbackStreamUrl(state.current), location.origin);
    recovery.searchParams.set('recover', String(Date.now()));
    audio.src = `${recovery.pathname}${recovery.search}`;
    audio.load();
    const timer = setTimeout(() => {
      state.retrying = false;
      setBuffering(false);
    }, 8000);
    audio.addEventListener('loadedmetadata', () => {
      clearTimeout(timer);
      try { audio.currentTime = resumeAt; } catch (err) { /* 忽略 */ }
      audio.play().then(() => {
        state.retrying = false;
        setBuffering(false);
      }).catch(() => {
        state.retrying = false;
        setBuffering(false);
        toast('续播失败，请点击播放重试', 'err');
      });
    }, { once: true });
    return;
  }
  const reason = audio.error && audio.error.code === 4 ? '格式不支持或文件不可用' : '网络或文件读取失败';
  toast(`无法播放：${state.current.title}（${reason}）`, 'err', 5000);
}

function bindEvents() {
  buildEqBars();
  document.addEventListener('click', (event) => {
    if (event.target.matches('input[type="checkbox"]')) return;
    const scope = event.target.closest('#scopeChips .seg');
    if (scope) {
      document.querySelectorAll('#scopeChips .seg').forEach((item) => item.classList.remove('active'));
      scope.classList.add('active');
      return;
    }
    const el = event.target.closest('[data-action]');
    if (!el) return;
    const action = el.dataset.action;
    if (['modal-cancel'].includes(action)) {
      // confirm 取消优先
    }
    handleAction(action, el, event).catch((err) => {
      console.error(err);
      toast(err.message || '操作失败', 'err');
    });
  });

  document.addEventListener('change', (event) => {
    const target = event.target;
    if (target.matches('[data-file-select]')) {
      const path = target.dataset.fileSelect;
      if (target.checked) state.files.selected.add(path); else state.files.selected.delete(path);
      renderFiles();
      return;
    }
    if (target.id === 'fileSelectAll') {
      state.files.selected = target.checked ? new Set(state.files.entries.map((item) => item.path)) : new Set();
      renderFiles();
      return;
    }
    if (target.id === 'fileSort') {
      state.files.sort = target.value;
      loadFiles();
      return;
    }
    if (target.id === 'artistSongSort') {
      state.artists.sortSongs = target.value;
      renderArtists();
      return;
    }
    if (target.id === 'artistAlbumSort') {
      state.artists.sortAlbums = target.value;
      renderArtists();
      return;
    }
    if (target.id === 'organizeScope') {
      const folder = $('#organizeScopeFolderWrap');
      const artist = $('#organizeScopeArtistWrap');
      if (folder) folder.style.display = target.value === 'folder' ? '' : 'none';
      if (artist) artist.style.display = target.value === 'artist' ? '' : 'none';
      return;
    }
    if (target.id === 'organizeMode') {
      const wrap = $('#organizeDirWrap');
      if (wrap) wrap.style.display = target.value === 'custom' ? '' : 'none';
      return;
    }
    if (target.id === 'organizeFileMode') {
      const wrap = $('#organizeFileWrap');
      if (wrap) wrap.style.display = target.value === 'custom' ? '' : 'none';
      return;
    }
    if (target.id === 'renameMode') {
      const wrap = $('#renameTemplateWrap');
      if (wrap) wrap.style.display = target.value === 'custom' ? '' : 'none';
      return;
    }
    if (target.id === 'uploadAutoClassify') {
      const wrap = $('#uploadClassifyWrap');
      if (wrap) wrap.style.display = target.checked ? '' : 'none';
      return;
    }
    if (target.id === 'exportFormat') {
      const wrap = $('#exportBitrateWrap');
      if (wrap) wrap.style.display = ['mp3', 'm4a', 'ogg', 'opus'].includes(target.value) ? '' : 'none';
      return;
    }
    if (target.matches('[data-select-id]')) {
      const id = Number(target.dataset.selectId);
      if (target.checked) state.selected.add(id); else state.selected.delete(id);
      renderLibrary();
      return;
    }
    if (target.id === 'libSort') {
      state.library.sort = target.value;
      state.library.page = 1;
      loadLibrary();
      return;
    }
    if (target.matches('#targetLyrics, #targetCover, #targetTags')) {
      const seg = target.closest('.seg');
      if (seg) seg.classList.toggle('active', target.checked);
      return;
    }
    if (target.matches('[data-provider-toggle]')) {
      const kind = target.dataset.providerToggle;
      const name = target.value;
      const list = state.providersDraft[kind];
      if (target.checked) { if (!list.includes(name)) list.push(name); }
      else { const index = list.indexOf(name); if (index >= 0) list.splice(index, 1); }
      const container = kind === 'lyrics' ? $('#providerLyrics') : $('#providerCover');
      if (container) container.innerHTML = providerListHtml(kind);
      return;
    }
    if (target.matches('[data-day]')) {
      target.classList.toggle('active');
      return;
    }
  });

  document.addEventListener('input', (event) => {
    if (event.target.id === 'fileSearch') {
      state.files.q = event.target.value;
      clearTimeout(state.files.searchTimer);
      state.files.searchTimer = setTimeout(() => loadFiles(), 260);
      return;
    }
    if (event.target.id === 'volume' || event.target.id === 'npVolume') {
      const value = Number(event.target.value);
      audio.volume = value / 100;
      localStorage.setItem('morg.volume', String(value));
      const pbVol = $('#volume');
      const npVol = $('#npVolume');
      if (pbVol) pbVol.value = String(value);
      if (npVol) npVol.value = String(value);
    }
  });

  document.addEventListener('keydown', (event) => {
    const tag = (event.target.tagName || '').toLowerCase();
    const typing = tag === 'input' || tag === 'textarea' || tag === 'select' || event.target.isContentEditable;
    if (event.target.id === 'folderManual' && event.key === 'Enter') {
      event.preventDefault();
      const value = event.target.value.trim();
      if (value) loadFolder(value);
      return;
    }
    if (event.key === 'Escape') {
      if ($('#modalRoot').classList.contains('open')) {
        if (state.confirmResolve) { resolveConfirm(false); return; }
        if ($('#modalRoot').dataset.closable !== '0') closeModal();
        return;
      }
      if ($('#queueDrawer').classList.contains('open')) { closeQueue(); return; }
      if ($('#trackDrawer').classList.contains('open')) { closeTrack(); return; }
      if ($('#nowPlaying').classList.contains('open')) { closePlayer(); return; }
    }
    if (typing) return;
    if (event.key === '/') {
      event.preventDefault();
      const input = $('#globalSearch');
      if (input) { input.focus(); input.select(); }
    }
    if (event.key === ' ') {
      event.preventDefault();
      togglePlay();
    }
    if (event.key === 'ArrowRight') { audio.currentTime = Math.min((audio.duration || 0), audio.currentTime + 5); }
    if (event.key === 'ArrowLeft') { audio.currentTime = Math.max(0, audio.currentTime - 5); }
  });

  // 点击遮罩关闭弹窗 / 抽屉
  $('#modalRoot').addEventListener('mousedown', (event) => {
    if (event.target === $('#modalRoot') && $('#modalRoot').dataset.closable !== '0' && !state.confirmResolve) closeModal();
  });
  $('#drawerBackdrop').addEventListener('click', () => { closeQueue(); closeTrack(); });

  // 顶栏搜索
  const search = $('#globalSearch');
  const doSearch = debounce(() => {
    state.library.q = search.value.trim();
    state.library.page = 1;
    state.selected.clear();
    if (state.route !== 'library') navigate('library');
    else loadLibrary();
  }, 320);
  search.addEventListener('input', doSearch);
  search.addEventListener('keydown', (event) => {
    if (event.key === 'Enter') { event.preventDefault(); doSearch(); }
  });

  // 播放器
  audio.addEventListener('play', () => { updatePlayIcons(); startPlayTimer(); });
  audio.addEventListener('pause', () => { updatePlayIcons(); });
  audio.addEventListener('ended', () => nextTrack(true));
  audio.addEventListener('loadedmetadata', () => { updateProgress(); });
  audio.addEventListener('timeupdate', () => {
    state.lastPlayTime = audio.currentTime || 0;
    if (!audio.paused) updateProgress();
  });
  audio.addEventListener('waiting', () => setBuffering(true));
  audio.addEventListener('stalled', () => setBuffering(true));
  audio.addEventListener('canplay', () => setBuffering(false));
  audio.addEventListener('playing', () => { setBuffering(false); state.retrying = false; updatePlayIcons(); });
  audio.addEventListener('error', handleAudioError);
  updatePlayModeUI();
  const volume = localStorage.getItem('morg.volume') || '80';
  $('#volume').value = volume;
  if ($('#npVolume')) $('#npVolume').value = volume;
  audio.volume = Number(volume) / 100;

  // 全局路由
  window.addEventListener('hashchange', () => route());

  // 定时刷新概览（每 60 秒）
  setInterval(() => {
    if (document.hidden) return;
    loadOverview().then(() => {
      if (state.route === 'dashboard') renderDashboard();
      if (state.route === 'repair' || state.route === 'automation') updateJobPanel();
    }).catch(() => {});
  }, 60000);
}

/* -------------------------------------------------------------------------
   启动
   ------------------------------------------------------------------------- */

async function init() {
  bindEvents();
  try {
    await loadOverview();
  } catch (err) {
    $('#view').innerHTML = `<div class="empty" style="min-height:50vh"><span class="empty-icon">${ICONS.alert}</span><h3>无法连接后端</h3><p>${esc(err.message)}</p><button class="btn btn-soft" data-action="refresh">重试</button></div>`;
    return;
  }
  if (!state.route) state.route = currentRoute();
  await route();
  if (!state.overview.initialized) {
    openFolderPicker(true);
  }
}

if (document.readyState === 'loading') {
  document.addEventListener('DOMContentLoaded', init);
} else {
  init();
}
