<script setup>
import { computed, onMounted } from "vue";
import { useRoute } from "vue-router";
import { navItems, titles, api, viewLoaders, bindEditForm } from "./lib/pages.js";
import { uiState } from "./lib/ui-state.js";

const route = useRoute();

const viewTitle = computed(() => {
  const name = route.name || "overview";
  return (titles[name] && titles[name][0]) || "";
});

const viewDesc = computed(() => {
  const name = route.name || "overview";
  return (titles[name] && titles[name][1]) || "";
});

async function doSync() {
  uiState.syncStatus = "同步中…";
  try {
    const stats = await api("/api/sync", { method: "POST" });
    uiState.syncStatus = `同步完成：题 ${stats.questions ?? 0}，记录 ${stats.attempts ?? 0}`;
    const name = route.name || "overview";
    if (viewLoaders[name]) viewLoaders[name]();
  } catch (err) {
    uiState.syncStatus = `同步失败：${err.message}`;
  }
}

onMounted(() => {
  bindEditForm();
});
</script>

<template>
  <div class="app-shell">
    <aside class="sidebar">
      <div class="brand">
        <p class="brand-mark">Yuki Study</p>
        <p class="brand-sub">佛山顺德 · 2027 中考</p>
      </div>
      <nav class="nav">
        <template v-for="item in navItems" :key="item.name">
          <router-link :to="{ name: item.name }" custom v-slot="{ navigate, isExactActive }">
            <button
              type="button"
              class="nav-item"
              :class="{ active: isExactActive }"
              @click="navigate"
            >{{ item.label }}</button>
          </router-link>
        </template>
      </nav>
      <div class="sidebar-foot">
        <button type="button" id="btn-sync" class="btn-sync" @click="doSync">从仓库同步</button>
        <p id="sync-status" class="muted">{{ uiState.syncStatus }}</p>
      </div>
    </aside>

    <main class="main">
      <header class="topbar">
        <h1 id="view-title">{{ viewTitle }}</h1>
        <p id="view-desc" class="muted">{{ viewDesc }}</p>
      </header>
      <router-view :key="route.name" />
    </main>
  </div>

  <dialog id="edit-dialog">
    <form method="dialog" id="edit-form" class="dialog-card">
      <h2>编辑掌握度</h2>
      <p id="edit-kid" class="mono muted"></p>
      <label>等级
        <select name="level" id="edit-level">
          <option value="L0">L0 未学</option>
          <option value="L1">L1 了解</option>
          <option value="L2">L2 理解</option>
          <option value="L3">L3 掌握</option>
          <option value="L4">L4 熟练</option>
        </select>
      </label>
      <label>错误次数
        <input type="number" name="wrong_count" id="edit-wrong" min="0" step="1" />
      </label>
      <label>备注
        <textarea name="notes" id="edit-notes" rows="3"></textarea>
      </label>
      <div class="dialog-actions">
        <button type="submit" value="cancel" class="btn-ghost">取消</button>
        <button type="submit" value="save" class="btn-primary">保存并回写 YAML</button>
      </div>
    </form>
  </dialog>
</template>
