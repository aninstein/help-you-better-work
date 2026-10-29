import { useCallback, useEffect, useState } from "react";
import {
  ArrowLeft,
  AudioLines,
  Check,
  ExternalLink,
  Link,
  ListChecks,
  LoaderCircle,
  MemoryStick,
  Mic2,
  Keyboard,
  Settings2,
  Sparkles,
} from "lucide-react";
import type { Role } from "../lib/types";
import { api, post } from "../lib/api";
import { copyText } from "../lib/clipboard";
import { useSession } from "../hooks/useSession";
import { useReplyAudio } from "../hooks/useReplyAudio";
import { useAutoPlayback } from "../hooks/useAutoPlayback";
import WorkspaceDirectory from "./WorkspaceDirectory";
import Conversation from "./Conversation";
import MemoryPanel from "./MemoryPanel";
import QuestionPanel from "./QuestionPanel";
import ReviewComposer from "./ReviewComposer";
import SettingsDialog from "./SettingsDialog";
import TalkerComposer from "./TalkerComposer";
import CallPanel, { CallReviewPanel, callLabels } from "./CallPanel";

export default function ChatRoom({
  role,
  onExit,
}: {
  role: Role;
  onExit: () => void;
}) {
  const controller = useSession(role);
  return <div className={role === "hinter" ? "hinter-shell" : ""}>
    {role === "hinter" && <WorkspaceDirectory
      selectedTalkerId={controller.selectedTalkerId}
      selectedSessionId={controller.selectedSessionId}
      onSelect={controller.selectSession}
      onExit={onExit}
      ready={!!controller.session}
    />}
    <div className="min-w-0 flex-1">
      {controller.session ? <SessionWorkspace key={controller.session.id} role={role} onExit={onExit} {...controller} session={controller.session} /> :
        <div className="flex min-h-screen flex-col items-center justify-center gap-4" aria-live="polite">
          {!controller.error && <LoaderCircle className="animate-spin text-ai" size={28} />}
          <p className="text-sm text-white/55">{controller.error || "正在加载会话空间…"}</p>
          {controller.error && <button className="secondary-button" onClick={() => controller.selectSession(controller.selectedSessionId, controller.selectedTalkerId)}>重试加载</button>}
          <button className="secondary-button" onClick={onExit}>返回首页</button>
        </div>}
    </div>
  </div>;
}

