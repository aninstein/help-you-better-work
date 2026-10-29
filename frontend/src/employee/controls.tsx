import { useEffect, useId, useRef, useState } from 'react';
import type { InputHTMLAttributes, ReactNode } from 'react';
import { Eye, EyeOff } from 'lucide-react';

export function Field({label, labelAction, error, help, ...props}: InputHTMLAttributes<HTMLInputElement> & {label:string;labelAction?:ReactNode;error?:string;help?:string}) {
  const id = useId();
  return <div className="employee-field"><div className="employee-field-heading"><label className="field-label" htmlFor={id}>{label}</label>{labelAction}</div><input {...props} id={id} className="field" aria-invalid={!!error} aria-describedby={error || help ? id+'-help' : undefined}/>{(error || help) && <small id={id+'-help'} className={error ? 'employee-error' : 'text-white/50'}>{error || help}</small>}</div>;
}
export function PasswordField({label='密码', error, ...props}: InputHTMLAttributes<HTMLInputElement> & {label?:string;error?:string}) {
  const [visible,setVisible] = useState(false);
  const id = useId();
  return <div className="employee-field"><label className="field-label" htmlFor={id}>{label}</label><div className="employee-secret"><input {...props} id={id} className="field" type={visible ? 'text':'password'} aria-invalid={!!error} aria-describedby={error ? id+'-help' : undefined}/><button type="button" className="icon-button" aria-label={visible ? '隐藏密码':'显示密码'} aria-pressed={visible} onClick={()=>setVisible(!visible)}>{visible ? <EyeOff size={17}/>:<Eye size={17}/>}</button></div>{error&&<small id={id+'-help'} className="employee-error">{error}</small>}</div>;
}
export function Notice({children, error=false}: {children:ReactNode;error?:boolean}) { return <div className={error ? 'employee-notice employee-error' : 'employee-notice'} role={error ? 'alert':'status'}>{children}</div>; }
export function ConfirmDialog({title,children,onCancel,onConfirm,busy,error,action}: {title:string;children:ReactNode;onCancel:()=>void;onConfirm:()=>void;busy:boolean;error:string;action:string}) {
  const ref = useRef<HTMLDialogElement>(null);
  const cancelRef = useRef<HTMLButtonElement>(null);
  const id = useId();
  useEffect(()=>{ const trigger=document.activeElement as HTMLElement; ref.current?.showModal(); cancelRef.current?.focus(); return ()=>trigger?.focus(); },[]);
  return <dialog ref={ref} className="employee-dialog glass" aria-labelledby={id} onCancel={e=>{e.preventDefault(); if(!busy)onCancel();}}><h2 id={id}>{title}</h2><div className="employee-dialog-body">{children}</div>{error && <Notice error>{error}</Notice>}<div className="employee-actions"><button ref={cancelRef} className="secondary-button" disabled={busy} onClick={onCancel}>取消</button><button className="primary-button" disabled={busy} onClick={onConfirm} aria-busy={busy}>{busy ? '保存中…' : action}</button></div></dialog>;
}
