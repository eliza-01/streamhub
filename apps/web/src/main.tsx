import React, { useEffect, useMemo, useRef, useState } from "react";
import Hls from "hls.js";
import { createRoot } from "react-dom/client";
import "./styles.css";

type Session = {
  id: string;
  event_id?: string;
  media_type: "live" | "vod";
  status: string;
  completeness_status: string;
  channel_login?: string | null;
  title?: string | null;
  created_at: string;
  source_duration_ms?: number | null;
  coverage_end_ms?: number | null;
  deleted_at_utc?: string | null;
  deletion_group_id?: string | null;
  message_count?: number;
  progress_percent?: number | null;
};

type VideoSession = {
  id: string;
  event_id: string;
  status: string;
  completeness_status: string;
  quality: string;
  recorder_mode: string;
  source_url: string;
  recording_started_at_utc?: string | null;
  ended_at_utc?: string | null;
  duration_recorded_ms: number;
  required_start_ms?: number | null;
  required_end_ms?: number | null;
  coverage_start_ms?: number | null;
  coverage_end_ms?: number | null;
  gap_count: number;
  last_error?: string | null;
  last_activity_at_utc?: string | null;
  stop_reason?: string | null;
  metadata?: {
    media_type?: "live" | "vod";
    channel_login?: string | null;
    output_root_key?: string;
    output_subdir?: string;
  } | null;
  created_at: string;
  updated_at: string;
  progress_percent?: number | null;
  segment_count?: number;
  bytes?: number;
  archive_relative_root?: string;
  output_root_key?: string;
  output_subdir?: string;
  storage_summary?: Record<string, { segments: number; bytes: number }>;
  deletion_group_id?: string | null;
  event?: {
    id: string;
    media_type: "live" | "vod";
    channel_login?: string | null;
    channel_display_name?: string | null;
    title?: string | null;
    external_key: string;
  };
};

type VideoPartJob = {
  id: number;
  status: string;
  phase: string;
  attempts: number;
  cancel_requested: boolean;
  progress_bytes: number;
  total_bytes: number;
  last_error?: string | null;
  created_at: string;
  started_at_utc?: string | null;
  completed_at_utc?: string | null;
  heartbeat_at_utc?: string | null;
};

type VideoPart = {
  id: string;
  video_session_id: string;
  part_no: number;
  run_no: number;
  start_segment_no: number;
  end_segment_no: number;
  duration_ms: number;
  expected_bytes: number;
  final_bytes?: number | null;
  sha256?: string | null;
  file_name: string;
  relative_path: string;
  status: string;
  last_error?: string | null;
  created_at: string;
  completed_at_utc?: string | null;
  job?: VideoPartJob | null;
};

type TelegramBinding = {
  part_id: string;
  session_id: string;
  part_no: number;
  file_name: string;
  part_bytes: number;
  status: "linked" | "missing" | "size_mismatch" | "conflict" | "match_pending" | "linked_other_channel" | string;
  channel_id?: number | null;
  message_id?: number | null;
  matched_by?: string | null;
  telegram_bytes?: number | null;
};

type TelegramStatus = {
  ok: boolean;
  configured: boolean;
  session_name?: string;
  session_present?: boolean;
  config_error?: string | null;
  channel?: {
    channel_id: number;
    channel_title?: string | null;
    account_display?: string | null;
    last_scanned_message_id: number;
    last_scan_at_utc?: string | null;
    catalog_files: number;
    bound_parts: number;
    ready_parts: number;
    last_error?: string | null;
  };
  runtime: {
    state: string;
    last_error?: string | null;
    last_sync_at_utc?: string | null;
    last_scanned_messages?: number;
    last_discovered_files?: number;
  };
};

type PlaybackStatus = {
  video_session_id: string;
  playable: boolean;
  playback_error?: string | null;
  linked_segment_count: number;
  first_segment_no?: number | null;
  last_segment_no?: number | null;
  playlist_url: string;
};

type PartPlan = {
  mode: "manual" | "target" | "count";
  from_segment_no: number;
  end_segment_no: number;
  run_no: number;
  segment_count: number;
  expected_bytes: number;
  duration_ms: number;
  target_mib?: number | null;
  requested_segment_count?: number | null;
  warnings: string[];
};

type PartPlanAll = {
  items: PartPlan[];
  part_count: number;
  segment_count: number;
  expected_bytes: number;
  duration_ms: number;
  skipped: string[];
};

type OutputRoot = {
  key: string;
  label: string;
};

type OutputSettings = {
  output_root_key: string;
  output_subdir: string;
  roots: OutputRoot[];
  batch_segments: number;
  applies_to: string;
};

type StorageMigration = {
  id: number;
  status: "queued" | "running" | "completed" | "failed" | string;
  source_root_key: string;
  destination_root_key: string;
  total_sessions: number;
  migrated_sessions: number;
  skipped_sessions: number;
  total_bytes: number;
  copied_bytes: number;
  current_session_id?: string | null;
  last_error?: string | null;
  created_at: string;
  started_at_utc?: string | null;
  completed_at_utc?: string | null;
};

type VideoRun = {
  id: number;
  run_no: number;
  status: string;
  started_at_utc: string;
  ended_at_utc?: string | null;
  resume_source_offset_ms?: number | null;
  first_segment_no?: number | null;
  last_segment_no?: number | null;
  streamlink_exit_code?: number | null;
  ffmpeg_exit_code?: number | null;
  close_reason?: string | null;
  last_error?: string | null;
};

type VideoSegment = {
  id: number;
  segment_no: number;
  video_run_id: number;
  run_no: number;
  file_name: string;
  relative_path: string;
  timeline_start_ms: number;
  timeline_end_ms: number;
  source_media_start_ms?: number | null;
  source_media_end_ms?: number | null;
  duration_ms: number;
  bytes: number;
  storage_state: string;
  integrity_state: string;
  sha256?: string | null;
  archive_attempts: number;
  archive_last_error?: string | null;
  archived_at_utc?: string | null;
  closed_at_utc: string;
};

type SiteCategory = {
  slug: string;
  label: string;
};

type SiteVideoSession = {
  id: string;
  status: string;
  completeness_status: string;
  duration_recorded_ms: number;
  ready_parts: number;
  linked_parts: number;
  playable: boolean;
  playback_url?: string | null;
  created_at: string;
};

type SiteChatSession = {
  id: string;
  status: string;
  completeness_status: string;
  source_duration_ms?: number | null;
  coverage_end_ms?: number | null;
  message_count: number;
  created_at: string;
};

type SiteChatMessage = {
  id: number;
  timeline_offset_ms: number;
  chatter_external_id?: string | null;
  chatter_login?: string | null;
  chatter_name?: string | null;
  color?: string | null;
  badges?: unknown;
  message_text: string;
  fragments?: unknown;
  reply?: unknown;
  bits?: number | null;
  message_type?: string | null;
  is_action?: boolean;
  source_kind?: string | null;
  provider_message_id?: string | null;
  channel_points_reward_id?: string | null;
};

type SiteChatUserSummary = {
  event_id: string;
  chat_session_id?: string | null;
  chatter_external_id?: string | null;
  chatter_login?: string | null;
  message_count: number;
  first_message_ms?: number | null;
  last_message_ms?: number | null;
};

type SiteChatStatsUser = {
  chatter_external_id?: string | null;
  chatter_login?: string | null;
  chatter_name?: string | null;
  message_count: number;
};

type SiteChatStatsPayload = {
  event_id: string;
  chat_session_id?: string | null;
  total_messages: number;
  unique_chatters: number;
  most_active?: SiteChatStatsUser | null;
  least_active?: SiteChatStatsUser | null;
};

type SiteChatBadgeAsset = {
  set_id: string;
  version: string;
  scope?: "global" | "channel" | string;
  image_url_1x?: string | null;
  image_url_2x?: string | null;
  image_url_4x?: string | null;
  title?: string | null;
  description?: string | null;
};

type SiteEvent = {
  id: string;
  platform: string;
  media_type: "live" | "vod";
  external_key: string;
  channel_login?: string | null;
  channel_display_name?: string | null;
  title: string;
  source_started_at_utc?: string | null;
  source_duration_ms?: number | null;
  source_url?: string | null;
  published_at_utc: string;
  categories: Array<SiteCategory & { position?: number }>;
  video_sessions: SiteVideoSession[];
  chat_sessions: SiteChatSession[];
  primary_chat_session_id?: string | null;
  chat_message_count: number;
  has_chat: boolean;
  ready_parts: number;
  linked_parts: number;
  primary_video_session_id?: string | null;
  playback_url?: string | null;
  playable: boolean;
};

type SiteAvailableEvent = {
  id: string;
  media_type: "live" | "vod";
  external_key: string;
  channel_login?: string | null;
  channel_display_name?: string | null;
  title: string;
  source_started_at_utc?: string | null;
  source_duration_ms?: number | null;
  video_sessions: number;
  ready_parts: number;
  linked_parts: number;
};

type MediaEvent = {
  id: string;
  platform: string;
  media_type: "live" | "vod";
  external_key: string;
  channel_login?: string | null;
  channel_display_name?: string | null;
  title?: string | null;
  source_started_at_utc?: string | null;
  source_duration_ms?: number | null;
  created_at: string;
  chat_sessions: Session[];
  video_sessions: VideoSession[];
  chat_sessions_count: number;
  video_sessions_count: number;
  active_chat_sessions_count: number;
  active_video_sessions_count: number;
  has_chat: boolean;
  has_video: boolean;
  metadata?: { identity_state?: string } | null;
  deleted_at_utc?: string | null;
  deletion_group_id?: string | null;
};

type Message = {
  id: number;
  timeline_offset_ms: number;
  chatter_name?: string | null;
  chatter_login?: string | null;
  message_text: string;
};

type CaptureProgress = {
  session_id: string;
  media_type: "live" | "vod";
  status: string;
  completeness_status: string;
  source_duration_ms?: number | null;
  coverage_end_ms?: number | null;
  percent?: number | null;
};

type VideoCaptureProgress = {
  session_id: string;
  status: string;
  completeness_status: string;
  duration_recorded_ms: number;
  coverage_end_ms?: number | null;
  required_end_ms?: number | null;
  gap_count: number;
  last_error?: string | null;
  percent?: number | null;
};

type ViewMode = "events" | "video-manager" | "site" | "trash" | "storage";

const API = import.meta.env.VITE_API_BASE_URL || "http://localhost:18741";
const PROGRESS_POLL_MS = 5000;
const PART_TARGET_MIB_KEY = "streamhub.videoManager.targetMib";
const PART_SEGMENT_COUNT_KEY = "streamhub.videoManager.segmentCount";

function storedPositiveInt(key: string, fallback: number) {
  try {
    const value = Number(window.localStorage.getItem(key));
    return Number.isFinite(value) && value >= 1 ? Math.floor(value) : fallback;
  } catch {
    return fallback;
  }
}
const CAPTURE_BUSY_STATUSES = new Set(["arming", "recording", "paused", "reconciling"]);
const CAPTURE_BUSY_COMPLETENESS = new Set(["collecting", "verifying"]);
const VIDEO_BUSY_STATUSES = new Set(["arming", "recording", "reconnecting"]);

function fmtMs(ms?: number | null) {
  if (ms == null) return "—";
  const sec = Math.floor(ms / 1000);
  const h = Math.floor(sec / 3600);
  const m = Math.floor((sec % 3600) / 60);
  const s = sec % 60;
  return [h, m, s].map((v) => String(v).padStart(2, "0")).join(":");
}

function fmtBytes(bytes?: number | null) {
  if (bytes == null) return "—";
  const units = ["B", "KiB", "MiB", "GiB", "TiB"];
  let value = Math.max(0, bytes);
  let index = 0;
  while (value >= 1024 && index < units.length - 1) {
    value /= 1024;
    index += 1;
  }
  return `${value.toFixed(index === 0 ? 0 : 1)} ${units[index]}`;
}

function isCaptureBusy(session: Session) {
  return CAPTURE_BUSY_STATUSES.has(session.status) || CAPTURE_BUSY_COMPLETENESS.has(session.completeness_status);
}

function isVideoBusy(session: VideoSession) {
  return VIDEO_BUSY_STATUSES.has(session.status);
}

function localProgressPercent(session: Session): number | null {
  if (session.progress_percent != null) return session.progress_percent;
  if (session.completeness_status === "complete" || session.status === "completed") return 100;
  if (session.media_type !== "vod" || !session.source_duration_ms || session.source_duration_ms <= 0) return null;
  const covered = Math.max(0, Math.min(session.coverage_end_ms || 0, session.source_duration_ms));
  return Math.min(99.9, Math.round((covered / session.source_duration_ms) * 1000) / 10);
}

function localVideoProgressPercent(session: VideoSession): number | null {
  if (session.progress_percent != null) return session.progress_percent;
  if (session.completeness_status === "complete") return 100;
  if (session.metadata?.media_type !== "vod" || !session.required_end_ms || session.required_end_ms <= 0) return null;
  const covered = Math.max(0, Math.min(session.coverage_end_ms || 0, session.required_end_ms));
  return Math.min(99.9, Math.round((covered / session.required_end_ms) * 1000) / 10);
}

function chatProgressLabel(session: Session, progress?: CaptureProgress): string {
  const percent = progress?.percent ?? localProgressPercent(session);
  if (percent != null) return `${Math.max(0, Math.min(100, percent)).toFixed(1)}%`;
  return session.media_type === "live" ? "LIVE" : "VOD · …";
}

function videoProgressLabel(session: VideoSession, progress?: VideoCaptureProgress): string {
  const percent = progress?.percent ?? localVideoProgressPercent(session);
  if (percent != null) return `${Math.max(0, Math.min(100, percent)).toFixed(1)}%`;
  return session.metadata?.media_type === "live" ? "LIVE" : "VOD · …";
}

function ProgressBar({ session, progress }: { session: Session; progress?: CaptureProgress }) {
  const percent = progress?.percent ?? localProgressPercent(session);
  const status = progress?.status || session.status;
  if (!isCaptureBusy({ ...session, status, completeness_status: progress?.completeness_status || session.completeness_status })) {
    return null;
  }
  if (percent == null) {
    const label = chatProgressLabel(session, progress);
    return (
      <div className="capture-progress" aria-label={`Chat ${session.media_type.toUpperCase()}: процент пока недоступен`}>
        <div className="capture-progress-head"><span>Chat сбор</span><strong>{label}</strong></div>
        <div className="progress-track indeterminate"><div className="progress-fill" /></div>
      </div>
    );
  }
  const safePercent = Math.max(0, Math.min(100, percent));
  return (
    <div className="capture-progress" aria-label={`Прогресс Chat ${safePercent.toFixed(1)}%`}>
      <div className="capture-progress-head"><span>Chat</span><strong>{safePercent.toFixed(1)}%</strong></div>
      <div className="progress-track"><div className="progress-fill" style={{ width: `${safePercent}%` }} /></div>
    </div>
  );
}