function SessionWorkspace({ role, onExit, session, connection, refresh, lastEvent, newSession, selectSession, resetSession }: Omit<ReturnType<typeof useSession>, "session"> & { role: Role; onExit: () => void; session: NonNullable<ReturnType<typeof useSession>["session"]> }) {
  const { autoPlaybackEnabled, setAutoPlaybackEnabled } = useAutoPlayback();
  const [panel, setPanel] = useState(
    new URLSearchParams(location.search).get("panel") === "memory"
      ? "memory"
      : "questions",
  );
  const [selectedIds, setSelectedIds] = useState<string[]>([]);
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [callContainer, setCallContainer] = useState<HTMLDivElement | null>(null);
  const [callExpanded, setCallExpanded] = useState(false);
  const [toast, setToast] = useState("");
  const [loadingSample, setLoadingSample] = useState(false);
  const [togglingAi, setTogglingAi] = useState(false);
  const replyWarning = useReplyAudio(session?.id || "", !!session && role === "talker" && autoPlaybackEnabled
    && (!session.call || session.call.status === "ended"));
  const closeSettings = useCallback(() => setSettingsOpen(false), []);
  useEffect(() => {
    if (!toast) return;
    const timer = window.setTimeout(() => setToast(""), 4000);
    return () => window.clearTimeout(timer);
  }, [toast]);
  useEffect(() => setSelectedIds([]), [session?.id]);

  const pending = session.questions.filter(
    (q) => q.status === "held",
  );
  const inCall = !!session.call && session.call.status !== "ended";
  const selected = session.questions.filter(
    (q) =>
      selectedIds.includes(q.id) &&
      [
        "held",
        "unavailable",
        "awaiting_user",
        "ready",
        "legacy_pending",
      ].includes(q.status),
  );
  const thinking = session.questions.some((q) => q.status === "generating");
  const screening = session.messages.some((m) =>
    ["intake_pending", "transcribing"].includes(m.status),
  );
  const RoleIcon = role === "talker" ? Mic2 : Keyboard;
  const otherRole = role === "talker" ? "hinter" : "talker";
  const otherLink = `/?role=${otherRole}&talker=${session.talker_id}&session=${session.id}`;
  function choosePanel(value: string) {
    setPanel(value);
    const params = new URLSearchParams(location.search);
    params.set("panel", value);
    history.replaceState(null, "", "/?" + params);
  }
  function toggle(id: string) {
    setSelectedIds((ids) =>
      ids.includes(id) ? ids.filter((item) => item !== id) : [...ids, id],
    );
  }
  function toggleAll() {
    setSelectedIds(
      pending.every((q) => selectedIds.includes(q.id))
        ? []
        : pending.map((q) => q.id),
    );
  }
  async function toggleAiMode() {
    setTogglingAi(true);
    try {
      await api(`/sessions/${session!.id}/ai-mode`, {
        method: "PUT",
        body: JSON.stringify({ enabled: !session!.ai_mode }),
      });
      await refresh();
      setToast(
        session!.ai_mode
          ? "自动回复已暂停，现有回答已保留。"
          : "已恢复自动回复，无需逐题确认。",
      );
    } catch (e) {
      setToast((e as Error).message);
    } finally {
      setTogglingAi(false);
    }
  }
  async function rejectQuestion(id: string) {
    const question = session?.questions.find((q) => q.id === id);
    if (!question) return;
    try {
      await post(`/sessions/${session!.id}/questions/${id}/resolve`, {
        decision: "reject", version: question.version,
      });
      await refresh();
      setSelectedIds((ids) => ids.filter((item) => item !== id));
      setToast("已跳过该问题。");
    } catch (e) {
      setToast((e as Error).message);
    }
  }
  async function sample() {
    setLoadingSample(true);
    try {
      await post(`/sessions/${session!.id}/sample`);
      await refresh();
      setToast("示例已提交，AI 正在识别问题。");
    } catch (e) {
      setToast((e as Error).message);
    } finally {
      setLoadingSample(false);
    }
  }

  return (
    <div className="grid-lines min-h-screen p-3 sm:p-5">
      <div className="workspace mx-auto flex max-w-[1600px] flex-col overflow-hidden rounded-3xl border border-white/10 bg-ink/90 shadow-2xl">
        <header className="flex min-h-16 shrink-0 flex-wrap items-center justify-between gap-2 border-b border-white/10 px-4 py-3 sm:px-6">
          <div className="flex items-center gap-3">
            <button
              className="icon-button"
              onClick={onExit}
              aria-label="返回首页"
            >
              <ArrowLeft size={18} />
            </button>
            <AudioLines className="text-ai" size={21} />
            <div>
              <h1 className="text-sm font-medium">{session.talker_name} · {role === "hinter" ? "Hinter 工作台" : "个人助手"}</h1>
              <p className="mt-0.5 text-[11px] text-white/40">
                {session.customer_name} · {session.customer_id} ·{" "}
                {session.id.slice(0, 8)}
              </p>
            </div>
          </div>
          <div className="flex items-center gap-2">
            {role === "hinter" && !inCall && (
              <button
                role="switch"
                aria-label="自动回复"
                aria-checked={!!session.ai_mode}
                disabled={togglingAi}
                onClick={toggleAiMode}
                className={`secondary-button !px-3 !py-2 !text-[11px] ${session.ai_mode ? "!border-ai/40 !bg-ai/15 !text-ai" : ""}`}
              >
                <Sparkles size={13} />
                自动回复 · {session.ai_mode ? "运行中" : "已暂停"}
              </button>
            )}
            <button
              className="secondary-button !px-3 !py-2 !text-[11px]"
              aria-label="新建对话并清屏"
              onClick={() => resetSession().catch((e) => setToast(e.message))}
            >
              新建对话 / 清屏
            </button>
            <a
              href={otherLink}
              target="_blank"
              rel="noreferrer"
              className="secondary-button !px-3 !py-2 !text-[11px]"
              aria-label={`打开 ${otherRole} 工作台`}
            >
              <ExternalLink size={13} />
              <span className="hidden sm:inline">
                打开 {otherRole === "talker" ? "Talker" : "Hinter"}
              </span>
            </a>
            <button
              className="icon-button"
              aria-label="复制会话邀请链接"
              onClick={() =>
                copyText(location.origin + otherLink).then((ok) =>
                  setToast(
                    ok
                      ? "协作链接已复制，打开后进入同一会话。"
                      : "无法复制链接，请点击打开另一角色工作台。",
                  ),
                )
              }
            >
              <Link size={16} />
            </button>
            <div
              className={`flex items-center gap-2 rounded-xl border px-3 py-2 text-xs ${role === "talker" ? "border-talker/20 bg-talker/10 text-talker" : "border-hinter/20 bg-hinter/10 text-hinter"}`}
            >
              <RoleIcon size={14} />
              {role === "talker" ? "Talker" : "Hinter"}
            </div>
            <button
              className="icon-button"
              onClick={() => setSettingsOpen(true)}
              aria-label="设置"
            >
              <Settings2 size={17} />
            </button>
          </div>
        </header>
        <div className="flex shrink-0 flex-wrap items-center justify-between gap-2 border-b border-white/10 bg-white/[.02] px-4 py-3 text-[11px] sm:px-6">
          <div className="flex items-center gap-4">
            <span className="flex items-center gap-1.5 text-white/50">
              <span
                className={`h-1.5 w-1.5 rounded-full ${["live", "sse"].includes(connection) ? "bg-emerald-400" : "bg-orange-400"}`}
              />
              {connection === "live"
                ? "实时连接"
                : connection === "sse"
                  ? "SSE 已连接"
                  : "重新连接中"}
            </span>
            <span className="text-ai/80">AI Agent</span>
            <span className="hidden text-hinter/75 sm:block">
              {session.automatic_paused ? "自动回复已暂停" : "默认自动回答 · 按需提示"}
            </span>
          </div>
          <span className="flex items-center gap-2 text-white/45">
            {screening || thinking ? (
              <LoaderCircle size={12} className="animate-spin text-ai" />
            ) : (
              <Sparkles size={12} />
            )}
            {inCall ? callLabels[session.call!.status] || "通话中" : screening
              ? "AI 正在理解问题…"
              : thinking
                ? "AI 正在回答…"
                : session.questions.some(
                      (q) => q.status === "pending_confirmation",
                    )
                  ? "英文草稿等待确认发送"
                  : pending.length ? `${pending.length} 个问题待补充` : "随时可以开始交流"}
          </span>
        </div>
        {replyWarning && <p role="status" className="border-b border-white/10 px-5 py-2 text-xs text-orange-300">{replyWarning}</p>}
        <div className="workspace-grid grid min-h-0 flex-1 lg:grid-cols-[minmax(0,1fr)_370px]">
          <section className="relative flex min-h-0 flex-col border-white/10 lg:border-r">
            <div className="contents" inert={callExpanded || undefined}>
            <Conversation
              key={session.id}
              session={session}
              lastEvent={lastEvent}
              autoPlaybackEnabled={autoPlaybackEnabled && !inCall}
              notify={setToast}
              refresh={refresh}
            />
            <div className="shrink-0 border-t border-white/10 bg-white/[.02] p-4 sm:p-5">
              {role === "talker" && !session.sample_loaded && !inCall && (
                <div className="mb-3 flex items-center justify-between gap-3">
                  <span className="text-[11px] text-white/40">
                    试一试：11 月试点发布准备度
                  </span>
                  <button
                    disabled={loadingSample}
                    onClick={sample}
                    className="flex items-center gap-1.5 text-[11px] text-ai disabled:opacity-40"
                  >
                    {loadingSample ? (
                      <LoaderCircle size={12} className="animate-spin" />
                    ) : (
                      <Sparkles size={12} />
                    )}
                    载入示例对话
                  </button>
                </div>
              )}
              {role === "talker" ? (
                <TalkerComposer
                  key={session.id}
                  session={session}
                  notify={setToast}
                  refresh={refresh}
                  callContainer={callContainer}
                  onExpandedChange={setCallExpanded}
                />
              ) : inCall ? (
                <CallPanel key={session.id} session={session} refresh={refresh} notify={setToast} />
              ) : (
                <ReviewComposer
                  key={session.id}
                  session={session}
                  selected={selected}
                  onClear={() => setSelectedIds([])}
                  refresh={refresh}
                  notify={setToast}
                />
              )}
            </div>
            </div>
            <div ref={setCallContainer} className="call-surface-host" />
          </section>
          <aside className="side-panel flex min-h-0 flex-col bg-white/[.018]">
            <div className="flex shrink-0 gap-1 border-b border-white/10 p-2">
              <button
                onClick={() => choosePanel("questions")}
                className={`flex flex-1 items-center justify-center gap-2 rounded-lg px-3 py-2 text-xs ${panel === "questions" ? "bg-white/10 text-white/80" : "text-white/40"}`}
              >
                <ListChecks size={14} />
                {inCall && role === "hinter" ? "通话监管" : "问题列表"}
                {pending.length > 0 && (
                  <span className="rounded-full bg-hinter/15 px-1.5 py-0.5 text-[9px] text-hinter">
                    {pending.length}
                  </span>
                )}
              </button>
              <button
                onClick={() => choosePanel("memory")}
                className={`flex flex-1 items-center justify-center gap-2 rounded-lg px-3 py-2 text-xs ${panel === "memory" ? "bg-white/10 text-white/80" : "text-white/40"}`}
              >
                <MemoryStick size={14} />
                Talker Wiki
              </button>
            </div>
            {panel === "memory" ? (
              <MemoryPanel session={session} refresh={refresh} />
            ) : inCall && role === "hinter" ? (
              <CallReviewPanel session={session} refresh={refresh} notify={setToast} />
            ) : (
              <QuestionPanel
                role={role}
                questions={session.questions}
                selectedIds={selectedIds}
                onToggle={toggle}
                onSelectAll={toggleAll}
                onReject={rejectQuestion}
              />
            )}
          </aside>
        </div>
      </div>
      {settingsOpen && (
        <SettingsDialog
          session={session}
          onClose={closeSettings}
          onNewSession={newSession}
          onSelectSession={selectSession}
          autoPlaybackEnabled={autoPlaybackEnabled}
          onAutoPlaybackChange={setAutoPlaybackEnabled}
        />
      )}
      {toast && (
        <div
          role="status"
          className="fixed bottom-6 left-1/2 z-[60] flex max-w-[90vw] -translate-x-1/2 items-center gap-2 rounded-xl border border-white/10 bg-[#171923] px-4 py-3 text-sm text-white/80 shadow-xl"
        >
          <Check size={16} className="shrink-0 text-talker" />
          {toast}
        </div>
      )}
    </div>
  );
}
