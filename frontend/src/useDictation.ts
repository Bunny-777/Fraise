import { useCallback, useEffect, useRef, useState } from "react";
import { httpBase } from "./useVoiceAgent";

export type DictationStatus =
  | "idle"
  | "connecting"
  | "loading"
  | "listening"
  | "paused"
  | "finishing"
  | "polishing"
  | "done"
  | "error";

export interface DictationEmail {
  to: string;
  subject: string;
  body: string;
}

const INPUT_RATE = 16_000;

function dictationWsUrl(sid: string): string {
  const url = new URL("/ws/dictation", httpBase());
  url.protocol = url.protocol === "https:" ? "wss:" : "ws:";
  url.searchParams.set("sid", sid);
  return url.toString();
}

export function useDictation(sid: string) {
  const [status, setStatus] = useState<DictationStatus>("idle");
  const [segments, setSegments] = useState<string[]>([]);
  const [interim, setInterim] = useState("");
  const [id, setId] = useState<string | null>(null);
  const [polished, setPolished] = useState("");
  const [isEmail, setIsEmail] = useState(false);
  const [email, setEmail] = useState<DictationEmail | null>(null);
  const [note, setNote] = useState("");
  const [error, setError] = useState("");
  const [elapsed, setElapsed] = useState(0);
  const levelRef = useRef(0);

  const wsRef = useRef<WebSocket | null>(null);
  const streamRef = useRef<MediaStream | null>(null);
  const ctxRef = useRef<AudioContext | null>(null);
  const workletRef = useRef<AudioWorkletNode | null>(null);
  const pausedRef = useRef(false);
  const startingRef = useRef(false);
  const startedAtRef = useRef(0);
  const pausedForRef = useRef(0);
  const pausedAtRef = useRef(0);

  const statusRef = useRef(status);
  statusRef.current = status;

  const teardownAudio = useCallback(() => {
    workletRef.current?.disconnect();
    workletRef.current = null;
    streamRef.current?.getTracks().forEach((t) => t.stop());
    streamRef.current = null;
    ctxRef.current?.close().catch(() => {});
    ctxRef.current = null;
    levelRef.current = 0;
  }, []);

  const teardown = useCallback(() => {
    teardownAudio();
    const ws = wsRef.current;
    wsRef.current = null;
    ws?.close();
    pausedRef.current = false;
    startingRef.current = false;
  }, [teardownAudio]);

  useEffect(() => {
    if (status !== "listening" && status !== "paused") return;
    const id = window.setInterval(() => {
      const pausedFor =
        pausedForRef.current + (pausedRef.current ? Date.now() - pausedAtRef.current : 0);
      setElapsed(Math.floor((Date.now() - startedAtRef.current - pausedFor) / 1000));
    }, 500);
    return () => window.clearInterval(id);
  }, [status]);

  const resetState = useCallback(() => {
    setSegments([]);
    setInterim("");
    setId(null);
    setPolished("");
    setIsEmail(false);
    setEmail(null);
    setError("");
    setNote("");
    setElapsed(0);
  }, []);

  const start = useCallback(async () => {
    if (startingRef.current) return;
    if (statusRef.current !== "idle" && statusRef.current !== "done" && statusRef.current !== "error") return;
    startingRef.current = true;
    resetState();
    setStatus("connecting");
    pausedRef.current = false;
    pausedForRef.current = 0;

    try {
      const ws = new WebSocket(dictationWsUrl(sid));
      ws.binaryType = "arraybuffer";
      wsRef.current = ws;

      ws.onmessage = (ev) => {
        if (typeof ev.data !== "string") return;
        let data: any;
        try {
          data = JSON.parse(ev.data);
        } catch {
          return;
        }
        switch (data.type) {
          case "info":
            setNote(data.message ?? "");
            setStatus((s) => (s === "connecting" ? "loading" : s));
            break;
          case "ready":
            setNote("");
            setStatus((s) => (s === "connecting" || s === "loading" ? "listening" : s));
            startedAtRef.current = Date.now();
            break;
          case "segment":
            setSegments((prev) => [...prev, data.text as string]);
            setInterim("");
            break;
          case "interim":
            setInterim((data.text as string) ?? "");
            break;
          case "polishing":
            setStatus("polishing");
            break;
          case "final": {
            const text = (data.polished as string) || (data.text as string) || "";
            if (text) setSegments([text]);
            setInterim("");
            setId((data.id as string) ?? null);
            setPolished((data.polished as string) ?? (data.text as string) ?? "");
            setIsEmail(Boolean(data.is_email));
            setEmail((data.email as DictationEmail) ?? null);
            setStatus("done");
            teardown();
            break;
          }
          case "error":
            setError(data.message ?? "Dictation failed.");
            if (data.fatal) {
              setStatus("error");
              teardown();
            }
            break;
        }
      };
      ws.onerror = () => {
        if (wsRef.current === ws) {
          setError("Couldn't reach the transcription backend.");
          setStatus("error");
          teardown();
        }
      };
      ws.onclose = () => {
        if (wsRef.current === ws && statusRef.current !== "done") {
          setError((e) => e || "Connection closed.");
          setStatus((s) => (s === "done" ? s : "error"));
          teardown();
        }
      };

      const stream = await navigator.mediaDevices.getUserMedia({
        audio: {
          channelCount: 1,
          echoCancellation: true,
          noiseSuppression: true,
          autoGainControl: true,
        },
      });
      streamRef.current = stream;

      const ctx = new AudioContext({ sampleRate: INPUT_RATE });
      await ctx.resume();
      ctxRef.current = ctx;
      await ctx.audioWorklet.addModule("/pcm-worklet.js");

      const source = ctx.createMediaStreamSource(stream);
      const worklet = new AudioWorkletNode(ctx, "pcm-processor");
      workletRef.current = worklet;
      const sink = ctx.createGain();
      sink.gain.value = 0;
      source.connect(worklet);
      worklet.connect(sink);
      sink.connect(ctx.destination);

      worklet.port.onmessage = (e) => {
        const pcm = e.data as Int16Array;
        let sum = 0;
        for (let i = 0; i < pcm.length; i++) {
          const x = pcm[i] / 0x8000;
          sum += x * x;
        }
        const rms = Math.sqrt(sum / pcm.length);
        levelRef.current += (Math.min(1, rms * 3.2) - levelRef.current) * 0.4;
        if (!pausedRef.current && ws.readyState === WebSocket.OPEN) {
          ws.send(pcm.buffer as ArrayBuffer);
        }
      };
    } catch {
      setError("Microphone access failed.");
      setStatus("error");
      teardown();
    }
  }, [sid, teardown, resetState]);

  const pause = useCallback(() => {
    if (statusRef.current !== "listening") return;
    pausedRef.current = true;
    pausedAtRef.current = Date.now();
    setStatus("paused");
  }, []);

  const resume = useCallback(() => {
    if (statusRef.current !== "paused") return;
    pausedRef.current = false;
    pausedForRef.current += Date.now() - pausedAtRef.current;
    setStatus("listening");
  }, []);

  const stop = useCallback(() => {
    const ws = wsRef.current;
    if (!ws || (statusRef.current !== "listening" && statusRef.current !== "paused")) return;
    teardownAudio();
    setStatus("finishing");
    if (ws.readyState === WebSocket.OPEN) {
      ws.send(JSON.stringify({ type: "stop" }));
    } else {
      setStatus("error");
      setError("Connection closed.");
      teardown();
    }
  }, [teardown, teardownAudio]);

  const open = useCallback(
    async (dictationId: string): Promise<void> => {
      teardown();
      resetState();
      setStatus("polishing");
      try {
        const url = new URL(`/dictations/${dictationId}`, httpBase());
        url.searchParams.set("sid", sid);
        const res = await fetch(url);
        if (!res.ok) throw new Error(`fetch failed (${res.status})`);
        const d = await res.json();
        setId(d.id);
        setSegments([d.polished_text || d.raw_text || ""]);
        setPolished(d.polished_text || d.raw_text || "");
        setIsEmail(Boolean(d.is_email));
        setEmail(
          d.is_email
            ? { to: d.email_to || "", subject: d.email_subject || "", body: d.email_body || "" }
            : null,
        );
        setStatus("done");
      } catch {
        setError("Couldn't load that dictation.");
        setStatus("error");
      }
    },
    [sid, teardown, resetState],
  );

  const cancel = useCallback(() => {
    teardown();
    resetState();
    setStatus("idle");
  }, [teardown, resetState]);

  useEffect(() => () => teardown(), [teardown]);

  return {
    status, segments, interim, id, polished, isEmail, email, note, error, elapsed, levelRef,
    start, pause, resume, stop, open, cancel,
    setEmail,
  };
}
