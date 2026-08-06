import { useCallback, useEffect, useRef, useState } from "react";
import { useVoiceAgent, uploadDocument, type OrbState, type UploadKind } from "./useVoiceAgent";
import {
  AVATAR_CHOICES,
  createAssistant,
  deleteAssistant,
  getActiveId,
  listAssistants,
  setActiveId,
  updateAssistant,
  type Assistant,
} from "./assistants";
import { DEFAULT_VOICE, INDIA_VOICE, VOICES, sampleUrl, type VoiceOption } from "./voices";
import Orb from "./Orb";
import Board from "./Board";
import Hero from "./Hero";
import Dictation from "./Dictation";
import DayView from "./DayView";
import Elicit from "./Elicit";
import { AgentPanel, ArtifactView } from "./Agents";
import { useAgents } from "./useAgents";
import { useDay, useDayHistory } from "./useDay";
import { useDictationHistory } from "./useDictations";
import { useHistory } from "./useHistory";
import { FraiseMark, GitHubMark, SunIcon, MoonIcon } from "./icons";
import "./App.css";
import { SpeedInsights } from "@vercel/speed-insights/react";

function statusPill(
  status: "connecting" | "online" | "error",
  orbState: OrbState,
): { label: string; color: string } {
  if (status === "connecting") return { label: "Connecting…", color: "#C79A45" };
  if (status === "error") return { label: "Offline", color: "#E06B7E" };
  return {
    idle: { label: "Ready", color: "#7E8598" },
    listening: { label: "Listening", color: "#5B6CFF" },
    thinking: { label: "Working", color: "#3FE0A0" },
    speaking: { label: "Speaking", color: "#8493FF" },
  }[orbState];
}

const DOCK_HINT: Record<OrbState, string> = {
  idle: "Tap the orb to speak",
  listening: "Listening…",
  thinking: "Working…",
  speaking: "Speaking…",
};

export type Theme = "light" | "dark";

function useTheme(): [Theme, () => void] {
  const [theme, setTheme] = useState<Theme>(() => {
    const saved = localStorage.getItem("fraise-theme");
    if (saved === "light" || saved === "dark") return saved;
    return "light";
  });
  useEffect(() => {
    document.documentElement.classList.toggle("dark", theme === "dark");
    localStorage.setItem("fraise-theme", theme);
    let meta = document.querySelector<HTMLMetaElement>('meta[name="theme-color"]');
    if (!meta) {
      meta = document.createElement("meta");
      meta.name = "theme-color";
      document.head.appendChild(meta);
    }
    meta.content = theme === "dark" ? "#14120E" : "#F7F3E6";
  }, [theme]);
  return [theme, () => setTheme((t) => (t === "dark" ? "light" : "dark"))];
}

function useTypewriter(text: string, enabled: boolean, cps = 48): string {
  const [count, setCount] = useState(0);
  const printed = useRef("");

  useEffect(() => {
    if (!text.startsWith(printed.current)) setCount(0);
    printed.current = text;
  }, [text]);

  useEffect(() => {
    if (!enabled) {
      setCount(text.length);
      return;
    }
    if (count >= text.length) return;
    const id = setTimeout(() => setCount((c) => Math.min(c + 1, text.length)), 1000 / cps);
    return () => clearTimeout(id);
  }, [count, text, enabled, cps]);

  return text.slice(0, count);
}

const MenuIcon = () => (
  <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round">
    <path d="M4 7h16M4 12h16M4 17h16" />
  </svg>
);
function timeAgo(iso: string): string {
  const secs = (Date.now() - new Date(iso).getTime()) / 1000;
  if (secs < 60) return "just now";
  if (secs < 3600) return `${Math.floor(secs / 60)}m ago`;
  if (secs < 86400) return `${Math.floor(secs / 3600)}h ago`;
  return `${Math.floor(secs / 86400)}d ago`;
}

