// 我在呢 · 管理台
// 体验原则（像手表表盘）：一眼可读、没事安静、有事清楚、主控件可感、电量和在线放在最前面。
// 结构：基础工具 → 首页（表盘/告警/复杂功能/日报）→ 对讲 → 记录 → 故事 → 记忆/错字本 → 维护/测试对话 → 启动
'use strict';

// ---------- 基础 ----------
const $ = (id) => document.getElementById(id);
const ME = { role: null, label: '妈妈', pronoun: '她' };
const isAdmin = () => ME.role === 'admin';
function applyElderLabels() {
  document.querySelectorAll('[data-elder-label]').forEach((el) => { el.textContent = ME.label; });
  document.querySelectorAll('[data-elder-pronoun]').forEach((el) => { el.textContent = ME.pronoun; });
}
function toast(msg, isError) {
  const t = $('toast');
  t.textContent = msg;
  t.className = 'show' + (isError ? ' error' : '');
  clearTimeout(t._h);
  t._h = setTimeout(() => { t.className = isError ? 'error' : ''; }, isError ? 4200 : 2200);
}
async function api(path, opts) {
  const resp = await fetch(path, Object.assign({ headers: { 'Content-Type': 'application/json' } }, opts || {}));
  if (resp.status === 401) { location.href = '/login?next=' + encodeURIComponent(location.pathname); throw new Error('请重新登录'); }
  let data = {}; try { data = await resp.json(); } catch (e) { /* 非 JSON */ }
  if (!resp.ok) throw new Error(data.error || data.message || ('HTTP ' + resp.status));
  return data;
}
const post = (path, body) => api(path, { method: 'POST', body: JSON.stringify(body || {}) });
const pad2 = (n) => String(n).padStart(2, '0');
const fmtDate = (d) => d.getFullYear() + '-' + pad2(d.getMonth() + 1) + '-' + pad2(d.getDate());
const todayStr = () => fmtDate(new Date());
const addDays = (ds, n) => { const d = new Date(ds + 'T00:00:00'); d.setDate(d.getDate() + n); return fmtDate(d); };
const yesterdayStr = () => addDays(todayStr(), -1);
const fmtTime = (ts) => (ts || '').slice(11, 16);
const WEEK = '日一二三四五六';
function niceDate(ds, withWeek) {
  if (ds === todayStr()) return '今天';
  if (ds === yesterdayStr()) return '昨天';
  const d = new Date(ds + 'T00:00:00');
  const s = (d.getFullYear() !== new Date().getFullYear() ? d.getFullYear() + '年' : '') + (d.getMonth() + 1) + '月' + d.getDate() + '日';
  return withWeek ? s + ' 周' + WEEK[d.getDay()] : s;
}
function agoText(sec) {
  sec = Math.max(0, Math.round(sec));
  if (sec < 60) return '刚刚';
  if (sec < 3600) return Math.round(sec / 60) + ' 分钟前';
  if (sec < 86400) return Math.round(sec / 3600) + ' 小时前';
  return Math.round(sec / 86400) + ' 天前';
}
function durText(sec) {
  const mins = Math.max(0, Math.round(sec / 60));
  if (mins < 60) return mins + ' 分钟';
  const h = Math.floor(mins / 60), m = mins % 60;
  if (h >= 24) return Math.floor(h / 24) + ' 天' + (h % 24 ? ' ' + (h % 24) + ' 小时' : '');
  return m ? h + ' 小时 ' + m + ' 分' : h + ' 小时';
}
const STATE_CN = { listening: '待命', user_speaking: '在听', processing: '在想', ai_speaking: '在说话' };
function esc(s) { return String(s == null ? '' : s).replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c])); }
function emptyState(title, body) {
  return '<div class="empty"><span class="empty-title">' + esc(title) + '</span>' + esc(body || '') + '</div>';
}
function haptic(pattern) { try { if (navigator.vibrate) navigator.vibrate(pattern || 10); } catch (e) { /* 不支持就算了 */ } }

