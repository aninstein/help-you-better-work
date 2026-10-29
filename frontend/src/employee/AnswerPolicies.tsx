import { useEffect, useRef, useState } from 'react';
import { request, send } from './api';
import { Notice } from './controls';

type Policy = { id: string; workspace_id: string; aliases: string[]; scope: 'talker'; enabled: boolean; version: number };

export default function AnswerPolicies({ workspaceId, workspaceName, refreshKey }: { workspaceId: string; workspaceName: string; refreshKey: string }) {
  const [items, setItems] = useState<Policy[]>([]), [topics, setTopics] = useState('');
  const [scope, setScope] = useState<'talker'>('talker');
  const [loading, setLoading] = useState(true), [busy, setBusy] = useState('');
  const [error, setError] = useState(''), [notice, setNotice] = useState('');
  const revision = useRef(0);
  const current = useRef(workspaceId); current.current = workspaceId;
  const draft = useRef(new Map<string, { topics: string; scope: 'talker' }>());
  const id = 'answer-policy-' + workspaceId;
  useEffect(() => {
    revision.current++;
    const controller = new AbortController(); const saved = draft.current.get(workspaceId);
    setTopics(saved?.topics || ''); setScope(saved?.scope || 'talker');
    setItems([]); setLoading(true); setError(''); setNotice(''); setBusy('');
    request<{ policies: Policy[] }>('/admin/workspaces/' + workspaceId + '/answer-policies', { signal: controller.signal })
      .then(value => setItems(value.policies)).catch(e => { if (!controller.signal.aborted) setError(e.message); })
      .finally(() => { if (!controller.signal.aborted) setLoading(false); });
    return () => controller.abort();
  }, [workspaceId, refreshKey]);
  function updateDraft(value: string, selected: 'talker') {
    setTopics(value); setScope(selected); draft.current.set(workspaceId, { topics: value, scope: selected });
  }
  async function save() {
    if (busy || !topics.trim()) return;
    const selected = workspaceId, started = revision.current; setBusy('create'); setError(''); setNotice('');
    try {
      const aliases = topics.split(/[,，、\n]/).map(v => v.trim()).filter(Boolean);
      const value = await send<Policy>('/admin/workspaces/' + selected + '/answer-policies', { aliases, scope });
      draft.current.delete(selected);
      if (current.current !== selected || revision.current !== started) return;
      setItems(previous => [value, ...previous.filter(item => item.id !== value.id)]); setTopics('');
      setNotice('已保存到该 Talker 的共享封禁记忆，对其全部会话生效。');
    } catch (e) { if (current.current === selected && revision.current === started) setError((e as Error).message); }
    finally { if (current.current === selected && revision.current === started) setBusy(''); }
  }
  async function toggle(item: Policy) {
    if (busy) return;
    const selected = workspaceId, started = revision.current; setBusy(item.id); setError(''); setNotice('');
    try {
      const value = await send<Policy>('/admin/answer-policies/' + item.id, { enabled: !item.enabled, expected_version: item.version }, 'PATCH');
      if (current.current !== selected || revision.current !== started) return;
      setItems(previous => previous.map(row => row.id === value.id ? value : row));
      setNotice(value.enabled ? '回答限制已启用。' : '回答限制已停用。');
    } catch (e) { if (current.current === selected && revision.current === started) setError((e as Error).message); }
    finally { if (current.current === selected && revision.current === started) setBusy(''); }
  }
  return <details className="employee-guidance">
    <summary>回答限制</summary>
    <p className="employee-help">该 Talker 的所有会话共享这些封禁。明确的“禁止回答某主题”提示会自动进入这里，也可以直接设置主题和别名。</p>
    {error && <Notice error>{error}</Notice>}{notice && <Notice>{notice}</Notice>}
    {loading ? <p role="status" className="employee-help">正在加载回答限制…</p> : items.length ?
      <ul className="employee-policy-list">{items.map(item => <li key={item.id}>
        <div><strong>{item.aliases.join(' / ')}</strong><p className="employee-help">{workspaceName + ' · 全部会话'} · {item.enabled ? '已启用' : '已停用'}</p></div>
        <button type="button" className="secondary-button" disabled={!!busy} aria-label={(item.enabled ? '停用' : '启用') + item.aliases[0] + '的回答限制'} onClick={() => toggle(item)}>{busy === item.id ? '保存中…' : item.enabled ? '停用' : '启用'}</button>
      </li>)}</ul> : <p className="employee-help">当前空间还没有回答限制。</p>}
    <form noValidate onSubmit={event => { event.preventDefault(); void save(); }}>
      <label className="field-label" htmlFor={id}>禁止回答的主题与别名</label>
      <textarea id={id} className="field resize-none" rows={2} value={topics} maxLength={2000} placeholder="主题名称、别名，或“禁止回答某主题”指令" onChange={event => updateDraft(event.target.value, scope)} />
      <p className="employee-help">同一主题的别名用逗号或换行分隔；任一别名命中即可限制回答。</p>
      <p className="employee-help">作用范围：{workspaceName}对应 Talker 的全部会话</p>

      <button className="secondary-button" disabled={!!busy || loading || !topics.trim()}>{busy === 'create' ? '保存中…' : '保存封禁记忆'}</button>
    </form>
  </details>;
}
