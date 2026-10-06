import React, { useCallback, useEffect, useMemo, useRef, useState } from "react";
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
    display_title?: string | null;
    external_key: string;
    source_started_at_utc?: string | null;
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
  kind?: "source" | "proxy" | string;
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
  local_file_state: string;
  local_unlinked_at_utc?: string | null;
  local_unlink_error?: string | null;
  profile?: {
    width?: number; height?: number; fps?: number; video_codec?: string; video_bitrate_kbps?: number; video_max_bitrate_kbps?: number;
    audio_codec?: string; audio_bitrate_kbps?: number; max_mib?: number; source_part_nos?: number[];
  } | null;
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

type ProxyPartPlan = {
  source_part_ids: string[];
  source_part_nos: number[];
  first_source_part_no: number;
  last_source_part_no: number;
  source_part_count: number;
  start_segment_no: number;
  end_segment_no: number;
  duration_ms: number;
  expected_bytes: number;
  target_mib: number;
};

type ProxyPartPlanAll = {
  items: ProxyPartPlan[];
  part_count: number;
  source_part_count: number;
  duration_ms: number;
  expected_bytes: number;
  target_mib: number;
  profile: { width: number; height: number; fps: number; video_codec: string; video_bitrate_kbps: number; video_max_bitrate_kbps: number; audio_codec: string; audio_bitrate_kbps: number };
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

type EventPurgeJob = {
  id: number;
  event_id: string;
  status: "queued" | "running" | "completed" | "failed" | string;
  total_chat_sessions: number;
  total_video_sessions: number;
  purged_chat_sessions: number;
  purged_video_sessions: number;
  result?: Record<string, unknown> | null;
  last_error?: string | null;
  created_at: string;
  started_at_utc?: string | null;
  completed_at_utc?: string | null;
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
  replacement_directory?: string | null;
  replacement_path?: string | null;
  replaced_at_utc?: string | null;
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
  player_offset_ms?: number | null;
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

type SiteChatUserIdentity = {
  chatter_external_id?: string | null;
  chatter_login?: string | null;
  chatter_name?: string | null;
  color?: string | null;
  badges?: unknown;
};

type SiteChatUserSummary = SiteChatUserIdentity & {
  event_id: string;
  chat_session_id?: string | null;
  message_count: number;
  first_message_ms?: number | null;
  last_message_ms?: number | null;
};

type SiteChatUserEvent = {
  event_id: string;
  title: string;
  channel_login?: string | null;
  channel_display_name?: string | null;
  source_started_at_utc?: string | null;
  message_count: number;
};

const siteChatOtherEventsCache = new Map<string, SiteChatUserEvent[]>();

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

type SitePlaybackTimelineRange = {
  run_no: number;
  first_segment_no: number;
  last_segment_no: number;
  timeline_start_ms: number;
  timeline_end_ms: number;
  source_start_ms: number;
  source_end_ms: number;
  mapping_source: string;
};

type SitePlaybackTimeline = {
  event_id: string;
  video_session_id?: string | null;
  synchronized: boolean;
  mapping_quality: "exact" | "mixed" | "approximate" | "unavailable" | string;
  ranges: SitePlaybackTimelineRange[];
};

type SiteAsset = {
  slot: string;
  url: string;
  content_type: string;
  size_bytes: number;
  width: number;
  height: number;
  sha256: string;
  original_filename?: string | null;
  updated_at: string;
};

type SiteAssets = {
  cover?: SiteAsset | null;
  frames: Array<SiteAsset | null>;
  complete: boolean;
};

type SiteTimecode = {
  id: number;
  position: number;
  offset_ms: number;
  title: string;
};

type SiteEvent = {
  id: string;
  platform: string;
  media_type: "live" | "vod";
  external_key: string;
  channel_login?: string | null;
  channel_display_name?: string | null;
  title: string;
  display_title: string;
  source_title?: string | null;
  source_started_at_utc?: string | null;
  source_duration_ms?: number | null;
  source_url?: string | null;
  published_at_utc: string;
  categories: Array<SiteCategory & { position?: number }>;
  assets: SiteAssets;
  timecodes?: SiteTimecode[];
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

type SiteWatchSession = {
  id: string;
  event_id: string;
  owner_user_id?: string | null;
  owner_label: string;
  participant_count: number;
  position_ms: number;
  is_playing: boolean;
  state_version: number;
  state_updated_at?: string | null;
  last_activity_at?: string | null;
  created_at?: string | null;
};

type SiteWatchMessage = {
  id: number;
  session_id: string;
  body: string;
  created_at: string;
  user: { id: string; nickname: string; login: string; avatar_url?: string | null };
};

type SiteCommentReactionName = "like" | "dislike" | "heart" | "broken_heart" | "fire" | "cry" | "laugh" | "poop";

type SiteUserComment = {
  id: number;
  event_id: string;
  parent_comment_id?: number | null;
  body: string;
  text_color?: string | null;
  is_underlined?: boolean;
  created_at: string;
  reactions?: Partial<Record<SiteCommentReactionName, number>>;
  viewer_reaction?: SiteCommentReactionName | null;
  user: {
    id: string;
    nickname: string;
    login: string;
    avatar_url?: string | null;
  };
};

type SiteAvailableEvent = {
  id: string;
  media_type: "live" | "vod";
  external_key: string;
  channel_login?: string | null;
  channel_display_name?: string | null;
  title: string;
  display_title: string;
  source_title?: string | null;
  source_started_at_utc?: string | null;
  source_duration_ms?: number | null;
  assets: SiteAssets;
  video_sessions: number;
  ready_parts: number;
  linked_parts: number;
};

type SiteAdminEvent = SiteAvailableEvent & {
  published: boolean;
  hidden: boolean;
  categories: Array<SiteCategory & { position?: number }>;
  timecodes: SiteTimecode[];
};

type AuthUser = {
  id: string;
  nickname: string;
  login: string;
  email?: string | null;
  email_verified?: boolean;
  telegram_verified: boolean;
  telegram_username?: string | null;
  twitch_verified?: boolean;
  twitch_login?: string | null;
  avatar_url?: string | null;
  created_at?: string | null;
};

type AuthModalMode = "login" | "register";

type TelegramRegistrationState = {
  pollToken: string;
  deepLink: string;
  status: "pending" | "started";
};

type TwitchRegistrationState = {
  pollToken: string;
  authorizeUrl: string;
  status: "pending" | "failed" | "denied";
};

type MediaEvent = {
  id: string;
  platform: string;
  media_type: "live" | "vod";
  external_key: string;
  channel_login?: string | null;
  channel_display_name?: string | null;
  title?: string | null;
  display_title?: string | null;
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

const SITE_CATEGORY_ICON_URLS: Record<string, string> = {
  all: new URL("./assets/categories/all-videos.png", import.meta.url).href,
  fncs: new URL("./assets/categories/fncs.png", import.meta.url).href,
  games: new URL("./assets/categories/games.png", import.meta.url).href,
  shows: new URL("./assets/categories/shows.png", import.meta.url).href,
  films: new URL("./assets/categories/films.png", import.meta.url).href,
  chatroulette: new URL("./assets/categories/chatroulette.png", import.meta.url).href,
};

const STREAMVAULT_LOGO_FRAMES = [0, 1, 2, 3, 4, 5].map(
  (frame) => new URL(`./assets/logo/${frame}.png`, import.meta.url).href,
);

const STREAMVAULT_LOGO_FRAME_SEQUENCE: Array<[number, number]> = [
  [0, 0],
  [205, 1],
  [310, 2],
  [415, 3],
  [520, 4],
  [625, 5],
  [720, 0],
  [815, 0],
  [920, 1],
  [1025, 2],
  [1130, 3],
  [1235, 4],
  [1340, 5],
];
const STREAMVAULT_LOGO_ANIMATION_MS = 1500;

function StreamVaultAnimatedBrand({ onActivate }: { onActivate: () => void }) {
  const [frame, setFrame] = useState(5);
  const [animating, setAnimating] = useState(false);
  const timersRef = useRef<number[]>([]);
  const reducedMotionRef = useRef(false);

  const clearTimers = useCallback(() => {
    timersRef.current.forEach((timer: number) => window.clearTimeout(timer));
    timersRef.current = [];
  }, []);

  const playLogoAnimation = useCallback(() => {
    if (reducedMotionRef.current) return;
    clearTimers();
    setFrame(0);
    setAnimating(false);
    timersRef.current.push(window.setTimeout(() => setAnimating(true), 16));
    STREAMVAULT_LOGO_FRAME_SEQUENCE.forEach(([at, nextFrame]) => {
      timersRef.current.push(window.setTimeout(() => setFrame(nextFrame), at));
    });
    timersRef.current.push(window.setTimeout(() => {
      setFrame(5);
      setAnimating(false);
      timersRef.current = [];
    }, STREAMVAULT_LOGO_ANIMATION_MS));
  }, [clearTimers]);

  useEffect(() => {
    STREAMVAULT_LOGO_FRAMES.forEach((src) => {
      const image = new Image();
      image.decoding = "async";
      image.src = src;
    });

    const reducedMotion = window.matchMedia("(prefers-reduced-motion: reduce)");
    const syncReducedMotion = () => {
      reducedMotionRef.current = reducedMotion.matches;
      if (reducedMotion.matches) {
        clearTimers();
        setFrame(5);
        setAnimating(false);
      }
    };
    syncReducedMotion();
    reducedMotion.addEventListener?.("change", syncReducedMotion);

    const introTimer = window.setTimeout(playLogoAnimation, 650);
    timersRef.current.push(introTimer);
    return () => {
      reducedMotion.removeEventListener?.("change", syncReducedMotion);
      clearTimers();
    };
  }, [clearTimers, playLogoAnimation]);

  return (
    <button
      type="button"
      className="streamvault-brand"
      onClick={onActivate}
      onPointerEnter={playLogoAnimation}
      onFocus={playLogoAnimation}
      aria-label="MRW Hub — главная"
    >
      <span className={`streamvault-brand-mark${animating ? " is-animating" : ""}`} aria-hidden="true">
        <span className="streamvault-brand-tilt">
          <span className="streamvault-brand-scale">
            <img src={STREAMVAULT_LOGO_FRAMES[frame]} alt="" draggable={false} />
          </span>
        </span>
      </span>
      <span className="streamvault-brand-copy" aria-hidden="true">
        <strong>MRW Hub</strong>
        <small>v0.1</small>
      </span>
    </button>
  );
}

function SiteCategoryIcon({ slug }: { slug: string }) {
  const src = SITE_CATEGORY_ICON_URLS[slug];
  if (!src) return <span className="streamvault-category-icon is-fallback" aria-hidden="true">•</span>;
  return (
    <span className="streamvault-category-icon" aria-hidden="true">
      <img src={src} alt="" draggable={false} />
    </span>
  );
}

const API = import.meta.env.VITE_API_BASE_URL || "http://localhost:18741";
const PROGRESS_POLL_MS = 5000;
const PART_TARGET_MIB_KEY = "streamhub.videoManager.targetMib";
const PROXY_PART_TARGET_MIB_KEY = "streamhub.videoManager.proxyTargetMib";
const PART_SEGMENT_COUNT_KEY = "streamhub.videoManager.segmentCount";
const EVENT_TITLE_MODE_KEY = "streamhub.events.titleMode";
const SITE_COMMENT_COLOR_KEY = "streamhub.site.commentColor";
const SITE_WATCH_CLIENT_ID_KEY = "streamhub.site.watchClientId";
const SITE_COMMENT_REACTIONS: Array<{ key: SiteCommentReactionName; label: string; emoji: string; image: string }> = [
  { key: "like", label: "Нравится", emoji: "👍", image: "https://em-content.zobj.net/source/apple/419/thumbs-up_1f44d.png" },
  { key: "dislike", label: "Не нравится", emoji: "👎", image: "https://em-content.zobj.net/source/apple/419/thumbs-down_1f44e.png" },
  { key: "heart", label: "Сердце", emoji: "❤️", image: "https://em-content.zobj.net/source/apple/419/red-heart_2764-fe0f.png" },
  { key: "broken_heart", label: "Разбитое сердце", emoji: "💔", image: "https://em-content.zobj.net/source/apple/419/broken-heart_1f494.png" },
  { key: "fire", label: "Огонь", emoji: "🔥", image: "https://em-content.zobj.net/source/apple/419/fire_1f525.png" },
  { key: "cry", label: "Рыдаю", emoji: "😭", image: "https://em-content.zobj.net/source/apple/419/loudly-crying-face_1f62d.png" },
  { key: "laugh", label: "Смешно", emoji: "😂", image: "https://em-content.zobj.net/source/apple/419/face-with-tears-of-joy_1f602.png" },
  { key: "poop", label: "Какашка", emoji: "💩", image: "https://em-content.zobj.net/source/apple/419/pile-of-poo_1f4a9.png" },
];
type EventTitleMode = "display" | "source";

function storedSiteWatchClientId() {
  try {
    const existing = window.localStorage.getItem(SITE_WATCH_CLIENT_ID_KEY);
    if (existing && /^[A-Za-z0-9._:-]{8,64}$/.test(existing)) return existing;
    const generated = typeof crypto?.randomUUID === "function"
      ? crypto.randomUUID()
      : `watch-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 12)}`;
    window.localStorage.setItem(SITE_WATCH_CLIENT_ID_KEY, generated);
    return generated;
  } catch {
    return `watch-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 12)}`;
  }
}

function storedSiteCommentColor() {
  try {
    const value = window.localStorage.getItem(SITE_COMMENT_COLOR_KEY) || "";
    return /^#[0-9a-f]{6}$/i.test(value) ? value.toLowerCase() : "#ff9b37";
  } catch {
    return "#ff9b37";
  }
}

function storedEventTitleMode(): EventTitleMode {
  try {
    return window.localStorage.getItem(EVENT_TITLE_MODE_KEY) === "source" ? "source" : "display";
  } catch {
    return "display";
  }
}

function fallbackEventTitle(event: MediaEvent) {
  return event.media_type === "vod" ? `VOD ${event.external_key.split(":").pop()}` : "LIVE stream";
}

function visibleEventTitle(event: MediaEvent, mode: EventTitleMode) {
  const fallback = fallbackEventTitle(event);
  if (mode === "source") return event.title?.trim() || fallback;
  return event.display_title?.trim() || event.title?.trim() || fallback;
}

function visibleVideoEventTitle(session: VideoSession, mode: EventTitleMode) {
  const event = session.event;
  const fallback = event?.external_key || session.id;
  if (mode === "source") return event?.title?.trim() || fallback;
  return event?.display_title?.trim() || event?.title?.trim() || fallback;
}

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

function parseTimecodeText(value: string): number | null {
  const parts = value.trim().split(":");
  if (parts.length !== 2 && parts.length !== 3) return null;
  const numbers = parts.map((part) => Number(part));
  if (numbers.some((part) => !Number.isInteger(part) || part < 0)) return null;
  const [hours, minutes, seconds] = parts.length === 3 ? numbers : [0, numbers[0], numbers[1]];
  if (minutes > 59 || seconds > 59) return null;
  return (hours * 3600 + minutes * 60 + seconds) * 1000;
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
  autoPlay = false,
}: {
  playlistUrl: string;
  externalRef?: { current: HTMLVideoElement | null };
  autoPlay?: boolean;
}) {
  const videoRef = useRef<HTMLVideoElement | null>(null);
  const autoPlayStartedRef = useRef(false);
  const autoPlayInFlightRef = useRef(false);
  const autoPlayMutedFallbackRef = useRef(false);
  const playbackStartedRef = useRef(false);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    const video = videoRef.current;
    if (!video) return;
    const source = apiUrl(playlistUrl);
    autoPlayStartedRef.current = false;
    autoPlayInFlightRef.current = false;
    autoPlayMutedFallbackRef.current = false;
    playbackStartedRef.current = false;
    setLoading(true);
    let loadingTimer: number | null = null;

    const clearLoadingTimer = () => {
      if (loadingTimer == null) return;
      window.clearTimeout(loadingTimer);
      loadingTimer = null;
    };
    const tryAutoPlay = () => {
      if (!autoPlay || autoPlayStartedRef.current || autoPlayInFlightRef.current) return;
      if (!video.paused && !video.ended) {
        autoPlayStartedRef.current = true;
        return;
      }

      autoPlayInFlightRef.current = true;
      const attempt = async () => {
        try {
          if (!autoPlayMutedFallbackRef.current) {
            video.muted = false;
            video.defaultMuted = false;
          }
          await video.play();
          autoPlayStartedRef.current = true;
          return;
        } catch (error) {
          if (!(error instanceof DOMException) || error.name !== "NotAllowedError") return;
        }

        // Browsers routinely block audible autoplay in a freshly opened tab.
        // A muted retry is standards-compliant and guarantees that the event starts playing.
        autoPlayMutedFallbackRef.current = true;
        video.muted = true;
        video.defaultMuted = true;
        try {
          await video.play();
          autoPlayStartedRef.current = true;
        } catch {
          // A later canplay/playing lifecycle event will retry.
        }
      };

      void attempt().finally(() => {
        autoPlayInFlightRef.current = false;
      });
    };
    const markLoadingNow = () => {
      clearLoadingTimer();
      setLoading(true);
    };
    const markLoadingSoon = (delay: number) => {
      clearLoadingTimer();
      loadingTimer = window.setTimeout(() => {
        loadingTimer = null;
        if (video.seeking || video.readyState < 3) setLoading(true);
      }, delay);
    };
    const markReady = () => {
      clearLoadingTimer();
      tryAutoPlay();
    };
    const markProgressing = () => {
      if (video.seeking || video.readyState < 2) return;
      if (!playbackStartedRef.current) {
        if (video.paused || video.ended || video.currentTime <= 0.01) return;
        playbackStartedRef.current = true;
      } else if (video.paused) {
        return;
      }
      clearLoadingTimer();
      setLoading(false);
    };
    const markSeeked = () => {
      if (video.readyState >= 2) markReady();
      else markLoadingSoon(120);
    };
    const onWaiting = () => markLoadingSoon(320);
    const onStalled = () => markLoadingSoon(450);
    const onSeeking = () => markLoadingSoon(180);

    video.addEventListener("loadstart", markLoadingNow);
    video.addEventListener("waiting", onWaiting);
    video.addEventListener("stalled", onStalled);
    video.addEventListener("seeking", onSeeking);
    video.addEventListener("loadeddata", markReady);
    video.addEventListener("canplay", markReady);
    video.addEventListener("playing", markReady);
    video.addEventListener("timeupdate", markProgressing);
    video.addEventListener("seeked", markSeeked);

    const detachMediaEvents = () => {
      clearLoadingTimer();
      video.removeEventListener("loadstart", markLoadingNow);
      video.removeEventListener("waiting", onWaiting);
      video.removeEventListener("stalled", onStalled);
      video.removeEventListener("seeking", onSeeking);
      video.removeEventListener("loadeddata", markReady);
      video.removeEventListener("canplay", markReady);
      video.removeEventListener("playing", markReady);
      video.removeEventListener("timeupdate", markProgressing);
      video.removeEventListener("seeked", markSeeked);
    };

    if (video.canPlayType("application/vnd.apple.mpegurl")) {
      video.src = source;
      video.load();
      return () => {
        detachMediaEvents();
        video.removeAttribute("src");
        video.load();
      };
    }
    if (!Hls.isSupported()) {
      detachMediaEvents();
      setLoading(false);
      return;
    }
    const hls = new Hls();
    hls.loadSource(source);
    hls.attachMedia(video);
    hls.on(Hls.Events.MANIFEST_PARSED, tryAutoPlay);
    return () => {
      detachMediaEvents();
      hls.destroy();
    };
  }, [playlistUrl, autoPlay]);

  return (
    <>
      <video
        ref={(node) => {
          videoRef.current = node;
          if (externalRef) externalRef.current = node;
        }}
        className="telegram-player"
        controls
        autoPlay={autoPlay}
        playsInline
        preload="auto"
      />
      {loading ? (
        <div className="streamvault-video-loading" role="status" aria-live="polite" aria-label="Видео загружается">
          <div className="streamvault-video-loading-card">
            <span className="streamvault-video-loading-spinner" aria-hidden="true" />
            <strong>Загрузка видео…</strong>
            <small>Подготавливаем воспроизведение</small>
          </div>
        </div>
      ) : null}
    </>
  );
}

function safeChatColor(value?: string | null) {
  if (!value) return undefined;
  const normalized = value.trim().toLowerCase().replace(/\s+/g, "");
  if (["#000", "#000000", "black", "rgb(0,0,0)", "rgba(0,0,0,1)"].includes(normalized)) return "#8b949e";
  if (/^#[0-9a-f]{3}$/i.test(normalized)) {
    return `#${normalized.slice(1).split("").map((part) => part + part).join("")}`;
  }
  if (/^#[0-9a-f]{6}$/i.test(normalized)) return normalized;
  return undefined;
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
      const setId = record.setID ?? record.setId ?? record.set_id ?? record.name;
      const version = record.version ?? record.id;
      push(setId, version, `${String(setId || "badge")}/${String(version || "")}:${index}`);
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

function ChatMessageFragments({ message, onAssetLoad }: { message: SiteChatMessage; onAssetLoad?: () => void }) {
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
          onLoad={onAssetLoad}
          key={`${emoteId}:${index}`}
        />;
      }
      return <React.Fragment key={index}>{text}</React.Fragment>;
    })}
  </>;
}

function chatUserQuery(user: SiteChatUserIdentity) {
  const params = new URLSearchParams();
  if (user.chatter_external_id) params.set("chatter_external_id", user.chatter_external_id);
  else if (user.chatter_login) params.set("chatter_login", user.chatter_login);
  return params;
}

function chatUserMenuPosition(clientX: number, clientY: number) {
  const width = 286;
  const height = 250;
  return {
    x: Math.max(10, Math.min(clientX + 10, window.innerWidth - width - 10)),
    y: Math.max(10, Math.min(clientY + 10, window.innerHeight - height - 10)),
  };
}

