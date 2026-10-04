let context = null;
let state = null;
let pollTimer = null;
let activeTabId = null;
let currentEventId = null;
let activeChatSession = null;
let activeVideoSession = null;
let authRequestActive = false;
let actionInFlight = null;

const $ = (id) => document.getElementById(id);

function showMessage(text) {
  const el = $("message");
  el.textContent = text;
  el.classList.toggle("show", Boolean(text));
}

function bindAction(id, handler) {
  $(id).addEventListener("click", async () => {
    if (actionInFlight || authRequestActive) return;
    actionInFlight = id;
    render();
    try {
      await handler();
    } finally {
      actionInFlight = null;
      render();
    }
  });
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

function isTwitchAuthorizationError(value) {
  const text = String(value || "").toLowerCase();
  return [
    "twitch authorization is missing",
    "twitch authorization expired",
    "was revoked; authorize twitch again",
    "authorization lacks user:read:chat",
    "authorize twitch in the extension first",
    "authorize twitch again",
    "twitch irc authentication failed"
  ].some((needle) => text.includes(needle));
}

async function api(path, init = {}, options = {}) {
  const response = await fetch(`${state.apiBaseUrl}${path}`, {
    ...init,
    headers: { "Content-Type": "application/json", ...(init.headers || {}) }
  });
  if (!response.ok) {
    const body = (await response.text()).slice(0, 500);
    if (options.autoAuthorize !== false && isTwitchAuthorizationError(body)) {
      void startTwitchAuthorization({ automatic: true });
    }
    throw new Error(`${response.status}: ${body}`);
  }
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
  const chatWriting = Boolean(activeChatSession && ["arming", "recording", "reconciling"].includes(activeChatSession.status));
  const videoWriting = Boolean(activeVideoSession && ["arming", "recording", "reconnecting"].includes(activeVideoSession.status));
  const partialCapture = chatWriting !== videoWriting;
  const missingCapture = chatWriting ? "Video не пишется" : videoWriting ? "Chat не пишется" : null;
  const statusText = context?.mode === "loading"
    ? "Twitch загружается…"
    : context?.supported
      ? `${context.mode?.toUpperCase()} · player ${context.player_open ? "open" : "closed"}`
      : "Неподдерживаемая страница Twitch";
  $("status").textContent = partialCapture && missingCapture
    ? `${statusText}\n⚠ ${missingCapture}`
    : statusText;
  $("status").classList.toggle("capture-warning", partialCapture);

  const uiBusy = Boolean(actionInFlight || authRequestActive);
  $("controls").disabled = uiBusy;
  $("controls").setAttribute("aria-busy", String(uiBusy));
  document.querySelector("main")?.classList.toggle("is-busy", uiBusy);

  $("chat-toggle").checked = Boolean(activeChatSession || state?.captureChatEnabled);
  $("video-toggle").checked = Boolean(activeVideoSession || state?.captureVideoEnabled);
  $("chat-toggle").disabled = Boolean(activeChatSession);
  $("video-toggle").disabled = Boolean(activeVideoSession);

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
  const chatStatus = activeChatSession?.status || null;
  $("pause-chat").disabled = !activeChatSession || chatStatus === "paused";
  $("resume-chat").disabled = !activeChatSession || chatStatus !== "paused";
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

function sleep(ms) {
  return new Promise((resolve) => window.setTimeout(resolve, ms));
}

async function readTwitchContextDirect(tabId) {
  const results = await chrome.scripting.executeScript({
    target: { tabId },
    func: () => {
      const serviceSegments = new Set([
        "directory", "downloads", "jobs", "p", "settings", "subscriptions", "inventory",
        "search", "wallet", "drops", "friends", "messages", "moderator", "creator-camp"
      ]);
      const url = new URL(location.href);
      let route = { supported: false, mode: "unsupported" };
      if (url.hostname === "www.twitch.tv" || url.hostname === "twitch.tv") {
        const parts = url.pathname.split("/").filter(Boolean);
        if (parts[0] === "videos" && /^\d+$/.test(parts[1] || "")) {
          route = { supported: true, mode: "vod", video_id: parts[1] };
        } else if (parts[0] === "clip" || parts[0] === "clips") {
          route = { supported: false, mode: "unsupported_clip" };
        } else if (parts.length === 1 && !serviceSegments.has(parts[0])) {
          route = { supported: true, mode: "live", channel_login: parts[0].toLowerCase() };
        }
      }

      const videos = [...document.querySelectorAll("video")];
      const visibleVideo = videos.find((video) => {
        const rect = video.getBoundingClientRect();
        return rect.width > 160 && rect.height > 90;
      });
      const player = visibleVideo ? {
        player_open: true,
        current_time_ms: Number.isFinite(visibleVideo.currentTime) ? Math.round(visibleVideo.currentTime * 1000) : null,
        duration_ms: Number.isFinite(visibleVideo.duration) ? Math.round(visibleVideo.duration * 1000) : null,
        paused: visibleVideo.paused
      } : { player_open: false };

      return {
        href: location.href,
        ...route,
        ...player,
        observed_at: new Date().toISOString()
      };
    }
  });
  return results?.[0]?.result || null;
}

async function readTwitchContext(tabId) {
  const delays = [0, 120, 350];
  let lastError = null;
  for (const delay of delays) {
    if (delay) await sleep(delay);
    try {
      const value = await chrome.tabs.sendMessage(tabId, { type: "STREAMHUB_GET_CONTEXT" });
      if (value) return value;
    } catch (error) {
      lastError = error;
    }
    try {
      const value = await readTwitchContextDirect(tabId);
      if (value) return value;
    } catch (error) {
      lastError = error;
    }
  }
  throw lastError || new Error("Не удалось прочитать контекст Twitch");
}

async function refreshContext() {
  const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
  activeTabId = tab?.id ?? null;
  if (!tab?.id || !/^https:\/\/(www\.)?twitch\.tv\//i.test(tab.url || "")) {
    context = { supported: false, mode: "unsupported", player_open: false };
    currentEventId = null;
    activeChatSession = null;
    activeVideoSession = null;
    await persistActiveChatSession(null);
    render();
    return;
  }
  try {
    context = await readTwitchContext(tab.id);
  } catch {
    context = { supported: true, mode: "loading", player_open: false, href: tab.url };
  }
  render();
}

async function syncCurrentCaptureStatus({ silent = false } = {}) {
  if (!context?.supported) {
    currentEventId = null;
    activeChatSession = null;
    activeVideoSession = null;
    await persistActiveChatSession(null);
    render();
    return null;
  }
  if (!context?.player_open || context?.mode === "loading") {
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

bindAction("start", async () => {
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
    const authError = [result.chat?.error, result.video?.error].find(isTwitchAuthorizationError);
    if (authError) void startTwitchAuthorization({ automatic: true });
    await syncCurrentCaptureStatus({ silent: true });
  } catch (e) {
    showMessage(`Ошибка Start: ${e}`);
  }
});

bindAction("pause-chat", async () => {
  try {
    await syncCurrentCaptureStatus({ silent: true });
    if (!activeChatSession) throw new Error("для текущего события активного Chat нет");
    await api(`/api/v1/collector/sessions/${activeChatSession.id}/pause`, { method: "POST", body: "{}" });
    showMessage("Chat Pause установлен.");
    await syncCurrentCaptureStatus({ silent: true });
  } catch (e) { showMessage(`Ошибка Chat Pause: ${e}`); }
});

bindAction("resume-chat", async () => {
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

bindAction("stop-chat", async () => {
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

bindAction("pause-video", async () => {
  try {
    await syncCurrentCaptureStatus({ silent: true });
    if (!activeVideoSession) throw new Error("для текущего события активного Video нет");
    await api(`/api/v1/video-sessions/${activeVideoSession.id}/pause`, { method: "POST", body: "{}" });
    showMessage("Video Pause установлен.");
    await syncCurrentCaptureStatus({ silent: true });
  } catch (e) { showMessage(`Ошибка Video Pause: ${e}`); }
});

bindAction("resume-video", async () => {
  try {
    await syncCurrentCaptureStatus({ silent: true });
    if (!activeVideoSession) throw new Error("для текущего события активного Video нет");
    await api(`/api/v1/video-sessions/${activeVideoSession.id}/resume`, { method: "POST", body: "{}" });
    showMessage("Video Resume выполнен.");
    await syncCurrentCaptureStatus({ silent: true });
  } catch (e) { showMessage(`Ошибка Video Resume: ${e}`); }
});

bindAction("stop-video", async () => {
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

bindAction("stop-all", async () => {
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

async function startTwitchAuthorization({ automatic = false } = {}) {
  if (authRequestActive || (actionInFlight && !automatic)) return;
  authRequestActive = true;
  render();
  try {
    const result = await api(
      "/api/v1/auth/twitch/device/start",
      { method: "POST", body: "{}" },
      { autoAuthorize: false }
    );
    if (result.verification_uri) await chrome.tabs.create({ url: result.verification_uri });
    showMessage(`${automatic ? "⚠ Требуется повторная авторизация Twitch.\n" : ""}Код Twitch: ${result.user_code || "—"}\nОжидаю подтверждение…`);
    clearInterval(pollTimer);
    pollTimer = setInterval(async () => {
      try {
        const status = await api(
          `/api/v1/auth/twitch/device/${result.auth_request_id}`,
          {},
          { autoAuthorize: false }
        );
        if (status.status === "authorized") {
          clearInterval(pollTimer);
          authRequestActive = false;
          render();
          showMessage(`Twitch авторизован: ${status.identity?.login || "ok"}`);
          await syncCurrentCaptureStatus({ silent: true });
        } else if (["failed", "expired"].includes(status.status)) {
          clearInterval(pollTimer);
          authRequestActive = false;
          render();
          showMessage(`Авторизация: ${status.status}${status.error ? ` — ${status.error}` : ""}`);
        }
      } catch (e) {
        clearInterval(pollTimer);
        authRequestActive = false;
        render();
        showMessage(`Ошибка проверки авторизации: ${e}`);
      }
    }, Math.max(3000, Number(result.interval || 5) * 1000));
  } catch (e) {
    authRequestActive = false;
    render();
    showMessage(`Ошибка авторизации: ${e}`);
  }
}

$("auth").addEventListener("click", () => {
  void startTwitchAuthorization();
});

chrome.runtime.onMessage.addListener((message, sender) => {
  if (message?.type !== "STREAMHUB_CONTEXT_CHANGED") return false;
  if (activeTabId == null || Number(sender.tab?.id) !== Number(activeTabId)) return false;
  context = message.context;
  render();
  void syncCurrentCaptureStatus({ silent: true });
  return false;
});

chrome.tabs.onUpdated.addListener((tabId, changeInfo) => {
  if (Number(tabId) !== Number(activeTabId)) return;
  if (!changeInfo.url && changeInfo.status !== "complete") return;
  void refreshContext().then(() => syncCurrentCaptureStatus({ silent: true }));
});

(async function init() {
  state = await getState();
  await refreshContext();
  await syncCurrentCaptureStatus();
})();
