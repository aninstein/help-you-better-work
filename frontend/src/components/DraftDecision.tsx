import { useState } from "react";
import {
  Check,
  FilePenLine,
  LoaderCircle,
  Save,
  ShieldCheck,
  Sparkles,
  X,
} from "lucide-react";
import { api, post } from "../lib/api";
import type { Message, Session } from "../lib/types";

export default function DraftDecision({
  session,
  message,
  refresh,
  notify,
}: {
  session: Session;
  message: Message;
  refresh: () => Promise<void>;
  notify: (text: string) => void;
}) {
  const [busy, setBusy] = useState(false);
  const [mode, setMode] = useState<"edit" | "polish" | null>(null);
  const [text, setText] = useState("");
  const [hint, setHint] = useState("");
  const [revision, setRevision] = useState(message.revision || 1);
  const ready = message.status === "pending_confirmation";

  function open(next: "edit" | "polish") {
    setRevision(message.revision || 1);
    setText(message.text);
    setHint("");
    setMode(next);
  }
  async function save() {
    setBusy(true);
    try {
      if (mode === "edit")
        await api(`/sessions/${session.id}/answers/${message.id}/draft`, {
          method: "PUT",
          body: JSON.stringify({ text, revision }),
        });
      else
        await post(`/sessions/${session.id}/answers/${message.id}/polish`, {
          hint,
          revision,
        });
      setMode(null);
      await refresh();
      notify(
        mode === "edit"
          ? "草稿已更新，请审核后确认发送。"
          : "AI 润色完成，请审核新草稿后确认发送。",
      );
    } catch (e) {
      notify((e as Error).message);
    } finally {
      setBusy(false);
    }
  }
  async function decide(action: "confirm" | "cancel") {
    setBusy(true);
    try {
      await post(`/sessions/${session.id}/answers/${message.id}/decision`, {
        action,
        revision: message.revision || 1,
      });
      await refresh();
      notify(
        action === "confirm"
          ? "已确认发送，Talker 将收到英文答案并播放语音。"
          : "已取消发送，问题返回待审核。",
      );
    } catch (e) {
      notify((e as Error).message);
    } finally {
      setBusy(false);
    }
  }
  return (
    <div
      className="mt-4 rounded-xl border border-hinter/30 bg-hinter/5 p-3"
      data-testid="draft-decision"
    >
      <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
        <p className="flex items-center gap-2 text-xs text-hinter">
          <ShieldCheck size={14} />
          {ready
            ? "英文预回答 · 仅 Hinter 可见，确认前不会发送"
            : message.status === "polishing"
              ? "AI 正在按 Hint 润色 · 原草稿未发送"
              : "草稿生成中 · 仅 Hinter 可见"}
        </p>
        <div className="flex gap-1">
          <button
            className="icon-button !h-8 !w-8"
            aria-label="编辑 AI 草稿"
            title="编辑草稿"
            disabled={busy || !ready}
            onClick={() => open("edit")}
          >
            <FilePenLine size={16} />
          </button>
          <button
            className="icon-button !h-8 !w-8 !text-ai"
            aria-label="AI 润色草稿"
            title="输入 Hint 润色"
            disabled={busy || !ready}
            onClick={() => open("polish")}
          >
            <Sparkles size={16} />
          </button>
        </div>
      </div>
      {mode && (
        <div className="mb-3 space-y-2 rounded-lg border border-white/10 bg-black/15 p-3">
          <label className="field-label">
            {mode === "edit"
              ? "编辑将要发布的英文内容"
              : "输入私有 Hint，让 AI 润色这条预回答"}
            <textarea
              aria-label={
                mode === "edit" ? "编辑 AI 预回答内容" : "润色草稿的 Hint"
              }
              value={mode === "edit" ? text : hint}
              onChange={(e) =>
                mode === "edit"
                  ? setText(e.target.value)
                  : setHint(e.target.value)
              }
              rows={mode === "edit" ? 7 : 3}
              className="field resize-none"
            />
          </label>
          <div className="flex gap-2">
            <button
              className="primary-button"
              disabled={
                busy || !ready || !(mode === "edit" ? text.trim() : hint.trim())
              }
              onClick={save}
            >
              {busy ? (
                <LoaderCircle size={14} className="animate-spin" />
              ) : mode === "edit" ? (
                <Save size={14} />
              ) : (
                <Sparkles size={14} />
              )}
              {mode === "edit" ? "保存草稿" : "按 Hint 润色"}
            </button>
            <button
              className="secondary-button"
              disabled={busy}
              onClick={() => setMode(null)}
            >
              取消修改
            </button>
          </div>
        </div>
      )}
      <div className="flex gap-2">
        <button
          className="primary-button !bg-hinter"
          disabled={busy || !ready || mode !== null}
          onClick={() => decide("confirm")}
          aria-label="确认发送 AI 答案"
        >
          {busy ? (
            <LoaderCircle size={14} className="animate-spin" />
          ) : (
            <Check size={14} />
          )}
          确认发送
        </button>
        <button
          className="secondary-button"
          disabled={busy}
          onClick={() => decide("cancel")}
          aria-label="取消发送 AI 答案"
        >
          <X size={14} />
          取消发送
        </button>
      </div>
    </div>
  );
}
