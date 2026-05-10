"""Локальная OpenAPI-страница без внешних CDN."""

from __future__ import annotations


def local_openapi_docs_html() -> str:
    return """
<!doctype html>
<html lang="ru">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>ИМЦРО API - Docs</title>
  <style>
    :root {
      --bg: #f6f7f9;
      --panel: #ffffff;
      --text: #172033;
      --muted: #667085;
      --line: #d9dee8;
      --accent: #0f766e;
      --danger: #b42318;
      --ok: #047857;
    }
    * { box-sizing: border-box; }
    body {
      margin: 0;
      background: var(--bg);
      color: var(--text);
      font: 14px/1.45 Arial, sans-serif;
    }
    header {
      position: sticky;
      top: 0;
      z-index: 2;
      display: flex;
      gap: 12px;
      align-items: center;
      justify-content: space-between;
      padding: 14px 22px;
      background: var(--panel);
      border-bottom: 1px solid var(--line);
    }
    h1 { margin: 0; font-size: 20px; letter-spacing: 0; }
    main { max-width: 1180px; margin: 0 auto; padding: 22px; }
    .toolbar { display: flex; gap: 10px; align-items: center; flex-wrap: wrap; }
    input, textarea {
      width: 100%;
      border: 1px solid var(--line);
      border-radius: 6px;
      padding: 9px 10px;
      color: var(--text);
      background: #fff;
      font: 13px/1.4 Consolas, monospace;
    }
    header input { width: min(520px, 46vw); font-family: Arial, sans-serif; }
    button {
      border: 1px solid var(--accent);
      border-radius: 6px;
      padding: 8px 12px;
      color: #fff;
      background: var(--accent);
      cursor: pointer;
      font-weight: 600;
    }
    button.secondary { color: var(--accent); background: #fff; }
    .meta { color: var(--muted); margin: 0 0 18px; }
    .endpoint {
      margin: 12px 0;
      overflow: hidden;
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: 8px;
    }
    .summary {
      display: grid;
      grid-template-columns: 86px 1fr auto;
      gap: 12px;
      align-items: center;
      width: 100%;
      padding: 13px 15px;
      background: #fff;
      border: 0;
      color: var(--text);
      text-align: left;
      cursor: pointer;
    }
    .method {
      display: inline-flex;
      align-items: center;
      justify-content: center;
      min-width: 72px;
      padding: 4px 8px;
      border-radius: 5px;
      color: #fff;
      font: 700 12px/1 Arial, sans-serif;
    }
    .GET { background: #2563eb; }
    .POST { background: #059669; }
    .PUT, .PATCH { background: #d97706; }
    .DELETE { background: #dc2626; }
    .path { font: 700 14px/1.3 Consolas, monospace; overflow-wrap: anywhere; }
    .tag { color: var(--muted); font-size: 12px; }
    .details { display: none; padding: 15px; border-top: 1px solid var(--line); }
    .endpoint.open .details { display: block; }
    .grid { display: grid; grid-template-columns: minmax(0, 1fr) minmax(0, 1fr); gap: 14px; }
    pre {
      overflow: auto;
      max-height: 420px;
      margin: 8px 0 0;
      padding: 12px;
      border-radius: 6px;
      background: #101828;
      color: #e5e7eb;
      white-space: pre-wrap;
      overflow-wrap: anywhere;
    }
    .status { margin-top: 8px; color: var(--muted); }
    .error { color: var(--danger); }
    .ok { color: var(--ok); }
    @media (max-width: 760px) {
      header { align-items: stretch; flex-direction: column; }
      header input { width: 100%; }
      .summary { grid-template-columns: 78px 1fr; }
      .tag { grid-column: 2; }
      .grid { grid-template-columns: 1fr; }
    }
  </style>
</head>
<body>
  <header>
    <h1>ИМЦРО API</h1>
    <div class="toolbar">
      <input id="filter" placeholder="Фильтр по пути, методу или тегу">
      <input id="token" placeholder="Bearer token для запросов">
    </div>
  </header>
  <main>
    <p id="meta" class="meta">Загружаю /openapi.json...</p>
    <div id="app"></div>
  </main>
  <script>
    const state = { spec: null, endpoints: [] };
    const app = document.getElementById("app");
    const meta = document.getElementById("meta");
    const filter = document.getElementById("filter");
    const token = document.getElementById("token");

    token.value = localStorage.getItem("docsBearerToken") || "";
    token.addEventListener("input", () => localStorage.setItem("docsBearerToken", token.value.trim()));

    function escapeHtml(value) {
      return String(value ?? "")
        .replace(/&/g, "&amp;")
        .replace(/</g, "&lt;")
        .replace(/>/g, "&gt;")
        .replace(/"/g, "&quot;");
    }

    function resolveRef(ref) {
      if (!ref || !ref.startsWith("#/")) return null;
      return ref.slice(2).split("/").reduce((obj, part) => obj && obj[part], state.spec);
    }

    function sampleFromSchema(schema) {
      if (!schema) return "";
      if (schema.$ref) schema = resolveRef(schema.$ref) || schema;
      if (schema.type === "object" || schema.properties) {
        const obj = {};
        for (const [key, prop] of Object.entries(schema.properties || {})) {
          const resolved = prop.$ref ? resolveRef(prop.$ref) || prop : prop;
          if (resolved.default !== undefined) obj[key] = resolved.default;
          else if (resolved.type === "integer" || resolved.type === "number") obj[key] = 0;
          else if (resolved.type === "boolean") obj[key] = false;
          else if (resolved.type === "array") obj[key] = [];
          else if (resolved.type === "object") obj[key] = {};
          else obj[key] = "";
        }
        return JSON.stringify(obj, null, 2);
      }
      return "";
    }

    function requestBodySchema(operation) {
      return operation.requestBody?.content?.["application/json"]?.schema || null;
    }

    function render() {
      const q = filter.value.trim().toLowerCase();
      app.innerHTML = "";
      const filtered = state.endpoints.filter((item) => {
        const text = `${item.method} ${item.path} ${(item.operation.tags || []).join(" ")} ${item.operation.summary || ""}`.toLowerCase();
        return !q || text.includes(q);
      });
      meta.textContent = `${state.spec.info?.title || "API"} ${state.spec.info?.version || ""}: ${filtered.length} endpoint(s)`;

      for (const item of filtered) {
        const schema = requestBodySchema(item.operation);
        const sample = sampleFromSchema(schema);
        const tag = (item.operation.tags || []).join(", ");
        const responses = JSON.stringify(item.operation.responses || {}, null, 2);
        const id = btoa(unescape(encodeURIComponent(`${item.method}:${item.path}`))).replace(/=/g, "");
        const card = document.createElement("section");
        card.className = "endpoint";
        card.innerHTML = `
          <button class="summary secondary" type="button">
            <span class="method ${item.method}">${item.method}</span>
            <span class="path">${escapeHtml(item.path)}</span>
            <span class="tag">${escapeHtml(tag || item.operation.summary || "")}</span>
          </button>
          <div class="details">
            <p>${escapeHtml(item.operation.summary || item.operation.description || "")}</p>
            <div class="grid">
              <div>
                <label for="body-${id}">Request body</label>
                <textarea id="body-${id}" rows="10" ${sample ? "" : "placeholder='Для этого метода тело не требуется'"}>${escapeHtml(sample)}</textarea>
                <button type="button" data-run="${id}">Отправить</button>
                <span id="status-${id}" class="status"></span>
              </div>
              <div>
                <label>Responses</label>
                <pre>${escapeHtml(responses)}</pre>
              </div>
            </div>
            <pre id="result-${id}"></pre>
          </div>
        `;
        card.querySelector(".summary").addEventListener("click", () => card.classList.toggle("open"));
        card.querySelector("[data-run]").addEventListener("click", () => runRequest(item, id));
        app.appendChild(card);
      }
    }

    async function runRequest(item, id) {
      const status = document.getElementById(`status-${id}`);
      const result = document.getElementById(`result-${id}`);
      const bodyText = document.getElementById(`body-${id}`).value.trim();
      const headers = {};
      const savedToken = token.value.trim();
      if (savedToken) headers.Authorization = savedToken.startsWith("Bearer ") ? savedToken : `Bearer ${savedToken}`;
      const options = { method: item.method, headers };
      if (!["GET", "HEAD"].includes(item.method) && bodyText) {
        headers["Content-Type"] = "application/json";
        options.body = bodyText;
      }
      status.textContent = "Выполняю запрос...";
      status.className = "status";
      result.textContent = "";
      try {
        const response = await fetch(item.path, options);
        const text = await response.text();
        status.textContent = `${response.status} ${response.statusText}`;
        status.className = `status ${response.ok ? "ok" : "error"}`;
        try {
          result.textContent = JSON.stringify(JSON.parse(text), null, 2);
        } catch {
          result.textContent = text;
        }
      } catch (error) {
        status.textContent = error.message;
        status.className = "status error";
      }
    }

    async function boot() {
      try {
        const response = await fetch("/openapi.json");
        state.spec = await response.json();
        const methods = new Set(["get", "post", "put", "patch", "delete"]);
        state.endpoints = Object.entries(state.spec.paths || {}).flatMap(([path, ops]) =>
          Object.entries(ops)
            .filter(([method]) => methods.has(method))
            .map(([method, operation]) => ({ path, method: method.toUpperCase(), operation }))
        );
        render();
      } catch (error) {
        meta.innerHTML = `<span class="error">Не удалось загрузить /openapi.json: ${escapeHtml(error.message)}</span>`;
      }
    }

    filter.addEventListener("input", render);
    boot();
  </script>
</body>
</html>
    """
