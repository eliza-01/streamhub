const SERVICE_SEGMENTS = new Set([
  "directory", "downloads", "jobs", "p", "settings", "subscriptions", "inventory",
  "search", "wallet", "drops", "friends", "messages", "moderator", "creator-camp"
]);

function classifyRoute(urlString = location.href) {
  const url = new URL(urlString);
  if (url.hostname !== "www.twitch.tv" && url.hostname !== "twitch.tv") {
    return { supported: false, mode: "unsupported" };
  }
  const parts = url.pathname.split("/").filter(Boolean);
  if (parts[0] === "videos" && /^\d+$/.test(parts[1] || "")) {
    return { supported: true, mode: "vod", video_id: parts[1] };
  }
  if (parts[0] === "clip" || parts[0] === "clips") {
    return { supported: false, mode: "unsupported_clip" };
  }
  if (parts.length === 1 && !SERVICE_SEGMENTS.has(parts[0])) {
    return { supported: true, mode: "live", channel_login: parts[0].toLowerCase() };
  }
  return { supported: false, mode: "unsupported" };
}

function detectPlayer() {
  const videos = [...document.querySelectorAll("video")];
  const visibleVideo = videos.find((video) => {
    const rect = video.getBoundingClientRect();
    return rect.width > 160 && rect.height > 90;
  });
  if (!visibleVideo) return { player_open: false };
  return {
    player_open: true,
    current_time_ms: Number.isFinite(visibleVideo.currentTime) ? Math.round(visibleVideo.currentTime * 1000) : null,
    duration_ms: Number.isFinite(visibleVideo.duration) ? Math.round(visibleVideo.duration * 1000) : null,
    paused: visibleVideo.paused
  };
}

function getContext() {
  return {
    href: location.href,
    ...classifyRoute(location.href),
    ...detectPlayer(),
    observed_at: new Date().toISOString()
  };
}

chrome.runtime.onMessage.addListener((message, _sender, sendResponse) => {
  if (message?.type === "STREAMHUB_GET_CONTEXT") {
    sendResponse(getContext());
  }
  return false;
});

let lastHref = location.href;
const observer = new MutationObserver(() => {
  if (location.href !== lastHref) {
    lastHref = location.href;
    window.setTimeout(() => {
      chrome.runtime.sendMessage({ type: "STREAMHUB_CONTEXT_CHANGED", context: getContext() });
    }, 500);
  }
});
observer.observe(document.documentElement, { subtree: true, childList: true });