function VideoProgressBar({ session, progress }: { session: VideoSession; progress?: VideoCaptureProgress }) {
  const effective = progress ? { ...session, ...progress } : session;
  if (!isVideoBusy(effective)) return null;
  const percent = progress?.percent ?? localVideoProgressPercent(session);
  if (percent == null) {
    return (
      <div className="capture-progress" aria-label="Video запись идет">
        <div className="capture-progress-head"><span>Video запись</span><strong>{fmtMs(effective.duration_recorded_ms)}</strong></div>
        <div className="progress-track indeterminate"><div className="progress-fill" /></div>
      </div>
    );
  }
  const safePercent = Math.max(0, Math.min(100, percent));
  return (
    <div className="capture-progress" aria-label={`Прогресс Video ${safePercent.toFixed(1)}%`}>
      <div className="capture-progress-head"><span>Video</span><strong>{safePercent.toFixed(1)}% · {fmtMs(effective.duration_recorded_ms)}</strong></div>
      <div className="progress-track"><div className="progress-fill" style={{ width: `${safePercent}%` }} /></div>
    </div>
  );
}

function apiUrl(pathOrUrl: string) {
  if (/^https?:\/\//i.test(pathOrUrl)) return pathOrUrl;
  return `${API}${pathOrUrl.startsWith("/") ? pathOrUrl : `/${pathOrUrl}`}`;
}

function TelegramVideoPlayer({
  playlistUrl,
  externalRef,
}: {
  playlistUrl: string;
  externalRef?: { current: HTMLVideoElement | null };
}) {
  const videoRef = useRef<HTMLVideoElement | null>(null);

  useEffect(() => {
    const video = videoRef.current;
    if (!video) return;
    const source = apiUrl(playlistUrl);
    if (video.canPlayType("application/vnd.apple.mpegurl")) {
      video.src = source;
      return () => { video.removeAttribute("src"); video.load(); };
    }
    if (!Hls.isSupported()) return;
    const hls = new Hls();
    hls.loadSource(source);
    hls.attachMedia(video);
    return () => hls.destroy();
  }, [playlistUrl]);

  return (
    <video
      ref={(node) => {
        videoRef.current = node;
        if (externalRef) externalRef.current = node;
      }}
      className="telegram-player"
      controls
      preload="metadata"
    />
  );
}

function safeChatColor(value?: string | null) {
  return value && /^#[0-9a-f]{6}$/i.test(value) ? value : undefined;
}

type ChatBadgeView = {
  key: string;
  label: string;
  title: string;
  imageUrl?: string;
  imageUrl2x?: string;
  imageUrl4x?: string;
};

function asChatRecord(value: unknown): Record<string, unknown> | null {
  return value && typeof value === "object" && !Array.isArray(value) ? value as Record<string, unknown> : null;
}

function compactBadgeLabel(setId: string) {
  const parts = setId.split(/[^a-z0-9]+/i).filter(Boolean);
  const compact = parts.map((part) => part.slice(0, 3)).join("·").slice(0, 10);
  return (compact || setId.slice(0, 10) || "badge").toUpperCase();
}

function chatBadgeAssetKey(setId: string, version: string) {
  return `${setId}/${version}`;
}

function chatBadges(value: unknown, assets: Record<string, SiteChatBadgeAsset> = {}): ChatBadgeView[] {
  const result: ChatBadgeView[] = [];
  const push = (setIdValue: unknown, versionValue: unknown, uniqueValue?: unknown) => {
    const setId = String(setIdValue || "").trim();
    if (!setId) return;
    const version = String(versionValue || "").trim();
    const asset = assets[chatBadgeAssetKey(setId, version)];
    const fallbackTitle = version ? `${setId} · ${version}` : setId;
    const title = String(asset?.title || fallbackTitle);
    const key = String(uniqueValue || `${setId}/${version}`);
    result.push({
      key,
      label: compactBadgeLabel(setId),
      title,
      imageUrl: asset?.image_url_1x || undefined,
      imageUrl2x: asset?.image_url_2x || undefined,
      imageUrl4x: asset?.image_url_4x || undefined,
    });
  };

  if (Array.isArray(value)) {
    value.forEach((item, index) => {
      if (typeof item === "string") {
        const [setId, ...versionParts] = item.split("/");
        push(setId, versionParts.join("/"), `${item}:${index}`);
        return;
      }
      const record = asChatRecord(item);
      if (!record) return;
      push(record.setID ?? record.setId ?? record.set_id ?? record.name, record.version ?? record.id, record.id ?? index);
    });
  } else {
    const record = asChatRecord(value);
    if (record) Object.entries(record).forEach(([setId, version]) => push(setId, version, setId));
  }
  return result;
}

function chatEmoteId(fragmentValue: unknown) {
  const fragment = asChatRecord(fragmentValue);
  if (!fragment) return null;
  const emote = asChatRecord(fragment.emote);
  const value = emote?.emoteID ?? emote?.emoteId ?? emote?.id ?? fragment.emote_id ?? fragment.emoteID;
  if (value == null) return null;
  const raw = String(value).trim();
  const id = raw.split(";")[0];
  return /^[a-z0-9_-]+$/i.test(id) ? id : null;
}

function chatFragmentText(fragmentValue: unknown) {
  const fragment = asChatRecord(fragmentValue);
  return fragment ? String(fragment.text || "") : "";
}

function twitchEmoteUrl(emoteId: string) {
  return `https://static-cdn.jtvnw.net/emoticons/v2/${encodeURIComponent(emoteId)}/default/dark/1.0`;
}

function chatReplySummary(value: unknown) {
  const reply = asChatRecord(value);
  if (!reply) return null;
  const author = String(
    reply.parent_user_name ?? reply.parent_user_login ?? reply.thread_user_name ?? reply.thread_user_login ?? ""
  ).trim();
  const body = String(reply.parent_message_body ?? reply.parent_message_text ?? reply.parent_message ?? "").trim();
  if (!author && !body) return null;
  return `${author ? `↪ ${author}` : "↪"}${body ? `${author ? ": " : " "}${body}` : ""}`;
}

function ChatMessageFragments({ message }: { message: SiteChatMessage }) {
  const fragments = Array.isArray(message.fragments) ? message.fragments : [];
  if (!fragments.length) return <>{message.message_text}</>;
  return <>
    {fragments.map((fragment, index) => {
      const text = chatFragmentText(fragment);
      const emoteId = chatEmoteId(fragment);
      if (emoteId) {
        return <img
          className="site-chat-emote"
          src={twitchEmoteUrl(emoteId)}
          alt={text}
          title={text}
          loading="lazy"
          key={`${emoteId}:${index}`}
        />;
      }
      return <React.Fragment key={index}>{text}</React.Fragment>;
    })}
  </>;
}

function SiteChatStats({ eventId, hasChat, totalMessages }: { eventId: string; hasChat: boolean; totalMessages: number }) {
  const [stats, setStats] = useState<SiteChatStatsPayload | null>(null);
  const [loading, setLoading] = useState(hasChat);

  useEffect(() => {
    setStats(null);
    setLoading(hasChat);
    if (!hasChat) return;
    const controller = new AbortController();
    void (async () => {
      try {
        const response = await fetch(apiUrl(`/api/v1/site/events/${eventId}/chat/stats`), { cache: "no-store", signal: controller.signal });
        if (!response.ok) throw new Error(await response.text());
        setStats(await response.json() as SiteChatStatsPayload);
      } catch (e) {
        if (!(e instanceof DOMException && e.name === "AbortError")) console.warn("chat stats unavailable", e);
      } finally {
        if (!controller.signal.aborted) setLoading(false);
      }
    })();
    return () => controller.abort();
  }, [eventId, hasChat]);

  const userLabel = (user?: SiteChatStatsUser | null) => user?.chatter_name || user?.chatter_login || "—";
  const value = (text: React.ReactNode) => loading ? <i className="site-inline-spinner" /> : text;

  return (
    <section className="streamvault-chat-stats" aria-label="Статистика чата">
      <div className="streamvault-chat-stats-head">
        <div><span>СТАТИСТИКА ЧАТА</span><strong>Срез всей записи</strong></div>
        <small>Детальный просмотр пользователей — из чата по клику на ник</small>
      </div>
      <div className="streamvault-chat-stats-grid">
        <div><span>Сообщения</span><strong>{totalMessages.toLocaleString("ru-RU")}</strong></div>
        <div><span>Участники</span><strong>{hasChat ? value((stats?.unique_chatters ?? 0).toLocaleString("ru-RU")) : "—"}</strong></div>
        <div className="user"><span>Больше всего</span><strong>{hasChat ? value(userLabel(stats?.most_active)) : "—"}</strong>{!loading && stats?.most_active ? <small>{stats.most_active.message_count.toLocaleString("ru-RU")} сообщений</small> : null}</div>
        <div className="user"><span>Меньше всего</span><strong>{hasChat ? value(userLabel(stats?.least_active)) : "—"}</strong>{!loading && stats?.least_active ? <small>{stats.least_active.message_count.toLocaleString("ru-RU")} сообщений</small> : null}</div>
      </div>
    </section>
  );
}

function SiteReplayChat({
  eventId,
  hasChat,
  messageCount,
  videoRef,
  onMention,
}: {
  eventId: string;
  hasChat: boolean;
  messageCount: number;
  videoRef: { current: HTMLVideoElement | null };
  onMention?: (mention: string) => void;
}) {
  const [messages, setMessages] = useState<SiteChatMessage[]>([]);
  const [badgeAssets, setBadgeAssets] = useState<Record<string, SiteChatBadgeAsset>>({});
  const [currentMs, setCurrentMs] = useState(0);
  const [chatStatus, setChatStatus] = useState(hasChat ? "Загрузка…" : "Чат не записан");
  const [following, setFollowing] = useState(true);
  const [userMenu, setUserMenu] = useState<{ message: SiteChatMessage; x: number; y: number } | null>(null);
  const [userSummary, setUserSummary] = useState<SiteChatUserSummary | null>(null);
  const [userSummaryLoading, setUserSummaryLoading] = useState(false);
  const listRef = useRef<HTMLDivElement | null>(null);
  const menuRef = useRef<HTMLDivElement | null>(null);
  const loadedRangeRef = useRef<{ from: number; to: number } | null>(null);
  const summaryRequestRef = useRef<AbortController | null>(null);

  useEffect(() => {
    setBadgeAssets({});
    if (!hasChat) return;
    let stopped = false;
    const controller = new AbortController();
    void (async () => {
      try {
        const response = await fetch(apiUrl(`/api/v1/site/events/${eventId}/chat/badges`), {
          cache: "no-store",
          signal: controller.signal,
        });
        if (!response.ok) return;
        const payload = await response.json();
        if (stopped) return;
        const next: Record<string, SiteChatBadgeAsset> = {};
        ((payload.badges || []) as SiteChatBadgeAsset[]).forEach((badge) => {
          const setId = String(badge.set_id || "").trim();
          const version = String(badge.version || "").trim();
          if (setId && version) next[chatBadgeAssetKey(setId, version)] = badge;
        });
        setBadgeAssets(next);
      } catch (e) {
        if (!(e instanceof DOMException && e.name === "AbortError")) {
          console.warn("Twitch badge manifest unavailable", e);
        }
      }
    })();
    return () => {
      stopped = true;
      controller.abort();
    };
  }, [eventId, hasChat]);

  useEffect(() => {
    setMessages([]);
    setFollowing(true);
    loadedRangeRef.current = null;
    if (!hasChat) {
      setChatStatus("Чат не записан");
      return;
    }

    let stopped = false;
    let controller: AbortController | null = null;
    let loading = false;

    const syncClock = () => {
      const video = videoRef.current;
      setCurrentMs(Math.max(0, Math.round((video?.currentTime || 0) * 1000)));
    };

    const loadWindow = async (force = false) => {
      if (loading || stopped) return;
      const current = Math.max(0, Math.round((videoRef.current?.currentTime || 0) * 1000));
      const loaded = loadedRangeRef.current;
      if (!force && loaded && current >= loaded.from + 12_000 && current <= loaded.to - 12_000) return;

      const from = Math.max(0, current - 60_000);
      const to = current + 45_000;
      loading = true;
      controller?.abort();
      controller = new AbortController();
      setChatStatus("Синхронизация…");
      try {
        const loadedMessages: SiteChatMessage[] = [];
        let cursor: { time_ms: number; id: number } | null = null;
        do {
          const params = new URLSearchParams({
            from_ms: String(from),
            to_ms: String(to),
            page_size: "1000",
          });
          if (cursor) {
            params.set("after_ms", String(cursor.time_ms));
            params.set("after_id", String(cursor.id));
          }
          const res = await fetch(apiUrl(`/api/v1/site/events/${eventId}/chat/messages?${params.toString()}`), {
            cache: "no-store",
            signal: controller.signal,
          });
          if (!res.ok) throw new Error(await res.text());
          const data = await res.json();
          loadedMessages.push(...((data.messages || []) as SiteChatMessage[]));
          cursor = data.next_cursor || null;
        } while (cursor && !stopped);

        if (stopped) return;
        loadedMessages.sort((a, b) => (a.timeline_offset_ms - b.timeline_offset_ms) || (a.id - b.id));
        setMessages(loadedMessages);
        loadedRangeRef.current = { from, to };
        setChatStatus(`${messageCount.toLocaleString("ru-RU")} сообщений`);
      } catch (e) {
        if (!(e instanceof DOMException && e.name === "AbortError")) {
          setChatStatus(`Ошибка чата: ${String(e)}`);
        }
      } finally {
        loading = false;
      }
    };

    const attach = () => {
      const video = videoRef.current;
      if (!video) return () => {};
      const onSeeked = () => { syncClock(); void loadWindow(true); };
      const onLoaded = () => { syncClock(); void loadWindow(true); };
      video.addEventListener("seeked", onSeeked);
      video.addEventListener("loadedmetadata", onLoaded);
      return () => {
        video.removeEventListener("seeked", onSeeked);
        video.removeEventListener("loadedmetadata", onLoaded);
      };
    };

    syncClock();
    void loadWindow(true);
    const detach = attach();
    const timer = window.setInterval(() => {
      syncClock();
      void loadWindow(false);
    }, 500);

    return () => {
      stopped = true;
      controller?.abort();
      window.clearInterval(timer);
      detach();
    };
  }, [eventId, hasChat, messageCount, videoRef]);

  useEffect(() => () => summaryRequestRef.current?.abort(), []);

  useEffect(() => {
    if (!userMenu) return;
    const onPointerDown = (event: PointerEvent) => {
      if (menuRef.current?.contains(event.target as Node)) return;
      setUserMenu(null);
    };
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") setUserMenu(null);
    };
    const close = () => setUserMenu(null);
    document.addEventListener("pointerdown", onPointerDown);
    window.addEventListener("keydown", onKeyDown);
    window.addEventListener("resize", close);
    return () => {
      document.removeEventListener("pointerdown", onPointerDown);
      window.removeEventListener("keydown", onKeyDown);
      window.removeEventListener("resize", close);
    };
  }, [userMenu]);

  const visible = useMemo(
    () => messages.filter((message) => message.timeline_offset_ms <= currentMs + 100).slice(-500),
    [messages, currentMs]
  );

  useEffect(() => {
    if (!following || !listRef.current) return;
    listRef.current.scrollTop = listRef.current.scrollHeight;
  }, [visible.length, following]);

  function seekTo(message: SiteChatMessage) {
    const video = videoRef.current;
    if (!video) return;
    video.currentTime = Math.max(0, message.timeline_offset_ms / 1000);
    void video.play().catch(() => {});
  }

  function followCurrent() {
    setFollowing(true);
    if (listRef.current) listRef.current.scrollTop = listRef.current.scrollHeight;
  }

  function handleChatScroll() {
    const node = listRef.current;
    if (!node) return;
    const distance = node.scrollHeight - node.scrollTop - node.clientHeight;
    setFollowing(distance < 56);
  }

  async function openUserMenu(event: React.MouseEvent<HTMLElement>, message: SiteChatMessage) {
    event.stopPropagation();
    const width = 286;
    const height = 230;
    const x = Math.max(10, Math.min(event.clientX + 10, window.innerWidth - width - 10));
    const y = Math.max(10, Math.min(event.clientY + 10, window.innerHeight - height - 10));
    setUserMenu({ message, x, y });
    setUserSummary(null);
    setUserSummaryLoading(true);
    summaryRequestRef.current?.abort();
    const controller = new AbortController();
    summaryRequestRef.current = controller;
    try {
      const params = new URLSearchParams();
      if (message.chatter_external_id) params.set("chatter_external_id", message.chatter_external_id);
      else if (message.chatter_login) params.set("chatter_login", message.chatter_login);
      else {
        setUserSummary({ event_id: eventId, message_count: 0 });
        return;
      }
      const response = await fetch(apiUrl(`/api/v1/site/events/${eventId}/chat/user-summary?${params.toString()}`), {
        cache: "no-store",
        signal: controller.signal,
      });
      if (!response.ok) throw new Error(await response.text());
      setUserSummary(await response.json() as SiteChatUserSummary);
    } catch (e) {
      if (!(e instanceof DOMException && e.name === "AbortError")) console.warn("chat user summary unavailable", e);
    } finally {
      if (!controller.signal.aborted) setUserSummaryLoading(false);
    }
  }

  const menuMessage = userMenu?.message;
  const menuName = menuMessage?.chatter_name || menuMessage?.chatter_login || "Гость";
  const menuLogin = menuMessage?.chatter_login || "";
  const menuColor = safeChatColor(menuMessage?.color) || "#ff8a32";
  const menuBadges = menuMessage ? chatBadges(menuMessage.badges, badgeAssets).slice(0, 4) : [];

  return (
    <aside className="site-replay-chat">
      <div className="site-replay-chat-head">
        <div>
          <strong>Чат записи</strong>
          <span>Синхронно с видео</span>
        </div>
        <span>{chatStatus}</span>
      </div>
      <div className="site-replay-chat-list" ref={listRef} onScroll={handleChatScroll}>
        {visible.length === 0 ? (
          <div className="site-replay-chat-empty">{hasChat ? "В этой точке таймлайна сообщений пока нет." : "Для этого события чат не записан."}</div>
        ) : visible.map((message) => {
          const badges = chatBadges(message.badges, badgeAssets);
          const reply = chatReplySummary(message.reply);
          const authorColor = safeChatColor(message.color);
          const specialType = message.message_type && !["message", "action"].includes(message.message_type)
            ? message.message_type
            : null;
          const author = message.chatter_name || message.chatter_login || "Гость";
          return (
            <div className={`site-replay-chat-message${message.is_action ? " is-action" : ""}`} key={message.id}>
              <button type="button" className="site-replay-chat-time" onClick={() => seekTo(message)}>{fmtMs(message.timeline_offset_ms)}</button>
              <div className="site-replay-chat-body">
                {reply ? <div className="site-chat-reply" title={reply}>{reply}</div> : null}
                <div className="site-chat-line">
                  {badges.map((badge) => badge.imageUrl ? (
                    <img
                      className="site-chat-badge-image"
                      src={badge.imageUrl}
                      srcSet={[
                        badge.imageUrl2x ? `${badge.imageUrl2x} 2x` : null,
                        badge.imageUrl4x ? `${badge.imageUrl4x} 4x` : null,
                      ].filter(Boolean).join(", ") || undefined}
                      alt={badge.title}
                      title={badge.title}
                      loading="lazy"
                      key={badge.key}
                    />
                  ) : (
                    <span className="site-chat-badge" title={badge.title} key={badge.key}>{badge.label}</span>
                  ))}
                  {message.channel_points_reward_id ? (
                    <span className="site-chat-reward" title={`Channel points reward · ${message.channel_points_reward_id}`}>★</span>
                  ) : null}
                  <button
                    type="button"
                    className="site-chat-author"
                    style={{ color: authorColor }}
                    onClick={(event) => void openUserMenu(event, message)}
                    title={`Открыть профиль ${author}`}
                  >{author}</button>
                  {message.bits ? <span className="site-chat-bits">{message.bits.toLocaleString("ru-RU")} bits</span> : null}
                  {specialType ? <span className="site-chat-type">{specialType}</span> : null}
                  <span className="site-chat-text" style={message.is_action && authorColor ? { color: authorColor } : undefined}>
                    <ChatMessageFragments message={message} />
                  </span>
                </div>
              </div>
            </div>
          );
        })}
      </div>
      {!following && hasChat ? (
        <button className="site-chat-follow" type="button" onClick={followCurrent}>↓ К текущему моменту</button>
      ) : null}
      <div className="site-replay-chat-foot">Клик по времени — перемотка · клик по нику — профиль и статистика</div>

      {userMenu && menuMessage ? (
        <div
          className="site-chat-user-menu"
          ref={menuRef}
          style={{ left: userMenu.x, top: userMenu.y }}
          role="dialog"
          aria-label={`Профиль ${menuName}`}
        >
          <div className="site-chat-user-head">
            <div className="site-chat-user-avatar" style={{ background: menuColor }}>{menuName.slice(0, 1).toUpperCase()}</div>
            <div className="site-chat-user-identity">
              <strong style={{ color: menuColor }}>{menuName}</strong>
              {menuLogin ? <span>@{menuLogin}</span> : <span>пользователь чата</span>}
              {menuBadges.length > 0 ? (
                <div className="site-chat-user-badges">
                  {menuBadges.map((badge) => badge.imageUrl ? (
                    <img key={badge.key} src={badge.imageUrl} alt={badge.title} title={badge.title} />
                  ) : <span key={badge.key} title={badge.title}>{badge.label}</span>)}
                </div>
              ) : null}
            </div>
          </div>
          <div className="site-chat-user-stat">
            <span>Все сообщения</span>
            <strong aria-label={userSummaryLoading ? "Загружается количество сообщений" : undefined}>
              {userSummaryLoading ? <i className="site-inline-spinner" /> : (userSummary?.message_count ?? "—").toLocaleString("ru-RU")}
            </strong>
          </div>
          {menuLogin ? (
            <a className="site-chat-user-action" href={`https://www.twitch.tv/${encodeURIComponent(menuLogin)}`} target="_blank" rel="noreferrer">
              <span>Посмотреть профиль</span><b>↗</b>
            </a>
          ) : (
            <div className="site-chat-user-action is-disabled"><span>Посмотреть профиль</span><b>↗</b></div>
          )}
          <button
            className="site-chat-user-action"
            type="button"
            onClick={() => {
              onMention?.(`@${menuLogin || menuName}`);
              setUserMenu(null);
            }}
          >
            <span>Отметить в комментарии</span><b>@</b>
          </button>
        </div>
      ) : null}
    </aside>
  );
}

