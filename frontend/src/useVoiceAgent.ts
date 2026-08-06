import { useCallback, useEffect, useRef, useState } from "react";
import { getActiveAssistant, getActiveId, listAssistants } from "./assistants";

export type Role = "user" | "agent" | "system";
export interface Message {
  id: string;
  role: Role;
  text: string;
}

type Status = "connecting" | "online" | "error";
export type OrbState = "idle" | "listening" | "thinking" | "speaking";

/** One thing a paused tool call is waiting to be told. */
export interface ElicitField {
  name: string;
  type: "string" | "number" | "integer" | "boolean" | "enum";
  label: string;
  description: string;
  choices: string[] | null;
  required: boolean;
  default: string | number | boolean | null;
}

export interface Elicitation {
  id: string;
  server: string;
  tool: string;
  message: string;
  fields: ElicitField[];
  sent: boolean;
}

const PROD_WS_URL = "wss://100-56-229-165.sslip.io/ws";
const isLocalHost = ["localhost", "127.0.0.1"].includes(location.hostname);
const WS_URL =
  import.meta.env.VITE_BACKEND_WS_URL ??
  (isLocalHost
    ? `${location.protocol === "https:" ? "wss" : "ws"}://${location.host}/ws`
    : PROD_WS_URL);
function sessionId(): string {
  return getActiveId();
}

function greetedKey(id: string): string {
  return `fraise-greeted-${id}`;
}

function wsUrlWithSession(): string {
  const active = getActiveAssistant();
  const url = new URL(WS_URL, location.href);
  url.searchParams.set("sid", active.id);
  const name = localStorage.getItem("fraise-name");
  if (name) url.searchParams.set("name", name);
  if (active.name && active.name.toLowerCase() !== "fraise") {
    url.searchParams.set("persona", active.name);
  }
  if (active.instructions.trim()) {
    url.searchParams.set("instructions", active.instructions.trim());
  }
  if (active.voice) {
    url.searchParams.set("voice", active.voice);
  }
  const others = listAssistants()
    .filter((a) => a.id !== active.id)
    .map((a) => a.name);
  if (others.length) url.searchParams.set("personas", others.join(","));
  if (sessionStorage.getItem(greetedKey(active.id))) {
    url.searchParams.set("greet", "0");
  }
  return url.toString();
}

export function httpBase(): string {
  const url = new URL(WS_URL, location.href);
  url.protocol = url.protocol === "wss:" ? "https:" : "http:";
  return url.origin;
}

export type UploadKind = "document" | "recording";

export async function uploadDocument(
  file: File,
): Promise<{ filename: string; chunks: number; kind: UploadKind }> {
  const url = new URL("/upload", httpBase());
  url.searchParams.set("sid", sessionId());
  const body = new FormData();
  body.append("file", file);
  const res = await fetch(url, { method: "POST", body });
  if (!res.ok) {
    const detail = await res.json().catch(() => null);
    throw new Error(detail?.detail ?? `upload failed (${res.status})`);
  }
  return res.json();
}

const INPUT_RATE = 16_000;
const OUTPUT_RATE = 24_000;
const PLAYBACK_LEAD = 0.18;

