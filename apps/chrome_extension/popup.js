let context = null;
let state = null;
let pollTimer = null;
let activeTabId = null;

const $ = (id) => document.getElementById(id);

function showMessage(text) {
  const el = $("message");
  el.textContent = text;
  el.classList.toggle("show", Boolean(text));
}

async function getState() {
  const defaults = { apiBaseUrl: "http://localhost:18741", autostart: false, activeSessionId: null, activeSessionMode: null, activeSessionTabId: null };
  const stored = await chrome.storage.local.get(Object.keys(defaults));
  const next = { ...defaults, ...stored };
  if (["http://localhost:8000", "http://127.0.0.1:8000"].includes(next.apiBaseUrl)) {
    next.apiBaseUrl = defaults.apiBaseUrl;
    await chrome.storage.local.set({ apiBaseUrl: next.apiBaseUrl });
  }
  return next;
}

async function api(path, init = {}) {
  const response = await fetch(`${state.apiBaseUrl}${path}`, {
    ...init,
    headers: { "Content-Type": "application/json", ...(init.headers || {}) }
  });
  if (!response.ok) throw new Error(`${response.status}: ${(await response.text()).slice(0, 500)}`);
  return response.json();
}

function render() {
  const supported = Boolean(context?.supported && context?.player_open);
  $("status").textContent = context?.supported
    ? `${context.mode?.toUpperCase()} · player ${context.player_open ? "open" : "closed"}`
    : "Неподдерживаемая страница Twitch";
  const parts = [
    context?.channel_login ? `channel: ${context.channel_login}` : null,
    context?.video_id ? `video_id: ${context.video_id}` : null,
    context?.duration_ms != null ? `duration: ${Math.round(context.duration_ms / 1000)}s` : null,
    state?.activeSessionId ? `session: ${state.activeSessionId}` : "session: none"
  ].filter(Boolean);
  $("details").textContent = parts.join("\n");
  $("start").disabled = !supported || Boolean(state?.activeSessionId);
  $("pause").disabled = !state?.activeSessionId;
  $("resume").disabled = !state?.activeSessionId;
  $("stop").disabled = !state?.activeSessionId;
  $("autostart").checked = Boolean(state?.autostart);
}

async function getTwitchIntegrity(tabId) {
  if (tabId == null) return null;
  return chrome.runtime.sendMessage({ type: "STREAMHUB_GET_TWITCH_INTEGRITY", tabId });
}

async function refreshContext() {
  const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
  activeTabId = tab?.id ?? null;
  if (!tab?.id || !tab.url?.includes("twitch.tv")) {
    context = { supported: false, mode: "unsupported", player_open: false };
    render();
    return;
  }
  try {
    context = await chrome.tabs.sendMessage(tab.id, { type: "STREAMHUB_GET_CONTEXT" });
  } catch {
    context = { supported: false, mode: "unsupported", player_open: false };
  }
  render();
}

$("start").addEventListener("click", async () => {
  showMessage("");
  try {
    const twitchIntegrity = context.mode === "vod" ? await getTwitchIntegrity(activeTabId) : null;
    if (context.mode === "vod" && !twitchIntegrity?.client_integrity) {
      throw new Error("Twitch Client-Integrity ещё не пойман. Обнови вкладку VOD, подожди несколько секунд и нажми Start снова.");
    }
    const payload = {
      mode: context.mode,
      channel_login: context.channel_login || null,
      video_external_id: context.video_id || null,
      duration_ms: context.duration_ms || null,
      page_url: context.href,
      player_open: context.player_open,
      metadata: { browser_current_time_ms: context.current_time_ms || null },
      twitch_integrity: twitchIntegrity
    };
    const result = await api("/api/v1/collector/sessions", { method: "POST", body: JSON.stringify(payload) });
    state.activeSessionId = result.id;
    state.activeSessionMode = result.media_type;
    state.activeSessionTabId = activeTabId;
    await chrome.storage.local.set({ activeSessionId: result.id, activeSessionMode: result.media_type, activeSessionTabId: activeTabId });
    showMessage(`Запись запущена: ${result.id}`);
    render();
  } catch (e) {
    showMessage(`Ошибка Start: ${e}`);
  }
});

$("pause").addEventListener("click", async () => {
  try {
    await api(`/api/v1/collector/sessions/${state.activeSessionId}/pause`, { method: "POST", body: "{}" });
    showMessage("Pause установлен. Для LIVE данные по ТЗ не должны теряться; VOD останавливается на checkpoint.");
  } catch (e) { showMessage(`Ошибка Pause: ${e}`); }
});

$("resume").addEventListener("click", async () => {
  try {
    if (state.activeSessionMode === "vod" && state.activeSessionTabId != null) {
      const twitchIntegrity = await getTwitchIntegrity(state.activeSessionTabId);
      if (twitchIntegrity?.client_integrity) {
        await api(`/api/v1/collector/sessions/${state.activeSessionId}/twitch-integrity`, {
          method: "POST", body: JSON.stringify(twitchIntegrity)
        });
      }
    }
    await api(`/api/v1/collector/sessions/${state.activeSessionId}/resume`, { method: "POST", body: "{}" });
    showMessage("Resume выполнен.");
  } catch (e) { showMessage(`Ошибка Resume: ${e}`); }
});

$("stop").addEventListener("click", async () => {
  if (!confirm("Остановить запись? Если диапазон ещё не покрыт полностью, сессия останется incomplete.")) return;
  try {
    await api(`/api/v1/collector/sessions/${state.activeSessionId}/stop`, {
      method: "POST", body: JSON.stringify({ reason: "stop_user" })
    });
    await chrome.storage.local.set({ activeSessionId: null, activeSessionMode: null, activeSessionTabId: null });
    state.activeSessionId = null;
    state.activeSessionMode = null;
    state.activeSessionTabId = null;
    showMessage("Запись остановлена.");
    render();
  } catch (e) { showMessage(`Ошибка Stop: ${e}`); }
});

$("auth").addEventListener("click", async () => {
  try {
    const result = await api("/api/v1/auth/twitch/device/start", { method: "POST", body: "{}" });
    if (result.verification_uri) await chrome.tabs.create({ url: result.verification_uri });
    showMessage(`Код Twitch: ${result.user_code || "—"}\nОжидаю подтверждение…`);
    clearInterval(pollTimer);
    pollTimer = setInterval(async () => {
      try {
        const status = await api(`/api/v1/auth/twitch/device/${result.auth_request_id}`);
        if (status.status === "authorized") {
          clearInterval(pollTimer);
          showMessage(`Twitch авторизован: ${status.identity?.login || "ok"}`);
        } else if (["failed", "expired"].includes(status.status)) {
          clearInterval(pollTimer);
          showMessage(`Авторизация: ${status.status}${status.error ? ` — ${status.error}` : ""}`);
        }
      } catch (e) {
        showMessage(`Ошибка проверки авторизации: ${e}`);
      }
    }, Math.max(3000, Number(result.interval || 5) * 1000));
  } catch (e) { showMessage(`Ошибка авторизации: ${e}`); }
});

$("autostart").addEventListener("change", async (event) => {
  state.autostart = event.target.checked;
  await chrome.storage.local.set({ autostart: state.autostart });
});

(async function init() {
  state = await getState();
  await refreshContext();
  if (
    state.activeSessionId &&
    state.activeSessionTabId == null &&
    activeTabId != null &&
    context?.mode === state.activeSessionMode
  ) {
    state.activeSessionTabId = activeTabId;
    await chrome.storage.local.set({ activeSessionTabId: activeTabId });
  }
})();
