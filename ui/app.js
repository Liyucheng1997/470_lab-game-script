/* 面板：Python 推状态（render），这里只负责画和把点击传回去（window.pywebview.api.*） */
'use strict';
const $ = (s, el = document) => el.querySelector(s);
const esc = (s) => String(s ?? '').replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
const api = (name, ...args) => {
  const a = window.pywebview && window.pywebview.api;
  return a && a[name] ? a[name](...args) : Promise.resolve();
};

let S = {};
const STAGE_NAME = { early: '前期 1~10 波', mid: '中期 11~20 波', late: '后期 21 波+' };
const STYLE_NAME = { magic: '法术', physical: '物理', summon: '召唤' };
const ENGAGE_NAME = { dive: '贴脸冲', kite: '拉开打', hold: '站桩' };

// ---------------------------------------------------------------- 背景（Python 截好身后的游戏画面塞进来）
window.setBackdrop = (url) => {
  document.documentElement.style.setProperty('--backdrop', url ? `url("${url}")` : 'none');
};

// ---------------------------------------------------------------- 分段控件
function seg(el, value) {
  const btns = [...el.querySelectorAll('button')];
  const i = Math.max(0, btns.findIndex((b) => b.dataset.v === value));
  btns.forEach((b, j) => b.classList.toggle('on', j === i));
  const t = el.querySelector('.thumb');
  const b = btns[i];
  t.style.left = b.offsetLeft + 'px';
  t.style.width = b.offsetWidth + 'px';
}

// ---------------------------------------------------------------- 各块
function nowCard(n) {
  if (!n) return '';
  const lines = (n.lines || []).filter(Boolean);
  return `<section class="lg-card card lg-rise">
    <div class="sec">${esc(n.tag || '现在')}</div>
    <div class="title">${esc(n.title)}</div>
    ${lines.length ? `<ul class="lines">${lines.map((l) => `<li>${esc(l)}</li>`).join('')}</ul>` : ''}
  </section>`;
}

function heroCard(h) {
  if (!h) return '';
  const sk = h.skill || {};
  return `<section class="lg-card card">
    <div class="sec">英雄${h.browsing ? '<span class="lg-chip">正在看</span>' : ''}<span class="grow"></span>
      ${h.range ? `<span class="lg-chip">${h.range === 'ranged' ? '远程' : '近战'}</span>` : ''}</div>
    <div class="hero-head"><div class="title">${esc(h.name)}</div><span class="muted">${esc(h.role || '')}</span></div>
    ${sk.name ? `<div class="skill"><b>${esc(sk.kind || '')}·${esc(sk.name)}</b>　${esc(sk.how || '')}</div>` : ''}
    ${(h.tips || []).length ? `<ul class="lines" style="margin-top:8px">${h.tips.map((t) => `<li>${esc(t)}</li>`).join('')}</ul>` : ''}
    ${h.stats ? `<div class="muted" style="margin-top:6px;font-size:11.5px">${esc(h.stats)}</div>` : ''}
  </section>`;
}

function equipRow(order, owned) {
  owned = owned || [];
  return `<div class="equip">${(order || []).map((e, i) =>
    `${i ? '<span class="arrow">›</span>' : ''}<span class="lg-chip ${owned.includes(e) ? 'own' : ''}">${esc(e)}</span>`).join('')}</div>`;
}

function buildsCard(B) {
  if (!B || !(B.list || []).length) return '';
  const head = B.confirmed ? '流派（点一下换）' : (B.in_run ? '选一个流派开始' : '常见流派');
  const items = B.list.map((b, i) => {
    const on = i === B.chosen;
    let detail = '';
    if (on) {
      const tl = b.plan ? `<div><div class="label">长线规划</div><div class="timeline">${['early', 'mid', 'late'].map((k) =>
        `<div class="${B.stage === k ? 'now' : ''}"><b>${STAGE_NAME[k]}</b>${esc(b.plan[k])}</div>`).join('')}</div></div>` : '';
      detail = `<div class="detail">
        <div><div class="label">核心主动</div><div class="chips">${(b.core_actives || []).map((a) =>
          `<span class="lg-chip ${(B.have || []).includes(a) ? 'good' : 'accent'}">${esc(a)}</span>`).join('')}</div></div>
        <div><div class="label">核心被动</div><div class="chips">${(b.core_passives || []).map((a) =>
          `<span class="lg-chip ${(B.have || []).includes(a) ? 'good' : ''}">${esc(a)}</span>`).join('')}</div></div>
        <div><div class="label">出装顺序</div>${equipRow(b.equip_order, B.owned)}</div>
        ${(b.relic_focus || []).length ? `<div><div class="label">遗物优先</div><div class="chips">${b.relic_focus.map((a) => `<span class="lg-chip">${esc(a)}</span>`).join('')}</div></div>` : ''}
        ${b.stats ? `<div class="dim">属性：${esc(b.stats)}${b.combat ? `　打法：${ENGAGE_NAME[b.combat.engage] || ''}` : ''}</div>` : ''}
        ${tl}
        ${(b.how_to_play || []).length ? `<ul class="lines">${b.how_to_play.map((t) => `<li>${esc(t)}</li>`).join('')}</ul>` : ''}
        ${b.challenge_tip ? `<div class="dim">挑战：${esc(b.challenge_tip)}</div>` : ''}
      </div>`;
    }
    return `<div class="lg-card build ${on ? 'is-accent' : ''}" data-build="${i}">
      <div class="bh"><span class="num">${i + 1}</span><span class="bn">${esc(b.name)}</span>
        <span class="grow" style="flex:1"></span>
        ${b.style ? `<span class="lg-chip">${STYLE_NAME[b.style] || b.style}</span>` : ''}
        ${b.difficulty ? `<span class="lg-chip">${esc(b.difficulty)}</span>` : ''}</div>
      <div class="tag">${esc(b.tagline || b.why_fun || '')}</div>
      ${detail}
    </div>`;
  }).join('');
  return `<section>
    <div class="sec" style="padding:0 4px">${head}<span class="grow"></span>
      ${B.source ? `<span class="lg-chip">${esc(B.source)}</span>` : ''}
      <button class="lg-pill" style="height:20px;padding:0 8px;font-size:11px" data-act="replan" title="让 AI 再配 3 个（F9）">AI 再配</button></div>
    <div class="builds">${items}</div>
    ${B.hint ? `<div class="pick-hint">${esc(B.hint)}</div>` : ''}
  </section>`;
}

