const $ = s => document.querySelector(s);
const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
let tab = 'top', D = null;

async function api(path, opts = {}) {
  const r = await fetch('/api' + path, {credentials: 'same-origin', headers: {'Content-Type': 'application/json'}, ...opts,
    body: opts.body ? JSON.stringify(opts.body) : undefined});
  if (r.status === 401 && !path.startsWith('/auth/')) { showAuth(); throw new Error('Please log in'); }
  if (!r.ok) {
    const d = (await r.json().catch(() => ({}))).detail;
    throw new Error(typeof d === 'string' ? d : Array.isArray(d) ? d.map(x => x.msg).join('; ') : r.statusText);
  }
  return r.json();
}

// ---------- accounts
let authMode = 'login';
function setAuthMode(m) {
  authMode = m;
  $('#authTitle').textContent = m === 'login' ? 'Log in to Fenris' : 'Create your Fenris account';
  $('#a_btn').textContent = m === 'login' ? 'Log in' : 'Create account';
  $('#a_name').classList.toggle('hidden', m === 'login'); $('#a_name').required = m !== 'login';
  $('#a_pw').autocomplete = m === 'login' ? 'current-password' : 'new-password';
  $('#authSwitch').textContent = m === 'login' ? 'Create an account' : 'I already have an account';
  $('#authErr').textContent = '';
}
function showAuth() { closeModal(); $('#auth').classList.remove('hidden'); }
$('#authSwitch').onclick = e => { e.preventDefault(); setAuthMode(authMode === 'login' ? 'register' : 'login'); };
$('#authForm').onsubmit = async e => {
  e.preventDefault(); $('#authErr').textContent = '';
  const body = {email: $('#a_email').value, password: $('#a_pw').value};
  if (authMode === 'register') { body.name = $('#a_name').value; body.timezone = Intl.DateTimeFormat().resolvedOptions().timeZone || 'Asia/Kolkata'; }
  try { await api('/auth/' + authMode, {method: 'POST', body}); $('#a_pw').value = ''; $('#auth').classList.add('hidden'); load(); }
  catch (err) { $('#authErr').textContent = err.message; }
};
async function logout() { try { await api('/auth/logout', {method: 'POST'}); } catch (e) {} D = null; location.reload(); }
async function deleteAccount() {
  const pw = prompt('This permanently deletes your account and ALL your data. Enter your password to confirm:');
  if (!pw) return;
  try { await api('/auth/delete-account', {method: 'POST', body: {password: pw}}); location.reload(); } catch (e) { alert(e.message); }
}

