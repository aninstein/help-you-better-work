import { useEffect, useState } from "react";
import { LoaderCircle, Settings2, X } from "lucide-react";
import { api } from "../lib/api";
import type { Session } from "../lib/types";

interface Props {
  session: Session;
  onClose: () => void;
  onNewSession: (id: string, name: string, language: string) => Promise<void>;
  onSelectSession: (id: string) => void;
  autoPlaybackEnabled: boolean;
  onAutoPlaybackChange: (enabled: boolean) => void;
}

export default function SettingsDialog({
  session,
  onClose,
  onNewSession,
  onSelectSession,
  autoPlaybackEnabled,
  onAutoPlaybackChange,
}: Props) {
  const [sessions, setSessions] = useState<
    { id: string; created_at: string }[]
  >([]);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");
  useEffect(() => {
    void api<{ items: typeof sessions }>(`/sessions?talker_id=${session.talker_id}`).then((data) =>
      setSessions(data.items),
    ).catch(e => setMessage((e as Error).message));
    const key = (event: KeyboardEvent) => {
      if (event.key === "Escape") onClose();
    };
    window.addEventListener("keydown", key);
    return () => window.removeEventListener("keydown", key);
  }, [onClose, session.talker_id]);
  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/70 px-4 backdrop-blur-sm"
      onClick={onClose}
    >
      <section
        role="dialog"
        aria-modal="true"
        aria-label="会话设置"
        className="w-full max-w-lg rounded-3xl border border-white/10 bg-[#11141f] p-6"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="mb-5 flex items-center justify-between">
          <h2 className="flex items-center gap-2 text-lg">
            <Settings2 size={20} className="text-ai" />
            会话设置
          </h2>
          <button
            onClick={onClose}
            className="icon-button"
            aria-label="关闭设置"
          >
            <X size={19} />
          </button>
        </div>
        <p className="mb-4 text-xs leading-6 text-white/45">
          {session.talker_name} 的会话独立保存，其 Wiki 跨会话共享。
        </p>
        <p className="mb-4 rounded-xl border border-ai/20 bg-ai/5 p-3 text-xs text-white/60">
          AI 答案与语音固定使用英文（English）。
        </p>
        <label className="mb-5 flex cursor-pointer items-center gap-3 rounded-xl border border-white/10 bg-white/[.02] p-3 text-sm text-white/75">
          <input
            type="checkbox"
            checked={autoPlaybackEnabled}
            onChange={(event) => onAutoPlaybackChange(event.target.checked)}
          />
          <span className="flex-1">自动播放新语音</span>
          <span className="text-xs text-white/40">全局设置</span>
        </label>
        <button
          className="primary-button mt-4"
          disabled={busy}
          onClick={async () => {
            setBusy(true);
            try {
              await onNewSession(
                session.customer_id,
                session.customer_name,
                "en-US",
              );
              onClose();
            } catch (e) {
              setMessage((e as Error).message);
            } finally {
              setBusy(false);
            }
          }}
        >
          {busy && <LoaderCircle size={14} className="animate-spin" />}
          创建新对话
        </button>
        <label className="field-label mt-5">
          已有会话
          <select
            className="field"
            value={session.id}
            onChange={(e) => {
              onSelectSession(e.target.value);
              onClose();
            }}
          >
            {sessions.map((item) => (
              <option key={item.id} value={item.id}>
                {new Date(item.created_at).toLocaleString()} ·{" "}
                {item.id.slice(0, 8)}
              </option>
            ))}
          </select>
        </label>
        {message && (
          <p role="alert" className="mt-4 text-sm text-orange-300">
            {message}
          </p>
        )}
      </section>
    </div>
  );
}
