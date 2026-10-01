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

type ViewMode = "events" | "trash" | "storage";

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

function ProgressBar({ session, progress }: { session: Session; progress?: CaptureProgress }) {
  const percent = progress?.percent ?? localProgressPercent(session);
  const status = progress?.status || session.status;
  if (!isCaptureBusy({ ...session, status, completeness_status: progress?.completeness_status || session.completeness_status })) {
    return null;
  }
  if (percent == null) {
    return (
      <div className="capture-progress" aria-label="Сбор идет, процент недоступен">
        <div className="capture-progress-head"><span>Chat сбор</span><strong>LIVE</strong></div>
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
  const [trashSessions, setTrashSessions] = useState<Session[]>([]);
  const [view, setView] = useState<ViewMode>("events");
  const [expandedIds, setExpandedIds] = useState<string[]>([]);
  const [selected, setSelected] = useState<Session | null>(null);
  const [selectedVideo, setSelectedVideo] = useState<VideoSession | null>(null);
  const [videoRuns, setVideoRuns] = useState<VideoRun[]>([]);
  const [videoSegments, setVideoSegments] = useState<VideoSegment[]>([]);
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

  async function loadOutputSettings() {
    try {
      const res = await fetch(`${API}/api/v1/storage/output-settings`, { cache: "no-store" });
      if (!res.ok) throw new Error(await res.text());
      const data = await res.json() as OutputSettings;
      setOutputSettings(data);
      setOutputRootKey(data.output_root_key);
      setOutputSubdir(data.output_subdir);
      setOutputBatchSegments(data.batch_segments);
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

  async function loadTrash() {
    try {
      const res = await fetch(`${API}/api/v1/sessions?page_size=100&deleted=true`, { cache: "no-store" });
      if (!res.ok) throw new Error(await res.text());
      const data = await res.json();
      setTrashSessions(data.items || []);
      setError(null);
    } catch (e) {
      setError(String(e));
    }
  }

  async function loadCurrentView(targetView: ViewMode = view) {
    if (targetView === "events") await loadEvents();
    else if (targetView === "storage") await loadOutputSettings();
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
    try {
      const [detailRes, runsRes, segmentsRes] = await Promise.all([
        fetch(`${API}/api/v1/video-sessions/${session.id}`, { cache: "no-store" }),
        fetch(`${API}/api/v1/video-sessions/${session.id}/runs`, { cache: "no-store" }),
        fetch(`${API}/api/v1/video-sessions/${session.id}/segments?page_size=500`, { cache: "no-store" }),
      ]);
      if (!detailRes.ok) throw new Error(await detailRes.text());
      if (!runsRes.ok) throw new Error(await runsRes.text());
      if (!segmentsRes.ok) throw new Error(await segmentsRes.text());
      const [detail, runs, segments] = await Promise.all([detailRes.json(), runsRes.json(), segmentsRes.json()]);
      setSelectedVideo(detail);
      setVideoRuns(runs.items || []);
      setVideoSegments(segments.items || []);
      setError(null);
    } catch (e) {
      setError(String(e));
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
      setTrashSessions((current) => current.filter((item) => item.id !== session.id));
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
      setTrashSessions((current) => current.filter((item) => item.id !== session.id));
      if (selected?.id === session.id) setSelected(null);
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
        <button className="link" onClick={() => setSelectedVideo(null)}>← Events</button>
        <header className="detail-header">
          <div>
            <h1>Video Manager · output</h1>
            <p className="muted">{selectedVideo.metadata?.media_type?.toUpperCase() || "VIDEO"} · {selectedVideo.status} · {selectedVideo.completeness_status}</p>
          </div>
          <div className="actions">
            <button onClick={() => openVideoSession(selectedVideo)}>Обновить</button>
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
          <div className="section-label">SEGMENTS · spool → output</div>
          <p className="muted small">Закрытый segment сначала живёт в Docker spool. Backend переносит закрытые segments пачками в физический output. Spool очищается только после проверки всей пачки и DB commit.</p>
          {videoSegments.length === 0 ? <div className="empty-child">Закрытых сегментов пока нет</div> : (
            <div className="table-wrap"><table><thead><tr><th>Segment</th><th>Run</th><th>Timeline</th><th>Duration</th><th>Bytes</th><th>Storage</th><th>Integrity</th><th>Archived</th><th>Path / error</th></tr></thead><tbody>
              {videoSegments.map((segment) => <tr key={segment.id}>
                <td>#{segment.segment_no}</td><td>{segment.video_run_id}</td>
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
          <p className="muted">{view === "trash" ? "Корзина Chat sessions" : view === "storage" ? "Video output · spool batches" : "Twitch Events · Chat + Video sessions"}</p>
        </div>
        <div className="actions">
          <button className={view === "events" ? "active-tab" : ""} onClick={() => setView("events")}>Events</button>
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
                      {event.active_chat_sessions_count > 0 && <span className="active-badge">chat active</span>}
                      {event.active_video_sessions_count > 0 && <span className="active-badge">video active</span>}
                    </div>
                  </button>

                  {expanded && (
                    <div className="event-children">
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
                          <div className="session-actions"><button onClick={() => openVideoSession(session)}>Video Manager</button></div>
                        </div>
                      ))}
                    </div>
                  )}
                </article>
              );
            })}
          </section>
        </>
      ) : view === "storage" ? (
        <section className="storage-panel">
          <div className="section-label">VIDEO OUTPUT</div>
          {!outputSettings ? <div className="empty-child">Загрузка настроек…</div> : (
            <>
              <p className="muted">Диск задаётся абсолютным путём в .env; здесь выбирается разрешённый root и каталог внутри него. Настройка применяется только к новым Video sessions.</p>
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
                После копирования и SHA-256 проверки вся пачка фиксируется как archive_ready и удаляется из Docker spool. При Stop/EOF остаток меньше batch тоже переносится. Для рабочего режима оставляем 100; для короткой проверки можно временно поставить 5.
              </div>
              <div className="actions storage-actions">
                <button disabled={savingOutput} onClick={saveOutputSettings}>{savingOutput ? "Сохраняю…" : "Сохранить output"}</button>
              </div>
            </>
          )}
        </section>
      ) : (
        <>
          {trashSessions.length === 0 && <div className="empty">Корзина пуста</div>}
          <section className="grid">
            {trashSessions.map((session) => (
              <article className="card" key={session.id}>
                <button className="card-main" onClick={() => openSession(session)}>
                  <div className="row"><strong>{session.channel_login || "Twitch"}</strong><span>{session.media_type.toUpperCase()}</span></div>
                  <div className="title">{session.title || "Без названия"}</div>
                  <div className="row muted"><span>{session.status}</span><span>{session.completeness_status}</span></div>
                  <div className="row muted"><span>{new Date(session.created_at).toLocaleString()}</span><span>{fmtMs(session.source_duration_ms)}</span></div>
                </button>
                <div className="card-actions">
                  <button disabled={actionId === session.id} onClick={() => restoreSession(session)}>Восстановить</button>
                  <button className="danger" disabled={actionId === session.id} onClick={() => purgeSession(session)}>Удалить навсегда</button>
                </div>
              </article>
            ))}
          </section>
        </>
      )}
    </main>
  );
}

createRoot(document.getElementById("root")!).render(<React.StrictMode><App /></React.StrictMode>);
