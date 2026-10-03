let context = null;
let state = null;
let pollTimer = null;
let activeTabId = null;
let currentEventId = null;
let activeChatSession = null;
let activeVideoSession = null;

const $ = (id) => document.getElementById(id);

function showMessage(text) {
  const el = $("message");
  el.textContent = text;
  el.classList.toggle("show", Boolean(text));
}

async function getState() {
  const defaults = {
    apiBaseUrl: "http://localhost:18741",
    activeSessionId: null,
    activeSessionMode: null,
    activeSessionTabId: null,
    captureChatEnabled: true,
    captureVideoEnabled: false
  };
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

async function persistActiveChatSession(activeSession) {
  state.activeSessionId = activeSession?.id || null;
  state.activeSessionMode = activeSession?.media_type || context?.mode || null;
  state.activeSessionTabId = activeSession ? activeTabId : null;
  await chrome.storage.local.set({
    activeSessionId: state.activeSessionId,
    activeSessionMode: state.activeSessionMode,
    activeSessionTabId: state.activeSessionTabId
  });
}

function render() {
  const supported = Boolean(context?.supported && context?.player_open);
  $("status").textContent = context?.supported
    ? `${context.mode?.toUpperCase()} · player ${context.player_open ? "open" : "closed"}`
    : "Неподдерживаемая страница Twitch";

  $("chat-toggle").checked = Boolean(state?.captureChatEnabled);
  $("video-toggle").checked = Boolean(state?.captureVideoEnabled);

  const parts = [
    context?.channel_login ? `channel: ${context.channel_login}` : null,
    context?.video_id ? `video_id: ${context.video_id}` : null,
    context?.duration_ms != null ? `duration: ${Math.round(context.duration_ms / 1000)}s` : null,
    currentEventId ? `event: ${currentEventId}` : "event: none",
    activeChatSession ? `chat: ${activeChatSession.id} · ${activeChatSession.status}` : "chat: idle",
    activeVideoSession ? `video: ${activeVideoSession.id} · ${activeVideoSession.status}` : "video: idle"
  ].filter(Boolean);
  $("details").textContent = parts.join("\n");

  const selectedAny = Boolean(state?.captureChatEnabled || state?.captureVideoEnabled);
  const selectedNeedStart = Boolean(
    (state?.captureChatEnabled && !activeChatSession) ||
    (state?.captureVideoEnabled && !activeVideoSession)
  );
  $("start").disabled = !supported || !selectedAny || !selectedNeedStart;
  $("pause-chat").disabled = !activeChatSession;
  $("resume-chat").disabled = !activeChatSession;
  $("stop-chat").disabled = !activeChatSession;
  const videoStatus = activeVideoSession?.status || null;
  $("pause-video").disabled = !activeVideoSession || videoStatus === "paused";
  $("resume-video").disabled = !activeVideoSession || videoStatus !== "paused";
  $("stop-video").disabled = !activeVideoSession;
  $("stop-all").disabled = !activeChatSession && !activeVideoSession;
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
    currentEventId = null;
    activeChatSession = null;
    activeVideoSession = null;
    await persistActiveChatSession(null);
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

async function syncCurrentCaptureStatus({ silent = false } = {}) {
  if (!context?.supported || !context?.player_open) {
    currentEventId = null;
    activeChatSession = null;
    activeVideoSession = null;
    await persistActiveChatSession(null);
    render();
    return null;
  }
  const payload = {
    mode: context.mode,
    channel_login: context.channel_login || null,
    video_external_id: context.video_id || null
  };
  try {
    const result = await api("/api/v1/capture/context-status", {
      method: "POST",
      body: JSON.stringify(payload)
    });
    currentEventId = result.event?.id || null;
    activeChatSession = result.active_chat_session || null;
    activeVideoSession = result.active_video_session || null;
    await persistActiveChatSession(activeChatSession);
    render();
    return result;
  } catch (e) {
    if (!silent) showMessage(`Ошибка определения текущего сбора: ${e}`);
    render();
    return null;
  }
}

function modeLine(name, result) {
  if (!result?.requested) return `${name}: не выбран`;
  if (result.result === "started") return `${name}: запущен · ${result.session_id}`;
  if (result.result === "already_active") return `${name}: уже идёт · ${result.session_id}`;
  return `${name}: ошибка · ${result.error || "unknown"}`;
}

$("chat-toggle").addEventListener("change", async (event) => {
  state.captureChatEnabled = Boolean(event.target.checked);
  await chrome.storage.local.set({ captureChatEnabled: state.captureChatEnabled });
  render();
});

$("video-toggle").addEventListener("change", async (event) => {
  state.captureVideoEnabled = Boolean(event.target.checked);
  await chrome.storage.local.set({ captureVideoEnabled: state.captureVideoEnabled });
  render();
});

$("start").addEventListener("click", async () => {
  showMessage("");
  try {
    await syncCurrentCaptureStatus({ silent: true });
    const needChatStart = Boolean(state.captureChatEnabled && !activeChatSession);
    const twitchIntegrity = context.mode === "vod" && needChatStart
      ? await getTwitchIntegrity(activeTabId)
      : null;
    if (context.mode === "vod" && needChatStart && !twitchIntegrity?.client_integrity) {
      throw new Error("Для VOD Chat ещё не пойман Twitch Client-Integrity. Video-only можно запускать без него.");
    }

    const payload = {
      platform: "twitch",
      mode: context.mode,
      channel_login: context.channel_login || null,
      video_external_id: context.video_id || null,
      duration_ms: context.duration_ms || null,
      page_url: context.href,
      player_open: context.player_open,
      metadata: { browser_current_time_ms: context.current_time_ms || null },
      capture: {
        chat: Boolean(state.captureChatEnabled),
        video: Boolean(state.captureVideoEnabled)
      },
      twitch_integrity: twitchIntegrity
    };
    const result = await api("/api/v1/capture/start", { method: "POST", body: JSON.stringify(payload) });
    currentEventId = result.event?.id || currentEventId;
    showMessage(`${modeLine("Chat", result.chat)}\n${modeLine("Video", result.video)}`);
    await syncCurrentCaptureStatus({ silent: true });
  } catch (e) {
    showMessage(`Ошибка Start: ${e}`);
  }
});

$("pause-chat").addEventListener("click", async () => {
  try {
    await syncCurrentCaptureStatus({ silent: true });
    if (!activeChatSession) throw new Error("для текущего события активного Chat нет");
    await api(`/api/v1/collector/sessions/${activeChatSession.id}/pause`, { method: "POST", body: "{}" });
    showMessage("Chat Pause установлен.");
    await syncCurrentCaptureStatus({ silent: true });
  } catch (e) { showMessage(`Ошибка Chat Pause: ${e}`); }
});

$("resume-chat").addEventListener("click", async () => {
  try {
    await syncCurrentCaptureStatus({ silent: true });
    if (!activeChatSession) throw new Error("для текущего события активного Chat нет");
    if (context.mode === "vod" && activeTabId != null) {
      const twitchIntegrity = await getTwitchIntegrity(activeTabId);
      if (twitchIntegrity?.client_integrity) {
        await api(`/api/v1/collector/sessions/${activeChatSession.id}/twitch-integrity`, {
          method: "POST", body: JSON.stringify(twitchIntegrity)
        });
      }
    }
    await api(`/api/v1/collector/sessions/${activeChatSession.id}/resume`, { method: "POST", body: "{}" });
    showMessage("Chat Resume выполнен.");
    await syncCurrentCaptureStatus({ silent: true });
  } catch (e) { showMessage(`Ошибка Chat Resume: ${e}`); }
});

$("stop-chat").addEventListener("click", async () => {
  if (!confirm("Остановить только Chat? Video продолжит запись, если он активен.")) return;
  try {
    await syncCurrentCaptureStatus({ silent: true });
    if (!activeChatSession) {
      showMessage("Активного Chat уже нет.");
      return;
    }
    await api(`/api/v1/collector/sessions/${activeChatSession.id}/stop`, {
      method: "POST", body: JSON.stringify({ reason: "stop_user" })
    });
    showMessage("Chat остановлен. Video не затронут.");
    await syncCurrentCaptureStatus({ silent: true });
  } catch (e) { showMessage(`Ошибка Stop Chat: ${e}`); }
});

$("pause-video").addEventListener("click", async () => {
  try {
    await syncCurrentCaptureStatus({ silent: true });
    if (!activeVideoSession) throw new Error("для текущего события активного Video нет");
    await api(`/api/v1/video-sessions/${activeVideoSession.id}/pause`, { method: "POST", body: "{}" });
    showMessage("Video Pause установлен.");
    await syncCurrentCaptureStatus({ silent: true });
  } catch (e) { showMessage(`Ошибка Video Pause: ${e}`); }
});

$("resume-video").addEventListener("click", async () => {
  try {
    await syncCurrentCaptureStatus({ silent: true });
    if (!activeVideoSession) throw new Error("для текущего события активного Video нет");
    await api(`/api/v1/video-sessions/${activeVideoSession.id}/resume`, { method: "POST", body: "{}" });
    showMessage("Video Resume выполнен.");
    await syncCurrentCaptureStatus({ silent: true });
  } catch (e) { showMessage(`Ошибка Video Resume: ${e}`); }
});

$("stop-video").addEventListener("click", async () => {
  if (!confirm("Остановить только Video? Chat продолжит сбор, если он активен.")) return;
  try {
    await syncCurrentCaptureStatus({ silent: true });
    if (!activeVideoSession) {
      showMessage("Активного Video уже нет.");
      return;
    }
    await api(`/api/v1/video-sessions/${activeVideoSession.id}/stop`, { method: "POST", body: "{}" });
    showMessage("Video остановлен. Chat не затронут.");
    await syncCurrentCaptureStatus({ silent: true });
  } catch (e) { showMessage(`Ошибка Stop Video: ${e}`); }
});

$("stop-all").addEventListener("click", async () => {
  if (!confirm("Остановить Chat и Video текущего события?")) return;
  try {
    await syncCurrentCaptureStatus({ silent: true });
    if (!currentEventId) {
      showMessage("Для текущего события активных записей уже нет.");
      return;
    }
    const result = await api(`/api/v1/events/${currentEventId}/stop-all`, { method: "POST", body: "{}" });
    showMessage(`Stop All: Chat ${result.chat?.stopped || 0}, Video ${result.video?.stopped || 0}`);
    await syncCurrentCaptureStatus({ silent: true });
  } catch (e) { showMessage(`Ошибка Stop All: ${e}`); }
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

(async function init() {
  state = await getState();
  await refreshContext();
  await syncCurrentCaptureStatus();
})();
