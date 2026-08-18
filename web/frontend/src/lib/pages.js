import { uiState } from "./ui-state.js";
import { getRouter } from "./router-bridge.js";

const titles = {
  overview: ["总览", "掌握度分布、点阵与薄弱点"],
  practice: ["练习", "基础练习，最高升到 L2"],
  basics: ["基础", "原文表格（周期表 / 九下诗文）"],
  history: ["做题记录", "永久保存的答题尝试与会话"],
  knowledge: ["知识点", "点阵、教程与晋级说明"],
  learn: ["学习", "教程讲义 · 关联修订"],
  mastery: ["掌握度", "筛选并编辑 L0–L4"],
  assessments: ["考核", "更高一档练习，提升熟练度（冲 L3/L4）"],
  plan: ["学习计划", "周计划 · 日计划 · 进度与负荷问卷"],
  profile: ["档案", "学生信息与佛山 2027 考策"],
  calc: ["计算专题", "同一题型换数据反复练，提高计算准确率"],
};

const subjectNames = {
  math: "数学",
  chinese: "语文",
  english: "英语",
  physics: "物理",
  chemistry: "化学",
  morality: "道法",
  history: "历史",
  pe: "体育",
};

let editingKid = null;
let cache = {
  overview: null,
  subjects: [],
  preferredKnowledgeSubject: null,
  pendingTutorial: null,
  openFollowupId: null,
  pendingPracticePaperId: null,
  bootError: null,
};

const navItems = [
  { name: "overview", path: "/", label: "总览" },
  { name: "practice", path: "/practice", label: "练习" },
  { name: "calc", path: "/calc", label: "计算" },
  { name: "basics", path: "/basics", label: "基础" },
  { name: "learn", path: "/learn", label: "学习" },
  { name: "history", path: "/history", label: "做题记录" },
  { name: "knowledge", path: "/knowledge", label: "知识点" },
  { name: "mastery", path: "/mastery", label: "掌握度" },
  { name: "assessments", path: "/assessments", label: "考核" },
  { name: "plan", path: "/plan", label: "学习计划" },
  { name: "profile", path: "/profile", label: "档案" },
];

const viewLoaders = {
  overview: () => renderOverview(),
  practice: () => renderPractice(),
  calc: () => renderCalc(),
  basics: () => renderBasics(),
  history: () => renderHistory(),
  knowledge: () => renderKnowledge(),
  learn: () => renderLearn(),
  mastery: () => renderMastery(),
  assessments: () => renderAssessments(),
  plan: () => renderPlan(),
  profile: () => renderProfile(),
};

function queryStr(key) {
  const raw = getRouter()?.currentRoute?.value?.query?.[key];
  if (Array.isArray(raw)) return raw[0] || "";
  return typeof raw === "string" ? raw : "";
}

function setSyncStatus(text) {
  uiState.syncStatus = text || "";
}

function switchView(name, query) {
  const router = getRouter();
  const dest = { name };
  if (query && Object.keys(query).length) dest.query = query;
  if (!router) {
    const fn = viewLoaders[name];
    return Promise.resolve(fn && fn());
  }
  const cur = router.currentRoute.value;
  if (cur.name === name) {
    if (query) {
      return router
        .replace(dest)
        .then(() => viewLoaders[name]?.())
        .catch(() => viewLoaders[name]?.());
    }
    return Promise.resolve(viewLoaders[name]?.());
  }
  return router.push(dest).catch(() => {});
}

async function api(path, options) {
  const res = await fetch(path, {
    headers: { "Content-Type": "application/json" },
    ...options,
  });
  if (!res.ok) {
    const text = await res.text();
    throw new Error(apiErrorMessage(text) || res.statusText);
  }
  if (res.status === 204) return null;
  return res.json();
}

function apiErrorMessage(raw) {
  const text = String(raw || "").trim();
  if (!text) return "";
  try {
    const obj = JSON.parse(text);
    if (typeof obj?.detail === "string") return obj.detail;
    if (Array.isArray(obj?.detail)) {
      return obj.detail.map((x) => x.msg || JSON.stringify(x)).join("；");
    }
  } catch (_) {
    /* 非 JSON 则原样返回 */
  }
  return text;
}

function $(id) {
  return document.getElementById(id);
}

function levelBar(bucket, opts = {}) {
  const parts = ["L0", "L1", "L2", "L3", "L4"]
    .map((lv) => {
      const n = Number(bucket[lv]) || 0;
      if (n <= 0) return "";
      return `<span class="${lv.toLowerCase()}" style="flex:${n} 0 0" title="${lv} ${n}"></span>`;
    })
    .join("");
  const showLegend = opts.showLegend !== false;
  return `
    <div class="bars" role="img" aria-label="掌握度分布 L0-L4" title="掌握度分布 L0-L4">
      ${parts || `<span class="l0" style="flex:1 0 0"></span>`}
    </div>
    ${
      showLegend
        ? `<p class="muted bars-legend">
      L0 ${bucket.L0 || 0} · L1 ${bucket.L1 || 0} · L2 ${bucket.L2 || 0} · L3 ${bucket.L3 || 0} · L4 ${bucket.L4 || 0}
    </p>`
        : ""
    }`;
}

function masteryStatsHtml(stats, opts = {}) {
  const s = stats || { L0: 0, L1: 0, L2: 0, L3: 0, L4: 0, total: 0, proficient: 0, proficient_pct: 0 };
  const compact = !!opts.compact;
  return `
    <div class="mastery-stats ${compact ? "compact" : ""}">
      <div class="mastery-stats-row">
        <div>
          <p class="stat-label">叶知识点</p>
          <p class="stat-value" style="font-size:${compact ? "1.35rem" : "1.8rem"}">${s.total || 0}</p>
        </div>
        <div>
          <p class="stat-label">熟练 L3+</p>
          <p class="stat-value" style="font-size:${compact ? "1.35rem" : "1.8rem"}">
            ${s.proficient || 0}
            <span class="muted" style="font-size:0.95rem">（${s.proficient_pct || 0}%）</span>
          </p>
        </div>
      </div>
      ${levelBar(s)}
      <div class="lattice-legend">
        <span><i class="dot L0"></i>L0 未学</span>
        <span><i class="dot L1"></i>L1 了解</span>
        <span><i class="dot L2"></i>L2 理解</span>
        <span><i class="dot L3"></i>L3 掌握</span>
        <span><i class="dot L4"></i>L4 熟练</span>
      </div>
    </div>`;
}

function renderLatticeBoard(lattice, opts = {}) {
  const mini = !!opts.mini;
  const modules = lattice.modules || [];
  if (!modules.length) {
    return `<p class="muted">暂无知识点。请先同步仓库。</p>`;
  }
  return `
    <div class="lattice-board ${mini ? "mini" : ""}">
      ${modules
        .map((mod) => {
          const nodes = mod.nodes || [];
          const m = mod.stats || {};
          return `<div class="lattice-module">
            <div class="lattice-module-head">
              <strong>${escapeHtml(mod.name)}</strong>
              ${
                mini
                  ? ""
                  : `<span class="muted">${nodes.length} 点 · L3+ ${m.proficient || 0}</span>`
              }
            </div>
            <div class="lattice-dots" role="list">
              ${
                nodes.length
                  ? nodes
                      .map((n) => {
                        const lv = n.level || "L0";
                        const title = `${n.name} · ${lv} · 深度 ${n.topo_depth ?? 0}`;
                        return `<button type="button" class="lattice-dot ${lv}"
                          data-open-tutorial="${escapeHtml(n.id)}"
                          title="${escapeHtml(title)}"
                          aria-label="${escapeHtml(title)}"
                          role="listitem"></button>`;
                      })
                      .join("")
                  : `<span class="muted" style="font-size:0.75rem">—</span>`
              }
            </div>
          </div>`;
        })
        .join("")}
    </div>`;
}

function bindLatticeOpeners(scope, mountId) {
  scope.querySelectorAll("[data-open-tutorial]").forEach((btn) => {
    btn.addEventListener("click", () => {
      openTutorial(btn.dataset.openTutorial, mountId ? { mountId } : {});
    });
  });
}

async function renderOverview() {
  const data = await api("/api/overview");
  cache.overview = data;
  cache.subjects = data.subjects;
  const phase1 = data.subjects.filter((s) => s.phase === 1);
  const learned = Object.values(data.levels).reduce(
    (acc, b) => acc + (b.L2 || 0) + (b.L3 || 0) + (b.L4 || 0),
    0
  );
  const totalMastery = Object.values(data.levels).reduce((acc, b) => acc + (b.total || 0), 0);

  const lattices = await Promise.all(
    phase1.map((s) =>
      api(`/api/knowledge/lattice?subject=${encodeURIComponent(s.id)}`).catch(() => null)
    )
  );

  const ms = data.mastery_score || {};
  $("view-overview").innerHTML = `
    <div class="grid">
      <div class="card span-12 score-hero">
        <p class="stat-label">掌握度赋分（L0=0 · L1=1 · L2=2 · L3=3 · L4=4；总分=全部熟练）</p>
        <p class="stat-value score-hero-value">
          ${ms.earned ?? 0}<span class="muted"> / ${ms.total ?? 0}</span>
        </p>
        <p class="muted" style="margin:6px 0 0">
          已得分 / 总分 · 完成度 ${ms.percent ?? 0}% · 计入 ${ms.point_count ?? 0} 个知识点
        </p>
        <div class="score-hero-bar"><span style="width:${Math.min(100, ms.percent ?? 0)}%"></span></div>
      </div>
      <div class="card span-3">
        <p class="stat-label">知识点</p>
        <p class="stat-value">${data.counts.knowledge}</p>
      </div>
      <div class="card span-3">
        <p class="stat-label">结构化题目</p>
        <p class="stat-value">${data.counts.questions ?? 0}</p>
      </div>
      <div class="card span-3">
        <p class="stat-label">知识点教程</p>
        <p class="stat-value">${data.counts.tutorials ?? 0}</p>
      </div>
      <div class="card span-3">
        <p class="stat-label">已达 L2+</p>
        <p class="stat-value">${learned}<span class="muted" style="font-size:1rem"> / ${totalMastery}</span></p>
      </div>
      ${phase1
        .map((s, i) => {
          const b = data.levels[s.id] || { L0: 0, L1: 0, L2: 0, L3: 0, L4: 0, total: 0 };
          const lat = lattices[i];
          const leafStats = lat?.stats;
          return `<div class="card span-6 overview-lattice-card" data-goto-knowledge="${s.id}">
            <div class="overview-lattice-head">
              <div>
                <p class="stat-label">${s.name_zh} · 计入 ${s.admit_score ?? "—"} 分</p>
                <p class="stat-value" style="font-size:1.25rem">${
                  leafStats ? leafStats.total : b.total
                } 个叶考点 · L3+ ${leafStats ? leafStats.proficient_pct : 0}%</p>
              </div>
              <button type="button" class="chip" data-goto-knowledge="${s.id}">打开点阵</button>
            </div>
            ${leafStats ? levelBar(leafStats) : levelBar(b)}
            ${lat ? renderLatticeBoard(lat, { mini: true }) : `<p class="muted">点阵加载失败</p>`}
          </div>`;
        })
        .join("")}
      <div class="card span-12">
        <h3 style="margin:0 0 12px;font-family:var(--font-display)">优先关注</h3>
        ${
          data.weak_points.length
            ? `<table><thead><tr><th>知识点</th><th>科目</th><th>等级</th><th>考频</th><th>错次</th></tr></thead><tbody>
            ${data.weak_points
              .map(
                (w) => `<tr>
                <td><div>${w.name}</div><div class="mono muted">${w.knowledge_id}</div></td>
                <td>${subjectNames[w.subject_id] || w.subject_id}</td>
                <td><span class="level-pill">${w.level}</span></td>
                <td class="weight-${w.exam_weight}">${w.exam_weight}</td>
                <td>${w.wrong_count}</td>
              </tr>`
              )
              .join("")}
          </tbody></table>`
            : `<p class="muted">暂无薄弱标记。先完成摸底并批改后会在这里出现。</p>`
        }
      </div>
    </div>`;
  setSyncStatus(data.last_sync ? `已同步 ${data.last_sync}` : "");

  $("view-overview").querySelectorAll("[data-goto-knowledge]").forEach((el) => {
    el.addEventListener("click", (e) => {
      e.stopPropagation();
      const sid = el.dataset.gotoKnowledge;
      cache.preferredKnowledgeSubject = sid;
      switchView("knowledge", { subject: sid });
    });
  });
  $("view-overview").querySelectorAll(".lattice-dot").forEach((btn) => {
    btn.addEventListener("click", (e) => {
      e.stopPropagation();
      const sid =
        btn.closest("[data-goto-knowledge]")?.dataset.gotoKnowledge ||
        cache.preferredKnowledgeSubject;
      if (sid) cache.preferredKnowledgeSubject = sid;
      const kid = btn.dataset.openTutorial;
      cache.pendingTutorial = kid;
      const query = {};
      if (sid) query.subject = sid;
      if (kid) query.kid = kid;
      switchView("knowledge", query);
    });
  });
}