function TwitchIcon() {
  return (
    <svg className="site-twitch-icon" viewBox="0 0 24 24" aria-hidden="true">
      <path fill="currentColor" d="M4 2h18v13l-5 5h-4l-3 3v-3H5V17H2V5l2-3Zm2 3v11h5v3l3-3h4l2-2V5H6Zm5 3h2v5h-2V8Zm5 0h2v5h-2V8Z" />
    </svg>
  );
}

function SiteChatUserContext({
  eventId,
  user,
  x,
  y,
  onClose,
  onMention,
  videoRef,
}: {
  eventId: string;
  user: SiteChatUserIdentity;
  x: number;
  y: number;
  onClose: () => void;
  onMention?: (mention: string) => void;
  videoRef?: { current: HTMLVideoElement | null };
}) {
  const identityParams = chatUserQuery(user);
  const identityQuery = identityParams.toString();
  const otherEventsCacheKey = `${eventId}?${identityQuery}`;
  const cachedOtherEvents = siteChatOtherEventsCache.get(otherEventsCacheKey);
  const [summary, setSummary] = useState<SiteChatUserSummary | null>(null);
  const [summaryLoading, setSummaryLoading] = useState(true);
  const [badgeAssets, setBadgeAssets] = useState<Record<string, SiteChatBadgeAsset>>({});
  const [messagesOpen, setMessagesOpen] = useState(false);
  const [historyTab, setHistoryTab] = useState<"current" | "other">("current");
  const [otherEvents, setOtherEvents] = useState<SiteChatUserEvent[]>(() => cachedOtherEvents || []);
  const [otherEventsLoading, setOtherEventsLoading] = useState(false);
  const [otherEventsLoaded, setOtherEventsLoaded] = useState(() => cachedOtherEvents !== undefined);
  const [selectedOther, setSelectedOther] = useState<SiteChatUserEvent | null>(null);
  const [historyMessages, setHistoryMessages] = useState<SiteChatMessage[]>([]);
  const [historyLoading, setHistoryLoading] = useState(false);
  const [historyError, setHistoryError] = useState("");
  const menuRef = useRef<HTMLDivElement | null>(null);
  const modalRef = useRef<HTMLDivElement | null>(null);
  const historyRequestRef = useRef<AbortController | null>(null);

  useEffect(() => {
    const controller = new AbortController();
    setSummary(null);
    setSummaryLoading(true);
    if (!identityQuery) {
      setSummaryLoading(false);
      return () => controller.abort();
    }
    void (async () => {
      try {
        const response = await fetch(apiUrl(`/api/v1/site/events/${eventId}/chat/user-summary?${identityQuery}`), {
          cache: "no-store",
          signal: controller.signal,
        });
        if (!response.ok) throw new Error(await response.text());
        setSummary(await response.json() as SiteChatUserSummary);
      } catch (e) {
        if (!(e instanceof DOMException && e.name === "AbortError")) console.warn("chat user summary unavailable", e);
      } finally {
        if (!controller.signal.aborted) setSummaryLoading(false);
      }
    })();
    return () => controller.abort();
  }, [eventId, identityQuery]);

  useEffect(() => {
    const controller = new AbortController();
    void (async () => {
      try {
        const response = await fetch(apiUrl(`/api/v1/site/events/${eventId}/chat/badges`), {
          cache: "no-store",
          signal: controller.signal,
        });
        if (!response.ok) return;
        const payload = await response.json();
        const next: Record<string, SiteChatBadgeAsset> = {};
        ((payload.badges || []) as SiteChatBadgeAsset[]).forEach((badge) => {
          const setId = String(badge.set_id || "").trim();
          const version = String(badge.version || "").trim();
          if (setId && version) next[chatBadgeAssetKey(setId, version)] = badge;
        });
        setBadgeAssets(next);
      } catch (e) {
        if (!(e instanceof DOMException && e.name === "AbortError")) console.warn("chat user badges unavailable", e);
      }
    })();
    return () => controller.abort();
  }, [eventId]);

  useEffect(() => {
    const onPointerDown = (event: PointerEvent) => {
      if (messagesOpen) return;
      if (menuRef.current?.contains(event.target as Node)) return;
      onClose();
    };
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") onClose();
    };
    const onResize = () => onClose();
    document.addEventListener("pointerdown", onPointerDown);
    window.addEventListener("keydown", onKeyDown);
    window.addEventListener("resize", onResize);
    return () => {
      document.removeEventListener("pointerdown", onPointerDown);
      window.removeEventListener("keydown", onKeyDown);
      window.removeEventListener("resize", onResize);
    };
  }, [messagesOpen, onClose]);

  async function loadHistoryMessages(targetEventId: string) {
    historyRequestRef.current?.abort();
    const controller = new AbortController();
    historyRequestRef.current = controller;
    setHistoryMessages([]);
    setHistoryLoading(true);
    setHistoryError("");
    try {
      const timelinePromise = fetch(apiUrl(`/api/v1/site/events/${targetEventId}/playback-timeline`), {
        cache: "no-store",
        signal: controller.signal,
      }).then(async (response) => response.ok ? await response.json() as SitePlaybackTimeline : null).catch(() => null);

      const loaded: SiteChatMessage[] = [];
      let cursor: { time_ms: number; id: number } | null = null;
      do {
        const params = new URLSearchParams(identityQuery);
        params.set("page_size", "1000");
        if (cursor) {
          params.set("after_ms", String(cursor.time_ms));
          params.set("after_id", String(cursor.id));
        }
        const response = await fetch(apiUrl(`/api/v1/site/events/${targetEventId}/chat/user-messages?${params.toString()}`), {
          cache: "no-store",
          signal: controller.signal,
        });
        if (!response.ok) throw new Error(await response.text());
        const payload = await response.json();
        loaded.push(...((payload.messages || []) as SiteChatMessage[]));
        cursor = payload.next_cursor || null;
      } while (cursor && !controller.signal.aborted);

      const timeline = await timelinePromise;
      const ranges = timeline?.synchronized ? timeline.ranges : [];
      setHistoryMessages(loaded.map((message) => ({
        ...message,
        player_offset_ms: ranges.length ? sourceToPlayerMs(message.timeline_offset_ms, ranges) : null,
      })));
    } catch (e) {
      if (!(e instanceof DOMException && e.name === "AbortError")) setHistoryError(`Не удалось загрузить сообщения: ${String(e)}`);
    } finally {
      if (!controller.signal.aborted) setHistoryLoading(false);
    }
  }

  async function loadOtherEvents() {
    if (otherEventsLoaded || otherEventsLoading) return;
    setOtherEventsLoading(true);
    setHistoryError("");
    try {
      const response = await fetch(apiUrl(`/api/v1/site/events/${eventId}/chat/user-events?${identityQuery}`), { cache: "no-store" });
      if (!response.ok) throw new Error(await response.text());
      const payload = await response.json();
      const items = (payload.items || []) as SiteChatUserEvent[];
      siteChatOtherEventsCache.set(otherEventsCacheKey, items);
      setOtherEvents(items);
      setOtherEventsLoaded(true);
    } catch (e) {
      setHistoryError(`Не удалось загрузить другие стримы: ${String(e)}`);
    } finally {
      setOtherEventsLoading(false);
    }
  }

  useEffect(() => {
    if (!messagesOpen) return;
    if (historyTab === "current") {
      setSelectedOther(null);
      void loadHistoryMessages(eventId);
      return;
    }
    setHistoryMessages([]);
    if (selectedOther) {
      void loadHistoryMessages(selectedOther.event_id);
    } else if (!otherEventsLoaded) {
      void loadOtherEvents();
    }
    return () => historyRequestRef.current?.abort();
  // identityQuery is stable for the lifetime of the menu target.
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [messagesOpen, historyTab, selectedOther?.event_id, eventId, identityQuery, otherEventsLoaded, otherEventsLoading]);

  useEffect(() => () => historyRequestRef.current?.abort(), []);

  const name = summary?.chatter_name || user.chatter_name || summary?.chatter_login || user.chatter_login || "Гость";
  const login = summary?.chatter_login || user.chatter_login || "";
  const color = safeChatColor(summary?.color || user.color) || "#ff8a32";
  const badges = chatBadges(summary?.badges ?? user.badges, badgeAssets).slice(0, 4);
  const targetEventId = historyTab === "current" ? eventId : selectedOther?.event_id;
  const targetTitle = historyTab === "current" ? "Этот стрим" : selectedOther?.title || "Другие стримы";

  function seekHistoryMessage(message: SiteChatMessage) {
    if (targetEventId !== eventId || message.player_offset_ms == null || !videoRef?.current) return;
    videoRef.current.currentTime = Math.max(0, message.player_offset_ms / 1000);
    void videoRef.current.play().catch(() => {});
  }

  return <>
    {!messagesOpen ? (
      <div
        className="site-chat-user-menu"
        ref={menuRef}
        style={{ left: x, top: y }}
        role="dialog"
        aria-label={`Профиль ${name}`}
      >
        <div className="site-chat-user-head">
          <div className="site-chat-user-avatar" style={{ background: color }}>{name.slice(0, 1).toUpperCase()}</div>
          <div className="site-chat-user-identity">
            <strong style={{ color }}>{name}</strong>
            {login ? <span>@{login}</span> : <span>пользователь чата</span>}
            {badges.length > 0 ? (
              <div className="site-chat-user-badges">
                {badges.map((badge) => badge.imageUrl ? (
                  <img key={badge.key} src={badge.imageUrl} alt={badge.title} title={badge.title} />
                ) : <span key={badge.key} title={badge.title}>{badge.label}</span>)}
              </div>
            ) : null}
          </div>
        </div>
        <button className="site-chat-user-action site-chat-user-stat is-button" type="button" onClick={() => setMessagesOpen(true)}>
          <span className="site-chat-user-action-label">Все сообщения</span>
          <strong aria-label={summaryLoading ? "Загружается количество сообщений" : undefined}>
            {summaryLoading ? <i className="site-inline-spinner" /> : (summary?.message_count ?? 0).toLocaleString("ru-RU")}
          </strong>
        </button>
        {login ? (
          <a className="site-chat-user-action" href={`https://www.twitch.tv/${encodeURIComponent(login)}`} target="_blank" rel="noreferrer">
            <span className="site-chat-user-action-label"><TwitchIcon />Посмотреть профиль Twitch</span>
          </a>
        ) : (
          <div className="site-chat-user-action is-disabled"><span className="site-chat-user-action-label"><TwitchIcon />Посмотреть профиль Twitch</span></div>
        )}
        <button
          className="site-chat-user-action"
          type="button"
          onClick={() => {
            onMention?.(`@${login || name}`);
            onClose();
          }}
        >
          <span>Отметить в комментарии</span>
        </button>
      </div>
    ) : null}

    {messagesOpen ? (
      <div className="site-chat-history-backdrop" role="presentation" onMouseDown={(event) => event.target === event.currentTarget && onClose()}>
        <section className="site-chat-history-modal" ref={modalRef} role="dialog" aria-modal="true" aria-label={`Сообщения ${name}`}>
          <header className="site-chat-history-head">
            <div>
              <span>ИСТОРИЯ ЧАТА</span>
              <strong>{name}</strong>
              {login ? <small>@{login}</small> : null}
            </div>
            <button type="button" onClick={onClose} aria-label="Закрыть"><span className="site-chat-history-close-icon" aria-hidden="true" /></button>
          </header>
          <div className="site-chat-history-tabs" role="tablist">
            <button className={historyTab === "current" ? "active" : ""} type="button" onClick={() => setHistoryTab("current")}>Этот стрим</button>
            <button className={historyTab === "other" ? "active" : ""} type="button" onClick={() => { setHistoryTab("other"); setSelectedOther(null); }}>
              <span>Другие</span>
              {otherEventsLoaded ? <b className="site-chat-history-tab-count">{otherEvents.length.toLocaleString("ru-RU")}</b> : null}
            </button>
          </div>
          <div className="site-chat-history-content">
            {historyError ? <div className="site-chat-history-error">{historyError}</div> : null}
            {historyTab === "other" && !selectedOther ? (
              <>
                {otherEventsLoading ? <div className="site-chat-history-loading"><i className="site-inline-spinner" /></div> : null}
                {!otherEventsLoading && otherEvents.length === 0 ? <div className="site-chat-history-empty">В других опубликованных стримах сообщений нет.</div> : null}
                <div className="site-chat-history-events">
                  {otherEvents.map((item) => (
                    <button type="button" className="site-chat-history-event" key={item.event_id} onClick={() => setSelectedOther(item)}>
                      <strong>{item.title}</strong>
                      <span>Сообщений: <b>{item.message_count.toLocaleString("ru-RU")}</b></span>
                    </button>
                  ))}
                </div>
              </>
            ) : (
              <>
                <div className="site-chat-history-stream-context">
                  {historyTab === "other" ? (
                    <div className="site-chat-history-back-row">
                      <button className="site-chat-history-back" type="button" onClick={() => setSelectedOther(null)}>Назад</button>
                    </div>
                  ) : null}
                  <div className="site-chat-history-stream-heading">
                    <div>
                      <strong>{targetTitle}</strong>
                    </div>
                    <b>{historyLoading ? "…" : `${historyMessages.length.toLocaleString("ru-RU")} сообщений`}</b>
                  </div>
                </div>
                {historyLoading ? <div className="site-chat-history-loading"><i className="site-inline-spinner" /></div> : null}
                {!historyLoading && historyMessages.length === 0 ? <div className="site-chat-history-empty">Сообщений не найдено.</div> : null}
                <div className="site-chat-history-messages">
                  {historyMessages.map((message) => {
                    const reply = chatReplySummary(message.reply);
                    const canSeek = targetEventId === eventId && message.player_offset_ms != null && !!videoRef?.current;
                    return (
                      <article className="site-chat-history-message" key={message.id}>
                        <button
                          type="button"
                          disabled={!canSeek}
                          onClick={() => seekHistoryMessage(message)}
                          title={canSeek
                            ? `Перейти к ${fmtMs(message.player_offset_ms as number)}`
                            : `Видео для этого сообщения пока не загружено · таймлайн Twitch ${fmtMs(message.timeline_offset_ms)}`}
                        >
                          {message.player_offset_ms != null ? fmtMs(message.player_offset_ms) : "—"}
                        </button>
                        <div>
                          {reply ? <small>{reply}</small> : null}
                          <p><ChatMessageFragments message={message} /></p>
                        </div>
                      </article>
                    );
                  })}
                </div>
              </>
            )}
          </div>
        </section>
      </div>
    ) : null}
  </>;
}

