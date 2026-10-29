import { useState } from "react";
import { AlertTriangle, LoaderCircle, Trash2, X } from "lucide-react";
import type { Session } from "../lib/types";
import { post } from "../lib/api";

const phrase = "我已确认删除所有的Wiki信息";

export default function ClearWikiDialog({
  session,
  onClose,
  onCleared,
}: {
  session: Session;
  onClose: () => void;
  onCleared: () => Promise<void>;
}) {
  const [confirmation, setConfirmation] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  async function remove() {
    setBusy(true);
    try {
      await post(`/wiki/clear?session_id=${session.id}`, { confirmation });
      await onCleared();
      onClose();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }
  return (
    <div className="fixed inset-0 z-[70] flex items-center justify-center bg-black/80 px-4 backdrop-blur-sm">
      <section
        role="dialog"
        aria-modal="true"
        aria-label="二次确认删除全部 Wiki"
        className="w-full max-w-xl rounded-3xl border border-red-400/30 bg-[#15141c] p-6 shadow-2xl"
      >
        <div className="mb-4 flex items-center justify-between">
          <h2 className="flex items-center gap-2 text-lg text-red-300">
            <AlertTriangle size={20} />
            删除全部 Wiki 信息
          </h2>
          <button
            className="icon-button"
            disabled={busy}
            onClick={onClose}
            aria-label="关闭 Wiki 删除确认"
          >
            <X size={19} />
          </button>
        </div>
        <p className="mb-3 text-sm leading-7 text-white/70">
          此操作会永久清空 {session.talker_name} 的全部公开记忆与私有提示
           Wiki：中心文档内容、主题记忆和历史轮次文档。保留空白中心文档供后续使用。聊天界面和音频不删除，已删除的记忆不会自动恢复。
        </p>
        <p className="mb-4 text-sm leading-6 text-red-300">
          此操作不可撤销。请完整输入：
          <strong className="block select-all">{phrase}</strong>
        </p>
        <input
          aria-label="输入 Wiki 全部删除确认语句"
          value={confirmation}
          onChange={(e) => setConfirmation(e.target.value)}
          className="field mb-4"
          autoComplete="off"
          placeholder={phrase}
        />
        {error && (
          <p role="alert" className="mb-3 text-sm text-red-300">
            {error}
          </p>
        )}
        <div className="flex justify-end gap-2">
          <button
            className="secondary-button"
            disabled={busy}
            onClick={onClose}
          >
            取消
          </button>
          <button
            className="primary-button !bg-red-400"
            disabled={busy || confirmation !== phrase}
            onClick={remove}
            aria-label="永久删除全部 Wiki"
          >
            {busy ? (
              <LoaderCircle size={14} className="animate-spin" />
            ) : (
              <Trash2 size={14} />
            )}
            永久删除全部 Wiki
          </button>
        </div>
      </section>
    </div>
  );
}
