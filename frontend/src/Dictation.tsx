import { useCallback, useEffect, useRef, useState } from "react";
import { useDictation, type DictationStatus } from "./useDictation";
import { httpBase } from "./useVoiceAgent";

const STATUS_LABEL: Record<DictationStatus, string> = {
  idle: "Ready",
  connecting: "Connecting…",
  loading: "Warming up…",
  listening: "Listening",
  paused: "Paused",
  finishing: "Transcribing…",
  polishing: "Polishing…",
  done: "Done",
  error: "Error",
};

const EMAIL_RE = /^[^@\s]+@[^@\s]+\.[^@\s]+$/;

function fmtTime(s: number): string {
  const m = Math.floor(s / 60);
  return `${m}:${String(s % 60).padStart(2, "0")}`;
}

export default function Dictation({
  sid,
  openId,
  onClose,
  onPlanDay,
}: {
  sid: string;
  openId?: string;
  onClose: () => void;
  onPlanDay: (text: string) => void;
}) {
  const {
    status, segments, interim, id, polished, isEmail, email, setEmail, note, error, elapsed, levelRef,
    start, pause, resume, stop, open, cancel,
  } = useDictation(sid);
  const [draft, setDraft] = useState("");
  const [view, setView] = useState<"polished" | "raw">("polished");
  const [copied, setCopied] = useState(false);
  const [sendState, setSendState] = useState<"idle" | "sending" | "sent" | "error">("idle");
  const [sendError, setSendError] = useState("");
  const feedRef = useRef<HTMLDivElement>(null);
  const dotRef = useRef<HTMLSpanElement>(null);

  useEffect(() => {
    if (openId) {
      void open(openId);
      return;
    }
    const t = window.setTimeout(() => void start(), 60);
    return () => window.clearTimeout(t);
  }, [openId, open, start]);

  useEffect(() => {
    if (status === "done") setDraft(view === "polished" && polished ? polished : segments.join(" "));
  }, [status, segments, polished, view]);

  useEffect(() => {
    feedRef.current?.scrollTo({ top: feedRef.current.scrollHeight });
  }, [segments]);

  useEffect(() => {
    let raf = 0;
    const tick = () => {
      const el = dotRef.current;
      if (el) el.style.setProperty("--mic-level", levelRef.current.toFixed(3));
      raf = requestAnimationFrame(tick);
    };
    raf = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(raf);
  }, [levelRef]);

  const close = useCallback(() => {
    cancel();
    onClose();
  }, [cancel, onClose]);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") close();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [close]);

  const recording = status === "listening" || status === "paused";
  const busy = status === "connecting" || status === "loading" || status === "finishing" || status === "polishing";
  const liveText = segments.join(" ");
  const finalText = draft.trim();
  const words = finalText ? finalText.split(/\s+/).length : liveText ? liveText.split(/\s+/).length : 0;

  const copy = async () => {
    try {
      await navigator.clipboard.writeText(finalText);
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    } catch {}
  };

  const send = async () => {
    if (!id || !email || !EMAIL_RE.test(email.to)) return;
    setSendState("sending");
    setSendError("");
    try {
      const url = new URL(`/dictations/${id}/send`, httpBase());
      url.searchParams.set("sid", sid);
      const res = await fetch(url, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ to: email.to, subject: email.subject, body: email.body }),
      });
      if (!res.ok) {
        const detail = await res.json().catch(() => null);
        throw new Error(detail?.detail ?? `send failed (${res.status})`);
      }
      setSendState("sent");
    } catch (e) {
      setSendState("error");
      setSendError(e instanceof Error ? e.message : "Couldn't send.");
    }
  };

  return (
    <div className="name-overlay" onClick={close}>
      <div
        className={`name-card dict-card${status === "done" ? " sheet" : ""}${status === "done" && isEmail ? " email" : ""}`}
        onClick={(e) => e.stopPropagation()}
      >
        <div className="dict-head">
          <h2 className="name-title dict-title">{status === "done" && isEmail ? "Email" : "Dictation"}</h2>
          <span className={`dict-status ${status}`}>
            {(status === "listening" || status === "paused") && (
              <span ref={dotRef} className={`dict-dot${status === "paused" ? " paused" : ""}`} />
            )}
            {STATUS_LABEL[status]}
            {recording && <span className="dict-timer">{fmtTime(elapsed)}</span>}
          </span>
        </div>

        <p className="name-sub dict-sub">
          {status === "done"
            ? isEmail
              ? "Fraise picked this up as an email — check the recipient before sending."
              : "Read it over — you can edit before copying."
            : error && status !== "error"
              ? error
              : note || "Speak naturally — pauses become punctuation, and it's polished automatically when you finish."}
        </p>

        {status === "error" ? (
          <div className="dict-error">{error || "Something went wrong."}</div>
        ) : status === "done" && isEmail && email ? (
          <div className="dict-email-form">
            <label className="dict-field">
              <span>To</span>
              <input
                type="email"
                value={email.to}
                placeholder="name@example.com"
                onChange={(e) => setEmail({ ...email, to: e.target.value })}
              />
            </label>
            <label className="dict-field">
              <span>Subject</span>
              <input
                type="text"
                value={email.subject}
                onChange={(e) => setEmail({ ...email, subject: e.target.value })}
              />
            </label>
            <label className="dict-field grow">
              <span>Message</span>
              <textarea
                className="dict-text"
                value={email.body}
                onChange={(e) => setEmail({ ...email, body: e.target.value })}
              />
            </label>
          </div>
        ) : status === "done" ? (
          <>
            <div className="dict-tabs" role="tablist">
              <button
                role="tab"
                aria-selected={view === "polished"}
                className={view === "polished" ? "on" : ""}
                onClick={() => setView("polished")}
                disabled={!polished}
              >
                Polished
              </button>
              <button
                role="tab"
                aria-selected={view === "raw"}
                className={view === "raw" ? "on" : ""}
                onClick={() => setView("raw")}
              >
                As spoken
              </button>
            </div>
            <textarea
              className="editor-textarea dict-text"
              value={draft}
              onChange={(e) => setDraft(e.target.value)}
              placeholder="Nothing was heard."
            />
          </>
        ) : (
          <div ref={feedRef} className="dict-feed" aria-live="polite">
            {liveText || interim ? (
              <p>
                {liveText}
                {interim && (
                  <span className="dict-interim">{liveText ? " " : ""}{interim}</span>
                )}
                {recording && <span className="caret" />}
              </p>
            ) : (
              <p className="dict-placeholder">
                {busy ? "One moment…" : "Start talking — your words will appear here."}
              </p>
            )}
          </div>
        )}

        <div className="dict-actions">
          {recording && (
            <>
              <button className="dict-btn" onClick={status === "paused" ? resume : pause}>
                {status === "paused" ? "Resume" : "Pause"}
              </button>
              <button className="dict-btn primary" onClick={stop} disabled={!liveText && status !== "paused"}>
                Finish
              </button>
            </>
          )}
          {(status === "finishing" || status === "polishing") && (
            <button className="dict-btn primary" disabled>{STATUS_LABEL[status]}</button>
          )}
          {status === "done" && isEmail && email && (
            <>
              <button
                className="dict-btn primary"
                onClick={send}
                disabled={!EMAIL_RE.test(email.to) || sendState === "sending" || sendState === "sent"}
              >
                {sendState === "sending" ? "Sending…" : sendState === "sent" ? "Sent ✓" : "Send"}
              </button>
              {sendState === "error" && <span className="dict-send-error">{sendError}</span>}
            </>
          )}
          {status === "done" && !isEmail && (
            <>
              <button className="dict-btn primary" onClick={copy} disabled={!finalText}>
                {copied ? "Copied ✓" : "Copy text"}
              </button>
              <button
                className="dict-btn"
                onClick={() => finalText && onPlanDay(finalText)}
                disabled={!finalText}
                title="Split this into tasks and let the agents handle it"
              >
                Turn into tasks
              </button>
            </>
          )}
          {status === "done" && !openId && (
            <button className="dict-btn" onClick={() => void start()}>
              Dictate again
            </button>
          )}
          {status === "error" && (
            <button className="dict-btn primary" onClick={() => void start()}>
              Try again
            </button>
          )}
          {status === "done" && !isEmail && (
            <span className="dict-count">{words ? `${words} word${words === 1 ? "" : "s"}` : ""}</span>
          )}
          <button className="dict-btn ghost" onClick={close}>
            {status === "done" ? "Close" : "Cancel"}
          </button>
        </div>
      </div>
    </div>
  );
}
