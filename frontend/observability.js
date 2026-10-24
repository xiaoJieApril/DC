// Health timeline, feature usage, and error triage data adapter.
const observabilityState = {
  summary: null,
  errorLevel: "",
  errorStatus: "open",
  errorCursor: null,
  errorRows: [],
  errorNextCursor: null,
  expandedError: null,
  pollingStarted: false,
  traceDrawn: false,
};

const featureViews = {
  messages: "messages",
  roles: "roles",
  onboarding: "onboarding",
  welcome: "welcome",
  moderation: "moderation",
  tickets: "tickets",
};

function formatDuration(seconds) {
  if (seconds === null || seconds === undefined) return "暂无数据";
  const total = Math.max(0, Math.floor(Number(seconds)));
  const days = Math.floor(total / 86400);
  const hours = Math.floor((total % 86400) / 3600);
  const minutes = Math.floor((total % 3600) / 60);
  return days ? `${days}天 ${hours}小时` : hours ? `${hours}小时 ${minutes}分` : `${minutes}分钟`;
}

function formatTimestamp(timestamp) {
  return timestamp ? new Date(Number(timestamp) * 1000).toLocaleString("zh-CN", { hour12: false }) : "暂无记录";
}

function escapeTelemetry(value) {
  return String(value ?? "").replace(/[&<>"']/g, (char) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[char]);
}

async function loadObservabilitySummary() {
  const summary = await api("/api/observability/summary");
  observabilityState.summary = summary;
  renderObservabilitySummary(summary);
  return summary;
}

const STATUS_LABELS = { normal: "运行正常", high_latency: "延迟偏高", disconnected: "已断线", unknown: "暂无心跳数据" };
const MINUTE_LABELS = { normal: "运行正常", high_latency: "延迟偏高", disconnected: "已断线", unknown: "暂无心跳" };
const HIGH_LATENCY_MS = 250; // same threshold the backend uses for "延迟偏高"

function clock(date) {
  return date.toLocaleTimeString("zh-CN", { hour: "2-digit", minute: "2-digit", hour12: false });
}

function setVital(id, value, unit = "") {
  const el = $(id);
  el.textContent = value;
  if (unit && value !== "—") {
    const span = document.createElement("span");
    span.className = "ov-unit";
    span.textContent = unit;
    el.append(span);
  }
}

function formatRelative(timestamp) {
  if (!timestamp) return "暂无记录";
  const seconds = Math.max(0, Date.now() / 1000 - Number(timestamp));
  if (seconds < 60) return "刚刚";
  if (seconds < 3600) return `${Math.floor(seconds / 60)} 分钟前`;
  if (seconds < 86400) return `${Math.floor(seconds / 3600)} 小时前`;
  if (seconds < 86400 * 7) return `${Math.floor(seconds / 86400)} 天前`;
  return new Date(Number(timestamp) * 1000).toLocaleDateString("zh-CN", { month: "numeric", day: "numeric" });
}

function renderObservabilitySummary(summary) {
  const badge = $("botStatusBadge");
  badge.textContent = STATUS_LABELS[summary.status] || STATUS_LABELS.unknown;
  badge.classList.remove("running", "stopped", "status-normal", "status-high", "status-down", "status-unknown");
  const className = { normal: "status-normal", high_latency: "status-high", disconnected: "status-down", unknown: "status-unknown" }[summary.status] || "status-unknown";
  badge.classList.add(className, summary.online ? "running" : "stopped");
  $("botStatusText").textContent = summary.sampled_at
    ? `最近心跳：${formatTimestamp(summary.sampled_at)}`
    : "Bot 尚无心跳记录。启动后，状态会在 15 秒内更新。";
  setVital("metricLatency", summary.gateway_latency_ms === null || summary.gateway_latency_ms === undefined ? "—" : String(Math.round(summary.gateway_latency_ms)), "ms");
  setVital("metricUptime", formatDuration(summary.uptime_seconds));
  setVital("metricGuilds", summary.online ? String(summary.guild_count ?? "—") : "—");
  setVital("metricMemory", summary.memory_mb === null || summary.memory_mb === undefined ? "—" : String(summary.memory_mb), "MB");
  $("unresolvedErrorCount").textContent = summary.unresolved_errors ?? 0;
  $("errorBadge").textContent = summary.unresolved_errors ?? 0;
  $("errorBadge").classList.toggle("hidden", !summary.unresolved_errors);
  renderHeartbeat(summary.timeline || []);
  renderFeatureUsage(summary.features || []);
  renderIncidents(summary.incidents || []);
}

// Draws the last 60 minutes as a latency trace. Gaps in the line are minutes with no
// latency reading; red bands are minutes where the bot was disconnected.
function renderHeartbeat(timeline) {
  const plot = $("heartbeatStrip");
  const count = timeline.length;
  const reading = (point) => (point && (point.status === "normal" || point.status === "high_latency") && point.latency_ms !== null && point.latency_ms !== undefined ? Number(point.latency_ms) : null);
  const values = timeline.map(reading);
  const known = timeline.filter((point) => point.status && point.status !== "unknown");
  const connected = known.filter((point) => point.status !== "disconnected").length;
  const readings = values.filter((value) => value !== null);

  $("ovAvailability").textContent = known.length ? `${connected} / ${known.length} 分钟在线` : "暂无数据";
  const average = readings.length ? Math.round(readings.reduce((sum, value) => sum + value, 0) / readings.length) : null;
  $("metricLatencyNote").textContent = average === null ? "近 60 分钟平均 —" : `近 60 分钟平均 ${average} ms`;

  const wasFirst = !observabilityState.traceDrawn;
  plot.className = "ov-plot" + (wasFirst && count ? " is-first" : "");
  plot.setAttribute("role", "img");
  plot.setAttribute("aria-label", known.length
    ? `过去 60 分钟：${connected} 分钟在线，${known.length - connected} 分钟断线${average === null ? "" : `，平均延迟 ${average} 毫秒`}`
    : "过去 60 分钟没有心跳数据");
  plot.replaceChildren();

  if (!count || !known.length) {
    plot.innerHTML = '<div class="ov-canvas"><div class="ov-empty">暂无心跳数据。启动 bot 后，曲线会在 15 秒内出现。</div></div>';
    return;
  }
  observabilityState.traceDrawn = true;

  const peak = Math.max(HIGH_LATENCY_MS, ...readings);
  const steps = [300, 400, 600, 800, 1200, 2000, 5000, 10000];
  const max = steps.find((step) => step >= peak * 1.1) || Math.ceil((peak * 1.1) / 1000) * 1000;
  const x = (index) => ((index + 0.5) / count) * 100;
  const y = (value) => 96 - (Math.min(value, max) / max) * 92;
  const f = (n) => n.toFixed(2);

  let ok = "", warn = "", bands = "", areas = "";
  let run = [];
  const closeRun = () => {
    if (run.length > 1) {
      areas += `<path class="ov-area" d="M${f(x(run[0]))} 96 ${run.map((i) => `L${f(x(i))} ${f(y(values[i]))}`).join(" ")} L${f(x(run[run.length - 1]))} 96 Z"/>`;
    }
    run = [];
  };
  values.forEach((value, i) => {
    if (timeline[i].status === "disconnected") bands += `<rect class="ov-band" x="${f((i / count) * 100)}" y="0" width="${f(100 / count + 0.06)}" height="100"/>`;
    if (value === null) { closeRun(); return; }
    run.push(i);
    const prev = values[i - 1];
    const next = values[i + 1];
    if (prev === null || prev === undefined) {
      if (next === null || next === undefined) {
        const dot = `M${f(x(i))} ${f(y(value))}h0`;
        if (timeline[i].status === "high_latency") warn += dot; else ok += dot;
      }
    } else {
      const segment = `M${f(x(i - 1))} ${f(y(prev))}L${f(x(i))} ${f(y(value))}`;
      if (timeline[i].status === "high_latency") warn += segment; else ok += segment;
    }
  });
  closeRun();

  const mid = max / 2;
  const limitY = y(HIGH_LATENCY_MS);
  const svg = `<svg viewBox="0 0 100 100" preserveAspectRatio="none" aria-hidden="true">
    ${bands}
    <line class="ov-grid" x1="0" x2="100" y1="${f(y(max))}" y2="${f(y(max))}"/>
    <line class="ov-grid" x1="0" x2="100" y1="${f(y(mid))}" y2="${f(y(mid))}"/>
    <line class="ov-grid is-limit" x1="0" x2="100" y1="${f(limitY)}" y2="${f(limitY)}"/>
    <line class="ov-grid is-base" x1="0" x2="100" y1="${f(y(0))}" y2="${f(y(0))}"/>
    ${areas}<path class="ov-line is-ok" d="${ok}"/><path class="ov-line is-warn" d="${warn}"/>
  </svg>`;

  const yLabels = [[max, ""], [mid, ""], [HIGH_LATENCY_MS, "is-limit"], [0, ""]]
    .filter(([value], index, all) => index === 3 || index === 2 || Math.abs(y(value) - limitY) > 9)
    .map(([value, cls]) => `<span class="${cls}" style="top:${f(y(value))}%">${Math.round(value)}</span>`).join("");

  const tickIndexes = [];
  for (let i = 0; i < count - 8; i += 15) tickIndexes.push(i);
  const xLabels = tickIndexes.map((i) => `<span style="left:${f((i / count) * 100)}%">${clock(new Date(Number(timeline[i].minute) * 1000))}</span>`).join("") + "<span>现在</span>";

  const lastIndex = count - 1;
  const live = values[lastIndex] === null ? "" : `<span class="ov-live ${timeline[lastIndex].status === "high_latency" ? "is-warn" : ""}" style="left:${f(x(lastIndex))}%;top:${f(y(values[lastIndex]))}%"></span>`;

  plot.innerHTML = `<div class="ov-ylabels">${yLabels}</div>
    <div class="ov-canvas">${svg}${live}<div class="ov-cols">${timeline.map((_, i) => `<div class="ov-col" data-i="${i}"></div>`).join("")}</div><div class="ov-tip" hidden></div></div>
    <div class="ov-xlabels">${xLabels}</div>`;

  const tip = plot.querySelector(".ov-tip");
  const cols = plot.querySelector(".ov-cols");
  cols.addEventListener("mouseover", (event) => {
    const col = event.target.closest(".ov-col");
    if (!col) return;
    const i = Number(col.dataset.i);
    const point = timeline[i];
    const latency = values[i] === null ? "无延迟数据" : `${Math.round(values[i])} ms`;
    const tone = point.status === "disconnected" ? "t-bad" : point.status === "high_latency" ? "t-warn" : "";
    tip.innerHTML = `<b>${clock(new Date(Number(point.minute) * 1000))}</b><span class="${tone}">${escapeTelemetry(MINUTE_LABELS[point.status] || MINUTE_LABELS.unknown)}</span><span>${latency}</span>`;
    tip.style.left = `${x(i)}%`;
    tip.classList.toggle("is-left", i < 6);
    tip.classList.toggle("is-right", i > count - 7);
    tip.hidden = false;
  });
  cols.addEventListener("mouseleave", () => { tip.hidden = true; });
}

function formatSpan(seconds) {
  const total = Math.max(0, Math.round(seconds));
  if (total < 60) return `${total} 秒`;
  if (total < 3600) return `${Math.round(total / 60)} 分钟`;
  const hours = Math.floor(total / 3600);
  const minutes = Math.round((total % 3600) / 60);
  return minutes ? `${hours} 小时 ${minutes} 分` : `${hours} 小时`;
}

function renderIncidents(items) {
  const list = $("incidentList");
  if (!list) return;
  const openIds = new Set([...list.querySelectorAll("details[open]")].map((el) => el.dataset.id));
  list.replaceChildren();
  if (!items.length) {
    const empty = document.createElement("li");
    empty.className = "ov-empty-row";
    empty.textContent = "最近 7 天没有故障记录。";
    list.appendChild(empty);
    return;
  }
  const day = (date) => date.toLocaleDateString("zh-CN", { month: "numeric", day: "numeric" });
  items.forEach((item) => {
    const start = new Date(Number(item.started_at) * 1000);
    const end = item.ended_at ? new Date(Number(item.ended_at) * 1000) : null;
    const range = end
      ? `${day(start)} ${clock(start)} – ${day(end) === day(start) ? "" : `${day(end)} `}${clock(end)}`
      : `${day(start)} ${clock(start)} 起`;
    const tone = item.planned ? "is-planned" : item.kind === "high_latency" ? "is-warn" : "is-bad";
    const peak = item.kind === "high_latency" && item.peak_latency_ms ? `，峰值 ${Math.round(item.peak_latency_ms)} ms` : "";
    const evidence = (item.evidence || []).map((line) => `<li>${escapeTelemetry(line)}</li>`).join("");
    const row = document.createElement("li");
    row.className = `ov-incident ${tone}`;
    row.innerHTML = `
      <div class="ov-incident-when"><time>${escapeTelemetry(range)}</time><span>${item.ongoing ? "进行中，已持续 " : "持续 "}${escapeTelemetry(formatSpan(item.duration_seconds))}${escapeTelemetry(peak)}</span></div>
      <div class="ov-incident-what">
        <p class="ov-incident-kind"><b>${escapeTelemetry(item.kind_label)}</b>${item.planned ? '<i>计划内</i>' : ""}${item.ongoing ? '<i class="is-live">进行中</i>' : ""}</p>
        <p class="ov-incident-reason">${item.planned ? "" : '<span>可能原因</span>'}${escapeTelemetry(item.reason)}</p>
        ${evidence ? `<details data-id="${item.id}" ${openIds.has(String(item.id)) ? "open" : ""}><summary>查看依据（${item.evidence.length}）</summary><ul>${evidence}</ul></details>` : ""}
      </div>`;
    list.appendChild(row);
  });
}

function renderFeatureUsage(features) {
  const list = $("featureListBody");
  list.replaceChildren();
  if (!features.length || features.every((feature) => feature.success_rate === null)) {
    const empty = document.createElement("li");
    empty.className = "ov-empty-row";
    empty.textContent = "最近 24 小时暂无功能操作记录。";
    list.appendChild(empty);
    return;
  }
  const triggerNames = {
    messages: "/sendmessage · Dashboard",
    roles: "/reactionrole · /giverole · /removerole",
    onboarding: "Discord 交互 · Dashboard",
    welcome: "新成员加入 · Dashboard",
    moderation: "Moderation 指令 · Dashboard",
    tickets: "Discord 工单按钮 · Dashboard",
  };
  features.forEach((feature) => {
    const none = feature.success_rate === null || feature.success_rate === undefined;
    const low = !none && feature.success_rate < 90;
    const rate = none ? "暂无记录" : `${feature.success_rate}%`;
    const row = document.createElement("li");
    row.className = "ov-row";
    row.innerHTML = `
      <button type="button" class="feature-link" data-view="${escapeTelemetry(featureViews[feature.key] || "overview")}">${escapeTelemetry(feature.name)}</button>
      <span class="ov-trigger">${escapeTelemetry(triggerNames[feature.key] || feature.trigger)}</span>
      <span class="ov-rate ${low ? "is-low" : ""} ${none ? "is-none" : ""}"><i style="--rate:${none ? 0 : Math.max(0, Math.min(100, Number(feature.success_rate)))}%"></i><b>${rate}</b></span>
      <time title="${escapeTelemetry(formatTimestamp(feature.last_used))}">${escapeTelemetry(formatRelative(feature.last_used))}</time>`;
    row.querySelector("[data-view]").addEventListener("click", () => setView(featureViews[feature.key] || "overview"));
    list.appendChild(row);
  });
}

async function loadErrors(reset = true) {
  if (reset) {
    observabilityState.errorCursor = null;
    observabilityState.errorRows = [];
  }
  $("errorList").setAttribute("aria-busy", "true");
  const params = new URLSearchParams({ limit: "50" });
  if (observabilityState.errorLevel) params.set("level", observabilityState.errorLevel);
  if (observabilityState.errorStatus) params.set("status", observabilityState.errorStatus);
  if (!reset && observabilityState.errorCursor) params.set("cursor", observabilityState.errorCursor);
  try {
    const page = await api(`/api/errors?${params}`);
    observabilityState.errorRows = reset ? page.items : [...observabilityState.errorRows, ...page.items];
    observabilityState.errorNextCursor = page.next_cursor;
    $("unresolvedErrorCount").textContent = page.unresolved_count ?? 0;
    $("errorBadge").textContent = page.unresolved_count ?? 0;
    $("errorBadge").classList.toggle("hidden", !page.unresolved_count);
    renderErrors();
  } catch (error) {
    $("errorList").innerHTML = `<p class="inline-error">${escapeTelemetry(error.message)}</p>`;
  } finally {
    $("errorList").setAttribute("aria-busy", "false");
  }
}

function renderErrors() {
  const list = $("errorList");
  list.replaceChildren();
  if (!observabilityState.errorRows.length) {
    list.innerHTML = '<p class="empty-state">当前筛选条件下没有错误记录。</p>';
    $("loadMoreErrorsBtn").classList.add("hidden");
    return;
  }
  observabilityState.errorRows.forEach((entry) => {
    const row = document.createElement("article");
    row.className = "error-row";
    row.dataset.expanded = String(observabilityState.expandedError === entry.id);
    const statusLabel = { open: "未处理", in_progress: "处理中", resolved: "已解决" }[entry.status] || entry.status;
    row.innerHTML = `
      <button class="error-summary" type="button" aria-expanded="${observabilityState.expandedError === entry.id}">
        <time>${escapeTelemetry(formatTimestamp(entry.recorded_at))}</time>
        <span class="severity severity-${entry.level.toLowerCase()}">${escapeTelemetry(entry.level)}</span>
        <span>${escapeTelemetry(entry.feature || "Bot")}</span>
        <span class="error-command">${escapeTelemetry(entry.command || "—")}</span>
        <span class="error-message">${escapeTelemetry(entry.message)}</span>
        <span class="error-status">${statusLabel}</span>
      </button>
      <div class="error-details ${observabilityState.expandedError === entry.id ? "expanded" : ""}">
        <p>${escapeTelemetry(entry.hint || "暂无原因提示")}</p>
        <pre>${escapeTelemetry(entry.traceback || entry.message)}</pre>
        <div class="actions compact error-actions"></div>
      </div>`;
    const summaryButton = row.querySelector(".error-summary");
    summaryButton.addEventListener("click", () => {
      observabilityState.expandedError = observabilityState.expandedError === entry.id ? null : entry.id;
      renderErrors();
    });
    const actions = row.querySelector(".error-actions");
    const transitions = entry.status === "open"
      ? [["in_progress", "开始处理"]]
      : entry.status === "in_progress"
        ? [["resolved", "标记为已解决"]]
        : [["open", "重新打开"]];
    transitions.forEach(([status, label]) => {
      const button = document.createElement("button");
      button.type = "button";
      button.className = "secondary";
      button.textContent = label;
      button.addEventListener("click", async () => {
        button.disabled = true;
        try {
          await api(`/api/errors/${entry.id}`, { method: "PATCH", body: JSON.stringify({ status }) });
          toast(`错误 #${entry.id} 已更新为“${label}”。`);
          await Promise.all([loadErrors(), loadObservabilitySummary()]);
        } catch (error) {
          toast(`无法更新错误状态：${error.message}`);
          button.disabled = false;
        }
      });
      actions.appendChild(button);
    });
    list.appendChild(row);
  });
  $("loadMoreErrorsBtn").classList.toggle("hidden", !observabilityState.errorNextCursor);
}

function wireObservabilityEvents() {
  $("themeToggle").textContent = document.documentElement.dataset.theme === "dark" ? "切换到浅色" : "切换到深色";
  $("errorLevelFilter").addEventListener("change", () => {
    observabilityState.errorLevel = $("errorLevelFilter").value;
    loadErrors();
  });
  $("errorStatusFilter").addEventListener("change", () => {
    observabilityState.errorStatus = $("errorStatusFilter").value;
    loadErrors();
  });
  $("loadMoreErrorsBtn").addEventListener("click", () => {
    observabilityState.errorCursor = observabilityState.errorNextCursor;
    loadErrors(false);
  });
  $("themeToggle").addEventListener("click", () => {
    const next = document.documentElement.dataset.theme === "dark" ? "light" : "dark";
    document.documentElement.dataset.theme = next;
    localStorage.setItem("dashboardTheme", next);
    $("themeToggle").textContent = next === "dark" ? "切换到浅色" : "切换到深色";
  });
  $("refreshErrorsBtn").addEventListener("click", () => loadErrors());
  $("mobileMenuBtn").addEventListener("click", () => {
    const opened = document.body.classList.toggle("menu-open");
    $("mobileMenuBtn").setAttribute("aria-expanded", String(opened));
  });
  document.querySelectorAll(".nav[data-view]").forEach((button) => button.addEventListener("click", () => {
    document.body.classList.remove("menu-open");
    $("mobileMenuBtn").setAttribute("aria-expanded", "false");
  }));
  if (!observabilityState.pollingStarted) {
    observabilityState.pollingStarted = true;
    window.setInterval(() => {
      if (!$("appView").classList.contains("hidden")) {
        loadObservabilitySummary().catch(() => {});
        if (!$("errors").classList.contains("hidden")) loadErrors();
      }
    }, 15000);
  }
}

const savedTheme = localStorage.getItem("dashboardTheme");
if (savedTheme === "light" || savedTheme === "dark") document.documentElement.dataset.theme = savedTheme;