function greetingFor(d: Date): string {
  const h = d.getHours();
  if (h < 5) return "Working late";
  if (h < 12) return "Good morning";
  if (h < 18) return "Good afternoon";
  return "Good evening";
}

export default function App() {
  const [assistants, setAssistants] = useState<Assistant[]>(() => listAssistants());
  const [activeId, setActiveIdState] = useState<string>(() => getActiveId());
  const active = assistants.find((a) => a.id === activeId) ?? assistants[0];
  const [editing, setEditing] = useState<Assistant | "new" | null>(null);

  const switchTo = useCallback((id: string) => {
    setActiveId(id);
    setActiveIdState(id);
    setEditing(null);
  }, []);

  const handleVoiceSwitch = useCallback(
    (name: string) => {
      const target = listAssistants().find(
        (a) => a.name.trim().toLowerCase() === name.trim().toLowerCase(),
      );
      if (target) switchTo(target.id);
    },
    [switchTo],
  );

  const { messages, status, orbState, levelRef, outLevelRef, speechSupported, toggle, reconnect, notifyUpload, authNeeded, clearAuth, listening, elicitation, answerElicitation } = useVoiceAgent(handleVoiceSwitch);

  const { run, artifact, history, openId, openArtifact, dismiss: dismissRun } = useAgents(activeId);

  const { day, process: processDay, open: openDay, dismiss: dismissDay } = useDay(activeId);
  const dayHistory = useDayHistory(activeId, `${day?.dayId ?? ""}:${day?.status ?? ""}`);
  const [enteredApp, setEnteredApp] = useState(false);
  const [dictating, setDictating] = useState(false);
  const [openDictId, setOpenDictId] = useState<string | null>(null);
  const dictationHistory = useDictationHistory(activeId, `${dictating}`);

  const { turns, memories } = useHistory(activeId, messages.length);

  const prevIdRef = useRef(activeId);
  useEffect(() => {
    if (prevIdRef.current !== activeId) {
      prevIdRef.current = activeId;
      if (listening) reconnect();
    }
  }, [activeId, listening, reconnect]);

  const saveAssistant = useCallback(
    (data: { name: string; avatar: string; instructions: string; voice: string }, id?: string) => {
      if (id) {
        setAssistants(updateAssistant(id, data));
        if (id === activeId && listening) reconnect();
      } else {
        const created = createAssistant(data);
        setAssistants(listAssistants());
        switchTo(created.id);
      }
      setEditing(null);
    },
    [switchTo, activeId, listening, reconnect],
  );

  const removeAssistant = useCallback((id: string) => {
    setAssistants(deleteAssistant(id));
    setActiveIdState(getActiveId());
    setEditing(null);
  }, []);

  const [theme, toggleTheme] = useTheme();
  const [menuOpen, setMenuOpen] = useState(false);
  const [accountMenuOpen, setAccountMenuOpen] = useState(false);

  const [name, setName] = useState<string>(() => localStorage.getItem("fraise-name") ?? "");
  const [askName, setAskName] = useState<boolean>(() => localStorage.getItem("fraise-name") === null);
  const [editingName, setEditingName] = useState(false);
  const [nameInput, setNameInput] = useState("");
  const [voiceRegion, setVoiceRegion] = useState<"us" | "in">(() => (active?.voice === INDIA_VOICE ? "in" : "us"));
  const saveName = (raw: string) => {
    const clean = raw.trim().slice(0, 40);
    localStorage.setItem("fraise-name", clean);
    setName(clean);
    setAssistants(updateAssistant(activeId, { voice: voiceRegion === "in" ? INDIA_VOICE : DEFAULT_VOICE }));
    setAskName(false);
    setEditingName(false);
    if (listening) reconnect();
  };
  const openRename = () => {
    setNameInput(name);
    setVoiceRegion(active?.voice === INDIA_VOICE ? "in" : "us");
    setEditingName(true);
    setAccountMenuOpen(false);
  };

  const fileInputRef = useRef<HTMLInputElement>(null);
  const [docs, setDocs] = useState<{ name: string; chunks: number; kind: UploadKind }[]>([]);
  const [uploading, setUploading] = useState<string>("");
  const [docError, setDocError] = useState<string>("");

  async function handleFiles(files: FileList | null) {
    if (!files?.length) return;
    const file = files[0];
    setUploading(file.name);
    setDocError("");
    try {
      const { filename, chunks, kind } = await uploadDocument(file);
      setDocs((prev) => [{ name: filename, chunks, kind }, ...prev.filter((d) => d.name !== filename)]);
      notifyUpload(filename, kind);
    } catch (e) {
      setDocError(e instanceof Error ? e.message : "Upload failed");
      setTimeout(() => setDocError(""), 4000);
    } finally {
      setUploading("");
    }
  }

  const openDictation = useCallback(() => {
    if (listening) toggle();
    setOpenDictId(null);
    setDictating(true);
  }, [listening, toggle]);

  const closeDictation = useCallback(() => {
    setDictating(false);
    setOpenDictId(null);
  }, []);

  const dictationToTasks = useCallback(
    (text: string) => {
      closeDictation();
      dismissRun();
      void processDay(text);
    },
    [processDay, dismissRun, closeDictation],
  );

  const openPastDictation = useCallback((id: string) => {
    setOpenDictId(id);
    setDictating(true);
    setMenuOpen(false);
  }, []);

  const openPastDay = useCallback(
    (id: string) => {
      dismissRun();
      setMenuOpen(false);
      void openDay(id);
    },
    [openDay, dismissRun],
  );

  const waveRef = useRef<HTMLDivElement>(null);
  const orbStateRef = useRef(orbState);
  orbStateRef.current = orbState;
  useEffect(() => {
    let raf = 0;
    const tick = () => {
      const el = waveRef.current;
      if (el) {
        const s = orbStateRef.current;
        const scale =
          s === "listening" ? Math.min(1, 0.5 + levelRef.current * 1.6)
          : s === "speaking" ? 1
          : 0.5;
        el.style.setProperty("--wave-scale", scale.toFixed(3));
      }
      raf = requestAnimationFrame(tick);
    };
    raf = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(raf);
  }, [levelRef]);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const el = e.target as HTMLElement | null;
      const typing = el && (el.tagName === "INPUT" || el.tagName === "TEXTAREA" || el.isContentEditable);
      if (e.code === "Space" && !e.repeat && !typing && speechSupported && !dictating) {
        e.preventDefault();
        toggle();
      } else if (e.key === "Escape") {
        setMenuOpen(false);
        setAccountMenuOpen(false);
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [toggle, speechSupported, dictating]);

  const lastAgent = [...messages].reverse().find((m) => m.role === "agent");
  const lastUser = [...messages].reverse().find((m) => m.role === "user");
  const waveOn = orbState === "listening" || orbState === "speaking";

  let caption: string;
  let captionClass: string;
  if (!speechSupported) {
    caption = "Speech recognition isn't supported here — try Chrome.";
    captionClass = "idle";
  } else if (orbState === "thinking") {
    caption = "Working…";
    captionClass = "";
  } else if (orbState === "speaking") {
    caption = lastAgent?.text ?? "Speaking…";
    captionClass = "speaking";
  } else if (orbState === "listening") {
    caption = lastUser?.text ?? "Listening…";
    captionClass = "listening";
  } else {
    caption = messages.length
      ? (lastAgent ?? messages[messages.length - 1]).text
      : `${greetingFor(new Date())}${name ? `, ${name}` : ""} — what can I help with?`;
    captionClass = "idle";
  }

  const reduceMotion = useRef(window.matchMedia?.("(prefers-reduced-motion: reduce)").matches ?? false).current;
  const types = captionClass === "speaking" || captionClass === "idle";
  const typed = useTypewriter(caption, types && !reduceMotion);
  const caretOn = typed.length < caption.length || waveOn;

  const pill = statusPill(status, orbState);
  const showBoard = speechSupported && (messages.length === 0 || docs.length > 0 || !!uploading);

  if (!enteredApp) {
    return (
      <>
        <Hero
          orbState={orbState}
          onOrbClick={() => setEnteredApp(true)}
          inputLevelRef={levelRef}
          outputLevelRef={outLevelRef}
          day={day}
          onDismissDay={dismissDay}
          onEnterApp={() => setEnteredApp(true)}
          onDictate={() => {
            setEnteredApp(true);
            openDictation();
          }}
          theme={theme}
          onToggleTheme={toggleTheme}
        />
        {dictating && (
          <Dictation
            sid={activeId}
            openId={openDictId ?? undefined}
            onClose={closeDictation}
            onPlanDay={dictationToTasks}
          />
        )}
        <SpeedInsights />
      </>
    );
  }

  return (
    <div className={`stage ${orbState}`}>
      <div className="window" data-screen-label="Fraise — voice assistant">
        {menuOpen && <button className="scrim" aria-label="Close menu" onClick={() => setMenuOpen(false)} />}
        <aside className={`sidebar${menuOpen ? " open" : ""}`}>
          <div className="drawer-head">
            <span className="drawer-title">History &amp; memory</span>
            <button className="drawer-close" aria-label="Close" onClick={() => setMenuOpen(false)}>✕</button>
          </div>

          <div className="section-label">Research</div>

          <div className="recents">
            {history.length === 0 ? (
              <div className="recents-empty">
                Ask me to research something and the write-ups will collect here.
              </div>
            ) : (
              history.map((h) => (
                <button
                  key={h.id}
                  className={`recent${h.id === openId ? " active" : ""}`}
                  onClick={() => { void openArtifact(h.id); setMenuOpen(false); }}
                  title={h.title}
                >
                  <span className="recent-title">
                    <span className="dot" />
                    <span>{h.title}</span>
                  </span>
                  <span className="recent-meta">
                    {h.format === "slides" ? "Deck" : "Doc"} · {timeAgo(h.created_at)}
                  </span>
                </button>
              ))
            )}
          </div>

          {dayHistory.length > 0 && (
            <>
              <div className="section-label">Days</div>
              <div className="recents">
                {dayHistory.map((d) => (
                  <button
                    key={d.id}
                    className={`recent${day?.dayId === d.id ? " active" : ""}`}
                    onClick={() => openPastDay(d.id)}
                    title={d.snippet}
                  >
                    <span className="recent-title">
                      <span className="dot" />
                      <span>{d.snippet || "Dictated day"}</span>
                    </span>
                    <span className="recent-meta">
                      {d.task_count} task{d.task_count === 1 ? "" : "s"}
                      {d.status !== "done" && ` · ${d.status}`} · {timeAgo(d.created_at)}
                    </span>
                  </button>
                ))}
              </div>
            </>
          )}

          {dictationHistory.length > 0 && (
            <>
              <div className="section-label">Dictations</div>
              <div className="recents">
                {dictationHistory.map((d) => (
                  <button
                    key={d.id}
                    className={`recent${openDictId === d.id ? " active" : ""}`}
                    onClick={() => openPastDictation(d.id)}
                    title={d.snippet}
                  >
                    <span className="recent-title">
                      <span className="dot" />
                      <span>{d.snippet || "Dictation"}</span>
                    </span>
                    <span className="recent-meta">
                      {d.is_email ? "Email" : "Note"}
                      {d.status === "sent" && " · Sent"} · {timeAgo(d.created_at)}
                    </span>
                  </button>
                ))}
              </div>
            </>
          )}

          {turns.length > 0 && (
            <>
              <div className="section-label">Conversation</div>
              <div className="chat-log">
                {turns.map((t, i) => (
                  <div key={i} className={`chat-turn ${t.role}`}>
                    <span className="who">{t.role === "user" ? name || "You" : active.name}</span>
                    <span className="said">{t.text}</span>
                  </div>
                ))}
              </div>
            </>
          )}

          {memories.length > 0 && (
            <>
              <div className="section-label">Remembered</div>
              <div className="memories">
                {memories.slice(0, 6).map((m, i) => (
                  <div key={i} className="memory-line">{m}</div>
                ))}
              </div>
            </>
          )}

          <div className="account-wrap">
            {accountMenuOpen && (
              <button
                className="account-scrim"
                aria-label="Close account menu"
                onClick={() => setAccountMenuOpen(false)}
              />
            )}

            {accountMenuOpen && (
              <div className="account-menu" role="menu">
                <div className="acct-head">
                  <div className="account-mark lg">{(name || "You").charAt(0).toUpperCase()}</div>
                  <div style={{ minWidth: 0 }}>
                    <div className="account-name">{name || "You"}</div>
                    <div className="account-sub">Talking with {active.avatar} {active.name}</div>
                  </div>
                </div>

                <button className="acct-item" role="menuitem" onClick={openRename}>
                  <span className="acct-ico">
                    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round">
                      <path d="M12 20h9" />
                      <path d="M16.5 3.5a2.1 2.1 0 0 1 3 3L7 19l-4 1 1-4z" />
                    </svg>
                  </span>
                  Change your name
                </button>

                <button
                  className="acct-item"
                  role="menuitem"
                  onClick={() => { setEditing(active); setAccountMenuOpen(false); }}
                >
                  <span className="acct-ico avatar">{active.avatar}</span>
                  Customize {active.name}’s vibe
                </button>

                <div className="acct-sep" />

                <div className="acct-appearance">
                  <span className="acct-label">Appearance</span>
                  <div className="acct-theme" role="group" aria-label="Theme">
                    <button
                      className={theme === "light" ? "on" : ""}
                      onClick={() => theme !== "light" && toggleTheme()}
                      aria-pressed={theme === "light"}
                    >
                      Light
                    </button>
                    <button
                      className={theme === "dark" ? "on" : ""}
                      onClick={() => theme !== "dark" && toggleTheme()}
                      aria-pressed={theme === "dark"}
                    >
                      Dark
                    </button>
                  </div>
                </div>
              </div>
            )}

            <button
              className={`account${accountMenuOpen ? " open" : ""}`}
              aria-haspopup="menu"
              aria-expanded={accountMenuOpen}
              onClick={() => setAccountMenuOpen((o) => !o)}
            >
              <div className="account-mark">{(name || "You").charAt(0).toUpperCase()}</div>
              <div className="account-id">
                <div className="account-name">{name || "You"}</div>
                <div className="account-sub">{active.avatar} {active.name}</div>
              </div>
              <span className="account-dots" aria-hidden="true">
                <svg width="16" height="16" viewBox="0 0 24 24">
                  <circle cx="5" cy="12" r="2" />
                  <circle cx="12" cy="12" r="2" />
                  <circle cx="19" cy="12" r="2" />
                </svg>
              </span>
            </button>
          </div>

          <div className="credit">
            made by <a href="https://github.com/Dhruv-kys/Fraise" target="_blank" rel="noreferrer noopener">Dhruv</a>
          </div>
        </aside>

        <main className="main">
          <header className="main-header">
            <div className="header-left">
              <button
                className="icon-btn menu"
                aria-label="History, memory, and account"
                title="History, memory, and account"
                onClick={() => setMenuOpen(true)}
              >
                <MenuIcon />
              </button>
              <button className="nav-brand" onClick={() => setEnteredApp(false)} title="Back to the front door">
                <span className="nav-brand-mark"><FraiseMark /></span>
                <span className="nav-brand-name">Fraise</span>
                <span className="nav-brand-slash">/workspace</span>
              </button>
              <div className="status-pill">
                <span className="dot" style={{ background: pill.color }} />
                <span className="label">{pill.label}</span>
              </div>
            </div>

            <div className="assistant-switcher" role="tablist" aria-label="Assistants">
              {assistants.map((a) => {
                const isActive = a.id === activeId;
                return (
                  <button
                    key={a.id}
                    className={`assistant-pill${isActive ? " active" : ""}`}
                    role="tab"
                    aria-selected={isActive}
                    title={isActive ? `${a.name} — edit` : `Switch to ${a.name}`}
                    onClick={() => (isActive ? setEditing(a) : switchTo(a.id))}
                  >
                    <span className="assistant-avatar">{a.avatar}</span>
                    {isActive && <span className="assistant-name">{a.name}</span>}
                  </button>
                );
              })}
              <button
                className="assistant-add"
                title="New assistant"
                aria-label="New assistant"
                onClick={() => setEditing("new")}
              >
                +
              </button>
            </div>

            <div className="header-right">
              <button
                className="icon-btn"
                onClick={openDictation}
                disabled={!speechSupported}
                aria-label="Dictate"
                title="Dictate — speak a long message or essay, transcribed locally"
              >
                <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
                  <rect x="9" y="3" width="6" height="11" rx="3" />
                  <path d="M5 11a7 7 0 0 0 14 0M12 18v3" />
                </svg>
              </button>
              <a
                className="icon-btn"
                href="https://github.com/Dhruv-kys/Fraise"
                target="_blank"
                rel="noreferrer noopener"
                aria-label="View source on GitHub"
                title="View source on GitHub"
              >
                <GitHubMark />
              </a>
              <button
                className="icon-btn"
                aria-label={theme === "dark" ? "Switch to light mode" : "Switch to dark mode"}
                aria-pressed={theme === "dark"}
                onClick={toggleTheme}
              >
                {theme === "dark" ? <SunIcon /> : <MoonIcon />}
              </button>
              <button className="new-conv nav-cta" onClick={() => toggle()}>
                <span className="plus">+</span>
                <span className="cta-label">New conversation</span>
              </button>
            </div>
          </header>

          {authNeeded && (
            <div className="auth-banner">
              <span>Google Calendar needs to be connected.</span>
              <button
                className="auth-banner-btn"
                onClick={() => {
                  window.open(authNeeded, "_blank");
                  clearAuth();
                }}
              >
                Connect Calendar
              </button>
            </div>
          )}

          <section className="assistant-stage">
            <div className={`hero${run || artifact || day || elicitation ? " compact" : ""}`}>
              <Orb state={orbState} onClick={toggle} inputLevelRef={levelRef} outputLevelRef={outLevelRef} />

              <div className="caption-wrap">
                <p className={`caption ${captionClass}`} aria-live="polite">
                  {typed}
                  {caretOn && <span className="caret" />}
                </p>
              </div>
            </div>

            {elicitation && <Elicit ask={elicitation} onAnswer={answerElicitation} />}

            {artifact ? (
              <ArtifactView artifact={artifact} onClose={dismissRun} />
            ) : run ? (
              <AgentPanel run={run} />
            ) : day ? (
              <DayView day={day} onClose={dismissDay} />
            ) : (
              !elicitation && showBoard && <Board docs={docs} uploading={uploading} receded={orbState !== "idle"} />
            )}
            <input
              ref={fileInputRef}
              type="file"
              accept=".txt,.md,.pdf,.mp3,.m4a,.mp4,.wav,.aac,.flac,.ogg,.opus,.webm,.mov,.amr,audio/*"
              hidden
              onChange={(e) => handleFiles(e.target.files)}
            />
          </section>

          <footer className="dock">
            <div ref={waveRef} className={`wave${waveOn ? " on" : ""}`}>
              {Array.from({ length: 13 }).map((_, i) => (
                <span key={i} />
              ))}
            </div>
            <div className="dock-row">
              <span className="dock-hint">
                {speechSupported ? DOCK_HINT[orbState] : "Voice needs Chrome or Edge"}
                {speechSupported && orbState === "idle" && <kbd className="kbd">Space</kbd>}
              </span>
              <button
                className="dock-upload"
                onClick={openDictation}
                disabled={!speechSupported}
                title="Dictate — speak a long message or essay, transcribed locally"
                aria-label="Dictate"
              >
                <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
                  <rect x="9" y="3" width="6" height="11" rx="3" />
                  <path d="M5 11a7 7 0 0 0 14 0M12 18v3" />
                </svg>
                <span className="dock-upload-label">Dictate</span>
              </button>
              <button
                className={`dock-upload${docError ? " error" : ""}`}
                onClick={() => fileInputRef.current?.click()}
                onDragOver={(e) => e.preventDefault()}
                onDrop={(e) => {
                  e.preventDefault();
                  handleFiles(e.dataTransfer.files);
                }}
                title={docError || "Add a document or a call recording — .txt, .md, .pdf, or audio"}
                aria-label={docError || "Add a document or recording"}
              >
                <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
                  <path d="M12 16V4M8 8l4-4 4 4" />
                  <path d="M4 15v3a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2v-3" />
                </svg>
                <span className="dock-upload-label">
                  {uploading ? "Adding…" : docError ? "Try again" : "Add file"}
                </span>
              </button>
            </div>
          </footer>
        </main>
      </div>

      {(askName || editingName) && (
        <div className="name-overlay" onClick={() => editingName && setEditingName(false)}>
          <form
            className="name-card"
            onClick={(e) => e.stopPropagation()}
            onSubmit={(e) => {
              e.preventDefault();
              saveName(nameInput);
            }}
          >
            <div className="brand-mark name-mark"><FraiseMark /></div>
            <h2 className="name-title">{editingName ? "Your name" : "Welcome to Fraise"}</h2>
            <p className="name-sub">Choose your voice</p>
            <div className="region-picker" role="radiogroup" aria-label="Voice">
              <button
                type="button"
                className={`region-choice${voiceRegion === "us" ? " active" : ""}`}
                role="radio"
                aria-checked={voiceRegion === "us"}
                onClick={() => setVoiceRegion("us")}
              >
                🇺🇸 USA
              </button>
              <button
                type="button"
                className={`region-choice${voiceRegion === "in" ? " active" : ""}`}
                role="radio"
                aria-checked={voiceRegion === "in"}
                onClick={() => setVoiceRegion("in")}
              >
                🇮🇳 India
              </button>
            </div>
            <p className="name-sub">What should I call you?</p>
            <input
              className="name-input"
              type="text"
              autoFocus
              placeholder="Your name"
              value={nameInput}
              maxLength={40}
              onChange={(e) => setNameInput(e.target.value)}
            />
            <button className="name-go" type="submit" disabled={!nameInput.trim()}>
              {editingName ? "Save" : "Continue"}
            </button>
            <button
              className="name-skip"
              type="button"
              onClick={() => (editingName ? setEditingName(false) : saveName(""))}
            >
              {editingName ? "Cancel" : "Skip for now"}
            </button>
          </form>
        </div>
      )}

      {dictating && (
        <Dictation
          sid={activeId}
          openId={openDictId ?? undefined}
          onClose={closeDictation}
          onPlanDay={dictationToTasks}
        />
      )}

      {editing && (
        <AssistantEditor
          assistant={editing === "new" ? null : editing}
          canDelete={editing !== "new" && assistants.length > 1}
          onSave={saveAssistant}
          onDelete={removeAssistant}
          onCancel={() => setEditing(null)}
        />
      )}
      <SpeedInsights />
    </div>
  );
}

