import { useEffect, useRef, useState } from 'react';
import ReactMarkdown from 'react-markdown';
import { BookOpen, Check, ChevronRight, CircleStop, FileQuestion, LoaderCircle, MessageSquare, Mic, Phone, PhoneOff, Plus, Send, Volume2, Pencil } from 'lucide-react';
import { request, send, terminal } from './api';
import type { Account, Projection, RecordItem, Run, RunEvent, Thread, Workspace as WorkspaceType } from './api';
import { Notice } from './controls';
import { useMeetingVoice, useSpeechStream } from './useMeetingVoice';
import MeetingCall from './MeetingCall';
import LegacyBinding from './LegacyBinding';
import AnswerPolicies from './AnswerPolicies';
import AssistancePanel, { AssistanceBrief, HinterInbox, useAssistanceDetail } from './HinterAssistance';
import type { Assistance } from './HinterAssistance';

type MemoryDoc = {id:string;name:string;text:string;revision:number;visibility:string;created_at?:number;metadata?:{origin?:string;title?:string}};
export default function Workspace({account,supervise,drafts}: {account:Account;supervise:boolean;drafts:Map<string,string>}) {
  const [spaces,setSpaces]=useState<WorkspaceType[]>([]), [space,setSpace]=useState(account.default_workspace_id), [threadList,setThreadList]=useState<Thread[]>([]), [thread,setThread]=useState(''), [records,setRecords]=useState<RecordItem[]>([]), [runs,setRuns]=useState<Run[]>([]), [projection,setProjection]=useState<Projection[]>([]), [live,setLive]=useState<Record<string,string>>({}), [tools,setTools]=useState<Record<string,string>>({});
  const [draft,setDraft]=useState(''), [sending,setSending]=useState(false), [loading,setLoading]=useState(true), [error,setError]=useState(''), [side,setSide]=useState<'questions'|'memory'>('questions'), [docs,setDocs]=useState<MemoryDoc[]>([]), [memoryText,setMemoryText]=useState(''), [memoryBusy,setMemoryBusy]=useState(false), [guidance,setGuidance]=useState(''), [shared,setShared]=useState(false), [notice,setNotice]=useState(''), [playback,setPlayback]=useState(''), [speechBusy,setSpeechBusy]=useState(''), [editing,setEditing]=useState(''), [correction,setCorrection]=useState(''), [correcting,setCorrecting]=useState(false);
  const scroll=useRef<HTMLDivElement>(null), follow=useRef(true), input=useRef<HTMLTextAreaElement>(null), pendingKey=useRef<{text:string;key:string;thread:string;kind:'text'|'transcript'}|null>(null), audio=useRef<HTMLAudioElement|null>(null), generation=useRef(0), playbackRequest=useRef(0), spoken=useRef(new Set<string>()), callWasActive=useRef(false);
  const [callHost,setCallHost]=useState<HTMLDivElement|null>(null);
  const [acknowledgements,setAcknowledgements]=useState<Record<string,string>>({});
  const callButton=useRef<HTMLButtonElement>(null);
  const [preparingCall,setPreparingCall]=useState(false), [callToStart,setCallToStart]=useState<{space:string;thread:string}|null>(null);
  const callIntent=useRef<{space:string;controller:AbortController}|null>(null);
  function cancelCallPreparation(){callIntent.current?.controller.abort();callIntent.current=null;setPreparingCall(false);setCallToStart(null);}
  function stopReplyAudio(){const player=audio.current;audio.current=null;if(player){player.onended=null;player.onerror=null;player.onplaying=null;player.pause();player.removeAttribute('src');player.load();}setPlayback('');setSpeechBusy('');}
  function hangup(){playbackRequest.current++;stopReplyAudio();call.stop();requestAnimationFrame(()=>callButton.current?.focus());}
  const currentThread=useRef(thread);currentThread.current=thread;
  const [assistanceTarget,setAssistanceTarget]=useState<Assistance|null>(null), [assistanceRefresh,setAssistanceRefresh]=useState(0), [assistanceError,setAssistanceError]=useState('');
  const assistanceDetail=useAssistanceDetail(assistanceTarget?.id);
  const assistancePending=useRef<{payload:string;key:string}|null>(null), assistanceInFlight=useRef(false);
  const draftKey=assistanceTarget?account.user.id+':hinter-assistance:'+assistanceTarget.id:account.user.id+':'+space+':'+thread;
  const currentComposerKey=useRef(draftKey);currentComposerKey.current=draftKey;
  const assistanceBound=!!assistanceTarget&&assistanceTarget.workspace_id===space&&assistanceTarget.thread_id===thread;
  const assistanceRetry=!!assistanceDetail.item&&assistanceDetail.item.status!=='pending'&&['failed','cancelled'].includes(assistanceDetail.item.continuation_status||'');
  const assistanceReady=assistanceBound&&!assistanceDetail.loading&&!!assistanceDetail.item&&!assistanceDetail.error;
  function exitAssistance(){if(assistanceInFlight.current)return;dictation.stop();setAssistanceTarget(null);setAssistanceError('');requestAnimationFrame(()=>input.current?.focus());}
  function selectAssistance(item:Assistance){
    if(!canGuide||sending||assistanceInFlight.current)return;
    cancelCallPreparation();dictation.stop();
    setAssistanceTarget(item);setAssistanceError('');setNotice('');
    const params=new URLSearchParams(location.search);params.set('thread',item.thread_id);history.replaceState(null,'','?'+params);
    if(space===item.workspace_id)setThread(item.thread_id);else{setThread('');setSpace(item.workspace_id);}
    if(!spaces.some(value=>value.id===item.workspace_id))void request<{workspaces:WorkspaceType[]}>('/admin/workspaces').then(value=>setSpaces(value.workspaces)).catch(e=>setError((e as Error).message));
  }
  useEffect(()=>{if(assistanceReady&&!assistanceDetail.error)input.current?.focus();},[assistanceTarget?.id,assistanceReady]);
  async function resolveAssistance(action:'provide'|'decline'|'retry'){
    const item=assistanceDetail.item;
    if(!canGuide||!item||!assistanceReady||assistanceInFlight.current||dictation.present||sending||action==='provide'&&!draft.trim())return;
    const targetKey=draftKey,body={action,text:action==='provide'?draft.trim():'',expected_version:item.version};
    const payload=JSON.stringify([item.id,body]),key=assistancePending.current?.payload===payload?assistancePending.current.key:crypto.randomUUID();
    assistancePending.current={payload,key};assistanceInFlight.current=true;setSending(true);setAssistanceError('');
    try{
      await send('/assistance-requests/'+item.id+'/resolve',body,'POST',key);
      assistancePending.current=null;if(action==='provide')drafts.delete(targetKey);
      setAssistanceRefresh(value=>value+1);
      if(currentComposerKey.current!==targetKey)return;
      setAssistanceTarget(null);setAssistanceError('');
      setNotice(action==='provide'?'已公开补充并保存到该 Talker 的 Wiki，Agent 已安排继续回答原问题。':action==='decline'?'已标记暂不提供，Agent 将说明资料不足。':'已重新安排 Agent 继续回答。');
      requestAnimationFrame(()=>input.current?.focus());
    }catch(e){if(currentComposerKey.current===targetKey)setAssistanceError((e as Error).message);}finally{assistanceInFlight.current=false;setSending(false);}
  }
  const guidanceKey=account.user.id+':'+space+':hinter-guidance';
  const currentGuidanceScope=useRef(guidanceKey);currentGuidanceScope.current=guidanceKey;
  useEffect(()=>{setGuidance(drafts.get(guidanceKey)||'');setShared(drafts.get(guidanceKey+':shared')==='1');},[guidanceKey]);
  function setDraftTracked(value:string|((previous:string)=>string)){setDraft(previous=>{const next=typeof value==='function'?value(previous):value;drafts.set(draftKey,next);return next;});}
  const canGuide=supervise&&account.capabilities.includes('guidance.write');
  const call=useMeetingVoice(thread,(_text,accepted)=>{follow.current=true;if(accepted?.run_id){setRuns(previous=>previous.some(run=>run.id===accepted.run_id)?previous:[...previous,{id:accepted.run_id,record_id:accepted.record_id,status:accepted.status||'queued',attempt:0,created_at:Date.now()/1000}]);appendAcceptedRecords(accepted.input_record,accepted.assistant_record);if(accepted.acknowledgement)setAcknowledgements(previous=>({...previous,[accepted.run_id]:accepted.acknowledgement!.text}));}},setError,()=>{playbackRequest.current++;stopReplyAudio();},()=>!!audio.current&&!audio.current.paused);

  const dictationBase=useRef(''), draftIsVoice=useRef(false);
  const dictation=useSpeechStream({endpoint:'/api/v1/voice/dictation?workspace_id='+encodeURIComponent(space),enabled:!!space,dictation:true,scope:draftKey,
    onPartial:text=>{draftIsVoice.current=true;setDraftTracked([dictationBase.current,text].filter(Boolean).join('\n'));},
    onFinal:text=>{draftIsVoice.current=true;setDraftTracked([dictationBase.current,text].filter(Boolean).join('\n'));setNotice(text?'最终转写已校正，请确认后发送。':'未识别到语音，原草稿已保留。');requestAnimationFrame(()=>input.current?.focus());},onError:setError});
  useEffect(()=>{if(dictation.present&&input.current)input.current.scrollTop=input.current.scrollHeight;},[dictation.caption,dictation.present]);
  const voiceBusy=dictation.state==='connecting'||dictation.state==='finalizing', recording=dictation.active;
  const pendingRuns=runs.filter(run=>!terminal(run.status)).sort((a,b)=>(a.created_at||Number.MAX_VALUE)-(b.created_at||Number.MAX_VALUE));
  function toolName(name:string){return ({web_search:'网络搜索',web_fetch:'读取网页',recall_records:'查找会话记录',recall_memory:'查找个人记忆',remember:'保存记忆',current_time:'读取当前时间',task:'处理子任务',request_hinter:'请求 Hinter 补充'} as Record<string,string>)[name]||'工具处理';}
  function runFailure(run:Run){return run.error_code==='AGENT_RUN_TIMEOUT'?'这条回复处理超时，后续任务会继续。':run.error_code==='AGENT_RUN_IDLE_TIMEOUT'?'这条回复等待外部服务超时，后续任务会继续。':'这条回复未完成，记录已保留。';}
  function restoreInput(record:RecordItem){dictation.stop();setDraftTracked(record.text);draftIsVoice.current=record.kind==='transcript';setNotice('已放入输入框，确认后可重新发送。');requestAnimationFrame(()=>input.current?.focus());}

  useEffect(()=>{const controller=new AbortController();request<{workspaces:WorkspaceType[]}>(supervise?'/admin/workspaces':'/workspaces',{signal:controller.signal}).then(r=>{setSpaces(r.workspaces);if(!r.workspaces.some(w=>w.id===space))setSpace(r.workspaces[0]?.id||'');}).catch(e=>{if(!controller.signal.aborted)setError(e.message);});return()=>controller.abort();},[supervise,account.user.id]);
  useEffect(()=>{setPreparingCall(false);setCallToStart(null);return()=>{callIntent.current?.controller.abort();callIntent.current=null;};},[space]);
  useEffect(()=>{
    const controller=new AbortController();setThread('');setRecords([]);setRuns([]);setLive({});setLoading(true);setError('');generation.current++;
    if(!space){setLoading(false);return;}
    request<{threads:Thread[]}>('/threads?workspace_id='+space,{signal:controller.signal}).then(r=>{if(controller.signal.aborted)return;setThreadList(r.threads);const wanted=new URLSearchParams(location.search).get('thread');setThread(r.threads.find(t=>t.id===wanted)?.id||r.threads[0]?.id||'');}).catch(e=>{if(!controller.signal.aborted)setError(e.message);}).finally(()=>{if(!controller.signal.aborted)setLoading(false);});
    return()=>controller.abort();
  },[space]);
  useEffect(()=>{draftIsVoice.current=false;setDraftTracked(drafts.get(draftKey)||'');},[draftKey]);
  useEffect(()=>{
    stopReplyAudio();playbackRequest.current++;setEditing('');setCorrection('');const controller=new AbortController();const current=++generation.current;setRecords([]);setRuns([]);setLive({});setTools({});setAcknowledgements({});follow.current=true;setError('');
    if(!thread)return;
    const params=new URLSearchParams(location.search);params.set('thread',thread);history.replaceState(null,'','?'+params);
    let active=true;let timer:ReturnType<typeof setTimeout>;const eventState:Record<string,{seq:number;text:string;tool:string}>={};
    async function loadRecords(){const collected:RecordItem[]=[];let after=0;for(;;){const page=await request<{records:RecordItem[];next_seq:number}>('/threads/'+thread+'/records?after_seq='+after,{signal:controller.signal});collected.push(...page.records);if(page.records.length<200)return {records:collected};if(page.next_seq<=after)throw new Error('记录加载中断，请刷新重试。');after=page.next_seq;}}
    async function refresh(){
      try{
        const [r,t,p]=await Promise.all([loadRecords(),request<{runs:Run[]}>('/threads/'+thread,{signal:controller.signal}),request<{projections:Projection[]}>('/threads/'+thread+'/projections',{signal:controller.signal})]);
        if(!active||current!==generation.current)return;
        setRecords(r.records);setRuns(t.runs);setProjection(p.projections);setError('');
        const running=t.runs.filter(r=>r.status==='running');
        for(const run of running){const state=eventState[run.id]||{seq:0,text:'',tool:''};const result=await request<{events:RunEvent[]}>('/runs/'+run.id+'/events?after_seq='+state.seq,{signal:controller.signal});if(!active)return;let {text,tool}=state;for(const event of result.events){state.seq=Math.max(state.seq,event.seq);if(event.type==='assistant.reset')text='';if(event.type==='assistant.delta')text+=event.payload.text||'';if(event.type==='assistant.snapshot')text=event.payload.text||'';if(event.type==='assistant.completed')text=event.payload.text||text;if(event.type==='tool.started')tool='正在'+toolName(event.payload.name||'');if(event.type==='tool.failed')tool=toolName(event.payload.name||'')+'暂不可用';if(event.type==='tool.completed')tool=toolName(event.payload.name||'')+'已完成';}eventState[run.id]={seq:state.seq,text,tool};setLive(prev=>({...prev,[run.id]:text}));setTools(prev=>({...prev,[run.id]:tool}));}
      }catch(e){if(active&&!controller.signal.aborted)setError((e as Error).message);}finally{if(active)timer=setTimeout(refresh,1200);}
    }
    refresh();return()=>{active=false;controller.abort();clearTimeout(timer);};
  },[thread]);
  // Wait for the selected thread to bind the voice hook before opening its socket.
  useEffect(()=>{if(!callToStart||callToStart.space!==space||callToStart.thread!==thread||callIntent.current?.space!==space)return;callIntent.current=null;setCallToStart(null);setPreparingCall(false);void call.start();},[callToStart,space,thread]);
  useEffect(()=>{if(follow.current&&scroll.current)scroll.current.scrollTop=scroll.current.scrollHeight;},[records,live]);
  useEffect(()=>{if(side!=='memory'||!space)return;const controller=new AbortController();request<{documents:MemoryDoc[]}>('/workspaces/'+space+'/memory',{signal:controller.signal}).then(r=>{setDocs(r.documents);setMemoryText(r.documents.find(d=>d.name==='个人记忆')?.text||'');}).catch(e=>{if(!controller.signal.aborted)setError(e.message);});return()=>controller.abort();},[side,space,notice]);
  useEffect(()=>()=>{playbackRequest.current++;stopReplyAudio();},[]);
  async function newThread(){if(!space||sending||preparingCall)return;exitAssistance();setSending(true);try{const t=await send<Thread>('/threads',{workspace_id:space});setThreadList(prev=>[t,...prev]);setThread(t.id);}catch(e){setError((e as Error).message);}finally{setSending(false);}}
  async function startCall(){
    if(!space||loading||sending||assistanceTarget||callIntent.current||dictation.present||call.present)return;
    setError('');setNotice('');playbackRequest.current++;stopReplyAudio();
    if(thread){void call.start();return;}
    const intent={space,controller:new AbortController()};callIntent.current=intent;setPreparingCall(true);
    let timedOut=false;const timer=setTimeout(()=>{timedOut=true;intent.controller.abort();},15000);
    try{
      const created=await request<Thread>('/threads',{method:'POST',body:JSON.stringify({workspace_id:space}),signal:intent.controller.signal});
      if(callIntent.current!==intent)return;
      const initialDraft=drafts.get(draftKey);if(initialDraft)drafts.set(account.user.id+':'+space+':'+created.id,initialDraft);
      setThreadList(previous=>[created,...previous]);setCallToStart({space,thread:created.id});setThread(created.id);
    }catch(e){if(callIntent.current===intent){callIntent.current=null;setPreparingCall(false);setError(timedOut?'创建通话会话超时，请重试。':(e as Error).message);}}
    finally{clearTimeout(timer);}
  }
  function appendAcceptedRecords(inputRecord?:RecordItem,replyRecord?:RecordItem){
    const additions=[...(inputRecord?[inputRecord]:[]),...(replyRecord?[replyRecord]:[])];
    if(!additions.length)return;
    setRecords(previous=>{const byId=new Map(previous.map(item=>[item.id,item]));for(const item of additions)byId.set(item.id,item);return [...byId.values()].sort((a,b)=>a.seq-b.seq);});
  }
  async function submit(){
    if(assistanceTarget){await resolveAssistance(assistanceRetry?'retry':'provide');return;}
    if(!draft.trim()||sending||preparingCall||dictation.present)return;setSending(true);setError('');const text=draft.trim(),kind=draftIsVoice.current?'transcript':'text';let selected=thread;
    try{
      if(!selected){const t=await send<Thread>('/threads',{workspace_id:space});selected=t.id;setThreadList(prev=>[t,...prev]);setThread(selected);}
      const pending=pendingKey.current;
      const key=pending&&pending.text===text&&pending.thread===selected&&pending.kind===kind?pending.key:crypto.randomUUID();pendingKey.current={text,key,thread:selected,kind};
      const result=await send<{run_id:string|null;record_id:string|null;status?:string;input_record?:RecordItem;assistant_record?:RecordItem;ignored?:boolean;retryable?:boolean;message?:string;acknowledgement?:{text:string}|null}>('/threads/'+selected+'/inputs',{input:{kind,text},presentation_mode:'questions'},'POST',key);
      if(result.ignored&&result.retryable)throw new Error(result.message||'这段语音暂时无法确认，请重试或改用文字。');
      pendingKey.current=null;drafts.delete(draftKey);drafts.delete(account.user.id+':'+space+':'+selected);if(currentThread.current!==selected)return;setDraftTracked('');if(result.ignored)return;const {run_id,record_id}=result;if(!run_id||!record_id)throw new Error('语音记录未能提交，请重试。');if(result.acknowledgement)setAcknowledgements(previous=>({...previous,[run_id]:result.acknowledgement!.text}));appendAcceptedRecords(result.input_record,result.assistant_record);setRuns(prev=>[...prev,{id:run_id,record_id,status:result.status||'queued',attempt:0,created_at:Date.now()/1000}]);follow.current=true;input.current?.focus();
    }catch(e){drafts.set(account.user.id+':'+space+':'+selected,text);if(currentThread.current===selected){setDraftTracked(text);setError((e as Error).message);}}finally{setSending(false);}
  }
  async function correctRecord(record:RecordItem){if(correcting||!correction.trim())return;setCorrecting(true);try{await send('/records/'+record.id,{text:correction.trim(),expected_revision:record.revision},'PATCH');setEditing('');setCorrection('');setNotice('修订已保存，Duet 会按新内容重新处理；原始版本仍然保留。');}catch(e){setError((e as Error).message);}finally{setCorrecting(false);}}
  async function cancel(run:Run){try{await send('/runs/'+run.id+'/cancel',{});setNotice('已请求停止此任务，已完成的记录和操作会保留。');}catch(e){setError((e as Error).message);}}
  async function saveMemory(){if(memoryBusy)return;setMemoryBusy(true);try{const doc=docs.find(d=>d.name==='个人记忆');await send('/workspaces/'+space+'/memory',{name:'个人记忆',text:memoryText,visibility:'user_shared',expected_revision:doc?.revision},'PUT');setNotice('个人记忆已保存 · '+new Date().toLocaleTimeString('zh-CN'));}catch(e){setError((e as Error).message);}finally{setMemoryBusy(false);}}
  async function sendGuidance(){
    if(!thread||!guidance.trim()||sending||loading)return;
    if(threadList.find(item=>item.id===thread)?.workspace_id!==space){setError('会话仍在切换，请确认目标 Talker 后保存。');return;}
    const targetThread=thread,targetSpace=space,targetKey=guidanceKey,targetName=selectedSpace?.name||'当前 Talker',isShared=shared;
    setSending(true);setError('');
    try{
      const saved=await send<{memory_layer?:string}>('/threads/'+targetThread+'/guidance',{text:guidance,visibility:isShared?'user_shared':'admin_private',workspace_id:targetSpace});
      drafts.delete(targetKey);drafts.delete(targetKey+':shared');
      if(currentGuidanceScope.current!==targetKey)return;
      setGuidance('');setShared(false);
      setNotice(saved.memory_layer==='prohibition'?targetName+'：封禁已保存，对该 Talker 的全部会话生效。':isShared?targetName+'：提示已共享到 Wiki，该 Talker 的全部会话可召回。':targetName+'：私有备注已保存，仅管理员可见。');
    }catch(e){if(currentGuidanceScope.current===targetKey)setError((e as Error).message);}finally{setSending(false);}
  }
  function toggleVoice(){
    if(preparingCall)return;
    if(dictation.state==='finalizing'){dictation.stop();setNotice('已保留实时草稿，请检查后发送。');return;}
    if(dictation.present){dictation.finish();return;}
    setError('');setNotice('');playbackRequest.current++;stopReplyAudio();dictationBase.current=draft;dictation.start();
  }
  async function speak(record:RecordItem){
    const attempt=++playbackRequest.current, wasPlaying=playback===record.id;stopReplyAudio();if(wasPlaying)return;
    setSpeechBusy(record.id);
    try{
      const result=await send<{url:string}>('/records/'+record.id+'/speech?stream=true',{});
      if(attempt!==playbackRequest.current)return;
      const player=new Audio(result.url);audio.current=player;player.preload='auto';
      player.onplaying=()=>{if(attempt===playbackRequest.current){call.stopAcknowledgement();setSpeechBusy('');setPlayback(record.id);}};
      player.onended=()=>{if(attempt===playbackRequest.current){setPlayback('');setSpeechBusy('');}};
      player.onerror=()=>{if(attempt===playbackRequest.current){const message='语音播放中断，请点击朗读重试；文字回复已保留。';stopReplyAudio();setError(message);if(call.active)call.reportError(message);}};
      await player.play();
    }catch(e){if(attempt===playbackRequest.current){stopReplyAudio();setError((e as Error).message);if(call.active)call.reportError((e as Error).message);}}
    finally{if(attempt===playbackRequest.current)setSpeechBusy('');}
  }
  useEffect(()=>{if(!call.active){callWasActive.current=false;return;}if(!callWasActive.current){records.forEach(r=>spoken.current.add(r.id));callWasActive.current=true;return;}const fresh=records.filter(r=>r.role==='assistant'&&!r.withdrawn&&!spoken.current.has(r.id));fresh.forEach(r=>spoken.current.add(r.id));if(fresh.length)speak(fresh[fresh.length-1]);},[records,call.active]);
  const selectedSpace=spaces.find(w=>w.id===space);
  return <div className="employee-workspace"><aside className="employee-directory"><div className="employee-directory-title"><span>{supervise?'监管空间':'我的会话'}</span><button className="icon-button" aria-label="新建会议" disabled={!space||sending||preparingCall||selectedSpace?.state==='unclaimed_legacy'} onClick={newThread}><Plus size={18}/></button></div>{supervise&&<HinterInbox disabled={sending} selectedId={assistanceTarget?.id} refreshKey={assistanceRefresh} onOpen={selectAssistance}/>}{supervise&&<div className="employee-space-list">{spaces.map(w=><button key={w.id} className={space===w.id?'selected':''} disabled={sending} onClick={()=>{cancelCallPreparation();exitAssistance();setSpace(w.id);}}>{w.name}<small>{w.state==='unclaimed_legacy'?'待绑定旧空间':'个人工作空间'}</small></button>)}</div>}<nav aria-label="会议会话">{threadList.map(t=><button key={t.id} className={'employee-thread '+(thread===t.id?'selected':'')} aria-current={thread===t.id?'page':undefined} disabled={sending} onClick={()=>{cancelCallPreparation();exitAssistance();setThread(t.id);}}><MessageSquare size={15}/><span>{t.title}<small>{new Date(t.created_at*1000).toLocaleDateString('zh-CN')}</small></span><ChevronRight size={14}/></button>)}{!threadList.length&&<p className="employee-help">{loading?'正在加载…':'还没有会话。可以直接输入或开始通话。'}</p>}</nav></aside><section className="employee-chat"><div className="employee-chat-content"><header className="employee-agent-heading"><div className="employee-agent-icon"><BookOpen size={21}/></div><div><h1>Duet · 个人员工</h1><p>会议助手 · {supervise?selectedSpace?.name:'你的独立工作空间'}</p></div><span className="employee-runtime">DeerFlow 2.1</span></header>{error&&<Notice error>{error}</Notice>}{notice&&<Notice>{notice}</Notice>}{canGuide&&selectedSpace?.state==='unclaimed_legacy'&&<LegacyBinding workspaceId={space} workspaceName={selectedSpace.name} onBound={userId=>{setSpaces(prev=>prev.map(w=>w.id===space?{...w,state:'active',owner_user_id:userId}:w));setNotice('旧空间已绑定注册用户。');}}/>}<div className="employee-conversation" ref={scroll} onScroll={()=>{const el=scroll.current;if(el)follow.current=el.scrollHeight-el.scrollTop-el.clientHeight<100;}} aria-label="会话记录"><AssistancePanel key={account.user.id+':'+thread} threadId={thread} canResolve={canGuide} disabled={sending} onSelect={selectAssistance} refreshKey={assistanceRefresh}/>{!records.length&&!loading&&<div className="employee-chat-empty"><BookOpen size={32}/><h2>先记下来，再一起推进。</h2><p>可以记录一段讨论，也可以直接交代任务。</p><button disabled={!!assistanceTarget||selectedSpace?.state==='unclaimed_legacy'} onClick={()=>{setDraftTracked('先记录：今天讨论的决定和待确认事项。');input.current?.focus();}} className="secondary-button">开始一份会议记录</button></div>}{records.map(r=><article className={'employee-message '+r.role+(r.visibility==='admin_private'?' private':'')} key={r.id}><header><span>{r.role==='assistant'?'Duet':r.role==='hinter'?'Hinter 提示':'会议记录'}</span><small>{new Date(r.created_at*1000).toLocaleTimeString('zh-CN',{hour:'2-digit',minute:'2-digit'})}{r.revision>1?' · 修订 '+r.revision:''}{r.visibility==='admin_private'?' · 仅管理员':''}</small>{r.role==='user'&&!r.withdrawn&&<button className="icon-button" aria-label="修订记录" disabled={correcting} onClick={()=>{setEditing(r.id);setCorrection(r.text);}}><Pencil size={15}/></button>}{r.role==='assistant'&&!r.withdrawn&&<button className="icon-button" aria-label={speechBusy===r.id?'正在准备朗读':playback===r.id?'停止播放':'朗读回复'} disabled={speechBusy===r.id} aria-busy={speechBusy===r.id} onClick={()=>speak(r)}>{speechBusy===r.id?<LoaderCircle size={15} className="animate-spin"/>:playback===r.id?<CircleStop size={15}/>:<Volume2 size={15}/>}</button>}</header><div className="employee-markdown">{editing===r.id?<form noValidate onSubmit={event=>{event.preventDefault();correctRecord(r);}}><label className="field-label" htmlFor={'correction-'+r.id}>修订记录</label><textarea id={'correction-'+r.id} className="field resize-none" rows={4} value={correction} onChange={event=>setCorrection(event.target.value)} autoFocus/><p className="employee-help">原文将保留，保存后 Duet 会重新处理这条记录。</p><button className="secondary-button" disabled={correcting||!correction.trim()}>{correcting?'保存中…':'保存修订'}</button><button className="employee-text-button" type="button" disabled={correcting} onClick={()=>setEditing('')}>取消</button></form>:<ReactMarkdown>{r.text}</ReactMarkdown>}</div>{runs.filter(run=>run.record_id===r.id&&terminal(run.status)&&run.status!=='completed').map(run=><div key={run.id} className="employee-help">{run.status==='cancelled'?'任务已停止，记录保留。':runFailure(run)}{run.status==='failed'&&!call.present&&!assistanceTarget&&<button type="button" className="employee-text-button" onClick={()=>restoreInput(r)}>重新编辑发送</button>}</div>)}</article>)}{pendingRuns.map(run=><article className="employee-message assistant" key={run.id}><header><span><LoaderCircle size={14} className="animate-spin"/>{run.status==='queued'?(pendingRuns.indexOf(run)>0?'等待回复 · 前面 '+pendingRuns.indexOf(run)+' 条':'已收到'):'Duet 正在处理'}</span><button className="employee-text-button" onClick={()=>cancel(run)}><CircleStop size={14}/>停止任务</button></header>{tools[run.id]&&<p className="employee-help" role="status">{tools[run.id]}</p>}{live[run.id]?<div className="employee-markdown"><ReactMarkdown>{live[run.id]}</ReactMarkdown></div>:<p className="employee-help">{tools[run.id]||acknowledgements[run.id]||'收到，正在处理。'}</p>}</article>)}</div>{canGuide&&!assistanceTarget&&selectedSpace?.state!=='unclaimed_legacy'&&<AnswerPolicies workspaceId={space} workspaceName={selectedSpace?.name||'当前空间'} refreshKey={notice}/>}{canGuide&&!assistanceTarget&&thread&&selectedSpace?.state!=='unclaimed_legacy'&&<details className="employee-guidance"><summary>Hinter 提示与备注</summary><p className="employee-help">当前提示对象：<strong>{selectedSpace?.name}</strong> · 封禁适用于该 Talker 的全部会话</p><label className="field-label" htmlFor="hinter-guidance">补充内容</label><textarea id="hinter-guidance" className="field resize-none" disabled={sending||loading} value={guidance} onChange={e=>{setGuidance(e.target.value);drafts.set(guidanceKey,e.target.value);}} rows={3}/><label className="employee-check"><input type="checkbox" checked={shared} disabled={sending||loading} onChange={e=>{setShared(e.target.checked);drafts.set(guidanceKey+':shared',e.target.checked?'1':'0');}}/>共享到该 Talker 的 Wiki，允许其全部会话与 Agent 召回</label><button className="secondary-button" disabled={!guidance.trim()||sending||loading||threadList.find(item=>item.id===thread)?.workspace_id!==space} onClick={sendGuidance}>保存给当前 Talker</button></details>}{!call.present&&call.warning&&<Notice error>{call.warning}</Notice>}{!call.present&&dictation.warning&&<Notice error>{dictation.warning}</Notice>}{(!call.present||!!assistanceTarget)&&<form className="employee-composer" noValidate onSubmit={e=>{e.preventDefault();submit();}}>{assistanceTarget&&<AssistanceBrief selected={assistanceTarget} item={assistanceDetail.item} loading={assistanceDetail.loading||!assistanceBound} error={assistanceDetail.error} busy={sending} canDecline={assistanceReady&&!dictation.present} onCancel={exitAssistance} onReload={()=>{setAssistanceError('');assistanceDetail.reload();}} onDecline={()=>void resolveAssistance('decline')}/>}{assistanceTarget&&assistanceError&&<Notice error>{assistanceError}<button type="button" className="employee-text-button" disabled={sending} onClick={()=>{setAssistanceError('');assistanceDetail.reload();}}>重新加载事项</button></Notice>}<label className="sr-only" htmlFor="meeting-input">{assistanceTarget?'本次允许披露的补充资料':'会议记录或任务'}</label><textarea ref={input} readOnly={dictation.present||!!assistanceTarget&&(sending||!assistanceReady||assistanceDetail.item?.status!=='pending')} id="meeting-input" className="resize-none" placeholder={assistanceTarget?'填写允许披露的事实、数据、统计时间与适用范围…':'记录一段讨论，或交给 Duet 一个任务…'} value={draft} onChange={e=>{setDraftTracked(e.target.value);drafts.set(draftKey,e.target.value);}} rows={3} onKeyDown={e=>{if(e.key==='Escape'&&assistanceTarget){e.preventDefault();exitAssistance();return;}if(e.key==='Enter'&&!e.shiftKey&&!e.nativeEvent.isComposing){e.preventDefault();submit();}}}/>{dictation.present&&<div className="employee-dictation-status" role="status"><span className={dictation.active?'employee-voice-dot':''}/>{dictation.state==='connecting'?'正在连接实时转写…':dictation.state==='finalizing'?'正在校正最终文字…':'实时转写中 · 说话时文字会同步显示'}</div>}<footer><span role="status">{assistanceTarget?'Enter 公开补充 · Esc 退出':preparingCall?'正在准备通话会话…':recording?'点击麦克风结束并校正':voiceBusy?dictation.phase:!thread?'可以直接开始通话，自动创建会议':'Enter 发送 · Shift + Enter 换行'}</span><button type="button" className="icon-button" ref={callButton} aria-label={preparingCall?'正在准备通话':call.present?'结束通话':'开始通话'} aria-pressed={call.present} aria-busy={preparingCall} disabled={!!assistanceTarget||!space||!selectedSpace||loading||sending||preparingCall||voiceBusy||recording||selectedSpace.state==='unclaimed_legacy'} onClick={()=>{call.present?hangup():void startCall();}}>{preparingCall?<LoaderCircle size={18} className="animate-spin"/>:call.present?<PhoneOff size={18}/>:<Phone size={18}/>}</button>{call.active&&<button type="button" className="employee-text-button" onClick={call.toggleMute}>{call.muted?'取消静音':'静音'}</button>}<button type="button" className={'icon-button '+(recording?'text-red-300':'')} aria-label={dictation.state==='finalizing'?'停止等待，保留草稿':dictation.state==='connecting'?'取消语音输入':recording?'结束并校正转写':'实时语音输入'} aria-pressed={dictation.present} disabled={sending||!!assistanceTarget&&(!assistanceReady||assistanceDetail.item?.status!=='pending')||call.present||preparingCall||selectedSpace?.state==='unclaimed_legacy'} onClick={toggleVoice}><Mic size={18}/></button><button className="primary-button" disabled={(!assistanceRetry&&!draft.trim())||sending||preparingCall||dictation.present||!space||selectedSpace?.state==='unclaimed_legacy'||!!assistanceTarget&&(!assistanceReady||(assistanceDetail.item?.status!=='pending'&&!assistanceRetry))} aria-busy={sending}><Send size={16}/>{sending?'发送中…':assistanceTarget?(assistanceRetry?'重新继续回答':'公开补充并继续回答'):'发送'}</button></footer></form>}</div><div ref={setCallHost} className="call-surface-host"/><MeetingCall call={call} compact={!!assistanceTarget} container={callHost} thinking={runs.some(run=>!terminal(run.status))} preparingSpeech={!!speechBusy} playing={!!playback} pending={runs.filter(run=>!terminal(run.status)).length} failed={runs.some(run=>run.id===call.lastRunId&&run.status==='failed')} reply={records.filter(record=>record.role==='assistant'&&!record.withdrawn).slice(-1)[0]?.text||''} onHangup={hangup}/></section><aside className="employee-context"><div className="employee-context-tabs"><button aria-pressed={side==='questions'} className={side==='questions'?'selected':''} onClick={()=>setSide('questions')}><FileQuestion size={16}/>问题视图</button><button aria-pressed={side==='memory'} className={side==='memory'?'selected':''} onClick={()=>setSide('memory')}><BookOpen size={16}/>Wiki</button></div>{side==='questions'?<div className="employee-context-body"><p className="employee-help">从会话记录中提取的问题。它们用于回顾，不会阻塞 Agent 回答。</p>{projection.flatMap(p=>p.items).map((p,i)=><div className="employee-question" key={p.source_record_id+':'+i}><FileQuestion size={15}/><span>{p.text}<small>来源修订 {p.source_revision}</small></span></div>)}{!projection.some(p=>p.items.length>0)&&<p className="employee-empty">{projection.some(p=>p.status==='pending')?'记录已保存，问题视图稍后更新。':'有问题时会整理在这里，普通陈述也会完整保留。'}</p>}{projection.some(p=>p.status==='failed')&&<Notice>部分问题未能提取，原始记录和 Agent 任务不受影响。<button className="secondary-button" disabled={sending} onClick={async()=>{try{await send('/threads/'+thread+'/projections/rebuild',{});setNotice('已安排重新整理问题。');}catch(e){setError((e as Error).message);}}}>重新整理问题</button></Notice>}</div>:<div className="employee-context-body"><p className="employee-help">你的背景、偏好与 Hinter 补充会保存在这里，供后续问题查阅。</p><label className="field-label" htmlFor="personal-memory">个人记忆</label><textarea id="personal-memory" className="field resize-none" rows={8} value={memoryText} onChange={e=>setMemoryText(e.target.value)}/><button className="secondary-button" disabled={memoryBusy||!space||selectedSpace?.state==='unclaimed_legacy'} onClick={saveMemory}><Check size={15}/>{memoryBusy?'保存中…':'保存记忆'}</button>{docs.filter(d=>d.name!=='个人记忆').map(d=><details key={d.id}><summary>{d.metadata?.origin==='hinter'?'Hinter · '+(d.metadata.title||'提示'):d.name}{d.visibility==='admin_private'?' · 私有':''}</summary><p>{d.text}</p></details>)}</div>}</aside></div>;
}