async function load() {
  try { D = await api('/dashboard'); } catch (e) { if (e.message !== 'Please log in') $('#notes').innerHTML = `<div class="n">Could not load: ${esc(e.message)}</div>`; return; }
  $('#auth').classList.add('hidden');
  if (!D.user.setup_complete) openSetup(true);
  $('#date').textContent = `${D.weekday} ${D.date}`;
  $('#score').textContent = D.score.score + '/100';
  $('#yscore').textContent = 'Yesterday ' + D.yesterday_score + ' · ' + Object.entries(D.score.components).map(([k, v]) => k.replace('_', ' ') + ' ' + v).join(' · ');
  const h = D.habits;
  $('#streaks').innerHTML = [['🔥 Course', h.course], ['🔥 LeetCode', h.leetcode], ['📅 Planning', h.planning], ['🌙 Review', h.review]]
    .map(([n, x]) => `<div>${n}<br><b>${x.streak} days</b></div>`).join('');
  $('#notes').innerHTML = D.notifications.map(n => `<div class="n"><span>${esc(n.message.split('\n')[0])}</span><a href="#" onclick="markRead();return false" style="color:#fff">dismiss</a></div>`).join('');
  $('#workload').innerHTML = (D.workload.message ? `<div class="warn">⚠️ ${esc(D.workload.message)}</div>` : '') + (D.workload.recovery ? `<div class="warn">${esc(D.workload.recovery)}</div>` : '') +
    (D.procrastination || []).map(p => `<div class="warn">${esc(p.title)}: ${esc(p.message)}</div>`).join('');
  $('#tasks').innerHTML = D.tasks.map(t => `<li><span class="dot ${t.tier}"></span><span class="t">${esc(t.title)}<br><small>${t.deadline ? 'due ' + t.deadline.slice(0, 10) : 'no deadline'}${t.estimated_minutes ? ' · ' + t.estimated_minutes + ' min' : ''}${t.postponement_count ? ' · postponed ' + t.postponement_count + 'x' : ''}</small></span>
    <button class="ghost" onclick="done(${t.id})">Done</button><button class="ghost" onclick="tomorrow(${t.id})">Tomorrow</button></li>`).join('') || '<li class="muted">No open tasks.</li>';
  $('#unfinished').innerHTML = D.unfinished_yesterday.length ? '<h2 style="margin-top:14px">Unfinished (review, not auto-moved)</h2>' + D.unfinished_yesterday.map(u => `<div class="muted">• ${esc(u.title)}: ${esc(u.reason)}</div>`).join('') : '';
  const course = D.course;
  $('#mandatory').innerHTML = [['course', '📚 Course'], ['leetcode', '💻 LeetCode']].map(([k, n]) => {
    const x = h[k]; const p = Math.min(100, 100 * x.today_minutes / 30);
    return `<div class="habit"><div class="row"><b>${n}${k === 'course' && course ? ' · ' + esc(course.name) : ''}</b><span>${x.today_minutes}/30 min ${x.met_today ? '✅' : ''}</span></div>
    <div class="bar"><i style="width:${p}%"></i></div>
    <div class="row muted"><span>Streak ${x.streak} · week ${x.week_done}/7 · month ${x.month_done}/30</span><span><button class="ghost" onclick="logHabit('${k}',15)">+15</button> <button class="ghost" onclick="logHabit('${k}',30)">+30</button></span></div></div>`;
  }).join('') + (D.course_trend.message ? `<div class="warn">${esc(D.course_trend.message)}</div>` : '');
  $('#upcoming').innerHTML = Object.entries(D.upcoming).map(([k, v]) => `<b>${esc(k)}</b>` + v.map(t => `<div class="muted">• ${esc(t.title)}</div>`).join('')).join('') || '<span class="muted">Nothing due in the next two weeks.</span>';
  $('#goals').innerHTML = D.goals.map(g => `<div class="habit"><div class="row"><b>${esc(g.title)}</b><span>${g.percent}%</span></div><div class="bar"><i style="width:${g.percent}%"></i></div><div class="muted">${esc(g.basis)}</div></div>`).join('') || '<span class="muted">No goals yet.</span>';
  $('#aiState').textContent = D.ai_enabled ? '' : 'AI is off (no OPENAI_API_KEY): structured commands work, free-form chat does not.';
  renderNews();
}

function renderNews() {
  const n = D.news, tabs = ['top', 'technology', 'finance', 'india', 'world'];
  $('#tabs').innerHTML = tabs.map(t => `<button class="${t === tab ? 'on' : ''}" onclick="tab='${t}';renderNews()">${t}</button>`).join('');
  if (!n.available) { $('#news').innerHTML = `<div class="muted">${esc(n.message)}</div>`; return; }
  const items = tab === 'top' ? n.top : n.categories[tab] || [];
  $('#news').innerHTML = items.map(i => `<div class="news-item"><a href="${esc(i.url)}" target="_blank" rel="noopener">${esc(i.title)}</a><br>
    <span class="tag t${i.source_tier}">Tier ${i.source_tier}</span><span class="tag">${esc(i.source)}</span><span class="tag">${esc(i.verification)}</span>
    ${i.also_reported_by.length ? `<span class="muted">also: ${esc(i.also_reported_by.join(', '))}</span>` : ''}
    ${i.briefing ? `<div class="muted"><b>What happened</b> (confirmed): ${esc(i.briefing.what_happened)}<br><b>Why it matters</b> (analysis): ${esc(i.briefing.why_it_matters)}<br><b>What next</b> (speculation): ${esc(i.briefing.what_next)}</div>` : `<div class="muted">${esc((i.summary || '').slice(0, 180))}</div>`}</div>`).join('') || '<div class="muted">Nothing collected for this category.</div>';
}