function AssistantEditor({
  assistant,
  canDelete,
  onSave,
  onDelete,
  onCancel,
}: {
  assistant: Assistant | null;
  canDelete: boolean;
  onSave: (data: { name: string; avatar: string; instructions: string; voice: string }, id?: string) => void;
  onDelete: (id: string) => void;
  onCancel: () => void;
}) {
  const [name, setName] = useState(assistant?.name ?? "");
  const [avatar, setAvatar] = useState(assistant?.avatar ?? AVATAR_CHOICES[0]);
  const [instructions, setInstructions] = useState(assistant?.instructions ?? "");
  const [voice, setVoice] = useState(assistant?.voice ?? DEFAULT_VOICE);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onCancel();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onCancel]);

  return (
    <div className="name-overlay" onClick={onCancel}>
      <form
        className="name-card editor-card"
        onClick={(e) => e.stopPropagation()}
        onSubmit={(e) => {
          e.preventDefault();
          onSave({ name: name.trim() || "New assistant", avatar, instructions, voice }, assistant?.id);
        }}
      >
        <div className="editor-avatar-preview">{avatar}</div>
        <h2 className="name-title">{assistant ? "Edit assistant" : "New assistant"}</h2>
        <p className="name-sub">A separate name, look, and memory of its own.</p>

        <label className="editor-label">Voice</label>
        <VoicePicker value={voice} onChange={setVoice} />

        <label className="editor-label">Name</label>
        <input
          className="name-input"
          type="text"
          placeholder="Work, Personal, Coach…"
          value={name}
          maxLength={40}
          onChange={(e) => setName(e.target.value)}
        />

        <label className="editor-label">Avatar</label>
        <div className="avatar-grid">
          {AVATAR_CHOICES.map((e) => (
            <button
              key={e}
              type="button"
              className={`avatar-choice${e === avatar ? " active" : ""}`}
              onClick={() => setAvatar(e)}
            >
              {e}
            </button>
          ))}
        </div>

        <label className="editor-label">Custom instructions</label>
        <textarea
          className="editor-textarea"
          placeholder="Tone, role, and standing rules — e.g. 'You're my work assistant. Be concise and professional.'"
          value={instructions}
          maxLength={1500}
          rows={4}
          onChange={(e) => setInstructions(e.target.value)}
        />

        <button className="name-go" type="submit">
          {assistant ? "Save" : "Create"}
        </button>
        {canDelete && assistant && (
          <button
            className="name-skip editor-delete"
            type="button"
            onClick={() => onDelete(assistant.id)}
          >
            Delete this assistant
          </button>
        )}
        <button className="name-skip" type="button" onClick={onCancel}>
          Cancel
        </button>
      </form>
    </div>
  );
}