// 极简 Markdown：标题 / 加粗 / 无序有序列表 / 段落（先转义，再加标签）
function md(src) {
  const lines = String(src || '').replace(/\r/g, '').split('\n');
  let html = '', list = null, para = [];
  const inline = (s) => esc(s).replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>').replace(/`([^`]+)`/g, '<code>$1</code>');
  const flushPara = () => { if (para.length) { html += '<p>' + inline(para.join(' ')) + '</p>'; para = []; } };
  const flushList = () => { if (list) { html += '</' + list + '>'; list = null; } };
  for (const raw of lines) {
    const line = raw.trim();
    if (!line) { flushPara(); flushList(); continue; }
    let m;
    if ((m = line.match(/^(#{1,3})\s+(.*)$/))) { flushPara(); flushList(); html += '<h' + m[1].length + '>' + inline(m[2]) + '</h' + m[1].length + '>'; continue; }
    if ((m = line.match(/^[-*•]\s+(.*)$/))) { flushPara(); if (list !== 'ul') { flushList(); html += '<ul>'; list = 'ul'; } html += '<li>' + inline(m[1]) + '</li>'; continue; }
    if ((m = line.match(/^\d+[.、]\s*(.*)$/))) { flushPara(); if (list !== 'ol') { flushList(); html += '<ol>'; list = 'ol'; } html += '<li>' + inline(m[1]) + '</li>'; continue; }
    if ((m = line.match(/^\*\*(.+?)\*\*[:：]?$/))) { flushPara(); flushList(); html += '<h3>' + inline(m[1]) + '</h3>'; continue; }
    flushList(); para.push(line);
  }
  flushPara(); flushList();
  return html;
}

// 日报是四段小标题（聊了什么 / 状态 / 值得注意 / 明天可以关心的话题）。拆出来，首页先给一句结论
function parseReport(text) {
  const sec = {}; let cur = '_';
  String(text || '').replace(/\r/g, '').split('\n').forEach((raw) => {
    const line = raw.trim(); if (!line) return;
    const h = line.match(/^\*\*(.+?)\*\*[:：]?$/) || line.match(/^#{1,3}\s+(.+)$/) || line.match(/^\d[.、]\s*(.{2,16}?)[:：]?$/);
    if (h) { cur = h[1].replace(/[:：]$/, ''); sec[cur] = []; return; }
    (sec[cur] = sec[cur] || []).push(line);
  });
  const find = (re) => { const k = Object.keys(sec).find((x) => re.test(x)); return k ? sec[k] : []; };
  const plain = (arr) => arr.map((l) => l.replace(/^[-*•]\s+/, '').replace(/\*\*/g, '')).join(' ').trim();
  const attn = plain(find(/注意/));
  return {
    status: plain(find(/状态/)),
    summary: plain(find(/聊了什么|概括/)),
    attn: /^(无|没有|暂无)[。.]?$/.test(attn) ? '' : attn,
    topics: find(/话题|关心/).map((l) => l.replace(/^[-*•\d.、]+\s*/, '').replace(/\*\*/g, '')).filter(Boolean),
  };
}
function renderReport(el, report, opts) {
  opts = opts || {};
  if (!report || !report.text) { el.innerHTML = '<p class="report-glance muted">' + esc(opts.emptyText || '还没有日报。') + '</p>'; return; }
  const r = parseReport(report.text);
  const glance = r.status || r.summary;
  let html = glance ? '<p class="report-glance">' + esc(glance) + '</p>' : '';
  if (r.attn) html += '<div class="report-attn">' + esc(r.attn) + '</div>';
  if (r.topics.length) html += '<div class="report-topics"><small>下回可以聊</small><ul>' + r.topics.slice(0, 3).map((t) => '<li>' + esc(t) + '</li>').join('') + '</ul></div>';
  if (!glance && !r.attn && !r.topics.length) html = '<div class="md">' + md(report.text) + '</div>';
  else html += '<details class="report-full"' + (opts.open ? ' open' : '') + '><summary>看全文</summary><div class="md mt-8">' + md(report.text) + '</div></details>';
  if (report.generated_at && opts.meta) html += '<div class="report-meta">生成于 ' + esc(report.generated_at) + '</div>';
  el.innerHTML = html;
}

// ---------- 页签 ----------
const TABS = ['home', 'live', 'log', 'story', 'memory', 'ops'];
function go(tab) {
  if (!TABS.includes(tab)) tab = 'home';
  if (tab === 'ops' && ME.role && !isAdmin()) tab = 'home';
  document.body.dataset.tab = tab;
  document.querySelectorAll('section.tab').forEach((s) => s.classList.toggle('active', s.id === 'tab-' + tab));
  document.querySelectorAll('nav a').forEach((a) => {
    const on = a.dataset.tab === tab;
    a.classList.toggle('active', on);
    if (on) a.setAttribute('aria-current', 'page'); else a.removeAttribute('aria-current');
  });
  // 用真实路径而不是 #锚点：下拉刷新、加到主屏幕都会停在当前页
  history.replaceState(null, '', '/' + tab + location.search);
  window.scrollTo(0, 0);
  if (tab !== 'live' && IC.talking) icTalk(false);
  ({ home: loadHome, live: refreshDevice, log: loadLog, memory: loadMemory, story: loadStory,
     ops: () => { loadHealth(); loadConfig(); loadBalance(true); } })[tab]();
}
document.querySelectorAll('nav a[data-tab]').forEach((a) => a.addEventListener('click', (e) => { e.preventDefault(); go(a.dataset.tab); }));
document.addEventListener('click', (e) => {
  const el = e.target.closest('[data-go]');
  if (el) { e.preventDefault(); go(el.getAttribute('data-go')); }
});

// ---------- 余额（只有管理员；正常时不占地方，偏低才出现在顶栏）----------
async function loadBalance(force) {
  if (!isAdmin()) return;
  try {
    const balance = await api('/api/balance' + (force ? '?t=' + Date.now() : ''));
    const ds = balance.deepseek;
    const chip = $('balanceStatus');
    const low = ds && ds.total !== undefined && ds.total < 20;
    chip.hidden = !low;
    if (low) { chip.textContent = '余额 ¥' + Math.floor(ds.total); chip.className = 'chip bal low'; chip.title = 'DeepSeek 余额偏低，点开去维护页'; }
    const rows = [];
    rows.push('<div class="kv"><span>DeepSeek（对话）</span><b class="' + (low ? 'bad' : '') + '">' + (ds && ds.total !== undefined ? '¥' + ds.total.toFixed(2) : esc(ds && (ds.note || ds.error) ? (ds.note || ds.error) : '未配置')) + '</b></div>');
    rows.push('<div class="kv"><span>MiniMax（语音）</span><b><a href="' + esc(balance.minimax.console) + '" target="_blank" rel="noopener noreferrer">去控制台看</a></b></div>');
    const tc = balance.tencent || {};
    const asr = balance.asr || { month: {}, today: {} };
    const n = (u, k) => (u[k] && u[k].n) || 0;
    const mins = (u, k) => Math.round(((u[k] && u[k].sec) || 0) / 60);
    rows.push('<div class="kv"><span>阿里云（识别，主）</span><b>本月约 ¥' + (asr.month.aliyun_cost || 0).toFixed(2) + ' · <a href="' + esc(balance.aliyun.console) + '" target="_blank" rel="noopener noreferrer">余额去控制台看</a></b></div>');
    rows.push('<div class="kv"><span>腾讯云（识别，备用）</span><b class="' + (tc.total !== undefined && tc.total < 10 ? 'bad' : '') + '">' + (tc.total !== undefined ? '¥' + tc.total.toFixed(2) : esc(tc.error ? tc.error : '未配置')) + ' · <a href="' + esc(tc.console) + '" target="_blank" rel="noopener noreferrer">控制台</a></b></div>');
    rows.push('<div class="kv"><span>识别 · 今天</span><b>阿里 ' + n(asr.today, 'aliyun') + ' 句 · 腾讯 ' + n(asr.today, 'tencent') + ' 句' + (n(asr.today, 'all_fail') ? ' · <span class="bad">都失败 ' + n(asr.today, 'all_fail') + ' 次</span>' : '') + '</b></div>');
    rows.push('<div class="kv"><span>识别 · 本月</span><b>阿里 ' + n(asr.month, 'aliyun') + ' 句 / ' + mins(asr.month, 'aliyun') + ' 分钟 · 腾讯 ' + n(asr.month, 'tencent') + ' 句 / ' + mins(asr.month, 'tencent') + ' 分钟' + (n(asr.month, 'aliyun_fail') ? ' · 阿里失败 ' + n(asr.month, 'aliyun_fail') + ' 次' : '') + (n(asr.month, 'all_fail') ? ' · <span class="bad">都失败 ' + n(asr.month, 'all_fail') + ' 次</span>' : '') + '</b></div>');
    if (n(asr.month, 'tts_cosyvoice') || n(asr.month, 'tts_all_fail')) rows.push('<div class="kv"><span>语音合成 · 本月</span><b>MiniMax 失败、改用阿里 CosyVoice ' + n(asr.month, 'tts_cosyvoice') + ' 句' + (n(asr.month, 'tts_all_fail') ? ' · <span class="bad">都失败 ' + n(asr.month, 'tts_all_fail') + ' 次</span>' : '') + '</b></div>');
    rows.push('<div class="hint">阿里云按 ¥' + (asr.price_per_sec || 0.00022) + '/秒估算（百炼的 API Key 查不了账户余额）；腾讯云低于 ¥10、DeepSeek 低于 ¥20 标红。MiniMax 只能到控制台查看。查询时间 ' + esc(balance.checked_at) + '</div>');
    $('balanceBody').innerHTML = rows.join('');
  } catch (e) { $('balanceStatus').hidden = true; }
}

// ---------- 设备状态：顶栏 + 表盘 + 告警 ----------
let DEV = null;   // 最近一次 /api/device/status（已按角色裁剪）
const LOW_BAT = 20, MID_BAT = 40, OFFLINE_ALERT_SEC = 2 * 3600;

function batteryLevel(b) {
  if (!b || b.level == null) return '';
  if (b.charging) return 'ok';
  return b.level <= LOW_BAT ? 'low' : (b.level <= MID_BAT ? 'mid' : 'ok');
}
function setChip(el, cls, text) {
  el.className = 'chip ' + cls;
  el.innerHTML = '<span class="dot"></span><span class="chip-t">' + esc(text) + '</span>';
}
async function refreshDevice() {
  let g;
  try { g = await api('/api/device/status'); }
  catch (e) { g = { bridge_ok: false, error: e.message, devices: [], alerts: [] }; }
  DEV = g;
  const d = g.online ? (g.devices || [])[0] : null;
  const b = d && d.battery;
  const talking = !!(d && d.conversation_active);
  const face = $('deviceFace');
  const state = $('homeDeviceState'), batEl = $('homeBatPct'), verdict = $('homeVerdict'), hint = $('homeDeviceHint');

  // 顶栏：在线是一等公民；电量有就显示，低了变色
  const batChip = $('batStatus');
  if (!g.bridge_ok) setChip($('deviceStatus'), 'warn', '未知');
  else if (!d) setChip($('deviceStatus'), 'bad', '离线');
  else setChip($('deviceStatus'), 'ok', talking ? '对话中' : '在线');
  batChip.hidden = !(b && b.level != null);
  if (!batChip.hidden) {
    $('batStatusText').textContent = (b.charging ? '⚡' : '') + b.level + '%';
    batChip.dataset.level = batteryLevel(b);
    batChip.dataset.charging = b.charging ? '1' : '0';
    batChip.title = (b.charging ? '充电中，' : '设备电量 ') + b.level + '%';
  }

  // 表盘：圆环 = 电量；中间一个词说状态；下面一句话下结论
  const pct = b && b.level != null ? Math.max(0, Math.min(100, b.level)) : 0;
  $('batRing').setAttribute('stroke-dasharray', (d ? pct : 0) + ' 100');
  face.dataset.online = d ? '1' : '0';
  face.dataset.bat = d ? batteryLevel(b) : '';
  face.dataset.talk = talking ? '1' : '0';
  face.dataset.charging = b && b.charging ? '1' : '0';
  if (!g.bridge_ok) {
    state.textContent = '未知'; state.className = 'face-state warn';
    batEl.textContent = '暂时看不到设备';
    verdict.textContent = '服务暂时连不上';
    hint.textContent = isAdmin() ? (g.error || '桥接服务没有回应') : '已经在自动重试，管理员也会收到提醒';
  } else if (!d) {
    state.textContent = '离线'; state.className = 'face-state bad';
    batEl.textContent = g.offline_for != null ? '最后在线 ' + agoText(g.offline_for) : '还没连上过';
    verdict.textContent = '现在联系不上' + ME.label + '那边';
    hint.textContent = '可能没开机、没电，或家里 Wi‑Fi 断了；连上后会自动恢复';
  } else {
    state.textContent = talking ? '对话中' : '在线';
    state.className = 'face-state' + (talking ? '' : ' ok');
    batEl.textContent = b && b.level != null ? (b.charging ? '充电中 ' : '电量 ') + b.level + '%' : '电量 –';
    if (talking) verdict.textContent = '正在陪' + ME.label + '聊天';
    else if (batteryLevel(b) === 'low') verdict.textContent = '电量不多了，设备会提醒' + ME.pronoun + '充电';
    else verdict.textContent = ME.label + '这边一切正常';
    let sub = '已连上 ' + durText(Date.now() / 1000 - d.connected_at);
    if (d.last_activity && !talking) sub += ' · ' + agoText(Date.now() / 1000 - d.last_activity) + '有动静';
    if (d.state && !talking && STATE_CN[d.state] && d.state !== 'listening') sub += ' · ' + STATE_CN[d.state];
    hint.textContent = sub;
  }

  // 首页「对讲」小组件、对讲页的离线提示
  const liveV = $('homeLiveLabel'), liveS = $('homeLiveSub');
  if (!d) { liveV.textContent = '离线'; liveV.className = 'comp-v mute'; liveS.textContent = '连上后才能对讲'; }
  else if (talking) { liveV.textContent = '对话中'; liveV.className = 'comp-v'; liveS.textContent = '这会儿' + ME.pronoun + '正在和 AI 聊'; }
  else { liveV.textContent = '按住说'; liveV.className = 'comp-v'; liveS.textContent = '用你自己的声音'; }
  $('liveOffline').hidden = !(g.bridge_ok && !d) || !!IC.ws;
  $('icSwitch').disabled = !d && !IC.ws;

  renderAlerts(g);
}

// 有事清楚：只在真有事时出现，最急的在最上面
function renderAlerts(g) {
  const box = $('homeAlerts');
  const items = [];
  if (!g.bridge_ok) items.push({ level: 'bad', title: '设备服务连不上，设备现在没法对话', sub: isAdmin() ? '去「维护」看看服务状态，或重启设备桥接' : '管理员会收到提醒' });
  else if (!g.online && g.offline_for != null && g.offline_for >= OFFLINE_ALERT_SEC) {
    items.push({ level: 'bad', title: '设备已经 ' + durText(g.offline_for) + '没连上了', sub: '可以打个电话，请家里人看看设备亮不亮、路由器正不正常' });
  }
  (g.alerts || []).forEach((a) => {
    // 电量低时表盘本身已经变红、写明了，提醒区不再重复说一遍
    if (a.key === 'device_battery' && g.online) return;
    items.push({ level: 'warn', title: a.text, sub: agoText((g.server_time || Date.now() / 1000) - a.ts) });
  });
  box.hidden = !items.length;
  box.innerHTML = items.slice(0, 3).map((a) => '<div class="alert lvl-' + a.level + '"><span class="alert-icon" aria-hidden="true">!</span><div class="alert-body"><b>' + esc(a.title) + '</b>' + (a.sub ? '<small>' + esc(a.sub) + '</small>' : '') + '</div></div>').join('');
}

// ---------- 首页 ----------
let homeReportDate = null;
async function loadHome() {
  refreshDevice();
  try {
    // 「昨天」小组件：昨天聊了几句 + 近 7 天的小柱子（今天的数据还不全，所以看到昨天为止）
    const dates = (await api('/api/history/dates')).dates;
    const counts = {}; dates.forEach((d) => { counts[d.date] = d.count || 0; });
    const days = []; for (let i = 7; i >= 1; i--) days.push(addDays(todayStr(), -i));
    const max = Math.max(1, ...days.map((d) => counts[d] || 0));
    const yc = counts[yesterdayStr()] || 0;
    const v = $('homeTodayCount');
    v.textContent = yc ? yc + ' 句' : '没聊';
    v.className = 'comp-v' + (yc ? '' : ' warn');
    const spark = $('homeSpark'); spark.innerHTML = '';
    days.forEach((d, i) => {
      const c = counts[d] || 0;
      const bar = document.createElement('i');
      bar.className = c ? (i === 6 ? 'hi' : '') : 'zero';
      bar.style.height = (c ? Math.max(14, Math.round(100 * c / max)) : 14) + '%';
      bar.style.animationDelay = (i * 40) + 'ms';
      bar.title = niceDate(d) + '：' + (c ? c + ' 句' : '没聊');
      spark.appendChild(bar);
    });
    const talked = days.filter((d) => counts[d]).length;
    $('homeSparkCap').textContent = talked === 7 ? '近 7 天天天都聊了' : '近 7 天聊了 ' + talked + ' 天';

    const reports = (await api('/api/reports')).reports;
    if (reports.length) {
      homeReportDate = reports[0].date;
      const h = await api('/api/history?date=' + homeReportDate);
      $('homeReportTitle').textContent = niceDate(homeReportDate) + '的日报';
      renderReport($('homeReport'), h.report);
      $('homeReportMore').hidden = false;
    } else {
      homeReportDate = null;
      $('homeReportTitle').textContent = '日报';
      renderReport($('homeReport'), null, { emptyText: '每晚自动整理一份，第二天早上在这里看。' });
      $('homeReportMore').hidden = true;
    }
  } catch (e) { toast('读取失败：' + e.message, true); }
}
$('homeYesterday').addEventListener('click', () => openDay(yesterdayStr()));
$('homeReportMore').addEventListener('click', () => { if (homeReportDate) openDay(homeReportDate); });
function openDay(ds) { curDate = ds; logDatePinned = true; calMonth = new Date(ds + 'T00:00:00'); calMonth.setDate(1); go('log'); }

// ---------- 对讲：默认只听，按住才说（半双工）----------
const IC = { ws: null, ctx: null, mic: null, node: null, src: null, playAt: 0, talking: false, t0: 0, timer: null, lvl: 0, raf: 0 };
const IC_TEXT = {
  off: { badge: '未接通', label: '按住说话', sw: '接通' },
  connecting: { badge: '接通中', label: '接通中', sw: '接通中' },
  on: { badge: '只听', label: '按住说话', sw: '已接通' },
  talk: { badge: '说话中', label: '松开结束', sw: '已接通' },
};
function icSetState(state, hint) {
  const t = IC_TEXT[state];
  $('icStage').dataset.state = state;
  $('icState').textContent = t.badge;
  $('icState').className = 'badge' + (state === 'on' || state === 'talk' ? ' on' : '');
  $('icTalkLabel').textContent = t.label;
  $('icSwitchLabel').textContent = t.sw;
  $('icTalk').disabled = !(state === 'on' || state === 'talk');
  $('icTalk').setAttribute('aria-pressed', state === 'talk' ? 'true' : 'false');
  if (hint) $('icHint').textContent = hint;
}
// 声音电平：她那边一出声、或你按住说话时，表冠跟着声音大小呼吸
function icLevel(f32) {
  let sum = 0; for (let i = 0; i < f32.length; i += 4) sum += f32[i] * f32[i];
  const rms = Math.sqrt(sum / Math.max(1, f32.length / 4));
  IC.lvl = Math.max(IC.lvl, Math.min(1, rms * 6));
}
function icLevelLoop() {
  IC.lvl *= 0.86;
  $('icTalk').style.setProperty('--lvl', IC.lvl.toFixed(3));
  IC.raf = requestAnimationFrame(icLevelLoop);
}
function icTimerStart() {
  IC.t0 = Date.now(); $('icTimer').hidden = false;
  const tick = () => { const s = Math.floor((Date.now() - IC.t0) / 1000); $('icTimer').textContent = pad2(Math.floor(s / 60)) + ':' + pad2(s % 60); };
  tick(); IC.timer = setInterval(tick, 1000);
}
let _wakeLock = null;
async function wakeLockStart() { try { if (navigator.wakeLock) { _wakeLock = await navigator.wakeLock.request('screen'); _wakeLock.addEventListener('release', () => { _wakeLock = null; }); } } catch (e) { /* 浏览器不给就算了 */ } }
async function wakeLockStop() { try { if (_wakeLock) await _wakeLock.release(); } catch (e) { /* 已释放 */ } _wakeLock = null; }

async function icStart() {
  icSetState('connecting', '正在接通设备');
  try { IC.mic = await navigator.mediaDevices.getUserMedia({ audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: true } }); }
  catch (e) { toast('打不开麦克风：' + e.message, true); icStop(); return; }
  IC.ws = new WebSocket(wsUrl('/ws/intercom', 8459));
  IC.ws.binaryType = 'arraybuffer';
  IC.ctx = new (window.AudioContext || window.webkitAudioContext)({ sampleRate: 16000 });
  IC.playAt = 0;
  IC.ws.onopen = () => IC.ws.send(JSON.stringify({ type: 'start' }));
  IC.ws.onmessage = (ev) => {
    if (typeof ev.data === 'string') {
      const m = JSON.parse(ev.data);
      if (m.type === 'started') {
        icSetState('on', '接通了：' + ME.pronoun + '那边的声音会传过来。按住中间的圆说话，松开回到只听。');
        haptic(10); wakeLockStart(); icTimerStart(); $('liveOffline').hidden = true;
        if (!IC.raf) icLevelLoop();
      } else if (m.type === 'error') { toast(m.message, true); icStop(); }
      return;
    }
    // 设备传来的 16k PCM，排队播放
    const pcm = new Int16Array(ev.data);
    const buf = IC.ctx.createBuffer(1, pcm.length, 16000);
    const ch = buf.getChannelData(0); for (let i = 0; i < pcm.length; i++) ch[i] = pcm[i] / 32768;
    icLevel(ch);
    const src = IC.ctx.createBufferSource(); src.buffer = buf; src.connect(IC.ctx.destination);
    const now = IC.ctx.currentTime; if (IC.playAt < now + 0.05) IC.playAt = now + 0.05;
    src.start(IC.playAt); IC.playAt += buf.duration;
  };
  IC.ws.onclose = () => icStop(true);
  IC.ws.onerror = () => toast('对讲连接出错', true);
  // 手机麦克风 → 服务器（只有按住时才发，服务器也只在按住时转给设备）
  IC.src = IC.ctx.createMediaStreamSource(IC.mic);
  IC.node = IC.ctx.createScriptProcessor(2048, 1, 1);
  const g = IC.ctx.createGain(); g.gain.value = 0;
  IC.src.connect(IC.node); IC.node.connect(g); g.connect(IC.ctx.destination);
  IC.node.onaudioprocess = (e) => {
    if (!IC.ws || IC.ws.readyState !== WebSocket.OPEN || !IC.talking) return;
    const f = e.inputBuffer.getChannelData(0); icLevel(f);
    const i16 = new Int16Array(f.length);
    for (let i = 0; i < f.length; i++) { const s = Math.max(-1, Math.min(1, f[i])); i16[i] = s < 0 ? s * 0x8000 : s * 0x7fff; }
    IC.ws.send(i16.buffer);
  };
}
function icStop(fromClose) {
  if (IC.ws && !fromClose) { try { IC.ws.send(JSON.stringify({ type: 'stop' })); } catch (e) { /* 已断 */ } IC.ws.close(); }
  IC.ws = null; IC.talking = false;
  if (IC.node) { IC.node.disconnect(); IC.node = null; }
  if (IC.src) { IC.src.disconnect(); IC.src = null; }
  if (IC.ctx) { IC.ctx.close(); IC.ctx = null; }
  if (IC.mic) { IC.mic.getTracks().forEach((t) => t.stop()); IC.mic = null; }
  clearInterval(IC.timer); $('icTimer').hidden = true;
  cancelAnimationFrame(IC.raf); IC.raf = 0; IC.lvl = 0; $('icTalk').style.setProperty('--lvl', '0');
  wakeLockStop();
  $('icSwitch').checked = false;
  icSetState('off', '先接通（只听）；按住中间的圆，' + ME.pronoun + '才听得到你。电脑上也可以按住空格键。');
  if (DEV) refreshDevice();
}
function icTalk(on) {
  if (!IC.ws || IC.ws.readyState !== WebSocket.OPEN || IC.talking === on) return;
  IC.talking = on;
  IC.ws.send(JSON.stringify({ type: 'talk', on }));
  haptic(on ? 14 : 6);
  // 半双工：按住说话时服务器不把她那边的声音传回来，否则自己的声音绕一圈回来会很炸
  icSetState(on ? 'talk' : 'on', on ? '在说，' + ME.pronoun + '听得到你；这时听不到' + ME.pronoun + '那边，松开就恢复。' : '只听：' + ME.pronoun + '那边的声音会传过来。');
}
$('icSwitch').addEventListener('change', (e) => { if (e.target.checked) icStart(); else icStop(); });
// 按住说话：用 pointer 事件统一鼠标和触屏；手指滑出圆也不会卡在「说话中」
const crown = $('icTalk');
crown.addEventListener('contextmenu', (e) => e.preventDefault());
crown.addEventListener('pointerdown', (e) => { if (crown.disabled) return; e.preventDefault(); try { crown.setPointerCapture(e.pointerId); } catch (err) { /* 老浏览器 */ } icTalk(true); });
['pointerup', 'pointercancel', 'lostpointercapture'].forEach((ev) => crown.addEventListener(ev, () => icTalk(false)));
// 键盘：在对讲页按住空格说话（输入框里不算）
document.addEventListener('keydown', (e) => {
  if (e.code !== 'Space' || e.repeat || document.body.dataset.tab !== 'live' || /INPUT|TEXTAREA|SELECT/.test(e.target.tagName)) return;
  if (IC.ws) { e.preventDefault(); icTalk(true); }
});
document.addEventListener('keyup', (e) => { if (e.code === 'Space' && IC.talking) { e.preventDefault(); icTalk(false); } });

// ---------- 记录：日历 + 当天 ----------
let curDate = todayStr();
let logDatePinned = false;     // 从首页点进来的就别再自动换日子
let calMonth = new Date(); calMonth.setDate(1);
let dateInfo = {};             // date -> 对话条数
async function loadLog() {
  try {
    const dates = (await api('/api/history/dates')).dates;
    dateInfo = {}; dates.forEach((d) => { dateInfo[d.date] = d.count || 0; });
    if (!logDatePinned) {
      // 默认打开最近一天有对话的日子（今天常常还没聊，打开一片空白不体贴）
      const latest = dates.map((d) => d.date).filter((d) => d <= todayStr()).sort().pop();
      curDate = dateInfo[todayStr()] ? todayStr() : (latest || todayStr());
      calMonth = new Date(curDate + 'T00:00:00'); calMonth.setDate(1);
    }
    logDatePinned = false;
    renderCal(); loadDay();
  } catch (e) { toast('读取失败：' + e.message, true); }
}
function renderCal() {
  const y = calMonth.getFullYear(), m = calMonth.getMonth();
  $('calTitle').textContent = y + ' 年 ' + (m + 1) + ' 月';
  const cal = $('cal'); cal.innerHTML = '';
  ['一', '二', '三', '四', '五', '六', '日'].forEach((w) => { const e = document.createElement('div'); e.className = 'wd'; e.textContent = w; cal.appendChild(e); });
  const offset = (new Date(y, m, 1).getDay() + 6) % 7;
  for (let i = 0; i < offset; i++) { const e = document.createElement('div'); e.className = 'd empty'; cal.appendChild(e); }
  const today = todayStr();
  const days = new Date(y, m + 1, 0).getDate();
  for (let d = 1; d <= days; d++) {
    const ds = y + '-' + pad2(m + 1) + '-' + pad2(d);
    const count = dateInfo[ds] || 0;
    const future = ds > today;
    const e = document.createElement(future ? 'div' : 'button');
    e.className = 'd' + (ds === today ? ' today' : '') + (ds === curDate ? ' sel' : '') + (future ? ' future' : '');
    if (count) e.dataset.level = String(count >= 20 ? 3 : count >= 8 ? 2 : 1);
    e.textContent = d;
    if (!future) {
      e.type = 'button';
      e.setAttribute('aria-label', (m + 1) + '月' + d + '日，' + (count ? count + ' 句' : '没有对话'));
      if (ds === curDate) e.setAttribute('aria-pressed', 'true');
      e.addEventListener('click', () => { curDate = ds; renderCal(); loadDay(); });
    }
    cal.appendChild(e);
  }
  const thisMonth = new Date(); thisMonth.setDate(1);
  $('calNext').disabled = calMonth.getFullYear() * 12 + calMonth.getMonth() >= thisMonth.getFullYear() * 12 + thisMonth.getMonth();
}
$('calPrev').addEventListener('click', () => { calMonth.setMonth(calMonth.getMonth() - 1); renderCal(); });
$('calNext').addEventListener('click', () => { calMonth.setMonth(calMonth.getMonth() + 1); renderCal(); });
function stepDay(n) {
  const ds = addDays(curDate, n); if (ds > todayStr()) return;
  curDate = ds; calMonth = new Date(ds + 'T00:00:00'); calMonth.setDate(1); renderCal(); loadDay();
}
$('dayPrev').addEventListener('click', () => stepDay(-1));
$('dayNext').addEventListener('click', () => stepDay(1));

let _playing = null;   // 记录里正在放的那一句
function stopPlaying() { if (_playing) { _playing.audio.pause(); _playing.btn.textContent = '播放'; _playing.bubble.classList.remove('playing'); _playing = null; } }
async function loadDay() {
  const ds = curDate;
  const isToday = ds === todayStr();
  $('dayTitle').textContent = niceDate(ds, true);
  $('dayNext').disabled = isToday;
  let h;
  try { h = await api('/api/history?date=' + ds); } catch (e) { toast('读取失败：' + e.message, true); return; }
  if (ds !== curDate) return;   // 连点时只认最后一次
  const turns = h.turns || [];
  $('daySub').textContent = turns.length ? turns.length + ' 句' : '没有对话';
  // 今天的数据还不全，夜里整理完第二天再看日报；以前的日子：日报在上、原话在下
  $('reportCard').hidden = isToday;
  $('reportTitle').textContent = '日报';
  $('genReport').textContent = h.report ? '重新生成' : '生成日报';
  $('genReport').disabled = !turns.length;
  renderReport($('reportBody'), h.report, { meta: true, emptyText: turns.length ? '这天还没有日报，可以点右上角生成。' : '这天没有对话，也就没有日报。' });
  $('logTitle').textContent = '原话';
  const body = $('logBody'); body.innerHTML = '';
  stopPlaying();
  if (!turns.length) {
    body.innerHTML = emptyState(isToday ? '今天还没聊' : '这天没说话', isToday ? ME.pronoun + '拍一下设备就会开始聊，晚上回来看。' : ME.pronoun + '那天没拍按钮，或者设备不在线。');
    return;
  }
  // 按「一场对话」分组：隔了 20 分钟以上就是新的一场，中间放一条时间分隔线
  let lastT = null;
  turns.forEach((t) => {
    const tm = new Date((t.ts || '').replace(' ', 'T')).getTime();
    if (lastT === null || (tm && tm - lastT > 20 * 60 * 1000)) {
      const sep = document.createElement('div'); sep.className = 'session-sep'; sep.textContent = fmtTime(t.ts); body.appendChild(sep);
    }
    if (tm) lastT = tm;
    // 老人说的在左，孩子（AI 用孩子的声音）说的在右——和聊天软件里「我在右边」一个习惯
    const elder = t.role === 'user';
    const div = document.createElement('div'); div.className = 'bubble ' + (elder ? 'elder ai' : 'child me');
    div.appendChild(document.createTextNode(t.text));
    if (t.asr_raw && t.asr_raw !== t.text) {
      const raw = document.createElement('span'); raw.className = 'rawtxt'; raw.textContent = '原识别：' + t.asr_raw; div.appendChild(raw);
    }
    const ts = document.createElement('span'); ts.className = 'ts'; ts.textContent = (elder ? ME.label : '我') + ' · ' + fmtTime(t.ts); div.appendChild(ts);
    const tools = document.createElement('div'); tools.className = 'tools';
    if (t.asr_raw && t.asr_raw !== t.text) {
      const rb = document.createElement('button'); rb.type = 'button'; rb.textContent = '原识别';
      rb.addEventListener('click', () => div.classList.toggle('showraw'));
      tools.appendChild(rb);
    }
    if (t.audio) {
      const btn = document.createElement('button'); btn.type = 'button'; btn.textContent = '播放';
      btn.addEventListener('click', () => {
        if (_playing && _playing.btn === btn) { stopPlaying(); return; }
        stopPlaying();
        const a = new Audio('/api/' + t.audio); _playing = { audio: a, btn, bubble: div };
        btn.textContent = '停止'; div.classList.add('playing');
        a.onended = stopPlaying;
        a.play().catch(() => { stopPlaying(); toast('放不出来', true); });
      });
      tools.appendChild(btn);
    }
    if (tools.children.length) div.appendChild(tools);
    body.appendChild(div);
  });
}
$('genReport').addEventListener('click', async () => {
  $('genReport').disabled = true; toast('正在整理，大概十几秒');
  try { await post('/api/report/generate', { date: curDate }); await loadDay(); toast('日报已生成'); }
  catch (e) { toast(e.message, true); }
  finally { $('genReport').disabled = false; }
});

// ---------- 故事 ----------
async function loadStory() {
  try {
    const s = await api('/api/story');
    const prog = $('storyProgress'); prog.innerHTML = '';
    Object.entries(s.chapters).forEach(([ch, c]) => {
      const pct = c.total ? Math.round(100 * c.answered / c.total) : 0;
      const row = document.createElement('div'); row.className = 'story-ch';
      row.innerHTML = '<div class="row"><span>' + esc(ch) + '</span><span class="muted">' + c.entries + ' 段 · ' + c.answered + '/' + c.total + '</span></div><div class="story-bar"><i></i></div>';
      prog.appendChild(row);
      requestAnimationFrame(() => { row.querySelector('.story-bar > i').style.width = pct + '%'; });
    });
    $('storyEntryCount').textContent = s.entries + ' 段';
    window._storyText = s.biography || '';
    const bio = $('storyBio'); bio.innerHTML = '';
    if (!s.entry_list.length) bio.innerHTML = emptyState('还没有故事', 'AI 会在聊天里找合适的时机，慢慢问。');
    const byCh = {};
    s.entry_list.forEach((e) => { (byCh[e.chapter] = byCh[e.chapter] || []).push(e); });
    Object.keys(s.chapters).forEach((ch) => {
      const list = byCh[ch]; if (!list) return;
      const h = document.createElement('h3'); h.className = 'story-chapter-title'; h.textContent = ch; bio.appendChild(h);
      list.forEach((e) => {
        const text = (e.question || '') + '（' + e.date + '）\n' + (e.quotes || []).map((q, i) => '“' + q + '”' + ((e.audio || [])[i] ? '  〔录音：' + e.audio[i] + '〕' : '')).join('\n') + (e.summary ? '\n——' + e.summary : '');
        const card = document.createElement('div'); card.className = 'fact';
        const hasAudio = (e.audio || []).some(Boolean);
        card.innerHTML = '<div class="fact-meta head"><b>' + esc(e.question || '') + '</b><span><span class="muted">' + esc(e.date) + '</span><button type="button" class="link" data-act="copy">复制</button></span></div>' +
          (hasAudio ? '<div class="playbar"><button type="button" data-act="play">播放</button><button type="button" data-act="stop">停止</button><span class="muted" data-role="pos"></span></div>' : '') +
          '<div class="md"><blockquote class="story-quotes">' + (e.quotes || []).map((q, i) => '<span class="sq' + ((e.audio || [])[i] ? ' can-play' : '') + '" data-i="' + i + '">' + esc(q) + '</span>').join('') + '</blockquote>' +
          (e.summary ? '<div class="story-summary">' + esc(e.summary) + '</div>' : '') + '</div>';
        card.querySelector('[data-act=copy]').addEventListener('click', (ev) => copyText(text, ev.currentTarget));
        if (hasAudio) setupStoryPlayer(card, e);
        bio.appendChild(card);
      });
    });
    const qs = s.questions.filter((q) => !q.answered && !q.shelved);
    $('storyQCount').textContent = qs.length + ' 个待问';
    const box = $('storyQList'); box.innerHTML = '';
    qs.sort((a, b) => (b.pinned ? 1 : 0) - (a.pinned ? 1 : 0) || (a.source === 'family' ? -1 : 1) - (b.source === 'family' ? -1 : 1));
    qs.slice(0, 60).forEach((q) => {
      const div = document.createElement('div'); div.className = 'fact';
      const tag = q.pinned ? '下次问 · ' : (q.source === 'family' ? '家人出的 · ' : (q.source === 'follow_up' ? '追问 · ' : ''));
      div.innerHTML = '<div class="t">' + esc(q.text) + '</div><div class="fact-meta"><span class="muted">' + esc(tag) + esc(q.chapter) + (q.asked_count ? ' · 问过 ' + q.asked_count + ' 次' : '') + '</span><span><button type="button" class="link" data-act="pin">下次问</button><button type="button" class="link" data-act="shelve">不问了</button></span></div>';
      div.querySelector('[data-act=pin]').addEventListener('click', async () => { try { await post('/api/story/question/update', { id: q.id, pinned: true }); toast('下次聊天就问这个'); loadStory(); } catch (err) { toast(err.message, true); } });
      div.querySelector('[data-act=shelve]').addEventListener('click', async () => { try { await post('/api/story/question/update', { id: q.id, shelved: true }); loadStory(); } catch (err) { toast(err.message, true); } });
      box.appendChild(div);
    });
  } catch (e) { toast('读取失败：' + e.message, true); }
}
// 每段故事一个播放器：顺着原话一句句播，当前句反白；点句子从那句开始；暂停/继续；停止回到开头
let _storyPlayer = null;
function setupStoryPlayer(card, e) {
  const spans = Array.from(card.querySelectorAll('.sq'));
  const playBtn = card.querySelector('[data-act=play]'), stopBtn = card.querySelector('[data-act=stop]'), pos = card.querySelector('[data-role=pos]');
  const st = { audio: null, paused: false };
  const nextWithAudio = (from) => { for (let i = from; i < spans.length; i++) if ((e.audio || [])[i]) return i; return -1; };
  const highlight = (i) => { spans.forEach((s, k) => s.classList.toggle('now', k === i)); pos.textContent = i >= 0 ? (i + 1) + ' / ' + spans.length : ''; };
  const playFrom = (i) => {
    i = nextWithAudio(i);
    if (i < 0) { st.stop(); return; }
    if (_storyPlayer && _storyPlayer !== st) _storyPlayer.stop();
    _storyPlayer = st;
    if (st.audio) { st.audio.onended = null; st.audio.pause(); }
    st.paused = false; highlight(i); playBtn.textContent = '暂停';
    const a = new Audio('/api/' + e.audio[i]); st.audio = a;
    a.onended = () => playFrom(i + 1);
    a.play().catch(() => { toast('放不出来', true); st.stop(); });
  };
  st.stop = () => { if (st.audio) { st.audio.onended = null; st.audio.pause(); } st.audio = null; st.paused = false; highlight(-1); playBtn.textContent = '播放'; };
  playBtn.addEventListener('click', () => {
    if (st.audio && !st.paused) { st.audio.pause(); st.paused = true; playBtn.textContent = '继续'; return; }
    if (st.audio && st.paused) { st.paused = false; playBtn.textContent = '暂停'; st.audio.play(); return; }
    playFrom(0);
  });
  stopBtn.addEventListener('click', () => st.stop());
  spans.forEach((s, i) => { if (s.classList.contains('can-play')) s.addEventListener('click', () => playFrom(i)); });
}
async function copyText(text, btn) {
  try { await navigator.clipboard.writeText(text); }
  catch (e) { const ta = document.createElement('textarea'); ta.value = text; document.body.appendChild(ta); ta.select(); document.execCommand('copy'); ta.remove(); }
  if (btn) { const old = btn.textContent; btn.textContent = '已复制'; setTimeout(() => { btn.textContent = old; }, 1500); }
  toast('已复制');
}
$('storyCopyAll').addEventListener('click', (e) => copyText(window._storyText || '', e.currentTarget));
$('storyAddQ').addEventListener('click', async () => {
  const text = $('storyNewQ').value.trim(); if (!text) return;
  try { const r = await post('/api/story/question', { text, pin: true }); if (!r.ok) { toast(r.error, true); return; } $('storyNewQ').value = ''; toast('下次聊天就问这个'); loadStory(); }
  catch (e) { toast(e.message, true); }
});
$('storyNewQ').addEventListener('keydown', (e) => { if (e.key === 'Enter') $('storyAddQ').click(); });
$('storyRebuild').addEventListener('click', async () => {
  $('storyRebuild').disabled = true; toast('正在整理今天的对话，十几秒');
  try { const r = await post('/api/story/rebuild', {}); toast(r.added ? '新增 ' + r.added + ' 段' : '今天没有新的往事'); loadStory(); }
  catch (e) { toast(e.message, true); }
  finally { $('storyRebuild').disabled = false; }
});

// ---------- 记忆 ----------
let facts = [];
async function loadMemory() {
  try { facts = (await api('/api/facts')).facts; renderFacts(); }
  catch (e) { $('facts').innerHTML = emptyState('读不到', e.message); }
  if (isAdmin()) {
    loadTypo();
    try { const c = await api('/api/admin/config'); $('personaText').value = c.persona; } catch (e) { /* 人设读不到不影响别的 */ }
  }
}
function renderFacts() {
  const box = $('facts'); box.innerHTML = '';
  $('factsCount').textContent = facts.length + ' 条';
  if (!facts.length) { box.innerHTML = emptyState('还没有记住的事', '聊几次，它会自己记。'); return; }
  facts.slice().reverse().forEach((fact, ri) => {
    const i = facts.length - 1 - ri;
    const div = document.createElement('div'); div.className = 'fact';
    const t = document.createElement('div'); t.className = 't'; t.textContent = fact.text;
    const ts = document.createElement('div'); ts.className = 'ts';
    const when = document.createElement('span'); when.textContent = fact.added_at + (fact.edited_at ? ' · 改于 ' + fact.edited_at : '');
    const ops = document.createElement('span'); ops.className = 'ops';
    const edit = document.createElement('button'); edit.type = 'button'; edit.className = 'subtle'; edit.textContent = '修改';
    const del = document.createElement('button'); del.type = 'button'; del.className = 'subtle danger-text'; del.textContent = '删除';
    ops.appendChild(edit); ops.appendChild(del); ts.appendChild(when); ts.appendChild(ops);
    div.appendChild(t); div.appendChild(ts);
    edit.addEventListener('click', () => {
      if (div.querySelector('textarea')) return;
      const ta = document.createElement('textarea'); ta.value = fact.text;
      const row = document.createElement('div'); row.className = 'btnrow mt-8';
      const save = document.createElement('button'); save.type = 'button'; save.className = 'small'; save.textContent = '保存';
      const cancel = document.createElement('button'); cancel.type = 'button'; cancel.className = 'small secondary'; cancel.textContent = '取消';
      row.appendChild(save); row.appendChild(cancel); div.appendChild(ta); div.appendChild(row); ta.focus();
      cancel.addEventListener('click', () => { ta.remove(); row.remove(); });
      save.addEventListener('click', async () => { const v = ta.value.trim(); if (!v) return; try { facts = (await post('/api/admin/facts/update', { index: i, text: v })).facts; renderFacts(); toast('已修改'); } catch (e) { toast(e.message, true); } });
    });
    del.addEventListener('click', async () => { if (!confirm('删掉这条？\n' + fact.text)) return; try { facts = (await post('/api/admin/facts/delete', { index: i })).facts; renderFacts(); toast('已删除'); } catch (e) { toast(e.message, true); } });
    box.appendChild(div);
  });
}
$('addFact').addEventListener('click', async () => {
  const text = $('newFact').value.trim(); if (!text) return;
  try { facts = (await post('/api/admin/facts', { text })).facts; $('newFact').value = ''; renderFacts(); toast('已添加'); } catch (e) { toast(e.message, true); }
});
$('newFact').addEventListener('keydown', (e) => { if (e.key === 'Enter') $('addFact').click(); });
$('savePersona').addEventListener('click', async () => {
  try { await post('/api/admin/persona', { content: $('personaText').value }); toast('已保存，下一轮对话生效'); }
  catch (e) { toast('保存失败：' + e.message, true); }
});

// ---------- 错字本（只有管理员）----------
let typo = null;
const TYPO_KIND = { wrong: '直接改', maybe: '看上下文', rejected: '不算错' };
async function loadTypo() {
  try { typo = await api('/api/typo'); } catch (e) { $('typoTerms').innerHTML = emptyState('读不到', e.message); return; }
  renderTypo();
  if (typo.running) setTimeout(loadTypo, 5000);
}
function fixedHtml(raw, fixes) {
  let out = '', pos = 0;
  (fixes || []).slice().sort((a, b) => a.s - b.s).forEach((f) => {
    if (f.s < pos || raw.substr(f.s, f.from.length) !== f.from) return;
    out += esc(raw.slice(pos, f.s)) + '<span class="fx" title="原识别：' + esc(f.from) + (f.by === 'llm' ? '（DeepSeek 判的）' : '') + '">' + esc(f.to) + '</span>';
    pos = f.s + f.from.length;
  });
  return out + esc(raw.slice(pos));
}
function renderTypo() {
  const lr = typo.last_run;
  $('typoCount').textContent = typo.terms.length + ' 个词';
  $('typoStatus').innerHTML = esc(ME.label) + '的话里已经纠正了 <b>' + typo.lines_fixed + '</b> 句。' +
    (typo.running ? ' <span class="warn">正在跑……</span>' : '') +
    (lr ? ' 上次运行 ' + esc(lr.started || '') + '，DeepSeek 判了 ' + (lr.judged || 0) + ' 句' + (lr.pending ? '，还有 ' + lr.pending + ' 句等着陆续判' : '') + '。' : '');
  const box = $('typoTerms'); box.innerHTML = '';
  typo.terms.forEach((t) => {
    const div = document.createElement('div'); div.className = 'typo-term';
    div.innerHTML = '<div class="h"><b>' + esc(t.right) + '</b><span class="note">' + esc(t.note || '') + '</span><span class="badge">改了 ' + t.fixed + ' 处</span></div>';
    const chips = document.createElement('div');
    t.variants.forEach((v) => {
      const b = document.createElement('button'); b.type = 'button'; b.className = 'tchip ' + v.kind;
      const judged = v.judged_yes + v.judged_no;
      b.innerHTML = esc(v.text) + '<span class="n">' + (v.kind === 'maybe' ? (judged ? '判了 ' + judged + ' 处，' + v.judged_yes + ' 处是错的' : '等着判') : v.kind === 'wrong' ? v.fixed + ' 处' : '') + '</span>';
      b.addEventListener('click', () => typoMenu(div, t, v));
      chips.appendChild(b);
    });
    const add = document.createElement('button'); add.type = 'button'; add.className = 'tchip'; add.textContent = '＋ 加错写';
    add.addEventListener('click', async () => {
      const w = prompt('「' + t.right + '」还会被听成什么？（用空格隔开）'); if (!w || !w.trim()) return;
      try { await post('/api/typo/term', { right: t.right, wrong: w.split(/[\s,，、]+/) }); toast('已加，正在回改'); loadTypo(); } catch (e) { toast(e.message, true); }
    });
    chips.appendChild(add);
    const del = document.createElement('button'); del.type = 'button'; del.className = 'tchip rejected plain'; del.textContent = '删掉这个词';
    del.addEventListener('click', async () => {
      if (!confirm('删掉「' + t.right + '」？它改过的地方都会恢复成识别原文。')) return;
      try { await post('/api/typo/term/delete', { tid: t.id }); toast('已删，正在恢复'); setTimeout(loadTypo, 1500); } catch (e) { toast(e.message, true); }
    });
    chips.appendChild(del);
    div.appendChild(chips);
    box.appendChild(div);
  });
  const rb = $('typoRecent'); rb.innerHTML = '';
  if (!typo.recent.length) rb.innerHTML = emptyState('还没有改过', '');
  typo.recent.forEach((r) => {
    const d = document.createElement('div'); d.className = 'typo-line';
    d.innerHTML = '<div>' + fixedHtml(r.raw || r.text, r.fixes) + '</div><div class="raw">' + esc((r.ts || '').slice(5, 16)) + ' 原识别：' + esc(r.raw || '') + '</div>';
    const ops = document.createElement('div'); ops.className = 'ops';
    (r.fixes || []).forEach((f) => {
      const b = document.createElement('button'); b.type = 'button'; b.className = 'subtle'; b.textContent = '撤销「' + f.from + '→' + f.to + '」';
      b.addEventListener('click', async () => { try { await post('/api/typo/veto', { key: r.key, s: f.s, from: f.from }); toast('这一处已撤销'); setTimeout(loadTypo, 1500); } catch (e) { toast(e.message, true); } });
      ops.appendChild(b);
    });
    d.appendChild(ops); rb.appendChild(d);
  });
}
function typoMenu(div, t, v) {
  const old = div.querySelector('.typo-menu'); if (old) { const same = old.dataset.v === v.text; old.remove(); if (same) return; }
  const m = document.createElement('div'); m.className = 'typo-menu'; m.dataset.v = v.text;
  m.innerHTML = '<div class="t">「' + esc(v.text) + '」现在是：' + TYPO_KIND[v.kind] + '。改成：</div>';
  const row = document.createElement('div'); row.className = 'btnrow';
  [['wrong', '直接改成「' + t.right + '」'], ['maybe', '看上下文'], ['rejected', '不算错（撤销已改的）']].forEach(([k, label]) => {
    if (k === v.kind) return;
    const b = document.createElement('button'); b.type = 'button'; b.className = 'small secondary'; b.textContent = label;
    b.addEventListener('click', async () => { try { await post('/api/typo/variant', { tid: t.id, variant: v.text, kind: k }); toast('已改，正在重算'); setTimeout(loadTypo, 1500); } catch (e) { toast(e.message, true); } });
    row.appendChild(b);
  });
  m.appendChild(row); div.appendChild(m);
}
$('typoAdd').addEventListener('click', async () => {
  const right = $('typoRight').value.trim(); if (!right) return;
  const wrong = $('typoWrong').value.trim().split(/[\s,，、]+/).filter(Boolean);
  try {
    await post('/api/typo/term', { right, wrong, note: $('typoNote').value.trim() });
    $('typoRight').value = ''; $('typoWrong').value = ''; $('typoNote').value = '';
    toast('已加进错字本，正在回改以前的内容'); setTimeout(loadTypo, 1500);
  } catch (e) { toast(e.message, true); }
});
$('typoRun').addEventListener('click', async () => { try { await post('/api/typo/run', {}); toast('开始跑了'); setTimeout(loadTypo, 1500); } catch (e) { toast(e.message, true); } });

// ---------- 维护（只有管理员）----------
const NAME = { 'xiaozhi-bridge.service': '设备桥接服务', 'companion-realtime.service': '网页通话服务', 'companion.service': '管理页面服务' };
async function loadHealth() {
  $('health').textContent = '检测中';
  try {
    const h = await api('/api/admin/health');
    const rows = [];
    for (const [u, st] of Object.entries(h.services)) {
      const ok = st === 'active' || st === 'active (local)';
      const text = ok ? '运行中' : (st === 'inactive (local)' ? '未启动' : st);
      rows.push('<div class="kv"><span>' + esc(NAME[u] || u) + '</span><b class="' + (ok ? 'ok' : 'bad') + '">' + esc(text) + '</b></div>');
    }
    rows.push('<div class="kv"><span>公网入口</span><b class="' + (h.public_entry.ok ? 'ok' : 'bad') + '">' + (h.public_entry.ok ? '正常 · ' + h.public_entry.ms + ' ms' : '不通') + '</b></div>');
    rows.push('<div class="kv"><span>桥接控制口</span><b class="' + (h.bridge.ok ? 'ok' : 'bad') + '">' + (h.bridge.ok ? '正常' : esc(h.bridge.error || '异常')) + '</b></div>');
    rows.push('<div class="kv"><span>设备</span><b class="' + (h.devices.length ? 'ok' : 'warn') + '">' + (h.devices.length ? '在线 ' + h.devices.length + ' 台' : '离线') + '</b></div>');
    rows.push('<div class="hint">检测时间 ' + esc(h.server_time) + '</div>');
    $('health').innerHTML = rows.join('');
    const dl = $('deviceList');
    if (!h.devices.length) dl.innerHTML = emptyState('没有在线设备', '设备连上 Wi‑Fi 就会出现在这里。');
    else dl.innerHTML = h.devices.map((d) => '<div class="kv"><span>' + esc(d.device_id) + '</span><b>' + esc(STATE_CN[d.state] || d.state) + ' · ' + (d.listen_mode === 'auto' ? '连续对话' : '按住说话') + ' · ' + (d.tx_rate / 1000) + ' kHz</b></div>').join('');
    const d0 = h.devices[0];
    if (d0 && d0.volume != null && !window._volDragging) { $('volVal').textContent = d0.volume; $('volSlider').value = d0.volume; }
    const st = h.staged_firmware || {}; const keys = Object.keys(st);
    if (keys.length) $('otaStatus').innerHTML = keys.map((k) => '已准备：' + esc(k) + ' → ' + esc(st[k].version) + '（' + esc(st[k].staged_at) + '）').join('<br>') + '<br>设备下次开机自动升级；点「重启设备」立刻触发。';
  } catch (e) { $('health').innerHTML = '<span class="bad">检测失败：' + esc(e.message) + '</span>'; }
}
$('volSlider').addEventListener('input', () => { window._volDragging = true; $('volVal').textContent = $('volSlider').value; });
$('volSlider').addEventListener('change', () => { window._volDragging = false; });
$('volSet').addEventListener('click', async () => {
  $('volSet').disabled = true;
  try { const r = await post('/api/device/volume', { volume: Number($('volSlider').value) }); $('volVal').textContent = r.volume; toast('音量已设为 ' + r.volume); }
  catch (e) { toast(e.message, true); }
  finally { $('volSet').disabled = false; }
});
$('rebootDevice').addEventListener('click', async () => { if (!confirm('确定远程重启设备？接下来几分钟可能没法对话。')) return; try { await post('/api/device/reboot'); toast('已发送重启命令'); } catch (e) { toast(e.message, true); } });
$('endConv').addEventListener('click', async () => { try { await post('/api/device/end'); toast('已结束对话'); } catch (e) { toast(e.message, true); } });
async function restartSvc(services) {
  if (!confirm('重启：' + services.map((s) => NAME[s] || s).join('、') + '？')) return;
  try { await post('/api/admin/restart', { services }); toast('正在重启'); setTimeout(loadHealth, 4000); }
  catch (e) { toast(e.message, true); }
}
document.querySelectorAll('[data-restart]').forEach((el) => el.addEventListener('click', () => {
  const services = (el.getAttribute('data-restart') || '').split(',').map((s) => s.trim()).filter(Boolean);
  if (services.length) restartSvc(services);
}));
$('otaStage').addEventListener('click', async () => {
  const f = $('otaFile').files[0]; const board = $('otaBoard').value.trim(); const version = $('otaVersion').value.trim();
  if (!f || !version) { toast('请选择固件文件并填版本号', true); return; }
  $('otaStage').disabled = true; toast('上传中');
  try {
    const resp = await fetch('/api/admin/ota/stage?board=' + encodeURIComponent(board) + '&version=' + encodeURIComponent(version), { method: 'POST', body: f });
    const data = await resp.json(); if (!resp.ok) throw new Error(data.error || 'HTTP ' + resp.status);
    toast('固件已准备好'); loadHealth();
  } catch (e) { toast(e.message, true); }
  finally { $('otaStage').disabled = false; }
});
let cfg = null;
async function loadConfig() {
  if (!cfg) cfg = await api('/api/admin/config');
  const box = $('fields'); box.innerHTML = '';
  for (const f of cfg.fields) {
    const div = document.createElement('div'); div.className = 'field';
    const label = document.createElement('label'); label.textContent = f.label;
    const badge = document.createElement('span'); badge.className = 'badge' + (f.configured ? ' on' : ''); badge.textContent = f.configured ? '已设置' : '未设置'; label.appendChild(badge);
    div.appendChild(label);
    let ctl;
    const opts = (list) => '<option value="">不修改（当前：' + esc(f.preview || '') + '）</option>' + list.map((v) => '<option value="' + esc(v) + '">' + esc(v) + '</option>').join('');
    if (f.key === 'MINIMAX_VOICE_ID') { ctl = document.createElement('select'); ctl.innerHTML = opts(cfg.known_voices); }
    else if (f.key === 'MINIMAX_TTS_MODEL') { ctl = document.createElement('select'); ctl.innerHTML = opts(['speech-2.6-hd', 'speech-2.6-turbo']); }
    else {
      ctl = document.createElement('input'); ctl.type = f.sensitive ? 'password' : 'text'; ctl.autocomplete = 'off';
      ctl.placeholder = f.sensitive ? (f.configured ? '已设置 ' + (f.preview || '') + '，留空不改' : '未设置') : (f.preview || '留空不改');
    }
    ctl.dataset.key = f.key; div.appendChild(ctl);
    box.appendChild(div);
  }
}
$('saveConfig').addEventListener('click', async () => {
  const fields = {}; document.querySelectorAll('#fields [data-key]').forEach((el) => { if (el.value) fields[el.dataset.key] = el.value; });
  try { const d = await post('/api/admin/config', { fields }); toast(d.changed ? (d.restarting ? '已保存，服务正在重启' : '已保存，请手动重启服务') : '没有改动'); cfg = null; setTimeout(loadConfig, d.restarting ? 2500 : 0); }
  catch (e) { toast('保存失败：' + e.message, true); }
});

// ---------- 测试对话（维护页里，管理员用）----------
const chatLog = $('chatLog'), chatText = $('chatText'), chatSend = $('chatSend'), chatMic = $('chatMic'), callBtn = $('callBtn'), chatStatus = $('chatStatus');
const chatMsgs = $('chatMsgs');
function addBubble(text, who) {
  const e = $('chatEmpty'); if (e) e.remove();
  const div = document.createElement('div'); div.className = 'bubble ' + who; div.textContent = text; chatMsgs.appendChild(div);
  chatLog.scrollTop = chatLog.scrollHeight; return div;
}
new MutationObserver(() => { chatLog.scrollTop = chatLog.scrollHeight; }).observe(chatMsgs, { childList: true, characterData: true, subtree: true });
$('testPanel').addEventListener('toggle', () => { if ($('testPanel').open) setTimeout(() => { chatLog.scrollTop = chatLog.scrollHeight; }, 50); });
function syncChatButtons() { const has = chatText.value.trim().length > 0; chatSend.hidden = !has; chatMic.hidden = has; }
chatText.addEventListener('input', syncChatButtons);
function playSequence(list) { let i = 0; (function next() { if (i >= list.length) return; const a = new Audio(list[i++]); a.onended = next; a.play(); })(); }
function attachReplay(bubble, audios) {
  if (!audios.length) return; let btn = bubble.querySelector('.replay');
  if (!btn) { btn = document.createElement('button'); btn.type = 'button'; btn.className = 'replay'; btn.textContent = '重听'; bubble.appendChild(btn); }
  btn.onclick = (e) => { e.stopPropagation(); playSequence(audios); };
}
async function sendText(text) {
  if (!text) return;
  addBubble(text, 'me'); chatText.value = ''; syncChatButtons(); chatSend.disabled = true; chatStatus.textContent = '想一下';
  const aiBubble = addBubble('', 'ai'); const audioQueue = [], turnAudios = []; let playing = false;
  function playNext() { if (playing || !audioQueue.length) return; playing = true; const a = new Audio(audioQueue.shift()); a.onended = () => { playing = false; playNext(); }; a.play().catch(() => { playing = false; playNext(); }); }
  try {
    const resp = await fetch('/api/chat_stream', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ text }) });
    if (!resp.ok || !resp.body) throw new Error('出错了');
    const reader = resp.body.getReader(); const decoder = new TextDecoder(); let buf = '', gotAny = false;
    for (;;) {
      const { done, value } = await reader.read(); if (done) break;
      buf += decoder.decode(value, { stream: true }); let idx;
      while ((idx = buf.indexOf('\n')) >= 0) {
        const line = buf.slice(0, idx); buf = buf.slice(idx + 1); if (!line.trim()) continue;
        const evt = JSON.parse(line);
        if (evt.type === 'audio_chunk') { gotAny = true; aiBubble.textContent += evt.text; const src = 'data:' + evt.audio_mime + ';base64,' + evt.audio_base64; audioQueue.push(src); turnAudios.push(src); playNext(); chatStatus.textContent = ''; }
        else if (evt.type === 'error') chatStatus.textContent = '出错了：' + evt.error;
      }
    }
    if (!gotAny) chatStatus.textContent = '没说出话来，再试一次'; else attachReplay(aiBubble, turnAudios);
  } catch (err) { chatStatus.textContent = '出错了：' + err.message; }
  finally { chatSend.disabled = false; }
}
chatSend.addEventListener('click', () => sendText(chatText.value.trim()));
chatText.addEventListener('keydown', (e) => { if (e.key === 'Enter') sendText(chatText.value.trim()); });
let mediaRecorder = null, chunks = [], recording = false;
async function startRecording() {
  try {
    const stream = await navigator.mediaDevices.getUserMedia({ audio: true }); chunks = [];
    mediaRecorder = new MediaRecorder(stream); mediaRecorder.ondataavailable = (e) => chunks.push(e.data);
    mediaRecorder.onstop = async () => {
      stream.getTracks().forEach((t) => t.stop()); const blob = new Blob(chunks, { type: mediaRecorder.mimeType }); chatStatus.textContent = '识别中';
      try { const resp = await fetch('/api/asr', { method: 'POST', body: blob }); const data = await resp.json(); if (!resp.ok) throw new Error(data.error || '识别出错'); if (data.text) sendText(data.text); else chatStatus.textContent = '没听清，再说一次'; }
      catch (err) { chatStatus.textContent = '识别出错：' + err.message; }
    };
    mediaRecorder.start(); recording = true; chatMic.textContent = '松开发送'; chatStatus.textContent = '录音中';
  } catch (err) { chatStatus.textContent = '打不开麦克风：' + err.message; }
}
function stopRecording() { if (mediaRecorder && recording) { mediaRecorder.stop(); recording = false; chatMic.textContent = '按住说话'; } }
if (navigator.mediaDevices && window.MediaRecorder) {
  chatMic.addEventListener('pointerdown', (e) => { e.preventDefault(); startRecording(); });
  ['pointerup', 'pointerleave', 'pointercancel'].forEach((ev) => chatMic.addEventListener(ev, stopRecording));
} else { chatMic.disabled = true; }

let callActive = false, callWs = null, callAudioCtx = null, callMicStream = null, callScriptNode = null, callSourceNode = null, leftoverInt16 = new Int16Array(0);
const FRAME_SAMPLES = 512; let callAudioQueue = [], callPlaying = false, currentPlayingAudio = null;
function stopAllPlayback() { callAudioQueue = []; if (currentPlayingAudio) { currentPlayingAudio.pause(); currentPlayingAudio.src = ''; currentPlayingAudio = null; } callPlaying = false; }
function playNextCallAudio() { if (callPlaying || !callAudioQueue.length) return; callPlaying = true; currentPlayingAudio = new Audio(callAudioQueue.shift()); currentPlayingAudio.onended = () => { callPlaying = false; currentPlayingAudio = null; playNextCallAudio(); }; currentPlayingAudio.play().catch(() => { callPlaying = false; currentPlayingAudio = null; playNextCallAudio(); }); }
function downsampleTo16k(arr, rate) { if (rate === 16000) return arr; const ratio = rate / 16000, n = Math.floor(arr.length / ratio), out = new Float32Array(n); for (let i = 0; i < n; i++) out[i] = arr[Math.floor(i * ratio)]; return out; }
function sendMicChunk(arr, rate) {
  const down = downsampleTo16k(arr, rate); const int16 = new Int16Array(down.length);
  for (let i = 0; i < down.length; i++) { const s = Math.max(-1, Math.min(1, down[i])); int16[i] = s < 0 ? s * 0x8000 : s * 0x7fff; }
  const merged = new Int16Array(leftoverInt16.length + int16.length); merged.set(leftoverInt16, 0); merged.set(int16, leftoverInt16.length);
  let off = 0; while (off + FRAME_SAMPLES <= merged.length) { const fr = merged.subarray(off, off + FRAME_SAMPLES); if (callWs && callWs.readyState === WebSocket.OPEN) callWs.send(fr.buffer.slice(fr.byteOffset, fr.byteOffset + fr.byteLength)); off += FRAME_SAMPLES; }
  leftoverInt16 = merged.slice(off);
}
async function startCall() {
  try { callMicStream = await navigator.mediaDevices.getUserMedia({ audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: true } }); }
  catch (err) { chatStatus.textContent = '打不开麦克风：' + err.message; return; }
  callWs = new WebSocket(wsUrl('/ws/call', 8458));
  let aiBubble = null, turnAudios = [];
  callWs.onopen = () => { chatStatus.textContent = '通话中，随时说话'; };
  callWs.onclose = () => { if (callActive) endCall(); };
  callWs.onerror = () => { chatStatus.textContent = '通话连接出错'; };
  callWs.onmessage = (ev) => {
    const evt = JSON.parse(ev.data);
    if (evt.type === 'interrupt') { stopAllPlayback(); chatStatus.textContent = '在听'; aiBubble = null; turnAudios = []; }
    else if (evt.type === 'asr_text') addBubble(evt.text, 'me');
    else if (evt.type === 'audio_chunk') { if (!aiBubble) { aiBubble = addBubble('', 'ai'); turnAudios = []; } aiBubble.textContent += evt.text; const src = 'data:' + evt.audio_mime + ';base64,' + evt.audio_base64; callAudioQueue.push(src); turnAudios.push(src); playNextCallAudio(); }
    else if (evt.type === 'done') { if (aiBubble) attachReplay(aiBubble, turnAudios); aiBubble = null; chatStatus.textContent = '通话中，随时说话'; }
    else if (evt.type === 'error') chatStatus.textContent = '出错了：' + evt.error;
  };
  callAudioCtx = new (window.AudioContext || window.webkitAudioContext)(); callSourceNode = callAudioCtx.createMediaStreamSource(callMicStream);
  callScriptNode = callAudioCtx.createScriptProcessor(4096, 1, 1); const g = callAudioCtx.createGain(); g.gain.value = 0;
  callSourceNode.connect(callScriptNode); callScriptNode.connect(g); g.connect(callAudioCtx.destination);
  callScriptNode.onaudioprocess = (e) => sendMicChunk(new Float32Array(e.inputBuffer.getChannelData(0)), callAudioCtx.sampleRate);
  stopRecording();  // 万一按住说话还没松开，先收掉，避免和通话麦克风同时发
  callActive = true; callBtn.textContent = '通话中 · 点这里挂断'; callBtn.classList.add('active');
  $('chatBar').classList.add('calling');
}
function endCall() {
  callActive = false; callBtn.textContent = '通话'; callBtn.classList.remove('active'); chatStatus.textContent = ''; stopAllPlayback();
  $('chatBar').classList.remove('calling'); syncChatButtons();
  if (callScriptNode) { callScriptNode.disconnect(); callScriptNode = null; } if (callSourceNode) { callSourceNode.disconnect(); callSourceNode = null; }
  if (callAudioCtx) { callAudioCtx.close(); callAudioCtx = null; } if (callMicStream) { callMicStream.getTracks().forEach((t) => t.stop()); callMicStream = null; }
  if (callWs) { callWs.close(); callWs = null; } leftoverInt16 = new Int16Array(0);
}
callBtn.addEventListener('click', () => { if (callActive) endCall(); else startCall(); });

