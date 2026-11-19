(async function () {
  const me = await fetch('/api/me').then((r) => r.json()).catch(() => ({}));
  const L = me.elder_label || '妈妈';
  const data = await fetch('/api/book').then((r) => r.json());
  const book = data.book, ENT = data.entries;
  if (!book.chapters || !book.chapters.length) { document.getElementById('loading').textContent = '书还没开始写。和' + L + '聊几天，今晚就有第一章。'; return; }
  if (book.title) document.title = book.title;

  // ---- 尺寸：PC 对开，手机单页 ----
  const vw = window.innerWidth, vh = window.innerHeight;
  const portrait = vw < 760;
  const W = portrait ? Math.min(vw - 16, 460) : Math.min(Math.floor((vw - 60) / 2), 520);
  const H = portrait ? Math.min(vh - 70, Math.round(W * 1.5)) : Math.min(vh - 70, Math.round(W * 1.42));
  const FS = portrait ? 15 : 16, PAD = Math.round(H * 0.06), PADX = Math.round(W * 0.09);
  document.documentElement.style.setProperty('--fs', FS + 'px');
  document.documentElement.style.setProperty('--pad', PAD + 'px');
  document.documentElement.style.setProperty('--padx', PADX + 'px');

  // ---- 把每章正文分页：用隐藏页实测高度，贪心装段落 ----
  const probe = document.createElement('div');
  probe.className = 'page'; probe.style.cssText = `position:absolute;left:-9999px;top:0;width:${W}px;height:${H}px;`;
  probe.innerHTML = '<div class="page-inner"><div class="running">x</div><div class="note-rule"></div><div class="body" id="probeBody"></div></div>';
  document.body.appendChild(probe);
  const probeBody = probe.querySelector('#probeBody');
  const bodyMax = H - PAD * 2 - 48;   // 页眉 + 页码
  const esc = (s) => String(s == null ? '' : s).replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  const HL = esc(L), P = esc(me.elder_pronoun || '她');   // 称呼和代词进 HTML 前先转义
  const circled = (n) => n <= 20 ? String.fromCharCode(0x2460 + n - 1) : '(' + n + ')';
  const fitsText = (paras) => { probeBody.innerHTML = paras.map((h) => `<p>${h}</p>`).join(''); return probeBody.scrollHeight <= bodyMax; };
  const noteHtml = (n, ref) => {
    const e = ENT[ref.id] || { quotes: [], audio: [] }; const q = e.quotes[ref.q] || ''; const a = (e.audio || [])[ref.q];
    return `<div class="note"><span class="n">${circled(n)}</span><span class="q"><span class="say">“${esc(q)}”</span><span class="when">${esc(e.date || '')}</span></span>${a ? `<button class="play" data-audio="${esc(a)}" title="听${P}说">▶</button>` : ''}</div>`;
  };
  const fitsNotes = (refs) => { probeBody.innerHTML = refs.map((r, i) => noteHtml(i + 1, r)).join(''); return probeBody.scrollHeight <= bodyMax; };

  // 一个"对开"= 左页她的原话（注）+ 右页文学叙述。注的编号每个对开从①重新开始。
  // 贪心：往对开里加段落，直到叙述放不下或原话放不下；一段引用太多，就只注能放下的前几句。
  function paginateChapter(ch) {
    const spreads = []; let cur = null;
    const flush = () => { if (cur && cur.paras.length) spreads.push(cur); cur = { paras: [], refs: [] }; };
    flush();
    const numbering = (s) => { const m = new Map(); const uniq = []; s.paras.forEach((p) => p.refs.forEach((r) => { const k = r.id + ':' + r.q; if (!m.has(k)) { m.set(k, uniq.length + 1); uniq.push(r); } })); return { m, uniq }; };
    const render = (s) => { const { m } = numbering(s); return s.paras.map((p) => esc(p.text) + p.refs.map((r) => `<span class="note-mark" data-eid="${r.id}" data-q="${r.q}">${circled(m.get(r.id + ':' + r.q))}</span>`).join('')); };
    for (const p0 of ch.paragraphs) {
      const seen = new Set(); const refs0 = p0.refs.filter((r) => { const k = r.id + ':' + r.q; if (seen.has(k) || !(ENT[r.id] && ENT[r.id].quotes[r.q])) return false; seen.add(k); return true; });
      const tryAdd = (refs) => { const s = { paras: cur.paras.concat({ text: p0.text, refs }), refs: [] }; s.refs = numbering(s).uniq; return fitsText(render(s)) && fitsNotes(s.refs) ? s : null; };
      let s = tryAdd(refs0);
      if (!s && cur.paras.length) { flush(); s = tryAdd(refs0); }
      if (!s) { // 单段也放不下：少注几句
        let k = refs0.length - 1;
        while (k >= 0 && !(s = tryAdd(refs0.slice(0, k)))) k--;
        if (!s) s = { paras: [{ text: p0.text, refs: [] }], refs: [] };
      }
      cur = s;
    }
    flush();
    return spreads.map((s) => ({ html: render(s), refs: s.refs }));
  }

  // ---- 生成页面。StPageFlip showCover：0=封面（单页），之后奇数页在左、偶数页在右 ----
  const pagesEl = [];
  const mk = (cls, inner) => { const d = document.createElement('div'); d.className = 'page ' + cls; d.innerHTML = `<div class="page-inner">${inner}</div>`; return d; };
  const blank = () => mk('', '');
  const padToLeft = () => { if (!portrait && pagesEl.length % 2 === 0) pagesEl.push(blank()); };
  const folio = (n) => `<div class="folio">${n}</div>`;
  const NUM = ['一','二','三','四','五','六','七','八','九','十','十一','十二','十三','十四','十五','十六','十七','十八','十九','二十'];
  const tocItems = [];

  pagesEl.push(mk('cover', `<div class="title-cols">${esc(book.title).split(/[，,]/).map((t) => `<h1>${t}</h1>`).join('')}</div><div class="sub">${esc(book.subtitle || '')}</div><div class="seal">我在呢</div><div class="meta">${book.entries || 0} 段讲述 · ${book.quotes || 0} 句原话 · 更新于 ${esc(book.generated_at || '')}</div>`));
  pagesEl.push(mk('', `<div class="body intro"><p>这本书里，<br>左边是${HL}自己说的话，<br>一个字没改，点 ▶ 能听见${P}的声音；<br>右边是家人替${P}理出来的故事。</p></div>`));
  const tocPage = mk('toc', `<h2>目录</h2><ol id="tocList"></ol>`); pagesEl.push(tocPage);

  let folioNo = 0;
  book.chapters.forEach((ch, ci) => {
    padToLeft();
    tocItems.push({ title: ch.title, idx: pagesEl.length, orig: ch.chapter });
    const firstQuote = (() => { for (const p of ch.paragraphs) for (const r of p.refs) { const e = ENT[r.id]; if (e && e.quotes[r.q]) return e.quotes[r.q]; } return ''; })();
    pagesEl.push(mk('chap-open', `<div class="num">第${NUM[ci] || ci + 1}章</div><h2>${esc(ch.title)}</h2><div class="orig">${esc(ch.chapter)}</div>`));
    pagesEl.push(mk('', `<div class="running">题记</div><div class="body epigraph"><p>“${esc(firstQuote)}”</p><p class="by">——${esc((book.subtitle || '').split('口述')[0])}</p></div>`));
    paginateChapter(ch).forEach((sp) => {
      const notes = sp.refs.map((r, i) => noteHtml(i + 1, r)).join('');
      const textPage = mk('', `<div class="running">${esc(ch.title)}</div><div class="body">${sp.html.map((h) => `<p>${h}</p>`).join('')}</div>${folio(folioNo + 2)}`);
      const notePage = mk('notes', `<div class="running">${P}说</div><div class="note-rule"></div>${notes || `<div class="note"><span class="q gold">（这一段没有直接引${P}的话）</span></div>`}${folio(folioNo + 1)}`);
      folioNo += 2;
      if (portrait) { pagesEl.push(textPage, notePage); }      // 手机：先读故事，翻过去听原话
      else { padToLeft(); pagesEl.push(notePage, textPage); }  // 对开：左原话、右故事
    });
    if (ch.threads && ch.threads.length) {
      padToLeft();
      pagesEl.push(mk('threads', `<div class="running">${esc(ch.title)}</div><h3>想听更多</h3>` + ch.threads.map((t) => `<div class="thread">${esc(t.text)}<div class="ask">问${HL}：${esc(t.question)}</div><button data-qid="${t.qid || ''}">下次就问这个</button></div>`).join('')));
      if (!portrait) pagesEl.push(blank());
    }
  });
  if (!portrait && pagesEl.length % 2 === 1) pagesEl.push(blank());
  pagesEl.push(mk('', `<div class="body tbc">未完 · 每天都在往下写</div>`));
  if (!portrait && pagesEl.length % 2 === 1) pagesEl.push(blank());
  tocPage.querySelector('#tocList').innerHTML = tocItems.map((t) => `<li data-idx="${t.idx}"><span>${esc(t.title)}<span class="toc-orig">${esc(t.orig)}</span></span><span>${t.idx}</span></li>`).join('');
  probe.remove();

  // ---- 翻书 ----
  const bookEl = document.getElementById('book');
  pagesEl.forEach((p) => { p.style.width = W + 'px'; p.style.height = H + 'px'; bookEl.appendChild(p); });
  document.getElementById('loading').style.display = 'none';
  document.getElementById('stage').hidden = false;
  const flip = new St.PageFlip(bookEl, { width: W, height: H, size: 'fixed', usePortrait: portrait, showCover: true, mobileScrollSupport: false, maxShadowOpacity: 0.45, flippingTime: 650, drawShadow: true, autoSize: false });
  flip.loadFromHTML(pagesEl);
  const pos = document.getElementById('pos');
  const updatePos = () => { pos.textContent = (flip.getCurrentPageIndex() + 1) + ' / ' + pagesEl.length; };
  flip.on('flip', (e) => { updatePos(); history.replaceState(null, '', '/book?p=' + flip.getCurrentPageIndex()); });
  updatePos();
  const startP = Number(new URLSearchParams(location.search).get('p') || 0);
  if (new URLSearchParams(location.search).get('debug')) setTimeout(() => { const r = bookEl.getBoundingClientRect(); const pg = document.querySelector('.stf__item[style*="display: block"]'); document.title = JSON.stringify({ vw, W, H, book: [r.x, r.width], page: pg && [pg.getBoundingClientRect().x, pg.getBoundingClientRect().width] }); }, 1500);
  if (startP > 0 && startP < pagesEl.length) flip.turnToPage(startP);
  document.addEventListener('keydown', (e) => { if (e.key === 'ArrowRight' || e.key === ' ') flip.flipNext(); if (e.key === 'ArrowLeft') flip.flipPrev(); });
  document.getElementById('tocBtn').onclick = () => flip.flip(1);
  tocPage.addEventListener('click', (e) => { const li = e.target.closest('li[data-idx]'); if (li) flip.flip(Number(li.dataset.idx)); });

  // ---- 听原话 ----
  let playing = null;
  function playAudio(path, btn) {
    if (playing) { playing.audio.pause(); playing.btn && playing.btn.classList.remove('on'); if (playing.path === path) { playing = null; return; } }
    const a = new Audio('/api/' + path); playing = { audio: a, btn, path }; btn && btn.classList.add('on');
    a.onended = () => { btn && btn.classList.remove('on'); playing = null; };
    a.play().catch(() => { btn && btn.classList.remove('on'); playing = null; });
  }
  bookEl.addEventListener('click', (e) => {
    const pb = e.target.closest('button.play'); if (pb) { e.stopPropagation(); playAudio(pb.dataset.audio, pb); return; }
    const mark = e.target.closest('.note-mark');
    if (mark) {
      e.stopPropagation();
      const ent = ENT[mark.dataset.eid]; const q = ent && ent.quotes[mark.dataset.q]; const a = ent && (ent.audio || [])[mark.dataset.q];
      if (portrait) {
        document.getElementById('sheetBody').innerHTML = `<div class="note"><span class="n">${mark.textContent}</span><span class="q"><span class="say">“${esc(q || '')}”</span><span class="when">${esc(ent ? ent.date : '')}</span></span>${a ? `<button class="play" data-audio="${esc(a)}">▶</button>` : ''}</div>`;
        document.getElementById('sheet').classList.add('open');
      } else if (a) { playAudio(a); }
      return;
    }
    const tb = e.target.closest('.thread button');
    if (tb) {
      e.stopPropagation();
      fetch('/api/story/question/update', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ id: tb.dataset.qid, pinned: true }) })
        .then((r) => r.json()).then((d) => { tb.textContent = d.ok ? '好，下次就问' : '题库里没找到'; tb.classList.add('done'); }).catch(() => { tb.textContent = '没发出去'; });
    }
  });
  document.getElementById('sheet').addEventListener('click', (e) => { const pb = e.target.closest('button.play'); if (pb) playAudio(pb.dataset.audio, pb); });
  document.getElementById('sheetClose').onclick = () => document.getElementById('sheet').classList.remove('open');
})();
