import { useEffect, useState } from 'react';
import { Search, X } from 'lucide-react';
import { request, send } from './api';
import type { UserRow } from './api';
import { ConfirmDialog, Notice } from './controls';
export default function AdminPanel() {
  const initial=new URLSearchParams(location.search);
  const [query,setQuery]=useState(initial.get('q')||''), [page,setPage]=useState(Number(initial.get('page'))||1), [data,setData]=useState<{users:UserRow[];total:number}>({users:[],total:0}), [loading,setLoading]=useState(false), [error,setError]=useState(''), [notice,setNotice]=useState(''), [revision,setRevision]=useState(0), [composing,setComposing]=useState(false);
  const [target,setTarget]=useState<{user:UserRow;kind:'role'|'status'}|null>(null), [saving,setSaving]=useState(false), [dialogError,setDialogError]=useState('');
  useEffect(()=>{
    if(composing)return;
    const controller=new AbortController();
    const timer=setTimeout(()=>{
      const params=new URLSearchParams(location.search);params.set('view','users');params.set('q',query);params.set('page',String(page));history.replaceState(null,'','?'+params);
      setLoading(true);setError('');
      request<{users:UserRow[];total:number}>('/admin/users?q='+encodeURIComponent(query)+'&page='+page,{signal:controller.signal}).then(value=>{if(!controller.signal.aborted)setData(value);}).catch(e=>{if(!controller.signal.aborted)setError(e.message);}).finally(()=>{if(!controller.signal.aborted)setLoading(false);});
    },300);
    return()=>{clearTimeout(timer);controller.abort();};
  },[query,page,revision,composing]);
  async function save() {
    if(!target||saving)return;setSaving(true);setDialogError('');
    const admin=target.user.roles.includes('hinter_admin');
    try {
      if(target.kind==='role')await send('/admin/users/'+target.user.id+'/roles',{roles:admin?['talker']:['talker','hinter_admin'],expected_version:target.user.version},'PUT');
      else await send('/admin/users/'+target.user.id+'/status',{status:target.user.status==='active'?'disabled':'active',expected_version:target.user.version},'PATCH');
      setTarget(null);setRevision(x=>x+1);setNotice('权限已更新；该用户的旧登录会话已失效。');
    } catch(e){setDialogError((e as Error).message);}finally{setSaving(false);}
  }
  return <section className="employee-admin"><div className="employee-section-heading"><div><span className="employee-eyebrow">HINTER 工作台</span><h1>用户与权限</h1><p>指定哪些注册用户可以监管工作空间、提供提示和管理权限。</p></div></div><div className="employee-search"><Search size={17}/><input aria-label="搜索用户" value={query} onCompositionStart={()=>setComposing(true)} onCompositionEnd={()=>setComposing(false)} onChange={e=>{setQuery(e.target.value);setPage(1);}} placeholder="搜索名称、手机或邮箱"/>{query&&<button className="icon-button" aria-label="清空搜索" onClick={()=>{setQuery('');setPage(1);}}><X size={16}/></button>}</div>{notice&&<Notice>{notice}</Notice>}{error&&<Notice error>{error}<button className="secondary-button" onClick={()=>setRevision(x=>x+1)}>重试</button></Notice>}<div className="employee-table-frame" aria-busy={loading}><table><thead><tr><th>用户</th><th>联系方式</th><th>角色 / 状态</th><th>管理操作</th></tr></thead><tbody>{data.users.map(user=><tr key={user.id}><td><strong>{user.display_name}</strong><small>{user.id.slice(0,18)}…</small></td><td>{user.phone||'—'}<small>{user.email||'未设置邮箱'}</small></td><td><span className={user.roles.includes('hinter_admin')?'employee-role admin':'employee-role'}>{user.roles.includes('hinter_admin')?'Hinter 管理员':'Talker'}</span><small>{user.status==='active'?'可用':'已停用'}</small></td><td><div className="employee-row-actions"><button className="secondary-button" onClick={()=>{setTarget({user,kind:'role'});setDialogError('');}}>{user.roles.includes('hinter_admin')?'撤销管理权限':'授予管理权限'}</button><button className="employee-text-button" onClick={()=>{setTarget({user,kind:'status'});setDialogError('');}}>{user.status==='active'?'停用':'启用'}</button></div></td></tr>)}</tbody></table>{!data.users.length&&<div className="employee-empty">{loading?'正在加载用户…':query?'没有匹配用户，可清空搜索再试。':'还没有注册用户。'}</div>}</div><footer className="employee-pagination"><span role="status">{loading?'更新中 · ':''}共 {data.total} 位用户 · 第 {page} 页</span><button className="secondary-button" disabled={page<=1||loading} onClick={()=>setPage(x=>x-1)}>上一页</button><button className="secondary-button" disabled={page*20>=data.total||loading} onClick={()=>setPage(x=>x+1)}>下一页</button></footer>{target&&<ConfirmDialog title={target.kind==='role'?'变更 Hinter 权限':'变更账号状态'} busy={saving} error={dialogError} action="确认变更" onCancel={()=>setTarget(null)} onConfirm={save}><p>用户：<strong>{target.user.display_name}</strong></p><p>{target.kind==='role'?(target.user.roles.includes('hinter_admin')?'撤销后，该用户将不能监管其他用户或管理权限。':'授予后，该用户可以监管全部工作空间，并管理用户权限。'):(target.user.status==='active'?'停用后，该用户将不能登录，正在运行的任务会停止。':'启用后，该用户可以重新登录。')}</p><p>变更会让该用户的旧登录会话失效。系统会保护最后一位可用管理员。</p></ConfirmDialog>}</section>;
}
