import { useCallback, useEffect, useRef, useState } from "react";
import { getToken, post } from "../lib/api";
import { StreamPlayer } from "../lib/streamPlayer";

interface MediaState {
  context: AudioContext;
  mic: MediaStream;
  source: MediaStreamAudioSourceNode;
  worklet: AudioWorkletNode;
  sink: GainNode;
  output: GainNode;
  socket: WebSocket | null;
  heartbeat?: number;
  reconnect?: number;
}

export function useVoiceCall(sessionId: string, refresh: () => Promise<void>) {
  const [connecting, setConnecting] = useState(false);
  const [connected, setConnected] = useState(false);
  const [muted, setMuted] = useState(false);
  const [partial, setPartial] = useState("");
  const [error, setError] = useState("");
  const [level, setLevel] = useState(0);
  const [playbackLevel, setPlaybackLevel] = useState(0);
  const outputPlayer = useRef<StreamPlayer | null>(null);
  const attempt = useRef(0);
  const [inputStatus, setInputStatus] = useState("idle");
  const [inputWarning, setInputWarning] = useState("");
  const input = useRef({ turnId: "", revision: 0, sample: -1 });
  const media = useRef<MediaState | null>(null);
  const openingContext = useRef<AudioContext | null>(null);
  const callId = useRef("");
  const epoch = useRef(0);
  const muteRef = useRef(false);
  const stopping = useRef(false);
  const currentResponse = useRef("");
  const duck = useRef<{ candidate: string; response: string; epoch: number; timer: number } | null>(null);
  const withdrawnResponses = useRef(new Set<string>());

  const restoreVolume = useCallback(() => {
    if (duck.current) window.clearTimeout(duck.current.timer);
    duck.current = null;
    const m = media.current;
    if (m) m.output.gain.setTargetAtTime(1, m.context.currentTime, .015);
  }, []);

  const silence = useCallback(() => {
    restoreVolume();
    outputPlayer.current?.silence();
    setPlaybackLevel(0);
  }, [restoreVolume]);

  const cleanup = useCallback(() => {
    connectedRef.current = false;
    const m = media.current;
    media.current = null;
    const opening = openingContext.current;
    openingContext.current = null;
    if (opening && opening !== m?.context && opening.state !== "closed") void opening.close();
    silence();
    outputPlayer.current = null;
    if (m) {
      window.clearInterval(m.heartbeat);
      window.clearTimeout(m.reconnect);
      m.socket?.close();
      m.worklet.disconnect();
      m.source.disconnect();
      m.output.disconnect();
      m.mic.getTracks().forEach((track) => track.stop());
      void m.context.close();
    }
    setConnected(false);
    setConnecting(false);
    setLevel(0);
    setPartial("");
    setInputStatus("idle");
  }, [silence]);

  const hangup = useCallback(async () => {
    stopping.current = true;
    attempt.current++;
    const id = callId.current;
    callId.current = "";
    cleanup();
    if (id) {
      try { await post(`/sessions/${sessionId}/calls/${id}/hangup`); await refresh(); }
      catch (e) { setError((e as Error).message); }
    }
  }, [sessionId, cleanup, refresh]);

  useEffect(() => () => {
    stopping.current = true;
    attempt.current++;
    const id = callId.current;
    callId.current = "";
    cleanup();
    if (id) void post(`/sessions/${sessionId}/calls/${id}/hangup`).catch(() => {});
  }, [sessionId, cleanup]);

  async function start() {
    if (connecting || media.current) return;
    const ownAttempt = ++attempt.current;
    setConnecting(true);
    setError("");
    setInputWarning("");
    input.current = { turnId: "", revision: 0, sample: -1 };
    stopping.current = false;
    let pendingContext: AudioContext | undefined;
    let pendingMic: MediaStream | undefined;
    try {
      // The user gesture opens both microphone and audio output before async API work.
      const context = new AudioContext();
      pendingContext = context;
      openingContext.current = context;
      await context.resume();
      if (ownAttempt !== attempt.current || stopping.current) {
        if (context.state !== "closed") void context.close();
        return;
      }
      const mic = await navigator.mediaDevices.getUserMedia({ audio: {
        channelCount: 1, echoCancellation: true, noiseSuppression: true, autoGainControl: true,
      } });
      pendingMic = mic;
      if (ownAttempt !== attempt.current || stopping.current) {
        mic.getTracks().forEach((track) => track.stop());
        if (context.state !== "closed") void context.close();
        return;
      }
      await context.audioWorklet.addModule("/voice-worklet.js");
      if (ownAttempt !== attempt.current || stopping.current) {
        mic.getTracks().forEach((track) => track.stop());
        if (context.state !== "closed") void context.close();
        return;
      }
      const source = context.createMediaStreamSource(mic);
      const worklet = new AudioWorkletNode(context, "duet-capture", { processorOptions: { protocol: 3 } });
      const sink = context.createGain();
      sink.gain.value = 0;
      source.connect(worklet).connect(sink).connect(context.destination);
      const output = context.createGain();
      output.connect(context.destination);
      const m: MediaState = { context, mic, source, worklet, sink, output, socket: null };
      media.current = m;
      openingContext.current = null;
      const result = await post<{ call_id: string; turn_protocol: number }>(`/sessions/${sessionId}/calls`, { turn_protocol: 3, output_protocol: 4 });
      if (ownAttempt !== attempt.current || stopping.current || media.current !== m) {
        void post(`/sessions/${sessionId}/calls/${result.call_id}/hangup`).catch(() => {});
        if (media.current === m) cleanup();
        return;
      }
      callId.current = result.call_id;
      epoch.current = 0;
      withdrawnResponses.current.clear();
      muteRef.current = false;
      setMuted(false);
      const player = new StreamPlayer(context, output, (data) => {
        if (m.socket?.readyState === WebSocket.OPEN) m.socket.send(JSON.stringify(data));
      }, setPlaybackLevel, (response, generation) => {
        if (currentResponse.current !== response) restoreVolume();
        currentResponse.current = response; epoch.current = generation;
      }, setError);
      outputPlayer.current = player;
      let lastMeter = 0;
      let reconnects = 0;
      worklet.port.onmessage = ({ data }: MessageEvent<{ pcm: ArrayBuffer; level: number }>) => {
        const socket = m.socket;
        if (socket?.readyState !== WebSocket.OPEN || muteRef.current || !connectedRef.current) return;
        if (performance.now() - lastMeter > 100) {
          setLevel(data.level); lastMeter = performance.now();
        }
        // RMS only drives the meter. Server VAD confirms interruptions and turn boundaries.
        if (socket.bufferedAmount > 32000) {
          setError("网络发送较慢，请稍候再说。");
          return;
        }
        socket.send(data.pcm);
      };
      const connect = () => {
        if (stopping.current || media.current !== m) return;
        const socket = new WebSocket(`${location.protocol === "https:" ? "wss:" : "ws:"}//${location.host}/api/v1/ws/sessions/${sessionId}/calls/${result.call_id}`);
        socket.binaryType = "arraybuffer";
        m.socket = socket;
        socket.onopen = () => socket.send(JSON.stringify({ token: getToken() }));
        socket.onmessage = ({ data }) => {
          if (data instanceof ArrayBuffer) { player.frame(data); return; }
          const event = JSON.parse(data);
          if (player.event(event)) {
            if (event.type === "response.withdrawn" || event.type === "retracted") void refresh();
            return;
          }
          if (event.type === "ready") {
            reconnects = 0;
            epoch.current = event.epoch;
            connectedRef.current = true;
            setConnected(true); setConnecting(false); setError("");
            const settings = mic.getAudioTracks()[0]?.getSettings() || {};
            socket.send(JSON.stringify({ type: "media.settings", contextRate: context.sampleRate,
              sampleRate: settings.sampleRate, channelCount: settings.channelCount,
              echoCancellation: settings.echoCancellation, noiseSuppression: settings.noiseSuppression,
              autoGainControl: settings.autoGainControl }));
            if (event.endpoint_mode === "fixed_silence") setInputWarning("自然判停暂不可用，已切换为约三秒静音交接。");
            void refresh();
          } else if (event.type === "playback.duck") {
            if (event.epoch === epoch.current && event.response_id === currentResponse.current
              && !withdrawnResponses.current.has(event.response_id)) {
              restoreVolume();
              output.gain.setTargetAtTime(Math.max(.1, Math.min(1, event.gain)), context.currentTime, .015);
              const candidate = event.candidate_id;
              const timer = window.setTimeout(() => {
                if (duck.current?.candidate === candidate) restoreVolume();
              }, Math.min(500, Math.max(1, event.expires_ms || 500)));
              duck.current = { candidate, response: event.response_id, epoch: event.epoch, timer };
            }
          } else if (event.type === "playback.restore") {
            if (duck.current && duck.current.candidate === event.candidate_id && duck.current.response === event.response_id
              && event.epoch === epoch.current && currentResponse.current === event.response_id) restoreVolume();
          } else if (event.type === "stop") {
            epoch.current = event.epoch;
            player.silence(event.epoch);
            silence();
          } else if (event.type === "interruption.confirmed") {
            if (event.next_output_epoch >= epoch.current) {
              epoch.current = event.next_output_epoch;
              player.silence(event.next_output_epoch);
              silence();
            }
          } else if (event.type === "input.committed") {
            if (input.current.turnId === event.turn_id && input.current.revision === event.revision) {
              setPartial(""); setInputStatus("idle");
            }
            void refresh();
          } else if (event.type.startsWith("input.")) {
            if (event.call_id !== callId.current) return;
            if ((event.sample ?? 0) < input.current.sample) return;
            if (event.turn_id === input.current.turnId && event.revision < input.current.revision) return;
            if (event.turn_id !== input.current.turnId) setPartial("");
            input.current = { turnId: event.turn_id, revision: event.revision, sample: event.sample ?? 0 };
            if (event.type === "input.speech_started") {
              setInputStatus("collecting");
            } else if (event.type === "input.transcript") {
              setPartial(event.text);
            } else if (event.type === "input.waiting_pause") {
              setInputStatus("waiting_pause");
            } else if (event.type === "input.thinking" || event.type === "input.refining") {
              setInputStatus("refining");
            } else if (event.type === "input.degraded" || event.type === "input.limit_reached") {
              setInputWarning(event.message || "转写未完整完成，请确认原话。");
              if (event.type === "input.limit_reached") setInputStatus("input_limit");
            } else if (event.type === "input.recovered") {
              setInputWarning("");
            } else if (event.type === "input.cancelled") {
              setPartial(""); setInputStatus("idle");
              if (event.reason_code === "audio_gap") setInputWarning("音频有缺失，这段话尚未提交；停顿后请重说。");
            }
          } else if (event.type === "thinking.timeout" || event.type === "thinking.failed") {
            setInputWarning("本次处理未完成，可以继续说话或重试。");
            void refresh();
          } else if (event.type === "questions.routed") {
            void refresh();
          } else if (event.type === "transcript") {
            setPartial(event.final ? "" : event.text);
          } else if (event.type === "error") {
            setError(event.message);
          } else if (event.type === "ended") {
            stopping.current = true; callId.current = ""; cleanup(); void refresh();
          }
        };
        socket.onclose = () => {
          if (m.socket !== socket) return;
          connectedRef.current = false;
          setConnected(false); silence();
          if (!stopping.current && media.current === m) {
            if (++reconnects > 5) {
              void hangup(); setError("无法重新接通，请重新拨打。"); return;
            }
            setError("连接中断，正在重新接通…");
            m.reconnect = window.setTimeout(connect, 1500);
          }
        };
      };
      connect();
      m.heartbeat = window.setInterval(() => {
        if (m.socket?.readyState === WebSocket.OPEN) m.socket.send(JSON.stringify({ type: "ping" }));
      }, 3000);
    } catch (e) {
      if (ownAttempt !== attempt.current) {
        pendingMic?.getTracks().forEach((track) => track.stop());
        if (pendingContext?.state !== "closed") void pendingContext?.close();
        return;
      }
      const id = callId.current;
      stopping.current = true; callId.current = "";
      cleanup();
      pendingMic?.getTracks().forEach((track) => track.stop());
      if (pendingContext?.state !== "closed") void pendingContext?.close();
      if (id) void post(`/sessions/${sessionId}/calls/${id}/hangup`).catch(() => {});
      setError((e as Error).message || "无法开始通话，请检查麦克风权限。");
    }
  }
  const connectedRef = useRef(false);

  function toggleMute() {
    muteRef.current = !muteRef.current;
    setMuted(muteRef.current);
    if (muteRef.current) setLevel(0);
    media.current?.mic.getAudioTracks().forEach((track) => { track.enabled = !muteRef.current; });
    media.current?.socket?.send(JSON.stringify({ type: "mute", muted: muteRef.current }));
  }
  function sendText(text: string) {
    if (media.current?.socket?.readyState !== WebSocket.OPEN) return;
    media.current.socket.send(JSON.stringify({ type: "text", text }));
  }
  return { start, hangup, toggleMute, sendText, connecting, connected, muted, partial, error, level, playbackLevel, inputStatus, inputWarning };
}
