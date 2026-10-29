import { useRef, useState } from 'react';
import type { FormEvent } from 'react';
import { ArrowRight, AudioLines, LockKeyhole, Shuffle } from 'lucide-react';
import { ApiError, send } from './api';
import type { Account } from './api';
import { Field, Notice, PasswordField } from './controls';

type FieldKey = 'identifier' | 'name' | 'countryCode' | 'phone' | 'email' | 'password' | 'nextPassword';
type FieldErrors = Partial<Record<FieldKey, string>>;
const emailPattern = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;

export default function AuthScreen({onLogin,expired=false,changeAccount,onLogout}: {onLogin:(a:Account)=>void;expired?:boolean;changeAccount?:Account;onLogout?:()=>Promise<void>}) {
  const [register,setRegister]=useState(false), [busy,setBusy]=useState(false), [nameBusy,setNameBusy]=useState(false);
  const [error,setError]=useState(''), [notice,setNotice]=useState(''), [fieldErrors,setFieldErrors]=useState<FieldErrors>({});
  const [identifier,setIdentifier]=useState(''), [countryCode,setCountryCode]=useState('+86'), [phone,setPhone]=useState('');
  const [email,setEmail]=useState(''), [name,setName]=useState('');
  const [password,setPassword]=useState(''), [nextPassword,setNextPassword]=useState('');
  const form=useRef<HTMLFormElement>(null);
  const change=!!changeAccount;

  function edit(key:FieldKey, value:string, setter:(value:string)=>void) {
    setter(value);
    setFieldErrors(previous=>({...previous,[key]:undefined}));
    setError('');
  }

  async function suggestName() {
    if(nameBusy || busy)return;
    setNameBusy(true);
    setFieldErrors(previous=>({...previous,name:undefined}));
    try {
      const suggestion=await send<{display_name:string}>('/auth/name-suggestion',{});
      setName(suggestion.display_name);
    } catch(e) {setFieldErrors(previous=>({...previous,name:(e as Error).message}));}
    finally {setNameBusy(false);}
  }

  async function submit(e:FormEvent) {
    e.preventDefault();
    if(busy)return;
    setError('');setNotice('');
    const errors:FieldErrors={};
    const localPhone=phone.replace(/[\s().-]/g,'');
    if(change) {
      if(!password)errors.password='请输入当前密码。';
      if(nextPassword.length<12)errors.nextPassword='新密码至少需要 12 个字符。';
    } else if(register) {
      if(!/^\+[1-9]\d{0,2}$/.test(countryCode.trim()))errors.countryCode='请输入 1–3 位国家或地区区号，例如 +86。';
      if(!/^\d{4,14}$/.test(localPhone) || (countryCode.trim()==='+86' && !/^1[3-9]\d{9}$/.test(localPhone)))
        errors.phone=countryCode.trim()==='+86'?'请输入 11 位中国大陆手机号，无需重复填写 +86。':'请输入不含区号的手机号，仅使用数字。';
      if(!emailPattern.test(email.trim()))errors.email='请输入有效邮箱，例如 name@example.com。';
      if(password.length<12)errors.password='密码至少需要 12 个字符。';
    } else {
      if(!identifier.trim())errors.identifier='请输入邮箱或手机号。';
      if(!password)errors.password='请输入密码。';
    }
    const first=Object.keys(errors)[0] as FieldKey | undefined;
    if(first) {
      setFieldErrors(errors);
      form.current?.querySelector<HTMLInputElement>('[name="'+first+'"]')?.focus();
      return;
    }
    setFieldErrors({});setBusy(true);
    try {
      if(change) {
        onLogin(await send<Account>('/auth/change-password',{old_password:password,new_password:nextPassword}));
        setPassword('');setNextPassword('');
      } else if(register) {
        const result=await send<{display_name:string}>('/auth/register',{
          phone:countryCode.trim()+localPhone,email:email.trim(),password,
          ...(name.trim()?{display_name:name.trim()}:{}),
        });
        setRegister(false);setIdentifier(email.trim());setPassword('');
        setNotice('注册成功，用户名是“'+result.display_name+'”。现在可以用邮箱或手机登录。');
      } else {
        onLogin(await send<Account>('/auth/login',{identifier:identifier.trim(),password}));
        setPassword('');
      }
    } catch(e) {
      const failure=e as ApiError;
      setError(failure.message);
    } finally {setBusy(false);}
  }

  return <div className="employee-auth">
    <section className="employee-auth-story"><a href="/" className="employee-brand"><AudioLines size={25}/> Duet<span>个人员工</span></a><div><h1>把对话留下，<br/>让事情向前。</h1><p>你的个人助手，是你的工作生活好搭子。<br/>记录讨论，理清思路，也照看后续的每一步。</p><div className="employee-meeting-line"><span/>信息整理 <i/> 能力沉淀 <i/> 任务编排 <i/> 结果分析</div></div></section>
    <section className="employee-auth-card glass" aria-labelledby="auth-title"><LockKeyhole className="text-ai" size={25}/><h2 id="auth-title">{change?'设置你的新密码':register?'创建个人账号':'登录 Duet'}</h2>
      {expired&&<Notice>登录已过期，当前页面未发送的内容会在重新登录后恢复。</Notice>}{notice&&<Notice>{notice}</Notice>}
      <form ref={form} noValidate onSubmit={submit} aria-busy={busy}>
        {register?<>
          <Field name="name" label="用户名（选填，可重名）" value={name} onChange={e=>edit('name',e.target.value,setName)} autoComplete="nickname" maxLength={120} error={fieldErrors.name} labelAction={<button type="button" className="employee-name-action" title="领取 2–5 字古风花名" onClick={suggestName} disabled={nameBusy||busy}><Shuffle size={14}/>{nameBusy?'领取中…':'随机花名'}</button>}/>
          <div className="employee-phone-fields"><Field name="countryCode" label="区号" value={countryCode} onChange={e=>edit('countryCode',e.target.value,setCountryCode)} type="tel" inputMode="tel" autoComplete="tel-country-code" list="employee-calling-codes" required error={fieldErrors.countryCode}/><Field name="phone" label="手机号" value={phone} onChange={e=>edit('phone',e.target.value,setPhone)} type="tel" inputMode="tel" autoComplete="tel-national" placeholder="13800000000" required error={fieldErrors.phone}/></div>
          <datalist id="employee-calling-codes"><option value="+86" label="中国大陆"/><option value="+1" label="美国/加拿大"/><option value="+44" label="英国"/><option value="+81" label="日本"/><option value="+82" label="韩国"/><option value="+65" label="新加坡"/><option value="+852" label="中国香港"/><option value="+886" label="中国台湾"/><option value="+61" label="澳大利亚"/></datalist>
          <Field name="email" label="邮箱" value={email} onChange={e=>edit('email',e.target.value,setEmail)} type="email" autoComplete="email" placeholder="name@example.com" required error={fieldErrors.email}/>
        </>:!change&&<Field name="identifier" label="手机 / 邮箱" value={identifier} onChange={e=>edit('identifier',e.target.value,setIdentifier)} autoComplete="username" placeholder="邮箱或手机号" required error={fieldErrors.identifier}/>}
        <PasswordField name="password" label={change?'当前密码':'密码'} value={password} onChange={e=>edit('password',e.target.value,setPassword)} autoComplete={register?'new-password':'current-password'} placeholder={register?'至少 12 位':undefined} required maxLength={256} error={fieldErrors.password}/>
        {change&&<PasswordField name="nextPassword" label="新密码" value={nextPassword} onChange={e=>edit('nextPassword',e.target.value,setNextPassword)} autoComplete="new-password" required maxLength={256} error={fieldErrors.nextPassword}/>}
        {error&&<Notice error>{error}</Notice>}
        <button className="primary-button employee-submit" disabled={busy||nameBusy} aria-busy={busy}>{busy?'正在处理…':change?'修改密码并继续':register?'注册账号':'登录'}<ArrowRight size={17}/></button>
      </form>
      {!change&&<button className="employee-auth-switch" disabled={busy} onClick={()=>{setRegister(!register);setError('');setFieldErrors({});setPassword('');}}>{register?'已有账号？去登录':'还没有账号？注册'}</button>}{change&&onLogout&&<button className="employee-auth-switch" disabled={busy} onClick={onLogout}>退出并使用其他账号</button>}
    </section>
  </div>;
}