function VoiceAvatar({ voice }: { voice: VoiceOption }) {
  return (
    <div
      className="voice-avatar"
      style={{
        background: `linear-gradient(135deg, hsl(${voice.hue} 70% 42%), hsl(${voice.hue + 40} 65% 28%))`,
      }}
      aria-hidden="true"
    >
      {voice.name[0]}
    </div>
  );
}

function VoicePicker({ value, onChange }: { value: string; onChange: (id: string) => void }) {
  const audioRef = useRef<HTMLAudioElement | null>(null);
  const [playingId, setPlayingId] = useState<string | null>(null);

  const play = (id: string) => {
    const audio = audioRef.current;
    if (!audio) return;
    if (playingId === id) {
      audio.pause();
      setPlayingId(null);
      return;
    }
    audio.src = sampleUrl(id);
    audio.play().catch(() => setPlayingId(null));
    setPlayingId(id);
  };

  return (
    <div className="voice-grid">
      <audio
        ref={audioRef}
        onEnded={() => setPlayingId(null)}
        onError={() => setPlayingId(null)}
      />
      {VOICES.map((v) => (
        <div
          key={v.id}
          className={`voice-card${v.id === value ? " active" : ""}`}
          onClick={() => onChange(v.id)}
        >
          <VoiceAvatar voice={v} />
          <div className="voice-info">
            <span className="voice-name">{v.name}</span>
            <span className="voice-meta">{v.accent} · {v.traits.slice(0, 2).join(", ")}</span>
          </div>
          <button
            type="button"
            className={`voice-play${playingId === v.id ? " on" : ""}`}
            title={`Hear ${v.name} say "Hi, I'm Fraise."`}
            onClick={(e) => {
              e.stopPropagation();
              play(v.id);
            }}
          >
            {playingId === v.id ? "❚❚" : "▶"}
          </button>
        </div>
      ))}
    </div>
  );
}

