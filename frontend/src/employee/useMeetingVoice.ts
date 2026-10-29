import { useEffect, useRef, useState } from 'react';
import { request } from './api';
import type { RecordItem } from './api';

type VoiceState = 'idle' | 'connecting' | 'listening' | 'transcribing' | 'finalizing';
export type SpeechAcceptance = { run_id: string; record_id: string; status?: string; input_record?: RecordItem; assistant_record?: RecordItem; acknowledgement?: { locale: string; text: string; url: string } | null };
type SpeechOptions = {
  endpoint: string; enabled: boolean; dictation?: boolean; scope?: string;
  onFinal: (text: string, acceptance?: SpeechAcceptance) => void; onPartial?: (text: string) => void;
  onError: (text: string) => void; onSpeech?: () => void;
  replyPlaying?: () => boolean;
};

/** Sole owner of media for calls and editable streaming dictation. */
export function useSpeechStream(options: SpeechOptions) {
  const [state, setState] = useState<VoiceState>('idle');
  const [level, setLevel] = useState(0), [muted, setMuted] = useState(false);
  const [startedAt, setStartedAt] = useState(0), [caption, setCaption] = useState('');
  const [warning, setWarning] = useState(''), [lastRunId, setLastRunId] = useState('');
  const [provider, setProvider] = useState('');
  const [userSpeaking, setUserSpeaking] = useState(false);
  const [acknowledging, setAcknowledging] = useState(false), [acknowledgementText, setAcknowledgementText] = useState('');
  const cueGeneration = useRef(0);
  const cueSource = useRef<AudioBufferSourceNode | null>(null), cueBuffers = useRef(new Map<string, Promise<AudioBuffer>>());
  const connection = useRef<WebSocket | null>(null), context = useRef<AudioContext | null>(null);
  const media = useRef<MediaStream | null>(null), node = useRef<AudioWorkletNode | null>(null);
  const generation = useRef(0), live = useRef(false), muteRef = useRef(false);
  const deadline = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);
  const callbacks = useRef(options); callbacks.current = options;

  function stopAcknowledgement() {
    cueGeneration.current++;
    const source = cueSource.current; cueSource.current = null;
    if (source) { source.onended = null; try { source.stop(); } catch { /* Already ended. */ } source.disconnect(); }
    setAcknowledging(false); setAcknowledgementText('');
  }
  function releaseMedia() {
    stopAcknowledgement(); cueBuffers.current.clear();
    node.current?.disconnect(); node.current = null;
    media.current?.getTracks().forEach(track => track.stop()); media.current = null;
    void context.current?.close(); context.current = null;
    setLevel(0); setUserSpeaking(false);
  }
  function stop() {
    generation.current++; live.current = false; clearTimeout(deadline.current);
    releaseMedia();
    const socket = connection.current; connection.current = null;
    if (socket) {
      socket.onclose = null; socket.onerror = null; socket.onmessage = null;
      if (socket.readyState === WebSocket.OPEN) socket.send(JSON.stringify({ type: 'audio.finish' }));
      socket.close();
    }
    muteRef.current = false; setMuted(false); setState('idle'); setStartedAt(0);
  }
  useEffect(() => () => stop(), [options.endpoint, options.scope]);

  async function start() {
    if (live.current || !options.enabled) return;
    live.current = true; const attempt = ++generation.current;
    setState('connecting'); setCaption(''); setWarning(''); setLastRunId('');
    const failed = (message: string) => {
      if (attempt !== generation.current) return;
      stop(); setWarning(message); callbacks.current.onError(message);
    };
    try {
      const config = await request<{ asr_provider: string; streaming: boolean; cues?: Record<string, string> }>('/voice/config');
      if (attempt !== generation.current) return;
      if (options.dictation && !config.streaming) {
        failed('实时语音输入需要配置火山语音 API Key，请联系管理员完成配置。'); return;
      }
      setProvider(config.asr_provider);
      const stream = await navigator.mediaDevices.getUserMedia({ audio: {
        echoCancellation: true, noiseSuppression: true, autoGainControl: true,
      } });
      if (attempt !== generation.current) { stream.getTracks().forEach(t => t.stop()); return; }
      media.current = stream;
      const ctx = new AudioContext(); context.current = ctx;
      await ctx.resume(); await ctx.audioWorklet.addModule('/voice-worklet.js');
      if (attempt !== generation.current) return;
      function cueBuffer(url: string) {
        let loading = cueBuffers.current.get(url);
        if (!loading) {
          loading = fetch(url, { credentials: 'same-origin' }).then(response => {
            if (!response.ok) throw new Error('Cue unavailable');
            return response.arrayBuffer();
          }).then(data => ctx.decodeAudioData(data));
          cueBuffers.current.set(url, loading);
          void loading.catch(() => cueBuffers.current.delete(url));
        }
        return loading;
      }
      if (!options.dictation) for (const url of Object.values(config.cues || {})) void cueBuffer(url).catch(() => {});
      const worklet = new AudioWorkletNode(ctx, 'duet-capture', { processorOptions: { protocol: 3 } });
      node.current = worklet;
      const source = ctx.createMediaStreamSource(stream); source.connect(worklet);
      const silent = ctx.createGain(); silent.gain.value = 0; worklet.connect(silent); silent.connect(ctx.destination);
      const socket = new WebSocket((location.protocol === 'https:' ? 'wss:' : 'ws:') + '//' + location.host + options.endpoint);
      connection.current = socket;
      deadline.current = setTimeout(() => failed('语音连接超时，请检查网络或服务配置。'), 25000);
      let ready = false, voicedMs = 0, lastVoice = 0, noiseFloor = 0.004;
      let interruptedUtterance = '';
      const acknowledged = new Set<string>(); let lastAcknowledged = -Infinity, cueRequest = 0;
      async function acknowledge(acceptance: SpeechAcceptance) {
        const cue = acceptance.acknowledgement;
        if (options.dictation || !cue?.url || acknowledged.has(acceptance.run_id)) return;
        acknowledged.add(acceptance.run_id);
        if (performance.now() - lastAcknowledged < 5000 || callbacks.current.replyPlaying?.()) return;
        lastAcknowledged = performance.now(); const request = ++cueRequest, cueEpoch = cueGeneration.current;
        try {
          const buffer = await cueBuffer(cue.url);
          if (attempt !== generation.current || request !== cueRequest || cueEpoch !== cueGeneration.current || ctx.state !== 'running'
            || performance.now() - lastAcknowledged > 1600 || callbacks.current.replyPlaying?.()) return;
          stopAcknowledgement(); const source = ctx.createBufferSource(); cueSource.current = source;
          source.buffer = buffer; source.connect(ctx.destination);
          source.onended = () => { if (cueSource.current === source) { cueSource.current = null; source.disconnect(); setAcknowledging(false); setAcknowledgementText(''); } };
          if (socket.readyState === WebSocket.OPEN) socket.send(JSON.stringify({ type: 'audio.cue', locale: cue.locale, run_id: acceptance.run_id }));
          setAcknowledgementText(cue.text); setAcknowledging(true); source.start();
        } catch { /* The pending text remains visible; do not retry over a real answer. */ }
      }
      function confirmSpeech(activity?: { id: string; text: string; duration_ms: number }) {
        if (options.dictation || muteRef.current || !activity || activity.id === interruptedUtterance) return;
        const text = activity.text.trim();
        const compact = text.replace(/[\s\p{P}\p{S}]/gu, '').toLowerCase();
        const command = /^(停|停下|停一下|停止|等等|等一下|别说了|暂停|stop|wait|holdon)$/.test(compact);
        const words = text.match(/[\p{L}\p{N}]+/gu) || [];
        const cjk = (text.match(/[\u3040-\u30ff\u3400-\u9fff\uac00-\ud7af]/g) || []).length;
        const enoughText = command || cjk >= 4 || words.length >= 2;
        // A spike, a repeated cumulative caption, or a short backchannel cannot interrupt.
        if (!enoughText || voicedMs < (command ? 160 : 480)
          || activity.duration_ms < (command ? 160 : 480) || performance.now() - lastVoice > 700) return;
        interruptedUtterance = activity.id; cueRequest++; stopAcknowledgement(); setUserSpeaking(true);
        callbacks.current.onSpeech?.();
        if (socket.readyState === WebSocket.OPEN) socket.send(JSON.stringify({ type: 'audio.interrupt' }));
      }
      socket.onopen = () => {
        if (attempt !== generation.current) { socket.close(); return; }
        socket.send(JSON.stringify({ type: 'audio.start' }));
      };
      worklet.port.onmessage = event => {
        if (attempt !== generation.current || !ready) return;
        const energy = muteRef.current ? 0 : event.data.level;
        setLevel(energy);
        const now = performance.now(), threshold = Math.max(0.018, noiseFloor * 3);
        if (energy < threshold) noiseFloor = noiseFloor * 0.98 + energy * 0.02;
        if (energy > threshold) { voicedMs = Math.min(2000, voicedMs + 32); lastVoice = now; }
        else if (now - lastVoice > 160) voicedMs = Math.max(0, voicedMs - 64);
        if (now - lastVoice > 450 || muteRef.current) setUserSpeaking(false);
        if (socket.readyState === WebSocket.OPEN) {
          if (socket.bufferedAmount > 1024 * 1024) { failed('网络发送较慢，已停止录音；请检查连接后重试。'); return; }
          socket.send(event.data.pcm);
        }
      };
      socket.onmessage = event => {
        if (attempt !== generation.current) return;
        let data;
        try { data = JSON.parse(event.data); } catch { failed('语音服务返回格式无效，请重试。'); return; }
        if (data.type === 'voice.ready') {
          ready = true; clearTimeout(deadline.current); setState('listening');
          interruptedUtterance = ''; voicedMs = 0;
          setStartedAt(previous => previous || Date.now()); setProvider(data.provider);
        } else if (data.type === 'transcript.partial') {
          confirmSpeech(data.activity);
          setCaption(data.text); callbacks.current.onPartial?.(data.text);
        } else if (data.type === 'dictation.final') {
          setCaption(data.text); callbacks.current.onFinal(data.text); stop();
        } else if (data.type === 'transcript.final') {
          // Short speech waits for server-side semantic validation before interrupting.
          // Recognition may have ended seconds ago; do not require fresh microphone energy here.
          if (callbacks.current.replyPlaying?.()) callbacks.current.onSpeech?.();
          cueRequest++; stopAcknowledgement();
          setWarning(''); setLastRunId(data.run_id || ''); setCaption(data.text);
          callbacks.current.onFinal(data.text, data);
          void acknowledge(data);
          setState(data.pending > 0 ? 'transcribing' : 'listening');
        } else if (data.type === 'transcript.processing' || data.type === 'transcript.queued') {
          setState(options.dictation ? 'finalizing' : 'transcribing');
        } else if (data.type === 'transcript.empty' || data.type === 'transcript.ignored') {
          if (data.type === 'transcript.ignored') { setCaption(''); setUserSpeaking(false); }
          if (data.retryable && data.message) setWarning(data.message);
          setState(data.pending > 0 ? 'transcribing' : 'listening');
        } else if (data.type === 'warning') {
          setWarning(data.message);
        } else if (data.type === 'error') {
          if (data.fatal) { failed(data.message || '实时转写中断，请重试。'); return; }
          setState(data.pending > 0 ? 'transcribing' : 'listening');
          setWarning(data.message || '语音连接异常。'); callbacks.current.onError(data.message || '语音连接异常。');
        }
      };
      socket.onerror = () => failed('语音连接失败，请检查登录、网络和语音服务。');
      socket.onclose = event => failed(event.code === 4401 || event.code === 4403
        ? '语音连接已失效，请检查登录状态后重试。' : '语音连接已断开；已确认的文字和任务会保留。');
    } catch (error) {
      const message = error instanceof DOMException && error.name === 'NotAllowedError'
        ? '无法使用麦克风，请允许录音权限或直接输入文字。'
        : error instanceof Error ? error.message : '无法启动语音，请检查麦克风与服务配置。';
      failed(message);
    }
  }
  function finish() {
    if (!live.current || state === 'finalizing') return;
    if (state === 'connecting') { stop(); return; }
    releaseMedia(); setState('finalizing');
    const socket = connection.current;
    if (socket?.readyState !== WebSocket.OPEN) { stop(); return; }
    socket.send(JSON.stringify({ type: 'audio.finish' }));
    deadline.current = setTimeout(() => {
      stop(); const message = '最终转写等待超时；屏幕上的草稿已保留，请检查后发送。';
      setWarning(message); callbacks.current.onError(message);
    }, 18000);
  }
  function toggleMute() {
    const next = !muteRef.current; muteRef.current = next; setMuted(next);
    media.current?.getAudioTracks().forEach(track => { track.enabled = !next; });
    if (next) setLevel(0);
  }
  const active = state === 'listening' || state === 'transcribing';
  return { active, present: state !== 'idle', state, provider,
    phase: state === 'connecting' ? '正在连接…' : state === 'finalizing' ? '正在校正最终文字…' : state === 'transcribing' ? '转写中' : '倾听中',
    level, userSpeaking, acknowledging, acknowledgementText, stopAcknowledgement, muted, startedAt, caption, warning, lastRunId, reportError: setWarning, start, stop, finish, toggleMute };
}

export function useMeetingVoice(threadId: string, onTranscript: (text: string, acceptance?: SpeechAcceptance) => void,
  onError: (text: string) => void, onSpeech: () => void, replyPlaying?: () => boolean) {
  return useSpeechStream({ endpoint: '/api/v1/threads/' + threadId + '/voice', enabled: !!threadId,
    onFinal: onTranscript, onError, onSpeech, replyPlaying });
}
