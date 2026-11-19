function safeNext(raw) {
  if (!raw) return '/home';
  let path = raw;
  try { path = decodeURIComponent(raw); } catch (e) { return '/home'; }
  path = String(path).trim();
  if (!path.startsWith('/') || path.startsWith('//') || path.includes('\\') || path.includes('\0')) return '/home';
  const bare = path.split('?')[0];
  if (bare.includes(':') || bare.startsWith('/login') || bare.startsWith('/api/')) return '/home';
  return path;
}
const f = document.getElementById('f'), err = document.getElementById('err'), btn = document.getElementById('btn');
const pw = document.getElementById('password'), tog = document.getElementById('tog');
tog.addEventListener('click', () => {
  const show = pw.type === 'password';
  pw.type = show ? 'text' : 'password';
  tog.textContent = show ? '隐藏' : '显示';
  tog.setAttribute('aria-label', show ? '隐藏密码' : '显示密码');
});
f.addEventListener('submit', async (e) => {
  e.preventDefault(); err.textContent = ''; btn.disabled = true;
  try {
    const r = await fetch('/api/login', { method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ user: document.getElementById('user').value.trim(), password: pw.value }) });
    const d = await r.json().catch(() => ({}));
    if (!r.ok) { err.textContent = d.error || ('登录失败 ' + r.status); btn.disabled = false; return; }
    location.replace(safeNext(new URLSearchParams(location.search).get('next')));
  } catch (e2) { err.textContent = '网络错误：' + e2.message; btn.disabled = false; }
});
