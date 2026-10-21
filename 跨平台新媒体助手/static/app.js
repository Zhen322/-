/* Cross-platform new-media assistant — SPA.
   Views: plan, topics, materials, projects, review, publish, feedback, settings.
   Long actions return a job id; the UI polls until the job finishes. */
"use strict";

const VIEW_TITLES = {
  brain: "智能体",
  research: "资料库", project: "项目与制作",
  publish: "发布管理", settings: "设置",
};
const PLATFORM_LABEL = { wechat: "公众号", zhihu: "知乎", red: "小红书", x: "X" };
const STATUS_LABEL = {
  pending: "待发布", executing: "执行中", processing: "平台处理中",
  published: "已发布", failed: "失败", unknown: "结果未知", blocked: "接入受阻",
};
const STATUS_CLASS = {
  pending: "pill--info", executing: "pill--warn", processing: "pill--warn",
  published: "pill--ok", failed: "pill--bad", unknown: "pill--warn",
  blocked: "pill--bad",
};

let STATE = null;
let CURRENT_VIEW = "brain";
let VIEW_CTX = {};           // per-view scratch (selected ids, etc.)

/* ---------------- helpers ---------------- */

async function api(path, method = "GET", body = null) {
  const opts = { method, headers: {}, cache: "no-store" };
  if (body !== null) {
    opts.headers["Content-Type"] = "application/json";
    opts.body = JSON.stringify(body);
  }
  const res = await fetch(path, opts);
  const data = await res.json();
  if (!res.ok || (data && data.error)) {
    throw new Error((data && data.error) || `HTTP ${res.status}`);
  }
  return data;
}

