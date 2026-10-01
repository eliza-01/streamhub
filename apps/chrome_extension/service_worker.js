const DEFAULT_STATE = {
  apiBaseUrl: "http://localhost:18741",
  autostart: false,
  activeSessionId: null,
  activeSessionMode: null,
  activeSessionTabId: null
};

const LEGACY_API_BASE_URLS = new Set([
  "http://localhost:8000",
  "http://127.0.0.1:8000"
]);

chrome.runtime.onInstalled.addListener(async () => {
  const current = await chrome.storage.local.get(Object.keys(DEFAULT_STATE));
  const patch = {};
  for (const [key, value] of Object.entries(DEFAULT_STATE)) {
    if (current[key] === undefined) patch[key] = value;
  }
  if (LEGACY_API_BASE_URLS.has(current.apiBaseUrl)) {
    patch.apiBaseUrl = DEFAULT_STATE.apiBaseUrl;
  }
  if (Object.keys(patch).length) await chrome.storage.local.set(patch);
});

function headerMap(requestHeaders = []) {
  const result = {};
  for (const header of requestHeaders) {
    if (!header?.name || typeof header.value !== "string") continue;
    result[header.name.toLowerCase()] = header.value;
  }
  return result;
}

async function rememberIntegrity(tabId, bundle) {
  const current = await chrome.storage.session.get("twitchIntegrityByTab");
  const next = { ...(current.twitchIntegrityByTab || {}), [String(tabId)]: bundle };
  await chrome.storage.session.set({ twitchIntegrityByTab: next });

  // Keep a running VOD job supplied with Twitch's current short-lived web
  // integrity + identity bundle. Client-Integrity can be tied to the browser
  // OAuth identity, so the matching Authorization header must travel with it.
  // The service worker only observes Twitch's own GQL requests; it never
  // modifies them.
  const state = await chrome.storage.local.get([
    "apiBaseUrl", "activeSessionId", "activeSessionMode", "activeSessionTabId"
  ]);
  if (
    state.activeSessionId &&
    state.activeSessionMode === "vod" &&
    Number(state.activeSessionTabId) === Number(tabId)
  ) {
    const apiBaseUrl = state.apiBaseUrl || DEFAULT_STATE.apiBaseUrl;
    try {
      await fetch(`${apiBaseUrl}/api/v1/collector/sessions/${state.activeSessionId}/twitch-integrity`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(bundle)
      });
    } catch {
      // The next Twitch GQL request or an explicit Resume will retry the handoff.
    }
  }
}

chrome.webRequest.onBeforeSendHeaders.addListener(
  (details) => {
    if (details.tabId < 0) return;
    const headers = headerMap(details.requestHeaders);
    const clientIntegrity = headers["client-integrity"];
    const deviceId = headers["x-device-id"] || headers["device-id"];
    if (!clientIntegrity || !deviceId) return;

    void rememberIntegrity(details.tabId, {
      client_integrity: clientIntegrity,
      device_id: deviceId,
      client_id: headers["client-id"] || null,
      client_version: headers["client-version"] || null,
      client_session_id: headers["client-session-id"] || null,
      authorization: headers["authorization"] || null,
      user_agent: headers["user-agent"] || null,
      captured_at: new Date().toISOString()
    });
  },
  { urls: ["https://gql.twitch.tv/*"] },
  ["requestHeaders", "extraHeaders"]
);

chrome.runtime.onMessage.addListener((message, _sender, sendResponse) => {
  if (message?.type === "STREAMHUB_CONTEXT_CHANGED") {
    chrome.storage.session.set({ lastContext: message.context }).catch(() => {});
    return false;
  }

  if (message?.type === "STREAMHUB_GET_TWITCH_INTEGRITY") {
    chrome.storage.session.get("twitchIntegrityByTab").then((stored) => {
      const bundle = stored.twitchIntegrityByTab?.[String(message.tabId)] || null;
      sendResponse(bundle);
    }).catch(() => sendResponse(null));
    return true;
  }

  return false;
});