function SiteChatStats({
  eventId,
  hasChat,
  totalMessages,
  videoRef,
  onMention,
}: {
  eventId: string;
  hasChat: boolean;
  totalMessages: number;
  videoRef?: { current: HTMLVideoElement | null };
  onMention?: (mention: string) => void;
}) {
  const [stats, setStats] = useState<SiteChatStatsPayload | null>(null);
  const [loading, setLoading] = useState(hasChat);
  const [userMenu, setUserMenu] = useState<{ user: SiteChatUserIdentity; x: number; y: number } | null>(null);

  useEffect(() => {
    setStats(null);
    setUserMenu(null);
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
  const value = (content: React.ReactNode) => loading ? <i className="site-inline-spinner" /> : content;

  function openStatsUser(event: React.MouseEvent<HTMLElement>, user?: SiteChatStatsUser | null) {
    if (!user) return;
    const rect = event.currentTarget.getBoundingClientRect();
    const position = chatUserMenuPosition(rect.left, rect.bottom);
    setUserMenu({ user, ...position });
  }

  return (
    <section className="streamvault-chat-stats" aria-label="Статистика чата">
      <div className="streamvault-chat-stats-head">
        <div><span>СТАТИСТИКА ЧАТА</span></div>
      </div>
      <div className="streamvault-chat-stats-grid">
        <div><span>Сообщения</span><strong>{totalMessages.toLocaleString("ru-RU")}</strong></div>
        <div><span>Участники</span><strong>{hasChat ? value((stats?.unique_chatters ?? 0).toLocaleString("ru-RU")) : "—"}</strong></div>
        <button
          className="user"
          type="button"
          disabled={!stats?.most_active || loading}
          onClick={(event) => openStatsUser(event, stats?.most_active)}
          title={stats?.most_active ? `Открыть профиль и историю ${userLabel(stats.most_active)}` : undefined}
        >
          <span>Больше всего</span>
          <strong className="streamvault-chat-stat-user-name">
            {hasChat ? value(userLabel(stats?.most_active)) : "—"}
          </strong>
          {!loading && stats?.most_active ? <small>{stats.most_active.message_count.toLocaleString("ru-RU")} сообщений</small> : null}
        </button>
        <button
          className="user"
          type="button"
          disabled={!stats?.least_active || loading}
          onClick={(event) => openStatsUser(event, stats?.least_active)}
          title={stats?.least_active ? `Открыть профиль и историю ${userLabel(stats.least_active)}` : undefined}
        >
          <span>Меньше всего</span>
          <strong className="streamvault-chat-stat-user-name">
            {hasChat ? value(userLabel(stats?.least_active)) : "—"}
          </strong>
          {!loading && stats?.least_active ? <small>{stats.least_active.message_count.toLocaleString("ru-RU")} сообщений</small> : null}
        </button>
      </div>
      {userMenu ? (
        <SiteChatUserContext
          eventId={eventId}
          user={userMenu.user}
          x={userMenu.x}
          y={userMenu.y}
          onClose={() => setUserMenu(null)}
          onMention={onMention}
          videoRef={videoRef}
        />
      ) : null}
    </section>
  );
}

function mapTimelineValue(
  value: number,
  ranges: SitePlaybackTimelineRange[],
  direction: "player-to-source" | "source-to-player"
) {
  const matches = ranges.filter((item) => direction === "player-to-source"
    ? value >= item.timeline_start_ms && value <= item.timeline_end_ms
    : value >= item.source_start_ms && value <= item.source_end_ms);
  // At a player discontinuity two ranges share the same boundary timestamp.
  // Prefer the following range so the first frame after a reconnect maps to the
  // post-gap Twitch source clock rather than the end of the previous run.
  const range = direction === "player-to-source" ? matches[matches.length - 1] : matches[0];
  if (!range) return null;

  const inputStart = direction === "player-to-source" ? range.timeline_start_ms : range.source_start_ms;
  const inputEnd = direction === "player-to-source" ? range.timeline_end_ms : range.source_end_ms;
  const outputStart = direction === "player-to-source" ? range.source_start_ms : range.timeline_start_ms;
  const outputEnd = direction === "player-to-source" ? range.source_end_ms : range.timeline_end_ms;
  const inputSpan = Math.max(1, inputEnd - inputStart);
  const outputSpan = Math.max(0, outputEnd - outputStart);
  const ratio = Math.max(0, Math.min(1, (value - inputStart) / inputSpan));
  return Math.max(0, Math.round(outputStart + outputSpan * ratio));
}

function playerToSourceMs(value: number, ranges: SitePlaybackTimelineRange[]) {
  return mapTimelineValue(value, ranges, "player-to-source");
}

function sourceToPlayerMs(value: number, ranges: SitePlaybackTimelineRange[]) {
  return mapTimelineValue(value, ranges, "source-to-player");
}

function SiteReplayChat({
  eventId,
  hasChat,
  messageCount,
  videoRef,
  watchSession,
  watchClientId,
  authUser,
  onLogin,
  onMention,
}: {
  eventId: string;
  hasChat: boolean;
  messageCount: number;
  videoRef: { current: HTMLVideoElement | null };
  watchSession: SiteWatchSession | null;
  watchClientId: string;
  authUser: AuthUser | null;
  onLogin: () => void;
  onMention?: (mention: string) => void;
}) {
  const [messages, setMessages] = useState<SiteChatMessage[]>([]);
  const [badgeAssets, setBadgeAssets] = useState<Record<string, SiteChatBadgeAsset>>({});
  const [playbackTimeline, setPlaybackTimeline] = useState<SitePlaybackTimeline | null>(null);
  const [currentMs, setCurrentMs] = useState(0);
  const [chatStatus, setChatStatus] = useState(hasChat ? "Загрузка…" : "Чат не записан");
  const [following, setFollowing] = useState(true);
  const [userMenu, setUserMenu] = useState<{ user: SiteChatUserIdentity; x: number; y: number } | null>(null);
  const [chatMode, setChatMode] = useState<"recording" | "live">("recording");
  const [roomMessages, setRoomMessages] = useState<SiteWatchMessage[]>([]);
  const [roomDraft, setRoomDraft] = useState("");
  const [roomSending, setRoomSending] = useState(false);
  const [roomNotice, setRoomNotice] = useState("");
  const roomListRef = useRef<HTMLDivElement | null>(null);
  const listRef = useRef<HTMLDivElement | null>(null);
  const loadedRangeRef = useRef<{ from: number; to: number } | null>(null);
  const smoothFollowRef = useRef(false);
  const smoothFollowTimerRef = useRef<number | null>(null);
  const lastChatScrollTopRef = useRef(0);
  const pointerScrollIntentRef = useRef(false);
  const followingRef = useRef(true);
  const pausedWindowStartIdRef = useRef<number | null>(null);

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
    setPlaybackTimeline(null);
    if (!hasChat) return;
    const controller = new AbortController();
    void (async () => {
      try {
        const response = await fetch(apiUrl(`/api/v1/site/events/${eventId}/playback-timeline`), {
          cache: "no-store",
          signal: controller.signal,
        });
        if (!response.ok) throw new Error(await response.text());
        setPlaybackTimeline(await response.json() as SitePlaybackTimeline);
      } catch (e) {
        if (!(e instanceof DOMException && e.name === "AbortError")) {
          console.warn("playback timeline unavailable", e);
          setPlaybackTimeline({
            event_id: eventId,
            video_session_id: null,
            synchronized: false,
            mapping_quality: "unavailable",
            ranges: [],
          });
        }
      }
    })();
    return () => controller.abort();
  }, [eventId, hasChat]);

  useEffect(() => {
    setMessages([]);
    setFollowing(true);
    followingRef.current = true;
    pausedWindowStartIdRef.current = null;
    lastChatScrollTopRef.current = 0;
    loadedRangeRef.current = null;
    if (!hasChat) {
      setChatStatus("Чат не записан");
      return;
    }
    if (!playbackTimeline) {
      setChatStatus("Синхронизация…");
      return;
    }
    if (!playbackTimeline.synchronized || playbackTimeline.ranges.length === 0) {
      setChatStatus("Таймлайн видео недоступен");
      return;
    }

    const ranges = playbackTimeline.ranges;
    let stopped = false;
    let controller: AbortController | null = null;
    let loading = false;

    const syncClock = () => {
      const video = videoRef.current;
      setCurrentMs(Math.max(0, Math.round((video?.currentTime || 0) * 1000)));
    };

    const fetchFirstFutureMessage = async (currentPlayer: number, signal: AbortSignal): Promise<(SiteChatMessage & { player_offset_ms: number }) | null> => {
      for (const range of ranges) {
        if (range.timeline_end_ms <= currentPlayer + 100) continue;
        const playerStart = Math.max(currentPlayer + 101, range.timeline_start_ms);
        const sourceStart = playerToSourceMs(playerStart, [range]);
        if (sourceStart == null || sourceStart > range.source_end_ms) continue;

        const params = new URLSearchParams({
          from_ms: String(Math.max(0, sourceStart)),
          to_ms: String(Math.max(sourceStart, range.source_end_ms)),
          page_size: "1",
        });
        const response = await fetch(apiUrl(`/api/v1/site/events/${eventId}/chat/messages?${params.toString()}`), {
          cache: "no-store",
          signal,
        });
        if (!response.ok) throw new Error(await response.text());
        const payload = await response.json();
        const candidate = ((payload.messages || []) as SiteChatMessage[])[0];
        if (!candidate) continue;
        const playerOffset = sourceToPlayerMs(candidate.timeline_offset_ms, ranges);
        if (playerOffset == null || playerOffset <= currentPlayer + 100) continue;
        return { ...candidate, player_offset_ms: playerOffset };
      }
      return null;
    };

    const loadWindow = async (force = false) => {
      if (loading || stopped) return;
      const currentPlayer = Math.max(0, Math.round((videoRef.current?.currentTime || 0) * 1000));
      const currentSource = playerToSourceMs(currentPlayer, ranges);
      if (currentSource == null) {
        setMessages([]);
        setChatStatus("В этой точке нет source timeline");
        return;
      }
      const loaded = loadedRangeRef.current;
      if (!force && loaded && currentSource >= loaded.from + 12_000 && currentSource <= loaded.to - 12_000) return;

      const from = Math.max(0, currentSource - 60_000);
      const to = currentSource + 45_000;
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
        const mapped = loadedMessages
          .map((message) => ({
            ...message,
            player_offset_ms: sourceToPlayerMs(message.timeline_offset_ms, ranges),
          }))
          .filter((message) => message.player_offset_ms != null);
        const hasPastOrCurrent = mapped.some((message) => (message.player_offset_ms ?? Number.POSITIVE_INFINITY) <= currentPlayer + 100);
        const hasFuture = mapped.some((message) => (message.player_offset_ms ?? Number.NEGATIVE_INFINITY) > currentPlayer + 100);
        if (!hasPastOrCurrent && !hasFuture) {
          const nextMessage = await fetchFirstFutureMessage(currentPlayer, controller.signal);
          if (stopped) return;
          if (nextMessage && !mapped.some((message) => message.id === nextMessage.id)) mapped.push(nextMessage);
        }
        mapped.sort((a, b) => ((a.player_offset_ms || 0) - (b.player_offset_ms || 0)) || (a.id - b.id));
        setMessages(mapped);
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
      if (followingRef.current) void loadWindow(false);
    }, 500);

    return () => {
      stopped = true;
      controller?.abort();
      window.clearInterval(timer);
      detach();
    };
  }, [eventId, hasChat, messageCount, playbackTimeline, videoRef]);

  const visible = useMemo(() => {
    const eligible = messages.filter((message) => (message.player_offset_ms ?? Number.POSITIVE_INFINITY) <= currentMs + 100);
    if (following) return eligible.slice(-500);
    const startId = pausedWindowStartIdRef.current;
    if (startId != null) {
      const startIndex = eligible.findIndex((message) => message.id === startId);
      if (startIndex >= 0) return eligible.slice(startIndex);
    }
    return eligible.slice(-500);
  }, [messages, currentMs, following]);
  const nextMessagePlayerMs = useMemo(() => {
    const next = messages.find((message) => (message.player_offset_ms ?? Number.NEGATIVE_INFINITY) > currentMs + 100);
    return next?.player_offset_ms ?? null;
  }, [messages, currentMs]);
  const nextMessageCountdown = nextMessagePlayerMs == null
    ? null
    : fmtMs(Math.ceil(Math.max(0, nextMessagePlayerMs - currentMs) / 1000) * 1000);

  useEffect(() => {
    if (!following || smoothFollowRef.current) return;
    const node = listRef.current;
    if (!node) return;
    let frame2 = 0;
    const frame1 = window.requestAnimationFrame(() => {
      node.scrollTop = node.scrollHeight;
      frame2 = window.requestAnimationFrame(() => {
        node.scrollTop = node.scrollHeight;
      });
    });
    return () => {
      window.cancelAnimationFrame(frame1);
      if (frame2) window.cancelAnimationFrame(frame2);
    };
  }, [visible.length, messages, following]);

  useEffect(() => () => {
    if (smoothFollowTimerRef.current != null) window.clearTimeout(smoothFollowTimerRef.current);
  }, []);

  function pauseFollowing() {
    if (visible.length === 0) return;
    if (followingRef.current) pausedWindowStartIdRef.current = visible[0]?.id ?? null;
    followingRef.current = false;
    cancelSmoothFollow();
    setFollowing(false);
  }

  function cancelSmoothFollow() {
    smoothFollowRef.current = false;
    if (smoothFollowTimerRef.current != null) {
      window.clearTimeout(smoothFollowTimerRef.current);
      smoothFollowTimerRef.current = null;
    }
  }

  function followCurrent() {
    const node = listRef.current;
    if (!node) return;
    cancelSmoothFollow();
    smoothFollowRef.current = true;
    followingRef.current = true;
    pausedWindowStartIdRef.current = null;
    setFollowing(true);
    node.scrollTo({ top: node.scrollHeight, behavior: "smooth" });
    smoothFollowTimerRef.current = window.setTimeout(() => {
      smoothFollowRef.current = false;
      smoothFollowTimerRef.current = null;
      const current = listRef.current;
      if (current) current.scrollTop = current.scrollHeight;
      followingRef.current = true;
      pausedWindowStartIdRef.current = null;
      setFollowing(true);
    }, 450);
  }

  function handleChatScroll() {
    const node = listRef.current;
    if (!node) return;
    const previousTop = lastChatScrollTopRef.current;
    const currentTop = node.scrollTop;
    const movingUp = currentTop < previousTop - 0.5;
    lastChatScrollTopRef.current = currentTop;

    if (smoothFollowRef.current) return;
    if (visible.length === 0) {
      if (!following) {
        followingRef.current = true;
        pausedWindowStartIdRef.current = null;
        setFollowing(true);
      }
      return;
    }

    const distance = Math.max(0, node.scrollHeight - currentTop - node.clientHeight);
    if (following) {
      // DOM growth (large messages, emotes, simultaneous batches) can emit scroll events.
      // Leave live-follow only when an actual pointer/scrollbar gesture moved upward.
      if (pointerScrollIntentRef.current && movingUp && distance > 4) pauseFollowing();
      return;
    }
    if (distance <= 2) {
      followingRef.current = true;
      pausedWindowStartIdRef.current = null;
      setFollowing(true);
    }
  }

  function handleChatPointerDown() {
    pointerScrollIntentRef.current = true;
  }

  function handleChatPointerEnd() {
    pointerScrollIntentRef.current = false;
  }

  function handleChatWheel(event: React.WheelEvent<HTMLDivElement>) {
    pointerScrollIntentRef.current = false;
    if (event.deltaY < 0 && visible.length > 0) pauseFollowing();
  }

  function handleChatTouchMove() {
    if (visible.length === 0) return;
    pauseFollowing();
  }

  function handleChatKeyDown(event: React.KeyboardEvent<HTMLDivElement>) {
    if (visible.length > 0 && ["ArrowUp", "PageUp", "Home"].includes(event.key)) pauseFollowing();
  }

  function seekToNextMessage() {
    if (nextMessagePlayerMs == null) return;
    const video = videoRef.current;
    if (!video) return;
    const seekAndPlay = () => {
      video.currentTime = Math.max(0, nextMessagePlayerMs / 1000);
      setCurrentMs(nextMessagePlayerMs);
      void video.play().catch(() => undefined);
    };
    if (video.readyState >= 1) {
      seekAndPlay();
    } else {
      video.addEventListener("loadedmetadata", seekAndPlay, { once: true });
      void video.play().catch(() => undefined);
    }
    followingRef.current = true;
    pausedWindowStartIdRef.current = null;
    setFollowing(true);
  }

  useEffect(() => {
    setRoomMessages([]);
    setRoomDraft("");
    setRoomNotice("");
    const sessionId = watchSession?.id;
    if (!sessionId) return;
    let disposed = false;
    let inFlight = false;
    let lastId = 0;
    const load = async () => {
      if (inFlight || disposed) return;
      inFlight = true;
      try {
        const params = lastId ? `?after_id=${lastId}&page_size=200` : "?page_size=200";
        const res = await fetch(apiUrl(`/api/v1/site/watch-sessions/${sessionId}/messages${params}`), { cache: "no-store" });
        if (!res.ok) throw new Error(await res.text());
        const data = await res.json() as { items?: SiteWatchMessage[] };
        const incoming = data.items || [];
        if (disposed || !incoming.length) return;
        lastId = Math.max(lastId, ...incoming.map((item) => item.id));
        setRoomMessages((current) => {
          const seen = new Set(current.map((item) => item.id));
          return [...current, ...incoming.filter((item) => !seen.has(item.id))].slice(-500);
        });
      } catch (error) {
        if (!disposed) setRoomNotice(`Чат комнаты недоступен: ${error instanceof Error ? error.message : String(error)}`);
      } finally {
        inFlight = false;
      }
    };
    void load();
    const timer = window.setInterval(load, 1000);
    return () => { disposed = true; window.clearInterval(timer); };
  }, [watchSession?.id]);

  useEffect(() => {
    if (chatMode !== "live") return;
    const node = roomListRef.current;
    if (!node) return;
    const frame = window.requestAnimationFrame(() => { node.scrollTop = node.scrollHeight; });
    return () => window.cancelAnimationFrame(frame);
  }, [chatMode, roomMessages.length]);

  async function sendRoomMessage() {
    const sessionId = watchSession?.id;
    const body = roomDraft.trim();
    if (!sessionId || !body || roomSending) return;
    if (!authUser) { onLogin(); return; }
    setRoomSending(true);
    setRoomNotice("");
    try {
      const res = await fetch(apiUrl(`/api/v1/site/watch-sessions/${sessionId}/messages`), {
        method: "POST",
        credentials: "include",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ client_id: watchClientId, body }),
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(String(data.detail || data.message || `HTTP ${res.status}`));
      const message = data.message as SiteWatchMessage;
      setRoomMessages((current) => current.some((item) => item.id === message.id) ? current : [...current, message].slice(-500));
      setRoomDraft("");
    } catch (error) {
      setRoomNotice(`Не удалось отправить: ${error instanceof Error ? error.message : String(error)}`);
    } finally {
      setRoomSending(false);
    }
  }

  function openUserMenu(event: React.MouseEvent<HTMLElement>, message: SiteChatMessage) {
    event.stopPropagation();
    const position = chatUserMenuPosition(event.clientX, event.clientY);
    setUserMenu({ user: message, ...position });
  }

  return (
    <aside className="site-replay-chat">
      <div className="site-replay-chat-head">
        <div className="site-replay-chat-tabs" role="tablist" aria-label="Режим чата">
          <button type="button" className={chatMode === "recording" ? "is-active" : ""} onClick={() => setChatMode("recording")}>Чат записи</button>
          <button type="button" className={chatMode === "live" ? "is-active" : ""} onClick={() => setChatMode("live")}>Сейчас</button>
        </div>
        <span>{chatMode === "recording" ? (hasChat ? `${messageCount.toLocaleString("ru-RU")} сообщений` : "Чат не записан") : (watchSession ? `${roomMessages.length} в чате` : "Подключение…")}</span>
      </div>
      {chatMode === "recording" ? (
      <div className="site-replay-chat-list" ref={listRef} onScroll={handleChatScroll} onWheel={handleChatWheel} onPointerDown={handleChatPointerDown} onPointerUp={handleChatPointerEnd} onPointerCancel={handleChatPointerEnd} onTouchMove={handleChatTouchMove} onKeyDown={handleChatKeyDown} tabIndex={0}>
        {visible.length === 0 ? (
          hasChat && nextMessagePlayerMs != null ? (
            <button
              className="site-replay-chat-empty is-seekable"
              type="button"
              onClick={seekToNextMessage}
              title="Перейти к следующему сообщению"
            >
              Сообщения появятся через <strong>{nextMessageCountdown}</strong>
            </button>
          ) : (
            <div className="site-replay-chat-empty">
              {hasChat
                ? (chatStatus === "Загрузка…" || chatStatus === "Синхронизация…" ? "Загрузка сообщений…" : "В доступной части записи сообщений больше нет.")
                : "Для этого события чат не записан."}
            </div>
          )
        ) : visible.map((message) => {
          const badges = chatBadges(message.badges, badgeAssets);
          const reply = chatReplySummary(message.reply);
          const authorColor = safeChatColor(message.color);
          const author = message.chatter_name || message.chatter_login || "Гость";
          return (
            <div className={`site-replay-chat-message${message.is_action ? " is-action" : ""}`} key={message.id}>
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
                      onLoad={() => {
                        if (following && listRef.current) listRef.current.scrollTop = listRef.current.scrollHeight;
                      }}
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
                    onClick={(event) => openUserMenu(event, message)}
                    title={`Открыть профиль ${author}`}
                  >{author}</button>
                  {message.bits ? <span className="site-chat-bits">{message.bits.toLocaleString("ru-RU")} bits</span> : null}
                  <span className="site-chat-text" style={message.is_action && authorColor ? { color: authorColor } : undefined}>
                    <ChatMessageFragments message={message} onAssetLoad={() => { if (following && listRef.current) listRef.current.scrollTop = listRef.current.scrollHeight; }} />
                  </span>
                </div>
              </div>
            </div>
          );
        })}
      </div>
      ) : (
        <div className="site-room-chat">
          <div className="site-room-chat-list" ref={roomListRef}>
            {!watchSession ? <div className="site-replay-chat-empty">Подключаем комнату…</div> : null}
            {watchSession && roomMessages.length === 0 ? <div className="site-replay-chat-empty">В комнате пока никто не писал.</div> : null}
            {roomMessages.map((message) => (
              <div className="site-room-chat-message" key={message.id}>
                <div className="site-room-chat-avatar">
                  {message.user.avatar_url ? <img src={apiUrl(message.user.avatar_url)} alt="" /> : message.user.nickname.slice(0, 1).toUpperCase()}
                </div>
                <div>
                  <div className="site-room-chat-meta"><strong>{message.user.nickname}</strong><small>@{message.user.login}</small><time>{new Date(message.created_at).toLocaleTimeString("ru-RU", { hour: "2-digit", minute: "2-digit" })}</time></div>
                  <p>{message.body}</p>
                </div>
              </div>
            ))}
          </div>
          <div className="site-room-chat-compose">
            {authUser ? (
              <>
                <input
                  value={roomDraft}
                  maxLength={1000}
                  placeholder="Сообщение в комнату…"
                  disabled={!watchSession || roomSending}
                  onChange={(event) => setRoomDraft(event.target.value)}
                  onKeyDown={(event) => { if (event.key === "Enter" && !event.shiftKey) { event.preventDefault(); void sendRoomMessage(); } }}
                />
                <button type="button" disabled={!watchSession || !roomDraft.trim() || roomSending} onClick={() => void sendRoomMessage()}>{roomSending ? "…" : "→"}</button>
              </>
            ) : (
              <button className="site-room-chat-login" type="button" onClick={onLogin}>Войти, чтобы писать</button>
            )}
            {roomNotice ? <span>{roomNotice}</span> : null}
          </div>
        </div>
      )}
      {chatMode === "recording" && !following && visible.length > 0 ? (
        <button className="site-chat-follow" type="button" onClick={followCurrent}>↓ К текущему моменту</button>
      ) : null}
      {chatMode === "recording" && userMenu ? (
        <SiteChatUserContext
          eventId={eventId}
          user={userMenu.user}
          x={userMenu.x}
          y={userMenu.y}
          onClose={() => setUserMenu(null)}
          onMention={onMention}
          videoRef={videoRef}
        />
      ) : null}
    </aside>
  );
}

function StreamVaultHeader({
  query,
  searchResults,
  searchLoading,
  authUser,
  onQueryChange,
  onSubmit,
  onSearchResult,
  onBrand,
  onAdmin,
  onSiteAdmin,
  onLogin,
  onLogout,
}: {
  query: string;
  searchResults: SiteEvent[];
  searchLoading: boolean;
  authUser: AuthUser | null;
  onQueryChange: (value: string) => void;
  onSubmit: () => void;
  onSearchResult: (event: SiteEvent) => void;
  onBrand: () => void;
  onAdmin: () => void;
  onSiteAdmin: () => void;
  onLogin: () => void;
  onLogout: () => void;
}) {
  const [searchOpen, setSearchOpen] = useState(false);
  const trimmedQuery = query.trim();

  useEffect(() => {
    if (!trimmedQuery) setSearchOpen(false);
  }, [trimmedQuery]);

  return (
    <header className="streamvault-header">
      <div className="streamvault-header-inner">
        <StreamVaultAnimatedBrand onActivate={onBrand} />
        <div
          className="streamvault-search-wrap"
          onBlur={(event) => {
            if (!event.currentTarget.contains(event.relatedTarget as Node | null)) setSearchOpen(false);
          }}
        >
          <label className="streamvault-search">
            <span aria-hidden="true">⌕</span>
            <input
              type="search"
              value={query}
              onChange={(event) => { onQueryChange(event.target.value); setSearchOpen(true); }}
              onFocus={() => { if (trimmedQuery) setSearchOpen(true); }}
              onKeyDown={(event) => {
                if (event.key === "Escape") {
                  setSearchOpen(false);
                  event.currentTarget.blur();
                  return;
                }
                if (event.key === "Enter") {
                  event.preventDefault();
                  if (searchResults[0]) {
                    setSearchOpen(false);
                    onSearchResult(searchResults[0]);
                  } else {
                    onSubmit();
                  }
                }
              }}
              placeholder="Поиск"
              autoComplete="off"
            />
          </label>
          {searchOpen && trimmedQuery ? (
            <div className="streamvault-search-results" role="listbox" aria-label="Результаты поиска">
              {searchLoading ? <div className="streamvault-search-state">Ищем…</div> : null}
              {!searchLoading && searchResults.length === 0 ? <div className="streamvault-search-state">Ничего не найдено</div> : null}
              {!searchLoading ? searchResults.map((event) => (
                <button
                  type="button"
                  className="streamvault-search-result"
                  key={event.id}
                  onClick={() => { setSearchOpen(false); onSearchResult(event); }}
                  role="option"
                >
                  <span className="streamvault-search-result-cover">
                    {event.assets?.cover?.url ? <img src={apiUrl(event.assets.cover.url)} alt="" loading="lazy" /> : <span aria-hidden="true">▶</span>}
                  </span>
                  <span className="streamvault-search-result-copy">
                    <strong>{event.title}</strong>
                    <small>
                      {event.channel_display_name || event.channel_login || "Twitch"}
                      {event.source_started_at_utc ? ` · ${new Date(event.source_started_at_utc).toLocaleDateString("ru-RU")}` : ""}
                    </small>
                  </span>
                </button>
              )) : null}
            </div>
          ) : null}
        </div>
        <div className="streamvault-header-actions">
          <button type="button" className="streamvault-header-button" onClick={onAdmin}>StreamHub</button>
          <button type="button" className="streamvault-header-button admin" onClick={onSiteAdmin}>Админка</button>
          {authUser ? (
            <div className="streamvault-auth-user">
              <span className="streamvault-auth-avatar" aria-hidden="true">
                {authUser.avatar_url ? <img src={apiUrl(authUser.avatar_url)} alt="" /> : authUser.nickname.slice(0, 1).toUpperCase()}
              </span>
              <span className="streamvault-auth-user-copy"><strong>{authUser.nickname}</strong><small>@{authUser.login}</small></span>
              <button type="button" className="streamvault-auth-logout" onClick={onLogout}>Выйти</button>
            </div>
          ) : (
            <button type="button" className="streamvault-header-button primary" onClick={onLogin}>Войти</button>
          )}
        </div>
      </div>
    </header>
  );
}

function TelegramMark() {
  return (
    <svg className="streamvault-auth-telegram-icon" viewBox="0 0 24 24" aria-hidden="true">
      <path d="M21.7 3.2 18.5 19c-.24 1.12-.88 1.39-1.78.87l-4.88-3.6-2.35 2.27c-.26.26-.48.48-.98.48l.35-4.97 9.05-8.18c.39-.35-.09-.55-.61-.2L6.1 12.72l-4.81-1.5c-1.05-.33-1.07-1.05.22-1.55L20.32 2.4c.87-.32 1.63.2 1.38.8Z" />
    </svg>
  );
}

