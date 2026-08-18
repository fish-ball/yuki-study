<script setup>
import { onMounted } from "vue";
import { viewLoaders } from "../lib/pages.js";

const props = defineProps({
  pageId: { type: String, required: true },
});

function escapeText(value) {
  return String(value || "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;");
}

onMounted(() => {
  const fn = viewLoaders[props.pageId];
  if (!fn) return;
  Promise.resolve(fn()).catch((err) => {
    const el = document.getElementById(`view-${props.pageId}`);
    if (el) {
      el.innerHTML = `<div class="card"><p>无法连接后端：${escapeText(err.message)}</p></div>`;
    }
  });
});
</script>

<template>
  <section :id="'view-' + pageId" class="view active"></section>
</template>
