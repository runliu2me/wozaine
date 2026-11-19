// 已登录就「回管理台」，没登录就「回登录」；顺便数一数一共致敬了多少个来源
(function () {
  const home = document.getElementById('backHome');
  const login = document.getElementById('backLogin');
  const show = (loggedIn) => { home.hidden = !loggedIn; login.hidden = loggedIn; };
  fetch('/api/me', { credentials: 'same-origin' }).then((r) => show(r.ok)).catch(() => show(false));
  const n = document.querySelectorAll('.credits li').length;
  const count = document.getElementById('count');
  if (count && n) count.textContent = '一共 ' + n + ' 处来源';
})();
