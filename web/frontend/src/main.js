import { createApp } from "vue";
import App from "./App.vue";
import router from "./router";
import "./styles/app.css";
import { bindRouter } from "./lib/router-bridge.js";
import { api, cache } from "./lib/pages.js";

bindRouter(router);

api("/api/subjects")
  .then((subjects) => {
    cache.subjects = subjects;
  })
  .catch((err) => {
    cache.bootError = err.message;
  })
  .finally(() => {
    createApp(App).use(router).mount("#app");
  });