function StreamVaultHeader({
  query,
  onQueryChange,
  onSubmit,
  onBrand,
  onAdmin,
  onPublish,
}: {
  query: string;
  onQueryChange: (value: string) => void;
  onSubmit: () => void;
  onBrand: () => void;
  onAdmin: () => void;
  onPublish?: () => void;
}) {
  return (
    <header className="streamvault-header">
      <div className="streamvault-header-inner">
        <button type="button" className="streamvault-brand" onClick={onBrand} aria-label="StreamVault — главная">
          <span className="streamvault-brand-mark" aria-hidden="true" />
          <span>StreamVault</span>
        </button>
        <label className="streamvault-search">
          <span aria-hidden="true">⌕</span>
          <input
            type="search"
            value={query}
            onChange={(event) => onQueryChange(event.target.value)}
            onKeyDown={(event) => event.key === "Enter" && onSubmit()}
            placeholder="Поиск записей, стримеров, шоу…"
          />
        </label>
        <div className="streamvault-header-actions">
          {onPublish ? <button type="button" className="streamvault-header-button primary" onClick={onPublish}>+ Добавить событие</button> : null}
          <button type="button" className="streamvault-header-button" onClick={onAdmin}>StreamHub</button>
        </div>
      </div>
    </header>
  );
}

function SiteArtwork({ event, wide = false, label }: { event: SiteEvent; wide?: boolean; label?: string }) {
  const primaryCategory = event.categories[0]?.label || event.media_type.toUpperCase();
  return (
    <div className={`streamvault-artwork${wide ? " wide" : ""}`} aria-hidden="true">
      <span>{label || primaryCategory}</span>
      <div><strong>{event.channel_display_name || event.channel_login || "TWITCH"}</strong><small>{event.media_type.toUpperCase()}</small></div>
    </div>
  );
}


