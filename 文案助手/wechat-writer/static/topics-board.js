'use strict';

// Separate page; generation and draft persistence use the existing Coze flow.
const topicBoard = {batch:'',query:'',selection:null,limit:12,loading:false,loaded:false,error:'',loadedAt:0};
try {
  const saved=JSON.parse(localStorage.getItem('zili.topic-board.selection')||'null');
  if(saved&&typeof saved.batch_id==='string'&&typeof saved.topic_id==='string')topicBoard.selection=saved;
} catch { /* Storage is optional; the page works without it. */ }

function saveBoardSelection(selection) {
  topicBoard.selection=selection;
  try {
    if(selection)localStorage.setItem('zili.topic-board.selection',JSON.stringify(selection));
    else localStorage.removeItem('zili.topic-board.selection');
  } catch { /* No article or input depends on browser storage. */ }
}
function boardEntries() {
  return topicBatches.flatMap(batch=>(Array.isArray(batch.topics)?batch.topics:[]).map(topic=>({batch,topic})));
}
function boardSelectedEntry() {
  const selected=topicBoard.selection;
  return selected?boardEntries().find(({batch,topic})=>String(batch.id)===selected.batch_id&&String(topic.id)===selected.topic_id):null;
}
function boardSelectionMatches(batch,topic) {
  return topicBoard.selection?.batch_id===String(batch.id)&&topicBoard.selection?.topic_id===String(topic.id);
}
function boardFilteredEntries() {
  const query=topicBoard.query.trim().toLocaleLowerCase();
  return boardEntries().filter(({batch,topic})=>(!topicBoard.batch||String(batch.id)===topicBoard.batch)&&
    (!query||[topic.title,topic.brief,topic.reason].some(value=>String(value||'').toLocaleLowerCase().includes(query))));
}
function boardBatchOptions() {
  return `<option value="">所有批次（${topicBatches.length} 批）</option>${topicBatches.map(batch=>`<option value="${esc(batch.id)}"${topicBoard.batch===String(batch.id)?' selected':''}>${esc(fmtDate(batch.created_at))} · ${batch.source==='coze_api'?'Coze 推荐':'手动导入'} · ${(batch.topics||[]).length} 个选题</option>`).join('')}`;
}
function boardCardsHTML() {
  const all=boardEntries(),filtered=boardFilteredEntries();
  if(!all.length)return emptyState('cards',topicBoard.loaded?'还没有选题，先收集一批。':'正在读取本机选题…',topicBoard.loaded?'在左侧输入研究方向，获取 Coze 推荐；也可以粘贴已有的选题结果。':'你之前保存的选题会显示在这里。');
  if(!filtered.length)return emptyState('search','没有匹配的选题','换个关键词，或查看所有批次。','<button class="button secondary small" type="button" data-board-action="reset-filter">清除筛选</button>');
  return `<div class="board-card-grid">${filtered.slice(0,topicBoard.limit).map(({batch,topic},index)=>{
    const selected=boardSelectionMatches(batch,topic),brief=String(topic.brief||''),reason=String(topic.reason||'');
    const sourceLinks=(Array.isArray(topic.source_urls)?topic.source_urls:[]).map(safeURL).filter(Boolean);
    return `<article class="board-card${selected?' is-selected':''}" data-board-card data-batch-id="${esc(batch.id)}" data-topic-id="${esc(topic.id)}">
      <div class="board-card-meta"><span>${batch.source==='coze_api'?'Coze 推荐':'手动导入'} · ${esc(fmtDate(batch.created_at))}</span><span>${String(index+1).padStart(2,'0')}</span></div>
      <label class="board-choice"><input type="radio" name="board-topic" data-board-pick data-batch-id="${esc(batch.id)}" data-topic-id="${esc(topic.id)}"${selected?' checked':''}><span>${esc(topic.title||'未命名选题')}</span></label>
      <p class="board-card-brief">${esc(brief?brief.slice(0,220)+(brief.length>220?'…':''):'未提供写作方向，可在开始写作前补充。')}</p>
      <details class="board-card-details"><summary>查看完整方向、理由与来源</summary><div><h4>写作方向</h4><p>${esc(brief||'未提供')}</p><h4>选题理由</h4><p>${esc(reason||'未提供')}</p><h4>来源链接 · 待核实</h4>${sourceLinks.length?sourceLinks.map(url=>`<a href="${esc(url)}" target="_blank" rel="noopener noreferrer">${esc(url)}</a>`).join(''):'<p>未提供可用来源链接</p>'}</div></details>
    </article>`;
  }).join('')}</div>${filtered.length>topicBoard.limit?`<div class="board-more"><button class="button secondary" type="button" data-board-action="more">再显示 ${Math.min(12,filtered.length-topicBoard.limit)} 个选题</button></div>`:''}`;
}
function boardSelectedHTML() {
  const entry=boardSelectedEntry(),pending=cozeRolePending('writer');
  return `<div class="board-selected-copy"><span>${entry?'已选中 1 个选题':'还没有选中选题'}</span><strong>${entry?esc(entry.topic.title):'挑一个你认为值得写的问题。'}</strong><p>${pending?'已有文案任务正在处理，可在下方查看进度。':'确认读者和写作要求后，Coze 自动生成正文并保存为本地草稿。'}</p></div><div class="board-selected-actions">${entry?'<button type="button" class="text-button" data-board-action="clear-selection">取消选择</button>':''}<button class="button primary" type="button" data-board-action="write"${entry&&!pending?'':' disabled'}>${icon('spark')} 用这个选题写稿 ${icon('arrow')}</button></div>`;
}
function boardWriterJobsHTML() {
  const jobs=cozeJobs().filter(job=>job.type==='coze-writer').slice(0,5);
  return jobs.length?jobs.map(job=>`<div class="board-job"><div><strong>Coze 文案任务</strong><span>${esc(fmtDate(job.created_at))} · ${esc(cozeJobLabel(job))}</span></div><div class="board-job-actions"><button class="text-button" type="button" data-coze-output="${esc(job.id)}">查看进度与结果 ${icon('arrow')}</button>${cozeSavedDraftId(job)?`<button class="button secondary small" type="button" data-coze-saved-draft="${esc(cozeSavedDraftId(job))}">打开草稿</button>`:''}</div></div>`).join(''):'<p class="board-job-empty">选定题目并提交写作后，可在这里查看进度。生成完成的文章也会保留在「文章草稿」。</p>';
}
function renderTopicBoard() {
  queueMicrotask(()=>{const cards=$('#board-cards');if(cards)cards._boardHTML=boardCardsHTML();loadTopicBoard();});
  return `<section class="topic-board">${pageHeading('A / TOPIC DESK','先选题，再写一篇。','把推荐变成你的判断，再交给文案助手。','<button class="button secondary" type="button" data-page="drafts">'+icon('file')+' 查看文章草稿</button>')}
    <div class="board-steps"><span><b>01</b> 获取推荐</span>${icon('arrow')}<span><b>02</b> 挑选方向</span>${icon('arrow')}<span><b>03</b> 确认要求，自动写稿</span></div>
    <div class="board-layout"><aside class="board-sidebar">${renderQuickTopics()}<div class="board-guidance"><h3>选题由你拍板。</h3><p>先看信息是否扎实，再看是否与整灯厂、上游供应商的工作有关。推荐理由供判断参考，来源仍需核实。</p><p>你可以在写稿前加入自己的观点与资料。生成结果保留为待审草稿。</p></div></aside>
      <section class="board-library" aria-label="选题库"><div class="board-library-heading"><h2>我的选题库 <span id="board-count">${boardEntries().length} 个</span></h2><button class="text-button" type="button" data-board-action="refresh">刷新选题</button></div>
        <div class="board-toolbar"><label><span class="visually-hidden">搜索选题</span><input id="board-query" type="search" placeholder="搜索标题、方向或理由" value="${esc(topicBoard.query)}"></label><label><span class="visually-hidden">选择选题批次</span><select id="board-batch">${boardBatchOptions()}</select></label></div>
        <p id="board-load-status" class="board-load-status" role="status"></p><div id="board-cards">${boardCardsHTML()}</div>
      </section></div>
    <section id="board-selected" class="board-selected${boardSelectedEntry()?' has-selection':''}" aria-label="选中的选题">${boardSelectedHTML()}</section>
    <section class="board-writing-history"><div class="board-library-heading"><h2>最近的写作任务</h2><button type="button" class="text-button" data-action="coze-settings">全部执行记录 ${icon('arrow')}</button></div><div id="board-writer-jobs">${boardWriterJobsHTML()}</div></section>
  </section>`;
}
function updateBoardHTML(selector,html) {
  const node=$(selector);
  // Retain open details and focused controls when polling returns unchanged data.
  if(node&&node._boardHTML!==html){node.innerHTML=html;node._boardHTML=html;}
}
function refreshBoardSelection() {
  $$('[data-board-card]').forEach(card=>{
    const selected=topicBoard.selection?.batch_id===card.dataset.batchId&&topicBoard.selection?.topic_id===card.dataset.topicId;
    card.classList.toggle('is-selected',Boolean(selected));
    $('[data-board-pick]',card).checked=Boolean(selected);
  });
  updateBoardHTML('#board-selected',boardSelectedHTML());
  $('#board-selected')?.classList.toggle('has-selection',Boolean(boardSelectedEntry()));
}
function refreshTopicBoard(cards=false) {
  if(page!=='topics')return;
  updateBoardHTML('#board-count',`${boardFilteredEntries().length} / ${boardEntries().length} 个`);
  updateBoardHTML('#board-batch',boardBatchOptions());
  if(cards)updateBoardHTML('#board-cards',boardCardsHTML());
  updateBoardHTML('#board-load-status',topicBoard.error?`读取失败：${esc(topicBoard.error)}。已加载的选题仍保留，可点击刷新重试。`:topicBoard.loading&&!topicBoard.loaded?'正在读取本机选题…':topicBoard.loaded?(boardEntries().length?'选题已保存在本机 · 可随时回来继续挑选':'获取后的选题会保存在本机，方便随时回来挑选'):'');
  refreshBoardSelection();
  updateBoardHTML('#board-writer-jobs',boardWriterJobsHTML());
  refreshQuickTopics();
}
async function loadTopicBoard() {
  if(page!=='topics'||topicBoard.loading)return;
  topicBoard.loading=true;
  refreshTopicBoard();
  try {
    const previous=JSON.stringify(topicBatches),result=await api('/api/topics');
    if(!Array.isArray(result.batches))throw new Error('选题数据格式异常');
    topicBatches=result.batches;
    if(topicBoard.batch&&!topicBatches.some(batch=>String(batch.id)===topicBoard.batch))topicBoard.batch='';
    const firstLoad=!topicBoard.loaded;
    topicBoard.loaded=true;topicBoard.error='';topicBoard.loadedAt=Date.now();
    refreshTopicBoard(firstLoad||previous!==JSON.stringify(topicBatches));
  } catch(error) {topicBoard.error=error.message;}
  finally {topicBoard.loading=false;refreshTopicBoard();}
}
document.addEventListener('input',event=>{
  if(event.target.id!=='board-query')return;
  topicBoard.query=event.target.value;topicBoard.limit=12;refreshTopicBoard(true);
});
document.addEventListener('change',event=>{
  if(event.target.id==='board-batch'){topicBoard.batch=event.target.value;topicBoard.limit=12;refreshTopicBoard(true);}
  if(event.target.matches('[data-board-pick]')){
    saveBoardSelection({batch_id:event.target.dataset.batchId,topic_id:event.target.dataset.topicId});
    refreshBoardSelection();
    $('#board-cards')._boardHTML=boardCardsHTML();
  }
});
document.addEventListener('click',async event=>{
  const button=event.target.closest('[data-board-action]');
  if(!button||button.disabled)return;
  const action=button.dataset.boardAction;
  if(action==='refresh'){await busy(button,loadTopicBoard);return;}
  if(action==='more'){topicBoard.limit+=12;refreshTopicBoard(true);return;}
  if(action==='reset-filter'){topicBoard.batch='';topicBoard.query='';topicBoard.limit=12;$('#board-query').value='';refreshTopicBoard(true);return;}
  if(action==='clear-selection'){saveBoardSelection(null);refreshBoardSelection();$('#board-cards')._boardHTML=boardCardsHTML();return;}
  if(action==='write'){
    const entry=boardSelectedEntry();
    if(!entry){toast('请先选择一个选题。',true);return;}
    if(cozeRolePending('writer')){toast('已有文案任务正在处理，请先查看进度。',true);return;}
    openQuickWriter({batch_id:String(entry.batch.id),topic_id:String(entry.topic.id),title:String(entry.topic.title||''),brief:String(entry.topic.brief||''),reason:String(entry.topic.reason||'')});
  }
});
setInterval(()=>{
  if(typeof page!=='undefined'&&page==='topics'&&!document.hidden){
    refreshTopicBoard(true);
    if(Date.now()-topicBoard.loadedAt>5000)loadTopicBoard();
  }
},2500);
