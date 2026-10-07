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

function renderObservabilitySummary(summary) {
  const badge = $("botStatusBadge");
  const labels = { normal: "运行正常", high_latency: "延迟偏高", disconnected: "已断线", unknown: "暂无心跳数据" };
  badge.textContent = labels[summary.status] || labels.unknown;
  badge.classList.remove("running", "stopped", "status-normal", "status-high", "status-down", "status-unknown");
  const className = { normal: "status-normal", high_latency: "status-high", disconnected: "status-down", unknown: "status-unknown" }[summary.status] || "status-unknown";
  badge.classList.add(className, summary.online ? "running" : "stopped");
  $("botStatusText").textContent = summary.sampled_at
    ? `最近心跳：${formatTimestamp(summary.sampled_at)}`
    : "Bot 尚无心跳记录。启动后，状态会在 15 秒内更新。";
  $("metricLatency").textContent = summary.gateway_latency_ms === null ? "—" : `${Math.round(summary.gateway_latency_ms)} ms`;
  $("metricUptime").textContent = formatDuration(summary.uptime_seconds);
  $("metricGuilds").textContent = summary.online ? (summary.guild_count ?? "—") : "—";
  $("metricMemory").textContent = summary.memory_mb === null ? "—" : `${summary.memory_mb} MB`;
  $("unresolvedErrorCount").textContent = summary.unresolved_errors ?? 0;
  $("errorBadge").textContent = summary.unresolved_errors ?? 0;
  $("errorBadge").classList.toggle("hidden", !summary.unresolved_errors);
  renderHeartbeat(summary.timeline || []);
  renderFeatureUsage(summary.features || []);
}

function renderHeartbeat(timeline) {
  const strip = $("heartbeatStrip");
  strip.replaceChildren();
  const labels = { normal: "运行正常", high_latency: "延迟偏高", disconnected: "已断线", unknown: "暂无心跳" };
  timeline.forEach((point, index) => {
    const cell = document.createElement("button");
    cell.type = "button";
    cell.className = `heartbeat-cell ${point.status || "unknown"}`;
    const date = new Date(Number(point.minute) * 1000);
    const time = date.toLocaleTimeString("zh-CN", { hour: "2-digit", minute: "2-digit", hour12: false });
    const latency = point.latency_ms === null ? "无延迟数据" : `${Math.round(point.latency_ms)} ms`;
    cell.setAttribute("aria-label", `${time}，${labels[point.status] || labels.unknown}，${latency}`);
    cell.title = `${time} · ${labels[point.status] || labels.unknown} · ${latency}`;
    cell.style.setProperty("--cell-index", index);
    strip.appendChild(cell);
  });
  const end = new Date();
  const start = new Date(end.getTime() - 59 * 60000);
  $("heartbeatStart").textContent = start.toLocaleTimeString("zh-CN", { hour: "2-digit", minute: "2-digit", hour12: false });
  $("heartbeatEnd").textContent = end.toLocaleTimeString("zh-CN", { hour: "2-digit", minute: "2-digit", hour12: false });
}

function renderFeatureUsage(features) {
  const body = $("featureListBody");
  body.replaceChildren();
  if (!features.length || features.every((feature) => feature.success_rate === null)) {
    const empty = document.createElement("tr");
    empty.innerHTML = '<td colspan="4" class="empty-state">最近 24 小时暂无功能操作记录。</td>';
    body.appendChild(empty);
    return;
  }
  features.forEach((feature) => {
    const triggerNames = {
      messages: "/sendmessage · Dashboard",
      roles: "/reactionrole · /giverole · /removerole",
      onboarding: "Discord 交互 · Dashboard",
      welcome: "新成员加入 · Dashboard",
      moderation: "Moderation 指令 · Dashboard",
      tickets: "Discord 工单按钮 · Dashboard",
    };
    const row = document.createElement("tr");
    const rate = feature.success_rate === null ? "暂无记录" : `${feature.success_rate}%`;
    row.innerHTML = `
      <td><button class="feature-link" data-view="${escapeTelemetry(featureViews[feature.key] || "overview")}">${escapeTelemetry(feature.name)}</button></td>
      <td>${escapeTelemetry(triggerNames[feature.key] || feature.trigger)}</td>
      <td><span class="metric-rate ${feature.success_rate !== null && feature.success_rate < 90 ? "rate-low" : ""}">${rate}</span></td>
      <td>${escapeTelemetry(formatTimestamp(feature.last_used))}</td>`;
    row.querySelector("[data-view]").addEventListener("click", () => setView(featureViews[feature.key] || "overview"));
    body.appendChild(row);
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

// The view registry delegates loading to the page controller for each feature.
const dashboardPageModules = {
  overview: { load: loadObservabilitySummary },
  messages: { load: loadMessagePage },
  roles: { load: loadRolePanelPage },
  onboarding: { load: ensureOnboardingLoaded },
  welcome: { load: ensureWelcomeLoaded },
  moderation: { load: ensureModerationLoaded },
  tickets: { load: ensureTicketsLoaded },
  errors: { load: loadErrors },
  announcements: { load: () => Promise.resolve() },
  saved: { load: loadSaved },
  settings: { load: () => Promise.resolve() },
};

document.addEventListener("DOMContentLoaded", wireObservabilityEvents, { once: true });

const savedTheme = localStorage.getItem("dashboardTheme");
if (savedTheme === "light" || savedTheme === "dark") document.documentElement.dataset.theme = savedTheme;
