const titles = {
  overview: ["总览", "掌握度分布、点阵与薄弱点"],
  practice: ["练习", "选卷作答，即时反馈（可汗风格）"],
  history: ["做题记录", "永久保存的答题尝试与会话"],
  knowledge: ["知识点", "拓扑点阵、熟练度与教程"],
  learn: ["学习", "打开知识点教程讲义"],
  mastery: ["掌握度", "筛选并编辑 L0–L4"],
  assessments: ["考核", "浏览摸底卷与练习卷"],
  plan: ["周计划", "查看或编辑 plans/current-week.md"],
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
let cache = { overview: null, subjects: [], preferredKnowledgeSubject: null, pendingTutorial: null };

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

function levelBar(bucket) {
  const total = bucket.total || 1;
  return `
    <div class="bars" title="L0-L4">
      <span class="l0" style="width:${(100 * bucket.L0) / total}%"></span>
      <span class="l1" style="width:${(100 * bucket.L1) / total}%"></span>
      <span class="l2" style="width:${(100 * bucket.L2) / total}%"></span>
      <span class="l3" style="width:${(100 * bucket.L3) / total}%"></span>
      <span class="l4" style="width:${(100 * bucket.L4) / total}%"></span>
    </div>
    <p class="muted" style="margin:8px 0 0;font-size:0.8rem">
      L0 ${bucket.L0} · L1 ${bucket.L1} · L2 ${bucket.L2} · L3 ${bucket.L3} · L4 ${bucket.L4}
    </p>`;
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

  $("view-overview").innerHTML = `
    <div class="grid">
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

function formatTutorialHtml(md) {
  let text = String(md || "");
  const parts = text.split(/\n##\s*自测参考\s*\n/);
  const main = parts[0];
  const answers = parts[1];

  function renderChunk(chunk) {
    let t = escapeHtml(chunk);
    t = t.replace(/^###\s+(.+)$/gm, "<h4>$1</h4>");
    t = t.replace(/^##\s+(.+)$/gm, "<h3>$1</h3>");
    t = t.replace(/^#\s+(.+)$/gm, "<h2>$1</h2>");
    t = t.replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>");
    t = t.replace(/`([^`]+)`/g, '<code class="mono">$1</code>');
    t = t.replace(/^\s*-\s+(.+)$/gm, "<li>$1</li>");
    t = t.replace(/(?:<li>.*?<\/li>\n?)+/gs, (block) => `<ul>${block}</ul>`);
    t = t.replace(/\n{2,}/g, "<br><br>").replace(/\n/g, "<br>");
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

async function renderLearn() {
  const root = $("view-learn");
  const list = await api("/api/tutorials?subject=chemistry").catch(() => []);
  root.innerHTML = `
    <div class="grid">
      <div class="card span-4">
        <h3 style="margin-top:0">已有教程</h3>
        <p class="muted">点左侧也可从「知识点」进入；缺失时会自动生成。</p>
        <div class="paper-pick" id="learn-list">
          ${
            list.length
              ? list
                  .map(
                    (t) => `<button type="button" class="paper-card" data-kid="${t.knowledge_id}">
                    <strong>${escapeHtml(t.title || t.name)}</strong>
                    <div class="muted">${subjectNames[t.subject_id] || t.subject_id} · ${t.target_level || ""}</div>
                    <div class="mono muted" style="margin-top:6px">${t.knowledge_id}</div>
                  </button>`
                  )
                  .join("")
              : `<p class="muted">暂无教程。到知识点页点「学一学」自动生成。</p>`
          }
        </div>
      </div>
      <div class="card span-8" id="learn-stage">
        <div class="summary-box">
          <p class="stat-label">选择教程开始学习</p>
          <p class="muted">教程按知识点建模，保存在 knowledge/&lt;科目&gt;/tutorials/。</p>
        </div>
      </div>
    </div>`;
  $("learn-list")?.querySelectorAll("[data-kid]").forEach((btn) => {
    btn.addEventListener("click", () => {
      $("learn-list").querySelectorAll(".paper-card").forEach((el) => el.classList.remove("active"));
      btn.classList.add("active");
      openTutorial(btn.dataset.kid, { mountId: "learn-stage" });
    });
  });
}

async function renderKnowledge() {
  const root = $("view-knowledge");
  const preferred = cache.preferredKnowledgeSubject || "chemistry";
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
          <thead><tr><th>名称</th><th>深度</th><th>等级</th><th>考频</th><th>教程</th><th></th></tr></thead>
          <tbody>
            ${list
              .map(
                (k) => `<tr>
                <td><div>${escapeHtml(k.name)}</div><div class="mono muted">${escapeHtml(k.id)}</div></td>
                <td class="mono">${k.topo_depth ?? 0}</td>
                <td><span class="level-pill">${k.level || "—"}</span></td>
                <td class="weight-${k.exam_weight}">${k.exam_weight}</td>
                <td>${k.has_tutorial ? "有" : "无"}</td>
                <td><button type="button" class="chip" data-open-tutorial="${escapeHtml(k.id)}">学一学</button></td>
              </tr>`
              )
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
        (a) => `<div class="assess-item" data-id="${a.id}">
          <div>
            <strong>${a.theme || a.id}</strong>
            <div class="muted">${subjectNames[a.subject_id] || a.subject_id || "—"} · ${a.date || "无日期"} · ${a.status}</div>
            <div class="mono muted" style="margin-top:6px">${(a.knowledge_ids || []).slice(0, 4).join(", ")}${(a.knowledge_ids || []).length > 4 ? "…" : ""}</div>
          </div>
          <div style="display:flex;flex-direction:column;gap:8px;align-items:flex-end">
            <span class="chip">${a.target_level || "—"}</span>
            <button type="button" class="chip active" data-practice="${a.id}">去练习</button>
          </div>
        </div>`
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
  $("view-plan").innerHTML = `
    <div class="toolbar">
      <button type="button" class="btn-primary" id="plan-save" style="width:auto">保存计划</button>
      <span class="muted">更新于 ${data.updated_at || "—"}</span>
    </div>
    <div class="card">
      <textarea class="plan-editor" id="plan-editor">${escapeHtml(data.content_md || "")}</textarea>
    </div>`;
  $("plan-save").onclick = async () => {
    await api("/api/plan", {
      method: "PUT",
      body: JSON.stringify({ content_md: $("plan-editor").value, write_back_file: true }),
    });
    alert("周计划已保存并回写文件");
    renderPlan();
  };
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
  const papers = await api("/api/practice/papers");
  practiceState.papers = papers;
  const root = $("view-practice");
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
        <div class="paper-pick" id="pr-papers"></div>
      </div>
      <div class="practice-stage" id="pr-stage">
        <div class="summary-box">
          <p class="stat-label">选择左侧试卷开始练习</p>
          <p class="muted">一题一题作答，提交后立即看对错与解析；填空可在题干空格处分别填写。记录永久保存在 SQLite 与 practice/attempts。</p>
          <p class="muted" id="pr-llm-hint" style="margin-top:8px"></p>
        </div>
      </div>
    </div>`;

  async function refreshPapers() {
    const subject = $("pr-subject").value;
    const params = subject ? `?subject=${encodeURIComponent(subject)}` : "";
    const list = await api(`/api/practice/papers${params}`);
    practiceState.papers = list;
    $("pr-papers").innerHTML = list.length
      ? list
          .map(
            (p) => `<button type="button" class="paper-card" data-id="${p.id}">
            <strong>${p.theme || p.id}</strong>
            <div class="muted">${subjectNames[p.subject_id] || p.subject_id || "—"} · ${p.date || ""} · ${p.question_count} 题</div>
            <div class="mono muted" style="margin-top:6px">${p.id}</div>
          </button>`
          )
          .join("")
      : `<p class="muted">暂无试卷。请先同步考核 Markdown。</p>`;
    $("pr-papers").querySelectorAll(".paper-card").forEach((btn) => {
      btn.addEventListener("click", () => startPractice(btn.dataset.id));
    });
  }

  $("pr-subject").onchange = refreshPapers;
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
}

async function startPractice(paperId) {
  const session = await api("/api/practice/sessions", {
    method: "POST",
    body: JSON.stringify({ paper_id: paperId }),
  });
  practiceState.sessionId = session.session_id;
  practiceState.paperId = paperId;
  practiceState.index = 0;
  practiceState.total = session.total_questions;
  practiceState.questionIds = session.questions.map((q) => q.id);
  practiceState.results = {};
  practiceState.startedAt = Date.now();
  practiceState.finished = false;
  practiceState.summary = null;
  document.querySelectorAll(".paper-card").forEach((el) => {
    el.classList.toggle("active", el.dataset.id === paperId);
  });
  await showPracticeQuestion(0);
}

async function showPracticeQuestion(index) {
  if (practiceState.finished) return;
  const data = await api(
    `/api/practice/sessions/${practiceState.sessionId}/question?index=${index}`
  );
  practiceState.index = data.index;
  practiceState.total = data.total;
  practiceState.startedAt = Date.now();
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

  $("pr-check").onclick = async () => {
    let payload = {
      question_id: q.id,
      elapsed_ms: Date.now() - practiceState.startedAt,
      update_mastery: true,
    };
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

    $("pr-check").disabled = true;
    $("pr-feedback").innerHTML = `<div class="feedback-panel pending">${
      q.qtype === "short" ? "大模型批改中…" : "判分中…"
    }</div>`;

    let res;
    try {
      res = await api(`/api/practice/sessions/${practiceState.sessionId}/submit`, {
        method: "POST",
        body: JSON.stringify(payload),
      });
    } catch (err) {
      $("pr-check").disabled = false;
      $("pr-feedback").innerHTML = `<div class="feedback-panel bad">${escapeHtml(
        err.message || "提交失败"
      )}</div>`;
      return;
    }
    $("pr-check").disabled = false;

    let cls = "pending";
    if (res.is_correct === true) cls = "ok";
    if (res.is_correct === false) cls = "bad";
    practiceState.results[q.id] =
      res.is_correct === true ? "ok" : res.is_correct === false ? "bad" : "pending";
    $("pr-feedback").innerHTML = `
      <div class="feedback-panel ${cls}">
        <strong>${escapeHtml(res.feedback || "")}</strong>
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
                    `<button type="button" class="chip mono" data-open-tutorial="${k}">修订 ${k}</button>`
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
  };

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
  const ok = summary.correct_count || 0;
  const total = summary.total_questions || practiceState.total;
  $("pr-stage").innerHTML = `
    <div class="summary-box">
      <p class="stat-label">本场练习完成</p>
      <p class="stat-value">${ok}<span class="muted" style="font-size:1.2rem"> / ${total}</span></p>
      <p class="muted">首次答对计入正确数；全部尝试已写入做题记录。</p>
      <div class="practice-actions" style="justify-content:center;margin-top:20px">
        <button type="button" class="btn-primary" id="pr-again">再练一次</button>
        <button type="button" class="btn-ghost" id="pr-to-history">查看记录</button>
      </div>
    </div>`;
  $("pr-again").onclick = () => startPractice(practiceState.paperId);
  $("pr-to-history").onclick = () => switchView("history");
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
                      <td>${s.theme || s.paper_id || "—"}</td>
                      <td>${s.correct_count}/${s.total_questions}</td>
                      <td>${s.status}</td>
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
