'use strict';

const $ = (s, root = document) => root.querySelector(s);
const $$ = (s, root = document) => [...root.querySelectorAll(s)];
const esc = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const icon = name => `<svg class="icon" aria-hidden="true"><use href="#i-${name}"/></svg>`;
const safeURL = raw => {try { const u = new URL(raw); return ['http:', 'https:'].includes(u.protocol) ? u.href : ''; } catch {return '';}};
const fmtDate = value => {if (!value) return '未记录'; const d = new Date(value); return Number.isNaN(+d) ? String(value) : d.toLocaleDateString('zh-CN', {year:'numeric',month:'2-digit',day:'2-digit'});};
const chars = value => [...(value || '').replace(/\s/g, '')].length;
const labels = {topics:'A · 选题板块',home:'B · 公众号写作',articles:'素材库',cards:'写法卡片',notes:'我的思考',drafts:'文章草稿',performance:'D · 数据复盘',settings:'模型与设置'};
let state = {articles:[],cards:[],drafts:[],writing_runs:[],performance_records:[],notes:{text:'',path:'',revision:''},settings:{},jobs:[],stats:{}};
let page = 'home', modal = null, modalDirty = false, settingsDirty = false;
let noteSession = null, noteConflict = null, notesPollBusy = false, jobsBusy = false;
let articleFilter = {query:'',heat:'all',sort:'newest'};
let composer = {brief:'',audience:'整灯厂和上游供应商从业者',tone:'专业、有观点，兼顾行业深度与可读性',length:'约1500字',article_ids:[],use_web:true,find_images:true,image_count:6};
let trackedJobs = new Map();
let activeDraft = null, draftPreview = false;
let draftSavingId = null;
const imageStarts = new Set(), draftImageErrors = new Map();
let performanceReview=null, performanceQuery='', performanceImport=null, performanceLoading=false, performanceImportRead=0;
let performanceDateBaseline=null;
let topicBatches=[], activeTopicBatch=null;
let cozeSettings=null, cozeSession=null, cozePendingTopic='', cozeShownJob=null;
let writingPolicySession=null;
let cozeQuickTopic='', cozeQuickWriterSession=null;
const cozeQuickStarts=new Set();
const performanceMetrics={reads:'阅读',likes:'点赞',favorites:'收藏',shares:'转发',comments:'评论',wows:'在看'};

async function api(path, options = {}) {
  const response = await fetch(path, {headers:{'Content-Type':'application/json'},...options,body:options.body === undefined ? undefined : JSON.stringify(options.body)});
  let data;
  try {data = await response.json();} catch {throw new Error(`服务响应异常（${response.status}），请确认本地服务正在运行。`);}
  if (!response.ok) {const e = new Error(data.error || `请求失败（${response.status}）`); e.status = response.status; throw e;}
  return data;
}
function toast(message, error = false) {
  const el = document.createElement('div'); el.className = `toast${error ? ' error' : ''}`; el.textContent = message;
  $('#toast-root').append(el); setTimeout(() => el.remove(), error ? 9000 : 4800);
}
function formError(form, message) {
  let el = $('.form-error', form); if (!el) {el = document.createElement('div');el.className='form-error';el.setAttribute('role','alert');form.append(el);} el.textContent = message;
}
async function busy(button, work) {
  if (button?.disabled) return;
  const old = button?.innerHTML; if (button) {button.disabled=true;button.innerHTML='<span class="spinner"></span> 处理中…';}
  try {return await work();} finally {if (button?.isConnected) {button.disabled=false;button.innerHTML=old;}}
}
function modelLabel(settings=state.settings){return `${({ollama:'本地模型',codex:'Codex 会员',openai:'云端 API'})[settings.provider]||'写作模型'} · ${settings.model||'待配置'}`;}
function modelPrivacy(provider){
  if(provider==='codex')return '使用本机 Codex 登录，无需另填 API 密钥，消耗同一账号 Codex 额度。本次选用的素材、写法卡片和笔记会发送至 OpenAI；模型可用性取决于账号。';
  if(provider==='ollama')return '当前使用本地模型。联网搜索仍会把搜索词发送至搜索服务。';
  return '使用云端模型时，本次选用的素材、写法卡片及个人笔记会发送至你配置的模型服务。';
}
function updateProviderFields(selectDefaults=false){
  const provider=$('#provider').value, codex=provider==='codex',local=provider==='ollama';
  $('#base-url-field').hidden=codex;$('#base_url').disabled=codex;$('#base_url').required=!codex;
  $('#api-key-field').hidden=codex;$('#api_key').disabled=codex;
  $('#model').placeholder=codex?'gpt-6-astra':local?'qwen2.5:latest':'填写服务商提供的模型名称';
  if(selectDefaults){
    if(codex)$('#model').value=state.settings.provider==='codex'&&state.settings.model?state.settings.model:'gpt-6-astra';
    else if(local)$('#model').value=state.settings.provider==='ollama'&&state.settings.model?state.settings.model:'qwen2.5:latest';
    else $('#model').value=state.settings.provider==='openai'?state.settings.model||'':'';
    if(!codex&&['http://localhost:11434','http://127.0.0.1:11434','https://api.openai.com/v1',''].includes($('#base_url').value))$('#base_url').value=local?'http://localhost:11434':'https://api.openai.com/v1';
  }
  $('#model-privacy').textContent=modelPrivacy(provider);
  $('#model-preset-help').textContent=codex?'GPT-6 Astra 使用本机已登录的 Codex 账号；保存后可测试模型。':'点击预设可切换到 Codex 会员并使用 GPT-6 Astra。';
}
function updateChrome() {
  for (const name of ['articles','cards','drafts']) $(`#nav-${name}`).textContent=state[name].length;
  if($('#nav-performance'))$('#nav-performance').textContent=(state.performance_records||[]).length;
  $('#model-status').textContent=modelLabel();
  $('#page-label').textContent=labels[page];
  updateWritingSurfaces();
  $$('[data-page]').forEach(b => b.classList.toggle('active',b.dataset.page===page));
}
async function reloadState(render = true) {
  const previousPerformance=performanceRevision(state.performance_records);
  state = await api('/api/state');
  ['articles','cards','drafts','jobs','performance_records','writing_runs'].forEach(k => {if(!Array.isArray(state[k])) state[k]=[];});
  if(previousPerformance!==performanceRevision(state.performance_records))performanceReview=null;
  updateChrome();
  if (render && !settingsDirty && !(page==='notes' && noteSession)) renderPage();
}
function pageHeading(kicker,title,description,actions='') {return `<div class="page-heading"><div><div class="page-kicker">${kicker}</div><h1>${title}</h1><p>${description}</p></div>${actions ? `<div class="header-actions">${actions}</div>` : ''}</div>`;}
function emptyState(name,title,description,button='') {return `<div class="empty-state"><div class="empty-icon">${icon(name)}</div><h3>${title}</h3><p>${description}</p>${button}</div>`;}
function hasUnsaved() {return settingsDirty || (page==='notes' && noteSession?.dirty);}
async function navigate(next) {
  if (!labels[next]) next='home';
  if (next!==page && hasUnsaved() && !confirm('当前页面有尚未保存的修改。离开会放弃这些修改，确定继续吗？')) return;
  if (next!==page) {settingsDirty=false;if(page==='notes'){noteSession=null;noteConflict=null;}}
  page=next;history.replaceState(null,'',`#${page}`);updateChrome();renderPage();window.scrollTo(0,0);
  if (page==='notes') await pollNotes();
  if (page==='performance') await loadPerformance();
  if (page==='home') await loadWritingRuns();
}
function renderPage() {
  const renderers={topics:renderTopicBoard,home:renderHome,articles:renderArticles,cards:renderCards,notes:renderNotes,drafts:renderDrafts,performance:renderPerformance,settings:renderSettings};
  $('#main').innerHTML=renderers[page]();
  if(page==='notes') updateNoteIndicators();
}

function renderHome() {
  const approved=state.cards.filter(c=>c.approved), noteCount=chars(state.notes.text);
  return `<section class="hero"><div><div class="eyebrow">INDUSTRY CONTENT STUDIO</div><h1>让行业经验，<br>长成<span style="color:#769367">值得读的文章。</span></h1><p>先把事实与角度想清楚，再写一篇有你观点的公众号文章。</p></div><div class="hero-badge">${icon('spark')} 汽车与车灯产业 · 专属写作空间</div></section>
  <div class="workflow" aria-label="工作流程"><span class="workflow-item"><span class="workflow-number">01</span> 收集好文章</span><span class="workflow-arrow"></span><span class="workflow-item"><span class="workflow-number">02</span> 拆解好写法</span><span class="workflow-arrow"></span><span class="workflow-item"><span class="workflow-number">03</span> 加入你的思考</span><span class="workflow-arrow"></span><span class="workflow-item active"><span class="workflow-number">04</span> 写一篇新文章</span></div>
  <div class="home-grid"><section class="panel"><div class="panel-header"><div class="panel-heading">${icon('note')}<h2>今天，想写点什么？</h2></div><button class="text-button writing-policy-entry" type="button" data-action="writing-policy">${icon('note')} 写作规范</button></div>
  <form id="compose-form" class="composer-body"><label class="form-label" for="brief">文章主题与写作要求</label><textarea id="brief" name="brief" class="topic-input" required placeholder="例如：ADB 车灯越来越普及，整灯厂和光学供应商的竞争，正在发生哪些变化？\n\n写下你的观点、想讨论的问题，或希望读者带走的结论。">${esc(composer.brief)}</textarea>
  <div class="suggestions"><button class="suggestion" type="button" data-topic="围绕 ADB 车灯普及，分析整灯厂与光学供应商的机会、挑战与协同方式。区分已验证事实与趋势判断。">ADB 与智能车灯</button><button class="suggestion" type="button" data-topic="从汽车车灯产业链视角，讨论降本压力下，整灯厂和上游供应商如何在成本、质量和技术差异化之间取舍。">供应链与降本</button><button class="suggestion" type="button" data-topic="面向车灯产业链从业者，讨论新材料与光学设计如何影响车灯性能、可靠性与量产。明确技术边界，避免营销夸大。">材料与光学创新</button></div>
  <div class="field"><label class="form-label" for="audience">写给谁看</label><input id="audience" name="audience" value="${esc(composer.audience)}" required></div>
  <div class="form-row"><div><label class="form-label" for="tone">文章语气</label><select id="tone" name="tone">${['专业、有观点，兼顾行业深度与可读性','深入浅出，用具体问题讲清技术','克制客观，适合产业观察','鲜明有力，适合观点评论'].map(x=>`<option${composer.tone===x?' selected':''}>${x}</option>`).join('')}</select></div><div><label class="form-label" for="length">建议篇幅</label><select id="length" name="length">${['约1500字','800—1200 字','1500—2000 字','2500—3000 字'].map(x=>`<option${composer.length===x?' selected':''}>${x}</option>`).join('')}</select></div></div>
  <div class="reference-box"><div class="reference-head"><label class="form-label" style="margin:0">参考素材 <small>按需选择</small></label><button type="button" class="text-button" data-page="articles">去素材库 ${icon('arrow')}</button></div>
  ${state.articles.length?`<div class="reference-list">${state.articles.map(a=>`<label class="check-row"><input type="checkbox" name="article_ids" value="${esc(a.id)}"${composer.article_ids.includes(String(a.id))?' checked':''}><span>${esc(a.title)}${a.content_kind==='snippet'?' <span class="tag orange">仅搜索摘要</span>':''}</span></label>`).join('')}</div>`:`<div class="reference-empty">${icon('book')} 添加第一篇文章，让写作有据可依。</div>`}
  <div class="reference-note">${icon('check')} 每次读取最新笔记 · 参考 ${Math.min(approved.length,12)} 张已启用卡片${approved.length>12?'（按添加时间取最近 12 张）':''}</div><div class="help" style="font-size:9px">最多选择 8 篇素材；开启联网时最多选择 5 篇。</div></div>
  <div class="composer-bottom"><label class="toggle-row"><span class="toggle"><input type="checkbox" name="use_web"${composer.use_web?' checked':''}><span></span></span><span class="toggle-text">联网补充资料</span></label><span class="toggle-caption">${state.settings.tavily_configured?'Tavily 已配置':'免密试用 · 有额度限制'}</span></div>
  <div class="composer-images"><div class="composer-images-row"><label class="toggle-row"><span class="toggle"><input type="checkbox" name="find_images"${composer.find_images?' checked':''}><span></span></span><span class="toggle-text">成稿后找配图</span></label><label class="image-count-label" for="compose-image-count">建议数量<select id="compose-image-count" name="image_count"${composer.find_images?'':' disabled'}>${imageCountOptions(composer.image_count)}</select></label></div><p>联网查找真实图片，提供来源、图注与建议位置。</p></div>
  <button class="button primary full" type="submit">${icon('book')} 整理事实与提纲 ${icon('arrow')}</button><div class="writing-two-steps"><span><b>1</b> 整理事实与提纲</span>${icon('arrow')}<span><b>2</b> 你确认后，生成并审核</span></div><p class="fine-print">${state.settings.provider==='codex'?`${esc(modelLabel())}<br>${esc(modelPrivacy('codex'))}`:state.settings.provider==='ollama'?'使用本机模型生成，耗时取决于文章长度与电脑性能':'使用你配置的云端 API 生成，所选资料将发送至该服务'}</p></form></section>
  <aside class="desk-column">${renderQuickTopics()}<div class="panel desk-panel"><div class="panel-header"><div><h2 style="font-size:14px">你的写作底座</h2><span class="small-kicker">COLLECT. THINK. CREATE.</span></div>${icon('folder')}</div><div class="foundation">${foundationRow('book','行业素材','值得参考的文章与资料',state.articles.length,'articles')}${foundationRow('cards','可用写法','经过你审核的表达与结构',approved.length,'cards')}${foundationRow('file','文章草稿','每一次积累，都留下来',state.drafts.length,'drafts')}</div></div>
  <div class="notebook-teaser"><span class="corner-mark">”</span><div class="panel-heading">${icon('note')}<h2>你的思考，始终在场</h2></div><p>把观察、判断和一闪而过的灵感，记在同一份 Markdown 笔记里。每次写作，都会重新读取。</p><button class="text-button" data-page="notes">打开我的思考 ${icon('arrow')}</button><div class="help" style="font-size:8px;margin-top:9px">本地文件 · ${noteCount.toLocaleString()} 字 · 保存后同步</div></div>
  <div class="principle"><div class="principle-title">${icon('check')} 参考好文章，写出自己的判断</div><p>提炼标题、开头与结构方法。公开热度逐项记录来源，未公开的数据保持未知。</p></div></aside></div><section id="writing-runs" class="writing-runs-section">${renderWritingRuns()}</section>`;
}
function foundationRow(i,title,desc,count,next){return `<div class="foundation-row"><span class="foundation-icon">${icon(i)}</span><div class="foundation-main"><strong>${title}</strong><small>${desc}</small></div><b>${count}</b><button data-page="${next}" aria-label="打开${title}">${icon('arrow')}</button></div>`;}

function topicHistoryHTML(){return topicBatches.length?`<div class="topic-history">${topicBatches.map(batch=>`<button type="button" data-topic-batch="${esc(batch.id)}"><span>${esc(fmtDate(batch.created_at))} 导入</span><small>${Array.isArray(batch.topics)?batch.topics.length:0} 个选题</small>${icon('arrow')}</button>`).join('')}</div>`:'<p class="help">还没有导入记录。粘贴真实的选题输出后，本机将保留原文与选题卡片。</p>';}
async function loadTopicBatches(){const result=await api('/api/topics');topicBatches=Array.isArray(result.batches)?result.batches:[];const history=$('#topic-import-history');if(modal?.type==='topic-import'&&history)history.innerHTML=topicHistoryHTML();return topicBatches;}
function openTopicImport(){
  showModal('从 Coze 选题导入',`<form id="topic-import-form"><div class="topic-bridge-notice"><span class="tag orange">手动导入 · 来源与事实未经核实</span><p>把 Coze 给出的选题输出粘贴到这里。本机只整理为选题卡片，选中后带入写作要求；不会自动生成文章。</p><div class="coze-entry-actions"><button type="button" class="text-button" data-action="coze-settings">配置 API 接入 ${icon('link')}</button><button type="button" class="text-button" data-coze-role="topics">从已配置 API 获取选题 ${icon('arrow')}</button></div></div><label class="form-label" for="topic-raw-content">Coze 选题输出 <small>文本或 JSON</small></label><textarea id="topic-raw-content" name="content" rows="10" required placeholder="在这里粘贴你从 Coze 获得的真实选题结果，保留原有标题、理由和参考链接。"></textarea><div class="modal-actions"><button class="button primary" type="submit">${icon('cards')} 整理为选题卡片</button></div></form><section class="topic-import-history"><h3>之前导入的选题</h3><div id="topic-import-history">${topicHistoryHTML()}</div></section>`,{wide:true,type:'topic-import'});
  loadTopicBatches().catch(e=>{if(modal?.type==='topic-import')toast(e.message,true);});
}
function showTopicBatch(batch){
  activeTopicBatch=batch;const topics=Array.isArray(batch.topics)?batch.topics:[];
  showModal('选择一个选题，交给写作助手',`<div class="topic-bridge-notice"><span class="tag">${batch.source==='coze_api'?'Coze API 返回':'Coze · 手动导入'} · 未经事实核实</span><p>挑选一个选题，可修改标题与写作方向。选择「用这个选题写稿」后确认读者与要求，再交给现成 Coze 文案助手；也可填入本地 B 写作。</p></div>${topics.length?`<form id="topic-select-form"><div class="topic-selection-grid">${topics.map((topic,i)=>`<article class="topic-selection-card"><label class="topic-radio"><input type="radio" name="topic_id" value="${esc(topic.id)}" required><span>选用这个选题</span><b>${String(i+1).padStart(2,'0')}</b></label><label class="form-label" for="topic-title-${i}">选题标题</label><input id="topic-title-${i}" name="title_${i}" value="${esc(topic.title)}" required><label class="form-label" for="topic-brief-${i}">写作方向</label><textarea id="topic-brief-${i}" name="brief_${i}" rows="4">${esc(topic.brief||'')}</textarea><label class="form-label" for="topic-reason-${i}">选题理由</label><textarea id="topic-reason-${i}" name="reason_${i}" rows="2">${esc(topic.reason||'')}</textarea>${Array.isArray(topic.source_urls)&&topic.source_urls.length?`<div class="topic-source-links"><span>输出中提供的来源 · 待核实</span>${topic.source_urls.map(safeURL).filter(Boolean).map(url=>`<a href="${esc(url)}" target="_blank" rel="noopener noreferrer">${esc(url)}</a>`).join('')}</div>`:''}</article>`).join('')}</div><div class="modal-actions"><button class="text-button" type="button" data-action="coze-settings">API 接入配置</button><button class="text-button" type="submit" name="destination" value="coze">高级 API 参数</button><button class="button secondary" type="submit" name="destination" value="local">填入本地 B 写作</button><button class="button primary" type="submit" name="destination" value="coze-quick">${icon('check')} 用这个选题写稿 ${icon('arrow')}</button></div></form>`:'<div class="writing-soft-note">这份输出没有可用选题，请返回粘贴完整结果后再导入。</div>'}<details class="topic-original"><summary>查看保留的原始输出</summary><textarea readonly rows="10" aria-label="Coze 原始输出">${esc(batch.raw||'')}</textarea></details>`,{wide:true,type:'topic-selection',id:batch.id});
  activeTopicBatch=batch;
}
async function reopenTopicBatch(id,button){
  if(modalDirty&&!confirm('当前粘贴内容尚未导入。确定放弃并打开之前的选题吗？'))return;
  await busy(button,async()=>{try{await loadTopicBatches();const batch=topicBatches.find(b=>String(b.id)===String(id));if(!batch)throw new Error('未找到这份选题，请重新导入。');showTopicBatch(batch);}catch(e){toast(e.message,true);}});
}
function selectedTopicBrief(fd,batch){
  const topics=Array.isArray(batch?.topics)?batch.topics:[],index=topics.findIndex(t=>String(t.id)===String(fd.get('topic_id')));
  if(index<0)throw new Error('请先选择一个选题。');const topic=topics[index],title=String(fd.get(`title_${index}`)||'').trim(),brief=String(fd.get(`brief_${index}`)||'').trim(),reason=String(fd.get(`reason_${index}`)||'').trim();
  if(!title)throw new Error('请为所选选题填写标题。');
  const urls=(Array.isArray(topic.source_urls)?topic.source_urls:[]).map(safeURL).filter(Boolean);
  const result=[title,brief&&brief!==title?brief:'',batch?.source==='coze_api'?'选题来源：Coze API 返回的选题内容（未经事实核实）。':'选题来源：手动导入的 Coze 选题内容（未经核实）。',reason?`选题理由（待核实）：${reason}`:'',urls.length?`待核实参考链接：\n${urls.join('\n')}`:''].filter(Boolean).join('\n\n');
  if(result.length>6000)throw new Error('所选选题内容超过 6,000 字符，请精简写作方向或理由后再使用。');return result;
}