// ---------- 启动 ----------
// WebSocket 地址：正式部署（nginx 同域反代）走路径；本机开发（localhost / tailscale serve 按端口）走端口
function wsUrl(path, devPort) {
  const proto = location.protocol === 'https:' ? 'wss' : 'ws';
  const dev = location.hostname.endsWith('.ts.net') || location.hostname === 'localhost' || location.hostname === '127.0.0.1';
  return dev ? proto + '://' + location.hostname + ':' + devPort + '/' : proto + '://' + location.host + path;
}
(async function boot() {
  // 先知道是谁（管理员 / 家人），再画界面，免得家人先看到一闪而过的维护入口
  try {
    const me = await api('/api/me');
    ME.role = me.role || 'admin';
    if (me.elder_label) ME.label = me.elder_label;
    if (me.elder_pronoun) ME.pronoun = me.elder_pronoun;
  } catch (e) { ME.role = 'family'; }
  document.body.classList.toggle('family', !isAdmin());
  applyElderLabels();
  const fromPath = location.pathname.replace(/\//g, '');
  const fromHash = (location.hash || '').slice(1);   // 老的 #锚点链接还能用
  const first = TABS.includes(fromPath) ? fromPath : (TABS.includes(fromHash) ? fromHash : 'home');
  go(first);
  if (first !== 'home' && first !== 'live') refreshDevice();   // 顶栏的在线和电量每一页都要有
  loadBalance(false);
  setInterval(() => { if (document.visibilityState === 'visible') refreshDevice(); }, 20000);
  setInterval(() => loadBalance(false), 300000);
})();
// 回到前台时刷新（像抬腕看表）；切到后台时如果还按着说话，先松开，别把麦克风一直开着
document.addEventListener('visibilitychange', () => {
  if (document.visibilityState === 'visible') {
    refreshDevice();
    if (_wakeLock == null && IC.ws && IC.ws.readyState === WebSocket.OPEN) wakeLockStart();
  } else if (IC.talking) icTalk(false);
});
