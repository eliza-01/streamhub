import React, { useEffect, useMemo, useState } from "react";
import { createRoot } from "react-dom/client";
import "./styles.css";

type Session = {
  id: string;
  media_type: "live" | "vod";
  status: string;
  completeness_status: string;
  channel_login?: string | null;
  title?: string | null;
  created_at: string;
  source_duration_ms?: number | null;
};

type Message = {
  id: number;
  timeline_offset_ms: number;
  chatter_name?: string | null;
  chatter_login?: string | null;
  message_text: string;
};

const API = import.meta.env.VITE_API_BASE_URL || "http://localhost:18741";

function fmtMs(ms?: number | null) {
  if (ms == null) return "—";
  const sec = Math.floor(ms / 1000);
  const h = Math.floor(sec / 3600);
  const m = Math.floor((sec % 3600) / 60);
  const s = sec % 60;
  return [h, m, s].map((v) => String(v).padStart(2, "0")).join(":");
}

function App() {
  const [sessions, setSessions] = useState<Session[]>([]);
  const [selected, setSelected] = useState<Session | null>(null);
  const [messages, setMessages] = useState<Message[]>([]);
  const [position, setPosition] = useState(0);
  const [playing, setPlaying] = useState(false);
  const [seekText, setSeekText] = useState("00:00:00");
  const [error, setError] = useState<string | null>(null);

  async function loadSessions() {
    try {
      const res = await fetch(`${API}/api/v1/sessions?page_size=100`);
      if (!res.ok) throw new Error(await res.text());
      const data = await res.json();
      setSessions(data.items || []);
      setError(null);
    } catch (e) {
      setError(String(e));
    }
  }

  async function openSession(session: Session) {
    setSelected(session);
    setPosition(0);
    setPlaying(false);
    const res = await fetch(`${API}/api/v1/sessions/${session.id}/messages?from_ms=0&limit=2000`);
    const data = await res.json();
    setMessages(data.items || []);
  }

  useEffect(() => { loadSessions(); }, []);

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
    return (
      <main>
        <button className="link" onClick={() => setSelected(null)}>← Сессии</button>
        <h1>{selected.channel_login || "Twitch"}</h1>
        <p className="muted">{selected.title || "Без названия"} · {selected.media_type.toUpperCase()} · {selected.completeness_status}</p>
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
          <p className="muted">Сессии Twitch-чата</p>
        </div>
        <button onClick={loadSessions}>Обновить</button>
      </header>
      {error && <div className="error">{error}</div>}
      <section className="grid">
        {sessions.map((s) => (
          <button className="card" key={s.id} onClick={() => openSession(s)}>
            <div className="row"><strong>{s.channel_login || "Twitch"}</strong><span>{s.media_type.toUpperCase()}</span></div>
            <div className="title">{s.title || "Без названия"}</div>
            <div className="row muted"><span>{s.status}</span><span>{s.completeness_status}</span></div>
            <div className="row muted"><span>{new Date(s.created_at).toLocaleString()}</span><span>{fmtMs(s.source_duration_ms)}</span></div>
          </button>
        ))}
      </section>
    </main>
  );
}

createRoot(document.getElementById("root")!).render(<React.StrictMode><App /></React.StrictMode>);
