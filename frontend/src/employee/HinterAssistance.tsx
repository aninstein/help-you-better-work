import { useEffect, useId, useRef, useState } from 'react';
import { Bell, ChevronRight, LoaderCircle, X } from 'lucide-react';
import { request } from './api';
import { Notice } from './controls';

export type Assistance = {
  id: string; thread_id: string; workspace_id: string; source_record_id: string;
  summary: string; original_text: string; needed_information?: string; owner_name?: string;
  status: 'pending' | 'provided' | 'declined' | 'superseded'; version: number; continuation_status?: string;
};
type RequestList = { requests: Assistance[]; total: number };

function useRequests(endpoint: string, enabled = true, refreshKey = 0) {
  const [data, setData] = useState<RequestList>({ requests: [], total: 0 });
  const [error, setError] = useState(''), [loading, setLoading] = useState(true), [revision, setRevision] = useState(0);
  const scope = useRef(endpoint);
  useEffect(() => {
    if (!enabled) return;
    const controller = new AbortController(); let timer: ReturnType<typeof setTimeout>;
    if (scope.current !== endpoint) { setData({ requests: [], total: 0 }); scope.current = endpoint; } setLoading(true);
    async function load() {
      try {
        const value = await request<RequestList>(endpoint, { signal: controller.signal });
        if (!controller.signal.aborted) { setData(value); setError(''); }
      } catch (e) { if (!controller.signal.aborted) setError((e as Error).message); }
      finally { if (!controller.signal.aborted) { setLoading(false); timer = setTimeout(load, 5000); } }
    }
    void load();
    return () => { controller.abort(); clearTimeout(timer); };
  }, [endpoint, enabled, revision, refreshKey]);
  return { data, error, loading, reload: () => setRevision(value => value + 1) };
}

export function useAssistanceDetail(id: string | undefined) {
  const [item, setItem] = useState<Assistance | null>(null), [loading, setLoading] = useState(false), [error, setError] = useState(''), [revision, setRevision] = useState(0);
  useEffect(() => {
    setItem(null); setError(''); setLoading(!!id);
    if (!id) return;
    const controller = new AbortController();
    request<Assistance>('/assistance-requests/' + id, { signal: controller.signal })
      .then(value => { if (!controller.signal.aborted) setItem(value); })
      .catch(e => { if (!controller.signal.aborted) setError((e as Error).message); })
      .finally(() => { if (!controller.signal.aborted) setLoading(false); });
    return () => controller.abort();
  }, [id, revision]);
  return { item: item?.id === id ? item : null, loading, error, reload: () => setRevision(value => value + 1) };
}

export function HinterBadge() {
  const { data, error } = useRequests('/admin/assistance-requests?page_size=1');
  if (error) return <span className="employee-help" aria-label="待补充数量暂不可用"> · —</span>;
  return data.total ? <span className="employee-hinter-badge" aria-live="polite" aria-label={`${data.total} 项待补充`}>{data.total}</span> : null;
}

export function HinterInbox({ onOpen, selectedId, refreshKey = 0, disabled = false }: {
  onOpen: (item: Assistance) => void; selectedId?: string; refreshKey?: number; disabled?: boolean;
}) {
  const [page, setPage] = useState(1);
  const { data, error, loading, reload } = useRequests('/admin/assistance-requests?page=' + page, true, refreshKey);
  useEffect(() => { if (!loading && page > 1 && (page - 1) * 20 >= data.total) setPage(Math.max(1, Math.ceil(data.total / 20))); }, [data.total, loading, page]);
  return <section className="employee-hinter-inbox" aria-label="Hinter 待补充事项" aria-busy={loading}>
    <h2><Bell size={15} />待补充 <small>{data.total}</small></h2>
    {error && <Notice error>{error}<button className="employee-text-button" onClick={reload}>重新加载</button></Notice>}
    {loading ? <p className="employee-help" role="status">正在加载待补充事项…</p> : !data.requests.length && !error ? <p className="employee-help">暂无待补充事项。Agent 遇到缺少内部资料的请求时会在这里提醒。</p> : null}
    {data.requests.map(item => <button className="employee-hinter-inbox-item" key={item.id} aria-pressed={selectedId === item.id} disabled={disabled} onClick={() => onOpen(item)}>
      <span>{item.summary}<small>{item.owner_name}</small></span><ChevronRight size={14} />
    </button>)}
    {data.total > 20 && <div className="employee-hinter-pages"><button disabled={page === 1 || loading} onClick={() => setPage(value => value - 1)}>上一页</button><span>{page} / {Math.ceil(data.total / 20)}</span><button disabled={page * 20 >= data.total || loading} onClick={() => setPage(value => value + 1)}>下一页</button></div>}
  </section>;
}