function AuthModal({
  initialMode = "login",
  onClose,
  onAuthenticated,
}: {
  initialMode?: AuthModalMode;
  onClose: () => void;
  onAuthenticated: (user: AuthUser) => void;
}) {
  const [mode, setMode] = useState<AuthModalMode>(initialMode);
  const [login, setLogin] = useState("");
  const [password, setPassword] = useState("");
  const [nickname, setNickname] = useState("");
  const [registerLogin, setRegisterLogin] = useState("");
  const [registerPassword, setRegisterPassword] = useState("");
  const [passwordConfirm, setPasswordConfirm] = useState("");
  const [avatar, setAvatar] = useState<File | null>(null);
  const [avatarPreview, setAvatarPreview] = useState<string | null>(null);
  const [telegramRegistration, setTelegramRegistration] = useState<TelegramRegistrationState | null>(null);
  const [twitchRegistration, setTwitchRegistration] = useState<TwitchRegistrationState | null>(null);
  const [busyProvider, setBusyProvider] = useState<"telegram" | "twitch" | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");

  useEffect(() => () => {
    if (avatarPreview) URL.revokeObjectURL(avatarPreview);
  }, [avatarPreview]);

  useEffect(() => {
    if (mode !== "register" || !telegramRegistration?.pollToken) return undefined;
    let cancelled = false;
    let timer = 0;

    const poll = async () => {
      try {
        const params = new URLSearchParams({ poll_token: telegramRegistration.pollToken });
        const res = await fetch(`${API}/api/v1/user-auth/telegram-registration/status?${params.toString()}`, {
          cache: "no-store",
          credentials: "include",
        });
        if (!res.ok) {
          if (res.status === 404) throw new Error("Регистрация не найдена. Запустите её заново");
          return;
        }
        const data = await res.json();
        if (cancelled) return;
        if (data.status === "confirmed" && data.user) {
          onAuthenticated(data.user as AuthUser);
          onClose();
          return;
        }
        if (data.status === "expired") {
          setTelegramRegistration(null);
          setNotice("");
          setError("Ссылка регистрации истекла. Нажмите подтверждение ещё раз");
          return;
        }
        if (data.telegram_started || data.status === "started") {
          setTelegramRegistration((current) => current ? { ...current, status: "started" } : current);
          setNotice("Бот открыт. Нажмите «Подтвердить регистрацию» в Telegram");
        } else {
          setNotice("Ожидаем подтверждение в Telegram…");
        }
      } catch (e) {
        if (!cancelled) console.warn("telegram registration status failed", e);
      }
      if (!cancelled) timer = window.setTimeout(() => void poll(), 1500);
    };

    void poll();
    return () => {
      cancelled = true;
      if (timer) window.clearTimeout(timer);
    };
  }, [mode, telegramRegistration?.pollToken, onAuthenticated, onClose]);

  useEffect(() => {
    if (mode !== "register" || !twitchRegistration?.pollToken) return undefined;
    let cancelled = false;
    let timer = 0;

    const poll = async () => {
      try {
        const params = new URLSearchParams({ poll_token: twitchRegistration.pollToken });
        const res = await fetch(`${API}/api/v1/user-auth/twitch-registration/status?${params.toString()}`, {
          cache: "no-store",
          credentials: "include",
        });
        if (!res.ok) {
          if (res.status === 404) throw new Error("Регистрация не найдена. Запустите её заново");
          return;
        }
        const data = await res.json();
        if (cancelled) return;
        if (data.status === "confirmed" && data.user) {
          onAuthenticated(data.user as AuthUser);
          onClose();
          return;
        }
        if (data.status === "expired") {
          setTwitchRegistration(null);
          setNotice("");
          setError("Ссылка Twitch-регистрации истекла. Нажмите подтверждение ещё раз");
          return;
        }
        if (data.status === "failed" || data.status === "denied") {
          setTwitchRegistration((current) => current ? { ...current, status: data.status } : current);
          setNotice("");
          setError(data.status === "denied" ? "Подтверждение Twitch отменено" : "Не удалось подтвердить регистрацию через Twitch");
          return;
        }
        setNotice("Ожидаем подтверждение в Twitch…");
      } catch (e) {
        if (!cancelled) console.warn("twitch registration status failed", e);
      }
      if (!cancelled) timer = window.setTimeout(() => void poll(), 1500);
    };

    void poll();
    return () => {
      cancelled = true;
      if (timer) window.clearTimeout(timer);
    };
  }, [mode, twitchRegistration?.pollToken, onAuthenticated, onClose]);

  function resetRegistration() {
    setTelegramRegistration(null);
    setTwitchRegistration(null);
    setNotice("");
    setError("");
  }

  async function responseError(res: Response) {
    try {
      const data = await res.json();
      return String(data.detail || data.message || `HTTP ${res.status}`);
    } catch {
      return (await res.text()) || `HTTP ${res.status}`;
    }
  }

  async function submitLogin(event: React.FormEvent) {
    event.preventDefault();
    setBusy(true); setError(""); setNotice("");
    try {
      const res = await fetch(`${API}/api/v1/user-auth/login`, {
        method: "POST",
        credentials: "include",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ login, password }),
      });
      if (!res.ok) throw new Error(await responseError(res));
      const data = await res.json();
      onAuthenticated(data.user as AuthUser);
      onClose();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally { setBusy(false); }
  }

  async function submitRegistration(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setError("");
    if (registerPassword !== passwordConfirm) {
      setError("Пароли не совпадают");
      return;
    }

    const submitter = (event.nativeEvent as SubmitEvent).submitter as HTMLButtonElement | null;
    const provider: "telegram" | "twitch" = submitter?.value === "twitch" ? "twitch" : "telegram";
    if (provider === "telegram" && telegramRegistration) {
      window.open(telegramRegistration.deepLink, "_blank", "noopener,noreferrer");
      return;
    }
    if (provider === "twitch" && twitchRegistration && twitchRegistration.status === "pending") {
      window.open(twitchRegistration.authorizeUrl, "_blank", "noopener,noreferrer");
      return;
    }

    // Open synchronously from the user's click so popup blockers do not eat the provider link.
    const providerWindow = window.open("about:blank", "_blank");
    const form = new FormData();
    form.set("nickname", nickname);
    form.set("login", registerLogin);
    form.set("password", registerPassword);
    form.set("password_confirm", passwordConfirm);
    form.set("provider", provider);
    if (avatar) form.set("avatar", avatar);
    setBusy(true);
    setBusyProvider(provider);
    setNotice("");
    try {
      const res = await fetch(`${API}/api/v1/user-auth/register`, {
        method: "POST",
        credentials: "include",
        body: form,
      });
      if (!res.ok) throw new Error(await responseError(res));
      const data = await res.json();

      if (provider === "telegram") {
        const next = {
          pollToken: String(data.poll_token || ""),
          deepLink: String(data.deep_link || ""),
          status: "pending" as const,
        };
        if (!next.pollToken || !next.deepLink) throw new Error("API не вернул Telegram-ссылку регистрации");
        setTwitchRegistration(null);
        setTelegramRegistration(next);
        if (providerWindow) {
          providerWindow.opener = null;
          providerWindow.location.href = next.deepLink;
          setNotice("Ожидаем подтверждение в Telegram…");
        } else {
          setNotice("Ссылка готова. Нажмите Telegram ещё раз, чтобы открыть подтверждение");
        }
      } else {
        const next = {
          pollToken: String(data.poll_token || ""),
          authorizeUrl: String(data.authorize_url || ""),
          status: "pending" as const,
        };
        if (!next.pollToken || !next.authorizeUrl) throw new Error("API не вернул Twitch-ссылку регистрации");
        setTelegramRegistration(null);
        setTwitchRegistration(next);
        if (providerWindow) {
          providerWindow.opener = null;
          providerWindow.location.href = next.authorizeUrl;
          setNotice("Ожидаем подтверждение в Twitch…");
        } else {
          setNotice("Ссылка готова. Нажмите Twitch ещё раз, чтобы открыть подтверждение");
        }
      }
    } catch (e) {
      if (providerWindow) providerWindow.close();
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
      setBusyProvider(null);
    }
  }


  return (
    <div className="streamvault-auth-backdrop" role="presentation" onMouseDown={(event) => { if (event.target === event.currentTarget) onClose(); }}>
      <section className="streamvault-auth-modal" role="dialog" aria-modal="true" aria-labelledby="streamvaultAuthTitle">
        <button type="button" className="streamvault-auth-close" onClick={onClose} aria-label="Закрыть">×</button>
        <div className="streamvault-auth-brand"><span>MRW HUB</span><strong id="streamvaultAuthTitle">{mode === "login" ? "Вход" : "Регистрация"}</strong></div>

        {mode === "login" ? (
          <form className="streamvault-auth-form" onSubmit={submitLogin}>
            <label><span>Логин</span><input autoFocus autoComplete="username" value={login} onChange={(e) => setLogin(e.target.value)} required /></label>
            <label><span>Пароль</span><input type="password" autoComplete="current-password" value={password} onChange={(e) => setPassword(e.target.value)} required /></label>
            {error ? <div className="streamvault-auth-error">{error}</div> : null}
            <button type="submit" className="streamvault-auth-submit" disabled={busy}>{busy ? "Входим…" : "Войти"}</button>
            <button type="button" className="streamvault-auth-switch" onClick={() => { setMode("register"); setError(""); setNotice(""); }}>Зарегистрироваться</button>
          </form>
        ) : null}

        {mode === "register" ? (
          <form className="streamvault-auth-form" onSubmit={submitRegistration}>
            <label className="streamvault-auth-avatar-picker">
              <span>Аватарка</span>
              <span className="streamvault-auth-avatar-preview">{avatarPreview ? <img src={avatarPreview} alt="Предпросмотр аватара" /> : (nickname.trim() ? <b>{nickname.trim().slice(0, 1).toUpperCase()}</b> : <i className="streamvault-auth-avatar-plus" aria-hidden="true" />)}</span>
              <input className="streamvault-auth-avatar-input" type="file" accept="image/*,.gif" aria-label="Выбрать аватар" onChange={(e) => {
                const next = e.target.files?.[0] || null;
                if (next && next.size > 10 * 1024 * 1024) {
                  e.currentTarget.value = "";
                  setError("Аватар больше 10 MiB");
                  return;
                }
                if (avatarPreview) URL.revokeObjectURL(avatarPreview);
                setAvatar(next);
                setAvatarPreview(next ? URL.createObjectURL(next) : null);
                resetRegistration();
              }} />
              <small>JPG/PNG/WebP/GIF · до 10 MiB</small>
            </label>
            <label><span>Ник</span><input autoComplete="nickname" value={nickname} onChange={(e) => { setNickname(e.target.value); resetRegistration(); }} minLength={2} maxLength={40} required /></label>
            <label><span>Логин</span><input autoComplete="username" value={registerLogin} onChange={(e) => { setRegisterLogin(e.target.value); resetRegistration(); }} pattern="[A-Za-z0-9_.-]{3,32}" required /></label>
            <label><span>Пароль</span><input type="password" autoComplete="new-password" value={registerPassword} onChange={(e) => { setRegisterPassword(e.target.value); resetRegistration(); }} minLength={8} required /></label>
            <label><span>Повторить пароль</span><input type="password" autoComplete="new-password" value={passwordConfirm} onChange={(e) => { setPasswordConfirm(e.target.value); resetRegistration(); }} minLength={8} required /></label>
            {notice ? <div className="streamvault-auth-notice">{notice}</div> : null}
            {error ? <div className="streamvault-auth-error">{error}</div> : null}
            <div className="streamvault-auth-confirm-grid">
              {telegramRegistration ? (
                <a className="streamvault-auth-submit telegram" href={telegramRegistration.deepLink} target="_blank" rel="noreferrer">
                  <span>Подтвердить регистрацию</span><TelegramMark />
                </a>
              ) : (
                <button type="submit" name="provider" value="telegram" className="streamvault-auth-submit telegram" disabled={busy}>
                  <span>{busyProvider === "telegram" ? "Готовим регистрацию…" : "Подтвердить регистрацию"}</span><TelegramMark />
                </button>
              )}
              {twitchRegistration?.status === "pending" ? (
                <a className="streamvault-auth-submit twitch" href={twitchRegistration.authorizeUrl} target="_blank" rel="noreferrer">
                  <span>Подтвердить регистрацию</span><TwitchIcon />
                </a>
              ) : (
                <button type="submit" name="provider" value="twitch" className="streamvault-auth-submit twitch" disabled={busy}>
                  <span>{busyProvider === "twitch" ? "Готовим регистрацию…" : "Подтвердить регистрацию"}</span><TwitchIcon />
                </button>
              )}
            </div>
          </form>
        ) : null}
      </section>
    </div>
  );
}

function SiteHeroPreviewVideo({ event, onActivate }: { event: SiteEvent; onActivate?: () => void }) {
  const videoRef = useRef<HTMLVideoElement | null>(null);
  const fadeTimerRef = useRef<number | null>(null);
  const restartTimerRef = useRef<number | null>(null);
  const restartingRef = useRef(false);
  const [loading, setLoading] = useState(true);
  const [failed, setFailed] = useState(false);
  const [restarting, setRestarting] = useState(false);

  useEffect(() => {
    const video = videoRef.current;
    if (!video || !event.playable || !event.playback_url) {
      setLoading(false);
      setFailed(true);
      return;
    }

    const source = apiUrl(event.playback_url);
    let hls: Hls | null = null;
    let disposed = false;
    let previewEnd = 10;
    setLoading(true);
    setFailed(false);
    restartingRef.current = false;
    setRestarting(false);

    const clearRestartTimers = () => {
      if (fadeTimerRef.current != null) window.clearTimeout(fadeTimerRef.current);
      if (restartTimerRef.current != null) window.clearTimeout(restartTimerRef.current);
      fadeTimerRef.current = null;
      restartTimerRef.current = null;
    };

    const playForward = async () => {
      if (disposed) return;
      video.muted = true;
      video.defaultMuted = true;
      video.playbackRate = 1;
      try {
        await video.play();
      } catch {
        // canplay/loadeddata will retry once enough media is buffered.
      }
    };

    const restartPreview = () => {
      if (disposed || restartingRef.current) return;
      restartingRef.current = true;
      clearRestartTimers();
      video.pause();
      setRestarting(true);
      fadeTimerRef.current = window.setTimeout(() => {
        if (disposed) return;
        video.currentTime = 0;
        void playForward();
        restartTimerRef.current = window.setTimeout(() => {
          if (!disposed) {
            restartingRef.current = false;
            setRestarting(false);
          }
        }, 250);
      }, 250);
    };

    const refreshPreviewEnd = () => {
      const duration = Number.isFinite(video.duration) && video.duration > 0 ? video.duration : 10;
      previewEnd = Math.max(0.5, Math.min(10, duration));
    };
    const onLoadedMetadata = () => {
      refreshPreviewEnd();
      if (video.currentTime > 0.05) video.currentTime = 0;
      setLoading(false);
      void playForward();
    };
    const onCanPlay = () => {
      setLoading(false);
      if (!restartingRef.current && video.paused) void playForward();
    };
    const onTimeUpdate = () => {
      if (restartingRef.current) return;
      if (video.currentTime >= previewEnd - 0.05) restartPreview();
    };
    const onEnded = () => restartPreview();
    const onError = () => {
      clearRestartTimers();
      setLoading(false);
      setFailed(true);
      restartingRef.current = false;
      setRestarting(false);
    };

    video.addEventListener("loadedmetadata", onLoadedMetadata);
    video.addEventListener("loadeddata", onCanPlay);
    video.addEventListener("canplay", onCanPlay);
    video.addEventListener("timeupdate", onTimeUpdate);
    video.addEventListener("ended", onEnded);
    video.addEventListener("error", onError);

    if (video.canPlayType("application/vnd.apple.mpegurl")) {
      video.src = source;
      video.load();
    } else if (Hls.isSupported()) {
      hls = new Hls({ startPosition: 0 });
      hls.loadSource(source);
      hls.attachMedia(video);
      hls.on(Hls.Events.MANIFEST_PARSED, () => {
        refreshPreviewEnd();
        void playForward();
      });
      hls.on(Hls.Events.ERROR, (_event, data) => {
        if (data.fatal) onError();
      });
    } else {
      onError();
    }

    return () => {
      disposed = true;
      clearRestartTimers();
      video.pause();
      video.removeEventListener("loadedmetadata", onLoadedMetadata);
      video.removeEventListener("loadeddata", onCanPlay);
      video.removeEventListener("canplay", onCanPlay);
      video.removeEventListener("timeupdate", onTimeUpdate);
      video.removeEventListener("ended", onEnded);
      video.removeEventListener("error", onError);
      hls?.destroy();
      video.removeAttribute("src");
      video.load();
    };
  }, [event.id, event.playable, event.playback_url]);

  return (
    <div
      className="streamvault-preview-main"
      role={onActivate ? "link" : undefined}
      tabIndex={onActivate ? 0 : undefined}
      aria-label={onActivate ? `Открыть ${event.title}` : "Первые 10 секунд записи"}
      onClick={onActivate}
      onKeyDown={onActivate ? (keyEvent) => {
        if (keyEvent.key === "Enter" || keyEvent.key === " ") {
          keyEvent.preventDefault();
          onActivate();
        }
      } : undefined}
    >
      {failed ? (
        <SiteArtwork event={event} wide label="VIDEO PREVIEW" src={event.assets?.frames?.[0]?.url} />
      ) : (
        <video ref={videoRef} muted playsInline preload="auto" aria-hidden="true" />
      )}
      {loading && !failed ? (
        <div className="streamvault-video-loading" role="status" aria-live="polite" aria-label="Видео загружается">
          <div className="streamvault-video-loading-card">
            <span className="streamvault-video-loading-spinner" aria-hidden="true" />
            <strong>Загрузка видео…</strong>
            <small>Подготавливаем воспроизведение</small>
          </div>
        </div>
      ) : null}
      <span className={`streamvault-preview-loop-fade${restarting ? " is-active" : ""}`} aria-hidden="true" />
    </div>
  );
}

function SiteArtwork({ event, wide = false, label, src }: { event: SiteEvent; wide?: boolean; label?: string; src?: string | null }) {
  const primaryCategory = event.categories[0]?.label || event.media_type.toUpperCase();
  return (
    <div className={`streamvault-artwork${wide ? " wide" : ""}${src ? " has-image" : ""}`} aria-hidden="true">
      {src ? <img src={apiUrl(src)} alt="" loading="lazy" /> : (
        <>
          <span>{label || primaryCategory}</span>
          <div><strong>{event.channel_display_name || event.channel_login || "TWITCH"}</strong><small>{event.media_type.toUpperCase()}</small></div>
        </>
      )}
    </div>
  );
}

function SiteEventTimecodes({
  items,
  videoRef,
}: {
  items: SiteTimecode[];
  videoRef: { current: HTMLVideoElement | null };
}) {
  if (!items.length) return null;
  return (
    <section className="streamvault-timecodes" aria-label="Таймкоды события">
      <div className="streamvault-timecodes-head"><strong>Таймкоды</strong><span>{items.length}</span></div>
      <div className="streamvault-timecodes-list">
        {items.map((item) => (
          <button
            type="button"
            key={item.id}
            onClick={() => {
              const video = videoRef.current;
              if (!video) return;
              video.currentTime = Math.max(0, item.offset_ms / 1000);
              void video.play().catch(() => undefined);
            }}
          >
            <time>{fmtMs(item.offset_ms)}</time>
            <strong>{item.title}</strong>
          </button>
        ))}
      </div>
    </section>
  );
}

function SiteWatchSessionsPanel({
  sessions,
  current,
  busyId,
  notice,
  onJoin,
}: {
  sessions: SiteWatchSession[];
  current: SiteWatchSession | null;
  busyId: string | null;
  notice: string;
  onJoin: (session: SiteWatchSession) => void;
}) {
  return (
    <section className="streamvault-watch-sessions" aria-labelledby="streamvaultWatchSessionsTitle">
      <div className="streamvault-watch-sessions-head">
        <div>
          <strong id="streamvaultWatchSessionsTitle">Совместный просмотр</strong>
          <span>play / pause / перемотка синхронизируются для всей комнаты</span>
        </div>
      </div>
      {notice ? <div className="streamvault-watch-sessions-notice">{notice}</div> : null}
      <div className="streamvault-watch-sessions-list">
        {sessions.length ? sessions.map((session) => {
          const isCurrent = current?.id === session.id;
          return (
            <button
              type="button"
              key={session.id}
              className={isCurrent ? "is-current" : ""}
              disabled={isCurrent || busyId === session.id}
              onClick={() => onJoin(session)}
            >
              <span className="streamvault-watch-session-main">
                <strong>{session.owner_label}</strong>
                <small>{session.participant_count} {session.participant_count === 1 ? "зритель" : "зрителей"} · {session.is_playing ? `▶ ${fmtMs(session.position_ms)}` : `Ⅱ ${fmtMs(session.position_ms)}`}</small>
              </span>
              <span className="streamvault-watch-session-action">{isCurrent ? "Вы здесь" : busyId === session.id ? "Подключаем…" : "Присоединиться"}</span>
            </button>
          );
        }) : <div className="streamvault-watch-sessions-empty">Активных сессий пока нет.</div>}
      </div>
    </section>
  );
}

