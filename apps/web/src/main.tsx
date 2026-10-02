import React, { useEffect, useMemo, useState } from "react";
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

type PartPlan = {
  mode: "manual" | "target";
  from_segment_no: number;
  end_segment_no: number;
  run_no: number;
  segment_count: number;
  expected_bytes: number;
  duration_ms: number;
  target_mib?: number | null;
  warnings: string[];
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

type ViewMode = "events" | "video-manager" | "trash" | "storage";

const API = import.meta.env.VITE_API_BASE_URL || "http://localhost:18741";
const PROGRESS_POLL_MS = 5000;
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
  const [partMode, setPartMode] = useState<"manual" | "target">("manual");
  const [partFromSegment, setPartFromSegment] = useState(1);
  const [partToSegment, setPartToSegment] = useState(1);
  const [partTargetMib, setPartTargetMib] = useState(1024);
  const [partPlan, setPartPlan] = useState<PartPlan | null>(null);
  const [partAction, setPartAction] = useState(false);
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
      setError(null);
    } catch (e) {
      setError(String(e));
    }
  }

  function partRequestBody() {
    return partMode === "manual"
      ? { mode: "manual", from_segment_no: partFromSegment, to_segment_no: partToSegment }
      : { mode: "target", from_segment_no: partFromSegment, target_mib: partTargetMib };
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
      setError(null);
    } catch (e) {
      setPartPlan(null);
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
      await loadVideoParts(selectedVideo.id);
      const reserved = new Set<number>();
      [...videoParts, created].forEach((part) => {
        for (let no = part.start_segment_no; no <= part.end_segment_no; no += 1) reserved.add(no);
      });
      const after = created.plan?.end_segment_no || created.end_segment_no;
      const next = videoSegments.find((segment) => (
        segment.segment_no > after
        && segment.storage_state === "archive_ready"
        && segment.integrity_state === "hashed"
        && !reserved.has(segment.segment_no)
      ))?.segment_no || after + 1;
      setPartFromSegment(next);
      setPartToSegment(next);
      setPartPlan(null);
      setError(created.builder_wakeup_error ? `Part поставлен в durable queue, но wakeup builder не прошёл: ${created.builder_wakeup_error}` : null);
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
            <button className={partMode === "manual" ? "active-tab" : ""} onClick={() => { setPartMode("manual"); setPartPlan(null); }}>Manual range</button>
            <button className={partMode === "target" ? "active-tab" : ""} onClick={() => { setPartMode("target"); setPartPlan(null); }}>Target MiB</button>
          </div>
          <div className="part-controls">
            <label className="storage-field"><span>From segment</span><input type="number" min={1} value={partFromSegment} onChange={(e) => { const value = Math.max(1, Number(e.target.value) || 1); setPartFromSegment(value); if (partMode === "manual" && partToSegment < value) setPartToSegment(value); setPartPlan(null); }} /></label>
            {partMode === "manual" ? (
              <label className="storage-field"><span>To segment</span><input type="number" min={partFromSegment} value={partToSegment} onChange={(e) => { setPartToSegment(Math.max(partFromSegment, Number(e.target.value) || partFromSegment)); setPartPlan(null); }} /></label>
            ) : (
              <label className="storage-field"><span>Target MiB</span><input type="number" min={1} value={partTargetMib} onChange={(e) => { setPartTargetMib(Math.max(1, Number(e.target.value) || 1)); setPartPlan(null); }} /></label>
            )}
          </div>
          <div className="actions part-actions">
            <button disabled={partAction} onClick={previewPart}>Preview</button>
            <button disabled={partAction || !partPlan} onClick={buildPart}>{partAction ? "Работаю…" : "Build Part"}</button>
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
        </section>
        <section className="video-block">
          <div className="section-label">PARTS / BUILD QUEUE</div>
          {videoParts.length === 0 ? <div className="empty-child">Parts ещё не создавались</div> : (
            <div className="part-list">
              {videoParts.map((part) => {
                const job = part.job;
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
          <p className="muted">{view === "trash" ? "Корзина sessions · сгруппировано по уникальному Event" : view === "video-manager" ? "Video sessions · Segments · Parts · Build Queue" : view === "storage" ? "Video output · spool batches · migration" : "Twitch Events · Chat + Video sessions"}</p>
        </div>
        <div className="actions">
          <button className={view === "events" ? "active-tab" : ""} onClick={() => setView("events")}>Events</button>
          <button className={view === "video-manager" ? "active-tab" : ""} onClick={() => setView("video-manager")}>Video Manager</button>
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
       ) : view === "video-manager" ? (
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