export function AssistanceBrief({ selected, item, loading, error, busy, canDecline, onCancel, onReload, onDecline }: {
  selected: Assistance; item: Assistance | null; loading: boolean; error: string; busy: boolean; canDecline: boolean;
  onCancel: () => void; onReload: () => void; onDecline: () => void;
}) {
  const id = useId();
  return <section className="employee-assistance-context" aria-labelledby={id} aria-busy={loading || busy}>
    <header><h2 id={id}><Bell size={15} />正在为 {item?.owner_name || selected.owner_name || '该 Talker'} 补充资料</h2><button type="button" className="icon-button" aria-label="退出补充，恢复聊天输入" disabled={busy} onClick={onCancel}><X size={16} /></button></header>
    {loading ? <p className="employee-help" role="status"><LoaderCircle size={14} className="animate-spin" />正在加载事项详情…</p> : error ? <Notice error>{error}<button type="button" className="employee-text-button" disabled={busy} onClick={onReload}>重新加载事项</button></Notice> : item && <>
      <div className="employee-assistance-brief" tabIndex={0} aria-label="补充事项说明">
        <section><h3>用户原始问题</h3><p>{item.original_text}</p></section>
        <section><h3>AI 需要你补充的内容</h3><p>{item.needed_information || item.summary}</p></section>
        <section><h3>提交后会进行的操作</h3><p>补充内容会公开给该 Talker，并保存到其 Wiki，供其全部会话召回。Agent 将结合资料继续回答原问题，并遵守现有回答限制。</p></section>
      </div>
      {item.status === 'pending' ? <div className="employee-assistance-context-footer"><span>直接在下方输入框填写允许披露的资料；退出补充会保留草稿。</span><button type="button" className="employee-text-button" disabled={busy || !canDecline} onClick={onDecline}>暂不提供</button></div> : <p className="employee-help" role="status">{item.status === 'provided' ? '本事项已公开补充。' : item.status === 'declined' ? '本事项已标记暂不提供。' : '原问题已修订，请重新选择。'}{['failed', 'cancelled'].includes(item.continuation_status || '') ? '续答未完成，可点击下方按钮重新继续回答。' : '请退出补充或选择其他事项。'}</p>}
    </>}
  </section>;
}

export default function AssistancePanel({ threadId, canResolve, onSelect, refreshKey = 0, disabled = false }: {
  threadId: string; canResolve: boolean; onSelect: (item: Assistance) => void; refreshKey?: number; disabled?: boolean;
}) {
  const { data, error, reload } = useRequests('/threads/' + threadId + '/assistance-requests', !!threadId, refreshKey);
  if (!threadId || (!data.requests.length && !error)) return null;
  return <section className="employee-assistance" aria-label="人工补充事项">
    <h2>Hinter 协作</h2>
    {error && <Notice error>{error}<button className="employee-text-button" onClick={reload}>重新加载</button></Notice>}
    {data.requests.map(item => <article className="employee-assistance-card" key={item.id}>
      <header><Bell size={15} /><strong>{{ pending: '等待 Hinter 补充', provided: 'Hinter 已公开补充', declined: 'Hinter 暂未提供资料', superseded: '原问题已修订' }[item.status]}</strong></header>
      {canResolve && item.owner_name && <p className="employee-help">用户：{item.owner_name}</p>}
      {canResolve ? <button type="button" className="employee-assistance-question" disabled={disabled} onClick={() => onSelect(item)}>{item.original_text}<ChevronRight size={15} /></button> : <p>{item.summary}</p>}
      {item.continuation_status && <p className="employee-help">{({ queued: '已排队继续回答', running: 'Agent 正在继续回答', completed: '续答已完成', failed: '续答未完成，公开补充已保留', cancelled: '续答已停止，公开补充已保留' } as Record<string, string>)[item.continuation_status]}</p>}
    </article>)}
  </section>;
}
