import { useEffect, useRef, useState } from "react";
import ReactMarkdown from "react-markdown";
import {
  Bot,
  Copy,
  EyeOff,
  Keyboard,
  LoaderCircle,
  Mic2,
  Quote,
  RotateCcw,
} from "lucide-react";
import { post } from "../lib/api";
import { copyText } from "../lib/clipboard";
import type { Message, Session } from "../lib/types";
import type { SessionEvent } from "../hooks/useSession";
import AudioControl from "./AudioControl";
import AnswerParts from "./AnswerParts";
import DraftDecision from "./DraftDecision";
import { QuestionBadge } from "./QuestionPanel";

export default function Conversation({
  session,
  lastEvent,
  autoPlaybackEnabled,
  notify,
  refresh,
}: {
  session: Session;
  lastEvent: SessionEvent | null;
  autoPlaybackEnabled: boolean;
  notify: (text: string) => void;
  refresh: () => Promise<void>;
}) {
  const viewport = useRef<HTMLDivElement | null>(null);
  const nearBottom = useRef(true);
  const lastCount = useRef(session.messages.length);
  const [newCount, setNewCount] = useState(0);
  const handledEvents = useRef(new Set<string>());
  const [withdrawing, setWithdrawing] = useState("");
  const retractionCheck = session.retraction_checks?.slice().reverse().find(
    (check) => ["pending", "failed"].includes(check.status),
  ) || session.retraction_checks?.at(-1);
  async function withdraw(answerId: string) {
    setWithdrawing(answerId);
    try {
      await post(`/sessions/${session.id}/answers/${answerId}/withdraw`);
      await refresh();
      notify("回答已撤回，内容和播放入口已隐藏");
    } catch (e) { notify((e as Error).message); }
    finally { setWithdrawing(""); }
  }
  const [autoPlayToken, setAutoPlayToken] = useState<{
    resourceId: string;
    token: string;
  } | null>(null);
  useEffect(() => {
    const added = Math.max(0, session.messages.length - lastCount.current);
    lastCount.current = session.messages.length;
    if (!nearBottom.current && added) setNewCount((count) => count + added);
    if (nearBottom.current && viewport.current)
      viewport.current.scrollTop = viewport.current.scrollHeight;
  }, [session.messages, session.last_sequence]);
  useEffect(() => {
    if (!lastEvent || handledEvents.current.has(lastEvent.eventId)) return;
    handledEvents.current.add(lastEvent.eventId);
    if (!autoPlaybackEnabled) return;
    const isLatestTalkerVoice =
      lastEvent.type === "message.created" && session.role === "talker";
    const isConfirmedAiVoice = lastEvent.type === "audio.ready";
    if ((isLatestTalkerVoice || isConfirmedAiVoice) && lastEvent.resourceId) {
      setAutoPlayToken({
        resourceId: lastEvent.resourceId,
        token: lastEvent.eventId,
      });
    }
  }, [lastEvent, autoPlaybackEnabled, session.role]);
  async function retry(message: Message) {
    try {
      if (message.type === "ai")
        await post(`/sessions/${session.id}/answers/${message.id}/retry`);
      else {
        const text =
          message.status === "transcription_failed"
            ? message.text
            : message.text;
        if (text === null) return;
        await post(`/sessions/${session.id}/messages/${message.id}/retry`, {
          text,
        });
      }
      await refresh();
    } catch (e) {
      notify((e as Error).message);
    }
  }

  return (
    <div className="relative flex min-h-0 flex-1 flex-col">
    <div
      ref={viewport}
      onScroll={() => {
        const v = viewport.current!;
        nearBottom.current =
          v.scrollHeight - v.scrollTop - v.clientHeight < 100;
        if (nearBottom.current) setNewCount(0);
      }}
      className="conversation-scroll scrollbar-none flex flex-1 flex-col gap-3 overflow-y-auto p-4 sm:p-5"
    >
      <div className="flex items-center gap-3 text-[10px] uppercase tracking-widest text-white/30">
        <span className="h-px flex-1 bg-white/10" />
        Session {session.id.slice(0, 8)} · Live conversation
        <span className="h-px flex-1 bg-white/10" />
      </div>
      {session.role === "hinter" && retractionCheck && (
        <div role="status" className="flex flex-wrap items-center gap-2 rounded-lg border border-hinter/20 bg-hinter/5 px-3 py-2 text-xs text-hinter">
          <EyeOff size={14} />
          {retractionCheck.status === "pending" ? "正在按 Hint 回查最近5个问题…" : retractionCheck.status === "failed" ? retractionCheck.error : `已回查最近 ${retractionCheck.checked_count} 题，撤回 ${retractionCheck.withdrawn_count} 条回答`}
          {retractionCheck.status === "failed" && <button className="secondary-button !px-2 !py-1" onClick={() => void post(`/sessions/${session.id}/retraction-checks/${retractionCheck.id}/retry`).then(refresh).catch((e) => notify(e.message))}>重试回查</button>}
        </div>
      )}
      {!session.messages.length && (
        <div className="flex flex-1 flex-col items-center justify-center py-14 text-center">
          <div className="mb-5 flex h-16 w-16 items-center justify-center rounded-2xl border border-ai/20 bg-ai/10">
            <Bot size={28} className="text-ai" />
          </div>
          <h2 className="text-lg font-medium">让两路信息汇成一个好答案</h2>
          <p className="mt-3 max-w-md text-sm leading-7 text-white/40">
            {session.interaction_mode === "realtime_call" ? "通话已接通，直接用英语说话。AI 自动回答，Hinter 可随时补充提示。" : "Talker 直接提问，AI 结合记忆自动回答。Hinter 随时补充信息和限制，无需逐题审核。"}
          </p>
          <p className="mt-4 text-xs text-white/25">
            可载入示例对话，也可以直接开始自己的对话。
          </p>
        </div>
      )}
      {session.messages.map((message) => {
        if (message.status === "withdrawn") return (
          <article key={message.id} data-message-id={message.id} className="ml-12 flex items-center gap-2 rounded-xl border border-white/10 px-4 py-3 text-xs text-white/45" role="status">
            <EyeOff size={15} /><span>AI · 已撤回 · 内容已隐藏</span>
          </article>
        );
        const screening = message.type === "screening";
        const Icon =
          message.type === "talker"
            ? Mic2
            : message.type === "hinter"
              ? Keyboard
              : Bot;
        const color =
          message.type === "talker"
            ? "text-talker"
            : message.type === "hinter"
              ? "text-hinter"
              : "text-ai";
        const tone =
          message.type === "talker"
            ? "border-talker/15 bg-talker/[.04]"
            : message.type === "hinter"
              ? "border-hinter/15 bg-hinter/[.04]"
              : "border-ai/20 bg-ai/[.05]";
        const label =
          message.type === "talker"
            ? `Talker · ${message.input_mode === "voice" ? "Voice transcript" : "Text message"}`
            : message.type === "hinter"
              ? "Hinter · Private guidance"
              : screening
                ? "AI · Question screening"
                : message.public_stream ? "AI · 自动回答"
                : message.status === "generating" ||
                    ["pending_confirmation", "polishing"].includes(
                      message.status,
                    )
                  ? "AI · Private draft for review"
                  : message.call_id
                    ? "AI · 实时通话回答"
                  : message.refusal
                    ? "AI · Declined answer"
                    : "AI · 回答";
        const answer = session.answers.find((a) => a.id === message.id);
        const linkedQuestions = screening
          ? []
          : session.questions.filter((q) => message.question_ids?.includes(q.id));
        const allLinkedQuestionsAnswered = linkedQuestions.length > 0 && linkedQuestions.every((q) => q.status === "answered");
        const busy = [
          "intake_pending",
          "transcribing",
          "generating",
          "streaming",
          "polishing",
        ].includes(message.status);
        return (
          <div
            key={message.id}
            className={`conversation-message animate-rise ${screening ? "is-screening" : ""} ${linkedQuestions.length ? "has-questions" : ""}`}
          >
            <div className="conversation-message-main">
              {!screening && (
                <div
                  className={`flex h-8 w-8 shrink-0 items-center justify-center rounded-[10px] border border-white/10 bg-white/5 ${color}`}
                >
                  <Icon size={15} />
                </div>
              )}
              <article
                className={`conversation-bubble min-w-0 flex-1 rounded-xl border px-3.5 py-2.5 ${tone} ${screening ? "border-dashed" : "rounded-tl-sm"}`}
              >
              <div className="mb-1.5 flex min-w-0 items-center gap-2">
                <span className={`text-[11px] ${color}`}>{label}</span>
                {!message.is_answer_card && message.reply_kind === "supplement" && <span className="text-[10px] text-ai/75">补充回答</span>}
                {message.type === "ai" && message.status === "completed" && (
                  <span className="shrink-0 text-[10px] text-white/38">
                    · {message.call_id ? ({ speaking: "播放中", delivered: "已发送", interrupted: "已中断" }[message.delivery || ""] || "已发送") : "已发送"}
                  </span>
                )}
                {message.is_answer_card && message.updating && <span role="status" className="text-[10px] text-ai/75">更新中…</span>}
                <time className="text-[10px] text-white/30">
                  {new Date(message.created_at).toLocaleTimeString([], {
                    hour: "2-digit",
                    minute: "2-digit",
                  })}
                </time>
                <div className="ml-auto flex shrink-0 items-center gap-1">
                  {linkedQuestions.length === 1 && (
                    <QuestionBadge status={linkedQuestions[0].status} live={!!message.call_id} />
                  )}
                  {linkedQuestions.length > 1 && (
                    <span className={`inline-flex h-[22px] items-center justify-center rounded-full border px-2 text-[9px] leading-none ${allLinkedQuestionsAnswered ? "border-emerald-400/20 bg-emerald-400/10 text-emerald-300" : "border-white/10 bg-white/[.03] text-white/45"}`}>
                      {allLinkedQuestionsAnswered ? `${linkedQuestions.length} 题已答` : `${linkedQuestions.length} 个问题`}
                    </span>
                  )}
                  {message.text && (
                    <button
                      aria-label={message.type === "ai" ? "复制回答" : "复制消息"}
                      className="icon-button !h-7 !w-7"
                      onClick={() =>
                        copyText(message.text).then((ok) =>
                          notify(
                            ok
                              ? message.type === "ai" ? "回答已复制" : "消息已复制"
                              : "无法写入剪贴板，请选择文本后复制。",
                          ),
                        )
                      }
                    >
                      <Copy size={13} />
                    </button>
                  )}
                  {session.role === "hinter" && message.type === "ai" && ["completed", "streaming", "interrupted"].includes(message.status) && (
                    <button className="flex shrink-0 items-center gap-1 rounded px-1 py-1 text-[10px] text-white/45 hover:text-orange-300 focus-visible:outline focus-visible:outline-2 focus-visible:outline-hinter disabled:opacity-40" disabled={!!withdrawing} onClick={() => void withdraw(message.id)} aria-label="撤回消息" aria-busy={withdrawing === message.id}>
                      <EyeOff size={12} />{withdrawing === message.id ? "撤回中…" : "撤回消息"}
                    </button>
                  )}
                </div>
              </div>
              <div className="markdown-content text-sm leading-6 text-white/80">
                {message.type === "ai" ? (
                  message.is_answer_card && ((message.answer_parts?.length || 0) > 1 || session.role === "hinter") ?
                    <AnswerParts parts={message.answer_parts!} sessionId={session.id} answerId={message.id}
                      canCorrect={session.role === "hinter" && !message.updating} refresh={refresh} notify={notify} /> :
                    <ReactMarkdown>{message.text || "I don't know."}</ReactMarkdown>
                ) : (
                  <p className="whitespace-pre-wrap">
                    {message.text ||
                      (message.status === "transcribing"
                        ? "录音已保存，正在转录…"
                        : "")}
                  </p>
                )}
                {["generating", "streaming"].includes(message.status) && !message.text_done && (
                  <span className="ml-1 inline-block h-4 w-1 animate-pulse bg-ai" />
                )}
              </div>
              {message.type === "talker" && message.reply_summary?.state === "collecting" && (
                <p role="status" className="mt-2 flex items-center gap-2 text-[11px] text-ai/75">
                  <LoaderCircle size={12} className="animate-spin" />
                  正在汇总回答（{message.reply_summary.completed}/{message.reply_summary.total}）
                </p>
              )}
              {message.transcription_status && !["corrected", "verified_passthrough"].includes(message.transcription_status) && <p className="mt-2 text-[10px] text-orange-300">{message.transcription_status === "needs_clarification" ? "转写有歧义 · 等待确认原话" : "转写校正已降级 · 保留原话"}</p>}
              {session.role === "hinter" && message.transcription && (
                <details className="mt-2 text-[11px] leading-5 text-white/45">
                  <summary className="cursor-pointer">查看语音识别详情</summary>
                  <div className="mt-2 space-y-1 break-words">
                    <p>原始识别：{message.transcription.raw_transcript || "无"}</p>
                    <p>整段识别：{message.transcription.final_asr_text || "未完成"}</p>
                    <p>校正：{({corrected: "已校正", verified_passthrough: "整段识别已确认 · 无需额外校正", degraded: "已降级", needs_clarification: "待确认"}[message.transcription.status] || message.transcription.status)} · {message.transcription.model_label || message.transcription.model} · 独立校正</p>
                    <p>停顿 {message.transcription.silence_ms}ms · 整段识别 {message.transcription.final_asr_ms ?? "—"}ms · 校正 {message.transcription.refiner_ms}ms</p>
                  </div>
                </details>
              )}
              {busy && (
                <div className="mt-3 flex items-center gap-2 text-[11px] text-ai/80">
                  <LoaderCircle size={13} className="animate-spin" />
                  {message.status === "intake_pending"
                    ? "AI 正在识别、拆分与去重"
                    : message.status === "transcribing"
                      ? "等待最终转录"
                      : "正在生成回答"}
                </div>
              )}
              {message.error && (
                <div
                  role="alert"
                  className="mt-3 flex flex-wrap items-center gap-2 rounded-lg bg-red-400/5 p-2 text-xs text-orange-300"
                >
                  <span className="flex-1">{message.error}</span>
                  {(message.type !== "ai" ||
                    (session.role === "hinter" &&
                      ["failed", "interrupted"].includes(message.status))) && (
                    <button
                      onClick={() => retry(message)}
                      className="flex items-center gap-1 rounded bg-white/5 px-2 py-1"
                    >
                      <RotateCcw size={12} />
                      重试
                    </button>
                  )}
                </div>
              )}
              {session.role === "hinter" &&
                message.type === "ai" &&
                !message.public_stream &&
                ["generating", "pending_confirmation", "polishing"].includes(
                  message.status,
                ) && (
                  <DraftDecision
                    session={session}
                    message={message}
                    refresh={refresh}
                    notify={notify}
                  />
                )}
              {message.type === "ai" && message.status === "completed" && session.role === "hinter" && answer?.wiki_sources && (
                <div className="mt-1 text-[9px] text-white/30">Wiki 上下文 · {answer.wiki_sources.length} 篇</div>
              )}
              {message.audio &&
                (message.type !== "ai" || message.status === "completed") && (
                  <AudioControl
                    key={message.audio.id}
                    audio={message.audio}
                    sessionId={session.id}
                    text={message.text}
                    language="en-US"
                    original={message.type === "talker"}
                    compact
                    autoPlayToken={
                      autoPlayToken?.resourceId === message.id
                        ? autoPlayToken.token
                        : undefined
                    }
                  />
                )}
              {linkedQuestions.length > 0 && (
                <div className="message-reference" aria-label="关联问题">
                  <Quote size={12} aria-hidden="true" />
                  <span className="message-reference-text" title={linkedQuestions.map((q) => `${q.display_no} ${q.text}`).join(" · ")}>
                    {linkedQuestions.map((q) => `${q.display_no} · ${q.text}`).join("  /  ")}
                  </span>
                </div>
              )}
              </article>
            </div>
          </div>
        );
      })}
    </div>
    {newCount > 0 && <button className="secondary-button absolute bottom-3 left-1/2 -translate-x-1/2 !bg-panel" onClick={() => { if (viewport.current) viewport.current.scrollTop = viewport.current.scrollHeight; nearBottom.current = true; setNewCount(0); }}>查看 {newCount} 条新消息</button>}
    </div>
  );
}
