import { useState } from "react";
import { BookOpen, FilePenLine, FileText } from "lucide-react";
import type { Session } from "../lib/types";
import WikiEditor, { WikiText } from "./WikiEditor";

export default function MemoryPanel({
  session,
  refresh,
}: {
  session: Session;
  refresh: () => Promise<void>;
}) {
  const [editing, setEditing] = useState(false);
  const [initialPath, setInitialPath] = useState("context.md");
  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <div className="border-b border-white/10 p-5">
        <div className="flex items-center gap-3">
          <div className="flex h-10 w-10 items-center justify-center rounded-xl border border-ai/20 bg-ai/10 text-ai">
            <BookOpen size={18} />
          </div>
          <div>
            <h2 className="text-sm font-medium">
              {session.talker_name} 的 Wiki
            </h2>
            <p className="mt-1 text-[11px] text-white/40">
              {session.wiki.documents.length} 篇文档 · 此 Talker 跨会话共享
            </p>
          </div>
        </div>
        <button
          onClick={() => {
            setInitialPath("context.md");
            setEditing(true);
          }}
          className="secondary-button mt-4 w-full"
          aria-label="编辑 Wiki 文档"
        >
          <FilePenLine size={15} />
          编辑 Wiki 文档
        </button>
      </div>
      <div className="scrollbar-none flex-1 overflow-y-auto p-5">
        <div className="mb-4 flex items-center gap-2 text-xs text-ai">
          <FileText size={14} />
          context.md
        </div>
        <WikiText
          content={session.wiki.context}
          onLink={(path) => {
            setInitialPath(path);
            setEditing(true);
          }}
        />
      </div>
      {editing && (
        <WikiEditor
          session={session}
          initialPath={initialPath}
          refresh={refresh}
          onClose={() => setEditing(false)}
        />
      )}
    </div>
  );
}
