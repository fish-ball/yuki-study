/** 给页面渲染逻辑访问当前 Vue Router，避免循环引用 */
let router = null;

export function bindRouter(instance) {
  router = instance;
}

export function getRouter() {
  return router;
}