function choiceCard(C) {
  if (!C) return '';
  const max = Math.max(...C.cards.map((c) => c.score), 1);
  const min = Math.min(...C.cards.map((c) => c.score), 0);
  const none = C.best == null;
  return `<section class="choice lg-rise">
    <div class="sec" style="padding:0 4px">选${esc(C.kind_name)}<span class="grow"></span>
      ${none ? '<span class="lg-chip bad">不推荐</span>' : `<span class="lg-chip accent">★ 推荐第 ${C.best + 1} 张</span>`}</div>
    ${C.summary ? `<div class="lg-card card ${none ? '' : 'hi'}" style="padding:8px 11px"><div class="${none ? '' : 'dim'}" style="font-size:12.5px">${esc(C.summary)}</div></div>` : ''}
    ${C.refresh_why ? `<div class="pick-hint" style="margin:0 4px 2px">${C.refresh != null ? '↻ ' : ''}${esc(C.refresh_why)}</div>` : ''}
    ${C.cards.map((c, i) => `<div class="lg-card opt ${i === C.best ? 'is-accent' : ''}" style="${c.worth === false ? 'opacity:.62' : ''}">
      <div class="oh"><span class="on">${i === C.best ? '★ ' : ''}${esc(c.name || '?')}</span>
        ${i === C.refresh ? '<span class="lg-chip bad">↻ 建议刷新</span>' : ''}<span class="sc">${c.score}</span></div>
      ${c.replace ? `<div class="why" style="color:${c.worth === false ? 'var(--text-3)' : 'var(--accent)'};font-weight:500">${esc(c.replace)}</div>` : ''}
      <div class="why">${esc(c.why)}</div>
      ${c.note ? `<div class="why muted">${esc(c.note)}</div>` : ''}
      <div class="lg-meter"><i style="width:${Math.round(((c.score - min) / (max - min || 1)) * 92 + 8)}%"></i></div>
    </div>`).join('')}
  </section>`;
}

function mapsCard(M) {
  if (!M || !M.length) return '';
  return `<section class="lg-card card">
    <div class="sec">地图说明</div>
    ${M.map((m) => `<div class="map ${m.current ? 'cur' : ''}">
      <div class="row"><span class="mn">${esc(m.name)}</span>${m.diff ? `<span class="lg-chip ${m.current ? 'accent' : ''}">${esc(m.diff)}</span>` : ''}</div>
      <div class="ml">${esc(m.line)}</div>
      ${m.for ? `<div class="mt">适合：${esc(m.for)}　${esc(m.tip || '')}</div>` : ''}
    </div>`).join('')}
    ${S.map_advice ? `<div class="pick-hint">${esc(S.map_advice)}</div>` : ''}
  </section>`;
}