function SiteAdminPanel({
  events,
  drafts,
  loading,
  actionKey,
  onDraftChange,
  onSaveTitle,
  onUpload,
  onSaveTimecodes,
  onToggleVisibility,
  onAddEvent,
  onClose,
}: {
  events: SiteAdminEvent[];
  drafts: Record<string, string>;
  loading: boolean;
  actionKey: string | null;
  onDraftChange: (eventId: string, value: string) => void;
  onSaveTitle: (eventId: string) => void;
  onUpload: (eventId: string, slot: string, file: File) => void;
  onSaveTimecodes: (eventId: string, items: Array<{ offset_ms: number; title: string }>) => Promise<boolean>;
  onToggleVisibility: (eventId: string, hidden: boolean) => void;
  onAddEvent: () => void;
  onClose: () => void;
}) {
  const assetSlots = [
    ["cover", "Cover"],
    ["frame_1", "Кадр 1"],
    ["frame_2", "Кадр 2"],
    ["frame_3", "Кадр 3"],
    ["frame_4", "Кадр 4"],
  ] as const;
  const assetFor = (event: SiteAdminEvent, slot: string) => slot === "cover"
    ? event.assets.cover
    : event.assets.frames[Math.max(0, Number(slot.split("_")[1]) - 1)];
  const [uploadTarget, setUploadTarget] = useState<{ eventId: string; slot: string; label: string } | null>(null);
  const [clipboardNotice, setClipboardNotice] = useState("");
  const uploadInputRef = useRef<HTMLInputElement | null>(null);
  const [timecodeEditorEventId, setTimecodeEditorEventId] = useState<string | null>(null);
  const [timecodeDrafts, setTimecodeDrafts] = useState<Record<string, Array<{ time: string; title: string }>>>({});

  useEffect(() => {
    if (!uploadTarget) return;
    const onPaste = (event: ClipboardEvent) => {
      const clipboardItems = Array.from(event.clipboardData?.items || []);
      const imageItem = clipboardItems.find((item) => item.kind === "file" && item.type.startsWith("image/"));
      const file = imageItem?.getAsFile() || Array.from(event.clipboardData?.files || []).find((item) => item.type.startsWith("image/"));
      if (!file) {
        setClipboardNotice("В буфере обмена нет изображения.");
        return;
      }
      event.preventDefault();
      const target = uploadTarget;
      setUploadTarget(null);
      setClipboardNotice("");
      onUpload(target.eventId, target.slot, file);
    };
    window.addEventListener("paste", onPaste);
    return () => window.removeEventListener("paste", onPaste);
  }, [onUpload, uploadTarget]);

  const openAssetPicker = (eventId: string, slot: string, label: string) => {
    setClipboardNotice("");
    setUploadTarget({ eventId, slot, label });
  };

  const submitPickedFile = (file: File | undefined) => {
    if (!file || !uploadTarget) return;
    const target = uploadTarget;
    setUploadTarget(null);
    setClipboardNotice("");
    onUpload(target.eventId, target.slot, file);
  };

  const openTimecodeEditor = (event: SiteAdminEvent) => {
    setTimecodeDrafts((current) => ({
      ...current,
      [event.id]: current[event.id] || (event.timecodes.length
        ? event.timecodes.map((item) => ({ time: fmtMs(item.offset_ms), title: item.title }))
        : [{ time: "00:00:00", title: "" }]),
    }));
    setTimecodeEditorEventId(event.id);
  };

  const updateTimecodeDraft = (eventId: string, index: number, key: "time" | "title", value: string) => {
    setTimecodeDrafts((current) => ({
      ...current,
      [eventId]: (current[eventId] || []).map((item, itemIndex) => itemIndex === index ? { ...item, [key]: value } : item),
    }));
  };

  const removeTimecodeDraft = (eventId: string, index: number) => {
    setTimecodeDrafts((current) => ({
      ...current,
      [eventId]: (current[eventId] || []).filter((_item, itemIndex) => itemIndex !== index),
    }));
  };

  const saveTimecodes = async (eventId: string) => {
    const drafts = timecodeDrafts[eventId] || [];
    const parsed = drafts.map((item) => ({ offset_ms: parseTimecodeText(item.time), title: item.title.trim() }));
    if (parsed.some((item) => item.offset_ms == null || !item.title)) return;
    const saved = await onSaveTimecodes(eventId, parsed.map((item) => ({ offset_ms: item.offset_ms as number, title: item.title })));
    if (saved) setTimecodeEditorEventId(null);
  };

  return (
    <div className="site-modal-backdrop site-admin-backdrop" role="presentation" onMouseDown={(event) => { if (event.target === event.currentTarget) onClose(); }}>
      <div className="site-modal site-admin-modal" role="dialog" aria-modal="true" aria-labelledby="siteAdminTitle">
        <div className="site-admin-head">
          <div><div className="section-label">MRW HUB ADMIN</div><h2 id="siteAdminTitle">События</h2></div>
          <div className="site-admin-head-actions">
            <button type="button" className="primary" onClick={onAddEvent}>+ Добавить событие</button>
            <button type="button" onClick={onClose}>Закрыть</button>
          </div>
        </div>
        <p className="site-admin-storage-note">Cover и четыре preview-кадра хранятся отдельно от БД в persistent storage. В БД остаются только метаданные и ссылки.</p>
        {loading ? <div className="site-empty compact"><span className="site-inline-spinner" /> Загружаю события…</div> : events.length === 0 ? (
          <div className="site-empty compact">Нет событий с готовым Telegram-backed видео.</div>
        ) : (
          <div className="site-admin-event-list">
            {events.map((event) => {
              const assetCount = Number(Boolean(event.assets.cover)) + event.assets.frames.filter(Boolean).length;
              const eventTimecodes = timecodeDrafts[event.id] || [];
              const timecodesValid = eventTimecodes.every((item) => parseTimecodeText(item.time) != null && Boolean(item.title.trim()));
              return (
                <details className="site-admin-event" key={event.id}>
                  <summary className="site-admin-event-summary">
                    <div className="site-admin-summary-cell is-status">
                      <span>Статус</span>
                      <strong className={`site-admin-publish-state ${event.published && !event.hidden ? "is-published" : ""}${event.hidden ? " is-hidden" : ""}`}>
                        {!event.published ? "Не опубликовано" : event.hidden ? "Скрыто" : "Опубликовано"}
                      </strong>
                    </div>
                    <div className="site-admin-summary-cell is-date">
                      <span>Дата</span>
                      <strong>{event.source_started_at_utc ? new Date(event.source_started_at_utc).toLocaleDateString("ru-RU") : "—"}</strong>
                    </div>
                    <div className="site-admin-summary-cell is-categories">
                      <span>Категории</span>
                      <strong>{event.categories.length ? event.categories.map((category) => category.label).join(" · ") : "Без категории"}</strong>
                    </div>
                    <div className="site-admin-summary-cell is-title">
                      <span>Название</span>
                      <strong>{event.display_title}</strong>
                    </div>
                    <div className="site-admin-summary-cell is-assets">
                      <span>Изображения</span>
                      <strong className={`site-admin-assets-state ${event.assets.complete ? "is-complete" : ""}`}>{assetCount}/5</strong>
                    </div>
                  </summary>
                  <div className="site-admin-event-body">
                    <div className="site-admin-source-meta">
                      <span>Исходное название: <strong>{event.source_title || "—"}</strong></span>
                      <span>TG {event.linked_parts}/{event.ready_parts}</span>
                    </div>
                    {event.published ? (
                      <div className="site-admin-publication-actions">
                        <span>{event.hidden ? "Публикация скрыта с публичного сайта. Данные и оформление сохранены." : "Публикация видна на публичном сайте."}</span>
                        <button
                          type="button"
                          className={event.hidden ? "is-show" : "is-hide"}
                          disabled={Boolean(actionKey)}
                          onClick={() => onToggleVisibility(event.id, !event.hidden)}
                        >
                          {actionKey === `visibility:${event.id}` ? "Сохраняю…" : event.hidden ? "Показать публикацию" : "Скрыть публикацию"}
                        </button>
                      </div>
                    ) : null}
                    <div className="site-admin-title-editor">
                      <label>
                        <span>Отображаемое название</span>
                        <input value={drafts[event.id] ?? event.display_title} maxLength={1024} onChange={(e) => onDraftChange(event.id, e.target.value)} />
                      </label>
                      <button type="button" disabled={actionKey === `title:${event.id}` || !(drafts[event.id] ?? event.display_title).trim() || (drafts[event.id] ?? event.display_title).trim() === event.display_title} onClick={() => onSaveTitle(event.id)}>
                        {actionKey === `title:${event.id}` ? "Сохраняю…" : "Сохранить"}
                      </button>
                    </div>
                    <div className="site-admin-assets">
                      {assetSlots.map(([slot, label]) => {
                        const asset = assetFor(event, slot);
                        const busy = actionKey === `asset:${event.id}:${slot}`;
                        return (
                          <button
                            type="button"
                            className={`site-admin-asset ${asset ? "has-asset" : ""}`}
                            key={slot}
                            disabled={Boolean(actionKey)}
                            onClick={() => openAssetPicker(event.id, slot, label)}
                          >
                            <span>{label}</span>
                            <div>{asset ? <img src={apiUrl(asset.url)} alt="" /> : <b>+</b>}</div>
                            {busy || asset ? <small>{busy ? "Загрузка…" : `${asset?.width}×${asset?.height} · заменить`}</small> : null}
                          </button>
                        );
                      })}
                    </div>
                    <div className="site-admin-timecodes-actions">
                      <button type="button" className="site-admin-timecodes-open" onClick={() => openTimecodeEditor(event)}>Добавить таймкоды</button>
                    </div>
                    {timecodeEditorEventId === event.id ? (
                      <div className="site-admin-timecodes-editor">
                        <div className="site-admin-timecodes-editor-head">
                          <div><strong>Таймкоды события</strong><span>Время указывается относительно публичного видеоплеера.</span></div>
                          <button type="button" onClick={() => setTimecodeEditorEventId(null)}>Закрыть</button>
                        </div>
                        <div className="site-admin-timecode-rows">
                          {eventTimecodes.map((item, index) => (
                            <div className="site-admin-timecode-row" key={`${event.id}:${index}`}>
                              <label>
                                <span>Время</span>
                                <input
                                  value={item.time}
                                  inputMode="numeric"
                                  placeholder="00:00:00"
                                  onChange={(e) => updateTimecodeDraft(event.id, index, "time", e.target.value)}
                                />
                              </label>
                              <label>
                                <span>Название таймкода</span>
                                <input
                                  value={item.title}
                                  maxLength={255}
                                  placeholder="Например: Начало матча"
                                  onChange={(e) => updateTimecodeDraft(event.id, index, "title", e.target.value)}
                                />
                              </label>
                              <button type="button" className="site-admin-timecode-remove" onClick={() => removeTimecodeDraft(event.id, index)} aria-label="Удалить таймкод">×</button>
                            </div>
                          ))}
                        </div>
                        <div className="site-admin-timecodes-footer">
                          <button
                            type="button"
                            onClick={() => setTimecodeDrafts((current) => ({ ...current, [event.id]: [...(current[event.id] || []), { time: "00:00:00", title: "" }] }))}
                          >+ Ещё таймкод</button>
                          <button
                            type="button"
                            className="primary"
                            disabled={Boolean(actionKey) || !timecodesValid}
                            onClick={() => void saveTimecodes(event.id)}
                          >{actionKey === `timecodes:${event.id}` ? "Сохраняю…" : "Сохранить таймкоды"}</button>
                        </div>
                      </div>
                    ) : null}
                  </div>
                </details>
              );
            })}
          </div>
        )}
        {uploadTarget ? (
          <div className="site-admin-asset-picker-backdrop" role="presentation" onMouseDown={(event) => { if (event.target === event.currentTarget) setUploadTarget(null); }}>
            <div className="site-admin-asset-picker" role="dialog" aria-modal="true" aria-labelledby="siteAdminAssetPickerTitle">
              <span className="site-admin-asset-picker-kicker">{uploadTarget.label}</span>
              <h3 id="siteAdminAssetPickerTitle">Добавить изображение</h3>
              <p>Выберите файл на компьютере или вставьте изображение прямо из буфера обмена.</p>
              <div className="site-admin-asset-paste-hint" aria-label="Вставить изображение из буфера сочетанием Control V">
                <kbd>Ctrl</kbd><span>+</span><kbd>V</kbd>
              </div>
              {clipboardNotice ? <div className="site-admin-asset-picker-notice">{clipboardNotice}</div> : null}
              <div className="site-admin-asset-picker-actions">
                <button type="button" className="primary" onClick={() => uploadInputRef.current?.click()}>Выбрать файл</button>
                <button type="button" onClick={() => setUploadTarget(null)}>Отмена</button>
              </div>
              <input
                ref={uploadInputRef}
                className="site-admin-asset-picker-input"
                type="file"
                accept="image/jpeg,image/png,image/webp"
                onChange={(event) => { submitPickedFile(event.target.files?.[0]); event.currentTarget.value = ""; }}
              />
            </div>
          </div>
        ) : null}
      </div>
    </div>
  );
}