function cozeSavedDraftId(job){return job?.draft_id||job?.result?.draft_id||job?.result?.draft?.id||'';}
function cozeActiveRoleJob(role){return cozeJobs().find(job=>job.type===`coze-${role}`&&(['running','pending','queued'].includes(job.status)||cozeCanResume(job)));}
function cozeRolePending(role){return cozeQuickStarts.has(role)||Boolean(cozeActiveRoleJob(role))||[...trackedJobs.values()].some(job=>job.action===`coze-${role}`);}
function quickTopicsStatus(){const job=cozeActiveRoleJob('topics');if(!job)return cozeQuickStarts.has('topics')?'<p class="coze-quick-state"><span class="spinner"></span>正在提交选题请求…</p>':'';return `<div class="coze-quick-state"><span>${esc(cozeJobLabel(job))}</span><button class="text-button" type="button" data-coze-output="${esc(job.id)}">查看任务 ${icon('arrow')}</button>${cozeCanResume(job)?`<button class="text-button" type="button" data-resume-coze-job="${esc(job.id)}">继续查询已有任务</button>`:''}</div>`;}
function renderQuickTopics(){return `<section class="coze-quick-topics"><div class="coze-quick-heading"><span>COZE ASSISTANTS</span><h3>先找一个值得写的选题。</h3></div><form id="coze-quick-topics-form"><label class="form-label" for="coze-quick-topic">研究方向 <small>选填</small></label><textarea id="coze-quick-topic" name="topic" rows="3" maxlength="2000" placeholder="例如：近期智能车灯技术与供应链变化">${esc(cozeQuickTopic)}</textarea><button id="coze-quick-topic-submit" class="button primary full" type="submit"${cozeRolePending('topics')?' disabled':''}>${icon('search')} 获取 Coze 选题</button><p class="coze-quick-caption">留空按助手默认方向推荐。调用会消耗 Coze 对应额度；完成后由你选题，不会自动写稿。</p><div id="coze-quick-topics-status">${quickTopicsStatus()}</div></form><div class="coze-quick-links"><button class="text-button" data-action="import-topics">粘贴选题结果</button><button class="text-button" data-action="coze-settings">接入与历史 ${icon('arrow')}</button></div></section>`;}
function refreshQuickTopics(){const status=$('#coze-quick-topics-status'),button=$('#coze-quick-topic-submit');if(status)status.innerHTML=quickTopicsStatus();if(button)button.disabled=cozeRolePending('topics');if(modal?.type==='coze-quick-writer'){const form=$('#coze-quick-writer-form'),submit=form&&$('button[type="submit"]',form),status=$('#coze-quick-writer-status');if(submit)submit.disabled=cozeRolePending('writer');if(status){const job=cozeActiveRoleJob('writer');status.innerHTML=job?`<div class="writing-attention">${esc(cozeJobLabel(job))}。当前输入保留，请先处理已有文案任务。<button class="text-button" type="button" data-coze-output="${esc(job.id)}">查看已有任务</button></div>`:'';}}}
function selectedCozeTopic(fd,batch){const index=(batch?.topics||[]).findIndex(t=>String(t.id)===String(fd.get('topic_id')));if(index<0)throw new Error('请先选择一个选题。');const title=String(fd.get(`title_${index}`)||'').trim();if(!title)throw new Error('请填写所选选题的标题。');return {batch_id:String(batch.id),topic_id:String(batch.topics[index].id),title,brief:String(fd.get(`brief_${index}`)||'').trim(),reason:String(fd.get(`reason_${index}`)||'').trim()};}
function openQuickWriter(selection){
  cozeQuickWriterSession=selection;
  showModal('用这个选题写稿',`<div class="coze-quick-writer-intro"><span class="tag">使用你现成的 Coze 文案助手</span><p>确认下面的选题与要求后再提交。仅发送本次选题，以及你在这里明确填写的观点和资料；不会自动附加私人笔记或本地写作规范。</p></div><form id="coze-quick-writer-form"><input type="hidden" name="batch_id" value="${esc(selection.batch_id)}"><input type="hidden" name="topic_id" value="${esc(selection.topic_id)}"><div class="field"><label class="form-label" for="quick-writer-title">选题标题</label><input id="quick-writer-title" name="title" maxlength="500" value="${esc(selection.title)}" required></div><div class="field"><label class="form-label" for="quick-writer-brief">写作方向</label><textarea id="quick-writer-brief" name="brief" rows="4" maxlength="6000">${esc(selection.brief)}</textarea></div><div class="form-row"><div class="field"><label class="form-label" for="quick-writer-readers">目标读者</label><input id="quick-writer-readers" name="target_readers" maxlength="1000" value="${esc(composer.audience||'整灯厂和上游供应商从业者')}" required></div><div class="field"><label class="form-label" for="quick-writer-count">建议字数</label><input id="quick-writer-count" name="word_count" type="number" step="1" min="500" max="5000" value="1500" required></div></div><div class="field"><label class="form-label" for="quick-writer-view">你的观点 <small>选填</small></label><textarea id="quick-writer-view" name="author_view" rows="3" maxlength="6000" placeholder="希望文章保留的判断或你真实观察到的现象"></textarea></div><div class="field"><label class="form-label" for="quick-writer-materials">补充资料 <small>选填</small></label><textarea id="quick-writer-materials" name="materials" rows="4" maxlength="24000" placeholder="在这里粘贴你明确希望发给 Coze 的资料或链接"></textarea></div>${selection.reason?`<details class="coze-quick-reason"><summary>选题理由 · 可调整</summary><label class="visually-hidden" for="quick-writer-reason">选题理由</label><textarea id="quick-writer-reason" name="reason" rows="3" maxlength="3000">${esc(selection.reason)}</textarea></details>`:''}<div id="coze-quick-writer-status">${cozeRolePending('writer')?'<p class="writing-attention">已有 Coze 文案任务正在处理，请等待或从接入与历史中查看。</p>':''}</div><p class="coze-quick-caption">提交会调用一次 Coze 文案助手并消耗对应额度。成功后保存为本地草稿，继续查看不会重复生成。</p><div class="modal-actions"><button class="button primary" type="submit"${cozeRolePending('writer')?' disabled':''}>${icon('spark')} 确认交给 Coze 写稿</button></div></form>`,{wide:true,type:'coze-quick-writer',id:selection.topic_id});
}
function quickWriterPayload(fd){
  const text=key=>String(fd.get(key)||'').trim(),wordCount=Number(fd.get('word_count'));
  if(!text('title'))throw new Error('请填写选题标题。');if(!text('target_readers'))throw new Error('请填写目标读者。');if(!Number.isInteger(wordCount)||wordCount<500||wordCount>5000)throw new Error('建议字数须为 500 至 5,000 的整数。');
  if(!cozeQuickWriterSession||text('batch_id')!==cozeQuickWriterSession.batch_id||text('topic_id')!==cozeQuickWriterSession.topic_id)throw new Error('选题状态已改变，请重新选择一个选题。');
  const payload={batch_id:text('batch_id'),topic_id:text('topic_id'),title:text('title'),brief:text('brief'),reason:fd.has('reason')?text('reason'):cozeQuickWriterSession.reason,word_count:wordCount,target_readers:text('target_readers'),author_view:text('author_view'),materials:text('materials')};
  for(const [key,label,limit] of [['title','标题',500],['brief','写作方向',6000],['reason','选题理由',3000],['target_readers','目标读者',1000],['author_view','作者观点',6000],['materials','补充资料',24000]]){if(payload[key].length>limit)throw new Error(`${label}超过 ${limit.toLocaleString()} 字符，请精简后提交；当前输入未截断。`);}
  return payload;
}
async function submitQuickCoze(role,payload,startingModal){
  if(cozeRolePending(role))throw new Error('这个助手已有任务正在处理，请先查看已有任务，避免重复生成。');
  cozeQuickStarts.add(role);refreshQuickTopics();
  try{const result=await api(`/api/coze/${role}/quick-run`,{method:'POST',body:payload});if(!result.job_id)throw new Error('服务没有返回任务编号。');if(modal===startingModal){if(modal)closeModal(true);showCozeOutput({id:result.job_id,type:`coze-${role}`,status:'running'});}await registerJob(result,`coze-${role}`);}finally{cozeQuickStarts.delete(role);refreshQuickTopics();}
}
function isCozeDraft(d){return Boolean(d?.coze_job_id||d?.context?.source==='coze_writer'||d?.context?.coze_job_id||d?.source_type==='coze');}
function cozeDraftNotice(d){return isCozeDraft(d)?'<div class="coze-draft-notice"><span class="tag orange">Coze 原稿 · 待人工审阅</span><p>由现成 Coze 文案助手返回并保存在本机，尚未经过本地 B 的独立事实与语言审核。请核对后再使用。</p></div>':'';}
async function openCozeSavedDraft(id,button){
  if(modalDirty&&!confirm('当前输入尚未保存。确定放弃并打开已保存草稿吗？'))return;
  const previousModal=modal;await busy(button,async()=>{try{if(!state.drafts.some(d=>String(d.id)===String(id)))await reloadState(false);if(modal!==previousModal)return;if(!state.drafts.some(d=>String(d.id)===String(id)))throw new Error('暂未找到已保存草稿，请刷新任务记录。');showDraft(id);}catch(e){toast(e.message,true);}});
}
async function importCozeDraft(id,button){
  const previousModal=modal;await busy(button,async()=>{try{await api(`/api/coze/jobs/${encodeURIComponent(id)}/import-draft`,{method:'POST',body:{}});await reloadState(false);const job=await api(`/api/jobs/${encodeURIComponent(id)}`);refreshCozeJob(job);const draftId=cozeSavedDraftId(job);if(!draftId)throw new Error('本次未取得草稿编号，原始返回仍保留。');toast('Coze 原稿已保存到本地，尚未经过本地 B 独立审核。');if(modal===previousModal&&!modalDirty)await openCozeSavedDraft(draftId);}catch(e){toast(e.message,true);}});
}

