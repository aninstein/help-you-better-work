import { useEffect, useRef, useState } from "react";
import { LoaderCircle, Trash2 } from "lucide-react";
import { api } from "../lib/api";
import type { Talker } from "../lib/types";

export default function DeleteTalkerDialog({ talker, onClose, onDeleted }: {
  talker: Talker;
  onClose: () => void;
  onDeleted: (id: string, remaining: Talker[]) => void;
}) {
  const dialog = useRef<HTMLDialogElement>(null);
  const cancel = useRef<HTMLButtonElement>(null);
  const submitting = useRef(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  useEffect(() => {
    const element = dialog.current!;
    element.showModal();
    cancel.current?.focus();
    return () => element.close();
  }, []);
  async function remove() {
    if (submitting.current) return;
    submitting.current = true;
    setBusy(true); setError("");
    try {
      const result = await api<{ items: Talker[] }>(`/talkers/${encodeURIComponent(talker.id)}`, { method: "DELETE" });
      onDeleted(talker.id, result.items);
    } catch (e) {
      setError((e as Error).message);
      submitting.current = false;
      setBusy(false);
    }
  }
  return <dialog ref={dialog} aria-labelledby="delete-talker-title" aria-describedby="delete-talker-description"
    onCancel={event => { event.preventDefault(); if (!busy) onClose(); }}
    className="m-auto max-h-[calc(100dvh_-_2rem)] w-[calc(100%_-_2rem)] max-w-md overflow-y-auto rounded-2xl border border-red-400/25 bg-[#11141f] p-6 text-white backdrop:bg-black/75 backdrop:backdrop-blur-sm">
    <h2 id="delete-talker-title" className="flex items-center gap-2 text-base text-red-300"><Trash2 size={18} />删除 Talker</h2>
    <p id="delete-talker-description" className="mt-4 break-words text-sm leading-7 text-white/70">确定删除 <strong className="text-white">{talker.name}</strong>？该 Talker 的 {talker.session_count} 个会话、相关音频以及全部 Wiki（公开记忆和私有提示）将一并永久删除，无法恢复。</p>
    {talker.sessions.some(session => session.call_active) && <p className="mt-3 text-xs leading-6 text-hinter">此 Talker 正在通话，请先挂断后再删除。</p>}
    {error && <p role="alert" className="mt-3 text-xs leading-6 text-red-300">{error}</p>}
    <div className="mt-5 flex flex-wrap justify-end gap-2">
      <button ref={cancel} className="secondary-button" disabled={busy} onClick={onClose}>取消</button>
      <button className="primary-button !bg-red-400" disabled={busy || talker.sessions.some(session => session.call_active)} aria-busy={busy} onClick={() => void remove()}>
        {busy ? <LoaderCircle size={14} className="animate-spin" /> : <Trash2 size={14} />}删除 Talker
      </button>
    </div>
  </dialog>;
}
