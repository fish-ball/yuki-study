const titles = {
  overview: ["总览", "掌握度分布、点阵与薄弱点"],
  practice: ["练习", "练习上限与考核晋级（见卷面标题）"],
  basics: ["基础", "原文表格（周期表 / 九下诗文）"],
  history: ["做题记录", "永久保存的答题尝试与会话"],
  knowledge: ["知识点", "点阵、教程与晋级说明"],
  learn: ["学习", "教程讲义 · 关联修订"],
  mastery: ["掌握度", "筛选并编辑 L0–L4"],
  assessments: ["考核", "浏览摸底卷与练习卷"],
  plan: ["学习计划", "周计划 · 日计划 · 进度与负荷问卷"],
  profile: ["档案", "学生信息与佛山 2027 考策"],
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
};

async function api(path, options) {
  const res = await fetch(path, {
    headers: { "Content-Type": "application/json" },
    ...options,
  });
  if (!res.ok) {
    const text = await res.text();
    throw new Error(text || res.statusText);
  }
  if (res.status === 204) return null;
  return res.json();
}

function $(id) {
  return document.getElementById(id);
}

function switchView(name) {
  document.querySelectorAll(".nav-item").forEach((btn) => {
    btn.classList.toggle("active", btn.dataset.view === name);
  });
  document.querySelectorAll(".view").forEach((el) => {
    el.classList.toggle("active", el.id === `view-${name}`);
  });
  const [title, desc] = titles[name];
  $("view-title").textContent = title;
  $("view-desc").textContent = desc;
  const loaders = {
    overview: renderOverview,
    practice: renderPractice,
    basics: renderBasics,
    history: renderHistory,
    knowledge: renderKnowledge,
    learn: renderLearn,
    mastery: renderMastery,
    assessments: renderAssessments,
    plan: renderPlan,
    profile: renderProfile,
  };
  loaders[name]();
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
  $("sync-status").textContent = data.last_sync ? `已同步 ${data.last_sync}` : "";

  $("view-overview").querySelectorAll("[data-goto-knowledge]").forEach((el) => {
    el.addEventListener("click", (e) => {
      e.stopPropagation();
      const sid = el.dataset.gotoKnowledge;
      cache.preferredKnowledgeSubject = sid;
      switchView("knowledge");
    });
  });
  $("view-overview").querySelectorAll(".lattice-dot").forEach((btn) => {
    btn.addEventListener("click", (e) => {
      e.stopPropagation();
      const sid =
        btn.closest("[data-goto-knowledge]")?.dataset.gotoKnowledge ||
        cache.preferredKnowledgeSubject;
      if (sid) cache.preferredKnowledgeSubject = sid;
      switchView("knowledge");
      // 知识点页加载后会再绑教程；此处先记下待打开
      cache.pendingTutorial = btn.dataset.openTutorial;
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
  const stage = options.mountId ? $(options.mountId) : null;
  const target = stage || $("view-learn");
  if (!options.mountId) {
    switchView("learn");
  }
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
  if (back) back.onclick = () => switchView("knowledge");
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
  root.innerHTML = `
    <div class="grid">
      <div class="card span-12">
        <div class="mastery-stats compact">
          <p style="margin:0"><strong>学习 → 练习 → 考核</strong>：先学教程，再用练习冲到
          <span class="level-pill">${pmax}</span>（通过线约 ${ppass}%）；
          冲 L3/L4 必须走考核（通过线 <strong>${apass}%</strong>）。
          做题点「不会」后，会按知识点分别生成专学页与巩固练习。</p>
        </div>
      </div>
      <div class="card span-12" id="learn-unknown-wrap">
        <h3 style="margin-top:0">不会专学</h3>
        <p class="muted" style="margin-top:0">练习中标记「不会」的知识点专页（按错计分后生成）。</p>
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
        <p class="muted" style="font-size:0.8rem;margin-top:12px">没有教程？到「知识点」点「学一学」生成。</p>
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
    const kids = pack.knowledge_ids || (kid ? [kid] : []);
    // 旧包若含多个知识点：提示拆开看，但仍只展示本包教程
    const multiHint =
      kids.length > 1
        ? `<p class="muted">旧版合并包含多个知识点；请优先使用「每个知识点一份」的新专学。</p>`
        : "";
    $("learn-stage").innerHTML = `
      <div class="tutorial-panel">
        <h2 style="margin-top:0;font-family:var(--font-display)">${escapeHtml(
          pack.title || "不会专学"
        )}</h2>
        <p class="muted">单知识点专学 · ${(pack.created_at || "").replace("T", " ")}</p>
        <p class="mono muted">${escapeHtml(kid)}</p>
        ${multiHint}
        <h4 style="font-family:var(--font-display)">先学教程</h4>
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
        <h4 style="font-family:var(--font-display)">本知识点课后巩固</h4>
        <p class="muted">${escapeHtml(consol.label || "巩固卷")}</p>
        ${
          consol.id
            ? `<button type="button" class="btn-primary" id="learn-do-consol">开始巩固（${
                consol.question_count || "?"
              } 题）</button>`
            : `<p class="muted">暂无巩固卷</p>`
        }
      </div>`;
    $("learn-stage").querySelectorAll("[data-open-tutorial]").forEach((btn) => {
      btn.addEventListener("click", () =>
        openTutorial(btn.dataset.openTutorial, { mountId: "learn-unknown-tutorial" })
      );
    });
    const doBtn = $("learn-do-consol");
    if (doBtn && consol.id) {
      doBtn.onclick = () => {
        switchView("practice");
        setTimeout(() => startPractice(consol.id), 80);
      };
    }
    if (tuts[0]?.knowledge_id) {
      openTutorial(tuts[0].knowledge_id, { mountId: "learn-unknown-tutorial" });
    } else if (kid) {
      openTutorial(kid, { mountId: "learn-unknown-tutorial" });
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
    unkList.innerHTML = `<p class="muted">暂无。做题时点「不会」，结束练习后会按知识点分别出现在这里。</p>`;
  } else {
    unkList.innerHTML = sortedFu
      .map((f) => {
        const kid = f.knowledge_id || (f.knowledge_ids || [])[0] || "";
        const multi = (f.knowledge_ids || []).length > 1;
        return `<button type="button" class="paper-card" data-pack="${escapeHtml(f.id)}">
          <strong>${escapeHtml(f.title || kid || f.id)}</strong>
          <div class="muted">${multi ? "旧合并包 · " : "单知识点 · "}${escapeHtml(
            kid
          )} · ${f.consolidation_paper?.question_count || 0} 道巩固题</div>
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
    const url = subject ? `/api/tutorials?subject=${encodeURIComponent(subject)}` : "/api/tutorials";
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

  if (cache.openFollowupId) {
    const id = cache.openFollowupId;
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
  }
}

async function renderKnowledge() {
  const root = $("view-knowledge");
  const preferred = cache.preferredKnowledgeSubject || "chemistry";
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

    if (cache.pendingTutorial) {
      const kid = cache.pendingTutorial;
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
  $("kn-subject").onchange = load;
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
  const policyWrap = await api("/api/mastery-policy").catch(() => null);
  const apass = Math.round((policyWrap?.assessment_pass_rate || 0.8) * 100);
  const pmax = policyWrap?.practice_max_level || "L2";
  root.innerHTML = `
    <div class="toolbar">
      <label>科目
        <select id="as-subject">
          <option value="">全部</option>
          ${subjectOptions("")}
        </select>
      </label>
      <button type="button" class="chip" id="as-reload">刷新</button>
    </div>
    <p class="muted" style="margin:0 0 12px">练习上限 ${pmax}；考核通过线 ${apass}%（按知识点统计首次作答正确率）。完成后自动刷新更高难度去重新卷；已达 L4 不再刷新。</p>
    <div class="grid">
      <div class="card span-5"><div id="as-list" class="assess-list"></div></div>
      <div class="card span-7"><h3 style="margin-top:0" id="as-title">选择一份考核</h3><div id="as-body" class="markdown-box muted">左侧点选后显示正文</div></div>
    </div>`;

  async function load() {
    const params = new URLSearchParams();
    if ($("as-subject").value) params.set("subject", $("as-subject").value);
    const list = await api(`/api/assessments?${params}`);
    if (!list.length) {
      $("as-list").innerHTML = `<p class="muted">暂无考核。可用对话生成后点「从仓库同步」。</p>`;
      return;
    }
    $("as-list").innerHTML = list
      .map(
        (a) => {
          const isDrill = String(a.id || "").startsWith("drill-") || (a.theme || "").includes("drill");
          const kindLabel = isDrill ? "练习" : "考核";
          const passHint = isDrill ? "" : ` · 通过线 ${apass}%`;
          const title = a.note || a.theme || a.id;
          return `<div class="assess-item" data-id="${a.id}">
          <div>
            <strong>${escapeHtml(title)}</strong>
            <div class="muted">${subjectNames[a.subject_id] || a.subject_id || "—"} · ${kindLabel}${passHint} · ${a.date || "无日期"} · ${a.status}</div>
            <div class="mono muted" style="margin-top:6px">${(a.knowledge_ids || []).slice(0, 4).join(", ")}${(a.knowledge_ids || []).length > 4 ? "…" : ""}</div>
          </div>
          <div style="display:flex;flex-direction:column;gap:8px;align-items:flex-end">
            <span class="chip">${a.target_level || "—"}</span>
            <button type="button" class="chip active" data-practice="${a.id}">去${kindLabel}</button>
          </div>
        </div>`;
        }
      )
      .join("");
    $("as-list").querySelectorAll(".assess-item").forEach((el) => {
      el.addEventListener("click", async (ev) => {
        if (ev.target.closest("[data-practice]")) return;
        const detail = await api(`/api/assessments/${encodeURIComponent(el.dataset.id)}`);
        $("as-title").textContent = detail.theme || detail.id;
        $("as-body").classList.remove("muted");
        $("as-body").textContent = detail.content_md || "";
      });
    });
    $("as-list").querySelectorAll("[data-practice]").forEach((btn) => {
      btn.addEventListener("click", (ev) => {
        ev.stopPropagation();
        switchView("practice");
        setTimeout(() => startPractice(btn.dataset.practice), 50);
      });
    });
  }

  $("as-subject").onchange = load;
  $("as-reload").onclick = load;
  await load();
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
                        <span class="chip">${it.done ? "练习已完成" : "待练习"}</span>
                      </div>
                    </div>
                  </div>`
                )
                .join("")
            : `<p class="muted">这一天还没有知识点条目。</p>`
        }
      </div>
      ${
        isToday && d.status !== "completed"
          ? `<p class="muted" style="margin-top:16px">请到「练习」页完成对应知识点套题；全部做完后回到这里回答负荷反馈。</p>`
          : ""
      }`;
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
    </div>`;
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

function renderKatex(root) {
  if (!window.renderMathInElement || !root) return;
  window.renderMathInElement(root, {
    delimiters: [
      { left: "$$", right: "$$", display: true },
      { left: "$", right: "$", display: false },
    ],
    throwOnError: false,
  });
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
};

async function renderPractice() {
  const [papers, policyWrap, actives] = await Promise.all([
    api("/api/practice/papers"),
    api("/api/mastery-policy").catch(() => null),
    api("/api/practice/sessions/active").catch(() => []),
  ]);
  practiceState.papers = papers;
  const root = $("view-practice");
  const pmax = policyWrap?.practice_max_level || "L2";
  const apass = Math.round((policyWrap?.assessment_pass_rate || 0.8) * 100);
  const ppass = Math.round((policyWrap?.practice_pass_rate || 0.75) * 100);
  const activeByPaper = {};
  (actives || []).forEach((a) => {
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
          <label>类型
            <select id="pr-kind">
              <option value="">全部</option>
              <option value="practice">练习</option>
              <option value="assessment">考核</option>
            </select>
          </label>
        </div>
        <p class="muted" style="font-size:0.85rem;margin:0 0 10px">
          练习上限 <strong>${pmax}</strong>（通过线约 ${ppass}%）；
          考核通过线 <strong>${apass}%</strong>。进度自动留档，刷新/退出后可继续。
        </p>
        <div id="pr-active-banner"></div>
        <div class="paper-pick" id="pr-papers"></div>
      </div>
      <div class="practice-stage" id="pr-stage">
        <div class="summary-box">
          <p class="stat-label">选择左侧试卷开始</p>
          <p class="muted">卷面标题已标注可提升等级 / 通过线。答完后有晋级小结；已完成或免练卷会从列表消失，记录留在「做题记录」。</p>
          <p class="muted" id="pr-llm-hint" style="margin-top:8px"></p>
        </div>
      </div>
    </div>`;

  async function refreshPapers() {
    const subject = $("pr-subject").value;
    const kind = $("pr-kind").value;
    const params = subject ? `?subject=${encodeURIComponent(subject)}` : "";
    let list = await api(`/api/practice/papers${params}`);
    if (kind) list = list.filter((p) => p.paper_kind === kind);
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
            <div class="muted">${subjectNames[p.subject_id] || p.subject_id || "—"} · ${
              p.paper_kind === "practice" ? "练习" : "考核"
            } · ${p.date || ""} · ${p.question_count} 题${
              p.target_level ? ` · 目标 ${p.target_level}` : ""
            }${p.exempt ? " · 免练" : ""}${prog}</div>
            <div class="mono muted" style="margin-top:6px">${p.id}</div>
          </button>`;
          })
          .join("")
      : `<p class="muted">暂无试卷。请先同步考核 Markdown。</p>`;
    $("pr-papers").querySelectorAll(".paper-card").forEach((btn) => {
      btn.addEventListener("click", () => startPractice(btn.dataset.id));
    });
  }

  $("pr-subject").onchange = refreshPapers;
  $("pr-kind").onchange = refreshPapers;
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

  await restoreActivePractice(actives || []);
}

async function restoreActivePractice(actives) {
  // 若本页已有进行中会话挂载，不重复打断
  if (practiceState.sessionId && !practiceState.finished && $("pr-check")) {
    savePracticeProgress();
    return;
  }
  let target = null;
  const local = loadPracticeProgressLocal();
  const list = actives && actives.length ? actives : await api("/api/practice/sessions/active").catch(() => []);
  if (local?.sessionId) {
    target = (list || []).find((a) => a.session_id === local.sessionId) || null;
  }
  if (!target && list?.length) {
    target = list[0];
  }
  const banner = $("pr-active-banner");
  if (!target) {
    clearPracticeProgressLocal();
    if (banner) banner.innerHTML = "";
    return;
  }
  if (banner) {
    banner.innerHTML = `
      <div class="active-progress-banner">
        <span>未完成：已作答 <strong>${target.answered_count || 0}/${
          target.total_questions
        }</strong>，可继续第 ${(target.resume_index || 0) + 1} 题</span>
        <button type="button" class="chip active" id="pr-continue-btn">继续练习</button>
      </div>`;
    $("pr-continue-btn").onclick = () => startPractice(target.paper_id);
  }
  // 自动恢复到做题区（刷新后直接接着做）
  applySessionToState(target);
  document.querySelectorAll(".paper-card").forEach((el) => {
    el.classList.toggle("active", el.dataset.id === target.paper_id);
  });
  await showPracticeQuestion(practiceState.index);
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
  const forceNew = !!options.forceNew;
  const session = await api("/api/practice/sessions", {
    method: "POST",
    body: JSON.stringify({ paper_id: paperId, force_new: forceNew }),
  });
  applySessionToState(session);
  document.querySelectorAll(".paper-card").forEach((el) => {
    el.classList.toggle("active", el.dataset.id === paperId);
  });
  await showPracticeQuestion(practiceState.index);
  if (session.resumed) {
    const stage = $("pr-stage");
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
  const stage = $("pr-stage");
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
    <div id="pr-feedback"></div>
    <div class="practice-actions">
      <button type="button" class="btn-primary" id="pr-check">检查答案</button>
      <button type="button" class="btn-ghost btn-dont-know" id="pr-dont-know">不会</button>
      <button type="button" class="btn-ghost" id="pr-next" disabled>下一题</button>
      <button type="button" class="btn-ghost" id="pr-finish">结束并查看总结</button>
    </div>`;

  renderKatex(stage);

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
    $("pr-feedback").innerHTML = `
      <div class="feedback-panel ${cls}">
        <strong>${escapeHtml(res.feedback || "")}</strong>
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
            ? `<div style="margin-top:8px">参考答案：<span id="pr-ans">${escapeHtml(
                res.answer_key
              )}</span></div>`
            : ""
        }
        ${
          res.explanation
            ? `<div style="margin-top:8px" id="pr-exp">${escapeHtml(res.explanation).replace(
                /\n/g,
                "<br>"
              )}</div>`
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
  const summary = await api(`/api/practice/sessions/${practiceState.sessionId}/finish`, {
    method: "POST",
  });
  practiceState.finished = true;
  practiceState.summary = summary;
  clearPracticeProgressLocal();
  const ok = summary.correct_count || 0;
  const total = summary.total_questions || practiceState.total;
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
  const listNote = summary.removed_from_list
    ? `<p class="muted" style="margin-top:8px">本卷已从练习列表移除，记录已写入「做题记录」。</p>`
    : summary.fully_done === false
      ? `<p class="muted" style="margin-top:8px">尚未答完全部题目，本卷仍留在练习列表。</p>`
      : "";
  const retiredNote =
    retiredN > 0
      ? `<p class="muted">因考核通过/超练习上限，已取消 ${retiredN} 份练习卷（不再出现在列表）。</p>`
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
        <p class="muted">本场标记「不会」共 ${unknownKids.length || packs.length} 个知识点，请逐个学习并做对应巩固。</p>
        ${cards}
      </div>`;
  } else if (unknownKids.length) {
    unknownHtml = `<p class="muted">有不会标记，但专学包生成失败，请到「学习」页查看。</p>`;
  }
  $("pr-stage").innerHTML = `
    <div class="summary-box">
      <p class="stat-label">${isAssess ? "本场考核完成" : "本场练习完成"}</p>
      <p class="stat-value">${ok}<span class="muted" style="font-size:1.2rem"> / ${total}</span></p>
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
        <button type="button" class="btn-primary" id="pr-to-list">返回练习列表</button>
        <button type="button" class="btn-ghost" id="pr-to-history">查看做题记录</button>
      </div>
    </div>`;
  $("pr-to-list").onclick = () => {
    switchView("practice");
  };
  $("pr-to-history").onclick = () => switchView("history");
  const np = $("pr-next-paper");
  if (np && next?.id) {
    np.onclick = () => startPractice(next.id);
  }
  $("pr-stage").querySelectorAll("[data-open-tutorial]").forEach((btn) => {
    btn.addEventListener("click", () => openTutorial(btn.dataset.openTutorial));
  });
  $("pr-stage").querySelectorAll("[data-consol]").forEach((btn) => {
    btn.addEventListener("click", () => startPractice(btn.dataset.consol));
  });
  $("pr-stage").querySelectorAll("[data-pack]").forEach((btn) => {
    btn.addEventListener("click", () => {
      cache.openFollowupId = btn.dataset.pack;
      switchView("learn");
    });
  });
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

document.querySelectorAll(".nav-item").forEach((btn) => {
  btn.addEventListener("click", () => switchView(btn.dataset.view));
});

$("btn-sync").addEventListener("click", async () => {
  $("sync-status").textContent = "同步中…";
  try {
    const stats = await api("/api/sync", { method: "POST" });
    $("sync-status").textContent = `同步完成：题 ${stats.questions ?? 0}，记录 ${stats.attempts ?? 0}`;
    const active = document.querySelector(".nav-item.active")?.dataset.view || "overview";
    switchView(active);
  } catch (err) {
    $("sync-status").textContent = `同步失败：${err.message}`;
  }
});

$("edit-form").addEventListener("submit", async (e) => {
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

// 启动
api("/api/subjects")
  .then((subjects) => {
    cache.subjects = subjects;
    switchView("overview");
  })
  .catch((err) => {
    $("view-overview").innerHTML = `<div class="card"><p>无法连接后端：${err.message}</p></div>`;
  });