function combatCard(c) {
  if (!c) return '';
  const pct = (v) => (v == null ? '—' : Math.round(v * 100) + '%');
  const w = (v) => (v == null ? 0 : Math.round(v * 100));
  return `<section class="lg-card card">
    <div class="sec">战斗<span class="grow"></span>${c.enemies != null ? `<span class="lg-chip">视野内 ${c.enemies} 只</span>` : ''}</div>
    ${c.intent ? `<div class="intent"><span class="big">${esc(c.intent)}</span><span class="muted">${esc(c.why || '')}</span></div>` : ''}
    <div class="bars">
      <span>生命</span><div class="lg-meter hp ${c.hp != null && c.hp < 0.35 ? 'low' : ''}"><i style="width:${w(c.hp)}%"></i></div><span>${pct(c.hp)}</span>
      <span>法力</span><div class="lg-meter mp"><i style="width:${w(c.mp)}%"></i></div><span>${pct(c.mp)}</span>
      <span>压力</span><div class="lg-meter pr ${c.pressure != null && c.pressure > 0.7 ? 'high' : ''}"><i style="width:${w(c.pressure)}%"></i></div><span>${pct(c.pressure)}</span>
    </div>
    ${c.casts && c.casts.length ? `<div class="casts">最近施法：${c.casts.map(esc).join(' · ')}</div>` : ''}
  </section>`;
}

function runCard(r) {
  if (!r) return '';
  const rows = [];
  if (r.skills && r.skills.length) rows.push(`<span>技能</span><span>${esc(r.skills.join('、'))}</span>`);
  if (r.relics && r.relics.length) rows.push(`<span>遗物</span><span>${esc(r.relics.join('、'))}</span>`);
  if ((r.owned && r.owned.length) || (r.wish && r.wish.length))
    rows.push(`<span>出装</span><span>${esc([...(r.owned || []).map((e) => e + '✓'), ...(r.wish || []).slice(0, 4)].join(' › '))}</span>`);
  if (!rows.length) return '';
  return `<section class="lg-card card"><div class="sec">本局</div><div class="kv">${rows.join('')}</div></section>`;
}

function aiCard(a) {
  if (!a || !a.text) return '';
  return `<section class="lg-card card hi lg-rise"><div class="sec">AI 说</div><div class="ai ${a.busy ? 'busy' : ''}">${esc(a.text)}</div></section>`;
}

function logCard(logs) {
  if (!logs || !logs.length) return '';
  return `<section class="lg-card card"><div class="sec">刚才</div><div class="log">${logs.slice(-5).map((l) => `<span>${esc(l)}</span>`).join('')}</div></section>`;
}

// ---------------------------------------------------------------- 总渲染
function render(state) {
  const keepScroll = $('#main').scrollTop;
  S = state;
  seg($('#seg-mode'), S.auto ? 'auto' : 'advise');
  seg($('#seg-style'), S.playstyle);
  $('#status').textContent = S.status || '';
  const dot = $('#dot');
  dot.className = 'dot' + (S.auto ? ' auto' : S.live ? ' live' : '');

  const order = S.scene === 'choice' ? [choiceCard(S.choice), buildsCard(S.builds), heroCard(S.hero)]
    : S.scene === 'battle' ? [combatCard(S.combat), nowCard(S.now), buildsCard(S.builds), runCard(S.run)]
    : S.scene === 'map' ? [nowCard(S.now), mapsCard(S.maps), heroCard(S.hero), buildsCard(S.builds)]
    : [nowCard(S.now), heroCard(S.hero), buildsCard(S.builds), choiceCard(S.choice), mapsCard(S.maps), runCard(S.run)];
  const html = [...order, aiCard(S.ai), logCard(S.logs)].join('');
  $('#main').innerHTML = html || `<div class="empty"><div class="title">等游戏</div>进游戏后自动跟上</div>`;
  $('#main').scrollTop = keepScroll;
}
window.render = render;

// ---------------------------------------------------------------- 交互
document.addEventListener('click', (e) => {
  const b = e.target.closest('[data-build]');
  if (b) { api('choose_build', +b.dataset.build); return; }
  const a = e.target.closest('[data-act]');
  if (a) {
    const act = a.dataset.act;
    if (act === 'reload') { $('#status').textContent = '重新加载中…'; api('reload'); }
    else if (act === 'hide') api('hide');
    else if (act === 'quit') api('quit');
    else if (act === 'replan') api('replan');
    else if (act === 'ask-now') api('ask', '现在该怎么选、怎么做？');
    return;
  }
  const m = e.target.closest('.lg-seg button');
  if (m) {
    const id = m.parentElement.id;
    if (id === 'seg-mode') api('set_auto', m.dataset.v === 'auto');
    if (id === 'seg-style') api('set_style', m.dataset.v);
    seg(m.parentElement, m.dataset.v);
  }
});

$('#ask').addEventListener('keydown', (e) => {
  if (e.key === 'Enter' && !e.isComposing) {
    const q = e.target.value.trim();
    if (q) { api('ask', q); e.target.value = ''; }
  }
  if (e.key === 'Escape') { e.target.value = ''; api('focus_game'); }
});

window.addEventListener('pywebviewready', () => api('ready'));
window.addEventListener('resize', () => { seg($('#seg-mode'), S.auto ? 'auto' : 'advise'); seg($('#seg-style'), S.playstyle || '爽'); });
render({ playstyle: '爽', status: '启动中…', now: { title: '等游戏', lines: ['进游戏后我会自动跟上：选英雄、选地图、三选一、商店、战斗都会在这里给建议'] } });