function App() {
  const [events, setEvents] = useState<MediaEvent[]>([]);
  const [trashEvents, setTrashEvents] = useState<MediaEvent[]>([]);
  const [view, setView] = useState<ViewMode>("events");
  const [expandedIds, setExpandedIds] = useState<string[]>([]);
  const [selected, setSelected] = useState<Session | null>(null);
  const [selectedVideo, setSelectedVideo] = useState<VideoSession | null>(null);
  const [videoRuns, setVideoRuns] = useState<VideoRun[]>([]);
  const [videoSegments, setVideoSegments] = useState<VideoSegment[]>([]);
  const [videoSessions, setVideoSessions] = useState<VideoSession[]>([]);
  const [videoParts, setVideoParts] = useState<VideoPart[]>([]);
  const [partMode, setPartMode] = useState<"manual" | "target" | "count">("manual");
  const [partFromSegment, setPartFromSegment] = useState(1);
  const [partToSegment, setPartToSegment] = useState(1);
  const [partTargetMib, setPartTargetMib] = useState(() => storedPositiveInt(PART_TARGET_MIB_KEY, 1990));
  const [partSegmentCount, setPartSegmentCount] = useState(() => storedPositiveInt(PART_SEGMENT_COUNT_KEY, 10));
  const [partPlan, setPartPlan] = useState<PartPlan | null>(null);
  const [partPlanAll, setPartPlanAll] = useState<PartPlanAll | null>(null);
  const [partAction, setPartAction] = useState(false);
  const [telegramBindings, setTelegramBindings] = useState<Record<string, TelegramBinding>>({});
  const [telegramStatus, setTelegramStatus] = useState<TelegramStatus | null>(null);
  const [telegramSyncing, setTelegramSyncing] = useState(false);
  const [playbackStatus, setPlaybackStatus] = useState<PlaybackStatus | null>(null);
  const [messages, setMessages] = useState<Message[]>([]);
  const [progressBySession, setProgressBySession] = useState<Record<string, CaptureProgress>>({});
  const [videoProgressBySession, setVideoProgressBySession] = useState<Record<string, VideoCaptureProgress>>({});
  const [position, setPosition] = useState(0);
  const [playing, setPlaying] = useState(false);
  const [seekText, setSeekText] = useState("00:00:00");
  const [error, setError] = useState<string | null>(null);
  const [actionId, setActionId] = useState<string | null>(null);
  const [outputSettings, setOutputSettings] = useState<OutputSettings | null>(null);
  const [outputRootKey, setOutputRootKey] = useState("root1");
  const [outputSubdir, setOutputSubdir] = useState("streamhub");
  const [outputBatchSegments, setOutputBatchSegments] = useState(100);
  const [savingOutput, setSavingOutput] = useState(false);
  const [storageMigrations, setStorageMigrations] = useState<StorageMigration[]>([]);
  const [migrationSourceRoot, setMigrationSourceRoot] = useState("root1");
  const [migrationDestinationRoot, setMigrationDestinationRoot] = useState("root2");
  const [migrationAction, setMigrationAction] = useState(false);
  const [siteEvents, setSiteEvents] = useState<SiteEvent[]>([]);
  const [siteCategories, setSiteCategories] = useState<SiteCategory[]>([]);
  const [siteCategory, setSiteCategory] = useState("all");
  const [siteQuery, setSiteQuery] = useState("");
  const [selectedSiteEvent, setSelectedSiteEvent] = useState<SiteEvent | null>(null);
  const siteVideoRef = useRef<HTMLVideoElement | null>(null);
  const [sitePublisherOpen, setSitePublisherOpen] = useState(false);
  const [siteAvailableEvents, setSiteAvailableEvents] = useState<SiteAvailableEvent[]>([]);
  const [sitePublishEventId, setSitePublishEventId] = useState("");
  const [sitePublishCategories, setSitePublishCategories] = useState<string[]>([]);
  const [siteAction, setSiteAction] = useState(false);
  const [siteHeroFrame, setSiteHeroFrame] = useState(0);
  const [siteCommentDraft, setSiteCommentDraft] = useState("");
  const [siteCommentNotice, setSiteCommentNotice] = useState("");
  const siteCommentRef = useRef<HTMLTextAreaElement | null>(null);

  useEffect(() => {
    try { window.localStorage.setItem(PART_TARGET_MIB_KEY, String(partTargetMib)); } catch { /* browser storage unavailable */ }
  }, [partTargetMib]);

  useEffect(() => {
    try { window.localStorage.setItem(PART_SEGMENT_COUNT_KEY, String(partSegmentCount)); } catch { /* browser storage unavailable */ }
  }, [partSegmentCount]);

  async function loadEvents() {
    try {
      const res = await fetch(`${API}/api/v1/events?page_size=100`, { cache: "no-store" });
      if (!res.ok) throw new Error(await res.text());
      const data = await res.json();
      setEvents(data.items || []);
      setProgressBySession({});
      setVideoProgressBySession({});
      setError(null);
    } catch (e) {
      setError(String(e));
    }
  }

  async function loadVideoSessions() {
    try {
      const res = await fetch(`${API}/api/v1/video-sessions?page_size=200`, { cache: "no-store" });
      if (!res.ok) throw new Error(await res.text());
      const data = await res.json();
      setVideoSessions(data.items || []);
      setError(null);
    } catch (e) {
      setError(String(e));
    }
  }

  async function loadVideoParts(sessionId: string) {
    const res = await fetch(`${API}/api/v1/video-sessions/${sessionId}/parts`, { cache: "no-store" });
    if (!res.ok) throw new Error(await res.text());
    const data = await res.json();
    setVideoParts(data.items || []);
  }

  async function loadTelegramStatus() {
    const res = await fetch(`${API}/api/v1/telegram/status`, { cache: "no-store" });
    if (!res.ok) throw new Error(await res.text());
    setTelegramStatus(await res.json() as TelegramStatus);
  }

  async function loadTelegramBindings(sessionId: string) {
    const res = await fetch(`${API}/api/v1/telegram/bindings?session_id=${encodeURIComponent(sessionId)}`, { cache: "no-store" });
    if (!res.ok) {
      setTelegramBindings({});
      return;
    }
    const data = await res.json() as { bindings?: TelegramBinding[] };
    setTelegramBindings(Object.fromEntries((data.bindings || []).map((item) => [item.part_id, item])));
  }

  async function loadPlaybackStatus(sessionId: string) {
    const res = await fetch(`${API}/api/v1/playback/video-sessions/${sessionId}`, { cache: "no-store" });
    if (!res.ok) {
      setPlaybackStatus(null);
      return;
    }
    setPlaybackStatus(await res.json() as PlaybackStatus);
  }

  async function refreshTelegramForSession(sessionId: string) {
    await Promise.allSettled([
      loadTelegramStatus(),
      loadTelegramBindings(sessionId),
      loadPlaybackStatus(sessionId),
    ]);
  }

  async function syncTelegram() {
    if (!selectedVideo) return;
    setTelegramSyncing(true);
    try {
      const res = await fetch(`${API}/api/v1/telegram/sync`, { method: "POST" });
      if (!res.ok) throw new Error(await res.text());
      await refreshTelegramForSession(selectedVideo.id);
      setError(null);
    } catch (e) {
      setError(String(e));
    } finally {
      setTelegramSyncing(false);
    }
  }

  async function loadOutputSettings() {
    try {
      const res = await fetch(`${API}/api/v1/storage/output-settings`, { cache: "no-store" });
      if (!res.ok) throw new Error(await res.text());
      const data = await res.json() as OutputSettings;
      setOutputSettings(data);
      setOutputRootKey(data.output_root_key);
      setOutputSubdir(data.output_subdir);
      setOutputBatchSegments(data.batch_segments);
      const rootKeys = data.roots.map((root) => root.key);
      setMigrationSourceRoot((current) => rootKeys.includes(current) ? current : (rootKeys[0] || "root1"));
      setMigrationDestinationRoot((current) => {
        if (rootKeys.includes(current) && current !== migrationSourceRoot) return current;
        return rootKeys.find((key) => key !== data.output_root_key) || rootKeys[0] || "root1";
      });
      setError(null);
    } catch (e) {
      setError(String(e));
    }
  }

  async function saveOutputSettings() {
    setSavingOutput(true);
    try {
      const res = await fetch(`${API}/api/v1/storage/output-settings`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          output_root_key: outputRootKey,
          output_subdir: outputSubdir,
          batch_segments: outputBatchSegments,
        }),
      });
      if (!res.ok) throw new Error(await res.text());
      const data = await res.json() as OutputSettings;
      setOutputSettings(data);
      setOutputRootKey(data.output_root_key);
      setOutputSubdir(data.output_subdir);
      setOutputBatchSegments(data.batch_segments);
      setError(null);
    } catch (e) {
      setError(String(e));
    } finally {
      setSavingOutput(false);
    }
  }

  async function startStorageMigration() {
    if (migrationSourceRoot === migrationDestinationRoot) {
      setError("Для миграции выберите разные output roots.");
      return;
    }
    if (!window.confirm(`Перенести завершённые Video sessions ${migrationSourceRoot} → ${migrationDestinationRoot}? Активные и ещё не archive_ready sessions будут пропущены.`)) return;
    setMigrationAction(true);
    try {
      const res = await fetch(`${API}/api/v1/storage/migrations`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          source_root_key: migrationSourceRoot,
          destination_root_key: migrationDestinationRoot,
        }),
      });
      if (!res.ok) throw new Error(await res.text());
      await loadStorageMigrations();
      setError(null);
    } catch (e) {
      setError(String(e));
    } finally {
      setMigrationAction(false);
    }
  }

  async function retryStorageMigration(job: StorageMigration) {
    setMigrationAction(true);
    try {
      const res = await fetch(`${API}/api/v1/storage/migrations/${job.id}/retry`, { method: "POST" });
      if (!res.ok) throw new Error(await res.text());
      await loadStorageMigrations();
      setError(null);
    } catch (e) {
      setError(String(e));
    } finally {
      setMigrationAction(false);
    }
  }

  async function loadStorageMigrations() {
    try {
      const res = await fetch(`${API}/api/v1/storage/migrations?limit=20`, { cache: "no-store" });
      if (!res.ok) throw new Error(await res.text());
      const data = await res.json();
      setStorageMigrations(data.items || []);
      setError(null);
    } catch (e) {
      setError(String(e));
    }
  }

  async function loadSiteFeed(category = siteCategory, query = siteQuery) {
    try {
      const params = new URLSearchParams();
      if (category && category !== "all") params.set("category", category);
      if (query.trim()) params.set("q", query.trim());
      const res = await fetch(`${API}/api/v1/site/feed?${params.toString()}`, { cache: "no-store" });
      if (!res.ok) throw new Error(await res.text());
      const data = await res.json();
      setSiteEvents(data.items || []);
      setSiteCategories(data.categories || []);
      setError(null);
    } catch (e) {
      setError(String(e));
    }
  }

  function openSiteEvent(event: SiteEvent) {
    setSelectedSiteEvent(event);
    setSiteCommentDraft("");
    setSiteCommentNotice("");
    window.scrollTo({ top: 0, behavior: "auto" });
  }

  function mentionSiteComment(mention: string) {
    setSiteCommentDraft((current) => {
      const trimmed = current.trimStart();
      return trimmed ? `${current}${current.endsWith(" ") ? "" : " "}${mention} ` : `${mention} `;
    });
    setSiteCommentNotice("");
    window.setTimeout(() => {
      siteCommentRef.current?.scrollIntoView({ behavior: "smooth", block: "center" });
      siteCommentRef.current?.focus();
    }, 0);
  }

  async function openSitePublisher() {
    setSiteAction(true);
    try {
      const [eventsRes, categoriesRes] = await Promise.all([
        fetch(`${API}/api/v1/site/admin/available-events`, { cache: "no-store" }),
        fetch(`${API}/api/v1/site/categories`, { cache: "no-store" }),
      ]);
      if (!eventsRes.ok) throw new Error(await eventsRes.text());
      if (!categoriesRes.ok) throw new Error(await categoriesRes.text());
      const [eventsData, categoriesData] = await Promise.all([eventsRes.json(), categoriesRes.json()]);
      const available = (eventsData.items || []) as SiteAvailableEvent[];
      const categories = (categoriesData.items || []) as SiteCategory[];
      setSiteAvailableEvents(available);
      setSiteCategories(categories);
      setSitePublishEventId(available[0]?.id || "");
      setSitePublishCategories(categories[0]?.slug ? [categories[0].slug] : []);
      setSitePublisherOpen(true);
      setError(null);
    } catch (e) {
      setError(String(e));
    } finally {
      setSiteAction(false);
    }
  }

  function toggleSitePublishCategory(slug: string) {
    setSitePublishCategories((current) => {
      if (current.includes(slug)) return current.filter((item) => item !== slug);
      if (current.length >= 3) return current;
      return [...current, slug];
    });
  }

  async function publishSiteEvent() {
    if (!sitePublishEventId || sitePublishCategories.length === 0) return;
    setSiteAction(true);
    try {
      const res = await fetch(`${API}/api/v1/site/admin/events`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ event_id: sitePublishEventId, categories: sitePublishCategories }),
      });
      if (!res.ok) throw new Error(await res.text());
      setSitePublisherOpen(false);
      await loadSiteFeed(siteCategory, siteQuery);
      setError(null);
    } catch (e) {
      setError(String(e));
    } finally {
      setSiteAction(false);
    }
  }

  async function loadTrash() {
    try {
      const res = await fetch(`${API}/api/v1/deleted/events?page_size=100`, { cache: "no-store" });
      if (!res.ok) throw new Error(await res.text());
      const data = await res.json();
      setTrashEvents(data.items || []);
      setError(null);
    } catch (e) {
      setError(String(e));
    }
  }

  async function loadCurrentView(targetView: ViewMode = view) {
    if (targetView === "events") await loadEvents();
    else if (targetView === "video-manager") await loadVideoSessions();
    else if (targetView === "site") await loadSiteFeed();
    else if (targetView === "storage") await Promise.all([loadOutputSettings(), loadStorageMigrations()]);
    else await loadTrash();
  }

  async function openSession(session: Session) {
    setSelectedVideo(null);
    setSelected(session);
    setPosition(0);
    setPlaying(false);
    try {
      const res = await fetch(`${API}/api/v1/sessions/${session.id}/messages?from_ms=0&limit=2000`);
      if (!res.ok) throw new Error(await res.text());
      const data = await res.json();
      setMessages(data.items || []);
      setError(null);
    } catch (e) {
      setError(String(e));
    }
  }

  async function openVideoSession(session: VideoSession) {
    setSelected(null);
    setSelectedVideo(session);
    setPartPlan(null);
    setPartPlanAll(null);
    try {
      const [detailRes, runsRes, segmentsRes, partsRes] = await Promise.all([
        fetch(`${API}/api/v1/video-sessions/${session.id}`, { cache: "no-store" }),
        fetch(`${API}/api/v1/video-sessions/${session.id}/runs`, { cache: "no-store" }),
        fetch(`${API}/api/v1/video-sessions/${session.id}/segments?page_size=500`, { cache: "no-store" }),
        fetch(`${API}/api/v1/video-sessions/${session.id}/parts`, { cache: "no-store" }),
      ]);
      if (!detailRes.ok) throw new Error(await detailRes.text());
      if (!runsRes.ok) throw new Error(await runsRes.text());
      if (!segmentsRes.ok) throw new Error(await segmentsRes.text());
      if (!partsRes.ok) throw new Error(await partsRes.text());
      const [detail, runs, segments, parts] = await Promise.all([detailRes.json(), runsRes.json(), segmentsRes.json(), partsRes.json()]);
      const loadedSegments = (segments.items || []) as VideoSegment[];
      const loadedParts = (parts.items || []) as VideoPart[];
      setSelectedVideo(detail);
      setVideoRuns(runs.items || []);
      setVideoSegments(loadedSegments);
      setVideoParts(loadedParts);
      const reserved = new Set<number>();
      loadedParts.forEach((part) => {
        for (let no = part.start_segment_no; no <= part.end_segment_no; no += 1) reserved.add(no);
      });
      const next = loadedSegments.find((segment) => segment.storage_state === "archive_ready" && segment.integrity_state === "hashed" && !reserved.has(segment.segment_no))?.segment_no || loadedSegments[0]?.segment_no || 1;
      setPartFromSegment(next);
      setPartToSegment(next);
      await refreshTelegramForSession(session.id);
      setError(null);
    } catch (e) {
      setError(String(e));
    }
  }

  function partRequestBody() {
    if (partMode === "manual") {
      return { mode: "manual", from_segment_no: partFromSegment, to_segment_no: partToSegment };
    }
    if (partMode === "target") {
      return { mode: "target", from_segment_no: partFromSegment, target_mib: partTargetMib };
    }
    return { mode: "count", from_segment_no: partFromSegment, segment_count: partSegmentCount };
  }

  function clearPartPlans() {
    setPartPlan(null);
    setPartPlanAll(null);
  }

  function nextBuildableSegment(after: number, parts: VideoPart[]) {
    const reserved = new Set<number>();
    parts.forEach((part) => {
      for (let no = part.start_segment_no; no <= part.end_segment_no; no += 1) reserved.add(no);
    });
    return videoSegments.find((segment) => (
      segment.segment_no > after
      && segment.storage_state === "archive_ready"
      && segment.integrity_state === "hashed"
      && !reserved.has(segment.segment_no)
    ))?.segment_no || after + 1;
  }

  function advancePartEditor(after: number, createdParts: VideoPart[]) {
    const next = nextBuildableSegment(after, createdParts);
    const manualSpan = Math.max(1, partToSegment - partFromSegment + 1);
    setPartFromSegment(next);
    if (partMode === "manual") setPartToSegment(next + manualSpan - 1);
    clearPartPlans();
  }

  async function previewPart() {
    if (!selectedVideo) return;
    setPartAction(true);
    try {
      const res = await fetch(`${API}/api/v1/video-sessions/${selectedVideo.id}/parts/plan`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(partRequestBody()),
      });
      if (!res.ok) throw new Error(await res.text());
      setPartPlan(await res.json());
      setPartPlanAll(null);
      setError(null);
    } catch (e) {
      setPartPlan(null);
      setError(String(e));
    } finally {
      setPartAction(false);
    }
  }

  async function previewAllParts() {
    if (!selectedVideo) return;
    setPartAction(true);
    try {
      const res = await fetch(`${API}/api/v1/video-sessions/${selectedVideo.id}/parts/plan-all`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(partRequestBody()),
      });
      if (!res.ok) throw new Error(await res.text());
      setPartPlanAll(await res.json());
      setPartPlan(null);
      setError(null);
    } catch (e) {
      setPartPlanAll(null);
      setError(String(e));
    } finally {
      setPartAction(false);
    }
  }

  async function buildPart() {
    if (!selectedVideo) return;
    setPartAction(true);
    try {
      const res = await fetch(`${API}/api/v1/video-sessions/${selectedVideo.id}/parts`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(partRequestBody()),
      });
      if (!res.ok) throw new Error(await res.text());
      const created = await res.json() as VideoPart & { plan?: PartPlan; builder_wakeup_error?: string | null };
      const merged = [...videoParts, created];
      await loadVideoParts(selectedVideo.id);
      advancePartEditor(created.plan?.end_segment_no || created.end_segment_no, merged);
      setError(created.builder_wakeup_error ? `Part поставлен в durable queue, но wakeup builder не прошёл: ${created.builder_wakeup_error}` : null);
    } catch (e) {
      setError(String(e));
    } finally {
      setPartAction(false);
    }
  }

  async function buildAllParts() {
    if (!selectedVideo || !partPlanAll) return;
    setPartAction(true);
    try {
      const res = await fetch(`${API}/api/v1/video-sessions/${selectedVideo.id}/parts/build-all`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(partRequestBody()),
      });
      if (!res.ok) throw new Error(await res.text());
      const result = await res.json() as { items: (VideoPart & { plan?: PartPlan })[]; builder_wakeup_error?: string | null };
      const merged = [...videoParts, ...result.items];
      await loadVideoParts(selectedVideo.id);
      const last = result.items[result.items.length - 1];
      if (last) advancePartEditor(last.end_segment_no, merged);
      else clearPartPlans();
      setError(result.builder_wakeup_error ? `Parts поставлены в durable queue, но wakeup builder не прошёл: ${result.builder_wakeup_error}` : null);
    } catch (e) {
      setError(String(e));
    } finally {
      setPartAction(false);
    }
  }

  async function partActionRequest(part: VideoPart, action: "retry" | "cancel" | "delete") {
    if (!selectedVideo) return;
    if (action === "delete" && !window.confirm(`Удалить part #${part.part_no}? Source segments останутся.`)) return;
    setPartAction(true);
    try {
      const res = await fetch(`${API}/api/v1/video-parts/${part.id}${action === "delete" ? "" : `/${action}`}`, { method: action === "delete" ? "DELETE" : "POST" });
      if (!res.ok) throw new Error(await res.text());
      await loadVideoParts(selectedVideo.id);
      setError(null);
    } catch (e) {
      setError(String(e));
    } finally {
      setPartAction(false);
    }
  }

  async function copyPartPath(part: VideoPart) {
    try {
      await navigator.clipboard.writeText(part.relative_path);
      setError(null);
    } catch {
      setError(`Path: ${part.relative_path}`);
    }
  }

  async function moveToTrash(session: Session) {
    if (isCaptureBusy(session)) {
      setError("Идущую или приостановленную запись нельзя удалить: сначала остановите сбор.");
      return;
    }
    if (!window.confirm("Переместить эту Chat session в корзину?")) return;
    setActionId(session.id);
    try {
      const res = await fetch(`${API}/api/v1/sessions/${session.id}`, { method: "DELETE" });
      if (!res.ok) throw new Error(await res.text());
      setEvents((current) => current
        .map((event) => ({
          ...event,
          chat_sessions: event.chat_sessions.filter((item) => item.id !== session.id),
          chat_sessions_count: event.chat_sessions.filter((item) => item.id !== session.id).length,
        }))
        .filter((event) => event.chat_sessions_count > 0 || event.video_sessions_count > 0));
      if (selected?.id === session.id) setSelected(null);
      setError(null);
    } catch (e) {
      setError(String(e));
    } finally {
      setActionId(null);
    }
  }

  async function restoreSession(session: Session) {
    setActionId(session.id);
    try {
      const res = await fetch(`${API}/api/v1/sessions/${session.id}/restore`, { method: "POST" });
      if (!res.ok) throw new Error(await res.text());
      await loadTrash();
      if (selected?.id === session.id) setSelected(null);
      setError(null);
    } catch (e) {
      setError(String(e));
    } finally {
      setActionId(null);
    }
  }

  async function purgeSession(session: Session) {
    if (!window.confirm("Удалить сессию безвозвратно вместе со всеми сообщениями, событиями и журналом этой сессии?")) return;
    setActionId(session.id);
    try {
      const res = await fetch(`${API}/api/v1/deleted/sessions/${session.id}?permanent=true`, { method: "DELETE" });
      if (!res.ok) throw new Error(await res.text());
      await loadTrash();
      if (selected?.id === session.id) setSelected(null);
      setError(null);
    } catch (e) {
      setError(String(e));
    } finally {
      setActionId(null);
    }
  }

  async function moveVideoToTrash(session: VideoSession) {
    if (isVideoBusy(session)) {
      setError("Идущую Video запись нельзя удалить: сначала остановите её.");
      return;
    }
    if (!window.confirm("Переместить эту Video session в корзину? Файлы останутся на output до permanent purge.")) return;
    setActionId(session.id);
    try {
      const res = await fetch(`${API}/api/v1/video-sessions/${session.id}`, { method: "DELETE" });
      if (!res.ok) throw new Error(await res.text());
      if (selectedVideo?.id === session.id) setSelectedVideo(null);
      if (view === "video-manager") await loadVideoSessions();
      else await loadEvents();
      setError(null);
    } catch (e) {
      setError(String(e));
    } finally {
      setActionId(null);
    }
  }

  async function restoreVideoSession(session: VideoSession) {
    setActionId(session.id);
    try {
      const res = await fetch(`${API}/api/v1/video-sessions/${session.id}/restore`, { method: "POST" });
      if (!res.ok) throw new Error(await res.text());
      await loadTrash();
      setError(null);
    } catch (e) {
      setError(String(e));
    } finally {
      setActionId(null);
    }
  }

  async function purgeVideoSession(session: VideoSession) {
    if (!window.confirm("Удалить Video session навсегда? Будут удалены DB записи и весь каталог этой Video session на output.")) return;
    setActionId(session.id);
    try {
      const res = await fetch(`${API}/api/v1/deleted/video-sessions/${session.id}?permanent=true`, { method: "DELETE" });
      if (!res.ok) throw new Error(await res.text());
      await loadTrash();
      setError(null);
    } catch (e) {
      setError(String(e));
    } finally {
      setActionId(null);
    }
  }

  async function moveEventToTrash(event: MediaEvent) {
    if (event.active_chat_sessions_count > 0 || event.active_video_sessions_count > 0) {
      setError("Нельзя удалить Event с активным Chat или Video capture. Сначала остановите запись.");
      return;
    }
    if (!window.confirm("Переместить в корзину все текущие Chat и Video sessions этого Event? Сам уникальный Event остаётся группирующей сущностью.")) return;
    setActionId(event.id);
    try {
      const res = await fetch(`${API}/api/v1/events/${event.id}`, { method: "DELETE" });
      if (!res.ok) throw new Error(await res.text());
      setEvents((current) => current.filter((item) => item.id !== event.id));
      setExpandedIds((current) => current.filter((id) => id !== event.id));
      setError(null);
    } catch (e) {
      setError(String(e));
    } finally {
      setActionId(null);
    }
  }

  async function restoreEvent(event: MediaEvent) {
    setActionId(event.id);
    try {
      const res = await fetch(`${API}/api/v1/events/${event.id}/restore`, { method: "POST" });
      if (!res.ok) throw new Error(await res.text());
      await loadTrash();
      setError(null);
    } catch (e) {
      setError(String(e));
    } finally {
      setActionId(null);
    }
  }

  async function purgeEvent(event: MediaEvent) {
    if (!window.confirm("Удалить навсегда все sessions этого Event, которые сейчас находятся в корзине? Текущие sessions в Events и сам уникальный Event не затрагиваются.")) return;
    setActionId(event.id);
    try {
      const res = await fetch(`${API}/api/v1/deleted/events/${event.id}?permanent=true`, { method: "DELETE" });
      if (!res.ok) throw new Error(await res.text());
      await loadTrash();
      setError(null);
    } catch (e) {
      setError(String(e));
    } finally {
      setActionId(null);
    }
  }

  function toggleEvent(eventId: string) {
    setExpandedIds((current) => current.includes(eventId)
      ? current.filter((id) => id !== eventId)
      : [...current, eventId]);
  }

  useEffect(() => {
    loadCurrentView(view);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [view]);

  const activeStorageMigration = storageMigrations.find((job) => job.status === "queued" || job.status === "running");

  useEffect(() => {
    if (view !== "storage" || !activeStorageMigration) return;
    const timer = window.setInterval(() => {
      loadStorageMigrations();
    }, 3000);
    return () => window.clearInterval(timer);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [view, activeStorageMigration?.id, activeStorageMigration?.status]);

  const activePartBuild = videoParts.some((part) => ["queued", "waiting_capture_idle", "building", "verifying", "suspended_for_capture"].includes(part.status));

  useEffect(() => {
    if (!selectedVideo || !activePartBuild) return;
    const timer = window.setInterval(() => {
      loadVideoParts(selectedVideo.id).catch((e) => console.warn("part queue poll failed", e));
    }, 1500);
    return () => window.clearInterval(timer);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selectedVideo?.id, activePartBuild]);

  useEffect(() => {
    if (!selectedVideo) return;
    const timer = window.setInterval(() => {
      refreshTelegramForSession(selectedVideo.id).catch((e) => console.warn("telegram status poll failed", e));
    }, 5000);
    return () => window.clearInterval(timer);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selectedVideo?.id]);

  const monitoredChatIds = useMemo(
    () => view === "events"
      ? events.flatMap((event) => event.chat_sessions).filter(isCaptureBusy).map((session) => session.id)
      : [],
    [events, view]
  );
  const monitoredVideoIds = useMemo(
    () => view === "events"
      ? events.flatMap((event) => event.video_sessions).filter(isVideoBusy).map((session) => session.id)
      : [],
    [events, view]
  );
  const monitoredKey = `c:${monitoredChatIds.join(",")}|v:${monitoredVideoIds.join(",")}`;

  useEffect(() => {
    if (monitoredChatIds.length === 0 && monitoredVideoIds.length === 0) return;

    let cancelled = false;
    let timer: number | undefined;
    let inFlight: AbortController | null = null;
    const chatIds = [...monitoredChatIds];
    const videoIds = [...monitoredVideoIds];

    const schedule = () => {
      if (!cancelled) timer = window.setTimeout(poll, PROGRESS_POLL_MS);
    };

    const poll = async () => {
      if (cancelled) return;
      if (document.visibilityState !== "visible") {
        schedule();
        return;
      }

      inFlight = new AbortController();
      try {
        const params = new URLSearchParams();
        chatIds.forEach((id) => params.append("chat_session_id", id));
        videoIds.forEach((id) => params.append("video_session_id", id));
        const res = await fetch(`${API}/api/v1/capture/progress?${params.toString()}`, {
          signal: inFlight.signal,
          cache: "no-store",
        });
        if (!res.ok) throw new Error(await res.text());
        const data = await res.json();
        if (cancelled) return;

        const chatUpdates = (data.chat || []) as CaptureProgress[];
        const videoUpdates = (data.video || []) as VideoCaptureProgress[];
        const chatById = Object.fromEntries(chatUpdates.map((item) => [item.session_id, item]));
        const videoById = Object.fromEntries(videoUpdates.map((item) => [item.session_id, item]));
        setProgressBySession((current) => ({ ...current, ...chatById }));
        setVideoProgressBySession((current) => ({ ...current, ...videoById }));

        setEvents((current) => current.map((event) => ({
          ...event,
          chat_sessions: event.chat_sessions.map((session) => {
            const update = chatById[session.id];
            return update ? {
              ...session,
              status: update.status,
              completeness_status: update.completeness_status,
              source_duration_ms: update.source_duration_ms,
              coverage_end_ms: update.coverage_end_ms,
              progress_percent: update.percent,
            } : session;
          }),
          video_sessions: event.video_sessions.map((session) => {
            const update = videoById[session.id];
            return update ? {
              ...session,
              status: update.status,
              completeness_status: update.completeness_status,
              duration_recorded_ms: update.duration_recorded_ms,
              coverage_end_ms: update.coverage_end_ms,
              required_end_ms: update.required_end_ms,
              gap_count: update.gap_count,
              last_error: update.last_error,
              progress_percent: update.percent,
            } : session;
          }),
        })));
        setSelected((current) => {
          if (!current) return current;
          const update = chatById[current.id];
          return update ? { ...current, ...update, progress_percent: update.percent } : current;
        });
        setSelectedVideo((current) => {
          if (!current) return current;
          const update = videoById[current.id];
          return update ? { ...current, ...update, progress_percent: update.percent } : current;
        });
      } catch (e) {
        if (!(e instanceof DOMException && e.name === "AbortError")) {
          console.warn("capture progress poll failed", e);
        }
      } finally {
        inFlight = null;
        schedule();
      }
    };

    timer = window.setTimeout(poll, 1000);
    return () => {
      cancelled = true;
      if (timer !== undefined) window.clearTimeout(timer);
      inFlight?.abort();
    };
    // arrays are encoded in monitoredKey so polling restarts only when active ids change.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [monitoredKey]);

  useEffect(() => {
    if (!playing || !selected) return;
    const timer = window.setInterval(() => {
      setPosition((p) => {
        const max = selected.source_duration_ms || Math.max(0, ...messages.map((m) => m.timeline_offset_ms));
        const next = p + 250;
        if (next >= max) {
          setPlaying(false);
          return max;
        }
        return next;
      });
    }, 250);
    return () => window.clearInterval(timer);
  }, [playing, selected, messages]);

  function seekByText() {
    const parts = seekText.trim().split(":").map(Number);
    if (parts.some((v) => !Number.isFinite(v) || v < 0) || (parts.length !== 2 && parts.length !== 3)) return;
    const [h, m, sec] = parts.length === 3 ? parts : [0, parts[0], parts[1]];
    setPosition((h * 3600 + m * 60 + sec) * 1000);
  }

  const visible = useMemo(
    () => messages.filter((m) => m.timeline_offset_ms <= position).slice(-200),
    [messages, position]
  );

  if (selectedSiteEvent) {
    const eventDate = selectedSiteEvent.source_started_at_utc
      ? new Date(selectedSiteEvent.source_started_at_utc).toLocaleString("ru-RU")
      : "Дата неизвестна";
    return (
      <div className="streamvault-app">
        <StreamVaultHeader
          query={siteQuery}
          onQueryChange={setSiteQuery}
          onSubmit={() => { setSelectedSiteEvent(null); void loadSiteFeed(siteCategory, siteQuery); }}
          onBrand={() => setSelectedSiteEvent(null)}
          onAdmin={() => { setSelectedSiteEvent(null); setView("events"); }}
        />
        <main className="streamvault-watch-shell">
          <button className="streamvault-back" type="button" onClick={() => setSelectedSiteEvent(null)}>← К записям</button>
          <div className="streamvault-watch-layout">
            <div className="streamvault-watch-primary">
              <div className="streamvault-player">
                {selectedSiteEvent.playable && selectedSiteEvent.playback_url ? (
                  <TelegramVideoPlayer playlistUrl={selectedSiteEvent.playback_url} externalRef={siteVideoRef} />
                ) : (
                  <div className="streamvault-video-placeholder">Видео пока недоступно: нет связанного Telegram playback range.</div>
                )}
              </div>
              <section className="streamvault-watch-meta">
                <div className="streamvault-watch-title-row">
                  <div>
                    <h1>{selectedSiteEvent.title}</h1>
                    <div className="streamvault-watch-byline">
                      <strong>{selectedSiteEvent.channel_display_name || selectedSiteEvent.channel_login || "Twitch"}</strong>
                      <span>{eventDate}</span>
                      {selectedSiteEvent.source_duration_ms ? <span>{fmtMs(selectedSiteEvent.source_duration_ms)}</span> : null}
                    </div>
                  </div>
                  <div className="streamvault-badges">
                    {selectedSiteEvent.categories.map((category) => <span key={category.slug}>{category.label}</span>)}
                  </div>
                </div>
                <SiteChatStats
                  eventId={selectedSiteEvent.id}
                  hasChat={selectedSiteEvent.has_chat}
                  totalMessages={selectedSiteEvent.chat_message_count || 0}
                />
              </section>

              <section className="streamvault-comments" aria-labelledby="streamvaultCommentsTitle">
                <div className="streamvault-comments-head">
                  <div>
                    <h2 id="streamvaultCommentsTitle">Комментарии</h2>
                    <p>UI v1 · пользовательские комментарии подключим после утверждения интерфейса.</p>
                  </div>
                </div>
                <div className="streamvault-comment-composer">
                  <div className="streamvault-comment-avatar">SV</div>
                  <div>
                    <textarea
                      ref={siteCommentRef}
                      value={siteCommentDraft}
                      onChange={(event) => { setSiteCommentDraft(event.target.value); setSiteCommentNotice(""); }}
                      rows={3}
                      placeholder="Написать комментарий…"
                    />
                    <div className="streamvault-comment-actions">
                      <span>{siteCommentNotice}</span>
                      <button
                        type="button"
                        disabled={!siteCommentDraft.trim()}
                        onClick={() => setSiteCommentNotice("Сохранение комментариев подключим на backend-этапе.")}
                      >Отправить</button>
                    </div>
                  </div>
                </div>
              </section>
            </div>
            <SiteReplayChat
              eventId={selectedSiteEvent.id}
              hasChat={selectedSiteEvent.has_chat}
              messageCount={selectedSiteEvent.chat_message_count || 0}
              videoRef={siteVideoRef}
              onMention={mentionSiteComment}
            />
          </div>
        </main>
      </div>
    );
  }

  if (view === "site") {
    const latest = siteEvents[0] || null;
    return (
      <div className="streamvault-app">
        <StreamVaultHeader
          query={siteQuery}
          onQueryChange={setSiteQuery}
          onSubmit={() => void loadSiteFeed(siteCategory, siteQuery)}
          onBrand={() => { setSiteCategory("all"); setSiteQuery(""); void loadSiteFeed("all", ""); }}
          onAdmin={() => setView("events")}
          onPublish={() => void openSitePublisher()}
        />

        <nav className="streamvault-mobile-categories" aria-label="Категории">
          <button className={siteCategory === "all" ? "active" : ""} onClick={() => { setSiteCategory("all"); void loadSiteFeed("all", siteQuery); }}>Все</button>
          {siteCategories.map((category) => (
            <button key={category.slug} className={siteCategory === category.slug ? "active" : ""} onClick={() => { setSiteCategory(category.slug); void loadSiteFeed(category.slug, siteQuery); }}>{category.label}</button>
          ))}
        </nav>

        <div className="streamvault-home-shell">
          <aside className="streamvault-sidebar">
            <nav aria-label="Категории видео">
              <button className={siteCategory === "all" ? "active" : ""} onClick={() => { setSiteCategory("all"); void loadSiteFeed("all", siteQuery); }}><span>⌂</span>Все видео</button>
              {siteCategories.map((category) => (
                <button key={category.slug} className={siteCategory === category.slug ? "active" : ""} onClick={() => { setSiteCategory(category.slug); void loadSiteFeed(category.slug, siteQuery); }}><span>•</span>{category.label}</button>
              ))}
            </nav>
          </aside>

          <main className="streamvault-content">
            {error ? <div className="streamvault-error">{error}</div> : null}
            {latest ? (
              <section className="streamvault-hero" aria-labelledby="streamvaultLatestTitle">
                <h1>Последний стрим</h1>
                <div className="streamvault-latest-layout">
                  <button type="button" className="streamvault-latest-cover" onClick={() => openSiteEvent(latest)} aria-label={`Открыть ${latest.title}`}>
                    <SiteArtwork event={latest} label="COVER" />
                  </button>
                  <div className="streamvault-preview-stage">
                    <button type="button" className="streamvault-preview-main" onClick={() => openSiteEvent(latest)}>
                      <SiteArtwork event={latest} wide label={`PREVIEW ${siteHeroFrame + 1}`} />
                    </button>
                    <div className="streamvault-preview-rail" aria-label="Четыре preview-кадра">
                      {[0, 1, 2, 3].map((frame) => (
                        <button key={frame} type="button" className={siteHeroFrame === frame ? "active" : ""} onClick={() => setSiteHeroFrame(frame)}>
                          <SiteArtwork event={latest} wide label={`#${frame + 1}`} />
                        </button>
                      ))}
                    </div>
                  </div>
                </div>
                <div className="streamvault-latest-copy">
                  <div>
                    <h2 id="streamvaultLatestTitle">{latest.title}</h2>
                    <p>{latest.channel_display_name || latest.channel_login || "Twitch"} · {latest.source_started_at_utc ? new Date(latest.source_started_at_utc).toLocaleString("ru-RU") : "Дата неизвестна"}</p>
                  </div>
                  <div className="streamvault-badges">{latest.categories.map((category) => <span key={category.slug}>{category.label}</span>)}</div>
                </div>
              </section>
            ) : null}

            <section className="streamvault-recordings" aria-labelledby="streamvaultRecordingsTitle">
              <div className="streamvault-section-head">
                <div>
                  <h2 id="streamvaultRecordingsTitle">{siteCategory === "all" ? "Записи" : siteCategories.find((category) => category.slug === siteCategory)?.label || "Записи"}</h2>
                  {siteQuery.trim() ? <p>Поиск: «{siteQuery.trim()}»</p> : null}
                </div>
                <span>{siteEvents.length.toLocaleString("ru-RU")}</span>
              </div>
              {siteEvents.length === 0 ? (
                <div className="streamvault-empty">Пока нет опубликованных записей в этом разделе.</div>
              ) : (
                <div className="streamvault-recording-grid">
                  {siteEvents.map((event) => (
                    <button type="button" className="streamvault-recording-card" key={event.id} onClick={() => openSiteEvent(event)}>
                      <div className="streamvault-recording-cover"><SiteArtwork event={event} label="COVER" /></div>
                      <div className="streamvault-recording-body">
                        <h3>{event.title}</h3>
                        <p>{event.source_started_at_utc ? new Date(event.source_started_at_utc).toLocaleDateString("ru-RU") : "Дата неизвестна"} · {(event.chat_message_count || 0).toLocaleString("ru-RU")} сообщений</p>
                        <div className="streamvault-badges">{event.categories.slice(0, 3).map((category) => <span key={category.slug}>{category.label}</span>)}</div>
                      </div>
                    </button>
                  ))}
                </div>
              )}
            </section>
          </main>
        </div>

        {sitePublisherOpen && (
          <div className="site-modal-backdrop" role="presentation" onMouseDown={(event) => { if (event.target === event.currentTarget) setSitePublisherOpen(false); }}>
            <div className="site-modal" role="dialog" aria-modal="true" aria-labelledby="sitePublisherTitle">
              <div className="row">
                <div><div className="section-label">SITE PUBLICATION</div><h2 id="sitePublisherTitle">Добавить событие</h2></div>
                <button onClick={() => setSitePublisherOpen(false)}>Закрыть</button>
              </div>
              {siteAvailableEvents.length === 0 ? (
                <div className="site-empty compact">Нет доступных Events: событие должно иметь ready part, связанный с Telegram, и ещё не быть опубликовано.</div>
              ) : (
                <>
                  <label className="storage-field site-modal-field">
                    <span>Событие из хранилища</span>
                    <select value={sitePublishEventId} onChange={(event) => setSitePublishEventId(event.target.value)}>
                      {siteAvailableEvents.map((event) => <option key={event.id} value={event.id}>{event.title} · {event.channel_display_name || event.channel_login || event.media_type} · TG {event.linked_parts}/{event.ready_parts}</option>)}
                    </select>
                  </label>
                  <div className="site-publish-categories"><span>Категории · до 3</span><div>
                    {siteCategories.map((category) => (
                      <label key={category.slug} className={sitePublishCategories.includes(category.slug) ? "selected" : ""}>
                        <input type="checkbox" checked={sitePublishCategories.includes(category.slug)} disabled={!sitePublishCategories.includes(category.slug) && sitePublishCategories.length >= 3} onChange={() => toggleSitePublishCategory(category.slug)} />
                        {category.label}
                      </label>
                    ))}
                  </div></div>
                  <div className="actions site-modal-actions"><button disabled={siteAction || !sitePublishEventId || sitePublishCategories.length === 0} onClick={publishSiteEvent}>{siteAction ? "Добавляю…" : "Добавить на сайт"}</button></div>
                </>
              )}
            </div>
          </div>
        )}
      </div>
    );
  }

  if (selectedVideo) {
    const progress = videoProgressBySession[selectedVideo.id];
    const archiveReady = selectedVideo.storage_summary?.archive_ready?.segments || 0;
    const spoolPending = (selectedVideo.storage_summary?.spool?.segments || 0) + (selectedVideo.storage_summary?.copying?.segments || 0);
    return (
      <main>
        <button className="link" onClick={() => setSelectedVideo(null)}>← {view === "video-manager" ? "Video Manager" : "Events"}</button>
        <header className="detail-header">
          <div>
            <h1>Video Manager · output</h1>
            <p className="muted">{selectedVideo.metadata?.media_type?.toUpperCase() || "VIDEO"} · {selectedVideo.status} · {selectedVideo.completeness_status}</p>
          </div>
          <div className="actions">
            <button onClick={() => openVideoSession(selectedVideo)}>Обновить</button>
            <button className="danger" disabled={actionId === selectedVideo.id || isVideoBusy(selectedVideo)} onClick={() => moveVideoToTrash(selectedVideo)}>В корзину</button>
          </div>
        </header>
        <VideoProgressBar session={selectedVideo} progress={progress} />
        {selectedVideo.last_error && <div className="error">{selectedVideo.last_error}</div>}
        <section className="video-overview">
          <div><span>Записано</span><strong>{fmtMs(selectedVideo.duration_recorded_ms)}</strong></div>
          <div><span>Сегментов</span><strong>{selectedVideo.segment_count || videoSegments.length}</strong></div>
          <div><span>Размер</span><strong>{fmtBytes(selectedVideo.bytes || videoSegments.reduce((sum, item) => sum + item.bytes, 0))}</strong></div>
          <div><span>Output ready</span><strong>{archiveReady}</strong></div>
          <div><span>Spool backlog</span><strong>{spoolPending}</strong></div>
          <div><span>Gaps</span><strong>{selectedVideo.gap_count}</strong></div>
        </section>
        {selectedVideo.archive_relative_root && (
          <p className="muted small archive-root">Output: <code>{selectedVideo.archive_relative_root}</code></p>
        )}
        <section className="video-block">
          <div className="section-label">RUNS</div>
          {videoRuns.length === 0 ? <div className="empty-child">Run ещё не создан</div> : (
            <div className="table-wrap"><table><thead><tr><th>#</th><th>Status</th><th>Resume</th><th>Segments</th><th>Exit</th><th>Reason</th></tr></thead><tbody>
              {videoRuns.map((run) => <tr key={run.id}>
                <td>{run.run_no}</td><td>{run.status}</td><td>{fmtMs(run.resume_source_offset_ms)}</td>
                <td>{run.first_segment_no ?? "—"}–{run.last_segment_no ?? "—"}</td>
                <td>{run.streamlink_exit_code ?? "—"}/{run.ffmpeg_exit_code ?? "—"}</td><td>{run.close_reason || "—"}</td>
              </tr>)}
            </tbody></table></div>
          )}
        </section>
        <section className="video-block">
          <div className="section-label">BUILD PART</div>
          <p className="muted small">Part — точная byte-for-byte склейка archive_ready MPEG-TS segments одного run. Range резервируется в БД до build. Во время активной Video записи queue ждёт capture idle.</p>
          <div className="part-mode-tabs">
            <button className={partMode === "manual" ? "active-tab" : ""} onClick={() => { setPartMode("manual"); clearPartPlans(); }}>Manual range</button>
            <button className={partMode === "target" ? "active-tab" : ""} onClick={() => { setPartMode("target"); clearPartPlans(); }}>Target MiB</button>
            <button className={partMode === "count" ? "active-tab" : ""} onClick={() => { setPartMode("count"); clearPartPlans(); }}>Segment count</button>
          </div>
          <div className="part-controls">
            <label className="storage-field"><span>From segment</span><input type="number" min={1} value={partFromSegment} onChange={(e) => { const value = Math.max(1, Number(e.target.value) || 1); setPartFromSegment(value); if (partMode === "manual" && partToSegment < value) setPartToSegment(value); clearPartPlans(); }} /></label>
            {partMode === "manual" ? (
              <label className="storage-field"><span>To segment</span><input type="number" min={partFromSegment} value={partToSegment} onChange={(e) => { setPartToSegment(Math.max(partFromSegment, Number(e.target.value) || partFromSegment)); clearPartPlans(); }} /></label>
            ) : partMode === "target" ? (
              <label className="storage-field"><span>Target MiB · сохраняется</span><input type="number" min={1} value={partTargetMib} onChange={(e) => { setPartTargetMib(Math.max(1, Number(e.target.value) || 1)); clearPartPlans(); }} /></label>
            ) : (
              <label className="storage-field"><span>Segments per Part · сохраняется</span><input type="number" min={1} max={10000} value={partSegmentCount} onChange={(e) => { setPartSegmentCount(Math.max(1, Number(e.target.value) || 1)); clearPartPlans(); }} /></label>
            )}
          </div>
          {partMode === "target" && <p className="muted small">Стандарт: 1990 MiB. Последнее введённое значение сохраняется в браузере.</p>}
          {partMode === "manual" && <p className="muted small">После Build следующий диапазон сохраняет ту же длину: например 1–10 → 11–20.</p>}
          {partMode === "count" && <p className="muted small">Фиксированное число сегментов на Part; Preview показывает ожидаемый размер.</p>}
          <div className="actions part-actions">
            <button disabled={partAction} onClick={previewPart}>Preview</button>
            <button disabled={partAction || !partPlan} onClick={buildPart}>{partAction ? "Работаю…" : "Build Part"}</button>
            <button disabled={partAction} onClick={previewAllParts}>Preview All</button>
            <button disabled={partAction || !partPlanAll} onClick={buildAllParts}>{partAction ? "Работаю…" : "Build All"}</button>
          </div>
          {partPlan && (
            <div className="part-plan">
              <div><span>Range</span><strong>#{partPlan.from_segment_no}–#{partPlan.end_segment_no}</strong></div>
              <div><span>Run</span><strong>{partPlan.run_no}</strong></div>
              <div><span>Segments</span><strong>{partPlan.segment_count}</strong></div>
              <div><span>Duration</span><strong>{fmtMs(partPlan.duration_ms)}</strong></div>
              <div><span>Expected</span><strong>{fmtBytes(partPlan.expected_bytes)}</strong></div>
              {partPlan.warnings.length > 0 && <div className="part-plan-warning"><span>Warnings</span><strong>{partPlan.warnings.join(", ")}</strong></div>}
            </div>
          )}
          {partPlanAll && (
            <div className="part-plan-all">
              <div className="part-plan">
                <div><span>Parts</span><strong>{partPlanAll.part_count}</strong></div>
                <div><span>Segments</span><strong>{partPlanAll.segment_count}</strong></div>
                <div><span>Total duration</span><strong>{fmtMs(partPlanAll.duration_ms)}</strong></div>
                <div><span>Total expected</span><strong>{fmtBytes(partPlanAll.expected_bytes)}</strong></div>
                <div><span>Skipped</span><strong>{partPlanAll.skipped.length}</strong></div>
              </div>
              <div className="part-plan-all-list">
                {partPlanAll.items.map((plan, index) => (
                  <div className="part-plan-all-row" key={`${plan.run_no}-${plan.from_segment_no}-${plan.end_segment_no}`}>
                    <strong>#{index + 1} · seg {plan.from_segment_no}–{plan.end_segment_no}</strong>
                    <span>run {plan.run_no} · {plan.segment_count} seg · {fmtMs(plan.duration_ms)} · {fmtBytes(plan.expected_bytes)}</span>
                  </div>
                ))}
              </div>
              {partPlanAll.skipped.length > 0 && <div className="muted small">Skipped: {partPlanAll.skipped.join(", ")}</div>}
            </div>
          )}
        </section>
        <section className="video-block telegram-storage-block">
          <div className="row telegram-storage-head">
            <div>
              <div className="section-label">TELEGRAM STORAGE</div>
              <div className="muted small">Read-only user session · parts are uploaded manually as Telegram documents.</div>
            </div>
            <button disabled={telegramSyncing || !telegramStatus?.configured} onClick={syncTelegram}>{telegramSyncing ? "Syncing…" : "Telegram sync"}</button>
          </div>
          {!telegramStatus?.configured ? (
            <div className="inline-error small">Telegram не настроен: {telegramStatus?.config_error || "заполните .env и выполните telegram-auth.ps1"}</div>
          ) : (
            <div className="telegram-storage-summary">
              <div><span>Runtime</span><strong>{telegramStatus.runtime.state}</strong></div>
              <div><span>Session</span><strong>{telegramStatus.session_present ? telegramStatus.session_name : "missing"}</strong></div>
              <div><span>Channel</span><strong>{telegramStatus.channel?.channel_title || telegramStatus.channel?.channel_id || "—"}</strong></div>
              <div><span>Catalog / linked</span><strong>{telegramStatus.channel?.catalog_files || 0} / {telegramStatus.channel?.bound_parts || 0}</strong></div>
            </div>
          )}
          {playbackStatus?.playable ? (
            <div className="muted small telegram-playback-status">
              Telegram playback ready · segments {playbackStatus.first_segment_no}–{playbackStatus.last_segment_no} · {playbackStatus.linked_segment_count} linked
            </div>
          ) : playbackStatus?.playback_error ? (
            <div className="muted small">Playback: {playbackStatus.playback_error}</div>
          ) : null}
        </section>
        <section className="video-block">
          <div className="section-label">PARTS / BUILD QUEUE</div>
          {videoParts.length === 0 ? <div className="empty-child">Parts ещё не создавались</div> : (
            <div className="part-list">
              {videoParts.map((part) => {
                const job = part.job;
                const telegram = telegramBindings[part.id];
                const progressTotal = job?.total_bytes || part.expected_bytes || 1;
                const progressBytes = job?.progress_bytes || (part.status === "ready" ? (part.final_bytes || part.expected_bytes) : 0);
                const progressPercent = Math.max(0, Math.min(100, Math.round((progressBytes / progressTotal) * 100)));
                return (
                  <div className="part-row" key={part.id}>
                    <div className="row"><strong>Part #{part.part_no} · seg {part.start_segment_no}–{part.end_segment_no}</strong><span className={`part-status part-status-${part.status}`}>{part.status}</span></div>
                    <div className="muted small">run {part.run_no} · {fmtMs(part.duration_ms)} · {fmtBytes(part.final_bytes ?? part.expected_bytes)} · attempts {job?.attempts || 0}</div>
                    {["queued", "waiting_capture_idle", "building", "verifying", "suspended_for_capture"].includes(part.status) && (
                      <>
                        <div className="progress-track part-progress"><div className="progress-fill" style={{ width: `${progressPercent}%` }} /></div>
                        <div className="muted small">{job?.phase || part.status} · {fmtBytes(progressBytes)} / {fmtBytes(progressTotal)}{part.status === "waiting_capture_idle" || part.status === "suspended_for_capture" ? " · ожидает завершения Video capture" : ""}</div>
                      </>
                    )}
                    {part.sha256 && <div className="muted small hash-line">sha256 <code>{part.sha256}</code></div>}
                    <div className="muted small path-cell"><code>{part.relative_path}</code></div>
                    {part.status === "ready" && (
                      <div className={`telegram-part-status telegram-part-status-${telegram?.status || "missing"}`}>
                        Telegram: <strong>{telegram?.status || "missing"}</strong>
                        {telegram?.message_id != null && <> · message <code>{telegram.message_id}</code></>}
                        {telegram?.matched_by && <> · {telegram.matched_by}</>}
                      </div>
                    )}
                    {(part.last_error || job?.last_error) && <div className="inline-error small">{part.last_error || job?.last_error}</div>}
                    <div className="actions part-row-actions">
                      {(part.status === "failed" || part.status === "cancelled") && <button disabled={partAction} onClick={() => partActionRequest(part, "retry")}>Retry</button>}
                      {["queued", "waiting_capture_idle", "building", "verifying", "suspended_for_capture"].includes(part.status) && <button disabled={partAction} onClick={() => partActionRequest(part, "cancel")}>Cancel</button>}
                      <button onClick={() => copyPartPath(part)}>Copy path</button>
                      {["ready", "failed", "cancelled"].includes(part.status) && <button className="danger subtle" disabled={partAction} onClick={() => partActionRequest(part, "delete")}>Delete</button>}
                    </div>
                  </div>
                );
              })}
            </div>
          )}
        </section>
        <section className="video-block">
          <div className="section-label">SEGMENTS · spool → output</div>
          <p className="muted small">Закрытый segment сначала живёт в Docker spool. Backend переносит закрытые segments пачками в физический output. Spool очищается только после проверки всей пачки и DB commit.</p>
          {videoSegments.length === 0 ? <div className="empty-child">Закрытых сегментов пока нет</div> : (
            <div className="table-wrap"><table><thead><tr><th>Segment</th><th>Run</th><th>Timeline</th><th>Duration</th><th>Bytes</th><th>Storage</th><th>Integrity</th><th>Archived</th><th>Path / error</th></tr></thead><tbody>
              {videoSegments.map((segment, index) => <tr key={segment.id} className={index > 0 && videoSegments[index - 1].run_no !== segment.run_no ? "run-boundary" : ""}>
                <td><button className="segment-picker" onClick={() => { setPartFromSegment(segment.segment_no); setPartToSegment(segment.segment_no); setPartPlan(null); }}>#{segment.segment_no}</button></td><td>{segment.run_no}</td>
                <td>{fmtMs(segment.timeline_start_ms)}–{fmtMs(segment.timeline_end_ms)}</td>
                <td>{fmtMs(segment.duration_ms)}</td><td>{fmtBytes(segment.bytes)}</td>
                <td>{segment.storage_state}</td><td>{segment.integrity_state}</td>
                <td>{segment.archived_at_utc ? new Date(segment.archived_at_utc).toLocaleString() : "—"}</td>
                <td className="path-cell">{segment.archive_last_error ? <span className="inline-error">{segment.archive_last_error}</span> : <code>{segment.relative_path}</code>}</td>
              </tr>)}
            </tbody></table></div>
          )}
        </section>
      </main>
    );
  }

  if (selected) {
    const total = selected.source_duration_ms || Math.max(1, ...messages.map((m) => m.timeline_offset_ms));
    const isTrash = view === "trash";
    return (
      <main>
        <button className="link" onClick={() => setSelected(null)}>← {isTrash ? "Корзина" : "Events"}</button>
        <header className="detail-header">
          <div>
            <h1>{selected.channel_login || "Twitch"}</h1>
            <p className="muted">{selected.title || "Без названия"} · {selected.media_type.toUpperCase()} · {selected.completeness_status}</p>
          </div>
          <div className="actions">
            {isTrash ? (
              <>
                <button disabled={actionId === selected.id} onClick={() => restoreSession(selected)}>Восстановить</button>
                <button className="danger" disabled={actionId === selected.id} onClick={() => purgeSession(selected)}>Удалить навсегда</button>
              </>
            ) : (
              <button className="danger" disabled={actionId === selected.id || isCaptureBusy(selected)} onClick={() => moveToTrash(selected)}>В корзину</button>
            )}
          </div>
        </header>
        {!isTrash && <ProgressBar session={selected} progress={progressBySession[selected.id]} />}
        <section className="player-controls">
          <button onClick={() => setPlaying(true)}>Play</button>
          <button onClick={() => setPlaying(false)}>Pause</button>
          <button onClick={() => { setPlaying(false); setPosition(0); }}>Stop</button>
          <input min={0} max={Math.max(1, total)} type="range" value={Math.min(position, total)} onChange={(e) => setPosition(Number(e.target.value))} />
          <input value={seekText} onChange={(e) => setSeekText(e.target.value)} onKeyDown={(e) => e.key === "Enter" && seekByText()} aria-label="HH:MM:SS seek" />
          <button onClick={seekByText}>Seek</button>
          <strong>{fmtMs(position)} / {fmtMs(total)}</strong>
        </section>
        <section className="chat">
          {visible.map((m) => <div key={m.id} className="message"><span className="time">{fmtMs(m.timeline_offset_ms)}</span><b>{m.chatter_name || m.chatter_login || "unknown"}</b><span>{m.message_text}</span></div>)}
        </section>
      </main>
    );
  }

  return (
    <main>
      <header>
        <div>
          <h1>StreamHub</h1>
          <p className="muted">{view === "trash" ? "Корзина sessions · сгруппировано по уникальному Event" : view === "video-manager" ? "Video sessions · Segments · Parts · Build Queue" : view === "site" ? "Публичный каталог · категории · опубликованные Events" : view === "storage" ? "Video output · spool batches · migration" : "Twitch Events · Chat + Video sessions"}</p>
        </div>
        <div className="actions">
          <button className={view === "events" ? "active-tab" : ""} onClick={() => setView("events")}>Events</button>
          <button className={view === "video-manager" ? "active-tab" : ""} onClick={() => setView("video-manager")}>Video Manager</button>
          <button className={view === "site" ? "active-tab" : ""} onClick={() => setView("site")}>Сайт</button>
          <button className={view === "trash" ? "active-tab" : ""} onClick={() => setView("trash")}>Корзина</button>
          <button className={view === "storage" ? "active-tab" : ""} onClick={() => setView("storage")}>Хранилище</button>
          <button onClick={() => loadCurrentView(view)}>Обновить</button>
        </div>
      </header>
      {error && <div className="error">{error}</div>}

      {view === "events" ? (
        <>
          {events.length === 0 && <div className="empty">Events пока нет</div>}
          <section className="events-list">
            {events.map((event) => {
              const expanded = expandedIds.includes(event.id);
              const activeChat = event.chat_sessions.find(isCaptureBusy);
              const activeVideo = event.video_sessions.find(isVideoBusy);
              return (
                <article className="event-card" key={event.id}>
                  <button className="event-summary" onClick={() => toggleEvent(event.id)} aria-expanded={expanded}>
                    <div className="event-chevron">{expanded ? "▼" : "▶"}</div>
                    <div className="event-main">
                      <div className="row"><strong>{event.channel_display_name || event.channel_login || "Twitch"}</strong><span className="event-type">{event.media_type.toUpperCase()}</span></div>
                      <div className="event-title">{event.title || (event.media_type === "vod" ? `VOD ${event.external_key.split(":").pop()}` : "LIVE stream")}</div>
                      <div className="row muted event-meta"><span>{new Date(event.source_started_at_utc || event.created_at).toLocaleString()}</span><span>{event.media_type === "vod" ? fmtMs(event.source_duration_ms) : "LIVE"}</span></div>
                    </div>
                    <div className="event-badges">
                      <span>Chat: {event.chat_sessions_count}</span>
                      <span>Video: {event.video_sessions_count}</span>
                      {event.metadata?.identity_state && event.metadata.identity_state !== "canonical" && <span>legacy identity</span>}
                      {activeChat && <span className="active-badge">chat {chatProgressLabel(activeChat, progressBySession[activeChat.id])}</span>}
                      {activeVideo && <span className="active-badge">video {videoProgressLabel(activeVideo, videoProgressBySession[activeVideo.id])}</span>}
                    </div>
                  </button>

                  {expanded && (
                    <div className="event-children">
                      <div className="event-delete-row">
                        <span className="muted small">Event — постоянная Twitch-идентичность. Кнопка отправляет в корзину только текущие видимые sessions; старые удалённые sessions остаются в корзине отдельно от новых записей.</span>
                        <button
                          className="danger subtle"
                          disabled={actionId === event.id || event.active_chat_sessions_count > 0 || event.active_video_sessions_count > 0}
                          title={(event.active_chat_sessions_count > 0 || event.active_video_sessions_count > 0) ? "Сначала остановите Chat и Video capture" : "Переместить текущие sessions Event в корзину"}
                          onClick={() => moveEventToTrash(event)}
                        >
                          В корзину все sessions
                        </button>
                      </div>
                      <div className="section-label">CHAT SESSIONS</div>
                      {event.chat_sessions.length === 0 ? <div className="empty-child">Chat sessions нет</div> : event.chat_sessions.map((session, index) => (
                        <div className="session-row" key={session.id}>
                          <div className="session-number">#{index + 1}</div>
                          <div className="session-info">
                            <div className="session-status-line"><strong>{session.status}</strong><span>{session.completeness_status}</span><span>{(session.message_count || 0).toLocaleString()} сообщений</span></div>
                            <div className="muted session-date">{new Date(session.created_at).toLocaleString()}</div>
                            <ProgressBar session={session} progress={progressBySession[session.id]} />
                          </div>
                          <div className="session-actions">
                            <button onClick={() => openSession(session)}>Открыть</button>
                            <button className="danger subtle" disabled={actionId === session.id || isCaptureBusy(session)} title={isCaptureBusy(session) ? "Сначала остановите сбор" : "Переместить в корзину"} onClick={() => moveToTrash(session)}>В корзину</button>
                          </div>
                        </div>
                      ))}

                      <div className="section-label video-section-label">VIDEO SESSIONS</div>
                      {event.video_sessions.length === 0 ? <div className="empty-child">Video sessions нет</div> : event.video_sessions.map((session, index) => (
                        <div className="session-row video-session-row" key={session.id}>
                          <div className="session-number">#{index + 1}</div>
                          <div className="session-info">
                            <div className="session-status-line">
                              <strong>{session.status}</strong><span>{session.completeness_status}</span>
                              <span>{fmtMs(session.duration_recorded_ms)}</span><span>{session.segment_count || 0} seg</span><span>{fmtBytes(session.bytes || 0)}</span>
                            </div>
                            <div className="muted session-date">{new Date(session.created_at).toLocaleString()} · gaps {session.gap_count}</div>
                            <VideoProgressBar session={session} progress={videoProgressBySession[session.id]} />
                            {session.last_error && <div className="inline-warning">{session.last_error}</div>}
                          </div>
                          <div className="session-actions">
                            <button onClick={() => openVideoSession(session)}>Video Manager</button>
                            <button className="danger subtle" disabled={actionId === session.id || isVideoBusy(session)} title={isVideoBusy(session) ? "Сначала остановите Video" : "Переместить Video session в корзину"} onClick={() => moveVideoToTrash(session)}>В корзину</button>
                          </div>
                        </div>
                      ))}
                    </div>
                  )}
                </article>
              );
            })}
          </section>
        </>
       ) : view === "site" ? null : view === "video-manager" ? (
        <section className="storage-panel video-manager-index">
          <div className="section-label">VIDEO SESSIONS</div>
          <p className="muted">Открой session для Overview, Runs, Segments, Parts и durable Build Queue.</p>
          {videoSessions.length === 0 ? <div className="empty-child">Video sessions пока нет</div> : (
            <div className="video-manager-list">
              {videoSessions.map((session) => (
                <div className="video-manager-session" key={session.id}>
                  <div className="video-manager-session-main">
                    <div className="row"><strong>{session.event?.channel_display_name || session.event?.channel_login || session.metadata?.channel_login || "Twitch"}</strong><span>{session.event?.media_type?.toUpperCase() || session.metadata?.media_type?.toUpperCase() || "VIDEO"}</span></div>
                    <div className="event-title">{session.event?.title || session.event?.external_key || session.id}</div>
                    <div className="session-status-line"><strong>{session.status}</strong><span>{session.completeness_status}</span><span>{session.segment_count || 0} seg</span><span>{fmtBytes(session.bytes || 0)}</span><span>{fmtMs(session.duration_recorded_ms)}</span></div>
                    <div className="muted small"><code>{session.id}</code></div>
                  </div>
                  <div className="actions"><button onClick={() => openVideoSession(session)}>Открыть</button></div>
                </div>
              ))}
            </div>
          )}
        </section>
      ) : view === "storage" ? (
        <section className="storage-panel">
          <div className="section-label">VIDEO OUTPUT</div>
          {!outputSettings ? <div className="empty-child">Загрузка настроек…</div> : (
            <>
              <p className="muted">Диски задаются абсолютными host paths в .env; здесь выбирается разрешённый root и каталог внутри него. Настройка применяется только к новым Video sessions.</p>
              <label className="storage-field">
                <span>Диск / output root из .env</span>
                <select value={outputRootKey} onChange={(e) => setOutputRootKey(e.target.value)}>
                  {outputSettings.roots.map((root) => <option key={root.key} value={root.key}>{root.label}</option>)}
                </select>
              </label>
              <label className="storage-field">
                <span>Каталог внутри root</span>
                <input value={outputSubdir} onChange={(e) => setOutputSubdir(e.target.value)} placeholder="streamhub" />
              </label>
              <label className="storage-field">
                <span>Сегментов в пачке</span>
                <input
                  type="number"
                  min={1}
                  max={1000}
                  value={outputBatchSegments}
                  onChange={(e) => setOutputBatchSegments(Math.max(1, Math.min(1000, Number(e.target.value) || 1)))}
                />
              </label>
              <div className="storage-note">
                После копирования и SHA-256 проверки вся пачка фиксируется как archive_ready и удаляется из Docker spool. При Stop/EOF остаток меньше batch тоже переносится.
              </div>
              <div className="actions storage-actions">
                <button disabled={savingOutput} onClick={saveOutputSettings}>{savingOutput ? "Сохраняю…" : "Сохранить output"}</button>
              </div>

              <div className="section-label storage-subsection">OUTPUT MIGRATION</div>
              {outputSettings.roots.length < 2 ? (
                <div className="storage-note">Для миграции нужен второй enabled root. Подключи VIDEO_OUTPUT_ROOT_2_HOST и включи VIDEO_OUTPUT_ROOT_2_ENABLED=true.</div>
              ) : (
                <>
                  <p className="muted">Миграция переносит уже завершённые Video sessions между физическими output roots параллельно сервису. Для каждой session каталог полностью копируется и проверяется, затем DB переключается на новый root, и только после commit удаляется старый каталог. Активные или ещё не archive_ready sessions не трогаются.</p>
                  <div className="storage-migration-controls">
                    <label className="storage-field">
                      <span>Из output</span>
                      <select value={migrationSourceRoot} onChange={(e) => setMigrationSourceRoot(e.target.value)}>
                        {outputSettings.roots.map((root) => <option key={root.key} value={root.key}>{root.label}</option>)}
                      </select>
                    </label>
                    <label className="storage-field">
                      <span>В output</span>
                      <select value={migrationDestinationRoot} onChange={(e) => setMigrationDestinationRoot(e.target.value)}>
                        {outputSettings.roots.map((root) => <option key={root.key} value={root.key}>{root.label}</option>)}
                      </select>
                    </label>
                  </div>
                  <div className="actions storage-actions">
                    <button
                      disabled={migrationAction || !!activeStorageMigration || migrationSourceRoot === migrationDestinationRoot}
                      onClick={startStorageMigration}
                    >
                      {activeStorageMigration ? "Миграция уже выполняется" : migrationAction ? "Создаю…" : "Мигрировать output"}
                    </button>
                  </div>
                  <div className="migration-list">
                    {storageMigrations.length === 0 ? <div className="empty-child">Миграций ещё не было</div> : storageMigrations.slice(0, 10).map((job) => {
                      const percent = job.total_sessions > 0 ? Math.min(100, Math.round((job.migrated_sessions / job.total_sessions) * 100)) : 0;
                      const sourceLabel = outputSettings.roots.find((root) => root.key === job.source_root_key)?.label || job.source_root_key;
                      const destinationLabel = outputSettings.roots.find((root) => root.key === job.destination_root_key)?.label || job.destination_root_key;
                      return (
                        <div className="migration-row" key={job.id}>
                          <div className="row"><strong>#{job.id} · {sourceLabel} → {destinationLabel}</strong><span>{job.status}</span></div>
                          <div className="muted small">sessions {job.migrated_sessions}/{job.total_sessions} · skipped {job.skipped_sessions} · {fmtBytes(job.copied_bytes)} / {fmtBytes(job.total_bytes)}</div>
                          {(job.status === "queued" || job.status === "running") && <div className="progress-track"><div className="progress-fill" style={{ width: `${percent}%` }} /></div>}
                          {job.current_session_id && <div className="muted small">current: <code>{job.current_session_id}</code></div>}
                          {job.last_error && <div className="inline-error">{job.last_error}</div>}
                          {job.status === "failed" && <button disabled={migrationAction || !!activeStorageMigration} onClick={() => retryStorageMigration(job)}>Retry</button>}
                        </div>
                      );
                    })}
                  </div>
                </>
              )}
            </>
          )}
        </section>
      ) : (
        <>
          {trashEvents.length === 0 && <div className="empty">Корзина пуста</div>}

          {trashEvents.length > 0 && (
            <section className="events-list trash-events-list">
              {trashEvents.map((event) => (
                <article className="event-card" key={event.id}>
                  <div className="event-summary trash-event-summary">
                    <div className="event-main">
                      <div className="row"><strong>{event.channel_display_name || event.channel_login || "Twitch"}</strong><span className="event-type">{event.media_type.toUpperCase()}</span></div>
                      <div className="event-title">{event.title || (event.media_type === "vod" ? `VOD ${event.external_key.split(":").pop()}` : "LIVE stream")}</div>
                      <div className="row muted event-meta"><span>{new Date(event.source_started_at_utc || event.created_at).toLocaleString()}</span><span>{event.external_key}</span></div>
                    </div>
                    <div className="event-badges">
                      <span>Удалено Chat: {event.chat_sessions_count}</span>
                      <span>Удалено Video: {event.video_sessions_count}</span>
                    </div>
                  </div>

                  <div className="event-children">
                    <div className="event-delete-row">
                      <span className="muted small">Этот же уникальный Event может одновременно быть в Events с новыми sessions и здесь со старыми удалёнными sessions.</span>
                      <div className="actions">
                        <button disabled={actionId === event.id} onClick={() => restoreEvent(event)}>Восстановить все sessions</button>
                        <button className="danger" disabled={actionId === event.id} onClick={() => purgeEvent(event)}>Удалить из корзины навсегда</button>
                      </div>
                    </div>

                    <div className="section-label">CHAT SESSIONS В КОРЗИНЕ</div>
                    {event.chat_sessions.length === 0 ? <div className="empty-child">Chat sessions нет</div> : event.chat_sessions.map((session, index) => (
                      <div className="session-row" key={session.id}>
                        <div className="session-number">#{index + 1}</div>
                        <div className="session-info">
                          <div className="session-status-line"><strong>{session.completeness_status}</strong><span>{(session.message_count || 0).toLocaleString()} сообщений</span></div>
                          <div className="muted session-date">{new Date(session.created_at).toLocaleString()} · <code>{session.id}</code></div>
                        </div>
                        <div className="session-actions">
                          <button disabled={actionId === session.id} onClick={() => restoreSession(session)}>Восстановить</button>
                          <button className="danger subtle" disabled={actionId === session.id} onClick={() => purgeSession(session)}>Удалить навсегда</button>
                        </div>
                      </div>
                    ))}

                    <div className="section-label video-section-label">VIDEO SESSIONS В КОРЗИНЕ</div>
                    {event.video_sessions.length === 0 ? <div className="empty-child">Video sessions нет</div> : event.video_sessions.map((session, index) => (
                      <div className="session-row video-session-row" key={session.id}>
                        <div className="session-number">#{index + 1}</div>
                        <div className="session-info">
                          <div className="session-status-line"><strong>{session.completeness_status}</strong><span>{session.segment_count || 0} seg</span><span>{fmtBytes(session.bytes || 0)}</span></div>
                          <div className="muted session-date">{new Date(session.created_at).toLocaleString()} · <code>{session.id}</code></div>
                        </div>
                        <div className="session-actions">
                          <button disabled={actionId === session.id} onClick={() => restoreVideoSession(session)}>Восстановить</button>
                          <button className="danger subtle" disabled={actionId === session.id} onClick={() => purgeVideoSession(session)}>Удалить навсегда</button>
                        </div>
                      </div>
                    ))}
                  </div>
                </article>
              ))}
            </section>
          )}
        </>
      )}
    </main>
  );
}

createRoot(document.getElementById("root")!).render(<React.StrictMode><App /></React.StrictMode>);