const done = async id => { await api('/tasks/' + id, {method: 'PATCH', body: {status: 'completed'}}); load(); };
const tomorrow = async id => { const d = new Date(); d.setDate(d.getDate() + 1); d.setHours(23, 59, 0, 0);
  const iso = new Date(d - d.getTimezoneOffset() * 60000).toISOString().slice(0, 19);
  await api('/tasks/' + id, {method: 'PATCH', body: {deadline: iso, status: 'pending'}}); load(); };
const logHabit = async (h, m) => { await api('/habits', {method: 'POST', body: {habit: h, minutes: m}}); load(); };
const markRead = async () => { await api('/notifications/read', {method: 'POST'}); load(); };
async function refreshNews() { $('#news').innerHTML = '<div class="muted">Fetching trusted sources…</div>'; try { await api('/news/refresh', {method: 'POST'}); } catch (e) {} load(); }

function addMsg(t, me) { const d = document.createElement('div'); d.className = 'msg' + (me ? ' me' : ''); d.textContent = t; $('#log').append(d); $('#log').scrollTop = 1e9; }
async function say(m) { addMsg(m, true); try { const r = await api('/assistant/chat', {method: 'POST', body: {message: m}}); addMsg(r.reply); } catch (e) { addMsg('Error: ' + e.message); } load(); }
$('#chatForm').onsubmit = e => { e.preventDefault(); const v = $('#chatInput').value.trim(); if (v) { $('#chatInput').value = ''; say(v); } };
$('#addForm').onsubmit = e => { e.preventDefault(); const v = $('#addInput').value.trim(); if (v) { $('#addInput').value = ''; say(/^(add|remind|i need)/i.test(v) ? v : 'Add task: ' + v); } };
$('#goalForm').onsubmit = async e => { e.preventDefault(); const t = $('#goalTitle').value.trim(); if (!t) return;
  await api('/goals', {method: 'POST', body: {title: t, milestones: $('#goalMs').value.split(',').map(s => s.trim()).filter(Boolean)}}); $('#goalTitle').value = $('#goalMs').value = ''; load(); };

function modal(html) { $('#modalBody').innerHTML = html; $('#modal').classList.remove('hidden'); }
function closeModal() { $('#modal').classList.add('hidden'); }
async function showWeekly() { const r = await api('/reviews/weekly?refresh=true'); modal(`<pre>${esc(r.text)}</pre>`); }

async function openSetup(first) {
  const u = await api('/setup');
  const f = (id, label, v = '') => `<label>${label}</label><input id="s_${id}" value="${esc(v)}">`;
  modal(`<h2>${first ? 'Welcome to Fenris — quick setup' : 'Settings'}</h2>` +
    f('name', "What's your name?", u.name === 'there' ? '' : u.name) + f('timezone', 'Timezone', u.timezone || Intl.DateTimeFormat().resolvedOptions().timeZone) +
    f('wake', 'When do you start your day? (HH:MM)', u.wake_time) + f('sleep', 'When do you finish your day? (HH:MM)', u.sleep_time) +
    f('hours', 'Hours available for focused work per day', u.daily_hours) + f('course', 'Course you are studying', u.current_course) +
    f('goals', 'Main goals', u.goals_text) + f('interests', 'Areas of technology you like', u.interests) + f('news', 'News you care about most', u.news_interests) +
    f('mt', 'Morning briefing time', u.morning_time) + f('ct', 'Course reminder time', u.course_time) + f('lt', 'LeetCode reminder time', u.leetcode_time) + f('et', 'Evening review time', u.evening_time) +
    '<p><button onclick="saveSetup()">Save</button> <button class="ghost" onclick="deleteAccount()">Delete my account</button></p>');
}
async function saveSetup() {
  const g = id => $('#s_' + id).value;
  try { await api('/setup', {method: 'POST', body: {name: g('name') || 'there', timezone: g('timezone'), wake_time: g('wake'), sleep_time: g('sleep'), daily_hours: parseFloat(g('hours')) || 6,
    current_course: g('course'), goals: g('goals'), interests: g('interests'), news_interests: g('news'), morning_time: g('mt'), course_time: g('ct'), leetcode_time: g('lt'), evening_time: g('et')}}); closeModal(); load(); }
  catch (e) { alert(e.message); }
}
load(); setInterval(load, 5 * 60 * 1000);
