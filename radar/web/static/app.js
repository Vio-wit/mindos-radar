// 本地 UI 的少量交互：危险操作二次确认 + API 辅助。无第三方依赖、无远程请求。
(function () {
  "use strict";

  document.querySelectorAll("form[data-confirm]").forEach(function (form) {
    form.addEventListener("submit", function (event) {
      if (!window.confirm(form.dataset.confirm || "确认执行该操作？")) {
        event.preventDefault();
      }
    });
  });

  // 供控制台/脚本使用的本地 API 辅助：自动带上 CSRF 头。
  window.radarApi = function (path, payload, method) {
    var token = document.body.dataset.csrf || "";
    return fetch("/api/radar" + path, {
      method: method || (payload ? "POST" : "GET"),
      headers: {
        "Content-Type": "application/json",
        "X-CSRF-Token": token
      },
      body: payload ? JSON.stringify(payload) : undefined
    }).then(function (response) {
      return response.json().then(function (data) {
        if (!response.ok) {
          throw new Error((data && data.message) || ("HTTP " + response.status));
        }
        return data;
      });
    });
  };
})();
