import { useEffect, useLayoutEffect, useRef, useState } from "react";
import {
  CircleStop,
  LoaderCircle,
  MessageSquareText,
  Mic2,
  Phone,
  PhoneOff,
  Send,
  Trash2,
} from "lucide-react";
import { api, post } from "../lib/api";
import type { Session } from "../lib/types";
import { useRecorder } from "../hooks/useRecorder";
import { useVoiceCall } from "../hooks/useVoiceCall";
import CallExperience, { type CallPhase } from "./CallExperience";

export default function TalkerComposer({
  session,
  notify,
  refresh,
  callContainer,
  onExpandedChange,
}: {
  session: Session;
  notify: (text: string) => void;
  refresh: () => Promise<void>;
  callContainer: HTMLElement | null;
  onExpandedChange: (expanded: boolean) => void;
}) {
  const [mode, setMode] = useState("text");
  const [text, setText] = useState("");
  const [sending, setSending] = useState(false);
  const voice = useRecorder(session.language);
  const call = useVoiceCall(session.id, refresh);
  const active = !!session.call && session.call.status !== "ended";
  const calling = active || call.connecting || call.connected;
  const [tick, setTick] = useState(Date.now());
  const [callError, setCallError] = useState("");
  const [minimized, setMinimized] = useState(false);
  const [ending, setEnding] = useState(false);
  const phoneButton = useRef<HTMLButtonElement>(null);
  const wasCalling = useRef(false);
  const expanded = calling && !minimized;
  useLayoutEffect(() => { onExpandedChange(expanded); }, [expanded, onExpandedChange]);
  useEffect(() => () => onExpandedChange(false), [onExpandedChange]);
  useEffect(() => {
    let frame = 0;
    if (!calling) {
      setMinimized(false);
      if (wasCalling.current) frame = requestAnimationFrame(() => phoneButton.current?.focus({ preventScroll: true }));
    }
    wasCalling.current = calling;
    return () => cancelAnimationFrame(frame);
  }, [calling]);
  useEffect(() => {
    if (!calling) return;
    setTick(Date.now());
    const timer = window.setInterval(() => setTick(Date.now()), 1000);
    return () => window.clearInterval(timer);
  }, [calling]);
  const elapsed = active ? Math.max(0, Math.floor((tick - Date.parse(session.call!.started_at)) / 1000)) : 0;
  const duration = `${Math.floor(elapsed / 60).toString().padStart(2, "0")}:${(elapsed % 60).toString().padStart(2, "0")}`;
  async function toggleCall() {
    setCallError("");
    if (!calling) {
      await call.start();
    } else if (call.connected || call.connecting) {
      setEnding(true);
      try { await call.hangup(); } finally { setEnding(false); }
    } else if (active) {
      setEnding(true);
      try {
        await post(`/sessions/${session.id}/calls/${session.call!.id}/hangup`);
        await refresh();
      } catch (e) { setCallError((e as Error).message); }
      finally { setEnding(false); }
    }
  }
  async function submit() {
    setSending(true);
    try {
      if (mode === "text") {
        await post(`/sessions/${session.id}/messages`, { text });
        setText("");
      } else if (voice.blob) {
        const form = new FormData();
        form.append("file", voice.blob, "recording.webm");
        form.append("transcript", voice.transcript);
        await api(`/sessions/${session.id}/audio`, {
          method: "POST",
          body: form,
        });
        voice.reset();
      }
      await refresh();
    } catch (e) {
      notify((e as Error).message);
    } finally {
      setSending(false);
    }
  }
  const thinking = !["collecting", "waiting_pause"].includes(call.inputStatus)
    && (call.inputStatus === "refining" || session.call?.activity_state === "thinking" || session.call?.status === "thinking");
  const phase: CallPhase = !call.connected ? "connecting" : session.call?.paused ? "paused" : thinking ? "thinking" : "listening";
  const label = phase === "connecting" ? (call.connecting ? "正在接通" : "音频未连接")
    : phase === "paused" ? "自动回答已暂停" : phase === "thinking" ? "思考中"
    : call.muted ? "麦克风已静音" : session.call?.status === "speaking" ? "倾听中 · AI 正在说话" : "倾听中";
  return (
    <div
      className={calling ? "" : `rounded-2xl border p-4 ${voice.recording ? "border-talker/30 bg-talker/5" : "border-white/10 bg-white/[.025]"}`}
    >
      {!calling && <div className="mb-3 flex items-center justify-between gap-3">
        <div role="group" aria-label="消息方式" className="flex shrink-0 gap-1 rounded-xl border border-white/10 bg-black/15 p-1">
          <button
            onClick={() => setMode("text")}
            disabled={voice.recording || calling || sending}
            aria-label="文本消息"
            title="文本消息"
            aria-pressed={!calling && mode === "text"}
            className={`icon-button !rounded-lg disabled:opacity-35 ${!calling && mode === "text" ? "!bg-white/10 !text-white/80" : ""}`}
          >
            <MessageSquareText size={16} />
          </button>
          <button
            onClick={() => setMode("voice")}
            disabled={calling || sending}
            aria-label="语音消息"
            title="语音消息"
            aria-pressed={!calling && mode === "voice"}
            className={`icon-button !rounded-lg disabled:opacity-35 ${!calling && mode === "voice" ? "!bg-talker/15 !text-talker" : ""}`}
          >
            <Mic2 size={16} />
          </button>
          <button
            ref={phoneButton}
            onClick={() => void toggleCall()}
            disabled={voice.recording || sending}
            aria-label={calling ? "挂断通话" : "开始通话"}
            title={calling ? "挂断通话" : "开始通话"}
            aria-pressed={calling}
            className={`icon-button !rounded-lg disabled:opacity-35 ${calling ? "!bg-red-400/10 !text-red-300" : "hover:!text-talker"}`}
          >
            {call.connecting ? <LoaderCircle size={16} className="animate-spin" /> : calling ? <PhoneOff size={16} /> : <Phone size={16} />}
          </button>
        </div>
        <span className="text-right text-[10px] text-white/30">
          {session.language === "en-US" ? "EN" : "中文"} · ⌘ / Ctrl + Enter
        </span>
      </div>}
      {!calling && session.messages.some((m) => m.public_stream && m.status === "streaming") && (
        <button type="button" className="secondary-button mb-3 !py-1.5 !text-xs"
          onClick={() => void post(`/sessions/${session.id}/responses/stop`).then(refresh).catch((e) => notify(e.message))}>
          <CircleStop size={13} />停止回答
        </button>
      )}
      {calling ? (
        <CallExperience
          container={callContainer} expanded={expanded} phase={phase} label={label} duration={duration}
          level={Math.max(call.muted ? 0 : call.level, call.playbackLevel)} muted={call.muted}
          connected={call.connected} ending={ending} caption={call.partial}
          warning={call.inputWarning} pendingCount={session.call?.pending_count || 0}
          error={callError || call.error || session.call?.error || (!call.connected && !call.connecting ? "本页尚未连接音频，可挂断后重新拨号。" : "")}
          onMinimize={() => setMinimized(true)} onExpand={() => setMinimized(false)}
          onMute={call.toggleMute} onHangup={() => void toggleCall()}
        />
      ) : mode === "text" ? (
        <div className="flex items-end gap-3">
          <textarea
            aria-label="输入问题"
            value={text}
            onChange={(e) => setText(e.target.value)}
            onKeyDown={(e) => {
              if (
                e.key === "Enter" &&
                (e.metaKey || e.ctrlKey) &&
                text.trim() &&
                !sending
                && !e.nativeEvent.isComposing
              )
                void submit();
            }}
            rows={3}
            placeholder="输入一个或多个问题，AI 会识别、拆分并去重…"
            className="min-h-20 flex-1 resize-none rounded-xl border border-white/10 bg-black/15 px-3 py-2.5 text-sm leading-6 text-white/80 placeholder:text-white/30 focus:border-ai/40 focus:outline-none"
          />
          <button
            disabled={sending || !text.trim()}
            onClick={submit}
            className="flex h-11 w-11 shrink-0 items-center justify-center rounded-xl bg-ai text-ink disabled:opacity-35"
            aria-label="发送文本消息"
          >
            {sending ? (
              <LoaderCircle size={17} className="animate-spin" />
            ) : (
              <Send size={17} />
            )}
          </button>
        </div>
      ) : (
        <div>
          <div className="flex items-center gap-4">
            <button
              disabled={sending || !!voice.blob}
              onClick={() =>
                voice.recording ? voice.stop() : void voice.start()
              }
              aria-label={voice.recording ? "结束录音" : "开始录音"}
              className={`flex h-12 w-12 shrink-0 items-center justify-center rounded-full disabled:opacity-40 ${voice.recording ? "bg-talker text-ink" : "bg-white text-ink"}`}
            >
              {voice.recording ? <CircleStop size={23} /> : <Mic2 size={23} />}
            </button>
            <div className="flex-1">
              <p className="mb-2 text-xs text-white/50">
                {voice.recording
                  ? "正在录音 · 点击结束后发送"
                  : voice.blob
                    ? "录音已保存 · 确认最终转录后发送"
                    : "点击开始录音，原音频会保留供反复回听。"}
              </p>
              {voice.recording && (
                <div className="flex h-5 items-center gap-1">
                  {Array.from({ length: 24 }, (_, i) => (
                    <span
                      key={i}
                      className="h-full w-1 animate-wave rounded-full bg-talker/70"
                      style={{ animationDelay: `${(i % 8) * 75}ms` }}
                    />
                  ))}
                </div>
              )}
            </div>
          </div>
          {(voice.recording || voice.blob) && (
            <textarea
              aria-label="语音转录文本"
              rows={2}
              value={voice.transcript}
              onChange={(e) => voice.setTranscript(e.target.value)}
              placeholder="浏览器转录在这里显示；不可用时可填写最终转录。留空发送将由后端 STT 处理。"
              className="field mt-3 resize-none"
            />
          )}
          {voice.error && (
            <p className="mt-2 text-xs leading-5 text-orange-300">
              {voice.error}
            </p>
          )}
          {voice.blob && (
            <div className="mt-3 flex gap-2">
              <button
                className="primary-button"
                disabled={sending}
                onClick={submit}
              >
                {sending ? (
                  <LoaderCircle size={14} className="animate-spin" />
                ) : (
                  <Send size={14} />
                )}
                发送语音
              </button>
              <button
                className="secondary-button"
                disabled={sending}
                onClick={voice.reset}
              >
                <Trash2 size={14} />
                重新录制
              </button>
            </div>
          )}
        </div>
      )}
      {!calling && (callError || call.error) && <p className="mt-2 text-xs leading-5 text-orange-300" role="alert">{callError || call.error || session.call?.error}</p>}
    </div>
  );
}