function App() {
  const [events, setEvents] = useState<MediaEvent[]>([]);
  const [trashEvents, setTrashEvents] = useState<MediaEvent[]>([]);
  const [eventPurgeJobs, setEventPurgeJobs] = useState<EventPurgeJob[]>([]);
  const [view, setView] = useState<ViewMode>(() => new URLSearchParams(window.location.search).has("site_event") ? "site" : "events");
  const [expandedIds, setExpandedIds] = useState<string[]>([]);
  const [eventTitleMode, setEventTitleMode] = useState<EventTitleMode>(() => storedEventTitleMode());
  const [editingEventTitleId, setEditingEventTitleId] = useState<string | null>(null);
  const [eventTitleDraft, setEventTitleDraft] = useState("");
  const [selected, setSelected] = useState<Session | null>(null);
  const [selectedVideo, setSelectedVideo] = useState<VideoSession | null>(null);
  const [videoRuns, setVideoRuns] = useState<VideoRun[]>([]);
  const [videoSegments, setVideoSegments] = useState<VideoSegment[]>([]);
  const [videoSessions, setVideoSessions] = useState<VideoSession[]>([]);
  const [videoParts, setVideoParts] = useState<VideoPart[]>([]);
  const [segmentReplacementPartId, setSegmentReplacementPartId] = useState<string | null>(null);
  const [segmentReplacementDirectory, setSegmentReplacementDirectory] = useState("");
  const [partMode, setPartMode] = useState<"manual" | "target" | "count">("manual");
  const [partFromSegment, setPartFromSegment] = useState(1);
  const [partToSegment, setPartToSegment] = useState(1);
  const [partTargetMib, setPartTargetMib] = useState(() => storedPositiveInt(PART_TARGET_MIB_KEY, 1990));
  const [partSegmentCount, setPartSegmentCount] = useState(() => storedPositiveInt(PART_SEGMENT_COUNT_KEY, 10));
  const [partPlan, setPartPlan] = useState<PartPlan | null>(null);
  const [partPlanAll, setPartPlanAll] = useState<PartPlanAll | null>(null);
  const [partAction, setPartAction] = useState(false);
  const [proxyTargetMib, setProxyTargetMib] = useState(() => storedPositiveInt(PROXY_PART_TARGET_MIB_KEY, 1990));
  const [proxyPlan, setProxyPlan] = useState<ProxyPartPlanAll | null>(null);
  const [proxyAction, setProxyAction] = useState(false);
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
  const [siteSearchResults, setSiteSearchResults] = useState<SiteEvent[]>([]);
  const [siteSearchLoading, setSiteSearchLoading] = useState(false);
  const [selectedSiteEvent, setSelectedSiteEvent] = useState<SiteEvent | null>(null);
  const siteVideoRef = useRef<HTMLVideoElement | null>(null);
  const [sitePublisherOpen, setSitePublisherOpen] = useState(false);
  const [siteAdminOpen, setSiteAdminOpen] = useState(false);
  const [siteAdminEvents, setSiteAdminEvents] = useState<SiteAdminEvent[]>([]);
  const [siteAdminDrafts, setSiteAdminDrafts] = useState<Record<string, string>>({});
  const [siteAdminLoading, setSiteAdminLoading] = useState(false);
  const [siteAdminActionKey, setSiteAdminActionKey] = useState<string | null>(null);
  const [siteAvailableEvents, setSiteAvailableEvents] = useState<SiteAvailableEvent[]>([]);
  const [sitePublishEventId, setSitePublishEventId] = useState("");
  const [sitePublishCategories, setSitePublishCategories] = useState<string[]>([]);
  const [siteAction, setSiteAction] = useState(false);
  const [sitePreviewOverlay, setSitePreviewOverlay] = useState<{ items: Array<{ src: string; label: string }>; index: number } | null>(null);
  const [siteCommentDraft, setSiteCommentDraft] = useState("");
  const [siteCommentColor, setSiteCommentColor] = useState(storedSiteCommentColor);
  const [siteCommentUnderlined, setSiteCommentUnderlined] = useState(false);
  const [siteCommentReplyTo, setSiteCommentReplyTo] = useState<{ id: number; nickname: string; login: string } | null>(null);
  const [siteCommentReactionSending, setSiteCommentReactionSending] = useState<number | null>(null);
  const [siteCommentReactionPickerId, setSiteCommentReactionPickerId] = useState<number | null>(null);
  const [siteCommentNotice, setSiteCommentNotice] = useState("");
  const [siteComments, setSiteComments] = useState<SiteUserComment[]>([]);
  const [siteCommentsLoading, setSiteCommentsLoading] = useState(false);
  const [siteCommentSending, setSiteCommentSending] = useState(false);
  const [siteCommentCooldownUntil, setSiteCommentCooldownUntil] = useState(0);
  const [siteCommentClock, setSiteCommentClock] = useState(() => Date.now());
  const siteCommentRef = useRef<HTMLTextAreaElement | null>(null);
  const siteWatchClientIdRef = useRef(storedSiteWatchClientId());
  const [siteWatchSessions, setSiteWatchSessions] = useState<SiteWatchSession[]>([]);
  const [siteWatchSession, setSiteWatchSession] = useState<SiteWatchSession | null>(null);
  const [siteWatchBusyId, setSiteWatchBusyId] = useState<string | null>(null);
  const [siteWatchNotice, setSiteWatchNotice] = useState("");
  const siteWatchApplyingRemoteRef = useRef(false);
  const siteWatchLastAppliedVersionRef = useRef(0);
  const siteWatchPublishInFlightRef = useRef(false);
  const siteWatchCurrentRef = useRef<SiteWatchSession | null>(null);
  const [authUser, setAuthUser] = useState<AuthUser | null>(null);
  const [authModalOpen, setAuthModalOpen] = useState(false);

  useEffect(() => {
    const loadCurrentUser = async () => {
      try {
        let res = await fetch(`${API}/api/v1/user-auth/me`, { cache: "no-store", credentials: "include" });
        if (res.status === 401) {
          const refreshed = await fetch(`${API}/api/v1/user-auth/refresh`, { method: "POST", credentials: "include" });
          if (refreshed.ok) res = await fetch(`${API}/api/v1/user-auth/me`, { cache: "no-store", credentials: "include" });
        }
        if (!res.ok) return;
        const data = await res.json();
        setAuthUser(data.user || null);
      } catch { /* guest mode */ }
    };
    void loadCurrentUser();
  }, []);

  useEffect(() => {
    if (!sitePreviewOverlay) return;
    const previousOverflow = document.body.style.overflow;
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        setSitePreviewOverlay(null);
        return;
      }
      if (event.key === "ArrowLeft" || event.key === "ArrowRight") {
        event.preventDefault();
        setSitePreviewOverlay((current) => {
          if (!current || current.items.length < 2) return current;
          const delta = event.key === "ArrowLeft" ? -1 : 1;
          return { ...current, index: (current.index + delta + current.items.length) % current.items.length };
        });
      }
    };
    document.body.style.overflow = "hidden";
    window.addEventListener("keydown", onKeyDown);
    return () => {
      document.body.style.overflow = previousOverflow;
      window.removeEventListener("keydown", onKeyDown);
    };
  }, [sitePreviewOverlay]);

  useEffect(() => {
    if (!selectedSiteEvent?.id) {
      setSiteComments([]);
      setSiteCommentsLoading(false);
      return;
    }
    const controller = new AbortController();
    setSiteCommentsLoading(true);
    void (async () => {
      try {
        const res = await fetch(`${API}/api/v1/site/events/${selectedSiteEvent.id}/comments?page_size=100`, {
          cache: "no-store",
          credentials: "include",
          signal: controller.signal,
        });
        if (!res.ok) throw new Error(await res.text());
        const data = await res.json();
        setSiteComments((data.items || []) as SiteUserComment[]);
      } catch (e) {
        if (!(e instanceof DOMException && e.name === "AbortError")) console.warn("comments unavailable", e);
      } finally {
        if (!controller.signal.aborted) setSiteCommentsLoading(false);
      }
    })();
    return () => controller.abort();
  }, [selectedSiteEvent?.id]);

  useEffect(() => {
    siteWatchCurrentRef.current = siteWatchSession;
  }, [siteWatchSession]);

  useEffect(() => {
    const event = selectedSiteEvent;
    if (!event?.playable || !event.playback_url) {
      siteWatchCurrentRef.current = null;
      setSiteWatchSession(null);
      setSiteWatchSessions([]);
      setSiteWatchNotice("");
      return;
    }
    let cancelled = false;
    setSiteWatchSessions([]);
    setSiteWatchSession(null);
    siteWatchCurrentRef.current = null;
    siteWatchLastAppliedVersionRef.current = 0;
    setSiteWatchNotice("");
    const timer = window.setTimeout(() => {
      if (cancelled) return;
      const requestedRoomId = new URLSearchParams(window.location.search).get("room");
      if (requestedRoomId) void joinSiteWatchSessionById(requestedRoomId, event.id, true);
      else void createSiteWatchSession(event.id);
    }, 180);
    return () => {
      cancelled = true;
      window.clearTimeout(timer);
    };
    // Event changes should create a fresh room. Auth changes only affect the display name on the next heartbeat/create.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selectedSiteEvent?.id, selectedSiteEvent?.playable, selectedSiteEvent?.playback_url]);

  useEffect(() => {
    const eventId = selectedSiteEvent?.id;
    if (!eventId) return;
    const controller = new AbortController();
    const load = () => {
      void loadSiteWatchSessions(eventId, controller.signal).catch((error) => {
        if (!(error instanceof DOMException && error.name === "AbortError")) console.warn("watch session list failed", error);
      });
    };
    load();
    const timer = window.setInterval(load, 3000);
    return () => {
      controller.abort();
      window.clearInterval(timer);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selectedSiteEvent?.id]);

  useEffect(() => {
    const session = siteWatchSession;
    if (!session) return;
    let disposed = false;
    const heartbeat = async () => {
      try {
        const res = await fetch(`${API}/api/v1/site/watch-sessions/${session.id}/heartbeat`, {
          method: "POST",
          credentials: "include",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ client_id: siteWatchClientIdRef.current }),
        });
        const data = await res.json().catch(() => ({}));
        if (!res.ok) throw new Error(String(data.detail || data.message || `HTTP ${res.status}`));
        if (disposed) return;
        const next = data.session as SiteWatchSession;
        applySiteWatchState(next, data.server_now_utc || null);
        siteWatchCurrentRef.current = next;
        setSiteWatchSession(next);
      } catch (error) {
        if (!disposed) setSiteWatchNotice(`Синхронизация приостановлена: ${error instanceof Error ? error.message : String(error)}`);
      }
    };
    void heartbeat();
    const timer = window.setInterval(heartbeat, 700);
    return () => {
      disposed = true;
      window.clearInterval(timer);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [siteWatchSession?.id]);

  useEffect(() => {
    if (!siteWatchSession) return;
    const video = siteVideoRef.current;
    if (!video) return;
    const onLocalControl = () => {
      if (siteWatchApplyingRemoteRef.current) return;
      void publishSiteWatchState();
    };
    video.addEventListener("play", onLocalControl);
    video.addEventListener("pause", onLocalControl);
    video.addEventListener("seeked", onLocalControl);
    const timer = window.setInterval(() => {
      if (!video.paused && !video.ended) void publishSiteWatchState();
    }, 2500);
    return () => {
      video.removeEventListener("play", onLocalControl);
      video.removeEventListener("pause", onLocalControl);
      video.removeEventListener("seeked", onLocalControl);
      window.clearInterval(timer);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [siteWatchSession?.id]);

  useEffect(() => {
    if (siteCommentCooldownUntil <= Date.now()) {
      setSiteCommentClock(Date.now());
      return undefined;
    }
    setSiteCommentClock(Date.now());
    const timer = window.setInterval(() => {
      const now = Date.now();
      setSiteCommentClock(now);
      if (now >= siteCommentCooldownUntil) window.clearInterval(timer);
    }, 250);
    return () => window.clearInterval(timer);
  }, [siteCommentCooldownUntil]);

  async function logoutUser() {
    try { await fetch(`${API}/api/v1/user-auth/logout`, { method: "POST", credentials: "include" }); } finally { setAuthUser(null); }
  }

  useEffect(() => {
    try { window.localStorage.setItem(PART_TARGET_MIB_KEY, String(partTargetMib)); } catch { /* browser storage unavailable */ }
  }, [partTargetMib]);

  useEffect(() => {
    try { window.localStorage.setItem(PART_SEGMENT_COUNT_KEY, String(partSegmentCount)); } catch { /* browser storage unavailable */ }
  }, [partSegmentCount]);

  useEffect(() => {
    try { window.localStorage.setItem(PROXY_PART_TARGET_MIB_KEY, String(proxyTargetMib)); } catch { /* browser storage unavailable */ }
  }, [proxyTargetMib]);

  useEffect(() => {
    const query = siteQuery.trim();
    if (view !== "site" || !query) {
      setSiteSearchResults([]);
      setSiteSearchLoading(false);
      return;
    }

    const controller = new AbortController();
    const timer = window.setTimeout(async () => {
      setSiteSearchLoading(true);
      try {
        const params = new URLSearchParams({ q: query, limit: "8" });
        const res = await fetch(`${API}/api/v1/site/feed?${params.toString()}`, { cache: "no-store", signal: controller.signal });
        if (!res.ok) throw new Error(await res.text());
        const data = await res.json();
        setSiteSearchResults(data.items || []);
      } catch (error) {
        if (!(error instanceof DOMException && error.name === "AbortError")) {
          console.warn("site search failed", error);
          setSiteSearchResults([]);
        }
      } finally {
        if (!controller.signal.aborted) setSiteSearchLoading(false);
      }
    }, 180);

    return () => {
      window.clearTimeout(timer);
      controller.abort();
    };
  }, [siteQuery, view]);

  useEffect(() => {
    const eventId = new URLSearchParams(window.location.search).get("site_event");
    if (!eventId) return;
    const controller = new AbortController();
    fetch(`${API}/api/v1/site/events/${encodeURIComponent(eventId)}`, { cache: "no-store", signal: controller.signal })
      .then(async (res) => {
        if (!res.ok) throw new Error(await res.text());
        return await res.json() as SiteEvent;
      })
      .then((event) => {
        setView("site");
        setSelectedSiteEvent(event);
        setSiteCommentDraft("");
        setSiteCommentNotice("");
      })
      .catch((error) => {
        if (!(error instanceof DOMException && error.name === "AbortError")) setError(String(error));
      });
    return () => controller.abort();
  }, []);

  function changeEventTitleMode(mode: EventTitleMode) {
    setEventTitleMode(mode);
    try { window.localStorage.setItem(EVENT_TITLE_MODE_KEY, mode); } catch { /* browser storage unavailable */ }
  }

  function beginEventTitleEdit(event: MediaEvent) {
    setEditingEventTitleId(event.id);
    setEventTitleDraft(event.display_title?.trim() || event.title?.trim() || fallbackEventTitle(event));
  }

  function cancelEventTitleEdit() {
    setEditingEventTitleId(null);
    setEventTitleDraft("");
  }

  async function saveEventDisplayTitle(event: MediaEvent) {
    const displayTitle = eventTitleDraft.trim();
    if (!displayTitle) return;
    const actionKey = `event-title:${event.id}`;
    setActionId(actionKey);
    try {
      const res = await fetch(`${API}/api/v1/events/${event.id}/display-title`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ display_title: displayTitle }),
      });
      if (!res.ok) throw new Error(await res.text());
      const updated = await res.json() as MediaEvent;
      setEvents((current) => current.map((item) => item.id === event.id ? { ...item, display_title: updated.display_title } : item));
      setSiteAdminEvents((current) => current.map((item) => item.id === event.id ? { ...item, title: displayTitle, display_title: displayTitle } : item));
      setSiteEvents((current) => current.map((item) => item.id === event.id ? { ...item, title: displayTitle, display_title: displayTitle } : item));
      setSelectedSiteEvent((current) => current?.id === event.id ? { ...current, title: displayTitle, display_title: displayTitle } : current);
      cancelEventTitleEdit();
      setError(null);
    } catch (e) {
      setError(String(e));
    } finally {
      setActionId(null);
    }
  }

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

  async function loadSiteFeed(category = siteCategory, query = "") {
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

  function siteEventUrl(eventId: string) {
    const url = new URL(window.location.href);
    url.search = "";
    url.hash = "";
    url.searchParams.set("site_event", eventId);
    return url.toString();
  }

  function setSiteRoomInUrl(eventId: string, roomId: string | null) {
    const url = new URL(window.location.href);
    url.searchParams.set("site_event", eventId);
    if (roomId) url.searchParams.set("room", roomId);
    else url.searchParams.delete("room");
    window.history.replaceState(null, "", `${url.pathname}${url.search}${url.hash}`);
  }

  function openSiteEvent(event: SiteEvent) {
    window.location.assign(siteEventUrl(event.id));
  }

  function openSiteEventNewTab(event: SiteEvent) {
    window.open(siteEventUrl(event.id), "_blank", "noopener,noreferrer");
  }

  function openSitePreviewFrame(event: SiteEvent, frameIndex: number) {
    const items = (event.assets?.frames || []).flatMap((frame, index) => frame?.url ? [{ src: frame.url, label: `${event.title} · кадр ${index + 1}` }] : []);
    if (!items.length) return;
    const targetUrl = event.assets?.frames?.[frameIndex]?.url || "";
    const index = Math.max(0, items.findIndex((item) => item.src === targetUrl));
    setSitePreviewOverlay({ items, index });
  }

  function closeSiteEvent() {
    void leaveSiteWatchSession(siteWatchCurrentRef.current);
    siteWatchCurrentRef.current = null;
    setSiteWatchSession(null);
    setSiteWatchSessions([]);
    setSelectedSiteEvent(null);
    const url = new URL(window.location.href);
    if (url.searchParams.has("site_event")) {
      url.searchParams.delete("site_event");
      url.searchParams.delete("room");
      window.history.replaceState(null, "", `${url.pathname}${url.search}${url.hash}`);
    }
  }

  async function loadSiteWatchSessions(eventId: string, signal?: AbortSignal) {
    const res = await fetch(`${API}/api/v1/site/events/${eventId}/watch-sessions`, { cache: "no-store", signal });
    if (!res.ok) throw new Error(await res.text());
    const data = await res.json() as { items?: SiteWatchSession[] };
    setSiteWatchSessions(data.items || []);
  }

  async function leaveSiteWatchSession(session: SiteWatchSession | null) {
    if (!session) return;
    try {
      await fetch(`${API}/api/v1/site/watch-sessions/${session.id}/leave`, {
        method: "POST",
        credentials: "include",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ client_id: siteWatchClientIdRef.current }),
        keepalive: true,
      });
    } catch {
      // Session expiry is also handled server-side, so leaving is best-effort.
    }
  }

  async function createSiteWatchSession(eventId = selectedSiteEvent?.id || "") {
    if (!eventId) return;
    const previous = siteWatchCurrentRef.current;
    setSiteWatchBusyId("new");
    setSiteWatchNotice("");
    try {
      if (previous) await leaveSiteWatchSession(previous);
      const video = siteVideoRef.current;
      const res = await fetch(`${API}/api/v1/site/events/${eventId}/watch-sessions`, {
        method: "POST",
        credentials: "include",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          client_id: siteWatchClientIdRef.current,
          position_ms: Math.max(0, Math.round((video?.currentTime || 0) * 1000)),
          is_playing: Boolean(video && !video.paused && !video.ended),
        }),
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(String(data.detail || data.message || `HTTP ${res.status}`));
      const next = data.session as SiteWatchSession;
      siteWatchCurrentRef.current = next;
      siteWatchLastAppliedVersionRef.current = next.state_version || 0;
      setSiteWatchSession(next);
      setSiteRoomInUrl(eventId, null);
      await loadSiteWatchSessions(eventId);
    } catch (error) {
      setSiteWatchNotice(`Не удалось создать сессию: ${error instanceof Error ? error.message : String(error)}`);
    } finally {
      setSiteWatchBusyId(null);
    }
  }

  async function joinSiteWatchSessionById(sessionId: string, eventId: string, fallbackToOwn = false) {
    if (siteWatchCurrentRef.current?.id === sessionId) return;
    setSiteWatchBusyId(sessionId);
    setSiteWatchNotice("");
    try {
      const previous = siteWatchCurrentRef.current;
      if (previous) await leaveSiteWatchSession(previous);
      const res = await fetch(`${API}/api/v1/site/watch-sessions/${sessionId}/join`, {
        method: "POST",
        credentials: "include",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ client_id: siteWatchClientIdRef.current }),
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(String(data.detail || data.message || `HTTP ${res.status}`));
      const next = data.session as SiteWatchSession;
      if (next.event_id !== eventId) {
        await leaveSiteWatchSession(next);
        throw new Error("Комната относится к другому событию");
      }
      siteWatchCurrentRef.current = next;
      siteWatchLastAppliedVersionRef.current = 0;
      setSiteWatchSession(next);
      setSiteRoomInUrl(eventId, next.id);
      await loadSiteWatchSessions(eventId);
    } catch (error) {
      if (fallbackToOwn) {
        setSiteRoomInUrl(eventId, null);
        await createSiteWatchSession(eventId);
      } else {
        setSiteWatchNotice(`Не удалось присоединиться: ${error instanceof Error ? error.message : String(error)}`);
      }
    } finally {
      setSiteWatchBusyId(null);
    }
  }

  async function joinSiteWatchSession(target: SiteWatchSession) {
    const eventId = selectedSiteEvent?.id;
    if (!eventId) return;
    await joinSiteWatchSessionById(target.id, eventId);
  }

  async function publishSiteWatchState() {
    const session = siteWatchCurrentRef.current;
    const video = siteVideoRef.current;
    if (!session || !video || siteWatchApplyingRemoteRef.current || siteWatchPublishInFlightRef.current) return;
    siteWatchPublishInFlightRef.current = true;
    try {
      const res = await fetch(`${API}/api/v1/site/watch-sessions/${session.id}/state`, {
        method: "PUT",
        credentials: "include",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          client_id: siteWatchClientIdRef.current,
          position_ms: Math.max(0, Math.round(video.currentTime * 1000)),
          is_playing: !video.paused && !video.ended,
        }),
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(String(data.detail || data.message || `HTTP ${res.status}`));
      const next = data.session as SiteWatchSession;
      siteWatchCurrentRef.current = next;
      siteWatchLastAppliedVersionRef.current = Math.max(siteWatchLastAppliedVersionRef.current, next.state_version || 0);
      setSiteWatchSession(next);
    } catch (error) {
      console.warn("watch session state publish failed", error);
    } finally {
      siteWatchPublishInFlightRef.current = false;
    }
  }

  function applySiteWatchState(session: SiteWatchSession, serverNowUtc?: string | null) {
    const video = siteVideoRef.current;
    if (!video || siteWatchPublishInFlightRef.current) return;
    const isNewVersion = session.state_version > siteWatchLastAppliedVersionRef.current;
    if (isNewVersion) siteWatchLastAppliedVersionRef.current = session.state_version;
    const serverNow = serverNowUtc ? Date.parse(serverNowUtc) : NaN;
    const stateUpdated = session.state_updated_at ? Date.parse(session.state_updated_at) : NaN;
    const elapsedMs = session.is_playing && Number.isFinite(serverNow) && Number.isFinite(stateUpdated)
      ? Math.max(0, serverNow - stateUpdated)
      : 0;
    const targetSeconds = Math.max(0, (session.position_ms + elapsedMs) / 1000);
    const drift = Math.abs(video.currentTime - targetSeconds);
    if (!isNewVersion && drift < 1.25 && session.is_playing === (!video.paused && !video.ended)) return;
    siteWatchApplyingRemoteRef.current = true;
    if (drift > (isNewVersion ? 0.75 : 1.25) && Number.isFinite(targetSeconds)) video.currentTime = targetSeconds;
    if (session.is_playing) {
      if (video.paused || video.ended) void video.play().catch(() => undefined);
    } else if (!video.paused) {
      video.pause();
    }
    window.setTimeout(() => { siteWatchApplyingRemoteRef.current = false; }, 350);
  }

  function updateSiteCoverMotion(event: React.PointerEvent<HTMLButtonElement>) {
    const rect = event.currentTarget.getBoundingClientRect();
    if (!rect.width || !rect.height) return;
    const x = Math.min(1, Math.max(0, (event.clientX - rect.left) / rect.width));
    const y = Math.min(1, Math.max(0, (event.clientY - rect.top) / rect.height));
    event.currentTarget.style.setProperty("--sv-cover-shift-x", `${(0.5 - x) * 10}px`);
    event.currentTarget.style.setProperty("--sv-cover-shift-y", `${(0.5 - y) * 10}px`);
    event.currentTarget.style.setProperty("--sv-cover-tilt-x", `${(0.5 - y) * 2.2}deg`);
    event.currentTarget.style.setProperty("--sv-cover-tilt-y", `${(x - 0.5) * 2.2}deg`);
    event.currentTarget.style.setProperty("--sv-cover-spot-x", `${x * 100}%`);
    event.currentTarget.style.setProperty("--sv-cover-spot-y", `${y * 100}%`);
  }

  function resetSiteCoverMotion(event: React.PointerEvent<HTMLButtonElement>) {
    event.currentTarget.style.setProperty("--sv-cover-shift-x", "0px");
    event.currentTarget.style.setProperty("--sv-cover-shift-y", "0px");
    event.currentTarget.style.setProperty("--sv-cover-tilt-x", "0deg");
    event.currentTarget.style.setProperty("--sv-cover-tilt-y", "0deg");
    event.currentTarget.style.setProperty("--sv-cover-spot-x", "50%");
    event.currentTarget.style.setProperty("--sv-cover-spot-y", "50%");
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

  async function submitSiteComment() {
    if (!selectedSiteEvent || !authUser || !siteCommentDraft.trim() || siteCommentSending) return;
    const cooldownRemaining = Math.max(0, Math.ceil((siteCommentCooldownUntil - Date.now()) / 1000));
    if (cooldownRemaining > 0) {
      setSiteCommentClock(Date.now());
      setSiteCommentNotice(`Следующий комментарий через ${cooldownRemaining} сек.`);
      return;
    }

    setSiteCommentSending(true);
    setSiteCommentNotice("");
    try {
      const res = await fetch(`${API}/api/v1/site/events/${selectedSiteEvent.id}/comments`, {
        method: "POST",
        credentials: "include",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          body: siteCommentDraft.trim(),
          text_color: siteCommentColor,
          is_underlined: siteCommentUnderlined,
          parent_comment_id: siteCommentReplyTo?.id || null,
        }),
      });
      const data = await res.json().catch(() => ({}));
      if (res.status === 429) {
        const detail = data.detail && typeof data.detail === "object" ? data.detail : {};
        const seconds = Math.max(1, Number(detail.retry_after_seconds || res.headers.get("Retry-After") || 60));
        setSiteCommentCooldownUntil(Date.now() + seconds * 1000);
        setSiteCommentClock(Date.now());
        setSiteCommentNotice(`Лимит: 1 комментарий в минуту. Подождите ${seconds} сек.`);
        return;
      }
      if (!res.ok) throw new Error(String(data.detail || data.message || `HTTP ${res.status}`));
      if (data.comment) setSiteComments((current) => [data.comment as SiteUserComment, ...current]);
      setSiteCommentDraft("");
      setSiteCommentUnderlined(false);
      setSiteCommentReplyTo(null);
      const seconds = Math.max(1, Number(data.cooldown_seconds || 60));
      setSiteCommentCooldownUntil(Date.now() + seconds * 1000);
      setSiteCommentClock(Date.now());
      setSiteCommentNotice("Комментарий опубликован");
    } catch (e) {
      setSiteCommentNotice(e instanceof Error ? e.message : String(e));
    } finally {
      setSiteCommentSending(false);
    }
  }

  function replyToSiteComment(comment: SiteUserComment) {
    if (!authUser) {
      setAuthModalOpen(true);
      return;
    }
    setSiteCommentReplyTo({ id: comment.id, nickname: comment.user.nickname, login: comment.user.login });
    setSiteCommentNotice("");
    window.setTimeout(() => {
      siteCommentRef.current?.scrollIntoView({ behavior: "smooth", block: "center" });
      siteCommentRef.current?.focus();
    }, 0);
  }

  async function toggleSiteCommentReaction(comment: SiteUserComment, reaction: SiteCommentReactionName) {
    if (!authUser) {
      setAuthModalOpen(true);
      return;
    }
    if (siteCommentReactionSending === comment.id) return;
    setSiteCommentReactionSending(comment.id);
    try {
      const res = await fetch(`${API}/api/v1/site/comments/${comment.id}/reaction`, {
        method: "POST",
        credentials: "include",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ reaction }),
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(String(data.detail || data.message || `HTTP ${res.status}`));
      setSiteComments((current) => current.map((item) => item.id === comment.id ? {
        ...item,
        reactions: data.reactions || {},
        viewer_reaction: data.viewer_reaction || null,
      } : item));
      setSiteCommentReactionPickerId(null);
    } catch (error) {
      setSiteCommentNotice(error instanceof Error ? error.message : String(error));
    } finally {
      setSiteCommentReactionSending(null);
    }
  }

  async function loadSiteAdminEvents() {
    const res = await fetch(`${API}/api/v1/site/admin/events`, { cache: "no-store" });
    if (!res.ok) throw new Error(await res.text());
    const data = await res.json() as { items?: SiteAdminEvent[] };
    const items = data.items || [];
    setSiteAdminEvents(items);
    setSiteAdminDrafts((current) => Object.fromEntries(items.map((event) => [event.id, current[event.id] ?? event.display_title])));
    return items;
  }

  async function openSiteAdmin() {
    setSiteAdminOpen(true);
    setSiteAdminLoading(true);
    try {
      await loadSiteAdminEvents();
      setError(null);
    } catch (e) {
      setError(String(e));
    } finally {
      setSiteAdminLoading(false);
    }
  }

  async function saveSiteDisplayTitle(eventId: string) {
    const value = (siteAdminDrafts[eventId] || "").trim();
    if (!value) return;
    setSiteAdminActionKey(`title:${eventId}`);
    try {
      const res = await fetch(`${API}/api/v1/site/admin/events/${eventId}`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ display_title: value }),
      });
      if (!res.ok) throw new Error(await res.text());
      setSiteAdminEvents((current) => current.map((event) => event.id === eventId ? { ...event, title: value, display_title: value } : event));
      setSiteEvents((current) => current.map((event) => event.id === eventId ? { ...event, title: value, display_title: value } : event));
      setSelectedSiteEvent((current) => current?.id === eventId ? { ...current, title: value, display_title: value } : current);
      setError(null);
    } catch (e) {
      setError(String(e));
    } finally {
      setSiteAdminActionKey(null);
    }
  }

  async function toggleSiteVisibility(eventId: string, hidden: boolean) {
    setSiteAdminActionKey(`visibility:${eventId}`);
    try {
      const res = await fetch(`${API}/api/v1/site/admin/events/${eventId}/visibility`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ hidden }),
      });
      if (!res.ok) throw new Error(await res.text());
      setSiteAdminEvents((current) => current.map((event) => event.id === eventId ? { ...event, hidden } : event));
      await loadSiteFeed(siteCategory, "");
      if (hidden && selectedSiteEvent?.id === eventId) closeSiteEvent();
      setError(null);
    } catch (e) {
      setError(String(e));
    } finally {
      setSiteAdminActionKey(null);
    }
  }

  async function uploadSiteAsset(eventId: string, slot: string, file: File) {
    const actionKey = `asset:${eventId}:${slot}`;
    setSiteAdminActionKey(actionKey);
    try {
      const form = new FormData();
      form.append("image", file);
      const res = await fetch(`${API}/api/v1/site/admin/events/${eventId}/assets/${slot}`, { method: "PUT", body: form });
      if (!res.ok) throw new Error(await res.text());
      await loadSiteAdminEvents();
      await loadSiteFeed(siteCategory, "");
      if (selectedSiteEvent?.id === eventId) {
        const detail = await fetch(`${API}/api/v1/site/events/${eventId}`, { cache: "no-store" });
        if (detail.ok) setSelectedSiteEvent(await detail.json() as SiteEvent);
      }
      setError(null);
    } catch (e) {
      setError(String(e));
    } finally {
      setSiteAdminActionKey(null);
    }
  }

  async function saveSiteTimecodes(eventId: string, items: Array<{ offset_ms: number; title: string }>) {
    setSiteAdminActionKey(`timecodes:${eventId}`);
    try {
      const res = await fetch(`${API}/api/v1/site/admin/events/${eventId}/timecodes`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ items }),
      });
      if (!res.ok) throw new Error(await res.text());
      const data = await res.json() as { items?: SiteTimecode[] };
      const timecodes = data.items || [];
      setSiteAdminEvents((current) => current.map((event) => event.id === eventId ? { ...event, timecodes } : event));
      setSiteEvents((current) => current.map((event) => event.id === eventId ? { ...event, timecodes } : event));
      setSelectedSiteEvent((current) => current?.id === eventId ? { ...current, timecodes } : current);
      setError(null);
      return true;
    } catch (e) {
      setError(String(e));
      return false;
    } finally {
      setSiteAdminActionKey(null);
    }
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
    const selectedPublishEvent = siteAvailableEvents.find((event) => event.id === sitePublishEventId);
    if (!sitePublishEventId || sitePublishCategories.length === 0 || !selectedPublishEvent?.assets.complete) return;
    setSiteAction(true);
    try {
      const res = await fetch(`${API}/api/v1/site/admin/events`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ event_id: sitePublishEventId, categories: sitePublishCategories }),
      });
      if (!res.ok) throw new Error(await res.text());
      setSitePublisherOpen(false);
      await loadSiteFeed(siteCategory, "");
      if (siteAdminOpen) await loadSiteAdminEvents();
      setError(null);
    } catch (e) {
      setError(String(e));
    } finally {
      setSiteAction(false);
    }
  }

  async function loadTrash() {
    try {
      const [eventsRes, jobsRes] = await Promise.all([
        fetch(`${API}/api/v1/deleted/events?page_size=100`, { cache: "no-store" }),
        fetch(`${API}/api/v1/deleted/event-purge-jobs?limit=100`, { cache: "no-store" }),
      ]);
      if (!eventsRes.ok) throw new Error(await eventsRes.text());
      if (!jobsRes.ok) throw new Error(await jobsRes.text());
      const [eventsData, jobsData] = await Promise.all([eventsRes.json(), jobsRes.json()]);
      setTrashEvents(eventsData.items || []);
      setEventPurgeJobs(jobsData.items || []);
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
    setProxyPlan(null);
    setSegmentReplacementPartId(null);
    setSegmentReplacementDirectory("");
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
      loadedParts.filter((part) => (part.kind || "source") === "source").forEach((part) => {
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
    parts.filter((part) => (part.kind || "source") === "source").forEach((part) => {
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

  async function previewProxyParts() {
    if (!selectedVideo) return;
    setProxyAction(true);
    try {
      const res = await fetch(`${API}/api/v1/video-sessions/${selectedVideo.id}/proxy-parts/plan`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ target_mib: proxyTargetMib }),
      });
      if (!res.ok) throw new Error(await res.text());
      setProxyPlan(await res.json() as ProxyPartPlanAll);
      setError(null);
    } catch (e) {
      setProxyPlan(null);
      setError(String(e));
    } finally {
      setProxyAction(false);
    }
  }

  async function buildProxyParts() {
    if (!selectedVideo || !proxyPlan) return;
    setProxyAction(true);
    try {
      const res = await fetch(`${API}/api/v1/video-sessions/${selectedVideo.id}/proxy-parts/build-all`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ target_mib: proxyTargetMib }),
      });
      if (!res.ok) throw new Error(await res.text());
      const data = await res.json() as { builder_wakeup_error?: string | null };
      await loadVideoParts(selectedVideo.id);
      setProxyPlan(null);
      setError(data.builder_wakeup_error ? `Proxy Parts поставлены в durable queue, но wakeup builder не прошёл: ${data.builder_wakeup_error}` : null);
    } catch (e) {
      setError(String(e));
    } finally {
      setProxyAction(false);
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

  async function unlinkLocalPart(part: VideoPart) {
    if (!selectedVideo) return;
    if (!window.confirm(`Удалить локальный файл Part #${part.part_no}? Запись Part и Telegram linkage останутся в Video Manager.`)) return;
    setPartAction(true);
    try {
      const res = await fetch(`${API}/api/v1/video-parts/${part.id}/unlink-local`, { method: "POST" });
      if (!res.ok) throw new Error(await res.text());
      await loadVideoParts(selectedVideo.id);
      setError(null);
    } catch (e) {
      setError(String(e));
    } finally {
      setPartAction(false);
    }
  }

  async function replacePartSegments(part: VideoPart) {
    if (!selectedVideo) return;
    const copyDirectory = segmentReplacementDirectory.trim();
    if (!copyDirectory) {
      setError("Укажите каталог, содержащий проверенные копии segments.");
      return;
    }
    if (!window.confirm(`Проверить копии segments Part #${part.part_no} в указанном каталоге и удалить оригиналы из output? Метаданные segments останутся со статусом replaced.`)) return;
    setPartAction(true);
    try {
      const res = await fetch(`${API}/api/v1/video-parts/${part.id}/replace-segments`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ copy_directory: copyDirectory }),
      });
      if (!res.ok) throw new Error(await res.text());
      const result = await res.json() as { cleanup_pending?: number; replaced_segments?: number };
      await openVideoSession(selectedVideo);
      setSegmentReplacementPartId(null);
      setSegmentReplacementDirectory("");
      setError(result.cleanup_pending ? `Segments помечены replaced, но ${result.cleanup_pending} quarantine-файлов требуют повторной очистки.` : null);
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
    if (!window.confirm("Поставить в очередь безвозвратное удаление всех sessions этого Event, которые сейчас находятся в корзине? Текущие sessions в Events и сам уникальный Event не затрагиваются.")) return;
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

  function purgeJobForEvent(eventId: string) {
    return eventPurgeJobs.find((job) => job.event_id === eventId && ["queued", "running", "failed"].includes(job.status)) || null;
  }

  async function retryEventPurge(job: EventPurgeJob) {
    setActionId(job.event_id);
    try {
      const res = await fetch(`${API}/api/v1/deleted/event-purge-jobs/${job.id}/retry`, { method: "POST" });
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

  const activeEventPurgeJobs = eventPurgeJobs.some((job) => job.status === "queued" || job.status === "running");

  useEffect(() => {
    if (view !== "trash" || !activeEventPurgeJobs) return;
    const timer = window.setInterval(() => {
      loadTrash();
    }, 1500);
    return () => window.clearInterval(timer);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [view, activeEventPurgeJobs]);

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

  const siteCommentById = new Map(siteComments.map((comment) => [comment.id, comment]));
  const siteCommentChildren = new Map<number, SiteUserComment[]>();
  for (const comment of siteComments) {
    if (!comment.parent_comment_id || !siteCommentById.has(comment.parent_comment_id)) continue;
    const children = siteCommentChildren.get(comment.parent_comment_id) || [];
    children.push(comment);
    siteCommentChildren.set(comment.parent_comment_id, children);
  }
  for (const children of siteCommentChildren.values()) {
    children.sort((a, b) => new Date(a.created_at).getTime() - new Date(b.created_at).getTime() || a.id - b.id);
  }
  const siteRootComments = siteComments.filter((comment) => !comment.parent_comment_id || !siteCommentById.has(comment.parent_comment_id));

  const sitePreviewOverlayItem = sitePreviewOverlay
    ? sitePreviewOverlay.items[sitePreviewOverlay.index] || null
    : null;

  function renderSiteComment(comment: SiteUserComment, depth = 0): React.ReactNode {
    const replies = siteCommentChildren.get(comment.id) || [];
    return (
      <div className={`streamvault-comment-thread${depth ? " is-reply" : ""}`} key={comment.id}>
        <article className="streamvault-comment-item">
          <div className="streamvault-comment-avatar">
            {comment.user.avatar_url ? <img src={apiUrl(comment.user.avatar_url)} alt="" loading="lazy" /> : comment.user.nickname.slice(0, 1).toUpperCase()}
          </div>
          <div className="streamvault-comment-item-body">
            <div className="streamvault-comment-meta">
              <strong>{comment.user.nickname}</strong>
              <small>@{comment.user.login}</small>
              <time dateTime={comment.created_at}>{new Date(comment.created_at).toLocaleString("ru-RU")}</time>
            </div>
            <p className={comment.is_underlined ? "is-underlined" : ""} style={{ color: comment.text_color || "#ff9b37" }}>{comment.body}</p>
            <div className="streamvault-comment-footer">
              <div className="streamvault-comment-reactions" aria-label="Реакции на комментарий">
                {SITE_COMMENT_REACTIONS.filter((reaction) => Number(comment.reactions?.[reaction.key] || 0) > 0).map((reaction) => {
                  const count = Number(comment.reactions?.[reaction.key] || 0);
                  const selectedReaction = comment.viewer_reaction === reaction.key;
                  return (
                    <button
                      key={reaction.key}
                      type="button"
                      className={selectedReaction ? "is-selected" : ""}
                      aria-pressed={selectedReaction}
                      aria-label={`${reaction.label}: ${count}`}
                      title={reaction.label}
                      disabled={siteCommentReactionSending === comment.id}
                      onClick={() => void toggleSiteCommentReaction(comment, reaction.key)}
                    >
                      <span className="streamvault-comment-reaction-emoji" aria-hidden="true">
                        <span>{reaction.emoji}</span>
                        <img src={reaction.image} alt="" loading="lazy" referrerPolicy="no-referrer" />
                      </span>
                      <span className="streamvault-comment-reaction-count">{count}</span>
                    </button>
                  );
                })}
                <div className="streamvault-comment-reaction-picker-wrap">
                  <button
                    type="button"
                    className={`streamvault-comment-reaction-add${siteCommentReactionPickerId === comment.id ? " is-open" : ""}`}
                    aria-label="Выбрать реакцию"
                    aria-expanded={siteCommentReactionPickerId === comment.id}
                    onClick={() => {
                      if (!authUser) { setAuthModalOpen(true); return; }
                      setSiteCommentReactionPickerId((current) => current === comment.id ? null : comment.id);
                    }}
                  >☺+</button>
                  {siteCommentReactionPickerId === comment.id ? (
                    <div className="streamvault-comment-reaction-picker" role="menu" aria-label="Выбор реакции">
                      {SITE_COMMENT_REACTIONS.map((reaction) => (
                        <button
                          type="button"
                          key={reaction.key}
                          className={comment.viewer_reaction === reaction.key ? "is-selected" : ""}
                          title={reaction.label}
                          aria-label={reaction.label}
                          disabled={siteCommentReactionSending === comment.id}
                          onClick={() => void toggleSiteCommentReaction(comment, reaction.key)}
                        >
                          <span className="streamvault-comment-reaction-emoji" aria-hidden="true">
                            <span>{reaction.emoji}</span>
                            <img src={reaction.image} alt="" loading="lazy" referrerPolicy="no-referrer" />
                          </span>
                        </button>
                      ))}
                    </div>
                  ) : null}
                </div>
              </div>
              <button className="streamvault-comment-reply" type="button" onClick={() => replyToSiteComment(comment)}>Ответить</button>
            </div>
          </div>
        </article>
        {replies.length ? (
          <div className="streamvault-comment-replies">
            {replies.map((reply) => renderSiteComment(reply, depth + 1))}
          </div>
        ) : null}
      </div>
    );
  }

  if (selectedSiteEvent) {
    const eventDate = selectedSiteEvent.source_started_at_utc
      ? new Date(selectedSiteEvent.source_started_at_utc).toLocaleString("ru-RU")
      : "Дата неизвестна";
    return (
      <div className="streamvault-app">
        <StreamVaultHeader
          query={siteQuery}
          searchResults={siteSearchResults}
          searchLoading={siteSearchLoading}
          authUser={authUser}
          onQueryChange={setSiteQuery}
          onSubmit={() => {}}
          onSearchResult={openSiteEventNewTab}
          onBrand={closeSiteEvent}
          onAdmin={() => { closeSiteEvent(); setView("events"); }}
          onSiteAdmin={() => void openSiteAdmin()}
          onLogin={() => setAuthModalOpen(true)}
          onLogout={() => void logoutUser()}
        />
        {authModalOpen ? <AuthModal onClose={() => setAuthModalOpen(false)} onAuthenticated={setAuthUser} /> : null}
        <main className="streamvault-watch-shell">
          <button className="streamvault-back" type="button" onClick={closeSiteEvent}>← К записям</button>
          <div className="streamvault-watch-layout">
            <div className="streamvault-watch-primary">
              <div className="streamvault-player">
                {selectedSiteEvent.playable && selectedSiteEvent.playback_url ? (
                  <TelegramVideoPlayer playlistUrl={selectedSiteEvent.playback_url} externalRef={siteVideoRef} autoPlay />
                ) : (
                  <div className="streamvault-video-placeholder">Видео пока недоступно: нет связанного Telegram playback range.</div>
                )}
              </div>
              {selectedSiteEvent.playable ? (
                <div className="streamvault-room-presence">С вами в комнате: <strong>{Math.max(0, (siteWatchSession?.participant_count || 1) - 1)}</strong> чел.</div>
              ) : null}
              <SiteEventTimecodes items={selectedSiteEvent.timecodes || []} videoRef={siteVideoRef} />
              {selectedSiteEvent.playable ? (
                <SiteWatchSessionsPanel
                  sessions={siteWatchSessions}
                  current={siteWatchSession}
                  busyId={siteWatchBusyId}
                  notice={siteWatchNotice}
                  onJoin={(session) => void joinSiteWatchSession(session)}
                />
              ) : null}
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
                  videoRef={siteVideoRef}
                  onMention={mentionSiteComment}
                />
              </section>

              <section className="streamvault-comments" aria-labelledby="streamvaultCommentsTitle">
                <div className="streamvault-comments-head">
                  <h2 id="streamvaultCommentsTitle">Комментарии <span>{siteComments.length}</span></h2>
                </div>
                {authUser ? (
                  <div className="streamvault-comment-composer">
                    <div className="streamvault-comment-avatar">
                      {authUser.avatar_url ? <img src={apiUrl(authUser.avatar_url)} alt="" /> : authUser.nickname.slice(0, 1).toUpperCase()}
                    </div>
                    <div>
                      {siteCommentReplyTo ? (
                        <div className="streamvault-comment-replying">
                          <span>Ответ для <strong>{siteCommentReplyTo.nickname}</strong> <small>@{siteCommentReplyTo.login}</small></span>
                          <button type="button" onClick={() => setSiteCommentReplyTo(null)} aria-label="Отменить ответ">×</button>
                        </div>
                      ) : null}
                      <textarea
                        ref={siteCommentRef}
                        value={siteCommentDraft}
                        onChange={(event) => { setSiteCommentDraft(event.target.value); setSiteCommentNotice(""); }}
                        rows={3}
                        maxLength={4000}
                        placeholder={siteCommentReplyTo ? `Ответить ${siteCommentReplyTo.nickname}…` : "Написать комментарий…"}
                        className={siteCommentUnderlined ? "is-underlined" : ""}
                        style={{ color: siteCommentColor }}
                      />
                      <div className="streamvault-comment-actions">
                        <label className="streamvault-comment-color" title="Цвет текста комментария">
                          <input
                            type="color"
                            value={siteCommentColor}
                            onChange={(event) => {
                              const value = event.target.value.toLowerCase();
                              setSiteCommentColor(value);
                              try { window.localStorage.setItem(SITE_COMMENT_COLOR_KEY, value); } catch { /* ignore */ }
                            }}
                            aria-label="Цвет текста комментария"
                          />
                          <span>Цвет</span>
                        </label>
                        <button
                          type="button"
                          className={`streamvault-comment-underline${siteCommentUnderlined ? " is-active" : ""}`}
                          aria-pressed={siteCommentUnderlined}
                          title="Подчеркнуть текст"
                          onClick={() => setSiteCommentUnderlined((value) => !value)}
                        ><span>U</span></button>
                        <span>{Math.max(0, Math.ceil((siteCommentCooldownUntil - siteCommentClock) / 1000)) > 0
                          ? `Следующий комментарий через ${Math.max(0, Math.ceil((siteCommentCooldownUntil - siteCommentClock) / 1000))} сек.`
                          : siteCommentNotice}</span>
                        <button
                          type="button"
                          disabled={!siteCommentDraft.trim() || siteCommentSending || Math.max(0, Math.ceil((siteCommentCooldownUntil - siteCommentClock) / 1000)) > 0}
                          onClick={() => void submitSiteComment()}
                        >{siteCommentSending ? "Отправляем…" : siteCommentReplyTo ? "Ответить" : "Отправить"}</button>
                      </div>
                    </div>
                  </div>
                ) : (
                  <div className="streamvault-comment-login">
                    <span>Войдите, чтобы оставить комментарий.</span>
                    <button type="button" onClick={() => setAuthModalOpen(true)}>Войти</button>
                  </div>
                )}
                <div className="streamvault-comment-list">
                  {siteCommentsLoading ? <div className="streamvault-comment-empty">Загрузка комментариев…</div> : null}
                  {!siteCommentsLoading && siteComments.length === 0 ? <div className="streamvault-comment-empty">Комментариев пока нет.</div> : null}
                  {siteRootComments.map((comment) => renderSiteComment(comment))}
                </div>
              </section>
            </div>
            <SiteReplayChat
              eventId={selectedSiteEvent.id}
              hasChat={selectedSiteEvent.has_chat}
              messageCount={selectedSiteEvent.chat_message_count || 0}
              videoRef={siteVideoRef}
              watchSession={siteWatchSession}
              watchClientId={siteWatchClientIdRef.current}
              authUser={authUser}
              onLogin={() => setAuthModalOpen(true)}
              onMention={mentionSiteComment}
            />
          </div>
        </main>
        {siteAdminOpen ? (
          <SiteAdminPanel
            events={siteAdminEvents}
            drafts={siteAdminDrafts}
            loading={siteAdminLoading}
            actionKey={siteAdminActionKey}
            onDraftChange={(eventId, value) => setSiteAdminDrafts((current) => ({ ...current, [eventId]: value }))}
            onSaveTitle={(eventId) => void saveSiteDisplayTitle(eventId)}
            onUpload={(eventId, slot, file) => void uploadSiteAsset(eventId, slot, file)}
            onSaveTimecodes={saveSiteTimecodes}
            onToggleVisibility={(eventId, hidden) => void toggleSiteVisibility(eventId, hidden)}
            onAddEvent={() => { setSiteAdminOpen(false); closeSiteEvent(); void openSitePublisher(); }}
            onClose={() => setSiteAdminOpen(false)}
          />
        ) : null}
      </div>
    );
  }

  if (view === "site") {
    const latest = siteEvents[0] || null;
    return (
      <div className="streamvault-app">
        <StreamVaultHeader
          query={siteQuery}
          searchResults={siteSearchResults}
          searchLoading={siteSearchLoading}
          authUser={authUser}
          onQueryChange={setSiteQuery}
          onSubmit={() => {}}
          onSearchResult={openSiteEventNewTab}
          onBrand={() => { setSiteCategory("all"); setSiteQuery(""); void loadSiteFeed("all", ""); }}
          onAdmin={() => setView("events")}
          onSiteAdmin={() => void openSiteAdmin()}
          onLogin={() => setAuthModalOpen(true)}
          onLogout={() => void logoutUser()}
        />
        {authModalOpen ? <AuthModal onClose={() => setAuthModalOpen(false)} onAuthenticated={setAuthUser} /> : null}
        {sitePreviewOverlay && sitePreviewOverlayItem ? (
          <div
            className="streamvault-frame-overlay"
            role="dialog"
            aria-modal="true"
            aria-label={sitePreviewOverlayItem.label}
            onMouseDown={(event) => { if (event.currentTarget === event.target) setSitePreviewOverlay(null); }}
          >
            <button type="button" className="streamvault-frame-overlay-close" onClick={() => setSitePreviewOverlay(null)} aria-label="Закрыть просмотр кадра">×</button>
            {sitePreviewOverlay.items.length > 1 ? (
              <button
                type="button"
                className="streamvault-frame-overlay-nav is-prev"
                onClick={() => setSitePreviewOverlay((current) => current ? { ...current, index: (current.index - 1 + current.items.length) % current.items.length } : current)}
                aria-label="Предыдущий кадр"
              >←</button>
            ) : null}
            <img src={apiUrl(sitePreviewOverlayItem.src)} alt={sitePreviewOverlayItem.label} />
            {sitePreviewOverlay.items.length > 1 ? (
              <button
                type="button"
                className="streamvault-frame-overlay-nav is-next"
                onClick={() => setSitePreviewOverlay((current) => current ? { ...current, index: (current.index + 1) % current.items.length } : current)}
                aria-label="Следующий кадр"
              >→</button>
            ) : null}
            <div className="streamvault-frame-overlay-counter">{sitePreviewOverlay.index + 1} / {sitePreviewOverlay.items.length}</div>
          </div>
        ) : null}

        <nav className="streamvault-mobile-categories" aria-label="Категории">
          <button className={siteCategory === "all" ? "active" : ""} onClick={() => { setSiteCategory("all"); void loadSiteFeed("all", ""); }}>
            <SiteCategoryIcon slug="all" />
            <span className="streamvault-category-label">Все</span>
          </button>
          {siteCategories.map((category) => (
            <button key={category.slug} className={siteCategory === category.slug ? "active" : ""} onClick={() => { setSiteCategory(category.slug); void loadSiteFeed(category.slug, ""); }}>
              <SiteCategoryIcon slug={category.slug} />
              <span className="streamvault-category-label">{category.label}</span>
            </button>
          ))}
        </nav>

        <div className="streamvault-home-shell">
          <aside className="streamvault-sidebar">
            <nav aria-label="Категории видео">
              <button className={siteCategory === "all" ? "active" : ""} onClick={() => { setSiteCategory("all"); void loadSiteFeed("all", ""); }}>
                <SiteCategoryIcon slug="all" />
                <span className="streamvault-category-label">Все видео</span>
              </button>
              {siteCategories.map((category) => (
                <button key={category.slug} className={siteCategory === category.slug ? "active" : ""} onClick={() => { setSiteCategory(category.slug); void loadSiteFeed(category.slug, ""); }}>
                  <SiteCategoryIcon slug={category.slug} />
                  <span className="streamvault-category-label">{category.label}</span>
                </button>
              ))}
            </nav>
          </aside>

          <main className="streamvault-content">
            {error ? <div className="streamvault-error">{error}</div> : null}
            {latest ? (
              <section className="streamvault-hero" aria-labelledby="streamvaultLatestTitle">
                <h1>Последний стрим</h1>
                <div className="streamvault-latest-layout">
                  <button
                    type="button"
                    className="streamvault-latest-cover streamvault-cover-link"
                    onClick={() => openSiteEventNewTab(latest)}
                    onPointerMove={updateSiteCoverMotion}
                    onPointerLeave={resetSiteCoverMotion}
                    aria-label={`Открыть ${latest.title} в новой вкладке`}
                  >
                    <SiteArtwork event={latest} label="COVER" src={latest.assets?.cover?.url} />
                  </button>
                  <div className="streamvault-preview-stage">
                    <SiteHeroPreviewVideo event={latest} onActivate={() => openSiteEvent(latest)} />
                    <div className="streamvault-preview-rail" aria-label="Четыре preview-кадра">
                      {[0, 1, 2, 3].map((frame) => {
                        const src = latest.assets?.frames?.[frame]?.url || null;
                        return (
                          <button
                            key={frame}
                            type="button"
                            disabled={!src}
                            onClick={() => src && openSitePreviewFrame(latest, frame)}
                            aria-label={`Открыть кадр ${frame + 1}`}
                          >
                            <SiteArtwork event={latest} wide label={`#${frame + 1}`} src={src} />
                          </button>
                        );
                      })}
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
                  <h2 id="streamvaultRecordingsTitle">{siteCategory === "all" ? "Все видео" : siteCategories.find((category) => category.slug === siteCategory)?.label || "Все видео"}</h2>
                </div>
              </div>
              {siteEvents.length === 0 ? (
                <div className="streamvault-empty">Пока нет опубликованных записей в этом разделе.</div>
              ) : (
                <div className="streamvault-recording-grid">
                  {siteEvents.map((event) => (
                    <article className="streamvault-recording-card" key={event.id}>
                      <button
                        type="button"
                        className="streamvault-recording-cover-link streamvault-cover-link"
                        onClick={() => openSiteEventNewTab(event)}
                        onPointerMove={updateSiteCoverMotion}
                        onPointerLeave={resetSiteCoverMotion}
                        aria-label={`Открыть ${event.title} в новой вкладке`}
                      >
                        <div className="streamvault-recording-cover"><SiteArtwork event={event} label="COVER" src={event.assets?.cover?.url} /></div>
                      </button>
                      <div className="streamvault-recording-body">
                        <h3>{event.title}</h3>
                        <p>{event.source_started_at_utc ? new Date(event.source_started_at_utc).toLocaleDateString("ru-RU") : "Дата неизвестна"} · {(event.chat_message_count || 0).toLocaleString("ru-RU")} сообщений</p>
                        <div className="streamvault-badges">{event.categories.slice(0, 3).map((category) => <span key={category.slug}>{category.label}</span>)}</div>
                      </div>
                    </article>
                  ))}
                </div>
              )}
            </section>
          </main>
        </div>

        {siteAdminOpen ? (
          <SiteAdminPanel
            events={siteAdminEvents}
            drafts={siteAdminDrafts}
            loading={siteAdminLoading}
            actionKey={siteAdminActionKey}
            onDraftChange={(eventId, value) => setSiteAdminDrafts((current) => ({ ...current, [eventId]: value }))}
            onSaveTitle={(eventId) => void saveSiteDisplayTitle(eventId)}
            onUpload={(eventId, slot, file) => void uploadSiteAsset(eventId, slot, file)}
            onSaveTimecodes={saveSiteTimecodes}
            onToggleVisibility={(eventId, hidden) => void toggleSiteVisibility(eventId, hidden)}
            onAddEvent={() => { setSiteAdminOpen(false); void openSitePublisher(); }}
            onClose={() => setSiteAdminOpen(false)}
          />
        ) : null}

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
                      {siteAvailableEvents.map((event) => <option key={event.id} value={event.id}>{event.title} · {event.channel_display_name || event.channel_login || event.media_type} · TG {event.linked_parts}/{event.ready_parts} · {event.assets.complete ? "арт 5/5" : "нужны Cover + 4 кадра"}</option>)}
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
                  {!siteAvailableEvents.find((event) => event.id === sitePublishEventId)?.assets.complete ? <div className="site-admin-publish-warning">Перед публикацией загрузите Cover и все 4 preview-кадра в админке.</div> : null}
                  <div className="actions site-modal-actions"><button disabled={siteAction || !sitePublishEventId || sitePublishCategories.length === 0 || !siteAvailableEvents.find((event) => event.id === sitePublishEventId)?.assets.complete} onClick={publishSiteEvent}>{siteAction ? "Добавляю…" : "Добавить на сайт"}</button></div>
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
    const eventDate = selectedVideo.event?.source_started_at_utc || selectedVideo.recording_started_at_utc || selectedVideo.created_at;
    return (
      <main>
        <div className="video-manager-event-date">
          <span>Дата проведения события</span>
          <strong>{new Date(eventDate).toLocaleString("ru-RU")}</strong>
        </div>
        <button className="video-manager-back" onClick={() => setSelectedVideo(null)}>Назад</button>
        <div className="events-title-mode" role="group" aria-label="Режим названий Video Manager">
          <span>Название:</span>
          <button type="button" className={eventTitleMode === "display" ? "active" : ""} onClick={() => changeEventTitleMode("display")}>Отображаемое</button>
          <button type="button" className={eventTitleMode === "source" ? "active" : ""} onClick={() => changeEventTitleMode("source")}>Исходное</button>
        </div>
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
        <section className="video-block proxy-parts-block">
          <div className="section-label">PROXY PARTS · HEVC 1080p60</div>
          <p className="muted small">Создаются из готовых локальных Parts. Профиль фиксирован: HEVC/H.265 · 1920×1080 · 60 fps · target/max 4/4 Mbps · AAC 160 kbps. Исходные Parts не изменяются.</p>
          <div className="part-controls">
            <label className="storage-field"><span>Max Proxy Part MiB · сохраняется</span><input type="number" min={1} value={proxyTargetMib} onChange={(e) => { setProxyTargetMib(Math.max(1, Number(e.target.value) || 1)); setProxyPlan(null); }} /></label>
          </div>
          <p className="muted small">Стандарт: 1990 MiB. Builder объединяет целые исходные Parts, пока итоговый proxy остаётся в лимите.</p>
          <div className="actions part-actions">
            <button disabled={proxyAction} onClick={() => void previewProxyParts()}>{proxyAction ? "Работаю…" : "Preview Proxy"}</button>
            <button disabled={proxyAction || !proxyPlan} onClick={() => void buildProxyParts()}>{proxyAction ? "Работаю…" : "Build Proxy Parts"}</button>
          </div>
          {proxyPlan && (
            <div className="part-plan-all">
              <div className="part-plan">
                <div><span>Proxy Parts</span><strong>{proxyPlan.part_count}</strong></div>
                <div><span>Source Parts</span><strong>{proxyPlan.source_part_count}</strong></div>
                <div><span>Total duration</span><strong>{fmtMs(proxyPlan.duration_ms)}</strong></div>
                <div><span>Estimated</span><strong>{fmtBytes(proxyPlan.expected_bytes)}</strong></div>
              </div>
              <div className="part-plan-all-list">
                {proxyPlan.items.map((plan, index) => (
                  <div className="part-plan-all-row" key={`${plan.first_source_part_no}-${plan.last_source_part_no}`}>
                    <strong>Proxy #{index + 1} · Parts {plan.first_source_part_no}–{plan.last_source_part_no}</strong>
                    <span>{plan.source_part_count} source · {fmtMs(plan.duration_ms)} · ~{fmtBytes(plan.expected_bytes)} · max {plan.target_mib} MiB</span>
                  </div>
                ))}
              </div>
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
          {videoParts.filter((part) => (part.kind || "source") === "source").length === 0 ? <div className="empty-child">Parts ещё не создавались</div> : (
            <div className="part-list">
              {videoParts.filter((part) => (part.kind || "source") === "source").map((part) => {
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
                    <div className={`part-local-status part-local-status-${part.local_file_state || "present"}`}>
                      Local Part: <strong>{part.local_file_state === "unlinked" ? "deleted" : part.local_file_state || "present"}</strong>
                      {part.local_unlinked_at_utc && <> · {new Date(part.local_unlinked_at_utc).toLocaleString()}</>}
                    </div>
                    {part.local_unlink_error && <div className="inline-error small">{part.local_unlink_error}</div>}
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
                      {part.status === "ready" && telegram?.status === "linked" && part.local_file_state !== "unlinked" && (
                        <button className="danger subtle" disabled={partAction} onClick={() => void unlinkLocalPart(part)}>Unlink Part</button>
                      )}
                      {part.status === "ready" && telegram?.status === "linked" && (
                        <button disabled={partAction} onClick={() => { setSegmentReplacementPartId((current) => current === part.id ? null : part.id); setSegmentReplacementDirectory(""); }}>Replace segments</button>
                      )}
                      {(part.status === "failed" || part.status === "cancelled") && <button className="danger subtle" disabled={partAction} onClick={() => partActionRequest(part, "delete")}>Delete reservation</button>}
                    </div>
                    {segmentReplacementPartId === part.id && (
                      <div className="segment-replacement-form">
                        <label>
                          <span>Каталог с копиями segments</span>
                          <input
                            type="text"
                            value={segmentReplacementDirectory}
                            onChange={(event) => setSegmentReplacementDirectory(event.target.value)}
                            placeholder="E:\\StreamHub\\backup\\segments"
                            autoFocus
                          />
                        </label>
                        <p className="muted small">Каталог должен быть внутри подключённого VIDEO_OUTPUT_ROOT. Перед удалением проверяются имя, размер и SHA-256 каждого segment.</p>
                        <div className="actions">
                          <button disabled={partAction || !segmentReplacementDirectory.trim()} onClick={() => void replacePartSegments(part)}>Verify & replace</button>
                          <button disabled={partAction} onClick={() => { setSegmentReplacementPartId(null); setSegmentReplacementDirectory(""); }}>Отмена</button>
                        </div>
                      </div>
                    )}
                  </div>
                );
              })}
            </div>
          )}
        </section>
        <section className="video-block">
          <div className="section-label">PROXY PARTS / BUILD QUEUE</div>
          {videoParts.filter((part) => part.kind === "proxy").length === 0 ? <div className="empty-child">Proxy Parts ещё не создавались</div> : (
            <div className="part-list">
              {videoParts.filter((part) => part.kind === "proxy").map((part) => {
                const job = part.job;
                const telegram = telegramBindings[part.id];
                const progressTotal = job?.total_bytes || part.expected_bytes || 1;
                const progressBytes = job?.progress_bytes || (part.status === "ready" ? (part.final_bytes || part.expected_bytes) : 0);
                const progressPercent = Math.max(0, Math.min(100, Math.round((progressBytes / progressTotal) * 100)));
                const sourceNos = part.profile?.source_part_nos || [];
                return (
                  <div className="part-row proxy-part-row" key={part.id}>
                    <div className="row"><strong>Proxy Part #{part.part_no}{sourceNos.length ? ` · source ${sourceNos[0]}–${sourceNos[sourceNos.length - 1]}` : ""}</strong><span className={`part-status part-status-${part.status}`}>{part.status}</span></div>
                    <div className="muted small">1080p60 · HEVC target/max 4/4 Mbps · AAC 160 kbps · {fmtMs(part.duration_ms)} · {fmtBytes(part.final_bytes ?? part.expected_bytes)} · attempts {job?.attempts || 0}</div>
                    {["queued", "waiting_capture_idle", "building", "verifying", "suspended_for_capture"].includes(part.status) && (
                      <>
                        <div className="progress-track part-progress"><div className="progress-fill" style={{ width: `${progressPercent}%` }} /></div>
                        <div className="muted small">{job?.phase || part.status} · {fmtBytes(progressBytes)} / {fmtBytes(progressTotal)}{part.status === "waiting_capture_idle" || part.status === "suspended_for_capture" ? " · ожидает завершения Video capture" : ""}</div>
                      </>
                    )}
                    {part.sha256 && <div className="muted small hash-line">sha256 <code>{part.sha256}</code></div>}
                    <div className="muted small path-cell"><code>{part.relative_path}</code></div>
                    <div className={`part-local-status part-local-status-${part.local_file_state || "present"}`}>
                      Local Proxy: <strong>{part.local_file_state === "unlinked" ? "deleted" : part.local_file_state || "present"}</strong>
                      {part.local_unlinked_at_utc && <> · {new Date(part.local_unlinked_at_utc).toLocaleString()}</>}
                    </div>
                    {part.local_unlink_error && <div className="inline-error small">{part.local_unlink_error}</div>}
                    {part.status === "ready" && (
                      <div className={`telegram-part-status telegram-part-status-${telegram?.status || "missing"}`}>
                        Telegram: <strong>{telegram?.status || "missing"}</strong>
                        {telegram?.message_id != null && <> · message <code>{telegram.message_id}</code></>}
                        {telegram?.matched_by && <> · {telegram.matched_by}</>}
                      </div>
                    )}
                    {(part.last_error || job?.last_error) && <div className="inline-error small">{part.last_error || job?.last_error}</div>}
                    <div className="actions part-row-actions">
                      {(part.status === "failed" || part.status === "cancelled") && <button disabled={partAction || proxyAction} onClick={() => partActionRequest(part, "retry")}>Retry</button>}
                      {["queued", "waiting_capture_idle", "building", "verifying", "suspended_for_capture"].includes(part.status) && <button disabled={partAction || proxyAction} onClick={() => partActionRequest(part, "cancel")}>Cancel</button>}
                      <button onClick={() => copyPartPath(part)}>Copy path</button>
                      {part.status === "ready" && telegram?.status === "linked" && part.local_file_state !== "unlinked" && (
                        <button className="danger subtle" disabled={partAction || proxyAction} onClick={() => void unlinkLocalPart(part)}>Unlink Proxy</button>
                      )}
                      {(part.status === "failed" || part.status === "cancelled") && <button className="danger subtle" disabled={partAction || proxyAction} onClick={() => partActionRequest(part, "delete")}>Delete reservation</button>}
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
                <td>{segment.replaced_at_utc ? `replaced ${new Date(segment.replaced_at_utc).toLocaleString()}` : segment.archived_at_utc ? new Date(segment.archived_at_utc).toLocaleString() : "—"}</td>
                <td className="path-cell">
                  {segment.archive_last_error ? <span className="inline-error">{segment.archive_last_error}</span> : segment.storage_state === "replaced" ? (
                    <><code>{segment.replacement_path || segment.replacement_directory || "replacement verified"}</code><div className="muted small">original: {segment.relative_path}</div></>
                  ) : <code>{segment.relative_path}</code>}
                </td>
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
          {view !== "video-manager" && (
            <p className="muted">{view === "trash" ? "Корзина sessions · сгруппировано по уникальному Event" : view === "site" ? "Публичный каталог · категории · опубликованные Events" : view === "storage" ? "Video output · spool batches · migration" : "Twitch Events · Chat + Video sessions"}</p>
          )}
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
          <div className="events-title-mode" role="group" aria-label="Режим названий Events">
            <span>Название:</span>
            <button type="button" className={eventTitleMode === "display" ? "active" : ""} onClick={() => changeEventTitleMode("display")}>Отображаемое</button>
            <button type="button" className={eventTitleMode === "source" ? "active" : ""} onClick={() => changeEventTitleMode("source")}>Исходное</button>
          </div>
          {events.length === 0 && <div className="empty">Events пока нет</div>}
          <section className="events-list">
            {events.map((event) => {
              const expanded = expandedIds.includes(event.id);
              const activeChat = event.chat_sessions.find(isCaptureBusy);
              const activeVideo = event.video_sessions.find(isVideoBusy);
              const editingTitle = editingEventTitleId === event.id;
              return (
                <article className="event-card" key={event.id}>
                  <div className="event-summary-row">
                    <button
                      type="button"
                      className="event-edit-title-button"
                      aria-label={`Изменить отображаемое название: ${visibleEventTitle(event, "display")}`}
                      title="Изменить отображаемое название"
                      onClick={() => editingTitle ? cancelEventTitleEdit() : beginEventTitleEdit(event)}
                    >
                      ✎
                    </button>
                    <button className="event-summary" onClick={() => toggleEvent(event.id)} aria-expanded={expanded}>
                      <div className="event-main">
                        <div className="row"><strong>{event.channel_display_name || event.channel_login || "Twitch"}</strong><span className="event-type">{event.media_type.toUpperCase()}</span></div>
                        <div className="event-title">{visibleEventTitle(event, eventTitleMode)}</div>
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
                  </div>

                  {editingTitle && (
                    <form className="event-title-editor" onSubmit={(e) => { e.preventDefault(); void saveEventDisplayTitle(event); }}>
                      <div className="event-title-editor-copy">
                        <label htmlFor={`event-display-title-${event.id}`}>Отображаемое название</label>
                        <span>Исходное: {event.title?.trim() || fallbackEventTitle(event)}</span>
                      </div>
                      <input
                        id={`event-display-title-${event.id}`}
                        autoFocus
                        maxLength={1024}
                        value={eventTitleDraft}
                        onChange={(e) => setEventTitleDraft(e.target.value)}
                        onKeyDown={(e) => { if (e.key === "Escape") cancelEventTitleEdit(); }}
                      />
                      <div className="event-title-editor-actions">
                        <button type="submit" disabled={actionId === `event-title:${event.id}` || !eventTitleDraft.trim()}>Сохранить</button>
                        <button type="button" className="subtle" disabled={actionId === `event-title:${event.id}`} onClick={cancelEventTitleEdit}>Отмена</button>
                      </div>
                    </form>
                  )}

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
          <div className="events-title-mode" role="group" aria-label="Режим названий Video Manager">
            <span>Название:</span>
            <button type="button" className={eventTitleMode === "display" ? "active" : ""} onClick={() => changeEventTitleMode("display")}>Отображаемое</button>
            <button type="button" className={eventTitleMode === "source" ? "active" : ""} onClick={() => changeEventTitleMode("source")}>Исходное</button>
          </div>
          {videoSessions.length === 0 ? <div className="empty-child">Video sessions пока нет</div> : (
            <div className="video-manager-list">
              {videoSessions.map((session) => (
                <div
                  className="video-manager-session is-clickable"
                  key={session.id}
                  role="button"
                  tabIndex={0}
                  onClick={() => void openVideoSession(session)}
                  onKeyDown={(event) => {
                    if (event.key === "Enter" || event.key === " ") {
                      event.preventDefault();
                      void openVideoSession(session);
                    }
                  }}
                >
                  <div className="video-manager-session-main">
                    <div className="video-manager-index-event-date">
                      <span>Дата проведения события</span>
                      <strong>{new Date(session.event?.source_started_at_utc || session.recording_started_at_utc || session.created_at).toLocaleString("ru-RU")}</strong>
                    </div>
                    <div className="row"><strong>{session.event?.channel_display_name || session.event?.channel_login || session.metadata?.channel_login || "Twitch"}</strong><span>{session.event?.media_type?.toUpperCase() || session.metadata?.media_type?.toUpperCase() || "VIDEO"}</span></div>
                    <div className="event-title">{visibleVideoEventTitle(session, eventTitleMode)}</div>
                    <div className="session-status-line"><strong>{session.status}</strong><span>{session.completeness_status}</span><span>{session.segment_count || 0} seg</span><span>{fmtBytes(session.bytes || 0)}</span><span>{fmtMs(session.duration_recorded_ms)}</span></div>
                    <div className="muted small"><code>{session.id}</code></div>
                  </div>
                  <div className="actions"><button onClick={(event) => { event.stopPropagation(); void openVideoSession(session); }}>Открыть</button></div>
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
          {eventPurgeJobs.some((job) => ["queued", "running", "failed"].includes(job.status)) && (
            <section className="trash-purge-queue">
              <div className="section-label">ОЧЕРЕДЬ УДАЛЕНИЯ</div>
              {eventPurgeJobs.filter((job) => ["queued", "running", "failed"].includes(job.status)).map((job) => (
                <div className="trash-purge-job" key={job.id}>
                  <div>
                    <strong>Job #{job.id}</strong>
                    <span className={`trash-purge-status ${job.status}`}>{job.status}</span>
                    <span className="muted small">Event {job.event_id} · Chat {job.total_chat_sessions} · Video {job.total_video_sessions}</span>
                  </div>
                  {job.last_error ? <div className="error small">{job.last_error}</div> : null}
                  {job.status === "failed" ? <button disabled={actionId === job.event_id} onClick={() => retryEventPurge(job)}>Повторить</button> : null}
                </div>
              ))}
            </section>
          )}

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
                        {purgeJobForEvent(event.id) ? <span className={`trash-purge-status ${purgeJobForEvent(event.id)?.status}`}>#{purgeJobForEvent(event.id)?.id} · {purgeJobForEvent(event.id)?.status}</span> : null}
                        <button disabled={actionId === event.id || ["queued", "running"].includes(purgeJobForEvent(event.id)?.status || "")} onClick={() => restoreEvent(event)}>Восстановить все sessions</button>
                        {purgeJobForEvent(event.id)?.status === "failed" ? (
                          <button className="danger" disabled={actionId === event.id} onClick={() => retryEventPurge(purgeJobForEvent(event.id)!)}>Повторить удаление</button>
                        ) : (
                          <button className="danger" disabled={actionId === event.id || ["queued", "running"].includes(purgeJobForEvent(event.id)?.status || "")} onClick={() => purgeEvent(event)}>
                            {purgeJobForEvent(event.id)?.status === "queued" ? "В очереди" : purgeJobForEvent(event.id)?.status === "running" ? "Удаляется…" : "Удалить из корзины навсегда"}
                          </button>
                        )}
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