function esc(s) {
  return String(s == null ? "" : s)
    .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

function md(src) {
  if (!src) return "";
  let text = esc(src);
  const codeBlocks = [];
  text = text.replace(/```([^`]*)```/g, (_, c) => {
    codeBlocks.push(c);
    return `\u0000CODE${codeBlocks.length - 1}\u0000`;
  });
  // inline
  text = text.replace(/`([^`]+)`/g, "<code>$1</code>");
  text = text.replace(/!\[([^\]]*)\]\(([^)]+)\)/g,
    '<img src="$2" alt="$1" />');
  text = text.replace(/\[([^\]]+)\]\(([^)]+)\)/g,
    '<a href="$2" target="_blank" rel="noopener">$1</a>');
  text = text.replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>");

  // GFM tables (header | separator | rows)
  const tables = [];
  text = text.replace(
    /(?:^|\n)[ \t]*(\|[^\n]*\|\n[ \t]*\|[\s:|-]*\|[ \t]*\n(?:[ \t]*\|[^\n]*\|[ \t]*\n?)+)/g,
    (m, block) => {
      const rows = block.trim().split("\n").map((r) => r.trim()).filter(Boolean);
      if (rows.length < 2) return m;
      const cells = (r) =>
        r.replace(/^\s*\|/, "").replace(/\|\s*$/, "").split("|").map((s) => s.trim());
      const head = cells(rows[0]);
      let h = '<table class="md-table"><thead><tr>'
        + head.map((c) => `<th>${c}</th>`).join("") + "</tr></thead><tbody>";
      for (const r of rows.slice(2)) {
        h += "<tr>" + cells(r).map((c) => `<td>${c}</td>`).join("") + "</tr>";
      }
      h += "</tbody></table>";
      tables.push(h);
      return `\u0000TABLE${tables.length - 1}\u0000`;
    });

  const lines = text.split("\n");
  let html = "", list = null, quote = false;
  const closeList = () => { if (list) { html += `</${list}>`; list = null; } };
  const closeQuote = () => { if (quote) { html += "</blockquote>"; quote = false; } };
  for (let raw of lines) {
    let line = raw.trim();
    let m;
    if (line === "") { closeList(); closeQuote(); continue; }
    if (m = line.match(/^######\s+(.*)/)) { closeList(); closeQuote(); html += `<h6>${m[1]}</h6>`; continue; }
    if (m = line.match(/^#####\s+(.*)/)) { closeList(); closeQuote(); html += `<h5>${m[1]}</h5>`; continue; }
    if (m = line.match(/^####\s+(.*)/)) { closeList(); closeQuote(); html += `<h4>${m[1]}</h4>`; continue; }
    if (m = line.match(/^###\s+(.*)/)) { closeList(); closeQuote(); html += `<h3>${m[1]}</h3>`; continue; }
    if (m = line.match(/^##\s+(.*)/)) { closeList(); closeQuote(); html += `<h2>${m[1]}</h2>`; continue; }
    if (m = line.match(/^#\s+(.*)/)) { closeList(); closeQuote(); html += `<h1>${m[1]}</h1>`; continue; }
    if (m = line.match(/^>\s?(.*)/)) { closeList(); if (!quote) { html += "<blockquote>"; quote = true; } html += m[1] + "<br>"; continue; }
    if (m = line.match(/^[-*]\s+(.*)/)) {
      closeQuote();
      if (list !== "ul") { closeList(); html += "<ul>"; list = "ul"; }
      html += `<li>${m[1]}</li>`; continue;
    }
    if (m = line.match(/^\d+[.、]\s+(.*)/)) {
      closeQuote();
      if (list !== "ol") { closeList(); html += "<ol>"; list = "ol"; }
      html += `<li>${m[1]}</li>`; continue;
    }
    if (m = line.match(/^\u0000TABLE(\d+)\u0000$/)) {
      closeList(); closeQuote(); html += tables[+m[1]]; continue;
    }
    closeList(); closeQuote();
    html += `<p>${line}</p>`;
  }
  closeList(); closeQuote();
  html = html.replace(/\u0000CODE(\d+)\u0000/g, (_, i) =>
    `<pre><code>${esc(codeBlocks[+i]).trim()}</code></pre>`);
  return html;
}

function toast(msg, isError = false) {
  const el = document.getElementById("toast");
  el.textContent = msg;
  el.className = "toast show" + (isError ? " error" : "");
  clearTimeout(toast._t);
  toast._t = setTimeout(() => { el.className = "toast"; }, 3600);
}

function showModal(title, bodyHtml) {
  document.getElementById("modal-title").textContent = title;
  document.getElementById("modal-body").innerHTML = bodyHtml;
  document.getElementById("modal").classList.remove("hidden");
}
function closeModal() { document.getElementById("modal").classList.add("hidden"); }
document.getElementById("modal-close").onclick = closeModal;
document.getElementById("modal").onclick = (e) => {
  if (e.target.id === "modal") closeModal();
};

async function submitJob(path, body, label, onDone) {
  toast(label + "，任务已提交…");
  const { job_id } = await api(path, "POST", body);
  pollJob(job_id, label, onDone);
  return job_id;
}

function pollJob(jobId, label, onDone) {
  const timer = setInterval(async () => {
    let job;
    try { job = await api(`/api/jobs/${jobId}`); } catch { return; }
    if (job.status === "running") return;
    clearInterval(timer);
    if (job.status === "done") {
      toast(label + " 完成");
      await refresh();
      if (onDone) onDone(job.result);
    } else {
      toast(job.error || (label + " 失败"), true);
    }
  }, 1200);
}

/* image src: media paths are local; baidu urls are remote. Convert local path
   (returned from the agent as an absolute VM path) into a /media URL. */
function imgSrc(path) {
  if (!path) return "";
  if (path.startsWith("http") || path.startsWith("/media")) return path;
  // Map absolute <data root>/media/<rel> to /media/<rel>
  const idx = path.replace(/\\/g, "/").indexOf("/media/");
  if (idx >= 0) return "http://127.0.0.1:" + location.port + path.slice(idx);
  return path;
}

/* ---------------- navigation ---------------- */

function setView(view) {
  CURRENT_VIEW = view;
  document.getElementById("view-title").textContent = VIEW_TITLES[view];
  document.querySelectorAll(".nav-item").forEach((b) =>
    b.classList.toggle("active", b.dataset.view === view));
  document.body.classList.toggle("view-chat", view === "brain");
  render();
}
document.querySelectorAll(".nav-item").forEach((b) =>
  b.onclick = () => setView(b.dataset.view));

async function refresh() {
  STATE = await api("/api/state");
  updateGlobalStatus();
  render();
}
function updateGlobalStatus() {
  const s = STATE.settings;
  const llm = document.getElementById("llm-status");
  llm.textContent = s.model + (s.api_key_configured ? " (已配置)" : " (缺Key)");
  llm.className = "pill " + (s.api_key_configured ? "pill--ok" : "pill--warn");
  document.getElementById("global-status").textContent =
    `项目 ${STATE.projects.length} · 资料 ${STATE.materials.length} · 选题 ${STATE.topics.length}`;
}

function render() {
  const c = document.getElementById("content");
  const fn = VIEWS[CURRENT_VIEW];
  try {
    c.innerHTML = fn ? fn() : "";
    (AFTER_RENDER[CURRENT_VIEW] || (() => {}))();
  } catch (e) {
    c.innerHTML = '<div class="card" style="color:var(--red)">视图渲染错误：'
      + esc(e.message) + '<pre>' + esc(e.stack) + '</pre></div>';
  }
}

/* ---------------- view: plan ---------------- */

function viewPlan() {
  VIEW_CTX.planPlatforms = VIEW_CTX.planPlatforms || ["wechat", "zhihu", "red", "x"];
  const checks = ["wechat", "zhihu", "red", "x"].map((p) =>
    `<label class="small row" style="gap:6px"><input type="checkbox" class="inline-input"
      data-p="${p}" ${VIEW_CTX.planPlatforms.includes(p) ? "checked" : ""}>
      ${PLATFORM_LABEL[p]}</label>`).join("");
  const plans = STATE.plans.map((p) =>
    `<li><button class="btn btn--ghost btn--sm" data-show-plan="${p.id}">
      ${p.weeks}周计划 · ${p.entries.length} 条 · ${(p.created_at || "").slice(0, 10)}</button></li>`)
    .join("");
  return `
  <div class="grid grid-2">
    <div class="card">
      <div class="card-title">生成内容日历</div>
      <label class="field mt2"><span>规划周数</span>
        <input id="plan-weeks" type="number" value="4" min="1" max="12"/></label>
      <label class="field"><span>运营目标</span>
        <textarea id="plan-goals" placeholder="如：提升行业影响力，稳定输出有产业深度的并购与技术商业化分析"></textarea></label>
      <label class="field"><span>覆盖平台</span><div class="row">${checks}</div></label>
      <label class="field"><span>补充说明</span>
        <input id="plan-notes" placeholder="可选"/></label>
      <button class="btn" id="plan-go">生成日历</button>
    </div>
    <div class="card">
      <div class="card-title">历史计划</div>
      <ul class="list-tight mt2">${plans || '<li class="muted">暂无计划</li>'}</ul>
    </div>
  </div>
  <div class="card" id="plan-result"></div>`;
}
function afterPlan() {
  document.querySelectorAll("[data-p]").forEach((b) => b.onchange = () => {
    const set = new Set(VIEW_CTX.planPlatforms);
    b.checked ? set.add(b.dataset.p) : set.delete(b.dataset.p);
    VIEW_CTX.planPlatforms = [...set];
  });
  document.getElementById("plan-go").onclick = () => {
    const weeks = +document.getElementById("plan-weeks").value || 4;
    const goals = document.getElementById("plan-goals").value;
    const notes = document.getElementById("plan-notes").value;
    submitJob("/api/plan", { weeks, goals, notes, platforms: VIEW_CTX.planPlatforms },
      "生成内容日历", (plan) => renderPlanResult(plan));
  };
  document.querySelectorAll("[data-show-plan]").forEach((b) => b.onclick = () =>
    renderPlanResult(STATE.plans.find((p) => p.id === b.dataset.showPlan)));
  // Auto-render the most recent plan so the calendar is visible without a click.
  if (STATE.plans.length) {
    renderPlanResult(STATE.plans[STATE.plans.length - 1]);
  }
}
function renderPlanResult(plan) {
  const box = document.getElementById("plan-result");
  if (!box) return;
  const entries = (plan.entries || []).sort((a, b) =>
    (a.week - b.week) || String(a.date).localeCompare(String(b.date)));
  const rows = entries.map((e) => `
    <tr>
      <td>第${e.week}周<br><span class="small muted">${esc(e.date || "")}</span></td>
      <td>${PLATFORM_LABEL[e.platform] || e.platform}
        ${e.language === "en" ? '<span class="pill pill--info">EN</span>' : ""}
        <div class="small muted">${esc(e.content_type || "")}</div></td>
      <td><strong>${esc(e.topic || "")}</strong>
        <div class="small muted">${esc(e.angle || "")}</div></td>
      <td class="small">${esc(e.goal || "")}</td>
    </tr>`).join("");
  box.innerHTML = `<div class="card-title">内容日历（${plan.weeks}周 · ${entries.length} 条）</div>
    <table class="calendar"><thead><tr><th>时间</th><th>平台/类型</th><th>选题/角度</th><th>目标</th></tr></thead>
    <tbody>${rows}</tbody></table>`;
}

/* ---------------- view: topics ---------------- */

function viewTopics() {
  return `
  <div class="card">
    <div class="card-title">选题研究</div>
    <div class="row mt2">
      <input id="topic-theme" placeholder="输入主题或近期事件，如：现代摩比斯出售车灯业务" style="flex:1"/>
      <label class="small row" style="gap:6px"><input type="checkbox" id="topic-web" class="inline-input" checked>联网检索</label>
      <select id="topic-count" class="inline-input" style="width:90px">
        ${[3, 4, 5, 6].map((n) => `<option value="${n}" ${n === 5 ? "selected" : ""}>${n}个</option>`).join("")}
      </select>
      <button class="btn" id="topic-go">生成选题卡</button>
    </div>
  </div>
  <div class="grid grid-2" id="topic-list">${STATE.topics.map(topicCard).join("")}</div>`;
}
function topicCard(t) {
  const sources = (t.sources || []).map((s) =>
    `<li><a href="${esc(s.url)}" target="_blank" rel="noopener">${esc(s.title || s.url)}</a></li>`).join("");
  const outline = (t.outline || []).map((o) => `<li>${esc(o)}</li>`).join("");
  const plats = (t.platforms || []).map((p) =>
    `<span class="pill pill--info">${PLATFORM_LABEL[p] || p}</span>`).join("");
  return `
  <div class="item-card">
    <h4>${esc(t.title)}</h4>
    <div class="meta-row">${plats}</div>
    <div class="small"><strong>核心问题：</strong>${esc(t.core_question || "")}</div>
    <div class="small"><strong>角度：</strong>${esc(t.angle || "")}</div>
    <details><summary class="muted small">大纲</summary><ul class="small">${outline}</ul></details>
    <details><summary class="muted small">来源（${(t.sources || []).length}）</summary><ul class="small">${sources || "<li>无</li>"}</ul></details>
    <div class="small muted">工作量：${esc(t.effort || "")} ｜ 增量：${esc((t.information_gain || "").slice(0, 40))}</div>
    <div class="row">
      <button class="btn btn--sm" data-mk-project="${t.id}">用此选题建项目</button>
    </div>
  </div>`;
}
function afterTopics() {
  document.getElementById("topic-go").onclick = () => {
    const theme = document.getElementById("topic-theme").value.trim();
    if (!theme) return toast("请输入主题", true);
    const count = +document.getElementById("topic-count").value;
    const use_web = document.getElementById("topic-web").checked;
    submitJob("/api/research-topics", { theme, count, use_web }, "选题研究");
  };
  document.querySelectorAll("[data-mk-project]").forEach((b) => b.onclick = () => {
    const t = STATE.topics.find((x) => x.id === b.dataset.mkProject);
    showModal("创建项目", `
      <label class="field"><span>主题</span><input id="np-theme" value="${esc(t.title)}"/></label>
      <label class="field"><span>任务说明（核心观点/问题）</span>
        <textarea id="np-brief">${esc(t.core_question || "")}</textarea></label>
      <label class="field"><span>目标读者</span>
        <input id="np-audience" value="整灯厂与上游供应商从业者"/></label>
      <button class="btn" id="np-go">创建项目</button>`);
    document.getElementById("np-go").onclick = async () => {
      const body = {
        theme: document.getElementById("np-theme").value,
        brief: document.getElementById("np-brief").value,
        audience: document.getElementById("np-audience").value,
        topic_id: t.id,
      };
      await api("/api/projects", "POST", body);
      closeModal(); toast("项目已创建");
      await refresh(); goTab("project", "projects");
    };
  });
}

/* ---------------- view: materials ---------------- */

function viewMaterials() {
  const byKind = {};
  STATE.materials.forEach((m) => { (byKind[m.kind] = byKind[m.kind] || []).push(m); });
  const sections = Object.keys(byKind).map((k) => `
    <div class="card">
      <div class="card-title">${kindLabel(k)}（${byKind[k].length}）</div>
      <ul class="list-tight">
        ${byKind[k].map((m) => `
        <li>
          <div class="row">
            <strong>${esc(m.title)}</strong>
            <span class="spacer"></span>
            ${m.url ? `<a href="${esc(m.url)}" target="_blank" rel="noopener" class="small">来源</a>` : ""}
            <button class="btn btn--ghost btn--sm" data-mat-view="${m.id}">查看</button>
            <button class="btn btn--danger btn--sm" data-mat-del="${m.id}">删除</button>
          </div>
          <div class="small muted">${esc(m.source || "")} · ${(m.created_at || "").slice(0, 10)}</div>
        </li>`).join("")}
      </ul>
    </div>`).join("");
  return `
  <div class="card">
    <div class="row">
      <div class="card-title">资料库</div><span class="spacer"></span>
      <button class="btn btn--sm" id="mat-note">粘贴笔记</button>
      <button class="btn btn--ghost btn--sm" id="mat-upload">上传文件</button>
      <button class="btn btn--ghost btn--sm" id="mat-url">导入链接</button>
    </div>
  </div>
  ${sections || '<div class="card muted">暂无资料，先添加或上传。</div>'}`;
}
function kindLabel(k) {
  return ({ note: "笔记", upload: "上传文件", web: "联网资料", company: "公司可公开资料" })[k] || k;
}
function afterMaterials() {
  document.getElementById("mat-note").onclick = () =>
    showModal("粘贴笔记", `
      <label class="field"><span>标题</span><input id="mn-title"/></label>
      <label class="field"><span>内容</span><textarea id="mn-content" style="min-height:200px"></textarea></label>
      <button class="btn" id="mn-go">保存</button>`) ||
    (document.getElementById("mn-go").onclick = async () => {
      await api("/api/materials", "POST", {
        kind: "note", title: document.getElementById("mn-title").value,
        content: document.getElementById("mn-content").value });
      closeModal(); toast("已保存"); refresh();
    });
  document.getElementById("mat-upload").onclick = () =>
    showModal("上传文件", `
      <p class="muted small">支持 .md/.txt/.docx/.html（单文件 ≤ 8MB）</p>
      <input type="file" id="up-file" accept=".md,.txt,.docx,.html"/>
      <div class="mt2"><button class="btn" id="up-go">上传</button></div>`) ||
    (document.getElementById("up-go").onclick = async () => {
      const f = document.getElementById("up-file").files[0];
      if (!f) return toast("请选择文件", true);
      const data = await fileToBase64(f);
      await api("/api/upload", "POST", { filename: f.name, data });
      closeModal(); toast("已上传"); refresh();
    });
  document.getElementById("mat-url").onclick = () =>
    showModal("导入链接", `
      <label class="field"><span>文章链接</span><input id="im-url" placeholder="https://"/></label>
      <button class="btn" id="im-go">导入</button>`) ||
    (document.getElementById("im-go").onclick = async () => {
      const url = document.getElementById("im-url").value.trim();
      if (!url) return toast("请输入链接", true);
      await api("/api/import-url", "POST", { url });
      closeModal(); toast("已导入"); refresh();
    });
  document.querySelectorAll("[data-mat-view]").forEach((b) => b.onclick = () => {
    const m = STATE.materials.find((x) => x.id === b.dataset.matView);
    showModal(m.title, `<div class="preview">${md(m.content)}</div>`);
  });
  document.querySelectorAll("[data-mat-del]").forEach((b) => b.onclick = async () => {
    await api("/api/materials/" + b.dataset.matDel, "DELETE");
    toast("已删除"); refresh();
  });
}
function fileToBase64(f) {
  return new Promise((res, rej) => {
    const r = new FileReader();
    r.onload = () => res(r.result.split(",")[1]);
    r.onerror = rej; r.readAsDataURL(f);
  });
}

/* ---------------- view: projects ---------------- */

function viewProjects() {
  return `
  <div class="card">
    <div class="row">
      <div class="card-title">项目</div><span class="spacer"></span>
      <button class="btn btn--sm" id="proj-new">新建项目</button>
    </div>
  </div>
  <div class="grid grid-2">${STATE.projects.map(projectCard).join("")}</div>`;
}
function projectCard(p) {
  const contents = (p.content_ids || []).map((cid) => {
    const c = STATE.contents.find((x) => x.id === cid);
    if (!c) return "";
    return `<span class="pill ${STATUS_CLASS[c.status] || "pill--muted"}">
      ${PLATFORM_LABEL[c.platform]}${c.language === "en" ? "·EN" : ""} · ${STATUS_LABEL[c.status] || c.status}</span>`;
  }).join(" ");
  return `
  <div class="item-card">
    <h4>${esc(p.theme)}</h4>
    <div class="small muted">${esc((p.brief || "").slice(0, 120))}</div>
    <div class="meta-row">
      <span class="pill pill--info">${p.status}</span>
      <span class="pill pill--muted">事实卡 ${(p.fact_cards || []).length}</span>
      <span class="pill pill--muted">成品 ${(p.content_ids || []).length}</span>
    </div>
    <div class="meta-row">${contents}</div>
    <div class="row mt2">
      <button class="btn btn--ghost btn--sm" data-p-evidence="${p.id}"
        ${(p.fact_cards || []).length ? "disabled" : ""}>① 整理事实卡</button>
      <button class="btn btn--ghost btn--sm" data-p-produce="${p.id}">② 制作四平台</button>
      <button class="btn btn--sm" data-p-auto="${p.id}">一键全流程</button>
      <button class="btn btn--ghost btn--sm" data-p-open="${p.id}">详情</button>
    </div>
  </div>`;
}
function afterProjects() {
  document.getElementById("proj-new").onclick = () =>
    showModal("新建项目", `
      <label class="field"><span>主题</span><input id="np2-theme"/></label>
      <label class="field"><span>任务说明</span><textarea id="np2-brief"></textarea></label>
      <label class="field"><span>读者</span><input id="np2-audience" value="整灯厂与上游供应商从业者"/></label>
      <button class="btn" id="np2-go">创建</button>`) ||
    (document.getElementById("np2-go").onclick = async () => {
      await api("/api/projects", "POST", {
        theme: document.getElementById("np2-theme").value,
        brief: document.getElementById("np2-brief").value,
        audience: document.getElementById("np2-audience").value });
      closeModal(); toast("已创建"); refresh();
    });
  document.querySelectorAll("[data-p-evidence]").forEach((b) => b.onclick = () =>
    submitJob(`/api/projects/${b.dataset.pEvidence}/evidence`, {}, "整理事实卡"));
  document.querySelectorAll("[data-p-produce]").forEach((b) => b.onclick = () =>
    submitJob(`/api/projects/${b.dataset.pProduce}/produce`, {}, "制作四平台"));
  document.querySelectorAll("[data-p-auto]").forEach((b) => b.onclick = () =>
    submitJob(`/api/projects/${b.dataset.pAuto}/full-auto`, {}, "一键全流程（事实→制作→审核→修复）"));
  document.querySelectorAll("[data-p-open]").forEach((b) => b.onclick = () => {
    VIEW_CTX.selectedProject = b.dataset.pOpen;
    goTab("project", "review");
  });
}

/* ---------------- view: review ---------------- */

function viewReview() {
  const options = STATE.projects.map((p) =>
    `<option value="${p.id}" ${VIEW_CTX.selectedProject === p.id ? "selected" : ""}>${esc(p.theme)}</option>`).join("");
  const pid = VIEW_CTX.selectedProject || (STATE.projects[0] || {}).id;
  VIEW_CTX.selectedProject = pid;
  const project = STATE.projects.find((p) => p.id === pid);
  let body = "";
  if (project) {
    const contents = (project.content_ids || []).map((cid) =>
      STATE.contents.find((x) => x.id === cid)).filter(Boolean);
    body = contents.map(contentBlock).join("");
    if (!contents.length)
      body = `<div class="card muted">该项目尚未制作内容，请到“项目与制作”生成。</div>`;
  }
  const schedules = STATE.schedules.filter((s) => s.status === "scheduled").map((s) => `
    <li class="row">
      <span class="pill pill--info">${PLATFORM_LABEL[s.platform]}${s.language === "en" ? "·EN" : ""}</span>
      <span>${esc(s.execute_at)}</span><span class="spacer"></span>
      <button class="btn btn--danger btn--sm" data-sch-cancel="${s.id}">取消</button>
    </li>`).join("");
  return `
  <div class="card">
    <div class="row"><span class="card-title">选择项目</span>
      <select id="rv-project" style="max-width:420px">${options}</select></div>
  </div>
  ${body}
  <div class="card">
    <div class="card-title">排期队列</div>
    <ul class="list-tight mt2">${schedules || '<li class="muted">暂无待执行任务</li>'}</ul>
  </div>`;
}
function contentBlock(c) {
  const audits = c.audits || {};
  const issuesHtml = (label, info) => {
    if (!info || !info.issues.length) return "";
    return `<div class="mt2"><strong>${label}</strong>
      ${info.issues.map((i) => `<div class="issue">
        <div class="pos">${esc(i.position || "")}</div>
        <div class="orig">${esc(i.original || "")}</div>
        <div class="small">${esc(i.reason || "")}</div>
        <div class="sug small">${esc(i.suggestion || "")}</div></div>`).join("")}</div>`;
  };
  const images = (c.images || []).map((im) =>
    `<figure><img src="${esc(imgSrc(im.path))}"/><figcaption>${esc(im.role)} · ${esc(im.caption || "")}</figcaption></figure>`).join("");
  const approved = c.approval && c.approval.approved;
  let mainHtml;
  if (c.platform === "x") {
    mainHtml = `<div class="thread">${(c.posts || []).map((p, i) =>
      `<div class="thread-post"><div class="order">第 ${i + 1} 帖</div><div>${esc(p.text)}</div>
       ${(p.images || []).map((m) => `<img src="${esc(imgSrc(m))}"/>`).join("")}</div>`).join("")}</div>`;
  } else {
    mainHtml = `<div class="preview">${md(c.body)}</div>`;
  }
  return `
  <div class="card">
    <div class="card-head">
      <div class="row">
        <span class="card-title">${PLATFORM_LABEL[c.platform]}${c.language === "en" ? " · EN" : " · 中文"}</span>
        <span class="pill ${STATUS_CLASS[c.status] || "pill--muted"}">${STATUS_LABEL[c.status] || c.status}</span>
        ${approved ? '<span class="pill pill--ok">已审定</span>' : ""}
        ${c.needs_human ? '<span class="pill pill--bad">需人工处理</span>' : ""}
      </div>
    </div>
    <details open><summary class="muted small">内容预览</summary>
      <div class="mt2"><strong>${esc(c.title)}</strong></div>
      ${c.platform === "red" ? "" : mainHtml}
      <div class="gallery mt2">${images}</div>
      ${c.share_text ? `<div class="small mt2"><strong>分享语：</strong>${esc(c.share_text)}</div>` : ""}
      ${c.hashtags && c.hashtags.length ? `<div class="small mt2"><strong>话题：</strong>${esc(c.hashtags.join(" #"))}</div>` : ""}
    </details>

    <div class="row mt2">
      <button class="btn btn--ghost btn--sm" data-c-audit="${c.id}">双审核</button>
      <button class="btn btn--ghost btn--sm" data-c-fix="${c.id}"
        ${audits.fact || audits.language ? "" : "disabled"}>自动修复</button>
      <span class="spacer"></span>
    </div>
    ${issuesHtml("事实审核", audits.fact)}
    ${issuesHtml("语言审核", audits.language)}
    ${(c.unresolved || []).length ? `<div class="mt2 small" style="color:var(--red)"><strong>未解决：</strong>${esc(c.unresolved.join("；"))}</div>` : ""}

    <div class="card mt2" style="box-shadow:none;background:#fafbfc">
      <div class="card-title small">审定与排期</div>
      <div class="row mt2">
        <label class="small" style="margin:0">账号
          <input id="acc-${c.id}" class="inline-input" style="width:140px" placeholder="账号名称"/></label>
        <label class="small" style="margin:0">排期时间（留空立即）
          <input id="time-${c.id}" type="datetime-local" class="inline-input" style="width:210px"/></label>
        <button class="btn btn--sm" data-c-approve="${c.id}"
          ${approved ? "disabled" : ""}>审定${approved ? "✓" : ""}</button>
      </div>
    </div>
  </div>`;
}
function afterReview() {
  const sel = document.getElementById("rv-project");
  if (sel) sel.onchange = () => { VIEW_CTX.selectedProject = sel.value; render(); };
  document.querySelectorAll("[data-c-audit]").forEach((b) => b.onclick = () =>
    submitJob(`/api/contents/${b.dataset.cAudit}/audit`, {}, "双审核"));
  document.querySelectorAll("[data-c-fix]").forEach((b) => b.onclick = () =>
    submitJob(`/api/contents/${b.dataset.cFix}/fix`, {}, "自动修复并复审"));
  document.querySelectorAll("[data-c-approve]").forEach((b) => b.onclick = async () => {
    const id = b.dataset.cApprove;
    const account = document.getElementById("acc-" + id).value;
    const tval = document.getElementById("time-" + id).value;
    let execute_at = null;
    if (tval) execute_at = new Date(tval).toISOString();
    try {
      await api(`/api/contents/${id}/approve`, "POST",
        { account, execute_at });
      toast(execute_at ? "已审定并排期" : "已审定，可发布");
      refresh();
    } catch (e) { toast(e.message, true); }
  });
  document.querySelectorAll("[data-sch-cancel]").forEach((b) => b.onclick = async () => {
    await api(`/api/schedules/${b.dataset.schCancel}/cancel`, "POST");
    toast("已取消"); refresh();
  });
}

/* ---------------- view: publish ---------------- */

function viewPublish() {
  const caps = ["wechat", "x", "zhihu", "red"].map((p) => {
    const cap = STATE.capabilities[p];
    const cls = cap.status === "ready" ? "pill--ok" :
      cap.status === "unverified" ? "pill--warn" : "pill--bad";
    const txt = cap.status === "ready" ? "可接入" :
      cap.status === "unverified" ? "待验证" : "接入受阻";
    return `
    <div class="item-card">
      <div class="row"><h4>${PLATFORM_LABEL[p]}</h4>
        <span class="spacer"></span><span class="pill ${cls}">${txt}</span></div>
      <div class="small">${esc(cap.reason)}</div>
      ${(cap.conditions || []).length ? `<ul class="small">${cap.conditions.map((x) => `<li>${esc(x)}</li>`).join("")}</ul>` : ""}
      <div class="row">
        ${(p === "x" || p === "wechat") ? `<button class="btn btn--ghost btn--sm" data-creds="${p}">配置凭据</button>` : ""}
      </div>
    </div>`;
  }).join("");

  const records = STATE.publishes.map((r) => {
    const c = STATE.contents.find((x) => x.id === r.content_id) || {};
    const link = r.platform_link || (r.platform_result && r.platform_result.link) || "";
    return `
    <div class="item-card">
      <div class="row"><h4>${PLATFORM_LABEL[r.platform]}${r.language === "en" ? "·EN" : ""}</h4>
        <span class="spacer"></span>
        <span class="pill ${STATUS_CLASS[r.status] || "pill--muted"}">${STATUS_LABEL[r.status] || r.status}</span></div>
      <div class="small muted">${esc((c.title || "").slice(0, 70))}</div>
      ${link ? `<a href="${esc(link)}" target="_blank" rel="noopener" class="small">查看已发布内容</a>` : ""}
      ${r.error ? `<div class="small" style="color:var(--red)">${esc(r.error)}</div>` : ""}
      ${r.blocked_reason ? `<div class="small">${esc(r.blocked_reason)}</div>` : ""}
      <div class="row">
        ${(r.status === "processing" || r.status === "unknown") ?
          `<button class="btn btn--ghost btn--sm" data-verify="${r.id}" data-content="${r.content_id}">核查结果</button>` : ""}
        ${r.status === "pending" ? `<button class="btn btn--sm" data-do-publish="${r.content_id}">立即发布</button>` : ""}
      </div>
    </div>`;
  }).join("");

  return `
  <div class="card">
    <div class="card-title">平台接口能力</div>
    <div class="grid grid-2 mt2">${caps}</div>
    <p class="small muted mt2">说明：自动发布是核心交付项。尚未取得可用接口的平台标记为“接入受阻”，人工复制发布仅为备用，不计入自动发布验收。</p>
  </div>
  <div class="card">
    <div class="card-title">发布状态板（六态可核对）</div>
    <div class="status-board mt2">${records || '<span class="muted">暂无发布记录</span>'}</div>
  </div>`;
}
function afterPublish() {
  document.querySelectorAll("[data-creds]").forEach((b) => b.onclick = () => {
    const p = b.dataset.creds;
    if (p === "x") {
      showModal("X OAuth 1.0a 凭据", credForm(p, [
        ["api_key", "API Key"], ["api_secret", "API Secret"],
        ["access_token", "Access Token"], ["access_token_secret", "Access Token Secret"]]));
    } else {
      showModal("公众号凭据", credForm(p, [
        ["appid", "AppID"], ["secret", "AppSecret"]]));
    }
    bindCredSave(p);
  });
  document.querySelectorAll("[data-do-publish]").forEach((b) => b.onclick = () =>
    submitJob(`/api/contents/${b.dataset.doPublish}/publish`, {}, "发布"));
  document.querySelectorAll("[data-verify]").forEach((b) => b.onclick = () =>
    submitJob(`/api/contents/${b.dataset.content}/verify`,
      { record_id: b.dataset.verify }, "核查结果"));
}
function credForm(p, fields) {
  return fields.map(([k, label]) =>
    `<label class="field"><span>${label}</span><input id="cr-${k}" data-k="${k}"/></label>`).join("")
    + `<button class="btn" id="cr-save">保存</button>`;
}
function bindCredSave(p) {
  document.getElementById("cr-save").onclick = async () => {
    const credentials = {};
    document.querySelectorAll("#modal-body [data-k]").forEach((i) => {
      credentials[i.dataset.k] = i.value.trim();
    });
    await api("/api/platform-creds", "PUT", { platform: p, credentials });
    closeModal(); toast("凭据已保存，可重新核验能力"); refresh();
  };
}

/* ---------------- view: feedback ---------------- */

function viewFeedback() {
  const contentOptions = STATE.contents.map((c) =>
    `<option value="${c.id}">${PLATFORM_LABEL[c.platform]}${c.language === "en" ? "·EN" : ""} · ${esc(c.title)}</option>`).join("");
  const reviews = (STATE.feedback || []).filter((f) => f.result).map((r) =>
    `<div class="card">
      <div class="card-title">复盘报告 · ${(r.created_at || "").slice(0, 16)}</div>
      ${(r.result.adjustments || []).map((a) => `<p class="small">• ${esc(a)}</p>`).join("")}
      <details><summary class="small muted">发现与回复草稿</summary>
        ${(r.result.findings || []).map((f) => `<div class="small">${esc(f.finding)} <span class="muted">（${esc(f.evidence)}）</span></div>`).join("")}
        ${(r.result.reply_drafts || []).map((d) => `<div class="issue small">${esc(d.comment)}<div class="sug">${esc(d.draft)}</div></div>`).join("")}
      </details>
    </div>`).join("");
  return `
  <div class="card">
    <div class="card-title">录入真实发布数据</div>
    <p class="small muted">缺失指标请留空，系统不会让模型补数字；不同平台指标不混算。</p>
    <label class="field"><span>选择内容</span><select id="fb-content">${contentOptions}</select></label>
    <div class="grid grid-3">
      <label class="field"><span>阅读/播放</span><input id="fb-reads" type="number"/></label>
      <label class="field"><span>点赞</span><input id="fb-likes" type="number"/></label>
      <label class="field"><span>收藏</span><input id="fb-favs" type="number"/></label>
      <label class="field"><span>评论数</span><input id="fb-comments-n" type="number"/></label>
      <label class="field"><span>转发</span><input id="fb-shares" type="number"/></label>
      <label class="field"><span>统计时间</span><input id="fb-time" type="datetime-local"/></label>
    </div>
    <label class="field"><span>评论原文（每条一行，可选）</span><textarea id="fb-comments" placeholder="评论1&#10;评论2"></textarea></label>
    <div class="row">
      <button class="btn" id="fb-save">保存数据</button>
      <button class="btn btn--navy" id="fb-review">生成复盘</button>
    </div>
  </div>
  ${reviews}`;
}
function afterFeedback() {
  document.getElementById("fb-save").onclick = async () => {
    const content_id = document.getElementById("fb-content").value;
    if (!content_id) return toast("没有可关联的内容", true);
    const num = (id) => {
      const v = document.getElementById(id).value;
      return v === "" ? null : +v;
    };
    const tval = document.getElementById("fb-time").value;
    const metrics = {
      reads: num("fb-reads"), likes: num("fb-likes"),
      favorites: num("fb-favs"), comments_count: num("fb-comments-n"),
      shares: num("fb-shares"),
    };
    const comments = document.getElementById("fb-comments").value.split("\n")
      .map((x) => x.trim()).filter(Boolean).map((text) => ({ text }));
    await api("/api/feedback", "POST", {
      content_id, metrics, comments,
      observed_at: tval ? new Date(tval).toISOString() : null });
    toast("数据已保存"); refresh();
  };
  document.getElementById("fb-review").onclick = () =>
    submitJob("/api/review", {}, "生成复盘");
}

/* ---------------- view: settings ---------------- */

function viewSettings() {
  const s = STATE.settings;
  return `
  <div class="card" style="max-width:640px">
    <div class="card-title">模型设置（默认 DeepSeek）</div>
    <label class="field"><span>API 地址</span><input id="set-base" value="${esc(s.base_url)}"/></label>
    <label class="field"><span>模型名称</span><input id="set-model" value="${esc(s.model)}"/></label>
    <label class="field"><span>API Key${s.api_key_configured ? "（已配置，留空保持不变）" : ""}</span>
      <input id="set-key" type="password" placeholder="${s.api_key_configured ? "已配置" : "sk-..."}"/></label>
    <label class="field"><span>Tavily Key（可选，用于更高质量搜索）</span>
      <input id="set-tavily" type="password" placeholder="${s.tavily_configured ? "已配置" : ""}"/></label>
    <label class="field"><span>品牌名</span><input id="set-brand" value="${esc(s.brand)}"/></label>
    <label class="field"><span>业务定位</span><textarea id="set-profile">${esc(s.profile)}</textarea></label>
    <div class="row">
      <button class="btn" id="set-save">保存</button>
      <button class="btn btn--ghost" id="set-test">测试连接</button>
    </div>
  </div>`;
}
function afterSettings() {
  document.getElementById("set-save").onclick = async () => {
    const body = {
      base_url: document.getElementById("set-base").value,
      model: document.getElementById("set-model").value,
      brand: document.getElementById("set-brand").value,
      profile: document.getElementById("set-profile").value,
    };
    const key = document.getElementById("set-key").value;
    if (key) body.api_key = key;
    const tav = document.getElementById("set-tavily").value;
    if (tav) body.tavily_api_key = tav;
    await api("/api/settings", "PUT", body);
    toast("已保存"); refresh();
  };
  document.getElementById("set-test").onclick = () =>
    submitJob("/api/test-llm", {}, "测试模型连接");
}

/* ---------------- registry ---------------- */

/* ---------------- view: brain (chat) ---------------- */

const CHAT_SUGGESTION = "你好，我是跨平台新媒体助手。我可以帮你做运营规划、选题研究、资料采集、四平台图文、审核排期、接口发布和数据复盘。直接在下方输入你的需求即可。";

function viewBrain() {
  const convs = VIEW_CTX.chatConvs || [];
  const cur = VIEW_CTX.chatConvId;
  const events = VIEW_CTX.chatEvents || [];
  const busy = VIEW_CTX.chatBusy;

  const historyHtml = convs.map((c) => `
    <div class="chat-item ${c.id === cur ? "chat-item--active" : ""}">
      <span class="chat-item-title" data-id="${c.id}">${esc(c.title || "新对话")}</span>
      <button class="chat-item-del" data-id="${c.id}" title="删除">✕</button>
    </div>`).join("");

  const messagesHtml = events.length
    ? events.map(renderChatEvent).join("")
    : `<div class="chat-empty">${CHAT_SUGGESTION}</div>`;

  const liveHtml = busy
    ? `<div id="chat-live" class="chat-row chat-row--assistant">
         <div class="chat-process-wrap">${renderChatProcess(
           VIEW_CTX.chatLiveSteps || [], { collapsed: false, live: true })}</div>
       </div>`
    : "";

  return `
  <div class="chat-layout">
    <div class="chat-history">
      <button class="btn btn-block" id="chat-new">＋ 新建对话</button>
      <div class="chat-list">${historyHtml}</div>
    </div>
    <div class="chat-main">
      <div class="chat-messages" id="chat-messages">
        ${messagesHtml}
        ${liveHtml}
      </div>
      <div class="chat-input">
        <textarea id="chat-text" rows="1" placeholder="输入你的需求，Enter 发送，Shift+Enter 换行"></textarea>
        <button class="btn" id="chat-send" ${busy ? "disabled" : ""}>发送</button>
      </div>
    </div>
  </div>`;
}

function renderChatEvent(ev) {
  if (ev.type === "user") {
    return `<div class="chat-row chat-row--user">
      <div class="chat-bubble chat-user">${esc(ev.text)}</div></div>`;
  }
  const processHtml = (ev.steps && ev.steps.length)
    ? renderChatProcess(ev.steps, { collapsed: true, elapsed: ev.elapsed })
    : "";
  return `<div class="chat-row chat-row--assistant">
    <div class="chat-process-wrap">${processHtml}</div>
    <div class="chat-bubble chat-assistant">${md(ev.text)}</div>
  </div>`;
}

/* Small line icons (stroke = currentColor) */
const CP_ICONS = (() => {
  const svg = (inner) => `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" `
    + `stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round">${inner}</svg>`;
  return {
    search: svg('<circle cx="11" cy="11" r="7"/><path d="M21 21l-4.3-4.3"/>'),
    doc: svg('<path d="M14 3H7a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8z"/><path d="M14 3v5h5"/><path d="M9 13h6M9 17h6"/>'),
    edit: svg('<path d="M12 20h9"/><path d="M16.5 3.5a2.1 2.1 0 0 1 3 3L7 19l-4 1 1-4z"/>'),
    check: svg('<circle cx="12" cy="12" r="9"/><path d="M8.3 12.2l2.5 2.5 5-5.4"/>'),
    list: svg('<rect x="5" y="4" width="14" height="17" rx="2"/><path d="M9 9h6M9 13h6M9 17h4"/>'),
    shield: svg('<path d="M12 3l8 3v6c0 5-3.5 8-8 9-4.5-1-8-4-8-9V6z"/><path d="M9.2 12l2 2 3.8-4"/>'),
    terminal: svg('<rect x="3" y="4" width="18" height="16" rx="2"/><path d="M7 9l3 3-3 3M13 15h4"/>'),
    spark: svg('<path d="M12 3l1.7 4.6L18 9.5l-4.3 1.9L12 16l-1.7-4.6L6 9.5l4.3-1.9z"/>'),
    clock: svg('<circle cx="12" cy="12" r="9"/><path d="M12 7v5l3.2 2"/>'),
  };
})();

function formatElapsed(sec) {
  if (sec == null) return "";
  if (sec < 60) return `${sec} 秒`;
  const m = Math.floor(sec / 60);
  const s = sec % 60;
  return s ? `${m} 分 ${s} 秒` : `${m} 分`;
}

function renderChatProcess(steps, opts = {}) {
  const collapsed = opts.collapsed ? "collapsed" : "";
  const live = opts.live;
  // pair each action with its following result; thoughts stand alone
  const timeline = [];
  for (let i = 0; i < steps.length; i++) {
    const s = steps[i];
    if (s.kind === "thought") {
      timeline.push({ kind: "thought", text: s.text });
    } else if (s.kind === "action") {
      const nxt = steps[i + 1];
      const result = (nxt && nxt.kind === "result") ? nxt.text : "";
      if (result) i++;
      timeline.push({ kind: "tool", action: s.text, result, icon: s.icon || "terminal" });
    }
  }
  const nTools = timeline.filter((t) => t.kind === "tool").length;
  const body = timeline.map((t) => {
    if (t.kind === "thought") {
      return `<div class="cp-thought">${esc(t.text)}</div>`;
    }
    const detail = t.result
      ? `<div class="cp-tool-detail">${esc(t.result)}</div>` : "";
    return `<div class="cp-tool-row">
      <button type="button" class="cp-tool-head">
        <span class="cp-icon">${CP_ICONS[t.icon] || CP_ICONS.terminal}</span>
        <span class="cp-tool-text">${esc(t.action)}</span>
        ${t.result ? '<span class="cp-tool-chev"></span>' : ""}
      </button>
      ${detail}
    </div>`;
  }).join("");

  const tail = live
    ? '<div class="cp-live"><span class="cp-dot"></span><span>正在处理…</span></div>'
    : `<div class="cp-done"><span class="cp-icon">${CP_ICONS.check}</span><span>已完成</span></div>`;

  const headSummary = [
    opts.elapsed
      ? `<span class="cp-head-time">${CP_ICONS.clock}用时 ${formatElapsed(opts.elapsed)}</span>`
      : "",
    nTools ? `${nTools} 步操作` : "",
  ].filter(Boolean).join(" · ");

  return `<div class="chat-process ${collapsed}">
    <button type="button" class="cp-head">
      <span class="cp-icon cp-icon--head">${CP_ICONS.spark}</span>
      <span class="cp-head-title">思考过程</span>
      <span class="cp-head-summary">${headSummary}</span>
      <span class="cp-head-chev"></span>
    </button>
    <div class="cp-body">
      ${body}
      ${tail}
    </div>
  </div>`;
}

function afterBrain() {
  (async () => {
    try {
      const had = !!VIEW_CTX.chatConvId;
      await ensureChat();
      if (!had) rerenderChat();
    } catch (e) { toast(e.message, true); }
  })();

  // Collapse / expand the reasoning block and each tool row (event delegation).
  // Bound once on document so re-renders never lose the handler.
  if (!window.__cpDelegated) {
    window.__cpDelegated = true;
    document.addEventListener("click", (e) => {
      const head = e.target.closest(".cp-head");
      if (head) {
        head.closest(".chat-process").classList.toggle("collapsed");
        return;
      }
      const toolHead = e.target.closest(".cp-tool-head");
      if (toolHead) {
        toolHead.closest(".cp-tool-row").classList.toggle("expanded");
      }
    });
  }

  document.getElementById("chat-new").onclick = async () => {
    if (VIEW_CTX.chatBusy) { toast("请等当前回复结束", true); return; }
    const r = await api("/api/chat/new", "POST", {});
    VIEW_CTX.chatConvId = r.conversation_id;
    VIEW_CTX.chatEvents = [];
    await loadChatConvs();
    rerenderChat();
  };

  document.querySelector(".chat-history").onclick = async (e) => {
    const t = e.target.closest("[data-id]");
    if (!t) return;
    const id = t.dataset.id;
    if (e.target.classList.contains("chat-item-del")) {
      e.stopPropagation();
      if (VIEW_CTX.chatBusy) { toast("请等当前回复结束", true); return; }
      if (!confirm("确定删除这个对话？")) return;
      await api(`/api/chat/conversations/${id}`, "DELETE");
      if (VIEW_CTX.chatConvId === id) {
        VIEW_CTX.chatConvId = null;
        VIEW_CTX.chatEvents = [];
      }
      await loadChatConvs();
      await ensureChat();
      rerenderChat();
      return;
    }
    if (VIEW_CTX.chatBusy) { toast("请等当前回复结束", true); return; }
    await openConversation(id);
  };

  const textarea = document.getElementById("chat-text");
  const send = document.getElementById("chat-send");
  textarea.addEventListener("input", () => autoGrow(textarea));
  textarea.addEventListener("keydown", (e) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      doSend();
    }
  });
  send.onclick = doSend;

  async function doSend() {
    const text = textarea.value.trim();
    if (!text || VIEW_CTX.chatBusy) return;
    await ensureChat();
    const id = VIEW_CTX.chatConvId;
    textarea.value = "";
    autoGrow(textarea);
    VIEW_CTX.chatEvents.push({ type: "user", text });
    VIEW_CTX.chatBusy = true;
    VIEW_CTX.chatLiveSteps = [];
    rerenderChat();
    let jobId;
    try {
      const job = await api(`/api/chat/conversations/${id}/send`, "POST",
                            { message: text });
      jobId = job.job_id;
    } catch (e) {
      VIEW_CTX.chatBusy = false;
      toast(e.message, true);
      rerenderChat();
      return;
    }
    pollChatJob(jobId);
  }
}

async function ensureChat() {
  if (!VIEW_CTX.chatConvs) await loadChatConvs();
  if (!VIEW_CTX.chatConvId) {
    const convs = VIEW_CTX.chatConvs || [];
    if (convs.length) {
      await openConversation(convs[0].id);
    } else {
      const r = await api("/api/chat/new", "POST", {});
      VIEW_CTX.chatConvId = r.conversation_id;
      VIEW_CTX.chatEvents = [];
      await loadChatConvs();
    }
  }
}

async function loadChatConvs() {
  const r = await api("/api/chat/conversations");
  VIEW_CTX.chatConvs = r.conversations || [];
}

async function openConversation(id) {
  const r = await api(`/api/chat/conversations/${id}`);
  VIEW_CTX.chatConvId = id;
  VIEW_CTX.chatEvents = r.events || [];
  rerenderChat();
}

function pollChatJob(jobId) {
  let lastLen = 0;
  const timer = setInterval(async () => {
    let job;
    try { job = await api(`/api/jobs/${jobId}`); } catch { return; }
    const steps = (job.progress && job.progress.steps) || [];
    if (steps.length > lastLen) {
      lastLen = steps.length;
      VIEW_CTX.chatLiveSteps = steps;
      renderChatLive();
    }
    if (job.status !== "running") {
      clearInterval(timer);
      VIEW_CTX.chatBusy = false;
      VIEW_CTX.chatLiveSteps = [];
      if (job.status === "done") {
        const id = VIEW_CTX.chatConvId;
        await loadChatConvs();
        const r = await api(`/api/chat/conversations/${id}`);
        VIEW_CTX.chatEvents = r.events || [];
        toast("完成");
      } else {
        toast(job.error || "处理失败", true);
      }
      rerenderChat();
    }
  }, 900);
}

function renderChatLive() {
  let live = document.getElementById("chat-live");
  if (!live) {
    const msgs = document.getElementById("chat-messages");
    if (!msgs) return;
    live = document.createElement("div");
    live.id = "chat-live";
    live.className = "chat-row chat-row--assistant";
    msgs.appendChild(live);
  }
  live.innerHTML = `<div class="chat-process-wrap">${renderChatProcess(
    VIEW_CTX.chatLiveSteps, { collapsed: false, live: true })}</div>`;
  scrollChatBottom();
}

function rerenderChat() {
  if (CURRENT_VIEW !== "brain") return;
  setView("brain");
  scrollChatBottom();
}

function scrollChatBottom() {
  const msgs = document.getElementById("chat-messages");
  if (msgs) msgs.scrollTop = msgs.scrollHeight;
}

function autoGrow(el) {
  el.style.height = "auto";
  el.style.height = Math.min(el.scrollHeight, 160) + "px";
}

/* ---------------- hubs (grouped sub-views under one nav item) ---------------- */

const HUBS = {
  research: {
    default: "materials",
    tabs: [
      { id: "materials", label: "素材资料", render: viewMaterials, after: afterMaterials },
      { id: "topics", label: "选题研究", render: viewTopics, after: afterTopics },
      { id: "plan", label: "内容日历", render: viewPlan, after: afterPlan },
    ],
  },
  project: {
    default: "projects",
    tabs: [
      { id: "projects", label: "项目与制作", render: viewProjects, after: afterProjects },
      { id: "review", label: "审核与排期", render: viewReview, after: afterReview },
    ],
  },
  publish: {
    default: "publish",
    tabs: [
      { id: "publish", label: "接口发布", render: viewPublish, after: afterPublish },
      { id: "feedback", label: "数据复盘", render: viewFeedback, after: afterFeedback },
    ],
  },
};

function activeSubTab(hubKey) {
  VIEW_CTX.subTab = VIEW_CTX.subTab || {};
  const saved = VIEW_CTX.subTab[hubKey];
  if (saved && HUBS[hubKey].tabs.some((t) => t.id === saved)) return saved;
  return HUBS[hubKey].default;
}

function renderHub(hubKey) {
  const active = activeSubTab(hubKey);
  const tabs = HUBS[hubKey].tabs.map((t) =>
    `<button class="subtab ${t.id === active ? "subtab--active" : ""}"`
    + ` data-hub="${hubKey}" data-tab="${t.id}">${t.label}</button>`).join("");
  const tab = HUBS[hubKey].tabs.find((t) => t.id === active);
  return `<div class="subtabs">${tabs}</div>${tab.render()}`;
}

function afterHub(hubKey) {
  const active = activeSubTab(hubKey);
  document.querySelectorAll(`.subtab[data-hub="${hubKey}"]`).forEach((b) => {
    b.onclick = () => {
      VIEW_CTX.subTab[hubKey] = b.dataset.tab;
      setView(hubKey);
    };
  });
  HUBS[hubKey].tabs.find((t) => t.id === active).after();
}

function goTab(hubKey, subId) {
  VIEW_CTX.subTab = VIEW_CTX.subTab || {};
  VIEW_CTX.subTab[hubKey] = subId;
  setView(hubKey);
}

const VIEWS = {
  brain: viewBrain,
  research: () => renderHub("research"),
  project: () => renderHub("project"),
  publish: () => renderHub("publish"),
  settings: viewSettings,
};
const AFTER_RENDER = {
  brain: afterBrain,
  research: () => afterHub("research"),
  project: () => afterHub("project"),
  publish: () => afterHub("publish"),
  settings: afterSettings,
};

/* ---------------- boot ---------------- */
(async function boot() {
  try {
    STATE = await api("/api/state");
    updateGlobalStatus();
    setView("brain");
  } catch (e) {
    document.getElementById("content").innerHTML =
      `<div class="card">无法加载：${esc(e.message)}</div>`;
  }
})();
