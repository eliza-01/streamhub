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
  chat_sessions_count: number;
  active_chat_sessions_count: number;
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

type ViewMode = "events" | "trash";

const API = import.meta.env.VITE_API_BASE_URL || "http://localhost:18741";
const PROGRESS_POLL_MS = 5000;
const CAPTURE_BUSY_STATUSES = new Set(["arming", "recording", "paused", "reconciling"]);
const CAPTURE_BUSY_COMPLETENESS = new Set(["collecting", "verifying"]);

function fmtMs(ms?: number | null) {
  if (ms == null) return "—";
  const sec = Math.floor(ms / 1000);
  const h = Math.floor(sec / 3600);
  const m = Math.floor((sec % 3600) / 60);
  const s = sec % 60;
  return [h, m, s].map((v) => String(v).padStart(2, "0")).join(":");
}

function isCaptureBusy(session: Session) {
  return CAPTURE_BUSY_STATUSES.has(session.status) || CAPTURE_BUSY_COMPLETENESS.has(session.completeness_status);
}

function localProgressPercent(session: Session): number | null {
  if (session.progress_percent != null) return session.progress_percent;
  if (session.completeness_status === "complete" || session.status === "completed") return 100;
  if (session.media_type !== "vod" || !session.source_duration_ms || session.source_duration_ms <= 0) return null;
  const covered = Math.max(0, Math.min(session.coverage_end_ms || 0, session.source_duration_ms));
  return Math.min(99.9, Math.round((covered / session.source_duration_ms) * 1000) / 10);
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
        <div className="capture-progress-head"><span>Сбор идет</span><strong>LIVE</strong></div>
        <div className="progress-track indeterminate"><div className="progress-fill" /></div>
      </div>
    );
  }

  const safePercent = Math.max(0, Math.min(100, percent));
  return (
    <div className="capture-progress" aria-label={`Прогресс сбора ${safePercent.toFixed(1)}%`}>
      <div className="capture-progress-head"><span>Сбор</span><strong>{safePercent.toFixed(1)}%</strong></div>
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
  const [messages, setMessages] = useState<Message[]>([]);
  const [progressBySession, setProgressBySession] = useState<Record<string, CaptureProgress>>({});
  const [position, setPosition] = useState(0);
  const [playing, setPlaying] = useState(false);
  const [seekText, setSeekText] = useState("00:00:00");
  const [error, setError] = useState<string | null>(null);
  const [actionId, setActionId] = useState<string | null>(null);

  async function loadEvents() {
    try {
      const res = await fetch(`${API}/api/v1/events?page_size=100`, { cache: "no-store" });
      if (!res.ok) throw new Error(await res.text());
      const data = await res.json();
      setEvents(data.items || []);
      setProgressBySession({});
      setError(null);
    } catch (e) {
      setError(String(e));
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
    else await loadTrash();
  }

  async function openSession(session: Session) {
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
        .filter((event) => event.chat_sessions_count > 0));
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

  const monitoredIds = useMemo(
    () => view === "events"
      ? events.flatMap((event) => event.chat_sessions).filter(isCaptureBusy).map((session) => session.id)
      : [],
    [events, view]
  );
  const monitoredKey = monitoredIds.join(",");

  useEffect(() => {
    if (!monitoredKey) return;

    let cancelled = false;
    let timer: number | undefined;
    let inFlight: AbortController | null = null;
    const ids = monitoredKey.split(",");

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
        ids.forEach((id) => params.append("session_id", id));
        const res = await fetch(`${API}/api/v1/sessions/capture-progress?${params.toString()}`, {
          signal: inFlight.signal,
          cache: "no-store",
        });
        if (!res.ok) throw new Error(await res.text());
        const data = await res.json();
        const updates = (data.items || []) as CaptureProgress[];
        if (cancelled) return;

        const updatesById = Object.fromEntries(updates.map((item) => [item.session_id, item]));
        setProgressBySession((current) => ({ ...current, ...updatesById }));
        setEvents((current) => current.map((event) => ({
          ...event,
          chat_sessions: event.chat_sessions.map((session) => {
            const update = updatesById[session.id];
            return update ? {
              ...session,
              status: update.status,
              completeness_status: update.completeness_status,
              source_duration_ms: update.source_duration_ms,
              coverage_end_ms: update.coverage_end_ms,
              progress_percent: update.percent,
            } : session;
          }),
        })));
        setSelected((current) => {
          if (!current) return current;
          const update = updatesById[current.id];
          return update ? {
            ...current,
            status: update.status,
            completeness_status: update.completeness_status,
            source_duration_ms: update.source_duration_ms,
            coverage_end_ms: update.coverage_end_ms,
            progress_percent: update.percent,
          } : current;
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
          <input
            type="range"
            min={0}
            max={Math.max(1, total)}
            value={Math.min(position, total)}
            onChange={(e) => setPosition(Number(e.target.value))}
          />
          <input value={seekText} onChange={(e) => setSeekText(e.target.value)} onKeyDown={(e) => e.key === "Enter" && seekByText()} aria-label="HH:MM:SS seek" />
          <button onClick={seekByText}>Seek</button>
          <strong>{fmtMs(position)} / {fmtMs(total)}</strong>
        </section>
        <section className="chat">
          {visible.map((m) => (
            <div key={m.id} className="message">
              <span className="time">{fmtMs(m.timeline_offset_ms)}</span>
              <b>{m.chatter_name || m.chatter_login || "unknown"}</b>
              <span>{m.message_text}</span>
            </div>
          ))}
        </section>
      </main>
    );
  }

  return (
    <main>
      <header>
        <div>
          <h1>StreamHub</h1>
          <p className="muted">{view === "trash" ? "Корзина Chat sessions" : "Twitch Events · Chat sessions"}</p>
        </div>
        <div className="actions">
          <button className={view === "events" ? "active-tab" : ""} onClick={() => setView("events")}>Events</button>
          <button className={view === "trash" ? "active-tab" : ""} onClick={() => setView("trash")}>Корзина</button>
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
                      <div className="row">
                        <strong>{event.channel_display_name || event.channel_login || "Twitch"}</strong>
                        <span className="event-type">{event.media_type.toUpperCase()}</span>
                      </div>
                      <div className="event-title">{event.title || (event.media_type === "vod" ? `VOD ${event.external_key.split(":").pop()}` : "LIVE stream")}</div>
                      <div className="row muted event-meta">
                        <span>{new Date(event.source_started_at_utc || event.created_at).toLocaleString()}</span>
                        <span>{event.media_type === "vod" ? fmtMs(event.source_duration_ms) : "LIVE"}</span>
                      </div>
                    </div>
                    <div className="event-badges">
                      <span>Chat: {event.chat_sessions_count}</span>
                      {event.metadata?.identity_state && event.metadata.identity_state !== "canonical" && <span>legacy identity</span>}
                      {event.active_chat_sessions_count > 0 && <span className="active-badge">active {event.active_chat_sessions_count}</span>}
                    </div>
                  </button>

                  {expanded && (
                    <div className="event-children">
                      <div className="section-label">CHAT SESSIONS</div>
                      {event.chat_sessions.length === 0 ? (
                        <div className="empty-child">Нет активных Chat sessions</div>
                      ) : event.chat_sessions.map((session, index) => (
                        <div className="session-row" key={session.id}>
                          <div className="session-number">#{index + 1}</div>
                          <div className="session-info">
                            <div className="session-status-line">
                              <strong>{session.status}</strong>
                              <span>{session.completeness_status}</span>
                              <span>{(session.message_count || 0).toLocaleString()} сообщений</span>
                            </div>
                            <div className="muted session-date">{new Date(session.created_at).toLocaleString()}</div>
                            <ProgressBar session={session} progress={progressBySession[session.id]} />
                          </div>
                          <div className="session-actions">
                            <button onClick={() => openSession(session)}>Открыть</button>
                            <button
                              className="danger subtle"
                              disabled={actionId === session.id || isCaptureBusy(session)}
                              title={isCaptureBusy(session) ? "Сначала остановите сбор" : "Переместить в корзину"}
                              onClick={() => moveToTrash(session)}
                            >
                              В корзину
                            </button>
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