function subjectOptions(selected) {
  const list = cache.subjects.length
    ? cache.subjects.filter((s) => s.phase === 1)
    : Object.keys(subjectNames).map((id) => ({ id, name_zh: subjectNames[id], phase: 1 }));
  return list
    .map(
      (s) =>
        `<option value="${s.id}" ${s.id === selected ? "selected" : ""}>${s.name_zh}</option>`
    )
    .join("");
}

function renderTreeNodes(nodes) {
  if (!nodes || !nodes.length) return "";
  return `<ul>${nodes
    .map((n) => {
      const isLeaf = !n.children || !n.children.length;
      const cls = isLeaf ? `leaf ${n.exam_weight || ""}` : "branch";
      return `<li class="${cls}">
        <div class="node">
          <button type="button" class="linkish" data-open-tutorial="${n.id}">
            <strong>${n.name}</strong>
          </button>
          <span class="mono muted">${n.id}</span>
          <span class="level-pill">${n.level || "—"}</span>
          <span class="weight-${n.exam_weight}">${n.exam_weight}</span>
          ${
            n.has_tutorial
              ? `<span class="chip ok-chip">有教程</span>`
              : isLeaf
                ? `<span class="chip">待生成</span>`
                : ""
          }
          ${n.wrong_count ? `<span class="muted">错 ${n.wrong_count}</span>` : ""}
        </div>
        ${renderTreeNodes(n.children)}
      </li>`;
    })
    .join("")}</ul>`;
}

async function openTutorial(knowledgeId, options = {}) {
  const force = !!options.force;
  if (!options.mountId) {
    await switchView("learn", knowledgeId ? { kid: knowledgeId } : undefined);
  }
  const target = options.mountId ? $(options.mountId) : $("view-learn");
  if (!target) return;
  target.innerHTML = `<div class="summary-box"><p class="muted">正在加载教程…</p></div>`;
  let data;
  try {
    data = await api(`/api/tutorials/${encodeURIComponent(knowledgeId)}/ensure`, {
      method: "POST",
      body: JSON.stringify({ force, target_level: options.target_level || "L1" }),
    });
  } catch (err) {
    target.innerHTML = `<div class="feedback-panel bad">${escapeHtml(err.message || "加载失败")}</div>`;
    return;
  }
  renderTutorialPanel(target, data, { ...options, knowledgeId });
}

function renderTutorialPanel(target, data, options = {}) {
  const knowledgeId = options.knowledgeId || data.knowledge_id;
  const body = (data.body_md || data.content_md || "").replace(/^---[\s\S]*?---\s*/, "");
  const related = data.related_ids || [];
  target.innerHTML = `
    <div class="tutorial-panel">
      <div class="tutorial-toolbar">
        <div>
          <h2 style="margin:0;font-family:var(--font-display)">${escapeHtml(data.title || data.name || knowledgeId)}</h2>
          <p class="muted mono" style="margin:6px 0 0">${escapeHtml(data.knowledge_id)} · ${
            subjectNames[data.subject_id] || data.subject_id || ""
          } · ${data.target_level || "L1"} · v${data.version || 1} · ${data.source || "file"}${
            data.generated ? " · 刚生成" : ""
          }${data.revised ? " · 已修订" : ""}</p>
          ${
            data.revision_note
              ? `<p class="muted" style="margin:6px 0 0">修订：${escapeHtml(data.revision_note)}</p>`
              : ""
          }
          ${
            related.length
              ? `<p style="margin:8px 0 0">${related
                  .map(
                    (r) =>
                      `<button type="button" class="chip mono" data-open-tutorial="${r}">${r}</button>`
                  )
                  .join(" ")}</p>`
              : ""
          }
        </div>
        <div class="practice-actions" style="margin:0">
          <button type="button" class="btn-ghost" id="tu-integrate">整合关联</button>
          <button type="button" class="btn-ghost" id="tu-patch">补充修订</button>
          <button type="button" class="btn-ghost" id="tu-regen">重新生成</button>
          <button type="button" class="btn-ghost" id="tu-to-kn">返回知识点</button>
        </div>
      </div>
      <div class="markdown-box tutorial-body" id="tu-body">${formatTutorialHtml(body)}</div>
    </div>`;
  renderKatex(target);
  target.querySelectorAll("[data-open-tutorial]").forEach((btn) => {
    btn.addEventListener("click", () =>
      openTutorial(btn.dataset.openTutorial, { mountId: options.mountId })
    );
  });
  const regen = $("tu-regen");
  if (regen) {
    regen.onclick = () => openTutorial(knowledgeId, { ...options, force: true });
  }
  const integrate = $("tu-integrate");
  if (integrate) {
    integrate.onclick = async () => {
      integrate.disabled = true;
      integrate.textContent = "整合中…";
      try {
        const revised = await api(`/api/tutorials/${encodeURIComponent(knowledgeId)}/revise`, {
          method: "POST",
          body: JSON.stringify({
            mode: "integrate",
            notes: "整合先修与同模块已有教程",
            related_ids: related,
          }),
        });
        renderTutorialPanel(target, revised, options);
      } catch (err) {
        alert(err.message || "整合失败");
        integrate.disabled = false;
        integrate.textContent = "整合关联";
      }
    };
  }
  const patch = $("tu-patch");
  if (patch) {
    patch.onclick = async () => {
      const notes = window.prompt("要补充或纠正什么？（写入教程）", "");
      if (notes == null) return;
      const mode = window.confirm("点「确定」=纠正错误；点「取消」=仅补充")
        ? "correct"
        : "patch";
      patch.disabled = true;
      try {
        const revised = await api(`/api/tutorials/${encodeURIComponent(knowledgeId)}/revise`, {
          method: "POST",
          body: JSON.stringify({ mode, notes }),
        });
        renderTutorialPanel(target, revised, options);
      } catch (err) {
        alert(err.message || "修订失败");
        patch.disabled = false;
      }
    };
  }
  const back = $("tu-to-kn");
  if (back) {
    back.onclick = () => {
      const q = {};
      if (cache.preferredKnowledgeSubject) q.subject = cache.preferredKnowledgeSubject;
      switchView("knowledge", q);
    };
  }
}

function formatMdTables(html) {
  // 将连续 Markdown 表格行转为对齐 HTML 表
  const lines = String(html || "").split(/<br>/);
  const out = [];
  let i = 0;
  const isTableLine = (line) => {
    const t = line.trim();
    return t.includes("|") && /^\|?.+\|/.test(t) && !/^<\/?(h\d|ul|li|div)/i.test(t);
  };
  const parseRow = (line) =>
    line
      .trim()
      .replace(/^\|/, "")
      .replace(/\|$/, "")
      .split("|")
      .map((c) => c.trim());
  const isSep = (cells) => cells.length > 0 && cells.every((c) => /^:?-{3,}:?$/.test(c));

  while (i < lines.length) {
    if (!isTableLine(lines[i])) {
      out.push(lines[i]);
      i += 1;
      continue;
    }
    const block = [];
    while (i < lines.length && isTableLine(lines[i])) {
      block.push(lines[i]);
      i += 1;
    }
    if (block.length < 2) {
      out.push(...block);
      continue;
    }
    const head = parseRow(block[0]);
    let start = 1;
    if (block[1] && isSep(parseRow(block[1]))) start = 2;
    const body = block.slice(start).map(parseRow).filter((c) => c.length && !isSep(c));
    if (!body.length) {
      out.push(...block);
      continue;
    }
    const th = head.map((c) => `<th>${c}</th>`).join("");
    const trs = body
      .map((cells) => {
        while (cells.length < head.length) cells.push("");
        return `<tr>${cells
          .slice(0, head.length)
          .map((c) => `<td>${c}</td>`)
          .join("")}</tr>`;
      })
      .join("");
    out.push(
      `<div class="basics-table-wrap"><table class="basics-table"><thead><tr>${th}</tr></thead><tbody>${trs}</tbody></table></div>`
    );
  }
  return out.join("<br>");
}