export function useVoiceAgent(onRequestSwitch?: (name: string) => void) {
  const [messages, setMessages] = useState<Message[]>([]);
  const [status, setStatus] = useState<Status>("online");
  const [active, setActive] = useState(false);
  const [listening, setListening] = useState(false);
  const [thinking, setThinking] = useState(false);
  const [speaking, setSpeaking] = useState(false);
  const [authNeeded, setAuthNeeded] = useState<string | null>(null);
  const [elicitation, setElicitation] = useState<Elicitation | null>(null);

  const levelRef = useRef(0);
  const outLevelRef = useRef(0);

  const wsRef = useRef<WebSocket | null>(null);
  const streamRef = useRef<MediaStream | null>(null);
  const micCtxRef = useRef<AudioContext | null>(null);
  const playCtxRef = useRef<AudioContext | null>(null);
  const workletRef = useRef<AudioWorkletNode | null>(null);
  const playHeadRef = useRef(0);
  const sourcesRef = useRef<AudioBufferSourceNode[]>([]);
  const thinkingTimeoutRef = useRef<number | null>(null);
  const mutedRef = useRef(false);

  const switchRef = useRef(onRequestSwitch);
  switchRef.current = onRequestSwitch;

  const push = useCallback((role: Role, text: string) => {
    if (!text?.trim()) return;
    setMessages((m) => [...m, { id: crypto.randomUUID(), role, text }]);
  }, []);

  const stopPlayback = useCallback(() => {
    for (const s of sourcesRef.current) {
      try {
        s.stop();
      } catch {
      }
    }
    sourcesRef.current = [];
    outLevelRef.current = 0;
    if (playCtxRef.current) playHeadRef.current = playCtxRef.current.currentTime;
  }, []);

  const playChunk = useCallback((buf: ArrayBuffer) => {
    const ctx = playCtxRef.current;
    if (!ctx) return;
    const int16 = new Int16Array(buf);
    const f32 = new Float32Array(int16.length);
    let sum = 0;
    for (let i = 0; i < int16.length; i++) {
      const x = int16[i] / 0x8000;
      f32[i] = x;
      sum += x * x;
    }
    const rms = Math.sqrt(sum / (int16.length || 1));
    outLevelRef.current += (Math.min(1, rms * 3.2) - outLevelRef.current) * 0.5;

    const audio = ctx.createBuffer(1, f32.length, OUTPUT_RATE);
    audio.copyToChannel(f32, 0);
    const src = ctx.createBufferSource();
    src.buffer = audio;
    src.connect(ctx.destination);

    const now = ctx.currentTime;
    if (playHeadRef.current < now + 0.02) playHeadRef.current = now + PLAYBACK_LEAD;
    src.start(playHeadRef.current);
    playHeadRef.current += audio.duration;
    sourcesRef.current.push(src);
    src.onended = () => {
      sourcesRef.current = sourcesRef.current.filter((s) => s !== src);
    };
  }, []);

  const clearThinkingWatchdog = useCallback(() => {
    if (thinkingTimeoutRef.current !== null) {
      window.clearTimeout(thinkingTimeoutRef.current);
      thinkingTimeoutRef.current = null;
    }
  }, []);

  const armThinkingWatchdog = useCallback(() => {
    clearThinkingWatchdog();
    thinkingTimeoutRef.current = window.setTimeout(() => {
      thinkingTimeoutRef.current = null;
      setThinking(false);
      push("system", "That took too long — try again?");
    }, 20_000);
  }, [clearThinkingWatchdog, push]);

  const handleEvent = useCallback(
    (data: any) => {
      switch (data.type) {
        case "Welcome":
        case "SettingsApplied":
          setStatus("online");
          break;
        case "UserStartedSpeaking":
          clearThinkingWatchdog();
          mutedRef.current = true;
          stopPlayback();
          setSpeaking(false);
          setThinking(false);
          setListening(true);
          break;
        case "ConversationText":
          if (data.role === "user") {
            push("user", data.content);
          } else if (data.role === "assistant") {
            push("agent", data.content);
            clearThinkingWatchdog();
            mutedRef.current = false;
            setSpeaking(true);
            setThinking(false);
            setListening(false);
          }
          break;
        case "AgentThinking":
        case "FunctionCallRequest":
          armThinkingWatchdog();
          setThinking(true);
          setListening(false);
          break;
        case "AgentStartedSpeaking":
          clearThinkingWatchdog();
          mutedRef.current = false;
          setSpeaking(true);
          setThinking(false);
          setListening(false);
          break;
        case "AgentAudioDone":
          setSpeaking(false);
          outLevelRef.current = 0;
          break;
        case "auth_redirect":
          setAuthNeeded(data.url as string);
          break;
        case "elicitation":
          setElicitation({
            id: data.id,
            server: data.server ?? "",
            tool: data.tool ?? "",
            message: data.message ?? "",
            fields: (data.fields ?? []) as ElicitField[],
            sent: false,
          });
          break;
        case "elicitation_resolved":
          setElicitation((e) => (e && e.id !== data.id ? e : null));
          break;
        case "switch_assistant":
          if (typeof data.name === "string") switchRef.current?.(data.name);
          break;
        case "Error":
        case "error":
          clearThinkingWatchdog();
          setThinking(false);
          setSpeaking(false);
          setListening(false);
          push("system", data.message || data.description || "Agent error");
          break;
      }
    },
    [push, stopPlayback, armThinkingWatchdog, clearThinkingWatchdog],
  );

  const stop = useCallback(() => {
    clearThinkingWatchdog();
    wsRef.current?.close();
    wsRef.current = null;
    workletRef.current?.disconnect();
    workletRef.current = null;
    streamRef.current?.getTracks().forEach((t) => t.stop());
    streamRef.current = null;
    micCtxRef.current?.close().catch(() => {});
    micCtxRef.current = null;
    stopPlayback();
    playCtxRef.current?.close().catch(() => {});
    playCtxRef.current = null;
    levelRef.current = 0;
    mutedRef.current = false;
    setActive(false);
    setListening(false);
    setThinking(false);
    setSpeaking(false);
    setElicitation(null);
  }, [stopPlayback, clearThinkingWatchdog]);

  const start = useCallback(async () => {
    try {
      setStatus("connecting");

      const playCtx = new AudioContext({ sampleRate: OUTPUT_RATE });
      await playCtx.resume();
      playCtxRef.current = playCtx;
      playHeadRef.current = playCtx.currentTime;

      const ws = new WebSocket(wsUrlWithSession());
      ws.binaryType = "arraybuffer";
      wsRef.current = ws;
      ws.onopen = () => {
        setStatus("online");
        sessionStorage.setItem(greetedKey(getActiveId()), "1");
      };
      ws.onerror = () => setStatus("error");
      ws.onclose = () => {
        if (wsRef.current === ws) stop();
      };
      ws.onmessage = (ev) => {
        if (typeof ev.data === "string") {
          try {
            handleEvent(JSON.parse(ev.data));
          } catch {
          }
        } else if (!mutedRef.current) {
          playChunk(ev.data as ArrayBuffer);
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

      const micCtx = new AudioContext({ sampleRate: INPUT_RATE });
      await micCtx.resume();
      micCtxRef.current = micCtx;
      await micCtx.audioWorklet.addModule("/pcm-worklet.js");

      const source = micCtx.createMediaStreamSource(stream);
      const worklet = new AudioWorkletNode(micCtx, "pcm-processor");
      workletRef.current = worklet;
      const sink = micCtx.createGain();
      sink.gain.value = 0;
      source.connect(worklet);
      worklet.connect(sink);
      sink.connect(micCtx.destination);

      worklet.port.onmessage = (e) => {
        const pcm = e.data as Int16Array;
        let sum = 0;
        for (let i = 0; i < pcm.length; i++) {
          const x = pcm[i] / 0x8000;
          sum += x * x;
        }
        const rms = Math.sqrt(sum / pcm.length);
        levelRef.current += (Math.min(1, rms * 3.2) - levelRef.current) * 0.4;
        if (ws.readyState === WebSocket.OPEN) ws.send(pcm.buffer as ArrayBuffer);
      };

      setActive(true);
    } catch {
      setStatus("error");
      stop();
    }
  }, [handleEvent, playChunk, stop]);

  const toggle = useCallback(() => {
    if (active) stop();
    else void start();
  }, [active, start, stop]);

  const reconnect = useCallback(() => {
    setMessages([]);
    stop();
    setTimeout(() => void start(), 120);
  }, [stop, start]);

  const notifyUpload = useCallback((filename: string, kind: UploadKind) => {
    const ws = wsRef.current;
    if (ws?.readyState === WebSocket.OPEN) {
      ws.send(JSON.stringify({ type: "document_uploaded", filename, kind }));
    }
  }, []);

  // Answering on screen sends exact values — an address or a choice that speech
  // could garble goes back to the paused tool character for character.
  const answerElicitation = useCallback(
    (id: string, content: Record<string, unknown> | null) => {
      const ws = wsRef.current;
      if (ws?.readyState !== WebSocket.OPEN) return;
      ws.send(
        JSON.stringify({
          type: "elicitation_response",
          id,
          action: content ? "accept" : "decline",
          content,
        }),
      );
      setElicitation((e) => (e && e.id === id ? { ...e, sent: true } : e));
    },
    [],
  );

  useEffect(() => () => stop(), [stop]);

  const orbState: OrbState = speaking
    ? "speaking"
    : thinking
      ? "thinking"
      : listening
        ? "listening"
        : "idle";

  const speechSupported =
    typeof navigator !== "undefined" &&
    !!navigator.mediaDevices?.getUserMedia &&
    (typeof AudioContext !== "undefined" || "webkitAudioContext" in window);

  return {
    messages,
    status,
    listening: active,
    orbState,
    levelRef,
    outLevelRef,
    speechSupported,
    toggle,
    reconnect,
    notifyUpload,
    authNeeded,
    clearAuth: () => setAuthNeeded(null),
    elicitation,
    answerElicitation,
  };
}
