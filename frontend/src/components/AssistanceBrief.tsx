import type { AssistanceRequest } from "../lib/types";

export default function AssistanceBrief({ request }: { request?: AssistanceRequest }) {
  if (!request) return null;
  return <div className="mt-2 border-l-2 border-hinter/30 pl-3 text-[11px] leading-6">
    <p className="text-white/60">{request.question_summary}</p>
    <p className="text-hinter/85"><span className="font-medium">需要你补充：</span>{request.information_needed}</p>
    <p className="text-white/35">AI 会结合原问题，将补充内容组织成英文回答。</p>
  </div>;
}
