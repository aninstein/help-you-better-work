import { useEffect, useRef, useState } from 'react';
import { AudioLines, LogOut, MessageSquare, ShieldCheck, Users } from 'lucide-react';
import { ApiError, onExpired, request, send, setSession } from './employee/api';
import type { Account } from './employee/api';
import AuthScreen from './employee/AuthScreen';
import AdminPanel from './employee/AdminPanel';
import Workspace from './employee/Workspace';
import { HinterBadge } from './employee/HinterAssistance';
import { Notice } from './employee/controls';
import './employee/employee.css';
export type { Role } from './lib/types';
function permittedView(view:string,account:Account){
  return (view==='hinter'||view==='users')&&account.capabilities.includes('workspace.supervise')?view:'chat';
}
export default function App() {
  const [account,setAccount]=useState<Account|null>(null), [loading,setLoading]=useState(true), [error,setError]=useState(''), [expired,setExpired]=useState(false);
  const [view,setView]=useState(new URLSearchParams(location.search).get('view')||'chat');
  const drafts=useRef(new Map<string,string>()), channel=useRef<BroadcastChannel|null>(null), lastIdentity=useRef<string|null>(null);
  const accept=(value:Account|null)=>{if(value){if(lastIdentity.current&&lastIdentity.current!==value.user.id)navigate('chat',true);lastIdentity.current=value.user.id;setExpired(false);}setSession(value);setAccount(value);};
  const currentView=account?permittedView(view,account):view;
  useEffect(()=>{
    let active=true;
    const check=()=>request<Account>('/auth/me').then(a=>{if(active){accept(a);setError('');}}).catch(e=>{if(!active)return;if(e instanceof ApiError&&e.status===401){accept(null);}else setError(e.message);}).finally(()=>{if(active)setLoading(false);});
    onExpired(()=>{accept(null);setExpired(true);});
    check();const timer=setInterval(check,15000);window.addEventListener('focus',check);
    channel.current=new BroadcastChannel('duet-auth');channel.current.onmessage=check;
    return()=>{active=false;clearInterval(timer);window.removeEventListener('focus',check);channel.current?.close();};
  },[]);
  useEffect(()=>{document.title=account?currentView==='users'?'用户与权限 · Duet':currentView==='hinter'?'Hinter 工作台 · Duet':'个人员工 · Duet':'登录 · Duet';},[account,currentView]);
  function navigate(next:string,replace=false){setView(next);const p=new URLSearchParams(location.search);p.set('view',next);p.delete('q');p.delete('page');p.delete('thread');history[replace?'replaceState':'pushState'](null,'','?'+p);}
  useEffect(()=>{if(account&&view!==currentView)navigate(currentView,true);},[account?.user.id,view,currentView]);
  useEffect(()=>{const pop=()=>setView(new URLSearchParams(location.search).get('view')||'chat');window.addEventListener('popstate',pop);return()=>window.removeEventListener('popstate',pop);},[]);
  async function logout(){try{await send('/auth/logout',{});accept(null);channel.current?.postMessage('logout');}catch(e){setError((e as Error).message);}}
  const admin=account?.capabilities.includes('workspace.supervise');
  return <main className="app-bg min-h-screen text-white selection:bg-ai/30">{loading?<div className="employee-start" role="status">正在连接你的工作空间…</div>:!account?<>{error&&<Notice error>{error}</Notice>}<AuthScreen expired={expired} onLogin={value=>{navigate('chat',true);accept(value);channel.current?.postMessage('login');}}/></>:account.must_change_password?<>{error&&<Notice error>{error}</Notice>}<AuthScreen changeAccount={account} onLogin={accept} onLogout={logout}/></>:<><header className="employee-topbar"><a className="employee-brand" href="/" onClick={e=>{e.preventDefault();navigate('chat');}}><AudioLines size={23}/>Duet<span>个人员工</span></a><nav aria-label="工作台"><button aria-current={currentView==='chat'?'page':undefined} onClick={()=>navigate('chat')}><MessageSquare size={16}/>我的聊天</button>{admin&&<><button aria-current={currentView==='hinter'?'page':undefined} onClick={()=>navigate('hinter')}><ShieldCheck size={16}/>Hinter<HinterBadge key={account.user.id}/></button><button aria-current={currentView==='users'?'page':undefined} onClick={()=>navigate('users')}><Users size={16}/>用户权限</button></>}</nav><div className="employee-account"><span>{account.user.display_name}<small>{admin?'Talker · Hinter 管理员':'Talker · 已登录'}</small></span><button className="icon-button" aria-label="退出登录" onClick={logout}><LogOut size={18}/></button></div></header>{error&&<Notice error>{error}</Notice>}{currentView==='users'?<AdminPanel/>:<Workspace key={account.user.id+':'+currentView} account={account} supervise={currentView==='hinter'} drafts={drafts.current}/>}</>}</main>;
}