function formatTutorialHtml(md) {
  let text = String(md || "");
  const parts = text.split(/\n##\s*自测参考\s*\n/);
  const main = parts[0];
  const answers = parts[1];

  function renderChunk(chunk, { tables = false } = {}) {
    let t = escapeHtml(chunk);
    t = t.replace(/^###\s+(.+)$/gm, "<h4>$1</h4>");
    t = t.replace(/^##\s+(.+)$/gm, "<h3>$1</h3>");
    t = t.replace(/^#\s+(.+)$/gm, "<h2>$1</h2>");
    t = t.replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>");
    t = t.replace(/`([^`]+)`/g, '<code class="mono">$1</code>');
    t = t.replace(/^\s*-\s+(.+)$/gm, "<li>$1</li>");
    t = t.replace(/(?:<li>.*?<\/li>\n?)+/gs, (block) => `<ul>${block}</ul>`);
    t = t.replace(/\n{2,}/g, "<br><br>").replace(/\n/g, "<br>");
    if (tables) t = formatMdTables(t);
    return t;
  }

  let html = renderChunk(main);
  if (answers != null) {
    html += `<details class="tutorial-answers"><summary>自测参考（先自己做再展开）</summary>${renderChunk(
      answers
    )}</details>`;
  }
  return html;
}

function formatBasicsHtml(md) {
  // 基础库：只渲染原文与对齐表格，不拆自测区
  let t = escapeHtml(String(md || ""));
  t = t.replace(/^###\s+(.+)$/gm, "<h4>$1</h4>");
  t = t.replace(/^##\s+(.+)$/gm, "<h3>$1</h3>");
  t = t.replace(/^#\s+(.+)$/gm, "<h2>$1</h2>");
  t = t.replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>");
  t = t.replace(/`([^`]+)`/g, '<code class="mono">$1</code>');
  t = t.replace(/^\s*-\s+(.+)$/gm, "<li>$1</li>");
  t = t.replace(/(?:<li>.*?<\/li>\n?)+/gs, (block) => `<ul>${block}</ul>`);
  t = t.replace(/\n{2,}/g, "<br><br>").replace(/\n/g, "<br>");
  return formatMdTables(t);
}

async function renderBasics() {
  const root = $("view-basics");
  const items = await api("/api/basics");
  root.innerHTML = `
    <div class="grid">
      <div class="card span-4">
        <h3 style="margin-top:0">基础资料库</h3>
        <p class="muted">只放原文表格：周期表 1–118 与九上常用物质化学式、九下古诗词、九下文言文、英语词表。无要点、无练习、无考核。</p>
        <div class="paper-pick" id="basics-list">
          ${items
            .map(
              (it) => `<button type="button" class="paper-card" data-id="${it.id}">
              <strong>${escapeHtml(it.title || it.id)}</strong>
              <div class="muted">${subjectNames[it.subject] || it.subject || ""} · 原文</div>
            </button>`
            )
            .join("")}
        </div>
      </div>
      <div class="card span-8" id="basics-stage">
        <div class="summary-box">
          <p class="stat-label">选择左侧资料查阅</p>
          <p class="muted">表格分列对齐，方便对照记忆。内容仅为教材原文。</p>
        </div>
      </div>
    </div>`;
  $("basics-list").querySelectorAll("[data-id]").forEach((btn) => {
    btn.addEventListener("click", async () => {
      $("basics-list").querySelectorAll(".paper-card").forEach((el) => el.classList.remove("active"));
      btn.classList.add("active");
      const it = await api(`/api/basics/${encodeURIComponent(btn.dataset.id)}`);
      $("basics-stage").innerHTML = `
        <div class="tutorial-panel">
          <h2 style="margin-top:0;font-family:var(--font-display)">${escapeHtml(it.title)}</h2>
          <p class="muted">原文对照 · 无练习 / 无考核</p>
          <div class="markdown-box tutorial-body basics-body">${formatBasicsHtml(it.content_md || "")}</div>
        </div>`;
      renderKatex($("basics-stage"));
    });
  });
}

async function renderLearn() {
  const root = $("view-learn");
  const [policyWrap, followups] = await Promise.all([
    api("/api/mastery-policy").catch(() => null),
    api("/api/learn/unknown-followups?limit=12").catch(() => []),
  ]);
  const pmax = policyWrap?.practice_max_level || "L2";
  const apass = Math.round((policyWrap?.assessment_pass_rate || 0.8) * 100);
  const ppass = Math.round((policyWrap?.practice_pass_rate || 0.75) * 100);
  const cpass = Math.round((policyWrap?.consolidation_pass_rate || 0.75) * 100);
  root.innerHTML = `
    <div class="grid">
      <div class="card span-12">
        <div class="mastery-stats compact">
          <p style="margin:0"><strong>学习 →（可跳过练习）→ 考核</strong>：先学教程；练习最高到
          <span class="level-pill">${pmax}</span>（通过线约 ${ppass}%）。
          也可以跳过练习，直接考核进阶（每次升一档）。
          冲 L3/L4 走考核（通过线 <strong>${apass}%</strong>），题目会变形加难。
          已达 L4 的点不出现在本页列表，可到「知识点」打开学习。</p>
        </div>
      </div>
      <div class="card span-12" id="learn-unknown-wrap">
        <h3 style="margin-top:0">不会专学</h3>
        <p class="muted" style="margin-top:0">仅当某知识点本场未达合格线、且掌握度为 L0 时出现。同一知识点的错题会合并。巩固通过线 <strong>${cpass}%</strong>，通过后从本列表移除，并可升到 L1。</p>
        <div class="paper-pick" id="learn-unknown-list"></div>
      </div>
      <div class="card span-4">
        <h3 style="margin-top:0">知识点教程</h3>
        <label>科目
          <select id="learn-subject">
            <option value="">全部</option>
            ${subjectOptions("chemistry")}
          </select>
        </label>
        <label style="margin-top:8px;display:block">筛选
          <select id="learn-filter">
            <option value="">全部路径</option>
            <option value="practice">可练习晋级</option>
            <option value="assess">需考核 / 已达上限</option>
            <option value="exempt">免练</option>
          </select>
        </label>
        <div class="paper-pick" id="learn-list" style="margin-top:10px"></div>
        <p class="muted" style="font-size:0.8rem;margin-top:12px">没有教程？到「知识点」点「学一学」生成。已达 L4 的点请到知识点页打开。</p>
      </div>
      <div class="card span-8" id="learn-stage">
        <div class="summary-box">
          <p class="stat-label">选择左侧教程或上方「不会专学」开始学习</p>
          <p class="muted">学完后可做课后巩固；标题会标明可升到哪一档。</p>
        </div>
      </div>
    </div>`;

  function renderFollowupDetail(pack) {
    const kid = pack.knowledge_id || (pack.knowledge_ids || [])[0] || "";
    const tuts = pack.tutorials || [];
    const consol = pack.consolidation_paper || {};
    const lesson = pack.focus_lesson || {};
    const passPct = pack.pass_pct || consol.pass_pct || cpass;
    const qn = (pack.source_questions || []).length;
    $("learn-stage").innerHTML = `
      <div class="tutorial-panel">
        <h2 style="margin-top:0;font-family:var(--font-display)">${escapeHtml(
          pack.title || "不会专学"
        )}</h2>
        <p class="muted">针对本场未达合格线的 L0 知识点 · ${(pack.updated_at || pack.created_at || "").replace("T", " ")}</p>
        <p class="mono muted">${escapeHtml(kid)}${qn ? ` · 已合并 ${qn} 道错题/不会题` : ""}</p>
        <h4 style="font-family:var(--font-display)">针对性讲解（题型与变式）</h4>
        <div class="markdown-box tutorial-body" id="learn-focus-lesson">${
          lesson.body_md
            ? formatTutorialHtml(lesson.body_md)
            : `<p class="muted">正在准备针对你不会的题的讲解。</p>`
        }</div>
        <h4 style="font-family:var(--font-display)">完整教程（可对照）</h4>
        <div style="display:flex;flex-wrap:wrap;gap:8px;margin-bottom:12px">
          ${tuts
            .map(
              (t) =>
                `<button type="button" class="chip" data-open-tutorial="${escapeHtml(
                  t.knowledge_id
                )}">${escapeHtml(t.title || t.knowledge_id)}</button>`
            )
            .join("")}
        </div>
        <div id="learn-unknown-tutorial"></div>
        <h4 style="font-family:var(--font-display)">课后巩固</h4>
        <p class="muted">${escapeHtml(consol.label || "巩固卷")} · 通过线 <strong>${passPct}%</strong>，通过后本条会从不会专学列表移除。</p>
        ${
          consol.id
            ? `<button type="button" class="btn-primary" id="learn-do-consol">开始巩固（${
                consol.question_count || "?"
              } 题 · 通过线 ${passPct}%）</button>`
            : `<p class="muted">暂无巩固卷</p>`
        }
      </div>`;
    renderKatex($("learn-stage"));
    $("learn-stage").querySelectorAll("[data-open-tutorial]").forEach((btn) => {
      btn.addEventListener("click", () =>
        openTutorial(btn.dataset.openTutorial, { mountId: "learn-unknown-tutorial" })
      );
    });
    const doBtn = $("learn-do-consol");
    if (doBtn && consol.id) {
      doBtn.onclick = () => {
        cache.pendingPracticePaperId = consol.id;
        switchView("practice");
      };
    }
  }

  const unkList = $("learn-unknown-list");
  // 优先展示「单知识点」新包；多知识点旧包排后
  const sortedFu = [...(followups || [])].sort((a, b) => {
    const sa = (a.knowledge_ids || []).length === 1 || a.knowledge_id ? 0 : 1;
    const sb = (b.knowledge_ids || []).length === 1 || b.knowledge_id ? 0 : 1;
    return sa - sb;
  });
  if (!sortedFu.length) {
    unkList.innerHTML = `<p class="muted">暂无。练习/考核中某知识点未达合格线且为 L0 时，会合并出现在这里。</p>`;
  } else {
    unkList.innerHTML = sortedFu
      .map((f) => {
        const kid = f.knowledge_id || (f.knowledge_ids || [])[0] || "";
        const nq = (f.source_questions || []).length;
        const pct = f.pass_pct || cpass;
        return `<button type="button" class="paper-card" data-pack="${escapeHtml(f.id)}">
          <strong>${escapeHtml(f.title || kid || f.id)}</strong>
          <div class="muted">L0 专学 · ${escapeHtml(kid)} · ${
            f.consolidation_paper?.question_count || 0
          } 道巩固题 · 通过线 ${pct}%${nq ? ` · 已合并 ${nq} 题` : ""}</div>
        </button>`;
      })
      .join("");
    unkList.querySelectorAll("[data-pack]").forEach((btn) => {
      btn.addEventListener("click", async () => {
        unkList.querySelectorAll(".paper-card").forEach((el) => el.classList.remove("active"));
        btn.classList.add("active");
        const pack = await api(`/api/learn/unknown-followups/${encodeURIComponent(btn.dataset.pack)}`);
        renderFollowupDetail(pack);
      });
    });
  }

  let cachedList = [];

  async function refreshList() {
    const subject = $("learn-subject").value;
    const filter = $("learn-filter").value;
    const url = subject
      ? `/api/tutorials?hide_capped=1&subject=${encodeURIComponent(subject)}`
      : "/api/tutorials?hide_capped=1";
    cachedList = await api(url).catch(() => []);
    const list = filter
      ? cachedList.filter(
          (t) =>
            t.path_kind === filter ||
            (filter === "assess" && (t.path_kind === "assess" || t.path_kind === "exempt"))
        )
      : cachedList;
    $("learn-list").innerHTML = list.length
      ? list
          .map(
            (t) => `<button type="button" class="paper-card ${t.exempt ? "exempt" : ""}" data-kid="${t.knowledge_id}">
            <strong>${escapeHtml(t.title || t.name)}</strong>
            <div class="muted">${subjectNames[t.subject_id] || t.subject_id} ·
              掌握 <span class="level-pill">${t.mastery_level || "L0"}</span>
              · 教程目标 ${t.target_level || "—"} · v${t.version || 1}</div>
            <span class="learn-path-pill ${t.path_kind || ""}">${escapeHtml(t.path_hint || "")}</span>
            <div class="mono muted" style="margin-top:6px">${t.knowledge_id}</div>
          </button>`
          )
          .join("")
      : `<p class="muted">暂无教程。到知识点页点「学一学」自动生成。</p>`;
    $("learn-list").querySelectorAll("[data-kid]").forEach((btn) => {
      btn.addEventListener("click", () => {
        $("learn-list").querySelectorAll(".paper-card").forEach((el) => el.classList.remove("active"));
        btn.classList.add("active");
        openTutorial(btn.dataset.kid, { mountId: "learn-stage" });
      });
    });
  }
  $("learn-subject").onchange = refreshList;
  $("learn-filter").onchange = refreshList;
  await refreshList();

  if (cache.openFollowupId || queryStr("pack")) {
    const id = cache.openFollowupId || queryStr("pack");
    cache.openFollowupId = null;
    const btn = unkList.querySelector(`[data-pack="${id}"]`);
    if (btn) btn.click();
    else {
      try {
        const pack = await api(`/api/learn/unknown-followups/${encodeURIComponent(id)}`);
        renderFollowupDetail(pack);
      } catch (_) {
        /* ignore */
      }
    }
  } else if (queryStr("kid")) {
    const kid = queryStr("kid");
    const listEl = $("learn-list");
    const btn = listEl
      ? [...listEl.querySelectorAll("[data-kid]")].find((el) => el.dataset.kid === kid)
      : null;
    if (btn) btn.classList.add("active");
    openTutorial(kid, { mountId: "learn-stage" });
  }
}

async function renderKnowledge() {
  const root = $("view-knowledge");
  const preferred = queryStr("subject") || cache.preferredKnowledgeSubject || "chemistry";
  const policyWrap = await api("/api/mastery-policy").catch(() => null);
  const pmax = policyWrap?.practice_max_level || "L2";
  const apass = Math.round((policyWrap?.assessment_pass_rate || 0.8) * 100);
  root.innerHTML = `
    <div class="toolbar">
      <label>科目
        <select id="kn-subject">${subjectOptions(preferred)}</select>
      </label>
      <div class="kn-view-toggle" role="group" aria-label="视图">
        <button type="button" class="chip active" data-kn-mode="lattice">点阵</button>
        <button type="button" class="chip" data-kn-mode="list">列表</button>
      </div>
      <label class="kn-list-only hidden">考频
        <select id="kn-weight">
          <option value="">全部</option>
          <option value="high">high</option>
          <option value="mid">mid</option>
          <option value="low">low</option>
        </select>
      </label>
      <label class="kn-list-only hidden">搜索
        <input id="kn-q" placeholder="名称或 id" />
      </label>
      <button type="button" class="chip" id="kn-reload">刷新</button>
    </div>
    <p class="muted" style="margin:0 0 12px">练习上限 ${pmax}；考核通过线 ${apass}%。列表「晋级」列显示该点应走练习还是考核。</p>
    <div class="grid">
      <div class="card span-12" id="kn-lattice-panel">
        <div id="kn-stats"></div>
        <div id="kn-lattice" class="lattice-wrap"></div>
      </div>
      <div class="card span-5 kn-list-panel hidden">
        <h3 style="margin-top:0">知识树（拓扑序）</h3>
        <div id="kn-tree" class="tree"></div>
      </div>
      <div class="card span-7 kn-list-panel hidden">
        <h3 style="margin-top:0">列表 / 学一学</h3>
        <div id="kn-table"></div>
      </div>
      <div class="card span-12" id="kn-tutorial"></div>
    </div>`;

  let mode = "lattice";

  function setMode(next) {
    mode = next;
    root.querySelectorAll("[data-kn-mode]").forEach((btn) => {
      btn.classList.toggle("active", btn.dataset.knMode === mode);
    });
    const listOnly = root.querySelectorAll(".kn-list-only, .kn-list-panel");
    listOnly.forEach((el) => el.classList.toggle("hidden", mode !== "list"));
    $("kn-lattice-panel").classList.toggle("hidden", mode !== "lattice");
  }

  function bindOpeners(scope) {
    bindLatticeOpeners(scope, "kn-tutorial");
  }

  async function load() {
    const subject = $("kn-subject").value;
    cache.preferredKnowledgeSubject = subject;
    const lattice = await api(`/api/knowledge/lattice?subject=${encodeURIComponent(subject)}`);
    $("kn-stats").innerHTML = masteryStatsHtml(lattice.stats);
    $("kn-lattice").innerHTML = renderLatticeBoard(lattice);
    bindOpeners($("kn-lattice"));

    if (mode === "list") {
      const weight = $("kn-weight").value;
      const q = $("kn-q").value.trim();
      const tree = await api(`/api/knowledge/tree?subject=${encodeURIComponent(subject)}`);
      $("kn-tree").innerHTML = renderTreeNodes(tree.roots);
      bindOpeners($("kn-tree"));
      const params = new URLSearchParams({ subject });
      if (weight) params.set("weight", weight);
      if (q) params.set("q", q);
      const list = await api(`/api/knowledge?${params}`);
      $("kn-table").innerHTML = `
        <table>
          <thead><tr><th>名称</th><th>深度</th><th>等级</th><th>晋级</th><th>考频</th><th>教程</th><th></th></tr></thead>
          <tbody>
            ${list
              .map((k) => {
                const lv = k.level || "L0";
                let path = `练习可冲 ≤${pmax}`;
                let pathCls = "practice";
                const order = ["L0", "L1", "L2", "L3", "L4"];
                const li = order.indexOf(lv);
                const mi = order.indexOf(pmax);
                if (li > mi) {
                  path = "免练 · 走考核";
                  pathCls = "exempt";
                } else if (li === mi) {
                  path = "已达练习上限 · 考核冲更高";
                  pathCls = "assess";
                }
                return `<tr>
                <td><div>${escapeHtml(k.name)}</div><div class="mono muted">${escapeHtml(k.id)}</div></td>
                <td class="mono">${k.topo_depth ?? 0}</td>
                <td><span class="level-pill">${lv}</span></td>
                <td><span class="learn-path-pill ${pathCls}">${path}</span></td>
                <td class="weight-${k.exam_weight}">${k.exam_weight}</td>
                <td>${k.has_tutorial ? "有" : "无"}</td>
                <td><button type="button" class="chip" data-open-tutorial="${escapeHtml(k.id)}">学一学</button></td>
              </tr>`;
              })
              .join("")}
          </tbody>
        </table>`;
      bindOpeners($("kn-table"));
    }

    if (cache.pendingTutorial || queryStr("kid")) {
      const kid = cache.pendingTutorial || queryStr("kid");
      cache.pendingTutorial = null;
      openTutorial(kid, { mountId: "kn-tutorial" });
    }
  }

  root.querySelectorAll("[data-kn-mode]").forEach((btn) => {
    btn.addEventListener("click", async () => {
      setMode(btn.dataset.knMode);
      await load();
    });
  });
  $("kn-subject").onchange = () => {
    const subject = $("kn-subject").value;
    cache.preferredKnowledgeSubject = subject;
    const router = getRouter();
    if (router && router.currentRoute.value.name === "knowledge") {
      router.replace({ name: "knowledge", query: { subject } }).catch(() => {});
    }
    load();
  };
  $("kn-weight").onchange = load;
  $("kn-reload").onclick = load;
  $("kn-q").onkeydown = (e) => {
    if (e.key === "Enter") load();
  };
  setMode("lattice");
  await load();
}

async function renderMastery() {
  const root = $("view-mastery");
  root.innerHTML = `
    <div class="toolbar">
      <label>科目
        <select id="ms-subject">
          <option value="">全部一期科目</option>
          ${subjectOptions("")}
        </select>
      </label>
      <label>等级
        <select id="ms-level">
          <option value="">全部</option>
          <option>L0</option><option>L1</option><option>L2</option><option>L3</option><option>L4</option>
        </select>
      </label>
      <button type="button" class="chip" id="ms-reload">刷新</button>
    </div>
    <div class="card span-12" id="ms-table"></div>`;

  async function load() {
    const params = new URLSearchParams();
    if ($("ms-subject").value) params.set("subject", $("ms-subject").value);
    if ($("ms-level").value) params.set("level", $("ms-level").value);
    const list = await api(`/api/mastery?${params}`);
    $("ms-table").innerHTML = `
      <table>
        <thead><tr><th>知识点</th><th>科目</th><th>等级</th><th>考频</th><th>错次</th><th>最近考核</th><th></th></tr></thead>
        <tbody>
          ${list
            .map(
              (m) => `<tr>
              <td><div>${m.name}</div><div class="mono muted">${m.knowledge_id}</div></td>
              <td>${subjectNames[m.subject_id] || m.subject_id}</td>
              <td><span class="level-pill">${m.level}</span></td>
              <td class="weight-${m.exam_weight}">${m.exam_weight}</td>
              <td>${m.wrong_count}</td>
              <td>${m.last_assessed || "—"}</td>
              <td><button type="button" class="chip" data-edit='${JSON.stringify({
                knowledge_id: m.knowledge_id,
                level: m.level,
                wrong_count: m.wrong_count,
                notes: m.notes || "",
              }).replace(/'/g, "&#39;")}'>编辑</button></td>
            </tr>`
            )
            .join("")}
        </tbody>
      </table>`;
    $("ms-table").querySelectorAll("[data-edit]").forEach((btn) => {
      btn.addEventListener("click", () => {
        const raw = btn.getAttribute("data-edit").replace(/&#39;/g, "'");
        openEdit(JSON.parse(raw));
      });
    });
  }

  $("ms-subject").onchange = load;
  $("ms-level").onchange = load;
  $("ms-reload").onclick = load;
  await load();
}

function openEdit(item) {
  editingKid = item.knowledge_id;
  $("edit-kid").textContent = item.knowledge_id;
  $("edit-level").value = item.level;
  $("edit-wrong").value = item.wrong_count;
  $("edit-notes").value = item.notes || "";
  $("edit-dialog").showModal();
}

async function renderAssessments() {
  const root = $("view-assessments");
  if (root) {
    root.innerHTML = `<div class="card"><p class="muted">正在加载考核…</p></div>`;
  }
  const [allPapers, policyWrap, actives] = await Promise.all([
    api("/api/practice/papers"),
    api("/api/mastery-policy").catch(() => null),
    api("/api/practice/sessions/active").catch(() => []),
  ]);
  const papers = (allPapers || []).filter((p) => p.paper_kind === "assessment");
  practiceState.papers = papers;
  practiceState.surface = "assessment";
  const apass = Math.round((policyWrap?.assessment_pass_rate || 0.8) * 100);
  const pmax = policyWrap?.practice_max_level || "L2";
  const assessActives = (actives || []).filter((a) => !isPracticePaperId(a.paper_id, a.theme));
  const activeByPaper = {};
  assessActives.forEach((a) => {
    if (a.paper_id) activeByPaper[a.paper_id] = a;
  });
  root.innerHTML = `
    <div class="practice-layout">
      <div>
        <div class="toolbar" style="margin-bottom:10px">
          <label>科目
            <select id="as-subject">
              <option value="">全部</option>
              ${subjectOptions("")}
            </select>
          </label>
        </div>
        <p class="muted" style="font-size:0.85rem;margin:0 0 10px">
          考核用于冲更高档：通过线 <strong>${apass}%</strong>。升档题目会变形加难。
          已完成的卷不出现在列表；到 L4 不再出卷。允许跳过练习直接开考核。
        </p>
        <div id="as-active-banner"></div>
        <div class="paper-pick" id="as-papers"></div>
      </div>
      <div class="practice-stage" id="as-stage">
        <div class="summary-box">
          <p class="stat-label">选择左侧考核卷开始</p>
          <p class="muted">考核负责 L3/L4 熟练度。基础练习请到「练习」页。</p>
        </div>
      </div>
    </div>`;

  async function refreshPapers() {
    const subject = $("as-subject").value;
    const params = subject ? `?subject=${encodeURIComponent(subject)}` : "";
    let list = await api(`/api/practice/papers${params}`);
    list = (list || []).filter((p) => p.paper_kind === "assessment");
    practiceState.papers = list;
    $("as-papers").innerHTML = list.length
      ? list
          .map((p) => {
            const act = p.active_session || activeByPaper[p.id];
            const prog = act
              ? ` · 进行中 ${act.answered_count ?? 0}/${act.total_questions ?? p.question_count}`
              : "";
            return `<button type="button" class="paper-card ${
              act ? "has-progress" : ""
            }" data-id="${p.id}">
            <strong>${escapeHtml(p.display_title || p.level_label || p.theme || p.id)}</strong>
            <div class="muted">${subjectNames[p.subject_id] || p.subject_id || "—"} · 考核 · ${
              p.date || ""
            } · ${p.question_count} 题${
              p.target_level ? ` · 冲 ${p.target_level}` : ""
            } · 通过线 ${apass}%${prog}</div>
            <div class="mono muted" style="margin-top:6px">${p.id}</div>
          </button>`;
          })
          .join("")
      : `<p class="muted">暂无待做考核。已完成的卷已从列表移除；到 L4 的点不再出卷，下一批按知识清单自动出现。</p>`;
    $("as-papers").querySelectorAll(".paper-card").forEach((btn) => {
      btn.addEventListener("click", () => startPractice(btn.dataset.id));
    });
  }

  $("as-subject").onchange = refreshPapers;
  await refreshPapers();
  await restoreActivePractice(assessActives, "assessment");
  if (cache.pendingPracticePaperId) {
    const paperId = cache.pendingPracticePaperId;
    cache.pendingPracticePaperId = null;
    if (!isPracticePaperId(paperId)) {
      await startPractice(paperId);
    } else {
      cache.pendingPracticePaperId = paperId;
      switchView("practice");
    }
  }
}

async function skipToAssessment(knowledgeIds, dayPlanId) {
  const res = await api("/api/assessments/skip-practice", {
    method: "POST",
    body: JSON.stringify({
      knowledge_ids: knowledgeIds || [],
      day_plan_id: dayPlanId || null,
    }),
  });
  const paper = res.paper || (res.papers && res.papers[0]);
  if (!paper || !paper.id) {
    throw new Error("未生成考核卷");
  }
  cache.pendingPracticePaperId = paper.id;
  switchView("assessments");
}

async function renderPlan() {
  const data = await api("/api/plan");
  const weekPct = data.week_progress?.percent ?? 0;
  const days = data.days || [];
  const todayId = data.today_plan?.id;
  const week = data.week || {};
  const statusLabel = {
    pending: "未开始",
    in_progress: "进行中",
    completed: "已完成",
  };

  $("view-plan").innerHTML = `
    <div class="grid plan-board">
      <div class="card span-12 plan-box">
        <div class="plan-box-label">本周概览</div>
        <div class="plan-fields">
          <label class="plan-field">
            <span>周标题</span>
            <div class="plan-readonly">${escapeHtml(week.title || "本周学习计划")}</div>
          </label>
          <label class="plan-field">
            <span>日期范围</span>
            <div class="plan-readonly">${escapeHtml(
              [week.start_date, week.end_date].filter(Boolean).join(" ~ ") || "—"
            )}</div>
          </label>
          <label class="plan-field plan-field-wide">
            <span>本周进度</span>
            <div class="plan-readonly plan-readonly-progress">
              <strong>${data.week_progress?.done ?? 0} / ${data.week_progress?.total ?? 0}</strong>
              <span class="muted">知识点已完成 · ${weekPct}%</span>
              <div class="plan-progress-bar thin"><span style="width:${weekPct}%"></span></div>
            </div>
          </label>
        </div>
        <p class="muted" style="margin:12px 0 0">计划由系统自动维护。你只需在练完后回答「过多 / 适中 / 过少」。</p>
      </div>

      <div class="card span-4 plan-box">
        <div class="plan-box-label">本周日程</div>
        <div class="day-box-list" id="plan-day-list">
          ${
            days.length
              ? days
                  .map((d) => {
                    const p = d.progress || { done: 0, total: 0, percent: 0 };
                    const active = d.id === todayId ? "active" : "";
                    return `<button type="button" class="day-box ${active} status-${d.status}" data-focus-day="${d.id}">
                      <div class="day-box-top">
                        <strong>${escapeHtml(d.title || d.plan_date)}</strong>
                        <span class="level-pill">${statusLabel[d.status] || d.status}</span>
                      </div>
                      <div class="muted">${escapeHtml(d.plan_date)}</div>
                      <div class="day-box-meta">${p.done}/${p.total} 知识点</div>
                      <div class="plan-progress-bar thin"><span style="width:${p.percent}%"></span></div>
                    </button>`;
                  })
                  .join("")
              : `<p class="muted">正在准备日计划…</p>`
          }
        </div>
      </div>

      <div class="card span-8 plan-box" id="plan-day-panel"></div>

      <div class="card span-12 plan-box" id="plan-survey-wrap">
        <div class="plan-box-label">负荷反馈</div>
        <div id="plan-survey-box" class="survey-box survey-box-inline">
          <p class="muted">完成今日全部练习后，这里会出现「过多 / 适中 / 过少」三选一。</p>
        </div>
      </div>
    </div>`;

  let focusId = todayId || (days[0] && days[0].id);
  const surveyState = { day_plan_id: null, volume: null };

  function renderDayPanel(dayId) {
    const d = days.find((x) => x.id === dayId) || data.today_plan;
    const panel = $("plan-day-panel");
    if (!d) {
      panel.innerHTML = `<div class="plan-box-label">日计划</div><p class="muted">没有可显示的日计划。</p>`;
      return;
    }
    focusId = d.id;
    document.querySelectorAll("[data-focus-day]").forEach((el) => {
      el.classList.toggle("active", el.dataset.focusDay === d.id);
    });
    const p = d.progress || { done: 0, total: 0, percent: 0 };
    const isToday = d.plan_date === data.today;
    panel.innerHTML = `
      <div class="plan-box-label">${isToday ? "今日计划" : "日计划详情"}</div>
      <div class="plan-fields">
        <label class="plan-field">
          <span>日期</span>
          <div class="plan-readonly">${escapeHtml(d.plan_date)}</div>
        </label>
        <label class="plan-field">
          <span>状态</span>
          <div class="plan-readonly">${statusLabel[d.status] || d.status} · ${p.done}/${p.total}（${p.percent}%）</div>
        </label>
        <label class="plan-field plan-field-wide">
          <span>标题</span>
          <div class="plan-readonly">${escapeHtml(d.title || "")}</div>
        </label>
        <label class="plan-field plan-field-wide">
          <span>主攻内容</span>
          <div class="plan-readonly">${escapeHtml(d.focus_text || "")}</div>
        </label>
        <label class="plan-field plan-field-wide">
          <span>复习 / 巩固</span>
          <div class="plan-readonly">${escapeHtml(d.review_text || "")}</div>
        </label>
      </div>
      <div class="plan-box-label" style="margin-top:14px">知识点清单</div>
      <div class="plan-item-boxes">
        ${
          (d.items || []).length
            ? (d.items || [])
                .map(
                  (it) => `<div class="plan-item-box ${it.done ? "done" : ""}">
                    <div class="plan-item-body">
                      <strong>${escapeHtml(it.name || it.knowledge_id)}</strong>
                      <div class="mono muted">${escapeHtml(it.knowledge_id)}</div>
                      <div class="plan-item-tags">
                        <span class="level-pill">${it.level || "L0"}</span>
                        <span class="chip">${
                          it.done
                            ? "已完成"
                            : it.path === "assess"
                              ? "待考核"
                              : it.path === "cap"
                                ? "已到顶"
                                : "待学习"
                        }</span>
                      </div>
                      ${
                        it.done
                          ? ""
                          : `<div class="plan-item-actions">
                        <button type="button" class="chip" data-open-tutorial="${escapeHtml(
                          it.knowledge_id
                        )}">学教程</button>
                        ${
                          it.path === "assess" || it.path === "cap"
                            ? `<button type="button" class="chip" data-goto-assess="${escapeHtml(
                                it.knowledge_id
                              )}">去考核</button>`
                            : `<button type="button" class="chip" data-goto-practice="${escapeHtml(
                                it.knowledge_id
                              )}">去练习</button>`
                        }
                        ${
                          it.allow_skip
                            ? `<button type="button" class="chip" data-skip-assess="${escapeHtml(
                                it.knowledge_id
                              )}">跳过练习·考核</button>`
                            : ""
                        }
                      </div>`
                      }
                    </div>
                  </div>`
                )
                .join("")
            : `<p class="muted">这一天还没有知识点条目。</p>`
        }
      </div>
      ${
        isToday && d.status !== "completed"
          ? `<p class="muted" style="margin-top:16px">今日可同时推进多个知识点：先学教程，练习可跳过，直接考核进阶。全部完成后回来填负荷反馈。</p>
          <p style="margin-top:10px"><button type="button" class="btn-primary" id="plan-skip-all">今日未完成项：跳过练习并开考核</button></p>`
          : ""
      }`;
    panel.querySelectorAll("[data-open-tutorial]").forEach((btn) => {
      btn.addEventListener("click", () => openTutorial(btn.dataset.openTutorial));
    });
    panel.querySelectorAll("[data-skip-assess]").forEach((btn) => {
      btn.addEventListener("click", () => skipToAssessment([btn.dataset.skipAssess]));
    });
    panel.querySelectorAll("[data-goto-assess]").forEach((btn) => {
      btn.addEventListener("click", () => skipToAssessment([btn.dataset.gotoAssess]));
    });
    panel.querySelectorAll("[data-goto-practice]").forEach((btn) => {
      btn.addEventListener("click", () => switchView("practice"));
    });
    const skipAll = $("plan-skip-all");
    if (skipAll) {
      skipAll.onclick = () => skipToAssessment([], d.id);
    }
  }

  function showSurvey(survey, ctx) {
    const box = $("plan-survey-box");
    if (!survey) {
      box.innerHTML = `<p class="muted">暂无待填问卷。</p>`;
      return;
    }
    box.innerHTML = `
      <p class="survey-q">${escapeHtml(survey.question || "")}</p>
      <div class="survey-options">
        ${(survey.options || [])
          .map(
            (o) =>
              `<button type="button" class="btn-ghost survey-opt" data-val="${escapeHtml(
                String(o.value)
              )}">${escapeHtml(o.label)}</button>`
          )
          .join("")}
      </div>`;

    box.querySelectorAll(".survey-opt").forEach((btn) => {
      btn.addEventListener("click", async () => {
        const step = survey.step || ctx.step || "volume";
        let payload = { day_plan_id: surveyState.day_plan_id || ctx.day_plan_id };
        if (step === "volume") {
          surveyState.volume = btn.dataset.val;
          payload.volume = surveyState.volume;
        } else if (step === "rescale_week") {
          payload.volume = surveyState.volume || ctx.volume;
          payload.rescale_week = btn.dataset.val === "yes" || btn.dataset.val === "true";
        }
        const res = await api("/api/plan/survey", {
          method: "POST",
          body: JSON.stringify(payload),
        });
        if (res.done) {
          box.innerHTML = `<p><strong>${escapeHtml(res.message || "已保存")}</strong></p>`;
          setTimeout(() => renderPlan(), 600);
        } else {
          showSurvey(
            {
              step: res.step,
              question: res.question,
              options: res.options,
            },
            res
          );
        }
      });
    });
  }

  document.querySelectorAll("[data-focus-day]").forEach((btn) => {
    btn.addEventListener("click", () => renderDayPanel(btn.dataset.focusDay));
  });
  renderDayPanel(focusId);

  if (data.pending_survey) {
    surveyState.day_plan_id = data.pending_survey.day_plan_id;
    showSurvey(data.pending_survey, data.pending_survey);
  }
}

async function renderProfile() {
  const data = await api("/api/profile");
  const student = data.student || {};
  const policy = data.exam_policy || {};
  const subjects = policy.subjects || {};
  $("view-profile").innerHTML = `
    <div class="grid">
      <div class="card span-6">
        <h3 style="margin-top:0;font-family:var(--font-display)">学生</h3>
        <p><strong>${student.name || "—"}</strong> · ${student.grade || ""}</p>
        <p class="muted">${(student.region && [student.region.province, student.region.city, student.region.district].filter(Boolean).join(" ")) || ""}</p>
        <p>考季：${student.exam_year || "—"}</p>
        <p>一期科目：${(student.phase1_subjects || []).map((s) => subjectNames[s] || s).join("、")}</p>
        <p>二期预留：${(student.phase2_subjects || []).map((s) => subjectNames[s] || s).join("、")}</p>
      </div>
      <div class="card span-6">
        <h3 style="margin-top:0;font-family:var(--font-display)">计分摘要</h3>
        <p>总分口径：${policy.total_score ?? "—"} · ${policy.scoring_mode || ""}</p>
        <table>
          <thead><tr><th>科目</th><th>卷面</th><th>计入</th><th>期</th></tr></thead>
          <tbody>
            ${Object.entries(subjects)
              .map(
                ([id, s]) => `<tr>
                <td>${s.name || subjectNames[id] || id}</td>
                <td>${s.paper_full ?? "—"}</td>
                <td>${s.admit_score ?? "—"}</td>
                <td>${s.phase ?? "—"}</td>
              </tr>`
              )
              .join("")}
          </tbody>
        </table>
      </div>
      <div class="card span-12">
        <h3 style="margin-top:0;font-family:var(--font-display)">成就</h3>
        <div id="profile-achievements"><p class="muted">加载中…</p></div>
      </div>
    </div>`;
  try {
    const ach = await api("/api/achievements");
    const box = $("profile-achievements");
    if (box) {
      box.innerHTML = `<p class="muted">已解锁 ${ach.unlocked_count || 0} / ${ach.total || 0}</p>
        <div class="achieve-list">
          ${(ach.items || [])
            .map(
              (it) => `<div class="achieve-item ${it.unlocked ? "on" : "off"}">
              <strong>${escapeHtml(it.title)}</strong>
              <p class="muted" style="margin:4px 0 0">${escapeHtml(it.detail)}</p>
              ${
                it.unlocked_at
                  ? `<p class="muted" style="margin:4px 0 0;font-size:0.8rem">${escapeHtml(
                      String(it.unlocked_at).replace("T", " ")
                    )}</p>`
                  : `<p class="muted" style="margin:4px 0 0;font-size:0.8rem">未达成</p>`
              }
            </div>`
            )
            .join("")}
        </div>`;
    }
  } catch (_) {
    const box = $("profile-achievements");
    if (box) box.innerHTML = `<p class="muted">成就列表暂不可用。</p>`;
  }
}

function escapeHtml(str) {
  return String(str ?? "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

/** 把题干中的 ______ 换成逐空输入框 */
function renderFillStemHtml(stem) {
  const text = String(stem || "");
  const parts = text.split(/_{2,}/);
  const blankCount = Math.max(0, parts.length - 1);
  if (blankCount === 0) {
    return {
      blankCount: 1,
      html: `${escapeHtml(text).replace(/\n/g, "<br>")}<div style="margin-top:12px"><input type="text" class="blank-input" data-blank="0" autocomplete="off" placeholder="填写答案" /></div>`,
    };
  }
  let html = "";
  parts.forEach((part, i) => {
    html += escapeHtml(part).replace(/\n/g, "<br>");
    if (i < blankCount) {
      html += `<input type="text" class="blank-input" data-blank="${i}" autocomplete="off" />`;
    }
  });
  return { blankCount, html };
}

function usesMathPad(q) {
  const sub = String(q?.subject_id || (q?.knowledge_ids || [])[0] || "");
  return sub === "math" || sub === "physics" || sub.startsWith("math.") || sub.startsWith("physics.");
}

function mathPadHtml() {
  const rows = [
    [
      ["7", "7"],
      ["8", "8"],
      ["9", "9"],
      ["÷", "÷"],
      ["⌫", "back"],
    ],
    [
      ["4", "4"],
      ["5", "5"],
      ["6", "6"],
      ["×", "×"],
      ["√", "√"],
    ],
    [
      ["1", "1"],
      ["2", "2"],
      ["3", "3"],
      ["-", "-"],
      ["²", "²"],
    ],
    [
      ["0", "0"],
      [".", "."],
      ["/", "/"],
      ["+", "+"],
      ["³", "³"],
    ],
    [
      ["(", "("],
      [")", ")"],
      ["π", "π"],
      ["±", "±"],
      ["^", "^"],
    ],
  ];
  const grid = rows
    .map(
      (row) =>
        `<div class="math-pad-row">${row
          .map(
            ([lab, val]) =>
              `<button type="button" class="math-pad-key" data-math-key="${escapeHtml(
                val
              )}">${escapeHtml(lab)}</button>`
          )
          .join("")}</div>`
    )
    .join("");
  return `<div class="math-pad" id="pr-math-pad">
    <p class="muted" style="margin:0 0 6px;font-size:0.8rem">数学符号键盘（点空格后再点符号）</p>
    ${grid}
  </div>`;
}

function insertAtCursor(el, text) {
  if (!el) return;
  el.focus();
  const value = el.value || "";
  const start = el.selectionStart == null ? value.length : el.selectionStart;
  const end = el.selectionEnd == null ? value.length : el.selectionEnd;
  el.value = value.slice(0, start) + text + value.slice(end);
  const pos = start + text.length;
  if (el.setSelectionRange) el.setSelectionRange(pos, pos);
}

function bindMathPad(stage) {
  const pad = stage.querySelector("#pr-math-pad");
  if (!pad) return;
  let target = stage.querySelector(".blank-input, #pr-fill");
  stage.querySelectorAll(".blank-input, #pr-fill").forEach((el) => {
    el.addEventListener("focus", () => {
      target = el;
    });
  });
  pad.querySelectorAll("[data-math-key]").forEach((btn) => {
    btn.addEventListener("mousedown", (ev) => ev.preventDefault());
    btn.addEventListener("click", () => {
      const el = target || stage.querySelector(".blank-input, #pr-fill");
      if (!el) return;
      const key = btn.dataset.mathKey || "";
      if (key === "back") {
        const value = el.value || "";
        const start = el.selectionStart == null ? value.length : el.selectionStart;
        const end = el.selectionEnd == null ? value.length : el.selectionEnd;
        if (start !== end) {
          el.value = value.slice(0, start) + value.slice(end);
          if (el.setSelectionRange) el.setSelectionRange(start, start);
        } else if (start > 0) {
          el.value = value.slice(0, start - 1) + value.slice(start);
          if (el.setSelectionRange) el.setSelectionRange(start - 1, start - 1);
        }
        el.focus();
        return;
      }
      insertAtCursor(el, key);
    });
  });
}

function renderKatex(root) {
  if (!window.renderMathInElement || !root) return;
  try {
    window.renderMathInElement(root, {
      delimiters: [
        { left: "$$", right: "$$", display: true },
        { left: "\\(", right: "\\)", display: false },
        { left: "$", right: "$", display: false },
      ],
      ignoredTags: [
        "script",
        "noscript",
        "style",
        "textarea",
        "pre",
        "code",
        "option",
        "input",
        "button",
      ],
      throwOnError: false,
    });
  } catch (_) {
    /* 公式渲染失败不阻断做题 */
  }
}

let practiceState = {
  papers: [],
  sessionId: null,
  paperId: null,
  index: 0,
  total: 0,
  questionIds: [],
  results: {}, // question_id -> 'ok'|'bad'|'pending'
  startedAt: 0,
  finished: false,
  summary: null,
  surface: "practice", // practice | assessment
  reportNotice: null,
};

function formatDetailedExplanation(exp, answerKey) {
  let t = String(exp || "").trim();
  const key = String(answerKey || "").trim();
  if (!t) return "";
  t = t.replace(/^[A-D对错][.。、．]?\s*/, "");
  if (!t || t === key) return "";
  return t;
}

function isPracticePaperId(paperId, theme) {
  const id = String(paperId || "");
  const th = String(theme || "");
  return id.startsWith("drill-") || th.includes("drill");
}

function practiceStage() {
  return $(practiceState.surface === "assessment" ? "as-stage" : "pr-stage");
}

function setSurfaceFromPaper(paperId, theme) {
  practiceState.surface = isPracticePaperId(paperId, theme) ? "practice" : "assessment";
}

async function renderCalc() {
  const root = $("view-calc");
  if (!root) return;
  const topics = await api("/api/calc-drills").catch(() => []);
  root.innerHTML = `
    <div class="grid">
      <div class="card span-12">
        <p style="margin:0">选一个专题，系统会用<strong>不同数字</strong>出同一类计算题（仿可汗学院）。准确率从<strong>第一次练习</strong>开始累计。若验算后确认标准答案错了，可点「报错」：系统立刻重算修正，该题不计入正确率，刷新后可再做。</p>
      </div>
      ${(topics || [])
        .map((t) => {
          const tot = Number(t.total || 0);
          const ok = Number(t.correct || 0);
          const pct = tot ? Math.round((ok / tot) * 100) : null;
          const voided = Number(t.voided_count || 0);
          const recs = t.records || [];
          const recHtml = recs.length
            ? `<table class="calc-records">
                <thead><tr><th>时间</th><th>计入</th><th>报错</th></tr></thead>
                <tbody>
                  ${recs
                    .map(
                      (r) => `<tr>
                        <td>${escapeHtml(String(r.started_at || "").replace("T", " "))}</td>
                        <td>${r.correct}/${r.total}</td>
                        <td>${r.voided ? r.voided : "—"}</td>
                      </tr>`
                    )
                    .join("")}
                </tbody>
              </table>`
            : `<p class="muted" style="margin:8px 0 0">还没有练习记录。</p>`;
          return `<div class="card span-4">
          <h3 style="margin-top:0;font-family:var(--font-display)">${escapeHtml(t.title)}</h3>
          <p class="muted">${escapeHtml(t.blurb || "")}</p>
          <p class="calc-acc">${
            pct == null
              ? "尚未练习"
              : `计算准确率 <strong>${pct}%</strong> <span class="muted">（${ok}/${tot}）</span>`
          }</p>
          ${
            voided
              ? `<p class="muted" style="margin:4px 0 0;font-size:0.85rem">另有 ${voided} 题报错未计入</p>`
              : ""
          }
          <p class="mono muted">${escapeHtml(t.knowledge_id || "")}</p>
          <button type="button" class="btn-primary" data-calc="${escapeHtml(t.id)}">开始 8 题</button>
          <h4 style="margin:14px 0 6px;font-size:0.9rem">练习记录</h4>
          ${recHtml}
        </div>`;
        })
        .join("")}
    </div>`;
  root.querySelectorAll("[data-calc]").forEach((btn) => {
    btn.addEventListener("click", async () => {
      btn.disabled = true;
      try {
        const session = await api("/api/calc-drills/start", {
          method: "POST",
          body: JSON.stringify({ topic_id: btn.dataset.calc, count: 8 }),
        });
        cache.pendingPracticePaperId = session.paper_id;
        switchView("practice");
      } catch (err) {
        alert(err.message || "无法开始计算专题");
        btn.disabled = false;
      }
    });
  });
}

async function renderPractice() {
  const root = $("view-practice");
  if (root) {
    root.innerHTML = `<div class="card"><p class="muted">正在加载练习…</p></div>`;
  }
  const [allPapers, policyWrap, actives] = await Promise.all([
    api("/api/practice/papers"),
    api("/api/mastery-policy").catch(() => null),
    api("/api/practice/sessions/active").catch(() => []),
  ]);
  const papers = (allPapers || []).filter((p) => p.paper_kind === "practice");
  practiceState.papers = papers;
  practiceState.surface = "practice";
  const pmax = policyWrap?.practice_max_level || "L2";
  const ppass = Math.round((policyWrap?.practice_pass_rate || 0.75) * 100);
  const practiceActives = (actives || []).filter((a) =>
    isPracticePaperId(a.paper_id, a.theme)
  );
  const activeByPaper = {};
  practiceActives.forEach((a) => {
    if (a.paper_id) activeByPaper[a.paper_id] = a;
  });
  root.innerHTML = `
    <div class="practice-layout">
      <div>
        <div class="toolbar" style="margin-bottom:10px">
          <label>科目
            <select id="pr-subject">
              <option value="">全部</option>
              ${subjectOptions("")}
            </select>
          </label>
        </div>
        <p class="muted" style="font-size:0.85rem;margin:0 0 10px">
          练习最高升到 <strong>${pmax}</strong>（通过线约 ${ppass}%）。
          已达 ${pmax} 的知识点免练，请到「考核」冲更高档。也可跳过练习直接考核进阶。
        </p>
        <p style="margin:0 0 10px">
          <button type="button" class="chip" id="pr-skip-assess">跳过练习，直接考核</button>
        </p>
        <div id="pr-active-banner"></div>
        <div class="paper-pick" id="pr-papers"></div>
      </div>
      <div class="practice-stage" id="pr-stage">
        <div class="summary-box">
          <p class="stat-label">选择左侧练习卷开始</p>
          <p class="muted">练习用于打基础，最高到 ${pmax}。冲更高熟练度请用侧栏「考核」。</p>
          <p class="muted" id="pr-llm-hint" style="margin-top:8px"></p>
        </div>
      </div>
    </div>`;

  async function refreshPapers() {
    const subject = $("pr-subject").value;
    const params = subject ? `?subject=${encodeURIComponent(subject)}` : "";
    let list = await api(`/api/practice/papers${params}`);
    list = (list || []).filter((p) => p.paper_kind === "practice");
    practiceState.papers = list;
    $("pr-papers").innerHTML = list.length
      ? list
          .map((p) => {
            const act = p.active_session || activeByPaper[p.id];
            const prog = act
              ? ` · 进行中 ${act.answered_count ?? 0}/${act.total_questions ?? p.question_count}`
              : "";
            return `<button type="button" class="paper-card ${p.exempt ? "exempt" : ""} ${
              act ? "has-progress" : ""
            }" data-id="${p.id}">
            <strong>${escapeHtml(p.display_title || p.level_label || p.theme || p.id)}</strong>
            <div class="muted">${subjectNames[p.subject_id] || p.subject_id || "—"} · 练习 · ${
              p.date || ""
            } · ${p.question_count} 题${
              p.goal_level ? ` · 可升 ${p.goal_level}` : ""
            }${p.exempt ? " · 免练" : ""}${prog}</div>
            <div class="mono muted" style="margin-top:6px">${p.id}</div>
          </button>`;
          })
          .join("")
      : `<p class="muted">暂无练习卷。请先完成日计划或到「学习」生成巩固练习。</p>`;
    $("pr-papers").querySelectorAll(".paper-card").forEach((btn) => {
      btn.addEventListener("click", () => startPractice(btn.dataset.id));
    });
  }

  $("pr-subject").onchange = refreshPapers;
  const skipBtn = $("pr-skip-assess");
  if (skipBtn) {
    skipBtn.onclick = async () => {
      const kids = (practiceState.papers || []).flatMap((p) => p.knowledge_ids || []);
      const plan = await api("/api/plan").catch(() => null);
      await skipToAssessment(kids, plan?.today_plan?.id || null);
    };
  }
  await refreshPapers();
  try {
    const st = await api("/api/practice/llm-status");
    const hint = $("pr-llm-hint");
    if (hint) {
      hint.textContent = st.configured
        ? `主观题大模型批改已启用（${st.model}）`
        : st.hint || "主观题大模型尚未配置";
    }
  } catch (_) {
    /* ignore */
  }

  await restoreActivePractice(practiceActives, "practice");
  if (cache.pendingPracticePaperId) {
    const paperId = cache.pendingPracticePaperId;
    cache.pendingPracticePaperId = null;
    if (isPracticePaperId(paperId)) {
      await startPractice(paperId);
    } else {
      cache.pendingPracticePaperId = paperId;
      switchView("assessments");
    }
  }
}

async function restoreActivePractice(actives, kind = "practice") {
  // 若本页已有进行中会话挂载，不重复打断
  if (practiceState.sessionId && !practiceState.finished && $("pr-check")) {
    savePracticeProgress();
    return;
  }
  let target = null;
  const local = loadPracticeProgressLocal();
  const wantPractice = kind === "practice";
  const list = (actives && actives.length
    ? actives
    : await api("/api/practice/sessions/active").catch(() => [])
  ).filter((a) => isPracticePaperId(a.paper_id, a.theme) === wantPractice);
  if (local?.sessionId) {
    target = (list || []).find((a) => a.session_id === local.sessionId) || null;
  }
  if (!target && list?.length) {
    target = list[0];
  }
  const banner = $(kind === "assessment" ? "as-active-banner" : "pr-active-banner");
  if (!target) {
    if (banner) banner.innerHTML = "";
    return;
  }
  if (banner) {
    const btnId = kind === "assessment" ? "as-continue-btn" : "pr-continue-btn";
    const label = kind === "assessment" ? "继续考核" : "继续练习";
    banner.innerHTML = `
      <div class="active-progress-banner">
        <span>未完成：已作答 <strong>${target.answered_count || 0}/${
          target.total_questions
        }</strong>，可继续第 ${(target.resume_index || 0) + 1} 题</span>
        <button type="button" class="chip active" id="${btnId}">${label}</button>
      </div>`;
    $(btnId).onclick = () => startPractice(target.paper_id);
  }
}

const PRACTICE_ACTIVE_KEY = "yuki_practice_active_v1";

function savePracticeProgress() {
  if (!practiceState.sessionId || practiceState.finished) {
    try {
      localStorage.removeItem(PRACTICE_ACTIVE_KEY);
    } catch (_) {
      /* ignore */
    }
    return;
  }
  const payload = {
    sessionId: practiceState.sessionId,
    paperId: practiceState.paperId,
    index: practiceState.index,
    total: practiceState.total,
    questionIds: practiceState.questionIds,
    results: practiceState.results,
    savedAt: Date.now(),
  };
  try {
    localStorage.setItem(PRACTICE_ACTIVE_KEY, JSON.stringify(payload));
  } catch (_) {
    /* ignore */
  }
}

function loadPracticeProgressLocal() {
  try {
    const raw = localStorage.getItem(PRACTICE_ACTIVE_KEY);
    if (!raw) return null;
    return JSON.parse(raw);
  } catch (_) {
    return null;
  }
}

function clearPracticeProgressLocal() {
  try {
    localStorage.removeItem(PRACTICE_ACTIVE_KEY);
  } catch (_) {
    /* ignore */
  }
}

function applySessionToState(session) {
  setSurfaceFromPaper(session.paper_id, session.theme);
  practiceState.sessionId = session.session_id;
  practiceState.paperId = session.paper_id;
  practiceState.total = session.total_questions;
  practiceState.questionIds = (session.questions || []).map((q) => q.id);
  practiceState.results = session.results || {};
  practiceState.startedAt = Date.now();
  practiceState.finished = false;
  practiceState.summary = null;
  const idx =
    session.resume_index != null
      ? session.resume_index
      : session.current_index != null
        ? session.current_index
        : 0;
  practiceState.index = Math.max(0, Math.min(idx, Math.max(0, practiceState.total - 1)));
  savePracticeProgress();
}

async function startPractice(paperId, options = {}) {
  setSurfaceFromPaper(paperId);
  const forceNew = !!options.forceNew;
  let session;
  try {
    session = await api("/api/practice/sessions", {
      method: "POST",
      body: JSON.stringify({ paper_id: paperId, force_new: forceNew }),
    });
  } catch (err) {
    const msg = err.message || "无法开始练习";
    const stage = practiceStage();
    if (stage) {
      stage.innerHTML = `<div class="feedback-panel bad">${escapeHtml(msg)}</div>`;
    } else {
      alert(msg);
    }
    return;
  }
  applySessionToState(session);
  document.querySelectorAll(".paper-card").forEach((el) => {
    el.classList.toggle("active", el.dataset.id === paperId);
  });
  await showPracticeQuestion(practiceState.index);
  if (session.resumed) {
    const stage = practiceStage();
    if (stage && !$("pr-resume-banner")) {
      const tip = document.createElement("p");
      tip.className = "muted";
      tip.id = "pr-resume-banner";
      tip.style.cssText = "margin:0 0 10px;font-size:0.9rem";
      tip.textContent = `已恢复进度：已作答 ${session.answered_count || 0}/${
        session.total_questions
      } 题，从第 ${practiceState.index + 1} 题继续。`;
      stage.insertBefore(tip, stage.firstChild);
    }
  }
}

async function showPracticeQuestion(index) {
  if (practiceState.finished) return;
  const data = await api(
    `/api/practice/sessions/${practiceState.sessionId}/question?index=${index}`
  );
  practiceState.index = data.index;
  practiceState.total = data.total;
  practiceState.startedAt = Date.now();
  savePracticeProgress();
  const q = data.question;
  const stage = practiceStage();
  const dots = Array.from({ length: data.total }, (_, i) => {
    const qid = practiceState.questionIds[i];
    const st = practiceState.results[qid] || "";
    const cls = [i === index ? "current" : "", st].filter(Boolean).join(" ");
    return `<button type="button" class="${cls}" data-idx="${i}">${i + 1}</button>`;
  }).join("");

  let answerArea = "";
  let blankCount = 0;
  if (q.qtype === "choice") {
    answerArea = `<div class="option-list" id="pr-options">
      ${(q.options || [])
        .map(
          (o) => `<button type="button" class="option-btn" data-val="${o.label}">
          <span class="opt-label">${o.label}</span>
          <span class="opt-content">${escapeHtml(o.content)}</span>
        </button>`
        )
        .join("")}
    </div>`;
  } else if (q.qtype === "judge") {
    answerArea = `<div class="judge-row" id="pr-options">
      <button type="button" class="option-btn" data-val="对">对</button>
      <button type="button" class="option-btn" data-val="错">错</button>
    </div>`;
  } else if (q.qtype === "fill") {
    const rendered = renderFillStemHtml(q.stem);
    blankCount = rendered.blankCount || q.blank_count || 0;
    answerArea = `<div class="fill-stem" id="pr-fill-stem">${rendered.html}</div>
      <p class="muted" style="margin:0;font-size:0.85rem">请在题干空格处填写；多空不必手写分号。</p>`;
  } else {
    answerArea = `<textarea class="answer-input" id="pr-fill" rows="4" placeholder="${
      q.llm_grading
        ? "写下要点，提交后由大模型自动批改"
        : "写下要点（可对照解析自评；配置大模型后可自动批改）"
    }"></textarea>`;
  }

  const stemHtml =
    q.qtype === "fill"
      ? ""
      : `<div class="q-stem" id="pr-stem">${escapeHtml(q.stem).replace(/\n/g, "<br>")}</div>`;

  stage.innerHTML = `
    <div class="progress-dots">${dots}</div>
    <div class="q-meta">
      <span class="level-pill">${q.qtype}</span>
      <span class="muted">第 ${index + 1} / ${data.total} 题 · ${q.score} 分</span>
      ${q.llm_grading ? `<span class="chip">AI批改</span>` : ""}
      ${(q.knowledge_ids || [])
        .map(
          (k) =>
            `<button type="button" class="chip mono" data-open-tutorial="${k}">${k}</button>`
        )
        .join("")}
    </div>
    ${stemHtml}
    ${answerArea}
    ${usesMathPad(q) && (q.qtype === "fill" || q.qtype === "short") ? mathPadHtml() : ""}
    ${
      practiceState.reportNotice && practiceState.reportNotice.qid === q.id
        ? `<div class="feedback-panel pending" id="pr-report-banner">${escapeHtml(
            practiceState.reportNotice.detail || ""
          )}</div>`
        : ""
    }
    <div id="pr-feedback"></div>
    <div class="practice-actions">
      <button type="button" class="btn-primary" id="pr-check">检查答案</button>
      <button type="button" class="btn-ghost btn-dont-know" id="pr-dont-know">不会</button>
      ${
        data.can_report
          ? `<button type="button" class="btn-ghost btn-report" id="pr-report">报错</button>`
          : ""
      }
      <button type="button" class="btn-ghost" id="pr-next" disabled>下一题</button>
      <button type="button" class="btn-ghost" id="pr-finish">结束并查看总结</button>
    </div>`;

  renderKatex(stage);
  bindMathPad(stage);
  if (data.excluded_from_stats) {
    const nxt = $("pr-next");
    if (nxt) nxt.disabled = false;
  }

  let selected = "";
  stage.querySelectorAll(".option-btn").forEach((btn) => {
    btn.addEventListener("click", () => {
      stage.querySelectorAll(".option-btn").forEach((b) => b.classList.remove("selected"));
      btn.classList.add("selected");
      selected = btn.dataset.val;
    });
  });
  stage.querySelectorAll(".progress-dots button").forEach((btn) => {
    btn.addEventListener("click", () => showPracticeQuestion(Number(btn.dataset.idx)));
  });
  stage.querySelectorAll("[data-open-tutorial]").forEach((btn) => {
    btn.addEventListener("click", () => openTutorial(btn.dataset.openTutorial));
  });

  // 填空：回车跳到下一空
  stage.querySelectorAll(".blank-input").forEach((input, i, list) => {
    input.addEventListener("keydown", (ev) => {
      if (ev.key === "Enter") {
        ev.preventDefault();
        if (i + 1 < list.length) list[i + 1].focus();
        else $("pr-check").click();
      }
    });
  });

  async function submitCurrent(dontKnow) {
    let payload = {
      question_id: q.id,
      elapsed_ms: Date.now() - practiceState.startedAt,
      update_mastery: true,
      dont_know: !!dontKnow,
    };
    if (!dontKnow) {
      if (q.qtype === "fill") {
        const inputs = [...stage.querySelectorAll(".blank-input")];
        const answers = inputs.map((el) => el.value.trim());
        if (!answers.length) {
          const legacy = $("pr-fill");
          if (legacy && legacy.value.trim()) {
            payload.user_answer = legacy.value.trim();
          } else {
            $("pr-feedback").innerHTML = `<div class="feedback-panel pending">请先作答。</div>`;
            return;
          }
        } else if (answers.some((a) => !a)) {
          $("pr-feedback").innerHTML = `<div class="feedback-panel pending">请填完所有空（共 ${answers.length} 空）。</div>`;
          return;
        } else {
          payload.user_answers = answers;
          payload.user_answer = answers.join("；");
        }
      } else {
        const fill = $("pr-fill");
        const answer = fill ? fill.value : selected;
        if (!answer || !String(answer).trim()) {
          $("pr-feedback").innerHTML = `<div class="feedback-panel pending">请先作答。</div>`;
          return;
        }
        payload.user_answer = String(answer);
      }
    }

    $("pr-check").disabled = true;
    const dkBtn = $("pr-dont-know");
    if (dkBtn) dkBtn.disabled = true;
    $("pr-feedback").innerHTML = `<div class="feedback-panel pending">${
      dontKnow ? "已标记不会…" : q.qtype === "short" ? "大模型批改中…" : "判分中…"
    }</div>`;

    let res;
    try {
      res = await api(`/api/practice/sessions/${practiceState.sessionId}/submit`, {
        method: "POST",
        body: JSON.stringify(payload),
      });
    } catch (err) {
      $("pr-check").disabled = false;
      if (dkBtn) dkBtn.disabled = false;
      $("pr-feedback").innerHTML = `<div class="feedback-panel bad">${escapeHtml(
        err.message || "提交失败"
      )}</div>`;
      return;
    }
    $("pr-check").disabled = false;
    if (dkBtn) dkBtn.disabled = false;

    let cls = "pending";
    if (res.is_correct === true) cls = "ok";
    if (res.is_correct === false) cls = "bad";
    practiceState.results[q.id] =
      res.is_correct === true ? "ok" : res.is_correct === false ? "bad" : "pending";
    savePracticeProgress();
    const explainText = formatDetailedExplanation(res.explanation, res.answer_key);
    $("pr-feedback").innerHTML = `
      <div class="feedback-panel ${cls}">
        <strong>${escapeHtml(res.feedback || "")}</strong>
        ${
          res.excluded_from_stats
            ? `<div class="muted" style="margin-top:6px;font-size:0.85rem">本题已报错，不计入正确率</div>`
            : ""
        }
        ${
          res.dont_know
            ? `<div class="muted" style="margin-top:6px;font-size:0.85rem">已按错误计入正确率</div>`
            : ""
        }
        ${
          res.llm_graded ? `<div class="muted" style="margin-top:6px;font-size:0.85rem">已由大模型批改</div>` : ""
        }
        ${
          res.tutorial_revise_hint
            ? `<div style="margin-top:8px">${escapeHtml(
                res.tutorial_revise_hint.message || ""
              )} ${(res.tutorial_revise_hint.knowledge_ids || [])
                .map(
                  (k) =>
                    `<button type="button" class="chip mono" data-open-tutorial="${k}">学 ${k}</button>`
                )
                .join(" ")}</div>`
            : ""
        }
        ${
          res.answer_key
            ? `<div class="answer-key">参考答案：<span id="pr-ans">${escapeHtml(
                res.answer_key
              )}</span></div>`
            : ""
        }
        ${
          explainText
            ? `<div class="answer-explain" id="pr-exp"><span class="label">详细解析</span>${escapeHtml(
                explainText
              ).replace(/\n/g, "<br>")}</div>`
            : ""
        }
      </div>`;
    renderKatex($("pr-feedback"));
    $("pr-feedback").querySelectorAll("[data-open-tutorial]").forEach((btn) => {
      btn.addEventListener("click", () => openTutorial(btn.dataset.openTutorial));
    });
    $("pr-next").disabled = false;
    stage.querySelectorAll(".progress-dots button").forEach((btn, i) => {
      const qid = practiceState.questionIds[i];
      btn.classList.remove("ok", "bad", "pending", "current");
      if (i === practiceState.index) btn.classList.add("current");
      if (practiceState.results[qid]) btn.classList.add(practiceState.results[qid]);
    });
  }

  $("pr-check").onclick = () => submitCurrent(false);
  $("pr-dont-know").onclick = () => submitCurrent(true);
  const reportBtn = $("pr-report");
  if (reportBtn) {
    reportBtn.onclick = async () => {
      if (
        !window.confirm(
          "确认已经反复验算、标准答案仍然错了？系统会立刻按题面重算。若确实出错，本题不计入正确率。"
        )
      ) {
        return;
      }
      reportBtn.disabled = true;
      try {
        const res = await api(
          `/api/practice/sessions/${practiceState.sessionId}/report-error`,
          {
            method: "POST",
            body: JSON.stringify({ question_id: q.id }),
          }
        );
        practiceState.reportNotice = { qid: q.id, detail: res.detail || "" };
        if (res.excluded) {
          delete practiceState.results[q.id];
          savePracticeProgress();
        }
        await showPracticeQuestion(practiceState.index);
      } catch (err) {
        reportBtn.disabled = false;
        $("pr-feedback").innerHTML = `<div class="feedback-panel bad">${escapeHtml(
          err.message || "报错失败"
        )}</div>`;
      }
    };
  }

  $("pr-next").onclick = () => {
    if (practiceState.index + 1 >= practiceState.total) {
      finishPractice();
    } else {
      showPracticeQuestion(practiceState.index + 1);
    }
  };
  $("pr-finish").onclick = () => finishPractice();
}

