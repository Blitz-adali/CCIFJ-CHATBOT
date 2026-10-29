import {useEffect,useRef,useState,type FormEvent} from 'react';
import {MessageCircle,Send,X,Plus,ArrowUpRight,Sparkles,Square} from 'lucide-react';
import {sendChatStream,getSuggestedQuestions,resolveApproval,ApiError} from './api';
import {AnswerText} from './AnswerText';
import {NextActions} from './NextActions';
import {FollowUpQuestions} from './FollowUpQuestions';
import {ApprovalPanel} from './ApprovalPanel';
import {getCopy} from './i18n';
import type {ChatResponse,Locale} from './types';

type Turn={id:number;role:'user'|'assistant';text:string;response?:ChatResponse;error?:boolean;cancelled?:boolean;retry?:string};
const words={
  en:{title:'Your connection to Japan',intro:'Hello! How can I help you today?',subtitle:'Membership, events and business support — let’s find your next step.',close:'Close chat',new:'New conversation',note:'CCIFJ digital concierge',retry:'Try again',suggestions:'Explore a topic',starting:'The CCIFJ Concierge is reviewing your request…',working:'YOUR CCIFJ TEAM',cancel:'Cancel request',cancelled:'Request cancelled.'},
  fr:{title:'Votre lien avec le Japon',intro:'Bonjour ! Comment puis-je vous aider ?',subtitle:'Adhésion, événements et services aux entreprises : trouvons votre prochaine étape.',close:'Fermer le chat',new:'Nouvelle conversation',note:'Concierge numérique CCIFJ',retry:'Réessayer',suggestions:'Explorez un sujet',starting:'Le concierge CCIFJ examine votre demande…',working:'VOTRE ÉQUIPE CCIFJ',cancel:'Annuler la demande',cancelled:'Demande annulée.'},
  ja:{title:'日本での次の一歩へ',intro:'こんにちは！何をお手伝いできますか？',subtitle:'会員制度、イベント、ビジネス支援についてご案内します。',close:'チャットを閉じる',new:'新しい会話',note:'CCIFJ デジタルコンシェルジュ',retry:'再試行',suggestions:'相談できる内容',starting:'CCIFJコンシェルジュがご質問を確認しています…',working:'CCIFJコンシェルジュ',cancel:'リクエストをキャンセル',cancelled:'リクエストをキャンセルしました。'}
};
export function WidgetChat({initialLocale,locale:controlledLocale,initialDraft='',draftVersion=0,onClose,isAuthenticated=false,onLogin=()=>{}}:{initialLocale?:Locale;locale?:Locale;initialDraft?:string;draftVersion?:number;onClose?:()=>void;isAuthenticated?:boolean;onLogin?:()=>void}={}){
  const initial=new URLSearchParams(window.location.search).get('lang');
  const [localLocale,setLocale]=useState<Locale>(initialLocale??(initial==='fr'||initial==='ja'?initial:'en'));
  const locale=controlledLocale??localLocale;
  const [turns,setTurns]=useState<Turn[]>([]),[draft,setDraft]=useState(initialDraft),[busy,setBusy]=useState(false),[progressMessages,setProgressMessages]=useState<string[]>([]),[progressIndex,setProgressIndex]=useState(0),[starterQuestions,setStarterQuestions]=useState<string[]>([]);
  const session=useRef<string|null>(null),nextId=useRef(0),pending=useRef(false);
  const activeRequest=useRef<AbortController|null>(null);
  const priorAuthState=useRef(isAuthenticated);
  const input=useRef<HTMLTextAreaElement>(null),end=useRef<HTMLDivElement>(null);
  const t=getCopy(locale),w=words[locale];
  useEffect(()=>{if(draftVersion){setDraft(initialDraft);input.current?.focus();}},[initialDraft,draftVersion]);
  useEffect(()=>{
    setProgressIndex(0);
    if(progressMessages.length<2)return;
    const timer=window.setInterval(()=>setProgressIndex(index=>(index+1)%progressMessages.length),2600);
    return()=>window.clearInterval(timer);
  },[progressMessages]);
  useEffect(()=>{if(priorAuthState.current!==isAuthenticated){session.current=null;setTurns([]);setDraft('');priorAuthState.current=isAuthenticated;}},[isAuthenticated]);
  useEffect(()=>{let active=true;getSuggestedQuestions().then(questions=>{if(active)setStarterQuestions(questions);}).catch(()=>{if(active)setStarterQuestions([]);});return()=>{active=false;};},[]);
  function close(){if(onClose)onClose();else window.parent.postMessage({type:'ccifj:close'},'*');}
  useEffect(()=>{document.documentElement.lang=locale;},[locale]);
  useEffect(()=>{end.current?.scrollIntoView({behavior:'smooth'});},[turns,busy]);
  useEffect(()=>{
    function focus(event:MessageEvent){if(event.source===window.parent&&event.data?.type==='ccifj:focus')input.current?.focus();}
    function escape(event:KeyboardEvent){if(event.key==='Escape')close();}
    window.addEventListener('message',focus);window.addEventListener('keydown',escape);
    return()=>{window.removeEventListener('message',focus);window.removeEventListener('keydown',escape);};
  },[]);
  async function ask(text:string,append=true){
    const message=text.trim();if(!message||pending.current)return;
    const controller=new AbortController();
    activeRequest.current=controller;
    pending.current=true;setBusy(true);setProgressMessages([w.starting]);setDraft('');
    if(append)setTurns(old=>[...old,{id:++nextId.current,role:'user',text:message}]);
    try{
      const response=await sendChatStream({message,locale,session_id:session.current},setProgressMessages,controller.signal);
      session.current=response.session_id??session.current;
      setTurns(old=>[...old,{id:++nextId.current,role:'assistant',text:response.answer,response}]);
    }catch(error){
      if(controller.signal.aborted){setTurns(old=>[...old,{id:++nextId.current,role:'assistant',text:w.cancelled,cancelled:true}]);}
      else setTurns(old=>[...old,{id:++nextId.current,role:'assistant',text:error instanceof ApiError?error.message:t.errorBody,error:true,retry:message}]);
    }finally{if(activeRequest.current===controller)activeRequest.current=null;pending.current=false;setBusy(false);setProgressMessages([]);input.current?.focus();}
  }
  async function decide(approvalId:string,decision:'approve'|'reject'){
    if(!session.current||pending.current)return;
    pending.current=true;setBusy(true);
    try{
      const response=await resolveApproval({session_id:session.current,interrupt_id:approvalId,decision,locale});
      setTurns(old=>[...old.map(turn=>turn.response?.pending_approvals?.some(item=>item.interrupt_id===approvalId)&&turn.response
        ?{...turn,response:{...turn.response,pending_approvals:turn.response.pending_approvals?.filter(item=>item.interrupt_id!==approvalId)}}:turn),{id:++nextId.current,role:'assistant',text:response.answer,response}]);
    }catch(error){
      setTurns(old=>[...old,{id:++nextId.current,role:'assistant',text:error instanceof ApiError?error.message:t.errorBody,error:true}]);
    }finally{pending.current=false;setBusy(false);input.current?.focus();}
  }
  function submit(event:FormEvent){event.preventDefault();void ask(draft);}
  return <div className="widget-chat">
    <header className="widget-header"><span className="widget-mark"><MessageCircle size={23}/></span><div><strong>{t.assistantTitle}</strong><small>{w.note}</small></div>{controlledLocale===undefined&&<button className="icon-button" aria-label={w.close} onClick={close}><X size={21}/></button>}</header>
    <div className="widget-toolbar"><span>{locale==='ja'?'フランス':locale==='fr'?'FRANCE':'FRANCE'} <i>×</i> {locale==='ja'?'日本':locale==='fr'?'JAPON':'JAPAN'}</span><div><button className="icon-button new-chat" disabled={busy} aria-label={w.new} onClick={()=>{session.current=null;setTurns([]);setDraft('');input.current?.focus();}}><Plus size={17}/></button>{controlledLocale===undefined&&<select aria-label={t.language} value={locale} onChange={event=>setLocale(event.target.value as Locale)}><option value="en">EN</option><option value="fr">FR</option><option value="ja">日本語</option></select>}</div></div>
    <main className="widget-thread" aria-label={w.title}>
      {!turns.length&&<div className="widget-welcome"><span className="welcome-symbol">日<span>仏</span></span><p className="eyebrow">{w.title}</p><h1>{w.intro}</h1><p>{w.subtitle}</p>{starterQuestions.length>0&&<section className="starter-section" aria-label={w.suggestions}><div className="starter-heading"><span className="starter-spark" aria-hidden="true">✦</span><span>{w.suggestions}</span></div><div className="widget-suggestions">{starterQuestions.map(s=><button key={s} disabled={busy} onClick={()=>void ask(s)}>{s}<ArrowUpRight size={16}/></button>)}</div></section>}</div>}
      <div role="log" aria-live="polite" aria-relevant="additions text">{turns.map(turn=><article key={turn.id} className={`widget-turn ${turn.role} ${turn.error?'error':''} ${turn.cancelled?'cancelled':''}`}><div className="widget-bubble">{turn.role==='assistant'?<AnswerText text={turn.text} sources={turn.response?.sources}/>:turn.text}</div>{turn.retry&&<button className="widget-retry" disabled={busy} onClick={()=>void ask(turn.retry!,false)}>{w.retry}</button>}<NextActions actions={turn.response?.actions} locale={locale}/>{turn.response?.pending_approvals?.map(approval=><ApprovalPanel key={approval.interrupt_id} approval={approval} locale={locale} authenticated={isAuthenticated} disabled={busy} onLogin={onLogin} onDecision={decision=>void decide(approval.interrupt_id,decision)}/>)}{turn.role==='assistant'&&!turn.error&&!turn.cancelled&&<FollowUpQuestions questions={turn.response?.suggested_questions} locale={locale} disabled={busy} onSelect={question=>void ask(question)}/>}</article>)}</div>
      {busy&&<div className="widget-thinking" role="status"><span className="thinking-badge"><Sparkles size={17}/></span><span className="thinking-copy"><span className="thinking-kicker">{w.working}</span><strong className="widget-progress-label">{progressMessages[progressIndex]||w.starting}</strong></span><span className="thinking-indicator" aria-hidden="true"><i/><i/><i/></span></div>}<div ref={end}/>
    </main>
    <form className="widget-composer" onSubmit={submit}><label className="sr-only" htmlFor="widget-question">{t.inputPlaceholder}</label><textarea ref={input} id="widget-question" value={draft} onChange={e=>setDraft(e.target.value)} placeholder={t.inputPlaceholder} rows={2} maxLength={4000} onKeyDown={e=>{if(e.key==='Enter'&&!e.shiftKey&&!e.nativeEvent.isComposing){e.preventDefault();if(!busy)void ask(draft);}}}/>{busy?<button className="cancel-send" type="button" onClick={()=>activeRequest.current?.abort()} aria-label={w.cancel} title={w.cancel}><Square size={15} fill="currentColor"/></button>:<button type="submit" disabled={!draft.trim()} aria-label={t.send}><Send size={18}/></button>}</form>
    <footer className="widget-footer">{locale==='ja'?'公式情報源をご確認ください。':locale==='fr'?'Vérifiez les informations sur les sources officielles.':'Please verify details with official sources.'}</footer>
  </div>;
}