function cozeRoleLabel(role){return role==='writer'?'Coze 文案助手':'Coze 选题助手';}
function cozeRoleStatus(value={}){return !value.configured?'待接入':value.schema_fetched_at?'参数已读取':'已保存配置 · 待读取参数';}
function cozeOutputText(output){return typeof output==='string'?output:JSON.stringify(output??null,null,2);}
function cozeJobs(){return (state.jobs||[]).filter(j=>['coze-topics','coze-writer'].includes(j.type));}
function cozeHistoryHTML(){const jobs=cozeJobs();return jobs.length?`<div class="topic-history coze-job-history">${jobs.map(job=>`<div class="coze-history-row"><button type="button" data-coze-output="${esc(job.id)}"><span>${esc(cozeRoleLabel(job.type==='coze-writer'?'writer':'topics'))} · ${esc(fmtDate(job.created_at))}</span><small>${esc(cozeJobLabel(job))}${job.remote_task_id?`<em title="${esc(job.remote_task_id)}">任务 ${esc(String(job.remote_task_id).slice(0,14))}…</em>`:''}</small>${icon('arrow')}</button>${cozeSavedDraftId(job)?`<button class="text-button coze-history-draft" type="button" data-coze-saved-draft="${esc(cozeSavedDraftId(job))}">查看保存草稿</button>`:''}</div>`).join('')}</div>`:'<p class="help">尚无 API 执行记录。保存真实部署配置并读取参数后，才能人工发起执行。</p>';}
async function loadCozeSettings(){const result=await api('/api/coze/settings');cozeSettings=result.roles||{};return cozeSettings;}
function renderCozeSettings(){
  return `<div class="coze-intro"><span class="tag orange">可配置接入 · 需要真实部署 API</span><p>分享预览页不能直接调用。请从扣子编程的工作流部署页复制 API 基址与 Token，分别配置两个助手。保存只写入本机；读取参数会验证实际接口。</p></div><form id="coze-settings-form"><div class="coze-role-grid">${['topics','writer'].map(role=>{const value=cozeSettings?.[role]||{};return `<section class="coze-role-panel"><div class="coze-role-heading"><h3>${cozeRoleLabel(role)}</h3><span class="tag ${value.schema_fetched_at?'':'orange'}">${cozeRoleStatus(value)}</span></div><label class="form-label" for="coze-${role}-url">部署 API 基址</label><input id="coze-${role}-url" name="${role}_base_url" type="url" value="${esc(value.base_url||'')}" placeholder="https://你的部署子域.coze.site" autocomplete="off"><label class="form-label" for="coze-${role}-token">API Token <small>${value.token_configured?'已保存 · 留空保留':'尚未填写'}</small></label><input id="coze-${role}-token" name="${role}_token" type="password" autocomplete="new-password" placeholder="只填写 Token，不包含 Bearer" value="">${value.token_configured?`<label class="check-row coze-clear-token"><input name="${role}_clear_token" type="checkbox"><span>清除已保存的 Token</span></label>`:''}<p class="help">Token 只保存在本机，不会回显。更换地址时须重新填写 Token。</p><div class="coze-role-actions"><button class="button secondary small" type="button" data-coze-schema="${role}"${value.configured?'':' disabled'}>${icon('link')} ${value.schema_fetched_at?'重新读取参数':'读取真实参数'}</button><button class="text-button" type="button" data-coze-role="${role}"${value.configured&&value.schema_fetched_at?'':' disabled'}>填写并执行 ${icon('arrow')}</button></div>${value.schema?`<details class="coze-schema-preview"><summary>查看已读取的接口参数</summary><pre>${esc(cozeOutputText(value.schema))}</pre></details>`:''}</section>`;}).join('')}</div><div class="modal-actions"><button class="button primary" type="submit">${icon('check')} 保存 API 配置</button></div></form><section class="topic-import-history"><h3>API 执行记录</h3><div id="coze-history">${cozeHistoryHTML()}</div></section><div class="coze-modal-back"><button class="text-button" data-action="import-topics">${icon('arrow')} 返回手动导入</button></div>`;
}
async function openCozeSettings(button){
  if(modalDirty&&!confirm('当前输入尚未保存。确定放弃并打开 API 接入配置吗？'))return;
  await busy(button,async()=>{try{await loadCozeSettings();showModal('Coze API 接入',renderCozeSettings(),{wide:true,type:'coze-settings'});}catch(e){toast(e.message,true);}});
}
async function readCozeSchema(role,button){
  if(modalDirty){toast('请先保存 API 配置，再读取该配置的真实参数。',true);return;}
  await busy(button,async()=>{try{await api(`/api/coze/${role}/schema`,{method:'POST',body:{}});await loadCozeSettings();if(modal?.type==='coze-settings'&&!modalDirty)showModal('Coze API 接入',renderCozeSettings(),{wide:true,type:'coze-settings'});toast(`${cozeRoleLabel(role)}的真实参数已读取。`);}catch(e){toast(e.message,true);}});
}
function cozeSchemaFields(schema){
  const input=schema?.input_schema,properties=input?.properties;
  if(!input||typeof input!=='object'||Array.isArray(input)||!properties||typeof properties!=='object'||Array.isArray(properties))return null;
  if(input.oneOf||input.anyOf||input.allOf||input.$ref)return null;
  return Object.entries(properties).map(([name,spec],index)=>{spec=spec&&typeof spec==='object'&&!Array.isArray(spec)?spec:{};const types=Array.isArray(spec.type)?spec.type.filter(t=>t!=='null'):[spec.type];const type=types.length===1?types[0]:null;return {name,spec,index,type,required:Array.isArray(input.required)&&input.required.includes(name),simple:!spec.$ref&&!spec.oneOf&&!spec.anyOf&&!spec.allOf&&['string','number','integer','boolean'].includes(type)};});
}
function cozeInputField(field,topic){
  const {index,name,spec,type,required,simple}=field,id=`coze-param-${index}`,attrs=`id="${id}" name="param_${index}"${required?' required':''}`,label=spec.title&&spec.title!==name?`${spec.title} · ${name}`:name;
  let control;
  if(Array.isArray(spec.enum)&&spec.enum.length){control=`<select ${attrs}><option value="">${required?'请选择':'不传此参数'}</option>${spec.enum.map((value,i)=>`<option value="${i}">${esc(typeof value==='string'?value:cozeOutputText(value))}</option>`).join('')}</select>`;}
  else if(simple&&type==='boolean'){control=`<select ${attrs}><option value="">${required?'请选择':'不传此参数'}</option><option value="true">是 · true</option><option value="false">否 · false</option></select>`;}
  else if(simple&&['number','integer'].includes(type)){control=`<input ${attrs} type="number" step="${type==='integer'?'1':'any'}"${Number.isFinite(spec.minimum)?` min="${spec.minimum}"`:''}${Number.isFinite(spec.maximum)?` max="${spec.maximum}"`:''} placeholder="${type==='integer'?'整数':'数字'}">`;}
  else if(simple&&type==='string'){control=`<textarea ${attrs} rows="${topic?4:3}"${Number.isInteger(spec.maxLength)&&spec.maxLength>0?` maxlength="${spec.maxLength}"`:''} placeholder="按该接口参数填写"></textarea>${topic?`<button class="text-button coze-fill-topic" type="button" data-coze-fill="${index}">${icon('plus')} 将所选选题填入此参数</button>`:''}`;}
  else{control=`<textarea ${attrs} class="coze-json-input" rows="5" placeholder="填写此参数的 JSON 值，例如对象或数组"></textarea><small class="coze-complex-label">复杂参数 · 填写合法 JSON 值</small>`;}
  return `<div class="coze-parameter"><label class="form-label" for="${id}">${esc(label)} <small>${required?'必填':'选填'} · ${esc(type||'JSON')}</small></label>${spec.description?`<p class="help">${esc(spec.description)}</p>`:''}${control}</div>`;
}
function renderCozeRun(role,topic=''){
  const value=cozeSettings?.[role]||{},fields=cozeSchemaFields(value.schema);cozeSession={role,topic,schema:value.schema,fields};
  return `<div class="coze-intro"><span class="tag">${cozeRoleLabel(role)} · 参数已读取</span><p>下方参数来自当前部署的真实输入结构。核对后点击执行，填写的内容将发送至该 Coze 工作流。</p><small>${esc(value.base_url)}</small></div>${topic?`<details class="coze-selected-topic" open><summary>你选中的选题 · 尚未发送</summary><textarea id="coze-selected-topic" readonly rows="6">${esc(topic)}</textarea><p class="help">选择对应参数下的「填入选题」，或自行复制需要的部分。程序不会猜测文案助手需要哪个字段。</p></details>`:''}<form id="coze-run-form" data-role="${role}">${fields!==null?`<div class="coze-parameters">${fields.length?fields.map(field=>cozeInputField(field,topic)).join(''):'<p class="help">此接口没有声明输入参数；确认后将提交空对象。</p>'}</div>`:'<div class="writing-soft-note">此接口使用复杂输入结构，请根据下方真实 Schema，在高级 JSON 输入中填写整个 inputs 对象。</div>'}<details class="coze-advanced"${fields===null?' open':''}><summary>高级 JSON 输入与真实 Schema</summary><label class="check-row"><input type="checkbox" name="use_json" id="coze-use-json"${fields===null?' checked disabled':''}><span>使用完整 JSON 对象作为 inputs</span></label><textarea id="coze-inputs-json" name="inputs_json" rows="9" class="coze-json-input" placeholder='{"按真实参数名填写": "值"}'${fields===null?' required':' disabled'}>{}</textarea><pre>${esc(cozeOutputText(value.schema?.input_schema))}</pre></details><p class="coze-run-note">${role==='topics'?'执行成功后保留原始返回，并整理为可选择的选题卡片；不会自动写文章。':'执行成功后展示并保留外部工作流返回；结果尚未经过本地事实与语言审核。'}</p><div class="modal-actions"><button class="button secondary" type="button" data-action="coze-settings">返回 API 配置</button><button class="button primary" type="submit">${icon('spark')} ${role==='topics'?'确认执行并整理选题':'确认交给 Coze 文案助手'}</button></div></form>`;
}
async function openCozeRun(role,button,topic=''){
  if(!['topics','writer'].includes(role))return;
  if(modal?.type==='coze-settings'&&modalDirty){toast('请先保存 API 配置，再填写执行参数。',true);return;}
  if(!topic&&modalDirty&&!confirm('当前输入尚未保存。确定放弃并打开执行参数吗？'))return;
  await busy(button,async()=>{try{await loadCozeSettings();const value=cozeSettings[role];if(topic)cozePendingTopic=topic;if(role==='writer'&&!topic)topic=cozePendingTopic;if(!value?.configured||!value.schema_fetched_at||!value.schema){toast('此助手尚未完成 API 接入。请先保存部署地址与 Token，再读取真实参数。',true);return;}showModal(`${cozeRoleLabel(role)} · 确认输入`,renderCozeRun(role,topic),{wide:true,type:'coze-run',id:role});}catch(e){toast(e.message,true);}});
}
function cozeInputsPayload(fd,session=cozeSession){
  if(!session)throw new Error('输入参数已失效，请重新打开执行面板。');
  let inputs;
  if(fd.has('use_json')||session.fields===null){try{inputs=JSON.parse(String(fd.get('inputs_json')||''));}catch{throw new Error('高级输入不是合法 JSON，请检查逗号、引号与括号。');}if(!inputs||typeof inputs!=='object'||Array.isArray(inputs))throw new Error('inputs 必须是 JSON 对象，不能是数组、字符串或 null。');}
  else{inputs=Object.create(null);for(const field of session.fields){const raw=String(fd.get(`param_${field.index}`)??'');if(raw===''){if(field.required)throw new Error(`请填写必填参数 ${field.name}。`);continue;}let value;if(Array.isArray(field.spec.enum)&&field.spec.enum.length){const i=Number(raw);if(!Number.isInteger(i)||i<0||i>=field.spec.enum.length)throw new Error(`参数 ${field.name} 的选项无效。`);value=field.spec.enum[i];}else if(field.simple&&field.type==='string')value=raw;else if(field.simple&&field.type==='boolean'){if(!['true','false'].includes(raw))throw new Error(`参数 ${field.name} 必须为布尔值。`);value=raw==='true';}else if(field.simple&&['number','integer'].includes(field.type)){value=Number(raw);if(!raw.trim()||!Number.isFinite(value)||(field.type==='integer'&&!Number.isInteger(value)))throw new Error(`参数 ${field.name} 必须为${field.type==='integer'?'整数':'有效数字'}。`);}else{try{value=JSON.parse(raw);}catch{throw new Error(`参数 ${field.name} 不是合法 JSON。`);}}inputs[field.name]=value;}}
  const required=session.schema?.input_schema?.required;if(Array.isArray(required))for(const name of required){if(!Object.prototype.hasOwnProperty.call(inputs,name))throw new Error(`缺少真实 Schema 中的必填参数 ${name}。`);}
  if(new TextEncoder().encode(JSON.stringify(inputs)).length>1024*1024)throw new Error('输入超过 1 MiB，请精简后执行。');
  return inputs;
}
function syncCozeJSONMode(){const form=$('#coze-run-form');if(!form)return;const useJSON=$('#coze-use-json')?.checked||cozeSession?.fields===null;$$('.coze-parameters input,.coze-parameters textarea,.coze-parameters select,.coze-fill-topic',form).forEach(el=>el.disabled=useJSON);const input=$('#coze-inputs-json');if(input){input.disabled=!useJSON;input.required=useJSON;}}
function cozeLocalImportPending(job){const output=job?.result?.output;return Boolean(job?.type==='coze-writer'&&job.status==='error'&&job.draft_import_pending===true&&cozeRemoteState(job)==='completed'&&!job.remote_error&&job.result?.role==='writer'&&(typeof output==='string'?output.trim():output&&typeof output==='object'&&!Array.isArray(output)));}
function cozeHasRawResult(job){return job?.status==='done'||cozeLocalImportPending(job);}
function cozeWriterMarkdown(job){
  if(!cozeHasRawResult(job)||(job.type!=='coze-writer'&&job.result?.role!=='writer'))return '';
  let output=job.result?.output;if(typeof output==='string'){try{output=JSON.parse(output);}catch{return '';}}
  if(!output||typeof output!=='object'||Array.isArray(output))return '';
  return typeof output.markdown==='string'&&output.markdown.trim()?output.markdown:typeof output.article==='string'&&output.article.trim()?output.article:'';
}
function downloadCozeMarkdown(){
  const job=cozeShownJob,markdown=cozeWriterMarkdown(job);if(modal?.type!=='coze-output'||String(modal.id)!==String(job?.id)||!markdown){toast('此任务没有已保存在本机的 Markdown 正文。',true);return;}
  const url=URL.createObjectURL(new Blob([markdown],{type:'text/markdown;charset=utf-8'})),a=document.createElement('a');a.href=url;a.download=`Coze文案-${String(job.id).replace(/[^a-zA-Z0-9_-]/g,'').slice(0,16)||'正文'}.md`;document.body.append(a);a.click();a.remove();setTimeout(()=>URL.revokeObjectURL(url),5000);toast('已下载本机保留的 Markdown 正文。');
}
function cozeRemoteState(job){return String(job?.remote_status||'').toLowerCase();}
function cozeCanResume(job){return job?.status==='error'&&job.can_resume===true&&Boolean(job.remote_task_id)&&!['completed','failed','timeout'].includes(cozeRemoteState(job));}
function cozeJobLabel(job){if(job.status==='done')return '查看返回结果';if(cozeLocalImportPending(job))return '远端已完成 · 本地待保存';if(cozeCanResume(job))return '查询中断 · 可继续';const remote=cozeRemoteState(job);return ({pending:'远端排队中',running:'远端执行中',completed:'远端已完成',failed:'远端执行失败',timeout:'远端任务超时'})[remote]||({error:'执行未完成',running:'等待远端任务编号',queued:'排队中',pending:'排队中'})[job.status]||'查看任务';}
function cozeTimestamp(value){const date=new Date(value);return value&&Number.isFinite(+date)?date.toLocaleString('zh-CN',{year:'numeric',month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit',second:'2-digit',hour12:false}):String(value||'');}
function cozeRemoteProgress(job){
  const remote=cozeRemoteState(job),working=['running','pending','queued'].includes(job.status);
  return `<section class="coze-remote-progress"><div class="coze-progress-heading">${working?'<span class="spinner"></span>':icon('clock')}<strong>${esc(cozeJobLabel(job))}</strong></div>${job.message?`<p>${esc(job.message)}</p>`:''}${job.remote_task_id?`<dl><div class="coze-task-id"><dt>远端任务编号</dt><dd>${esc(job.remote_task_id)}</dd></div>${job.remote_submitted_at?`<div><dt>提交时间</dt><dd>${esc(cozeTimestamp(job.remote_submitted_at))}</dd></div>`:''}${job.remote_deadline?`<div><dt>远端任务期限</dt><dd>${esc(cozeTimestamp(job.remote_deadline))}</dd></div>`:''}${job.poll_expires_at?`<div><dt>本机继续查询截止</dt><dd>${esc(cozeTimestamp(job.poll_expires_at))}</dd></div>`:''}</dl>`:working?'<p>正在提交至已配置的工作流；获取远端编号后会保存在本机。</p>':''}${cozeCanResume(job)?`<div class="coze-resume-row"><button class="button primary small" data-resume-coze-job="${esc(job.id)}">${icon('clock')} 继续查询已有任务</button><p>只查询上方同一个远端任务，不会重新提交生成。</p></div>`:cozeLocalImportPending(job)?'<p>远端已完成，原始返回已保存在本机。可继续保存本地草稿，无需重新生成。</p>':job.status==='error'&&!job.remote_task_id?'<p>本次没有取得可恢复的远端任务编号。远端可能已受理，请先在 Coze 核对执行记录。</p>':job.status==='error'&&['completed','failed','timeout'].includes(remote)?'<p>远端任务已结束，不能继续查询生成进度。原有执行记录仍保留。</p>':job.status==='error'&&job.remote_task_id?'<p>当前任务不在可继续查询范围内；请查看错误说明与任务期限。</p>':''}${working?'<p class="coze-progress-help">可关闭窗口继续使用工作台，任务记录会保留；进度仅按远端返回的状态显示。</p>':''}</section>`;
}
function cozeDraftResultActions(job){
  if(!cozeHasRawResult(job)||job.type!=='coze-writer')return '';const id=cozeSavedDraftId(job),pending=cozeLocalImportPending(job);
  return id&&!pending?`<div class="coze-saved-draft"><div><strong>Coze 原稿已保存在本机</strong><p>尚未经过本地 B 独立审核，打开与编辑不会重复提交生成。</p></div><button class="button primary small" data-coze-saved-draft="${esc(id)}">查看保存草稿 ${icon('arrow')}</button></div>`:`${job.result?.draft_import_error?`<div class="writing-attention">本地保存草稿未完成：${esc(job.result.draft_import_error)}</div>`:''}<div class="coze-saved-draft"><p>${pending?'远端原稿已完成，本地保存因中断未完成。':'可将已有返回保存为本地原稿。'}只处理本机已保存的返回，不会再次调用 Coze 生成。</p><button class="button secondary small" data-import-coze-draft="${esc(job.id)}">${pending?'继续保存本地草稿':'保存为本地草稿'}</button></div>`;
}
function renderCozeOutput(job){
  const role=job.result?.role||(job.type==='coze-writer'?'writer':'topics'),done=cozeHasRawResult(job),pending=cozeLocalImportPending(job),result=job.result||{},text=cozeOutputText(result.output),baseURL=result.base_url||job.remote_base_url;
  return `<div class="coze-intro"><span class="tag ${job.status==='error'?'orange':''}">${pending?'远端已完成 · 本地保存待恢复':done?'真实 API 返回 · 未经事实核实':job.status==='error'?'查询或执行未完成':'Coze 长任务'}</span><p>${done?(role==='writer'?'这是外部 Coze 工作流返回的内容，尚未经过本地事实与语言审核。':'原始返回已保留。可整理为选题卡片，再由你选中进入写作。'):esc(job.error||'远端执行可能需要几分钟，请通过任务编号跟踪进度。')}</p>${baseURL?`<small>${esc(baseURL)}</small>`:''}</div>${cozeRemoteProgress(job)}${cozeDraftResultActions(job)}${done?`<textarea id="coze-output-raw" readonly rows="17">${esc(text)}</textarea>${cozeWriterMarkdown(job)?'<p class="help coze-markdown-help">Markdown 正文已随返回保存在本机，可直接下载，不依赖临时下载链接。</p>':''}<div class="modal-actions">${cozeWriterMarkdown(job)?`<button class="button primary" data-action="download-coze-markdown">${icon('file')} 下载文案 Markdown</button>`:''}<button class="button secondary" data-action="copy-coze-output">${icon('copy')} 复制原始返回</button>${role==='topics'?`<button class="button primary" data-import-coze-job="${esc(job.id)}">${icon('cards')} 整理为选题卡片</button>`:''}</div>`:`<div class="modal-actions"><button class="button secondary" data-coze-output="${esc(job.id)}">刷新本机任务状态</button></div>`}`;
}
function showCozeOutput(job){
  cozeShownJob=job;showModal(`${cozeRoleLabel(job.result?.role||(job.type==='coze-writer'?'writer':'topics'))} · ${cozeLocalImportPending(job)?'本地保存待恢复':job.status==='done'?'返回结果':'执行状态'}`,`<div id="coze-output-content">${renderCozeOutput(job)}</div>`,{wide:true,type:'coze-output',id:job.id});
}
function refreshCozeJob(job){
  state.jobs=[job,...(state.jobs||[]).filter(item=>String(item.id)!==String(job.id))];
  const history=$('#coze-history');if(history)history.innerHTML=cozeHistoryHTML();refreshQuickTopics();
  if(modal?.type!=='coze-output'||String(modal.id)!==String(job.id)||modalDirty)return;
  const target=$('#coze-output-content');if(target){cozeShownJob=job;target.innerHTML=renderCozeOutput(job);}
}
async function resumeCozeJob(id,button){
  const startingModal=modal;
  await busy(button,async()=>{try{
    const job=await api(`/api/jobs/${encodeURIComponent(id)}`);
    if(!['coze-topics','coze-writer'].includes(job.type)||!cozeCanResume(job))throw new Error('此任务当前不能继续查询。请刷新任务状态，查看远端状态与期限。');
    const result=await api(`/api/coze/jobs/${encodeURIComponent(id)}/resume`,{method:'POST',body:{}});
    if(String(result.job_id)!==String(id))throw new Error('恢复请求没有返回原任务编号，请刷新查看状态。');
    await registerJob(result,job.type,{resumed:true,remoteTaskId:job.remote_task_id});
    if(modal===startingModal&&!modalDirty&&modal?.type==='coze-output'&&String(modal.id)===String(id))refreshCozeJob(await api(`/api/jobs/${encodeURIComponent(id)}`));
  }catch(e){toast(e.message,true);}});
}
async function openCozeOutput(id,button){
  if(modalDirty&&!confirm('当前输入尚未保存。确定放弃并打开 API 执行结果吗？'))return;
  await busy(button,async()=>{try{const job=await api(`/api/jobs/${encodeURIComponent(id)}`);if(!['coze-topics','coze-writer'].includes(job.type))throw new Error('这不是 Coze 执行任务。');refreshCozeJob(job);showCozeOutput(job);}catch(e){toast(e.message,true);}});
}
async function importCozeJob(job,open=true){
  const startingModal=modal;
  const output=job.result?.output;if(job.status!=='done'||job.type!=='coze-topics'||output===undefined)throw new Error('Coze 选题任务尚未成功返回，不能生成选题卡片。');
  const result=await api('/api/topics/import',{method:'POST',body:{source:'coze_api',coze_job_id:String(job.id)}});
  if(!result.batch)throw new Error('没有收到选题解析结果；原始 API 返回仍可在执行记录中查看。');topicBatches=[result.batch,...topicBatches.filter(b=>String(b.id)!==String(result.batch.id))];if(open&&modal===startingModal&&!modalDirty)showTopicBatch(result.batch);return result.batch;
}

function metricNumber(value) {
  if(value===null||value===undefined||String(value).trim()==='')return null;
  const s=String(value).trim().replace(/,/g,'').replace(/＋/g,'+');
  const m=s.match(/^(\d+(?:\.\d+)?)\s*(亿|万|[kKmM])?\+?$/);if(!m)return null;
  const factor={'亿':1e8,'万':1e4,k:1e3,K:1e3,m:1e6,M:1e6};return Number(m[1])*(factor[m[2]]||1);
}
function hasMetrics(a){return ['reads','likes','favorites'].some(k=>metricNumber(a.metrics?.[k])!==null);}
function visibleArticles() {
  let list=state.articles.filter(a=>(`${a.title} ${a.content} ${(a.tags||[]).join(' ')}`).toLowerCase().includes(articleFilter.query.toLowerCase()));
  if(articleFilter.heat==='known') list=list.filter(hasMetrics);
  if(articleFilter.heat==='unknown') list=list.filter(a=>!hasMetrics(a));
  return list.sort((a,b)=>{if(articleFilter.sort==='newest')return new Date(b.created_at)-new Date(a.created_at);const x=metricNumber(a.metrics?.[articleFilter.sort]),y=metricNumber(b.metrics?.[articleFilter.sort]);if(x===null&&y===null)return 0;if(x===null)return 1;if(y===null)return -1;return y-x;});
}
function renderArticles() {
  return `${pageHeading('SOURCE LIBRARY','把好内容，变成自己的积累。','收集行业文章、补充真实热度，再拆解值得复用的写法。',`<button class="button secondary" data-action="research">${icon('search')} 搜索产业文章</button><button class="button primary" data-action="add-article">${icon('plus')} 添加素材</button>`)}
  <div class="search-banner">${icon('globe')}<div><h3>寻找有价值的产业文章，从真实来源开始</h3><p>搜索结果按相关性返回；阅读、点赞与收藏需以原文或你的记录核验。</p></div><button class="button secondary small" data-action="research">去搜索 ${icon('arrow')}</button></div>
  <div class="article-toolbar"><div class="search-input-wrap">${icon('search')}<input id="library-filter" placeholder="搜索已收藏的文章…" value="${esc(articleFilter.query)}" aria-label="搜索素材库"></div><select id="heat-filter" aria-label="热度筛选"><option value="all"${articleFilter.heat==='all'?' selected':''}>全部热度</option><option value="known"${articleFilter.heat==='known'?' selected':''}>有热度记录</option><option value="unknown"${articleFilter.heat==='unknown'?' selected':''}>热度待核验</option></select><select id="article-sort" aria-label="素材排序"><option value="newest"${articleFilter.sort==='newest'?' selected':''}>最近收藏</option><option value="reads"${articleFilter.sort==='reads'?' selected':''}>阅读从高到低</option><option value="likes"${articleFilter.sort==='likes'?' selected':''}>点赞从高到低</option><option value="favorites"${articleFilter.sort==='favorites'?' selected':''}>收藏从高到低</option></select></div><div id="article-results">${articleResults()}</div>`;
}
function articleResults(){const list=visibleArticles();return list.length?`<div class="article-grid">${list.map(articleCard).join('')}</div>`:emptyState('book',state.articles.length?'还没有符合条件的素材':'你的第一篇好文章，值得留下来。',state.articles.length?'试试其他关键词，或切换热度筛选。':'粘贴文章正文、上传文件，或搜索汽车与车灯产业资料。从这里开始建立你的内容资料库。',state.articles.length?'':`<button class="button secondary" data-action="add-article">${icon('plus')} 添加第一篇素材</button>`);}
function metricLabel(v){return v!==undefined&&v!==null&&String(v).trim()!=='' ? esc(v) : '未知';}
function articleCard(a){return `<article class="article-card"><div class="article-card-top"><span class="tag">${{manual:'手动收藏',upload:'文件导入',web:'网络来源'}[a.source_type]||'我的素材'}</span>${a.content_kind==='snippet'?'<span class="tag orange">仅搜索摘要</span>':''}${a.analysis?'<span class="tag">已拆解</span>':''}${!hasMetrics(a)?'<span class="tag gray">热度待核验</span>':''}</div><button class="article-title" data-article="${esc(a.id)}">${esc(a.title)}</button><p class="article-excerpt">${esc(a.content)}</p><div class="article-metrics"><span>阅读 <b>${metricLabel(a.metrics?.reads)}</b></span><span>点赞 <b>${metricLabel(a.metrics?.likes)}</b></span><span>收藏 <b>${metricLabel(a.metrics?.favorites)}</b></span></div><div class="article-card-bottom"><small>${esc(a.author||'作者未记录')} · 收藏于 ${esc(fmtDate(a.created_at))}</small><button class="text-button" data-article="${esc(a.id)}">查看素材 ${icon('arrow')}</button></div></article>`;}

function renderCards(){const approved=state.cards.filter(c=>c.approved).length;return `${pageHeading('WRITING METHODS','好写法，值得反复用。',`把文章拆解为可复用的方法。审核后启用，写作时自动参考。当前已启用 ${approved} 张。`)}<p class="subtle-note">卡片来自实际文章拆解：标题逻辑、开头方法、叙事结构与表达技巧。它们提供写法，不授权复制原文或挪用作者经历。</p>${state.cards.length?`<div class="cards-grid">${state.cards.map(c=>`<article class="article-card method-card"><div class="card-head"><span class="tag">${esc(c.category||'写作方法')}</span><span class="tag ${c.approved?'':'orange'}">${c.approved?'已启用':'待你审核'}</span></div><h3>${esc(c.title)}</h3><p>${esc(c.content)}</p><div class="method-source">来源：${esc(c.source_title||'已收藏文章')}</div><div class="method-approval"><button class="text-button" data-edit-card="${esc(c.id)}">编辑方法</button><button class="button ${c.approved?'secondary':'primary'} small" data-approve-card="${esc(c.id)}" data-approved="${!c.approved}">${icon(c.approved?'close':'check')} ${c.approved?'停用卡片':'审核并启用'}</button></div></article>`).join('')}</div>`:emptyState('cards','还没有写法卡片','添加一篇完整文章，点击「拆解文章」。AI 会提炼标题、开头和结构方法，等待你审核后再用于写作。',`<button class="button secondary" data-page="articles">去素材库 ${icon('arrow')}</button>`)}`;}

function renderNotes(){if(!noteSession)noteSession={text:state.notes.text||'',revision:state.notes.revision,path:state.notes.path,dirty:false};return `${pageHeading('YOUR OWN VOICE','好文章里，应该有你的思考。','一份可以持续编辑的本地 Markdown 笔记。你的观察与判断，是写作最重要的参考。')}<section class="panel"><div class="notes-toolbar"><div><div class="notes-file">${icon('file')} 我的思考.md</div><p class="notes-path">${esc(noteSession.path)}</p></div><div class="header-actions"><button class="button secondary small" data-action="open-notes">${icon('folder')} 用本地编辑器打开</button><button class="button primary small" data-action="save-notes">${icon('check')} 保存笔记</button></div></div><div id="note-conflict"${noteConflict?'':' hidden'}></div><label class="visually-hidden" for="notes-editor">我的思考 Markdown 编辑区</label><textarea id="notes-editor" class="editor" spellcheck="false">${esc(noteSession.text)}</textarea><div class="editor-footer"><span id="note-status" class="save-indicator">已同步到本地文件</span><span id="note-count" class="word-count">${chars(noteSession.text)} 字</span></div></section><div class="notes-help">${icon('clock')}<span>保存后供下次写作读取。用 Typora、Obsidian 等编辑同一个文件时，这里每 5 秒检查变化；遇到同时编辑会提示合并，不会直接覆盖。可在「模型与设置」选择你已有的 .md 笔记。</span></div>`;}
function updateNoteIndicators(){if(!noteSession||page!=='notes')return;const status=$('#note-status');if(status){status.textContent=noteSession.dirty?'有未保存的修改':'已同步到本地文件';status.classList.toggle('dirty',noteSession.dirty);}if($('#note-count'))$('#note-count').textContent=`${chars(noteSession.text).toLocaleString()} 字`;const conflict=$('#note-conflict');if(conflict){conflict.hidden=!noteConflict;if(noteConflict){conflict.className='conflict-banner';conflict.innerHTML=`本地文件已被另一编辑器修改。请对照磁盘版本合并，或加载它；当前输入仍保留在编辑区。<details><summary>查看磁盘新版本</summary><pre style="white-space:pre-wrap;max-height:180px;overflow:auto">${esc(noteConflict.text)}</pre></details><button class="button secondary small" data-action="reload-notes">加载磁盘版本</button><button class="button secondary small" data-action="merge-notes">我已合并，保存当前内容</button>`;}}}
async function pollNotes(){if(notesPollBusy)return;notesPollBusy=true;try{const latest=await api('/api/notes');state.notes=latest;if(page==='notes'&&noteSession&&latest.revision!==noteSession.revision){if(noteSession.dirty){noteConflict=latest;}else{noteSession={...latest,dirty:false};const ed=$('#notes-editor');if(ed)ed.value=latest.text;noteConflict=null;}updateNoteIndicators();}}catch(e){if(page==='notes'&&$('#note-status')){$('#note-status').textContent=`同步失败：${e.message}`;$('#note-status').classList.add('dirty');}}finally{notesPollBusy=false;}}
async function saveNotes(button, merged=false){if(!noteSession)return;const editingSession=noteSession;const submittedText=noteSession.text;await busy(button,async()=>{try{const revision=merged&&noteConflict?noteConflict.revision:editingSession.revision;const result=await api('/api/notes',{method:'PUT',body:{text:submittedText,revision}});state.notes=result;if(noteSession===editingSession){noteSession.revision=result.revision;noteSession.path=result.path;noteSession.dirty=noteSession.text!==submittedText;noteConflict=null;updateNoteIndicators();toast(noteSession.dirty?'已保存提交时的笔记，之后新增的编辑仍待保存。':'笔记已保存，下次写作将读取最新内容。');}else toast('笔记已保存。');}catch(e){if(e.status===409){await pollNotes();toast('文件已在其他编辑器中更新，请先合并两份修改。',true);}else toast(e.message,true);}});}

function renderDrafts(){return `${pageHeading('DRAFTS & IDEAS','每一篇草稿，都是新的起点。','修改文字、核对来源，再把 Markdown 草稿带到公众号编辑器。',`<button class="button primary" data-page="home">${icon('plus')} 写新文章</button>`)}${state.drafts.length?`<div class="draft-list">${state.drafts.map(d=>`<article class="draft-row">${icon('file')}<div><h3>${esc(d.title)}</h3><small>${esc(fmtDate(d.created_at))} · ${chars(d.content).toLocaleString()} 字 · ${esc(d.model||'模型未记录')}${draftImageItems(d).length?` · 已配 ${draftImageItems(d).length} 张`:''}</small></div><button class="button secondary small" data-draft="${esc(d.id)}">打开草稿 ${icon('arrow')}</button></article>`).join('')}</div>`:emptyState('file','你的下一篇文章，从这里开始。','从工作台输入主题，选择参考素材，再让你的积累和观点进入文章。生成的草稿会自动保存在这里。',`<button class="button secondary" data-page="home">开始写作 ${icon('arrow')}</button>`)}`;}
function renderSettings(){const s=state.settings;return `${pageHeading('SETTINGS','把工具，调成顺手的样子。','选择模型、连接网络搜索，并指定你每天打开的那一份笔记。',`<button class="button secondary" data-action="writing-policy">${icon('note')} 写作规范</button>`)}<form id="settings-form"><div class="settings-grid"><section class="panel settings-panel"><h2>写作模型</h2><p class="help">使用本机 Codex 会员账号调用 GPT-6 Astra，也可选择 Ollama 本地模型或兼容 OpenAI 的 API。</p><div class="field"><label class="form-label" for="provider">模型来源</label><select id="provider" name="provider"><option value="codex"${s.provider==='codex'?' selected':''}>Codex 会员 · 使用本机登录</option><option value="ollama"${s.provider==='ollama'?' selected':''}>Ollama · 本地模型</option><option value="openai"${s.provider==='openai'?' selected':''}>OpenAI 兼容 API · 云端模型</option></select></div><div class="field" id="base-url-field"${s.provider==='codex'?' hidden':''}><label class="form-label" for="base_url">服务地址</label><input id="base_url" name="base_url" value="${esc(s.base_url||'http://127.0.0.1:11434')}" placeholder="http://localhost:11434"${s.provider==='codex'?' disabled':' required'}></div><div class="field"><label class="form-label" for="model">模型名称</label><input id="model" name="model" value="${esc(s.model)}" required placeholder="${s.provider==='codex'?'gpt-6-astra':s.provider==='ollama'?'qwen2.5:latest':'填写服务商提供的模型名称'}"><div style="margin-top:10px"><button type="button" class="button subtle small" data-action="use-codex-gpt6">${icon('spark')} GPT-6 Astra · Codex 会员</button></div><p class="help" id="model-preset-help">${s.provider==='codex'?'GPT-6 Astra 使用本机已登录的 Codex 账号；保存后可测试模型。':'点击预设可切换到 Codex 会员并使用 GPT-6 Astra。'}</p></div><div class="field" id="api-key-field"${s.provider==='codex'?' hidden':''}><label class="form-label" for="api_key">API 密钥 <small>${s.api_key_configured?'已保存；留空保持不变':'本地 Ollama 无需填写'}</small></label><input id="api_key" name="api_key" type="password" autocomplete="new-password"${s.provider==='codex'?' disabled':''} placeholder="${s.api_key_configured?'••••••••（已配置）':'仅使用云端服务时填写'}"></div><div class="inline-notice" id="model-privacy">${esc(modelPrivacy(s.provider))}</div><div class="settings-footer"><button class="button primary" type="submit">${icon('check')} 保存设置</button><button type="button" class="button secondary" data-action="test-model">测试已保存的模型</button></div></section><div style="display:flex;flex-direction:column;gap:20px"><section class="panel settings-panel"><h2>联网搜索</h2><p class="help">通过 Tavily 搜索真实网页。微信文章可能受访问限制，可粘贴原文补充完整内容。</p><div class="field"><label class="form-label" for="tavily_api_key">Tavily API 密钥 <small>可选</small></label><input id="tavily_api_key" name="tavily_api_key" type="password" autocomplete="new-password" placeholder="${s.tavily_configured?'••••••••（已配置，留空保持）':'留空可免密试用，额度有限'}"><p class="help">${s.tavily_configured?'已配置个人密钥，留空保留现有配置。':'未配置个人密钥 · 免密试用有额度限制。额度不足时请填入自己的密钥。'}</p></div></section><section class="panel settings-panel"><h2>你的长期笔记</h2><p class="help">默认使用工作台的数据文件。也可填写已有 Markdown 文件的绝对路径，连接你正在用的笔记。</p><div class="field"><label class="form-label" for="notes_path">Markdown 文件路径</label><input id="notes_path" name="notes_path" value="${esc(s.notes_path||'')}" placeholder="留空使用默认的「我的思考.md」"><p class="help">当前文件：${esc(state.notes.path)}<br>选择已有文件只切换读取位置，不覆盖文件内容。</p></div></section><section class="panel settings-panel"><h2>公众号定位</h2><p class="help">每次写作都会参考这段长期定位。</p><label class="visually-hidden" for="profile">公众号定位</label><textarea id="profile" name="profile" rows="5">${esc(s.profile||'为汽车与车灯上下游公众号，面向整灯厂和上游供应商从业者。专业、具体、有判断，重视技术边界与产业价值。')}</textarea></section></div></div><div id="settings-error"></div></form>`;}

function performanceRevision(records){return (Array.isArray(records)?records:[]).map(r=>`${r.id}:${r.revision}`).sort().join('|');}
function performanceValue(value,digits=0){return typeof value==='number'&&Number.isFinite(value)?esc(value.toLocaleString('zh-CN',{maximumFractionDigits:digits})):'<span class="performance-unknown">未知</span>';}
function performanceDate(value){if(!value)return '<span class="performance-unknown">未提供</span>';const d=new Date(value);return Number.isNaN(+d)?esc(value):esc(d.toLocaleString('zh-CN',{year:'numeric',month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit',hour12:false}));}
function localDateInput(value){if(!value)return '';const date=new Date(value);if(Number.isNaN(+date))return '';const pad=n=>String(n).padStart(2,'0');return `${date.getFullYear()}-${pad(date.getMonth()+1)}-${pad(date.getDate())}T${pad(date.getHours())}:${pad(date.getMinutes())}:${pad(date.getSeconds())}${date.getMilliseconds()?'.'+String(date.getMilliseconds()).padStart(3,'0'):''}`;}
function performanceInput(value){return value===null||value===undefined?'':String(value);}
async function loadPerformance(){
  if(performanceLoading)return;performanceLoading=true;
  try{const result=await api('/api/performance'),records=Array.isArray(result.records)?result.records:[];
    if(performanceRevision(records)!==performanceRevision(state.performance_records))performanceReview=null;
    state.performance_records=records;updateChrome();if(page==='performance')renderPage();
  }catch(e){toast(`复盘记录读取失败：${e.message}`,true);}finally{performanceLoading=false;}
}
function renderPerformance(){
  const records=state.performance_records||[],articleCount=new Set(records.map(r=>r.article_id)).size;
  const unknownCount=records.reduce((n,r)=>n+Object.keys(performanceMetrics).filter(k=>r.metrics?.[k]===null||r.metrics?.[k]===undefined).length,0);
  return `${pageHeading('D / PERFORMANCE REVIEW','让每次发布，都留下经验。','把实际发布、读者反馈与修改过程放在一起，逐步找到值得继续尝试的写法。',`<button class="button secondary" data-action="import-performance">${icon('upload')} 导入数据</button><button class="button primary" data-action="add-performance">${icon('plus')} 新增统计记录</button>`)}
  <div class="performance-summary"><div><span>统计快照</span><strong>${records.length}</strong><small>每篇文章可记录多个时点</small></div><div><span>已记录文章</span><strong>${articleCount}</strong><small>按文章标识去重</small></div><div><span>未提供的指标项</span><strong>${unknownCount}</strong><small>空值保持未知，真实 0 单独保留</small></div></div>
  <div class="performance-records-header"><div><h2>发布与反馈记录</h2><p>每条记录对应一篇文章、一个统计时点。新增统计保留历史，编辑用于修正当前记录。</p></div><div class="performance-export"><a href="/api/performance/template.csv" class="text-button" download>下载 CSV 模板</a><span>·</span><a href="/api/export/performance.csv" class="text-button" download>导出 CSV</a><span>·</span><a href="/api/export/performance.json" class="text-button" download>导出 JSON</a></div></div>
  ${records.length?`<div class="performance-search search-input-wrap">${icon('search')}<input id="performance-filter" value="${esc(performanceQuery)}" placeholder="搜索标题、文章标识或选题类型…" aria-label="搜索复盘记录"></div>`:''}<div id="performance-records">${performanceRecords()}</div>
  <section class="performance-review panel"><div class="performance-review-heading"><div><div class="page-kicker">OBSERVE · LEARN · TRY</div><h2>从数据里，找到下一次尝试。</h2><p>基于已录入数据，不自动训练模型。仅在本机用规则整理事实、局限与可尝试调整。</p></div><button class="button primary" data-action="review-performance">${icon('chart')} ${performanceReview?'更新复盘':'生成本地复盘'}</button></div><div id="performance-review-result">${renderPerformanceReview()}</div></section>`;
}
function performanceRecords(){
  const records=(state.performance_records||[]).filter(r=>`${r.adopted_title||''} ${r.article_id||''} ${r.topic_type||''}`.toLowerCase().includes(performanceQuery.toLowerCase())).sort((a,b)=>(Date.parse(b.stats_at||b.created_at)||0)-(Date.parse(a.stats_at||a.created_at)||0));
  if(!records.length)return emptyState('chart',(state.performance_records||[]).length?'没有匹配的记录':'先记录一篇真实发布的文章。',(state.performance_records||[]).length?'换一个标题关键词，或搜索文章标识。':'可以关联已有草稿，也可以录入外部文章。暂时没有的数据留空，之后再补充。',`<button class="button secondary" data-action="add-performance">${icon('plus')} 新增统计记录</button>`);
  return `<div class="performance-table-wrap"><table class="performance-table"><thead><tr><th>文章与写法</th><th>发布 / 统计时点</th><th>阅读与互动</th><th>人工与投入</th><th><span class="visually-hidden">操作</span></th></tr></thead><tbody>${records.map(r=>`<tr><td><button class="performance-title" data-edit-performance="${esc(r.id)}">${esc(r.adopted_title||'未记录标题')}</button><div class="performance-article-id">${esc(r.article_id)}</div><div class="performance-tags">${r.topic_type?`<span class="tag">${esc(r.topic_type)}</span>`:''}${r.title_structure?`<span class="tag gray">${esc(r.title_structure)}</span>`:''}${r.draft_id?'<span class="tag">已关联草稿</span>':''}</div></td><td><div class="performance-date"><small>发布</small>${performanceDate(r.published_at)}</div><div class="performance-date"><small>统计</small>${performanceDate(r.stats_at)}</div></td><td><div class="performance-reads"><span>阅读</span><strong>${performanceValue(r.metrics?.reads)}</strong></div><div class="performance-interactions">${['likes','favorites','shares','comments','wows'].map(k=>`<span>${performanceMetrics[k]} ${performanceValue(r.metrics?.[k])}</span>`).join('')}</div></td><td><div class="performance-effort"><span>生成 ${performanceValue(r.generation_seconds,2)} 秒</span><span>费用 ${performanceValue(r.model_cost,4)} ${esc(r.cost_currency||'')}</span></div><p class="performance-change-excerpt">${esc(r.manual_changes||'未记录人工修改')}</p></td><td><div class="performance-row-actions"><button class="text-button" data-edit-performance="${esc(r.id)}">编辑记录</button><button class="text-button" data-copy-performance="${esc(r.id)}">新增统计 ${icon('plus')}</button></div></td></tr>`).join('')}</tbody></table></div>`;
}
function renderPerformanceReview(){
  const review=performanceReview;
  if(!review)return `<div class="performance-review-empty">${icon('chart')}<p>录入后点击「生成本地复盘」，查看哪些是事实、哪些还无法判断，以及下一次可以怎样验证。</p></div>`;
  const facts=Array.isArray(review.facts)?review.facts:[],limits=Array.isArray(review.limitations)?review.limitations:[],experiments=Array.isArray(review.experiments)?review.experiments:[],groups=Array.isArray(review.groups)?review.groups:[];
  const summary=review.summary||{};
  return `<div class="performance-review-meta"><span class="tag">本地规则 · 不调用模型</span><span>${esc(fmtDate(review.generated_at))}</span>${summary.snapshot_count!==undefined?`<span>${performanceValue(summary.snapshot_count)} 个快照 · ${performanceValue(summary.article_count)} 篇文章</span>`:''}</div><div class="performance-review-grid"><section class="performance-findings"><h3><span>01</span> 目前能看到的事实</h3>${facts.length?`<ul>${facts.map(x=>`<li>${esc(typeof x==='string'?x:JSON.stringify(x))}</li>`).join('')}</ul>`:'<p>当前数据还不足以给出明确事实。</p>'}</section><section class="performance-findings limitations"><h3><span>02</span> 解释这些数据的边界</h3>${limits.length?`<ul>${limits.map(x=>`<li>${esc(typeof x==='string'?x:JSON.stringify(x))}</li>`).join('')}</ul>`:'<p>请结合统计窗口、题材差异与样本量理解数据。</p>'}</section></div><section class="performance-experiments"><h3><span>03</span> 下一次可以尝试</h3>${experiments.length?`<div class="performance-experiment-grid">${experiments.map((x,i)=>`<article><span class="experiment-number">${String(i+1).padStart(2,'0')}</span><h4>${esc(x.title||'待验证的调整')}</h4>${x.reason?`<p>${esc(x.reason)}</p>`:''}<dl><dt>怎么做</dt><dd>${esc(x.action||'未提供具体动作')}</dd><dt>看什么</dt><dd>${esc(x.measure||'先约定一致的统计窗口')}</dd></dl></article>`).join('')}</div>`:'<p class="help">先继续积累可比较的记录，再选择一个因素做下一次尝试。</p>'}</section>${groups.length?`<details class="performance-groups"><summary>查看分组与统计口径</summary>${groups.map(g=>renderPerformanceGroup(g)).join('')}</details>`:''}`;
}
function renderPerformanceGroup(group){
  const metrics=group.metrics||{};
  const hours=group.observation_hours_min===null||group.observation_hours_min===undefined?'观察时长未提供':`${performanceValue(group.observation_hours_min,1)}—${performanceValue(group.observation_hours_max,1)} 小时`;
  return `<section class="performance-group"><div class="performance-group-title"><h4>${esc(group.label||'统计分组')}</h4><span>${performanceValue(group.article_count)} 篇文章 · ${hours}</span></div><div class="performance-group-table"><table><thead><tr><th>指标</th><th>已知</th><th>缺失</th><th>合计</th><th>均值</th></tr></thead><tbody>${Object.entries(performanceMetrics).filter(([key])=>metrics[key]).map(([key,label])=>`<tr><td>${label}</td><td>${performanceValue(metrics[key].known)}</td><td>${performanceValue(metrics[key].missing)}</td><td>${performanceValue(metrics[key].total,2)}</td><td>${performanceValue(metrics[key].mean,2)}</td></tr>`).join('')}</tbody></table></div>${Array.isArray(group.title_structures)&&group.title_structures.length?`<p>记录的标题结构：${group.title_structures.map(x=>esc(x)).join('、')}</p>`:''}${Array.isArray(group.topic_types)&&group.topic_types.length?`<p>记录的选题类型：${group.topic_types.map(x=>esc(x)).join('、')}</p>`:''}</section>`;
}
async function reviewPerformance(button){
  await busy(button,async()=>{try{performanceReview=await api('/api/performance/review');if(page==='performance')renderPage();toast('本地复盘已生成，没有调用模型。');}catch(e){toast(e.message,true);}});
}
function performanceTextField(label,name,value='',placeholder='',extra=''){return `<div class="field"><label class="form-label" for="perf-${name}">${label}</label><input id="perf-${name}" name="${name}" value="${esc(performanceInput(value))}" placeholder="${esc(placeholder)}" ${extra}></div>`;}
function performanceNumberField(label,name,value,step='1',signed=false){return performanceTextField(label,name,value,'未提供，请留空',`type="number" step="${step}"${signed?'':' min="0"'} inputmode="decimal"`);}
function draftTimingHint(draftId){const draft=state.drafts.find(d=>String(d.id)===String(draftId));return draft&&typeof draft.generation_seconds==='number'&&Number.isFinite(draft.generation_seconds)?`<button type="button" class="text-button" data-action="use-draft-timing" data-draft-id="${esc(draft.id)}">使用该草稿记录的生成耗时：${esc(draft.generation_seconds)} 秒</button>`:'<span>所关联草稿没有实测耗时，请按实际记录填写或留空。</span>';}
function openPerformanceEditor(id=null,{copy=false,draftId=''}={}){
  const original=id?(state.performance_records||[]).find(r=>String(r.id)===String(id)):null;
  if(id&&!original){toast('这条记录已不可用，请刷新后重试。',true);return;}
  const r=original?{...original,metrics:{...original.metrics}}:{metrics:{}};
  if(copy){delete r.id;delete r.revision;r.metrics={};r.stats_at='';r.account_followers_change=null;r.account_window_start='';r.account_window_end='';}
  if(draftId){const d=state.drafts.find(x=>String(x.id)===String(draftId));if(d){r.draft_id=d.id;r.article_id=d.id;r.adopted_title=d.title;}}
  const editing=Boolean(r.id),title=editing?'编辑统计记录':copy?'为同一篇文章新增统计':'记录一次真实发布';
  showModal(title,`<form id="performance-form" data-record-id="${esc(r.id||'')}"><input type="hidden" name="revision" value="${esc(r.revision===undefined?'':JSON.stringify(r.revision))}"><div class="performance-form-intro">${copy?'文章信息已带入，指标与统计时间已清空。本次保存会新增快照，保留之前的记录。':'把一次发布、一个统计时点记录清楚。未提供的数据请留空，真实的 0 可以填写。'}</div><fieldset class="performance-fieldset"><legend><span>01</span> 文章与发布信息</legend><div class="field"><label class="form-label" for="perf-draft_id">关联已有草稿 <small>可选</small></label><select id="perf-draft_id" name="draft_id"><option value="">不关联草稿</option>${state.drafts.map(d=>`<option value="${esc(d.id)}"${String(d.id)===String(r.draft_id)?' selected':''}>${esc(d.title)}</option>`).join('')}</select></div><div class="form-row">${performanceTextField('文章标识 <small>必填，同一篇文章保持一致</small>','article_id',r.article_id,'原文链接、平台文章 ID 或自定义唯一标识','required maxlength="200"')}${performanceTextField('实际采用的标题','adopted_title',r.adopted_title,'填写发布后的真实标题')}</div><div class="form-row">${performanceTextField('发布时间 <small>本机时区</small>','published_at',localDateInput(r.published_at),'','type="datetime-local" step="any"')}${performanceTextField('选题类型','topic_type',r.topic_type,'例如：技术解读 / 产业观察 / 观点评论')}</div><div class="form-row">${performanceTextField('标题结构','title_structure',r.title_structure,'记录实际采用的结构，不由阅读量推断')}${performanceTextField('封面样式 / 模板','cover_template',r.cover_template,'模板名称、版式描述或素材编号')}</div></fieldset><fieldset class="performance-fieldset"><legend><span>02</span> 这一次的文章数据</legend><div class="form-row">${performanceTextField('统计时间 <small>填写任一指标时必填</small>','stats_at',localDateInput(r.stats_at),'','type="datetime-local" step="any"')}${performanceTextField('数据来源','data_source',r.data_source,'例如：公众号后台 / 原文页面 / 截图记录')}</div><div class="performance-metric-fields">${Object.entries(performanceMetrics).map(([key,label])=>performanceNumberField(label,key,r.metrics?.[key])).join('')}</div><p class="help">请填写确切整数；公开显示的“10万+”是下限，不能直接当作准确阅读数。可在备注中记录。</p></fieldset><fieldset class="performance-fieldset"><legend><span>03</span> 人工修改与实际投入</legend><div class="form-row"><div class="field"><label class="form-label" for="perf-manual_changes">发布前改了什么</label><textarea id="perf-manual_changes" name="manual_changes" rows="3" placeholder="标题、开头、结构、事实或措辞的实际调整">${esc(r.manual_changes||'')}</textarea></div><div class="field"><label class="form-label" for="perf-change_reason">为什么修改</label><textarea id="perf-change_reason" name="change_reason" rows="3" placeholder="保留你的判断，方便下次复盘">${esc(r.change_reason||'')}</textarea></div></div><div class="performance-cost-fields">${performanceNumberField('生成耗时（秒）','generation_seconds',r.generation_seconds,'any')}${performanceNumberField('本次模型费用','model_cost',r.model_cost,'any')}<div class="field"><label class="form-label" for="perf-cost_currency">费用币种</label><select id="perf-cost_currency" name="cost_currency"><option value="">未提供</option><option value="CNY"${r.cost_currency==='CNY'?' selected':''}>CNY · 人民币</option><option value="USD"${r.cost_currency==='USD'?' selected':''}>USD · 美元</option></select></div></div><div id="performance-draft-timing" class="help performance-draft-timing">${draftTimingHint(r.draft_id)}</div>${performanceTextField('费用来源 / 口径','cost_source',r.cost_source,'例如：服务商本次账单；未知请留空')}<p class="help">只有实际记录才填耗时与费用；会员额度不等于每篇费用为 0。</p></fieldset><fieldset class="performance-fieldset account-window"><legend><span>04</span> 账号同期变化 <small>单独记录，不归因于这篇文章</small></legend><div class="performance-cost-fields">${performanceNumberField('账号关注净变化','account_followers_change',r.account_followers_change,'1',true)}${performanceTextField('账号统计窗口开始','account_window_start',localDateInput(r.account_window_start),'','type="datetime-local" step="any"')}${performanceTextField('账号统计窗口结束','account_window_end',localDateInput(r.account_window_end),'','type="datetime-local" step="any"')}</div><p class="help">正数为净增，负数为净减。填写变化量时请同时填写完整窗口。</p></fieldset><div class="field"><label class="form-label" for="perf-notes">补充备注</label><textarea id="perf-notes" name="notes" rows="3" placeholder="活动投放、节假日、转载等可能影响表现的情况">${esc(r.notes||'')}</textarea></div>${editing&&Array.isArray(r.history)&&r.history.length?`<p class="help">这条记录已有 ${r.history.length} 次修订，保存后保留修订历史。</p>`:''}<div class="modal-actions"><button class="button secondary" type="button" data-action="close-modal">取消</button><button class="button primary" type="submit">${icon('check')} ${editing?'保存修正':'保存统计快照'}</button></div></form>`,{wide:true,type:'performance',id:r.id||null});
  performanceDateBaseline=Object.fromEntries(['published_at','stats_at','account_window_start','account_window_end'].map(name=>[name,{raw:r[name]||'',visible:$(`#perf-${name}`)?.value??localDateInput(r[name])}]));
}
function numericPerformanceField(fd,name,label,{integer=false,signed=false}={}){
  const raw=String(fd.get(name)??'').trim();if(raw==='')return null;
  const value=Number(raw);if(!Number.isFinite(value)||(integer&&!Number.isSafeInteger(value))||(!signed&&value<0))throw new Error(`${label}须为${signed?'可正可负的':'非负'}${integer?'整数':'数值'}；未知请留空。`);return value;
}
function performancePayload(fd){
  const payload={};for(const name of ['article_id','draft_id','topic_type','adopted_title','title_structure','cover_template','manual_changes','change_reason','cost_currency','cost_source','data_source','notes'])payload[name]=String(fd.get(name)||'').trim();
  if(!payload.article_id)throw new Error('请填写文章标识，同一篇文章的不同统计时点使用同一标识。');
  for(const name of ['published_at','stats_at','account_window_start','account_window_end']){const value=String(fd.get(name)||''),baseline=performanceDateBaseline?.[name];if(baseline&&value===baseline.visible)payload[name]=baseline.raw;else if(!value)payload[name]='';else{const date=new Date(value);if(Number.isNaN(+date))throw new Error('发布时间或统计时间格式不正确。');payload[name]=date.toISOString();}}
  payload.metrics=Object.fromEntries(Object.entries(performanceMetrics).map(([key,label])=>[key,numericPerformanceField(fd,key,label,{integer:true})]));
  if(Object.values(payload.metrics).some(v=>v!==null)&&!payload.stats_at)throw new Error('填写了文章指标后，请补充这一次的统计时间。');
  payload.generation_seconds=numericPerformanceField(fd,'generation_seconds','生成耗时');payload.model_cost=numericPerformanceField(fd,'model_cost','模型费用');
  if(payload.model_cost!==null&&!['CNY','USD'].includes(payload.cost_currency))throw new Error('填写模型费用后，请选择费用币种。');
  payload.account_followers_change=numericPerformanceField(fd,'account_followers_change','账号关注净变化',{integer:true,signed:true});
  if(payload.account_followers_change!==null&&(!payload.account_window_start||!payload.account_window_end))throw new Error('填写账号关注净变化后，请补全账号统计窗口。');
  if(fd.get('revision'))payload.revision=JSON.parse(String(fd.get('revision')));return payload;
}
function selectPerformanceDraft(draftId){
  const draft=state.drafts.find(d=>String(d.id)===String(draftId));if(draft){if(!$('#perf-article_id').value.trim())$('#perf-article_id').value=draft.id;if(!$('#perf-adopted_title').value.trim())$('#perf-adopted_title').value=draft.title;}
  $('#performance-draft-timing').innerHTML=draftTimingHint(draftId);
}
function openPerformanceImport(){
  performanceImport=null;performanceImportRead++;
  showModal('导入发布数据',`<form id="performance-import-form"><div class="performance-form-intro">支持 UTF-8 CSV 或 JSON，最多 1,000 条、8 MB。先下载标准模板整理数据；不支持 XLSX。空白指标保持未知，真实 0 会保留。</div><a class="button secondary small" href="/api/performance/template.csv" download>${icon('file')} 下载 CSV 模板</a><label class="upload-zone performance-upload">${icon('upload')}<strong>选择整理好的数据文件</strong><small>CSV 使用模板字段；JSON 使用记录数组或导出的 JSON 文件</small><input id="performance-import-file" type="file" accept=".csv,.json,application/json,text/csv" required></label><div id="performance-import-preview" hidden><label class="form-label">文件预览 <small>前 4,000 字符；提交时使用完整内容</small></label><textarea id="performance-import-text" rows="8" readonly></textarea><p id="performance-import-info" class="help"></p></div><p class="help">导入会先校验整批数据。任意一条有误时不会保存这一批；错误会标明行号或字段。相同文章标识与统计时点不会重复新增。</p><div class="modal-actions"><button class="button primary" type="submit">${icon('upload')} 校验并导入</button></div></form>`,{type:'performance-import'});
}
async function readPerformanceImport(file){
  if(!file)return;const session=modal,readId=++performanceImportRead;performanceImport=null;$('.form-error',$('#performance-import-form'))?.remove();
  try{if(file.size>8*1024*1024)throw new Error('文件超过 8 MB，请按最多 1,000 条记录拆分后导入。');const extension=file.name.split('.').pop().toLowerCase();if(!['csv','json'].includes(extension))throw new Error('请选择 UTF-8 编码的 .csv 或 .json 文件。');const content=await file.text();if(modal!==session||modal?.type!=='performance-import'||readId!==performanceImportRead)return;if(!content.trim())throw new Error('文件内容为空，请选择包含记录的文件。');performanceImport={format:extension,content,filename:file.name};$('#performance-import-text').value=content.slice(0,4000);$('#performance-import-info').textContent=`${file.name} · ${(file.size/1024).toFixed(1)} KB · ${extension.toUpperCase()}`;$('#performance-import-preview').hidden=false;}catch(e){if(modal===session&&readId===performanceImportRead){formError($('#performance-import-form'),e.message);$('#performance-import-preview').hidden=true;}}
}
async function recordDraftPerformance(button){
  if(!activeDraft)return;const id=String(activeDraft.id);
  await busy(button,async()=>{if(modalDirty&&!(await saveDraft(null,{quiet:true})))return;if(!activeDraft||String(activeDraft.id)!==id)return;closeModal(true);await navigate('performance');openPerformanceEditor(null,{draftId:id});});
}

function showModal(title,body,{wide=false,tabs='',type='generic',id=null}={}){
  modal={type,id};modalDirty=false;$('#modal-root').innerHTML=`<div class="modal-backdrop"><section class="modal ${wide?'wide':''}" role="dialog" aria-modal="true" aria-label="${esc(title)}"><header class="modal-header"><h2>${esc(title)}</h2><button class="icon-button" data-action="close-modal" aria-label="关闭">${icon('close')}</button></header>${tabs}<div class="modal-body">${body}</div></section></div>`;document.body.style.overflow='hidden';setTimeout(()=>$('.modal input,.modal textarea,.modal button')?.focus(),30);
}
function closeModal(force=false){if(!force&&modalDirty&&!confirm('有尚未保存的修改。确定关闭并放弃吗？'))return false;$('#modal-root').innerHTML='';document.body.style.overflow='';modal=null;modalDirty=false;activeDraft=null;return true;}
function addArticle(tab='paste'){
  if(modalDirty&&!confirm('切换添加方式会清空尚未保存的输入，是否继续？'))return;
  const tabs=`<div class="modal-tabs"><button class="modal-tab ${tab==='paste'?'active':''}" data-add-tab="paste">粘贴文章</button><button class="modal-tab ${tab==='url'?'active':''}" data-add-tab="url">从链接导入</button><button class="modal-tab ${tab==='upload'?'active':''}" data-add-tab="upload">上传文件</button></div>`;
  let body='';
  if(tab==='paste')body=`<form id="add-article-form"><div class="field"><label class="form-label" for="article-title">文章标题</label><input id="article-title" name="title" required placeholder="值得留下来的那篇文章"></div><div class="field"><label class="form-label" for="article-content">文章正文</label><textarea id="article-content" name="content" rows="9" required placeholder="粘贴完整正文。阅读量、点赞量等可在下方手动记录。"></textarea></div><div class="form-row"><div class="field"><label class="form-label">原文链接 <small>选填</small></label><input name="url" type="url" placeholder="https://…"></div><div class="field"><label class="form-label">作者 / 公众号 <small>选填</small></label><input name="author" placeholder="记录真实出处"></div></div><details><summary class="form-label" style="cursor:pointer">补充热度数据与来源（选填）</summary>${metricsFields()}</details><div class="modal-actions"><button class="button primary" type="submit">${icon('plus')} 收藏到素材库</button></div></form>`;
  if(tab==='url')body=`<form id="import-form"><div class="field"><label class="form-label" for="import-url">文章链接</label><input id="import-url" name="url" type="url" required placeholder="https://mp.weixin.qq.com/s/…"></div><p class="help">只导入能够公开访问的正文。微信页面若需要登录、验证或无法提取，请改用「粘贴文章」保存完整内容。</p><div class="modal-actions"><button class="button primary" type="submit">${icon('link')} 读取并收藏</button></div></form>`;
  if(tab==='upload')body=`<form id="upload-form"><label class="upload-zone">${icon('upload')}<strong>把好文章带进素材库</strong><small>支持 .md、.txt、.html、.docx；PDF 取决于本机组件</small><input name="file" type="file" accept=".md,.txt,.html,.htm,.docx,.pdf" required></label><p class="help">文件会提取为可编辑正文。图片或扫描 PDF 可能无法提取，请优先使用文本文件。</p><div class="modal-actions"><button class="button primary" type="submit">${icon('upload')} 上传并收藏</button></div></form>`;
  showModal('添加一份值得参考的素材',body,{tabs,type:'add'});
}
function metricsFields(m={}){return `<div style="margin-top:13px"><div class="metric-fields"><label>阅读<input name="reads" value="${esc(m.reads||'')}" placeholder="未知，例如 10万+"></label><label>点赞<input name="likes" value="${esc(m.likes||'')}" placeholder="未知，例如 1200"></label><label>收藏<input name="favorites" value="${esc(m.favorites||'')}" placeholder="未知"></label></div><div class="form-row"><div><label class="form-label">数据来源</label><input name="metric_source" value="${esc(m.source||'')}" placeholder="例如：原文页面 / 作者截图"></div><div><label class="form-label">记录日期</label><input name="observed_at" type="date" value="${esc((m.observed_at||'').slice(0,10))}"></div></div><p class="help">未公开的数据请留空；搜索排序不能代表热度高低。</p></div>`;}
function readMetrics(fd){return {reads:fd.get('reads')||'',likes:fd.get('likes')||'',favorites:fd.get('favorites')||'',source:fd.get('metric_source')||'',observed_at:fd.get('observed_at')||''};}
function researchModal(){showModal('搜索产业文章',`<form id="research-form"><div class="field"><label class="form-label" for="research-query">想收集哪方面的资料？</label><input id="research-query" name="query" required placeholder="例如：ADB 智能车灯 供应链 量产" value=""></div><div class="field"><label class="form-label" for="scope">搜索范围</label><select name="scope" id="scope"><option value="wechat">公众号文章 · 微信公开页面</option><option value="web">全网行业资料</option></select></div><div class="inline-notice">搜索返回真实链接与可访问内容。部分网页只能获取摘要，会明确标注；没有公开的阅读、点赞、收藏不会自动补全。</div><p class="help">${state.settings.tavily_configured?'使用已配置的 Tavily 密钥。':'当前使用免密试用，额度有限；如达到限额，可在设置中填入 Tavily 密钥。'}</p><div class="modal-actions"><button class="button primary" type="submit">${icon('search')} 搜索并存入素材库</button></div></form>`,{type:'research'});}
function articleDetail(id){const a=state.articles.find(a=>String(a.id)===String(id));if(!a)return;const url=safeURL(a.url);showModal('素材详情',`<div class="article-card-top"><span class="tag">${a.content_kind==='snippet'?'搜索摘要 · 需补充正文':'完整正文'}</span>${a.analysis?'<span class="tag">已完成拆解</span>':''}</div><h2 class="detail-title">${esc(a.title)}</h2><div class="detail-meta"><span>作者：${esc(a.author||'未记录')}</span><span>收藏于 ${esc(fmtDate(a.created_at))}</span><span>阅读 ${metricLabel(a.metrics?.reads)}</span><span>点赞 ${metricLabel(a.metrics?.likes)}</span><span>收藏 ${metricLabel(a.metrics?.favorites)}</span></div><div class="help">热度来源：${esc(a.metrics?.source||'未记录')} · 记录日期：${esc(fmtDate(a.metrics?.observed_at))}</div>${url?`<a class="source-link" href="${esc(url)}" target="_blank" rel="noopener noreferrer">查看原文 · ${esc(url)}</a>`:''}<div class="detail-section"><div class="article-controls"><button class="button primary small" data-analyze="${esc(a.id)}"${a.content_kind==='snippet'?' disabled title="请先补充完整正文"':''}>${icon('spark')} ${a.analysis?'重新拆解文章':'拆解文章'}</button><button class="button secondary small" data-edit-article="${esc(a.id)}">编辑正文与热度</button><button class="text-button" data-archive-article="${esc(a.id)}" style="margin-left:auto;color:#a28d76">移出素材库</button></div>${a.content_kind==='snippet'?'<div class="inline-notice">当前只有搜索摘要，无法据此完整拆解标题、开头与结构。点击「编辑正文与热度」补充原文。</div>':''}</div><section class="detail-section"><div class="detail-section-head"><h3>文章拆解</h3><button class="text-button" data-page-modal="cards">查看写法卡片 ${icon('arrow')}</button></div>${a.analysis?`<div class="detail-content">${esc(a.analysis)}</div>`:'<div class="no-analysis">拆解将提炼标题逻辑、开头方式、文章结构与可复用方法。生成的写法卡片需要你审核后才会用于创作。</div>'}</section><section class="detail-section"><h3>收藏的正文</h3><div class="detail-content">${esc(a.content)}</div></section>`,{wide:true,type:'article',id:a.id});}
function editArticle(id){const a=state.articles.find(a=>String(a.id)===String(id));if(!a)return;showModal('编辑素材',`<form id="edit-article-form" data-id="${esc(id)}"><div class="field"><label class="form-label">文章标题</label><input name="title" value="${esc(a.title)}" required></div><div class="field"><label class="form-label">正文 <small>补充完整正文后可进行拆解</small></label><textarea name="content" rows="12" required>${esc(a.content)}</textarea></div><div class="form-row"><div><label class="form-label">原文链接</label><input name="url" type="url" value="${esc(a.url)}"></div><div><label class="form-label">作者 / 公众号</label><input name="author" value="${esc(a.author)}"></div></div>${metricsFields(a.metrics)}<div class="modal-actions"><button class="button primary" type="submit">${icon('check')} 保存素材</button></div></form>`,{type:'edit-article',id});}
function editCard(id){const c=state.cards.find(c=>String(c.id)===String(id));if(!c)return;showModal('编辑写法卡片',`<form id="edit-card-form" data-id="${esc(id)}"><div class="field"><label class="form-label">方法名称</label><input name="title" value="${esc(c.title)}" required></div><div class="field"><label class="form-label">分类</label><input name="category" value="${esc(c.category)}" required></div><div class="field"><label class="form-label">方法、使用条件与注意事项</label><textarea name="content" rows="10" required>${esc(c.content)}</textarea></div><label class="check-row"><input type="checkbox" name="approved"${c.approved?' checked':''}><span>我已审核，启用这张写法卡片</span></label><div class="modal-actions"><button class="button primary" type="submit">${icon('check')} 保存卡片</button></div></form>`,{type:'edit-card',id});}
function writingModeLabel(mode){return mode==='deep'?'深度专题':'Lite · 日常写作';}
function writingPolicySources(policy){const sources=Array.isArray(policy.sources)?policy.sources:[];return sources.length?`<details class="writing-policy-sources"><summary>规范参考来源（${sources.length} 项）</summary><div>${sources.map(source=>{const url=safeURL(source.url),name=source.name||source.title||'参考来源';return `<p>${url?`<a href="${esc(url)}" target="_blank" rel="noopener noreferrer">${esc(name)} ${icon('link')}</a>`:esc(name)}</p>`;}).join('')}</div></details>`:'';}
function renderWritingPolicy(policy){
  return `<div class="writing-policy-intro"><span class="tag">你的长期写作要求</span><p>在这里维护完整主提示词与写作要求，也可以在 Obsidian 中编辑同一份文件。保存后仅影响新建任务；已有任务继续使用准备时的规范快照。</p></div><form id="writing-policy-form"><input type="hidden" name="revision" value="${esc(policy.revision)}"><div class="writing-policy-mode"><div><label class="form-label" for="writing-policy-mode">默认写作方式</label><select id="writing-policy-mode" name="mode"><option value="lite"${policy.mode!=='deep'?' selected':''}>Lite · 日常公众号写作</option><option value="deep"${policy.mode==='deep'?' selected':''}>深度专题 · 更充分展开</option></select></div><p>日常文章从 Lite 开始；需要完整讨论复杂议题时，切换为深度专题。具体要求以主提示词为准。</p></div><div class="writing-policy-editor-head"><label class="form-label" for="writing-policy-text">主提示词与写作规范</label><span id="writing-policy-count">${chars(policy.text).toLocaleString()} 字</span></div><textarea id="writing-policy-text" name="text" class="writing-policy-editor" rows="24" required spellcheck="false">${esc(policy.text||'')}</textarea><div class="writing-policy-file"><div><span>本地文件</span><code id="writing-policy-path">${esc(policy.path||'未提供文件路径')}</code></div>${policy.path?'<button class="text-button" type="button" data-action="copy-writing-policy-path">复制路径</button>':''}</div>${writingPolicySources(policy)}<div class="modal-actions"><button class="button secondary" type="button" data-action="copy-writing-policy">复制当前规范</button><button class="button secondary" type="button" data-action="reload-writing-policy">重新读取文件</button><button class="button primary" type="submit">${icon('check')} 保存写作规范</button></div></form>`;
}
async function openWritingPolicy(button,{refresh=false}={}){
  if(modalDirty&&!confirm(refresh?'重新读取会替换尚未保存的输入。请先复制需要保留的内容，确定重新读取吗？':'当前窗口有未保存的输入。确定放弃并打开写作规范吗？'))return;
  const previousModal=modal;
  await busy(button,async()=>{try{const policy=await api('/api/writing/policy');if(modal!==previousModal){toast('写作规范已读取；当前窗口已切换，请重新打开。');return;}writingPolicySession=policy;showModal('写作规范',renderWritingPolicy(policy),{wide:true,type:'writing-policy'});}catch(e){toast(e.message,true);}});
}
async function copyWritingPolicy(path=false){const value=path?writingPolicySession?.path:$('#writing-policy-text')?.value;if(!value){toast(path?'没有可复制的文件路径。':'当前规范为空。',true);return;}try{await navigator.clipboard.writeText(value);toast(path?'文件路径已复制，可在 Obsidian 或本地编辑器中打开。':'当前编辑中的写作规范已复制。');}catch{toast('浏览器无法直接复制，请在文本中全选并复制。',true);}}
function preparedPolicySummary(prepared){const policy=prepared.writing_policy;if(!policy||typeof policy!=='object')return '';return `<div class="prepared-policy-summary">${icon('note')}<span>本次规范快照：${esc(writingModeLabel(policy.mode))}${policy.revision?` <small>版本 ${esc(String(policy.revision).slice(0,8))}</small>`:''}</span></div>`;}
function draftPublicationPackage(d){return d.publication_package||draftWritingRun(d)?.publication_package;}
function publicationPackageStale(d){const pkg=draftPublicationPackage(d);if(!pkg)return false;if(pkg.stale)return true;return Boolean(modal?.type==='draft'&&String(modal.id)===String(d.id)&&$('#draft-title')&&$('#draft-content')&&($('#draft-title').value.trim()!==String(d.title||'').trim()||$('#draft-content').value!==d.content));}
function publicationCurrentTitle(d){return modal?.type==='draft'&&String(modal.id)===String(d.id)&&$('#draft-title')?$('#draft-title').value.trim():String(d.title||'');}
function publicationBlock(label,key,value){return value?`<article class="publication-block"><div><h4>${label}</h4><button class="text-button" data-copy-publication="${key}">复制</button></div><p>${esc(value)}</p></article>`:'';}
function renderPublicationPackage(d){
  const pkg=draftPublicationPackage(d);if(!pkg)return '';const skipped=pkg.status==='skipped',attention=pkg.status==='needs_attention'||pkg.review?.status==='needs_attention',titles=Array.isArray(pkg.alternative_titles)?pkg.alternative_titles.filter(t=>typeof t==='string'):[];
  return `<div class="publication-heading"><div><span class="page-kicker">PUBLICATION KIT</span><h3>发布配套</h3><p>标题、摘要与分享语集中在这里，方便复制到公众号。</p></div><span class="tag ${skipped||attention?'orange':''}">${skipped?'本次未生成':attention?'需要人工处理':'待人工核对'}</span></div><div id="publication-stale" class="writing-attention"${publicationPackageStale(d)?'':' hidden'}>标题或正文已修改，配套文字对应旧版本。请先对照当前稿件调整，再用于发布。</div>${pkg.reason?`<div class="publication-note ${skipped||attention?'attention':''}">${esc(writingText(pkg.reason))}</div>`:''}${skipped?'':`<div class="publication-primary-title"><div><small>当前推荐标题</small><button class="text-button" data-copy-publication="title">复制标题</button></div><h4 id="publication-current-title">${esc(publicationCurrentTitle(d))}</h4></div>${titles.length?`<section class="publication-alternatives"><h4>备选标题</h4>${titles.map((title,i)=>`<div><span>${String(i+1).padStart(2,'0')}</span><p>${esc(title)}</p><button class="text-button" data-copy-publication-title="${i}">复制</button></div>`).join('')}</section>`:''}<div class="publication-grid">${publicationBlock('公众号摘要','summary',pkg.summary)}${publicationBlock('封面文字','cover_text',pkg.cover_text)}${publicationBlock('封面提示词','cover_prompt',pkg.cover_prompt)}${publicationBlock('分享语','share_text',pkg.share_text)}</div><p class="publication-review-note">${isCozeDraft(d)?'这是 Coze 返回的发布配套，尚未经过本地事实与语言独立审核，仍需你逐项核对。':'配套文字经过生成自查，尚未经过正文的独立双审，仍需你核对。'}封面提示词仅供后续作图使用，不代表已生成图片。</p>${pkg.review?.note?`<p class="publication-review-note">${esc(pkg.review.note)}</p>`:''}<div class="publication-copy-all"><button class="button secondary small" data-copy-publication="all">${icon('copy')} 复制整份发布配套</button></div>`}`;
}
function publicationCopyText(d,key,index){
  const pkg=draftPublicationPackage(d);if(!pkg)return '';if(index!==undefined){const title=Array.isArray(pkg.alternative_titles)?pkg.alternative_titles[Number(index)]:null;return typeof title==='string'?title:'';}
  const title=publicationCurrentTitle(d);if(key==='title')return title;if(key!=='all')return typeof pkg[key]==='string'?pkg[key]:'';
  const titles=Array.isArray(pkg.alternative_titles)?pkg.alternative_titles.filter(t=>typeof t==='string'):[];
  return [`# 推荐标题\n\n${title}`,titles.length?`## 备选标题\n\n${titles.map((t,i)=>`${i+1}. ${t}`).join('\n')}`:'',...[['summary','公众号摘要'],['cover_text','封面文字'],['cover_prompt','封面提示词'],['share_text','分享语']].map(([field,label])=>typeof pkg[field]==='string'&&pkg[field]?`## ${label}\n\n${pkg[field]}`:'')].filter(Boolean).join('\n\n');
}
async function copyPublication(key,index){const d=activeDraft&&state.drafts.find(d=>String(d.id)===String(activeDraft.id));if(!d)return;const text=publicationCopyText(d,key,index);if(!text){toast('没有可复制的配套内容。',true);return;}try{await navigator.clipboard.writeText(text);toast(publicationPackageStale(d)?'已复制；配套对应旧稿，请对照当前文章调整。':'发布配套已复制，使用前请人工核对。');}catch{toast('浏览器无法直接复制，请选中文本并复制。',true);}}
function refreshPublicationPackage(id){if(modal?.type!=='draft'||String(modal.id)!==String(id))return;const d=state.drafts.find(d=>String(d.id)===String(id)),section=$('#draft-publication-section');if(d&&section){section.innerHTML=renderPublicationPackage(d);section.hidden=!draftPublicationPackage(d);}}
function updatePublicationStatus(){const d=activeDraft&&state.drafts.find(d=>String(d.id)===String(activeDraft.id));if(!d)return;const stale=$('#publication-stale'),title=$('#publication-current-title');if(stale)stale.hidden=!publicationPackageStale(d);if(title)title.textContent=publicationCurrentTitle(d);}

function writingText(value){return value===null||value===undefined?'':typeof value==='string'?value:JSON.stringify(value,null,2);}
function writingStatus(run){return ({preparing:'整理事实与提纲中',awaiting_confirmation:'等待你确认',generating:'生成与审核中',awaiting_review:'待人工审阅',needs_attention:'需人工处理',error:'处理未完成'})[run.status]||'写作任务';}
function writingTitle(run){return writingText(run.request?.brief||run.prepared?.request?.brief||run.title||'公众号写作任务').slice(0,95);}
function upsertWritingRun(run){if(!run?.id)return;const runs=state.writing_runs||[];state.writing_runs=[run,...runs.filter(x=>String(x.id)!==String(run.id))];updateWritingSurfaces();}
function updateWritingSurfaces(){
  if($('#nav-writing'))$('#nav-writing').textContent=(state.writing_runs||[]).filter(r=>r.status==='awaiting_confirmation').length||'';
  if(page==='home'&&$('#writing-runs'))$('#writing-runs').innerHTML=renderWritingRuns();
}
async function loadWritingRuns(){try{const result=await api('/api/writing');state.writing_runs=Array.isArray(result.runs)?result.runs:[];updateWritingSurfaces();}catch(e){toast(`写作任务读取失败：${e.message}`,true);}}
function renderWritingRuns(){
  const runs=[...(state.writing_runs||[])].sort((a,b)=>(Date.parse(b.updated_at||b.created_at)||0)-(Date.parse(a.updated_at||a.created_at)||0));
  const waiting=runs.filter(r=>r.status==='awaiting_confirmation').length;
  return `<div class="writing-runs-heading"><div><span class="page-kicker">WORK IN PROGRESS</span><h2>你的写作任务${waiting?` <span class="writing-wait-count">${waiting} 项待确认</span>`:''}</h2><p>事实与提纲会先停下来等你确认。任务保存在本机，刷新后可继续。</p></div><button class="text-button" data-action="refresh-writing">刷新任务 ${icon('clock')}</button></div>${runs.length?`<div class="writing-run-list">${runs.map(r=>`<article class="writing-run-row"><span class="writing-run-symbol">${icon(['preparing','generating'].includes(r.status)?'clock':r.status==='awaiting_confirmation'?'book':'file')}</span><div class="writing-run-main"><button class="writing-run-title" data-writing-run="${esc(r.id)}">${esc(writingTitle(r))}</button><div class="writing-run-meta"><span class="tag ${['needs_attention','error'].includes(r.status)?'orange':''}">${esc(writingStatus(r))}</span><span>${esc(fmtDate(r.updated_at||r.created_at))}</span>${r.repair_rounds?`<span>已定点修改 ${esc(r.repair_rounds)} 轮</span>`:''}</div>${r.message?`<p>${esc(r.message)}</p>`:''}</div><button class="button secondary small" data-writing-run="${esc(r.id)}">${r.status==='awaiting_confirmation'?'确认事实与提纲':r.draft_id?'打开待审包':'查看任务'} ${icon('arrow')}</button></article>`).join('')}</div>`:`<div class="writing-runs-empty">${icon('note')}<div><strong>先整理依据，再开始成文。</strong><p>提交上方写作要求后，事实卡片、写法案例与提纲会出现在这里。</p></div></div>`}`;
}
function writingReference(prepared,sourceId){return [...(prepared.references||[]),...(prepared.sources||[])].find(r=>String(r.source_id||r.id)===String(sourceId));}
function writingSourceLink(prepared,sourceId){const ref=writingReference(prepared,sourceId),url=safeURL(ref?.url);return url?`<a href="${esc(url)}" target="_blank" rel="noopener noreferrer">${esc(ref.title||sourceId)} ${icon('link')}</a>`:`<span>${esc(ref?.title||sourceId||'未提供来源')}</span>`;}
function renderWritingFacts(prepared){
  const facts=Array.isArray(prepared.fact_cards)?prepared.fact_cards:[];
  return `<section class="writing-prepared-section"><div class="writing-section-heading"><span>01</span><h3>事实卡片</h3><small>${facts.length} 条依据，逐条看出处与边界</small></div>${facts.length?`<div class="writing-fact-grid">${facts.map((f,i)=>`<article class="writing-fact-card"><div class="writing-fact-top"><b>${esc(f.id||`事实 ${i+1}`)}</b><span class="tag ${f.confidence==='supported'?'':'orange'}">${f.confidence==='supported'?'有原文依据 · 未独立核实':'待核实'}</span></div><h4>${esc(writingText(f.claim))}</h4><div class="writing-fact-meta">${f.entity?`<span>主体：${esc(writingText(f.entity))}</span>`:''}${f.time?`<span>时间：${esc(writingText(f.time))}</span>`:''}${f.event_status?`<span>状态：${esc(writingText(f.event_status))}</span>`:''}${f.numbers&&writingText(f.numbers)!=='[]'?`<span>数字：${esc(writingText(f.numbers))}</span>`:''}</div>${f.source_quote?`<blockquote><span>捕获的原文片段</span>${esc(writingText(f.source_quote))}</blockquote>`:'<p class="writing-missing-evidence">未提供原文摘录，请核对后再采用。</p>'}<div class="writing-fact-source">${icon('link')}${writingSourceLink(prepared,f.source_id)}</div>${f.caveat?`<p class="writing-caveat">${esc(writingText(f.caveat))}</p>`:''}</article>`).join('')}</div>`:'<div class="writing-soft-note">本次没有形成可引用的事实卡片。可在调整要求中说明需要补充或删除哪些内容。</div>'}</section>`;
}
function renderWritingPrepared(prepared){
  const cases=Array.isArray(prepared.cases)?prepared.cases:[],outline=Array.isArray(prepared.outline)?prepared.outline:[],gaps=Array.isArray(prepared.gaps)?prepared.gaps:[];
  return `${preparedPolicySummary(prepared)}${renderWritingFacts(prepared)}<section class="writing-prepared-section"><div class="writing-section-heading"><span>02</span><h3>写法案例</h3><small>参考写法，不代表已验证的爆款</small></div>${cases.length?`<div class="writing-case-list">${cases.map(c=>`<article><div><span class="tag gray">${esc(({article_reference:'全文写法参考',approved_card:'已审核写法'})[c.kind]||'写法参考')}</span><h4>${esc(writingText(c.title)||'写法案例')}</h4></div>${c.reason?`<p>${esc(writingText(c.reason))}</p>`:''}${c.method?`<p class="writing-case-method">${esc(writingText(c.method))}</p>`:''}<div class="writing-fact-source">${icon('link')}${writingSourceLink(prepared,c.source_id)}</div></article>`).join('')}</div>`:'<p class="help">本次没有足够依据给出具体写法案例。</p>'}</section><section class="writing-prepared-section"><div class="writing-section-heading"><span>03</span><h3>切入角度与文章提纲</h3></div>${prepared.angle?`<div class="writing-angle">${esc(writingText(prepared.angle))}</div>`:''}<div class="writing-outline">${outline.map((o,i)=>`<article><span>${String(i+1).padStart(2,'0')}</span><div><h4>${esc(writingText(o.heading))}</h4><p>${esc(writingText(o.point))}</p>${Array.isArray(o.fact_ids)&&o.fact_ids.length?`<small>拟引用事实：${o.fact_ids.map(x=>esc(x)).join('、')}</small>`:''}</div></article>`).join('')||'<p class="help">暂未形成提纲，请查看任务说明。</p>'}</div></section><section class="writing-prepared-section"><div class="writing-section-heading"><span>04</span><h3>信息缺口</h3></div>${gaps.length?`<ul class="writing-gaps">${gaps.map(g=>`<li>${esc(writingText(g))}</li>`).join('')}</ul>`:'<p class="help">本次未列出具体缺口；仍请核对事实依据和表述边界。</p>'}</section>`;
}
function writingCanContinue(run){return run.status==='awaiting_confirmation'||(run.status==='error'&&run.prepared&&!run.draft_id);}
function renderWritingDialog(run){
  const working=['preparing','generating'].includes(run.status),prepared=run.prepared;
  return `<div id="writing-live-status" class="writing-live-status ${run.status==='error'?'error':''}">${working?'<span class="spinner"></span>':icon(run.status==='awaiting_confirmation'?'book':'clock')}<div><strong>${esc(writingStatus(run))}</strong><p>${esc(run.message||'任务保存在本地，可稍后回来继续。')}</p></div></div><p class="writing-task-brief">${esc(writingTitle(run))}</p>${run.error?`<div class="writing-attention">${esc(writingText(run.error))}</div>`:''}${prepared?renderWritingPrepared(prepared):`<div class="writing-runs-empty">${icon('book')}<div><strong>${working?'正在整理资料与依据':'暂未形成事实与提纲'}</strong><p>${working?'完成后会停在确认步骤，不会自动生成正文。':'请回到写作页调整要求，重新发起整理。'}</p></div></div>`}${writingCanContinue(run)?`<form id="writing-continue-form" data-writing-id="${esc(run.id)}"><input type="hidden" name="revision" value="${esc(run.revision)}"><div class="writing-confirmation"><h3>你的判断，决定这篇文章怎么写。</h3><p>核对以上事实、案例与提纲，写下需要调整的方向。点击后才开始生成正文、检查事实与语言，并进行有限轮次的定点修改。</p><label class="form-label" for="editor-direction">调整要求 <small>可选</small></label><textarea id="editor-direction" name="editor_direction" rows="4" maxlength="4000" placeholder="例如：删除未确认的量产表述；先讲供应链变化，再讲技术；开头避免空泛的行业判断。">${esc(run.editor_direction||'')}</textarea><div id="writing-revision-notice" class="writing-attention" hidden></div><button class="button primary full" type="submit">${icon('spark')} 继续生成并审核 ${icon('arrow')}</button></div></form>`:`<div class="writing-dialog-actions"><button class="button secondary" data-refresh-writing="${esc(run.id)}">刷新进度</button>${run.draft_id?`<button class="button primary" data-writing-draft="${esc(run.draft_id)}">打开待审包 ${icon('arrow')}</button>`:''}</div>`}`;
}
async function fetchWritingRun(id){const result=await api(`/api/writing/${encodeURIComponent(id)}`);if(!result.run)throw new Error('未找到这个写作任务。');upsertWritingRun(result.run);return result.run;}
async function openWritingRun(id,button){
  await busy(button,async()=>{try{const run=await fetchWritingRun(id);
    if(run.draft_id&&['awaiting_review','needs_attention'].includes(run.status)){if(!(state.drafts||[]).some(d=>String(d.id)===String(run.draft_id)))await reloadState(false);if(state.drafts.some(d=>String(d.id)===String(run.draft_id))){showDraft(run.draft_id);return;}}
    showModal('事实与提纲 · 等待你的判断',`<div id="writing-panel-content">${renderWritingDialog(run)}</div>`,{wide:true,type:'writing',id:run.id});
  }catch(e){toast(e.message,true);}});
}
function refreshWritingModal(run){
  if(modal?.type!=='writing'||String(modal.id)!==String(run.id))return;
  if(modalDirty){
    const status=$('#writing-live-status');if(status){const p=$('p',status);if(p)p.textContent=run.message||writingStatus(run);}
    const form=$('#writing-continue-form'),submittedRevision=form&&$('input[name="revision"]',form)?.value;
    if(submittedRevision&&submittedRevision!==String(run.revision)){
      const notice=$('#writing-revision-notice');if(notice){notice.hidden=false;notice.textContent='任务状态已更新，当前调整要求仍保留在这里。请复制需要保留的内容，关闭后重新打开最新任务。';}
      const button=form&&$('button[type="submit"]',form);if(button)button.disabled=true;
    }
    return;
  }
  const panel=$('#writing-panel-content');if(panel)panel.innerHTML=renderWritingDialog(run);
}
function draftWritingRun(d){return (state.writing_runs||[]).find(r=>String(r.id)===String(d.writing_id)||String(r.draft_id)===String(d.id));}
function draftReviewInfo(d){const run=draftWritingRun(d);return {run,review:d.review||run?.review};}
function draftReviewStale(d){
  const {review}=draftReviewInfo(d);if(!review)return false;if(review.stale)return true;
  return Boolean(modal?.type==='draft'&&String(modal.id)===String(d.id)&&$('#draft-title')&&$('#draft-content')&&($('#draft-title').value.trim()!==String(d.title||'').trim()||$('#draft-content').value!==d.content));
}
function reviewIssueCard(issue){return `<article class="writing-review-issue ${issue.severity==='critical'?'critical':''}"><div class="writing-review-issue-head"><span class="tag ${issue.severity==='critical'?'orange':'gray'}">${issue.severity==='critical'?'关键问题':'提示'}</span><span>${esc(issue.display_location||({title:'标题',content:'正文'})[issue.location]||writingText(issue.location)||'位置未提供')}</span></div>${issue.quote?`<blockquote>${esc(writingText(issue.quote))}</blockquote>`:''}<p><b>原因</b>${esc(writingText(issue.reason))}</p><p><b>建议</b>${esc(writingText(issue.suggestion))}</p>${Array.isArray(issue.source_ids)&&issue.source_ids.length?`<small>相关来源：${issue.source_ids.map(x=>esc(x)).join('、')}</small>`:''}</article>`;}
function renderDraftReview(d){
  const {run,review}=draftReviewInfo(d);if(!review)return '';
  if(isCozeDraft(d)&&review.scope==='not_locally_reviewed')return '<div class="writing-review-heading"><div><span class="page-kicker">EDITORIAL REVIEW</span><h3>本地审核尚未运行</h3><p>这份 Coze 原稿尚未进行本地事实与语言独立审核，也没有本地定点修改记录。请人工核对来源、数据与措辞。</p></div><span class="tag orange">待人工审阅</span></div>';
  const issues=Array.isArray(review.issues)?review.issues:[],critical=issues.filter(x=>x.severity==='critical'),rounds=Array.isArray(review.rounds)?review.rounds:[],repairRounds=review.repair_rounds??run?.repair_rounds??0;
  const incomplete=review.checks?.fact===false||review.checks?.language===false;
  const writingId=d.writing_id||run?.id;
  return `<div class="writing-review-heading"><div><span class="page-kicker">EDITORIAL REVIEW PACKAGE</span><h3>待人工审阅</h3><p>已进行 ${esc(repairRounds)} 轮定点修改。机器检查是审阅线索，问题数量为 0 也需要你确认后再使用。</p></div>${writingId?`<a class="button secondary small" href="/api/export/writing/${encodeURIComponent(writingId)}" download>${icon('file')} 下载待审包</a>`:''}</div><div id="draft-review-stale" class="writing-attention"${draftReviewStale(d)?'':' hidden'}>标题或正文已修改，下方审核对应旧版本。请结合当前内容重新人工核对。</div>${critical.length||incomplete?`<div class="writing-critical-summary">${icon('clock')}<span>${critical.length?`仍有 ${critical.length} 个关键问题需要处理。`:''}${incomplete?' 部分检查未完整完成，请查看问题说明。':''}当前稿件仍待人工审阅。</span></div>`:'<div class="writing-review-zero">本轮未标记关键问题，稿件仍待人工审阅。</div>'}<div class="writing-review-columns">${[['fact','事实核对'],['language','语言与表达']].map(([kind,label])=>{const list=issues.filter(x=>x.kind===kind);return `<section><h4>${label}<span>${list.length}</span></h4>${list.length?list.map(reviewIssueCard).join(''):`<p class="writing-no-issues">本轮未标记具体${kind==='fact'?'事实':'语言'}问题，仍需人工审阅。</p>`}</section>`;}).join('')}</div>${issues.some(i=>!['fact','language'].includes(i.kind))?`<div>${issues.filter(i=>!['fact','language'].includes(i.kind)).map(reviewIssueCard).join('')}</div>`:''}${review.note?`<p class="writing-review-note">${esc(writingText(review.note))}</p>`:''}${rounds.length?`<details class="writing-rounds"><summary>查看审核轮次与定点修改（${rounds.length} 次检查）</summary>${rounds.map(round=>`<section><h4>${Number(round.round)===0?'初次审核':`第 ${esc(round.round)} 轮检查`} <small>${Array.isArray(round.issues)?round.issues.length:0} 条问题记录</small></h4>${Array.isArray(round.issues)&&round.issues.length?`<details class="writing-round-issues"><summary>查看本轮问题与处理建议</summary>${round.issues.map(reviewIssueCard).join('')}</details>`:''}${Array.isArray(round.changes)&&round.changes.length?round.changes.map(change=>`<article class="writing-change"><div><span class="tag ${change.applied?'':'orange'}">${change.applied?'已应用定点修改':'未自动应用'}</span><span>${change.location==='title'?'标题':'正文'}</span></div><div class="writing-change-pair"><div><small>修改前</small><p>${esc(writingText(change.quote))}</p></div><div><small>替换内容</small><p>${change.replacement===''?'（删除原句）':esc(writingText(change.replacement))}</p></div></div>${change.reason?`<p class="writing-change-reason">${esc(writingText(change.reason))}</p>`:''}</article>`).join(''):'<p class="help">本轮没有记录定点替换。</p>'}<details class="writing-round-snapshot"><summary>查看本轮正文快照</summary><h5>${esc(writingText(round.title))}</h5><pre>${esc(writingText(round.content))}</pre></details></section>`).join('')}</details>`:''}`;
}
function refreshDraftReview(id){
  if(modal?.type!=='draft'||String(modal.id)!==String(id))return;
  const d=state.drafts.find(x=>String(x.id)===String(id)),section=$('#draft-review-section');if(d&&section){section.innerHTML=renderDraftReview(d);section.hidden=!draftReviewInfo(d).review;}
}
function updateDraftReviewStatus(){const d=activeDraft&&state.drafts.find(x=>String(x.id)===String(activeDraft.id)),status=$('#draft-review-stale');if(d&&status)status.hidden=!draftReviewStale(d);}

function draftSources(d){
  if(isCozeDraft(d))return '<div class="inline-notice">写作来源：Coze 文案助手返回。该原稿未经过本地 B 独立审核。</div>';
  const ctx=d.context||{}, refs=Array.isArray(d.references)?d.references:[];
  const sources=Array.isArray(ctx.articles)?ctx.articles:(ctx.article_ids||[]).map(id=>state.articles.find(a=>String(a.id)===String(id))).filter(Boolean);
  const merged=sources.length?sources:refs;
  const sourceRows=merged.map(r=>{if(typeof r==='string')return `<div>${esc(r)}</div>`;const url=safeURL(r.url);return `<div style="margin:6px 0">${url?`<a href="${esc(url)}" target="_blank" rel="noopener noreferrer">${esc(r.title||'来源文章')}</a>`:esc(r.title||'来源文章')}${r.content_kind==='snippet'?' <span class="tag orange">搜索摘要</span>':''}</div>`;}).join('');
  const usage=Array.isArray(d.usage)?d.usage.map(x=>`<li>${esc(typeof x==='string'?x:JSON.stringify(x))}</li>`).join(''):'';
  return `<div class="inline-notice"><strong style="font-weight:500">写作依据：${d.writing_id?'本次准备时的笔记快照':'生成时最新笔记'} · ${merged.length} 篇素材 · ${(ctx.card_ids||ctx.cards||[]).length} 张写法卡片</strong>${ctx.omitted_approved_cards?`<div>本次采用 12 张已审核卡片，其余 ${esc(ctx.omitted_approved_cards)} 张未纳入。</div>`:''}<details style="margin-top:8px"><summary style="cursor:pointer">查看来源与使用范围</summary>${sourceRows||'<div>本次没有选用文章素材。</div>'}${usage?`<ul style="padding-left:18px;margin:9px 0 0">${usage}</ul>`:''}${ctx.cards?.length?`<div style="margin-top:9px">参考写法：${ctx.cards.map(c=>esc(c.title)).join('、')}</div>`:''}</details></div>${d.editor_notes?`<details class="inline-notice"><summary style="cursor:pointer">发布前待核查事项</summary><div style="white-space:pre-wrap;margin-top:8px">${esc(typeof d.editor_notes==='string'?d.editor_notes:JSON.stringify(d.editor_notes))}</div></details>`:''}`;
}
function imageCount(value){const count=Number(value);return [5,6,7,8].includes(count)?count:6;}
function imageCountOptions(selected=6){return [5,6,7,8].map(n=>`<option value="${n}"${n===imageCount(selected)?' selected':''}>${n} 张</option>`).join('');}
function draftImageItems(d){return Array.isArray(d?.image_suggestions?.items)?d.image_suggestions.items:[];}
function imageJobRunning(id){return imageStarts.has(String(id))||[...trackedJobs.values()].some(j=>j.action==='images'&&String(j.draftId)===String(id));}
function draftImagesStale(d){
  if(!draftImageItems(d).length)return false;
  if(d.image_suggestions.stale)return true;
  if(modal?.type!=='draft'||String(modal.id)!==String(d.id))return false;
  const title=$('#draft-title'),body=$('#draft-content');
  return Boolean(title&&body&&(title.value.trim()!==String(d.title||'').trim()||body.value!==d.content));
}
function markdownText(value){return String(value??'').replace(/\s+/g,' ').trim().replace(/[\\`*_[\]<>]/g,'\\$&');}
function markdownURL(value){return safeURL(value).replace(/[<>\s\\]/g,c=>encodeURIComponent(c));}
function imageMarkdown(item){
  const url=markdownURL(item.image_url),source=markdownURL(item.source_url);
  if(!url||!source)return '';
  const caption=markdownText(item.caption||item.description||'文章配图');
  return `![${caption}](<${url}>)\n\n${caption}\n\n来源：[${markdownText(item.source_title||'图片来源')}](<${source}>)`;
}
function imageSuggestionCard(item,index){
  const url=safeURL(item.image_url),source=safeURL(item.source_url),md=imageMarkdown(item);
  const alt=item.description||item.caption||item.source_title||`配图 ${index+1}`;
  const linkAttrs='target="_blank" rel="noopener noreferrer" referrerpolicy="no-referrer"';
  return `<article class="image-card"><div class="image-card-preview${url?'':' image-unavailable'}">${url?`<img class="draft-image-thumb" src="${esc(url)}" alt="${esc(alt)}" loading="lazy" referrerpolicy="no-referrer" data-image-preview>`:''}<div class="image-preview-fallback"><span>${icon('link')}</span><p>这张图片暂时无法预览</p>${url?`<a href="${esc(url)}" ${linkAttrs}>在新窗口查看原图 ${icon('arrow')}</a>`:'<small>图片链接不可用</small>'}</div><span class="image-number">${String(index+1).padStart(2,'0')}</span></div><div class="image-card-body"><div class="image-card-top"><span class="tag">${esc(item.placement||'建议结合相邻段落使用')}</span><span class="image-license" title="${esc(item.license_note||'授权状态未知，请以来源页说明为准')}">授权未核验</span></div><h3>${esc(item.caption||item.description||item.source_title||'文章配图')}</h3>${item.description&&item.description!==item.caption?`<p class="image-description">${esc(item.description)}</p>`:''}<div class="image-source">${icon('link')}${source?`<a href="${esc(source)}" ${linkAttrs}>${esc(item.source_title||new URL(source).hostname)}</a>`:'<span>未提供可访问的来源页</span>'}</div>${item.license_note?`<p class="image-license-note">${esc(item.license_note)}</p>`:''}<div class="image-card-actions">${url?`<a class="text-button" href="${esc(url)}" ${linkAttrs}>打开原图 ${icon('arrow')}</a>`:''}<button class="button secondary small" data-copy-image="${index}"${md?'':' disabled'}>复制图文块</button></div>${md?`<details class="image-markdown"><summary>查看 Markdown</summary><textarea readonly rows="5" aria-label="配图 ${index+1} 的 Markdown">${esc(md)}</textarea></details>`:''}</div></article>`;
}
function renderDraftImages(d,count){
  const suggestions=d.image_suggestions||{},items=draftImageItems(d),running=imageJobRunning(d.id),stale=draftImagesStale(d);
  const error=draftImageErrors.get(String(d.id));
  const warnings=Array.isArray(suggestions.warnings)?suggestions.warnings.filter(x=>typeof x==='string'):[];
  const selected=imageCount(count||suggestions.requested_count||6);
  return `<div class="draft-images-heading"><div><span class="small-kicker">VISUAL REFERENCES</span><h3>给文章，找到合适的画面。</h3><p>搜图服务只接收从正文提炼的搜索词；图片展示不代表已获商用许可。</p></div><div class="draft-images-controls"><label class="visually-hidden" for="draft-image-count">配图数量</label><select id="draft-image-count" aria-label="配图数量"${running?' disabled':''}>${imageCountOptions(selected)}</select><button class="button primary small" data-action="find-draft-images"${running?' disabled':''}>${running?'<span class="spinner"></span> 正在找配图':`${icon('search')} 联网找配图`}</button></div></div><div id="draft-image-status" class="image-stale-note"${stale?'':' hidden'}>标题或正文已修改，这些配图基于旧版内容。可以重新查找，让图片跟上文章。</div>${error?`<div class="image-search-error">${esc(error)}<br>文章正文已保留，可调整内容后再次查找。</div>`:''}${items.length?`<div class="image-gallery-meta"><span>已找到 ${items.length} 张${suggestions.created_at?` · ${esc(fmtDate(suggestions.created_at))}`:''}</span><a class="text-button" href="/api/export/draft-images/${encodeURIComponent(d.id)}" download>${icon('file')} 导出配图清单</a></div><div class="draft-image-grid">${items.map(imageSuggestionCard).join('')}</div>`:`<div class="image-empty">${icon('globe')}<div><strong>${running?'正在根据文章查找配图…':'还没有为这篇文章配图'}</strong><p>${running?'结果会出现在这里，你可以继续编辑正文。':'选择 5—8 张，查找后逐张查看来源，再复制图文块到需要的位置。'}</p></div></div>`}${warnings.length?`<details class="image-search-notes"><summary>本次搜索说明</summary><ul>${warnings.map(w=>`<li>${esc(w)}</li>`).join('')}</ul></details>`:''}${Array.isArray(suggestions.queries)&&suggestions.queries.length?`<details class="image-search-notes"><summary>查看本次搜索词</summary><p>${suggestions.queries.map(q=>esc(typeof q==='string'?q:JSON.stringify(q))).join(' · ')}</p></details>`:''}`;
}
function refreshDraftImages(id){
  if(modal?.type!=='draft'||String(modal.id)!==String(id))return;
  const d=state.drafts.find(x=>String(x.id)===String(id)),section=$('#draft-image-section');if(!d||!section)return;
  const count=$('#draft-image-count')?.value||d.image_suggestions?.requested_count||6;
  if(activeDraft)activeDraft.image_suggestions=d.image_suggestions;
  section.innerHTML=renderDraftImages(d,count);
}
function updateDraftImageStatus(){
  if(modal?.type!=='draft')return;
  const d=state.drafts.find(x=>String(x.id)===String(modal.id)),el=$('#draft-image-status');
  if(d&&el)el.hidden=!draftImagesStale(d);
}
async function findDraftImages(button){
  if(!activeDraft)return;const id=String(activeDraft.id),count=imageCount($('#draft-image-count')?.value);
  if(imageJobRunning(id))return;
  imageStarts.add(id);
  await busy(button,async()=>{try{
    const saved=await saveDraft(null,{quiet:true});
    if(!saved){if(modal?.type==='draft'&&String(modal.id)===id)toast('请先保存新增的修改，再查找配图。',true);return;}
    if(modal?.type!=='draft'||String(modal.id)!==id)return;
    const result=await api(`/api/drafts/${encodeURIComponent(id)}/images`,{method:'POST',body:{count}});
    draftImageErrors.delete(id);
    await registerJob(result,'images',{draftId:id});
  }catch(e){draftImageErrors.set(id,e.message);toast(e.message,true);}finally{imageStarts.delete(id);refreshDraftImages(id);}});
}
async function copyImageBlock(button){
  const card=button.closest('.image-card'),field=card?.querySelector('.image-markdown textarea'),block=field?.value;if(!block)return;
  try{await navigator.clipboard.writeText(block);toast('图片、图注与来源已复制，可粘贴到正文需要的位置。');}
  catch{const details=card.querySelector('.image-markdown');if(details){details.open=true;field.focus();field.select();}toast('已展开图文块，请按 ⌘C 或 Ctrl+C 复制。');}
}
function showDraft(id){const d=state.drafts.find(d=>String(d.id)===String(id));if(!d)return;activeDraft={...d};draftPreview=false;showModal(isCozeDraft(d)?'Coze 原稿 · 待人工审阅':draftReviewInfo(d).review?'文章草稿 · 待人工审阅':'文章草稿',`${cozeDraftNotice(d)}<div class="field"><label class="form-label" for="draft-title">文章标题</label><input id="draft-title" value="${esc(d.title)}"></div><div class="notes-toolbar" style="padding:10px 0;background:transparent"><div class="preview-toggle"><button class="active" data-draft-mode="edit">编辑 Markdown</button><button data-draft-mode="preview">阅读视图</button></div><span class="help" id="draft-count">${chars(d.content).toLocaleString()} 字</span></div><div class="draft-editor"><label class="visually-hidden" for="draft-content">草稿正文</label><textarea id="draft-content" class="editor">${esc(d.content)}</textarea><div id="draft-preview" class="markdown-preview" hidden></div></div><section id="draft-review-section" class="draft-review-section"${draftReviewInfo(d).review?'':' hidden'}>${renderDraftReview(d)}</section><section id="draft-publication-section" class="draft-publication-section"${draftPublicationPackage(d)?'':' hidden'}>${renderPublicationPackage(d)}</section><section id="draft-image-section" class="draft-images-section">${renderDraftImages(d)}</section>${draftSources(d)}<div class="help">生成模型：${esc(d.model||'未记录')} · ${esc(fmtDate(d.created_at))}<br>请在发布前核对技术事实、引文与来源，补充你独有的行业判断。</div><div class="modal-actions"><button class="button secondary" data-action="record-draft-performance">${icon('chart')} 记录发布数据</button><button class="button secondary" data-action="copy-draft">复制正文</button><button class="button secondary" data-action="export-draft">${icon('file')} 导出 Markdown</button><button class="button primary" data-action="save-draft">${icon('check')} 保存修改</button></div>`,{wide:true,type:'draft',id});activeDraft={...d};}
async function saveDraft(button,{quiet=false}={}){
  if(!activeDraft)return false;
  if(draftSavingId){if(!quiet)toast('正在保存草稿，请稍候。');return false;}
  const savedId=activeDraft.id,title=$('#draft-title').value.trim(),content=$('#draft-content').value;
  draftSavingId=String(savedId);
  try{return await busy(button,async()=>{try{
    if(!title)throw new Error('请填写文章标题。');
    const result=await api(`/api/drafts/${encodeURIComponent(savedId)}`,{method:'PATCH',body:{title,content}});
    if(activeDraft&&String(activeDraft.id)===String(savedId)){
      activeDraft=result.draft||{...activeDraft,title,content};
      modalDirty=$('#draft-title').value.trim()!==title||$('#draft-content').value!==content;
      $('#draft-count').textContent=`${chars($('#draft-content').value).toLocaleString()} 字${modalDirty?' · 未保存':''}`;
    }
    await reloadState(true);refreshDraftImages(savedId);refreshDraftReview(savedId);refreshPublicationPackage(savedId);
    const stillCurrent=activeDraft&&String(activeDraft.id)===String(savedId);
    if(!quiet)toast(stillCurrent&&modalDirty?'已保存提交时的草稿，之后新增的编辑仍待保存。':'草稿已保存。');
    return Boolean(stillCurrent&&!modalDirty);
  }catch(e){toast(e.message,true);return false;}});}finally{draftSavingId=null;}
}

async function registerJob(data,action,info={}){
  if(!data.job_id)throw new Error('服务没有返回任务编号。');
  trackedJobs.set(String(data.job_id),{action,writingId:data.writing_id,...info});updateJobsStrip();
  if(info.resumed)toast('正在继续查询已有远端任务，没有重新提交生成。');else toast(({'coze-topics':'已提交 Coze 选题工作流，成功后整理为可选卡片。','coze-writer':'已提交 Coze 文案工作流，返回后等待你查看。','writing-prepare':'开始整理事实与提纲，完成后会等待你确认。','writing-continue':'开始生成并审核，完成后交付待人工审阅的稿件。',draft:'开始写作，可以继续整理素材。',images:'正在根据已保存的草稿联网找配图。',research:'开始搜索，结果会自动存入素材库。',analyze:'开始拆解，写法卡片生成后等待你审核。',test:'正在测试模型连接…'})[action]||'任务已开始。');
  await pollJobs();
}
function updateJobsStrip(){
  const strip=$('#job-strip');strip.hidden=!trackedJobs.size;if(!trackedJobs.size)return;
  const tasks=[...trackedJobs.values()],names={'coze-topics':'Coze 正在生成选题','coze-writer':'Coze 正在生成文案','writing-prepare':'正在整理事实与提纲','writing-continue':'正在生成与审核',draft:'正在写作',images:'正在找配图',research:'正在搜索产业文章',analyze:'正在拆解文章',test:'正在测试模型'};
  const modelTasks=tasks.some(t=>!['research','images','coze-topics','coze-writer'].includes(t.action)),imageTasks=tasks.some(t=>t.action==='images'),cozeTasks=tasks.some(t=>t.action.startsWith('coze-'));
  const hint=modelTasks?(state.settings.provider==='codex'?`${modelLabel()} 正在处理资料，使用同一账号 Codex 额度。`:state.settings.provider==='ollama'?'本地模型可能需要几分钟，你可以继续使用工作台。':'模型正在处理资料，你可以继续使用工作台。'):imageTasks?'根据正文提炼搜索词，查找有来源的图片；你可以继续编辑。':cozeTasks?'正在调用你配置的 Coze 工作流；结果会保留在 API 执行记录。':'正在检索并读取可访问网页，结果会自动保存。';
  const titles=tasks.map(t=>(t.action.startsWith('writing-')||t.action.startsWith('coze-'))&&t.stageMessage?t.stageMessage:t.action==='draft'&&t.message?.includes('配图')?'正在找配图':names[t.action]||'后台任务处理中');
  strip.innerHTML=`<span class="spinner"></span><span>${esc(titles.join(' · '))}。${esc(hint)}</span>`;
}
async function pollJobs(){
  if(jobsBusy||!trackedJobs.size)return;jobsBusy=true;
  try{for(const [id,meta] of [...trackedJobs]){
    let job;try{job=await api(`/api/jobs/${encodeURIComponent(id)}`);}catch(e){if(e.status===404){trackedJobs.delete(id);toast('任务已失效，请重新发起。',true);}continue;}
    meta.message=job.message||'';if(!meta.draftId&&job.draft_id)meta.draftId=job.draft_id;if(!meta.writingId&&job.writing_id)meta.writingId=job.writing_id;
    if(meta.action.startsWith('coze-')){job={...job,id:job.id||id,type:job.type||meta.action};meta.stageMessage=`${cozeRoleLabel(meta.action==='coze-writer'?'writer':'topics')} · ${cozeJobLabel(job)}`;meta.remoteTaskId=job.remote_task_id;refreshCozeJob(job);}
    if(meta.action.startsWith('writing-')&&meta.writingId){try{const run=await fetchWritingRun(meta.writingId);meta.stageMessage=run.message||writingStatus(run);refreshWritingModal(run);}catch(e){meta.stageMessage='读取写作进度中';}}
    if(job.status==='running'||job.status==='pending'||job.status==='queued')continue;
    trackedJobs.delete(id);
    if(job.status==='error'){
      const message=job.error||job.message||'任务没有完成，请重试。';
      if(meta.action==='images'&&meta.draftId){draftImageErrors.set(String(meta.draftId),message);refreshDraftImages(meta.draftId);}
      toast(message,true);if(meta.action.startsWith('coze-')){await reloadState(true);if(!modal)showCozeOutput({...job,id:job.id||id,type:job.type||meta.action});}continue;
    }
    await reloadState(true);
    if(meta.action.startsWith('coze-')){
      const completed={...job,id:job.id||id,type:job.type||meta.action},canOpen=!modal||(modal.type==='coze-output'&&String(modal.id)===String(id)&&!modalDirty);
      if(meta.action==='coze-topics'){try{const batch=await importCozeJob(completed,canOpen);toast(`Coze 已返回 ${batch.topics?.length||0} 个选题，请由你选择后再写作。`);}catch(e){toast(`API 原始返回已保留；选题整理未完成：${e.message}`,true);if(canOpen&&!modalDirty&&(!modal||(modal.type==='coze-output'&&String(modal.id)===String(id))))showCozeOutput(completed);}}
      else{const draftId=cozeSavedDraftId(completed);if(draftId&&state.drafts.some(d=>String(d.id)===String(draftId))){toast('Coze 原稿已保存到本地，尚未经过本地 B 独立审核。');if(canOpen)showDraft(draftId);}else{toast(completed.result?.draft_import_error?`Coze 返回已保留，保存草稿未完成：${completed.result.draft_import_error}`:'Coze 文案返回已保留，等待你查看与核对。',Boolean(completed.result?.draft_import_error));if(canOpen)showCozeOutput(completed);}}
    }else if(meta.action.startsWith('writing-')){
      const writingId=meta.writingId||job.result?.writing_id||job.result?.run?.id;
      const run=(state.writing_runs||[]).find(r=>String(r.id)===String(writingId))||job.result?.run;
      if(meta.action==='writing-prepare'){toast('事实与提纲已整理，请由你确认后再继续生成。');if(run){if(!modal)await openWritingRun(run.id);else refreshWritingModal(run);}}
      else if(run?.draft_id){toast(run.status==='needs_attention'?'稿件与待审包已保存，仍有问题需要人工处理。':'稿件与待审包已保存，等待人工审阅。');if(!modal||(modal.type==='writing'&&String(modal.id)===String(run.id)&&!modalDirty)){if(modal)closeModal(true);showDraft(run.draft_id);}else{refreshDraftReview(run.draft_id);refreshPublicationPackage(run.draft_id);refreshWritingModal(run);}}
      else if(run)refreshWritingModal(run);
    }else if(meta.action==='draft'){
      const resultDraft=job.result?.draft||job.result,draftId=job.result?.draft_id||resultDraft?.id;
      const draft=state.drafts.find(x=>String(x.id)===String(draftId))||state.drafts[0];
      if(job.result?.images_error){if(draft)draftImageErrors.set(String(draft.id),job.result.images_error);toast(`文章已保存，配图未完成：${job.result.images_error}`,true);}
      else toast(draftImageItems(draft).length?`文章已生成，并找到 ${draftImageItems(draft).length} 张配图。`:'文章草稿已生成。');
      if(draft){if(!modal)showDraft(draft.id);else refreshDraftImages(draft.id);}
    }else if(meta.action==='images'){
      const draftId=job.result?.draft_id||job.result?.draft?.id||meta.draftId;
      const draft=state.drafts.find(x=>String(x.id)===String(draftId));
      if(draftId){draftImageErrors.delete(String(draftId));refreshDraftImages(draftId);}
      const count=draftImageItems(draft).length;
      toast(count?`已找到 ${count} 张配图，来源和图注已整理。`:'本次没有找到合适的图片，可调整文章内容后重试。');
    }else if(meta.action==='analyze'){
      toast('文章拆解完成。请审核新生成的写法卡片。');
      if(modal?.type==='article'&&String(modal.id)===String(meta.articleId)&&!modalDirty)articleDetail(meta.articleId);
    }else if(meta.action==='research'){
      toast('搜索完成，真实来源已存入素材库。');
      if(page==='home'&&!modal&&!composer.brief)await navigate('articles');
    }else if(meta.action==='test')toast(typeof job.result==='string'?job.result:(job.result?.message||job.message||'模型连接正常。'));
  }}finally{jobsBusy=false;updateJobsStrip();refreshQuickTopics();}
}

document.addEventListener('click',async event=>{
  const b=event.target.closest('button,a');if(!b)return;
  if(b.matches('a.brand')){event.preventDefault();await navigate('home');return;}
  if(b.dataset.page){await navigate(b.dataset.page);return;}
  if(b.dataset.pageModal){if(closeModal())await navigate(b.dataset.pageModal);return;}
  if(b.dataset.topic){composer.brief=b.dataset.topic;$('#brief').value=composer.brief;$('#brief').focus();return;}
  if(b.dataset.addTab){addArticle(b.dataset.addTab);return;}
  if(b.dataset.article){articleDetail(b.dataset.article);return;}
  if(b.dataset.editArticle){editArticle(b.dataset.editArticle);return;}
  if(b.dataset.editCard){editCard(b.dataset.editCard);return;}
  if(b.dataset.draft){showDraft(b.dataset.draft);return;}
  if(b.dataset.writingRun){await openWritingRun(b.dataset.writingRun,b);return;}
  if(b.dataset.topicBatch){await reopenTopicBatch(b.dataset.topicBatch,b);return;}
  if(b.dataset.copyPublication){await copyPublication(b.dataset.copyPublication);return;}
  if(b.dataset.copyPublicationTitle!==undefined){await copyPublication(null,b.dataset.copyPublicationTitle);return;}
  if(b.dataset.cozeSchema){await readCozeSchema(b.dataset.cozeSchema,b);return;}
  if(b.dataset.cozeRole){await openCozeRun(b.dataset.cozeRole,b);return;}
  if(b.dataset.cozeOutput){await openCozeOutput(b.dataset.cozeOutput,b);return;}
  if(b.dataset.cozeSavedDraft){await openCozeSavedDraft(b.dataset.cozeSavedDraft,b);return;}
  if(b.dataset.importCozeDraft){await importCozeDraft(b.dataset.importCozeDraft,b);return;}
  if(b.dataset.resumeCozeJob){await resumeCozeJob(b.dataset.resumeCozeJob,b);return;}
  if(b.dataset.cozeFill!==undefined){const input=$(`#coze-param-${Number(b.dataset.cozeFill)}`);if(input&&cozeSession?.topic){if(input.value&&!confirm('这个参数已有内容。确定替换为所选选题吗？'))return;input.value=cozeSession.topic;modalDirty=true;input.focus();}return;}
  if(b.dataset.importCozeJob){await busy(b,async()=>{try{await importCozeJob(await api(`/api/jobs/${encodeURIComponent(b.dataset.importCozeJob)}`));}catch(e){toast(e.message,true);}});return;}
  if(b.dataset.refreshWriting){await busy(b,async()=>{try{const run=await fetchWritingRun(b.dataset.refreshWriting);refreshWritingModal(run);}catch(e){toast(e.message,true);}});return;}
  if(b.dataset.writingDraft){if(modalDirty&&!confirm('当前调整要求尚未保存。确定放弃并打开待审稿吗？'))return;closeModal(true);if(!state.drafts.some(d=>String(d.id)===String(b.dataset.writingDraft)))await reloadState(false);showDraft(b.dataset.writingDraft);return;}
  if(b.dataset.editPerformance){openPerformanceEditor(b.dataset.editPerformance);return;}
  if(b.dataset.copyPerformance){openPerformanceEditor(b.dataset.copyPerformance,{copy:true});return;}
  if(b.dataset.copyImage!==undefined){await copyImageBlock(b,b.dataset.copyImage);return;}
  if(b.dataset.draftMode){draftPreview=b.dataset.draftMode==='preview';$('#draft-content').hidden=draftPreview;$('#draft-preview').hidden=!draftPreview;$('#draft-preview').textContent=$('#draft-content').value;$$('[data-draft-mode]').forEach(x=>x.classList.toggle('active',x===b));return;}
  if(b.dataset.approveCard){await busy(b,async()=>{try{await api(`/api/cards/${encodeURIComponent(b.dataset.approveCard)}`,{method:'PATCH',body:{approved:b.dataset.approved==='true'}});await reloadState();toast(b.dataset.approved==='true'?'卡片已启用，将用于后续写作。':'卡片已停用。');}catch(e){toast(e.message,true);}});return;}
  if(b.dataset.analyze){await busy(b,async()=>{try{const result=await api(`/api/articles/${encodeURIComponent(b.dataset.analyze)}/analyze`,{method:'POST',body:{}});await registerJob(result,'analyze',{articleId:b.dataset.analyze});}catch(e){toast(e.message,true);}});return;}
  if(b.dataset.archiveArticle){if(!confirm('将这篇文章移出素材库？已生成的草稿会保留。'))return;await busy(b,async()=>{try{await api(`/api/articles/${encodeURIComponent(b.dataset.archiveArticle)}`,{method:'DELETE'});closeModal(true);await reloadState();toast('文章已移出素材库。');}catch(e){toast(e.message,true);}});return;}
  const action=b.dataset.action;
  if(action==='import-topics'){if(!modalDirty||confirm('当前输入尚未保存。确定放弃并打开手动导入吗？'))openTopicImport();}
  else if(action==='writing-policy')await openWritingPolicy(b);
  else if(action==='reload-writing-policy')await openWritingPolicy(b,{refresh:true});
  else if(action==='copy-writing-policy')await copyWritingPolicy();
  else if(action==='copy-writing-policy-path')await copyWritingPolicy(true);
  else if(action==='coze-settings')await openCozeSettings(b);
  else if(action==='download-coze-markdown')downloadCozeMarkdown();
  else if(action==='copy-coze-output'){try{await navigator.clipboard.writeText($('#coze-output-raw')?.value||'');toast('Coze 原始返回已复制。');}catch{toast('浏览器无法直接复制，请在结果框中全选并复制。',true);}}
  else if(action==='refresh-writing')await busy(b,loadWritingRuns);
  else if(action==='add-performance')openPerformanceEditor();
  else if(action==='import-performance')openPerformanceImport();
  else if(action==='review-performance')await reviewPerformance(b);
  else if(action==='record-draft-performance')await recordDraftPerformance(b);
  else if(action==='use-draft-timing'){const d=state.drafts.find(d=>String(d.id)===String(b.dataset.draftId));if(d&&typeof d.generation_seconds==='number'&&Number.isFinite(d.generation_seconds)){$('#perf-generation_seconds').value=d.generation_seconds;modalDirty=true;}}
  else if(action==='add-article')addArticle();
  else if(action==='research')researchModal();
  else if(action==='use-codex-gpt6'){$('#provider').value='codex';updateProviderFields(false);$('#model').value='gpt-6-astra';settingsDirty=true;$('#model').focus();}
  else if(action==='close-modal')closeModal();
  else if(action==='save-notes')await saveNotes(b);
  else if(action==='open-notes')await busy(b,async()=>{try{await api('/api/open-notes',{method:'POST',body:{}});toast('已请求本地编辑器打开当前 Markdown 文件。');}catch(e){toast(e.message,true);}});
  else if(action==='reload-notes'){if(noteSession.dirty&&!confirm('加载磁盘版本会放弃网页编辑区中未保存的修改，是否继续？'))return;noteSession={...noteConflict,dirty:false};state.notes=noteConflict;noteConflict=null;$('#notes-editor').value=noteSession.text;updateNoteIndicators();}
  else if(action==='merge-notes'){if(confirm('确认已在编辑区合并需要保留的内容？保存后将更新磁盘文件，旧版本会备份。'))await saveNotes(b,true);}
  else if(action==='save-draft')await saveDraft(b);
  else if(action==='find-draft-images')await findDraftImages(b);
  else if(action==='copy-draft'){try{await navigator.clipboard.writeText(`# ${$('#draft-title').value.trim()}\n\n${$('#draft-content').value}`);toast('标题与正文已复制。');}catch{toast('浏览器无法直接复制，请在编辑区全选并复制。',true);}}
  else if(action==='export-draft'){const id=activeDraft?.id;if(!id)return;if(modalDirty&&!(await saveDraft(null)))return;const a=document.createElement('a');a.href=`/api/export/drafts/${encodeURIComponent(id)}`;a.download='';document.body.append(a);a.click();a.remove();}
  else if(action==='reload')location.reload();
  else if(action==='test-model'){if(settingsDirty){toast('请先保存设置，再测试你刚修改的模型。',true);return;}await busy(b,async()=>{try{await registerJob(await api('/api/test-model',{method:'POST',body:{}}),'test');}catch(e){toast(e.message,true);}});}
});

document.addEventListener('input',event=>{
  const e=event.target;
  if(e.closest('#compose-form'))syncComposer();
  if(e.id==='coze-quick-topic')cozeQuickTopic=e.value;
  if(e.id==='library-filter'){articleFilter.query=e.value;$('#article-results').innerHTML=articleResults();}
  if(e.id==='performance-filter'){performanceQuery=e.value;$('#performance-records').innerHTML=performanceRecords();}
  if(e.id==='notes-editor'&&noteSession){noteSession.text=e.value;noteSession.dirty=e.value!==state.notes.text;updateNoteIndicators();}
  if(e.closest('#settings-form'))settingsDirty=true;
  if(e.closest('.modal')&&e.matches('input,textarea,select')&&e.id!=='draft-image-count'&&!e.readOnly)modalDirty=true;
  if(e.id==='writing-policy-text'&&$('#writing-policy-count'))$('#writing-policy-count').textContent=`${chars(e.value).toLocaleString()} 字 · 未保存`;
  if(e.id==='draft-content')$('#draft-count').textContent=`${chars(e.value).toLocaleString()} 字 · 未保存`;
  if(e.id==='draft-title'||e.id==='draft-content'){updateDraftImageStatus();updateDraftReviewStatus();updatePublicationStatus();}
});
document.addEventListener('change',event=>{
  const e=event.target;
  if(e.closest('#compose-form'))syncComposer();
  if(e.id==='heat-filter'){articleFilter.heat=e.value;$('#article-results').innerHTML=articleResults();}
  if(e.id==='article-sort'){articleFilter.sort=e.value;$('#article-results').innerHTML=articleResults();}
  if(e.closest('#settings-form'))settingsDirty=true;
  if(e.closest('.modal')&&e.matches('input,textarea,select')&&e.id!=='draft-image-count'&&!e.readOnly)modalDirty=true;
  if(e.id==='provider')updateProviderFields(true);
  if(e.id==='coze-use-json')syncCozeJSONMode();
  if(e.id==='perf-draft_id')selectPerformanceDraft(e.value);
  if(e.id==='performance-import-file')readPerformanceImport(e.files?.[0]);
});
function syncComposer(){const form=$('#compose-form');if(!form)return;const fd=new FormData(form);composer={brief:fd.get('brief'),audience:fd.get('audience'),tone:fd.get('tone'),length:fd.get('length'),article_ids:fd.getAll('article_ids').map(String),use_web:fd.has('use_web'),find_images:fd.has('find_images'),image_count:imageCount(fd.get('image_count')||composer.image_count)};if($('#compose-image-count'))$('#compose-image-count').disabled=!composer.find_images;}

document.addEventListener('submit',async event=>{
  const form=event.target;if(!form.matches('form'))return;event.preventDefault();$('.form-error',form)?.remove();const button=event.submitter||$('button[type="submit"]',form),fd=new FormData(form),submittedModal=modal;
  form.inert=true;
  await busy(button,async()=>{try{
    if(form.id==='compose-form'){syncComposer();if(!composer.brief.trim())throw new Error('请先写下文章主题。');const max=composer.use_web?5:8;if(composer.article_ids.length>max)throw new Error(`当前最多选择 ${max} 篇参考素材，请减少勾选后继续。`);const result=await api('/api/writing/prepare',{method:'POST',body:{...composer,card_ids:[]}});await registerJob(result,'writing-prepare');await loadWritingRuns();}
    else if(form.id==='topic-import-form'){
      const content=String(fd.get('content')||'');if(!content.trim())throw new Error('请粘贴真实的 Coze 选题输出。');
      const result=await api('/api/topics/import',{method:'POST',body:{content,source:'coze'}});
      if(!result.batch)throw new Error('没有收到选题解析结果，请重试。');
      topicBatches=[result.batch,...topicBatches.filter(b=>String(b.id)!==String(result.batch.id))];if(modal===submittedModal)showTopicBatch(result.batch);else toast('选题卡片已保存，可从导入记录中打开。');
    }
    else if(form.id==='topic-select-form'){
      if(event.submitter?.value==='coze-quick'){openQuickWriter(selectedCozeTopic(fd,activeTopicBatch));return;}
      const brief=selectedTopicBrief(fd,activeTopicBatch);
      if(event.submitter?.value==='coze'){await openCozeRun('writer',null,brief);return;}
      if(composer.brief.trim()&&composer.brief.trim()!==brief.trim()&&!confirm('写作区已有内容。确定用所选选题替换当前写作要求吗？'))return;
      composer.brief=brief;closeModal(true);await navigate('home');toast('选题已带入写作要求。核对后点击「整理事实与提纲」即可。');$('#brief')?.focus();
    }
    else if(form.id==='coze-quick-topics-form'){cozeQuickTopic=String(fd.get('topic')||'');if(cozeQuickTopic.trim().length>2000)throw new Error('研究方向超过 2,000 字符，请精简后提交；当前输入未截断。');await submitQuickCoze('topics',{topic:cozeQuickTopic.trim()},submittedModal);}
    else if(form.id==='coze-quick-writer-form'){await submitQuickCoze('writer',quickWriterPayload(fd),submittedModal);}
    else if(form.id==='coze-settings-form'){
      const roles={};for(const role of ['topics','writer']){const token=String(fd.get(`${role}_token`)||'');const clear=fd.has(`${role}_clear_token`);if(clear&&token)throw new Error('清除 Token 与填写新 Token 不能同时选择。');roles[role]={base_url:String(fd.get(`${role}_base_url`)||'').trim()};if(token||clear)roles[role].token=clear?'':token;}
      const result=await api('/api/coze/settings',{method:'PUT',body:{roles}});cozeSettings=result.roles||{};if(modal===submittedModal){modalDirty=false;showModal('Coze API 接入',renderCozeSettings(),{wide:true,type:'coze-settings'});}toast('API 配置已保存到本机。读取真实参数后即可填写并执行。');
    }
    else if(form.id==='coze-run-form'){
      const role=form.dataset.role,inputs=cozeInputsPayload(fd);if(cozeSession?.role!==role)throw new Error('助手参数状态已改变，请重新打开执行面板。');
      const result=await api(`/api/coze/${role}/run`,{method:'POST',body:{inputs}});if(!result.job_id)throw new Error('服务没有返回任务编号。');cozePendingTopic='';if(modal===submittedModal){closeModal(true);showCozeOutput({id:result.job_id,type:`coze-${role}`,status:'running'});}await registerJob(result,`coze-${role}`);
    }
    else if(form.id==='writing-policy-form'){
      const text=String(fd.get('text')||''),mode=String(fd.get('mode')||'lite');if(!text.trim())throw new Error('请保留一份完整的写作规范。');
      const policy=await api('/api/writing/policy',{method:'PUT',body:{revision:String(fd.get('revision')||''),text,mode}});writingPolicySession=policy;
      if(modal===submittedModal){modalDirty=false;showModal('写作规范',renderWritingPolicy(policy),{wide:true,type:'writing-policy'});}toast('写作规范已保存，将用于新建写作任务。');
    }
    else if(form.id==='writing-continue-form'){
      const id=form.dataset.writingId;
      const result=await api(`/api/writing/${encodeURIComponent(id)}/continue`,{method:'POST',body:{revision:String(fd.get('revision')||''),editor_direction:String(fd.get('editor_direction')||'').trim()}});
      if(modal===submittedModal)modalDirty=false;await registerJob(result,'writing-continue',{writingId:id});
      const run=await fetchWritingRun(id);refreshWritingModal(run);
    }
    else if(form.id==='add-article-form'){const result=await api('/api/articles',{method:'POST',body:{title:fd.get('title'),content:fd.get('content'),url:fd.get('url'),author:fd.get('author'),metrics:readMetrics(fd)}});closeModal(true);await reloadState();toast('文章已收藏，可以开始拆解。');articleDetail(result.article.id);}
    else if(form.id==='import-form'){const result=await api('/api/import',{method:'POST',body:{url:fd.get('url')}});closeModal(true);await reloadState();toast('文章已成功导入。');articleDetail(result.article.id);}
    else if(form.id==='upload-form'){const file=fd.get('file');if(!file?.size)throw new Error('请选择需要上传的文件。');if(file.size>8*1024*1024)throw new Error('文件过大，请上传 8 MB 以内的文件。');const base64=await new Promise((resolve,reject)=>{const r=new FileReader();r.onload=()=>resolve(String(r.result).split(',')[1]);r.onerror=()=>reject(new Error('文件读取失败。'));r.readAsDataURL(file);});const result=await api('/api/upload',{method:'POST',body:{filename:file.name,data:base64}});closeModal(true);await reloadState();toast('文件已导入素材库。');articleDetail(result.article.id);}
    else if(form.id==='research-form'){const result=await api('/api/research',{method:'POST',body:{query:fd.get('query'),scope:fd.get('scope'),max_results:5}});closeModal(true);if(page!=='articles')await navigate('articles');await registerJob(result,'research');}
    else if(form.id==='edit-article-form'){const id=form.dataset.id;await api(`/api/articles/${encodeURIComponent(id)}`,{method:'PATCH',body:{title:fd.get('title'),content:fd.get('content'),url:fd.get('url'),author:fd.get('author'),metrics:readMetrics(fd),content_kind:'full'}});closeModal(true);await reloadState();toast('素材已更新。');articleDetail(id);}
    else if(form.id==='edit-card-form'){await api(`/api/cards/${encodeURIComponent(form.dataset.id)}`,{method:'PATCH',body:{title:fd.get('title'),category:fd.get('category'),content:fd.get('content'),approved:fd.has('approved')}});closeModal(true);await reloadState();toast('写法卡片已保存。');}
    else if(form.id==='performance-form'){
      const id=form.dataset.recordId,payload=performancePayload(fd);
      await api(id?`/api/performance/${encodeURIComponent(id)}`:'/api/performance',{method:id?'PATCH':'POST',body:payload});
      closeModal(true);performanceReview=null;await reloadState();toast(id?'统计记录已修正，历史版本已保留。':'新的统计快照已保存。');
    }
    else if(form.id==='performance-import-form'){
      if(!performanceImport)throw new Error('请先选择并读取一个 CSV 或 JSON 文件。');
      const result=await api('/api/performance/import',{method:'POST',body:{format:performanceImport.format,content:performanceImport.content}});
      closeModal(true);performanceImport=null;performanceReview=null;await reloadState();toast(`已导入 ${result.imported??0} 条统计记录。`);
    }
    else if(form.id==='settings-form'){const payload=Object.fromEntries(fd.entries());if(payload.provider==='codex'){payload.base_url=$('#base_url').value||state.settings.base_url||'http://127.0.0.1:11434';delete payload.api_key;}await api('/api/settings',{method:'PUT',body:payload});settingsDirty=false;noteSession=null;noteConflict=null;await reloadState();toast('设置已保存。');}
  }catch(e){const writingConflict=e.status===409&&form.id==='writing-continue-form';if(writingConflict){try{const latest=await fetchWritingRun(form.dataset.writingId);refreshWritingModal(latest);}catch{}}const conflict=e.status===409&&form.id==='performance-form'&&form.dataset.recordId;if(conflict)await loadPerformance();const policyConflict=e.status===409&&form.id==='writing-policy-form';const message=policyConflict?`${e.message} 当前规范与写作方式已保留，未覆盖文件。请先复制需要保留的内容，再点击「重新读取文件」核对最新版本。`:writingConflict?`${e.message} 当前调整要求已保留，未自动生成或覆盖其他版本；请复制需要保留的内容后重新打开任务。`:conflict?`${e.message} 当前输入已保留，未覆盖其他版本；请核对后关闭并重新打开记录再保存。`:e.message;if(form.isConnected)formError(form,message);else toast(message,true);}finally{form.inert=false;}});
});

document.addEventListener('keydown',e=>{if(e.key==='Escape'&&modal)closeModal();if((e.metaKey||e.ctrlKey)&&e.key==='s'){if(page==='notes'&&!modal){e.preventDefault();saveNotes($('[data-action="save-notes"]'));}else if(modal?.type==='draft'){e.preventDefault();saveDraft($('[data-action="save-draft"]'));}}if(e.key==='Tab'&&modal){const focusable=$$('.modal button:not(:disabled),.modal input,.modal textarea,.modal select,.modal a[href],.modal summary').filter(x=>!x.hidden&&x.getClientRects().length);const first=focusable[0],last=focusable[focusable.length-1];if(e.shiftKey&&document.activeElement===first){e.preventDefault();last?.focus();}else if(!e.shiftKey&&document.activeElement===last){e.preventDefault();first?.focus();}}});

document.addEventListener('error',event=>{
  if(event.target.matches?.('img[data-image-preview]'))event.target.closest('.image-card-preview')?.classList.add('image-unavailable');
},true);

window.addEventListener('beforeunload',e=>{if(hasUnsaved()||modalDirty||composer.brief.trim()||cozeQuickTopic.trim()){e.preventDefault();e.returnValue='';}});
window.addEventListener('hashchange',()=>navigate(location.hash.slice(1)));

async function init(){try{page=labels[location.hash.slice(1)]?location.hash.slice(1):'home';await reloadState();for(const job of state.jobs){if(['running','pending','queued'].includes(job.status)){const types={draft:'draft',drafts:'draft',generate:'draft',research:'research',analyze:'analyze',test_model:'test','test-model':'test',test:'test','draft-images':'images','coze-topics':'coze-topics','coze-writer':'coze-writer','writing-prepare':'writing-prepare','writing-continue':'writing-continue'};trackedJobs.set(String(job.id),{action:types[job.type]||job.type,draftId:job.draft_id,writingId:job.writing_id});}}updateJobsStrip();setInterval(pollJobs,2200);setInterval(pollNotes,5000);}catch(e){$('#main').innerHTML=emptyState('settings','暂时无法连接本地工作台',esc(e.message),'<button class="button secondary" data-action="reload">重新连接</button>');$('#model-status').textContent='本地服务未连接';$('#model-status').classList.add('status-error');}}
init();
