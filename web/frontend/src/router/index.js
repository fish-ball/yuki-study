import { createRouter, createWebHistory } from "vue-router";
import { navItems } from "../lib/pages.js";
import PageHost from "../pages/PageHost.vue";

const routes = navItems.map((item) => ({
  path: item.path,
  name: item.name,
  component: PageHost,
  props: { pageId: item.name },
}));

routes.push({
  path: "/:pathMatch(.*)*",
  redirect: "/",
});

const router = createRouter({
  history: createWebHistory(),
  routes,
});

export default router;