async function finishPractice() {
  if (!practiceState.sessionId) return;
  unlockAchieveAudio();
  const summary = await api(`/api/practice/sessions/${practiceState.sessionId}/finish`, {
    method: "POST",
  });
  practiceState.finished = true;
  practiceState.summary = summary;
  clearPracticeProgressLocal();
  const ok =
    summary.score_correct != null ? summary.score_correct : summary.correct_count || 0;
  const total =
    summary.score_total != null
      ? summary.score_total
      : summary.total_questions || practiceState.total;
  const voidedN = summary.voided_count || 0;
  const prog = summary.progression || {};
  const items = prog.items || prog.per_knowledge || [];
  const next = prog.next_paper;
  const isAssess = prog.kind === "assessment";
  const reasonMap = {
    pass: "达标晋级",
    below_pass: "未达通过线",
    too_few: "题量不足",
    at_cap: "已达练习上限",
    exempt: "免练（请走考核）",
  };
  const itemHtml = items.length
    ? `<ul style="text-align:left;max-width:560px;margin:12px auto">
        ${items
          .map((it) => {
            const rate =
              it.rate != null ? ` · 正确率 ${Math.round(Number(it.rate) * 100)}%` : "";
            if (it.changed) {
              return `<li><code>${escapeHtml(it.knowledge_id)}</code>：${escapeHtml(
                it.from
              )} → <strong>${escapeHtml(it.to)}</strong>${rate}</li>`;
            }
            if (it.passed === false) {
              return `<li><code>${escapeHtml(it.knowledge_id)}</code>：未通过（需 ≥ ${Math.round(
                (it.need || 0.8) * 100
              )}%）${rate}</li>`;
            }
            if (it.passed === true && !it.changed) {
              return `<li><code>${escapeHtml(it.knowledge_id)}</code>：通过，等级未变（${escapeHtml(
                it.from || it.to || ""
              )}）${rate}</li>`;
            }
            return `<li><code>${escapeHtml(it.knowledge_id)}</code>：${escapeHtml(
              reasonMap[it.reason] || it.reason || "未晋级"
            )}${rate}</li>`;
          })
          .join("")}
      </ul>`
    : `<p class="muted">本场暂无按知识点晋级明细。</p>`;
  const nextHtml = next
    ? `<p class="muted" style="margin-top:12px">已刷新下一考核：<strong>${escapeHtml(
        next.label || next.id
      )}</strong>（${escapeHtml(next.target_level || "")} · ${
        next.question_count || "?"
      } 题）</p>
       <button type="button" class="btn-primary" id="pr-next-paper">去做新考核</button>`
    : "";
  const retiredN = prog.retired_practice_count || 0;
  const listName = isAssess ? "考核列表" : "练习列表";
  const listNote = summary.removed_from_list
    ? `<p class="muted" style="margin-top:8px">本卷已从${listName}移除，记录已写入「做题记录」。</p>`
    : summary.fully_done === false
      ? `<p class="muted" style="margin-top:8px">尚未答完全部题目，本卷仍留在${listName}。</p>`
      : "";
  const retiredNote =
    retiredN > 0
      ? `<p class="muted">因考核通过/超练习上限，已取消 ${retiredN} 份练习卷（不再出现在练习列表）。</p>`
      : "";
  const packs = summary.unknown_followups || (summary.unknown_followup ? [summary.unknown_followup] : []);
  const unknownKids = summary.unknown_knowledge_ids || [];
  let unknownHtml = "";
  if (packs.length) {
    const cards = packs
      .map((pack) => {
        const kid = pack.knowledge_id || (pack.knowledge_ids || [])[0] || "";
        const title = pack.title || kid;
        const consol = pack.consolidation_paper || {};
        return `<div class="unknown-kid-card">
          <strong>${escapeHtml(title)}</strong>
          <div class="muted mono" style="margin:4px 0">${escapeHtml(kid)}</div>
          <div style="display:flex;flex-wrap:wrap;gap:8px;margin-top:8px">
            <button type="button" class="chip" data-open-tutorial="${escapeHtml(kid)}">学本知识点</button>
            ${
              consol.id
                ? `<button type="button" class="chip active" data-consol="${escapeHtml(
                    consol.id
                  )}">${escapeHtml(consol.label || "课后巩固")}</button>`
                : ""
            }
            <button type="button" class="chip" data-pack="${escapeHtml(pack.id)}">打开专学页</button>
          </div>
        </div>`;
      })
      .join("");
    unknownHtml = `
      <div class="unknown-followup-box">
        <h4 style="margin:16px 0 6px;font-family:var(--font-display)">不会专学（按知识点分开）</h4>
        <p class="muted">本场有 ${unknownKids.length || packs.length} 个知识点未达合格线且为 L0，已合并进不会专学。巩固通过线约 ${Math.round(
          (summary.consolidation_pass_rate || 0.75) * 100
        )}%。</p>
        ${cards}
      </div>`;
  } else if (unknownKids.length) {
    unknownHtml = `<p class="muted">有不会标记，但专学包生成失败，请到「学习」页查看。</p>`;
  }
  const stage = practiceStage();
  if (!stage) return;
  stage.innerHTML = `
    <div class="summary-box">
      <p class="stat-label">${isAssess ? "本场考核完成" : "本场练习完成"}</p>
      <p class="stat-value">${ok}<span class="muted" style="font-size:1.2rem"> / ${total}</span></p>
      <p class="muted">计入正确率（报错题已排除）${
        total ? Math.round((ok / total) * 100) : 0
      }%${voidedN ? ` · ${voidedN} 题报错未计入` : ""}</p>
      <p class="muted">练习上限 ${summary.practice_max_level || "L2"}（通过线约 ${Math.round(
        (summary.practice_pass_rate || 0.75) * 100
      )}%）；考核通过线 ${Math.round((summary.assessment_pass_rate || 0.8) * 100)}%</p>
      <h4 style="margin:16px 0 6px;font-family:var(--font-display)">晋级小结</h4>
      ${itemHtml}
      ${listNote}
      ${retiredNote}
      ${unknownHtml}
      ${nextHtml}
      <div class="practice-actions" style="justify-content:center;margin-top:20px">
        <button type="button" class="btn-primary" id="pr-to-list">返回${listName}</button>
        <button type="button" class="btn-ghost" id="pr-to-history">查看做题记录</button>
      </div>
    </div>`;
  $("pr-to-list").onclick = () => {
    switchView(isAssess ? "assessments" : "practice");
  };
  $("pr-to-history").onclick = () => switchView("history");
  const np = $("pr-next-paper");
  if (np && next?.id) {
    np.onclick = () => {
      cache.pendingPracticePaperId = next.id;
      switchView("assessments");
    };
  }
  stage.querySelectorAll("[data-open-tutorial]").forEach((btn) => {
    btn.addEventListener("click", () => openTutorial(btn.dataset.openTutorial));
  });
  stage.querySelectorAll("[data-consol]").forEach((btn) => {
    btn.addEventListener("click", () => {
      cache.pendingPracticePaperId = btn.dataset.consol;
      switchView("practice");
    });
  });
  stage.querySelectorAll("[data-pack]").forEach((btn) => {
    btn.addEventListener("click", () => {
      cache.openFollowupId = btn.dataset.pack;
      switchView("learn", { pack: btn.dataset.pack });
    });
  });
  showAchievementCelebration(summary.new_achievements || []);
}

async function renderHistory() {
  const [sessions, attempts] = await Promise.all([
    api("/api/practice/sessions?limit=20"),
    api("/api/practice/history?limit=40"),
  ]);
  $("view-history").innerHTML = `
    <div class="grid">
      <div class="card span-5">
        <h3 style="margin-top:0;font-family:var(--font-display)">练习会话</h3>
        <table>
          <thead><tr><th>时间</th><th>试卷</th><th>正确</th><th>状态</th></tr></thead>
          <tbody>
            ${
              sessions.length
                ? sessions
                    .map(
                      (s) => `<tr>
                      <td>${(s.started_at || "").replace("T", " ")}</td>
                      <td>${escapeHtml(s.kind_label || "")} · ${escapeHtml(
                        s.theme || s.paper_id || "—"
                      )}</td>
                      <td>${s.correct_count}/${s.total_questions}</td>
                      <td>${s.status === "completed" ? "已完成" : s.status || "—"}</td>
                    </tr>`
                    )
                    .join("")
                : `<tr><td colspan="4" class="muted">暂无会话</td></tr>`
            }
          </tbody>
        </table>
      </div>
      <div class="card span-7">
        <h3 style="margin-top:0;font-family:var(--font-display)">答题尝试</h3>
        <table>
          <thead><tr><th>时间</th><th>题目</th><th>你的答案</th><th>结果</th></tr></thead>
          <tbody>
            ${
              attempts.length
                ? attempts
                    .map(
                      (a) => `<tr>
                      <td>${(a.created_at || "").replace("T", " ")}</td>
                      <td><div>${escapeHtml((a.stem || a.question_id || "").slice(0, 60))}</div>
                        <div class="mono muted">${a.question_id}</div></td>
                      <td>${escapeHtml(a.user_answer || "")}</td>
                      <td>${
                        a.is_correct === 1
                          ? '<span class="level-pill">正确</span>'
                          : a.is_correct === 0
                            ? '<span class="chip">错误</span>'
                            : '<span class="muted">待评</span>'
                      }</td>
                    </tr>`
                    )
                    .join("")
                : `<tr><td colspan="4" class="muted">暂无记录</td></tr>`
            }
          </tbody>
        </table>
      </div>
    </div>`;
}

let achieveAudioCtx = null;

function unlockAchieveAudio() {
  const AC = window.AudioContext || window.webkitAudioContext;
  if (!AC) return null;
  if (!achieveAudioCtx) achieveAudioCtx = new AC();
  if (achieveAudioCtx.state === "suspended") {
    achieveAudioCtx.resume();
  }
  return achieveAudioCtx;
}

function playAchieveChime() {
  const ctx = unlockAchieveAudio();
  if (!ctx) return;
  const now = ctx.currentTime;
  [523.25, 659.25, 783.99].forEach((freq, i) => {
    const osc = ctx.createOscillator();
    const gain = ctx.createGain();
    osc.type = "sine";
    osc.frequency.value = freq;
    gain.gain.setValueAtTime(0.0001, now);
    gain.gain.exponentialRampToValueAtTime(0.12, now + 0.02 + i * 0.09);
    gain.gain.exponentialRampToValueAtTime(0.0001, now + 0.5 + i * 0.09);
    osc.connect(gain);
    gain.connect(ctx.destination);
    osc.start(now + i * 0.09);
    osc.stop(now + 0.65 + i * 0.09);
  });
}

function showAchievementCelebration(items) {
  const list = (items || []).filter((x) => x && x.title);
  if (!list.length) return;
  playAchieveChime();
  let idx = 0;
  const host = document.createElement("div");
  host.className = "achieve-overlay";
  document.body.appendChild(host);

  function renderOne() {
    const it = list[idx];
    host.innerHTML = `
      <div class="achieve-card">
        <p class="stat-label">成就解锁</p>
        <h2>${escapeHtml(it.title)}</h2>
        <p>${escapeHtml(it.detail || "")}</p>
        <p class="muted">${idx + 1} / ${list.length}</p>
        <button type="button" class="btn-primary" id="achieve-next">${
          idx + 1 < list.length ? "下一项" : "继续学习"
        }</button>
      </div>`;
    const btn = host.querySelector("#achieve-next");
    if (btn) {
      btn.onclick = () => {
        idx += 1;
        if (idx < list.length) {
          playAchieveChime();
          renderOne();
        } else {
          host.remove();
        }
      };
    }
  }
  renderOne();
}

function bindEditForm() {
  const form = $("edit-form");
  if (!form || form.dataset.bound === "1") return;
  form.dataset.bound = "1";
  form.addEventListener("submit", async (e) => {
    const submitter = e.submitter;
    if (!submitter || submitter.value !== "save") {
      editingKid = null;
      return;
    }
    e.preventDefault();
    if (!editingKid) return;
    await api(`/api/mastery/${encodeURIComponent(editingKid)}`, {
      method: "PATCH",
      body: JSON.stringify({
        level: $("edit-level").value,
        wrong_count: Number($("edit-wrong").value || 0),
        notes: $("edit-notes").value,
        write_back_yaml: true,
      }),
    });
    $("edit-dialog").close();
    editingKid = null;
    renderMastery();
  });
}

export {
  titles,
  navItems,
  viewLoaders,
  cache,
  api,
  switchView,
  bindEditForm,
};
