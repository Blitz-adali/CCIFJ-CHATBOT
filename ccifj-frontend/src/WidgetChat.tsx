import {useEffect,useRef,useState,useSyncExternalStore,type FormEvent} from 'react';
import {MessageCircle,Send,X,Plus,ArrowUpRight,Sparkles,Square} from 'lucide-react';
import {getSuggestedQuestions} from './api';
import {ChatSession} from './chat-session';
import {AnswerText} from './AnswerText';
import {NextActions} from './NextActions';
import {FollowUpQuestions} from './FollowUpQuestions';
import {ApprovalPanel} from './ApprovalPanel';
import {getCopy} from './i18n';
import type {Locale} from './types';

const words={
  en:{title:'Your connection to Japan',intro:'Hello! How can I help you today?',subtitle:'Membership, events and business support — let’s find your next step.',close:'Close chat',new:'New conversation',note:'CCIFJ digital concierge',retry:'Try again',suggestions:'Explore a topic',starting:'The CCIFJ Concierge is reviewing your request…',working:'YOUR CCIFJ TEAM',cancel:'Stop waiting'},
  fr:{title:'Votre lien avec le Japon',intro:'Bonjour ! Comment puis-je vous aider ?',subtitle:'Adhésion, événements et services aux entreprises : trouvons votre prochaine étape.',close:'Fermer le chat',new:'Nouvelle conversation',note:'Concierge numérique CCIFJ',retry:'Réessayer',suggestions:'Explorez un sujet',starting:'Le concierge CCIFJ examine votre demande…',working:'VOTRE ÉQUIPE CCIFJ',cancel:'Arrêter l’attente'},
  ja:{title:'日本での次の一歩へ',intro:'こんにちは！何をお手伝いできますか？',subtitle:'会員制度、イベント、ビジネス支援についてご案内します。',close:'チャットを閉じる',new:'新しい会話',note:'CCIFJ デジタルコンシェルジュ',retry:'再試行',suggestions:'相談できる内容',starting:'CCIFJコンシェルジュがご質問を確認しています…',working:'CCIFJコンシェルジュ',cancel:'応答待ちを終了'}
};
export function WidgetChat({initialLocale,locale:controlledLocale,initialDraft='',draftVersion=0,onClose,isAuthenticated=false,onLogin=()=>{}}:{initialLocale?:Locale;locale?:Locale;initialDraft?:string;draftVersion?:number;onClose?:()=>void;isAuthenticated?:boolean;onLogin?:()=>void}={}){
  const initial=new URLSearchParams(window.location.search).get('lang');
  const [localLocale,setLocale]=useState<Locale>(initialLocale??(initial==='fr'||initial==='ja'?initial:'en'));
  const locale=controlledLocale??localLocale;
  const [chat]=useState(()=>new ChatSession());
  const state=useSyncExternalStore(chat.subscribe,chat.snapshot,chat.snapshot);
  const {turns,busy,progress:progressMessages}=state;
  const [draft,setDraft]=useState(initialDraft),[progressIndex,setProgressIndex]=useState(0),[starterQuestions,setStarterQuestions]=useState<string[]>([]);
  const blocked=busy||state.approvals.length>0||state.outcome!=='ready';
  useEffect(()=>{chat.start();return()=>chat.stop();},[chat]);
  const input=useRef<HTMLTextAreaElement>(null),end=useRef<HTMLDivElement>(null);
  const t=getCopy(locale),w=words[locale];
  useEffect(()=>{if(draftVersion){setDraft(initialDraft);input.current?.focus();}},[initialDraft,draftVersion]);
  useEffect(()=>{
    setProgressIndex(0);
    if(progressMessages.length<2)return;
    const timer=window.setInterval(()=>setProgressIndex(index=>(index+1)%progressMessages.length),2600);
    return()=>window.clearInterval(timer);
  },[progressMessages]);
  useEffect(()=>{let active=true;getSuggestedQuestions().then(questions=>{if(active)setStarterQuestions(questions);}).catch(()=>{if(active)setStarterQuestions([]);});return()=>{active=false;};},[]);
  function close(){if(onClose)onClose();else window.parent.postMessage({type:'ccifj:close'},'*');}
  useEffect(()=>{document.documentElement.lang=locale;},[locale]);
  useEffect(()=>{end.current?.scrollIntoView({behavior:'smooth'});},[turns,busy,state.streamingText]);
  useEffect(()=>{
    function focus(event:MessageEvent){if(event.source===window.parent&&event.data?.type==='ccifj:focus')input.current?.focus();}
    function escape(event:KeyboardEvent){if(event.key==='Escape')close();}
    window.addEventListener('message',focus);window.addEventListener('keydown',escape);
    return()=>{window.removeEventListener('message',focus);window.removeEventListener('keydown',escape);};
  },[]);
  async function ask(text:string){
    if(blocked||!text.trim())return;
    setDraft('');
    await chat.ask(text,locale);
    input.current?.focus();
  }
  function submit(event:FormEvent){event.preventDefault();void ask(draft);}
  return <div className="widget-chat">
    <header className="widget-header"><span className="widget-mark"><MessageCircle size={23}/></span><div><strong>{t.assistantTitle}</strong><small>{w.note}</small></div>{controlledLocale===undefined&&<button className="icon-button" aria-label={w.close} onClick={close}><X size={21}/></button>}</header>
    <div className="widget-toolbar"><span>{locale==='ja'?'フランス':locale==='fr'?'FRANCE':'FRANCE'} <i>×</i> {locale==='ja'?'日本':locale==='fr'?'JAPON':'JAPAN'}</span><div><button className="icon-button new-chat" disabled={busy} aria-label={w.new} onClick={()=>{chat.newConversation();setDraft('');input.current?.focus();}}><Plus size={17}/></button>{controlledLocale===undefined&&<select aria-label={t.language} value={locale} onChange={event=>setLocale(event.target.value as Locale)}><option value="en">EN</option><option value="fr">FR</option><option value="ja">日本語</option></select>}</div></div>
    <main className="widget-thread" aria-label={w.title}>
      {!turns.length&&<div className="widget-welcome"><span className="welcome-symbol">日<span>仏</span></span><p className="eyebrow">{w.title}</p><h1>{w.intro}</h1><p>{w.subtitle}</p>{starterQuestions.length>0&&<section className="starter-section" aria-label={w.suggestions}><div className="starter-heading"><span className="starter-spark" aria-hidden="true">✦</span><span>{w.suggestions}</span></div><div className="widget-suggestions">{starterQuestions.map(s=><button key={s} disabled={blocked} onClick={()=>void ask(s)}>{s}<ArrowUpRight size={16}/></button>)}</div></section>}</div>}
      <div role="log" aria-live="polite" aria-relevant="additions text">{turns.map(turn=><article key={turn.id} className={`widget-turn ${turn.role} ${turn.error?'error':''} `}><div className="widget-bubble">{turn.role==='assistant'?<AnswerText text={turn.text} sources={turn.response?.sources}/>:turn.text}</div>{turn.retry&&<button className="widget-retry" disabled={blocked} onClick={()=>void chat.retry(turn.retry!,locale)}>{w.retry}</button>}{turn.response?.warnings?.map((warning,index)=><p role="status" key={index}>{warning}</p>)}<NextActions actions={turn.response?.actions} locale={locale}/>{turn.role==='assistant'&&!turn.error&&<FollowUpQuestions questions={turn.response?.suggested_questions} locale={locale} disabled={blocked} onSelect={question=>void ask(question)}/>}</article>)}</div>
      {state.streamingText&&<article className="widget-turn assistant"><AnswerText text={state.streamingText}/></article>}
      {state.approvals.map(approval=><ApprovalPanel key={approval.interrupt_id} approval={approval} locale={locale} authenticated={isAuthenticated} disabled={busy||state.outcome!=='ready'} onLogin={onLogin} onDecision={decision=>void chat.decide(approval.interrupt_id,decision,locale)}/>)}
      {state.notice&&<p role="status">{state.notice}</p>}
      {state.outcome==='processing'&&<p role="status">{locale==='fr'?'La demande est toujours en cours.':locale==='ja'?'リクエストを処理中です。':'The server is still processing this request.'}</p>}
      {state.outcome==='login'&&<button type="button" onClick={onLogin}>{locale==='fr'?'Se reconnecter':locale==='ja'?'再ログイン':'Log in again'}</button>}
      {state.outcome==='unknown'&&<div role="alert"><p>{locale==='fr'?'Vérifiez le résultat et toute réservation avant de continuer.':locale==='ja'?'続行する前に、処理結果と予約をご確認ください。':'Check the request outcome and any bookings before continuing.'}</p><button type="button" disabled={busy} onClick={()=>void chat.recover()}>{locale==='fr'?'Actualiser la conversation':locale==='ja'?'会話を更新':'Refresh conversation'}</button><button type="button" disabled={busy} onClick={()=>void chat.acknowledge()}>{locale==='fr'?'J’ai vérifié le résultat':locale==='ja'?'処理結果を確認しました':'I have checked the outcome'}</button></div>}
      {busy&&<div className="widget-thinking" role="status"><span className="thinking-badge"><Sparkles size={17}/></span><span className="thinking-copy"><span className="thinking-kicker">{w.working}</span><strong className="widget-progress-label">{progressMessages[progressIndex]||w.starting}</strong></span><span className="thinking-indicator" aria-hidden="true"><i/><i/><i/></span></div>}<div ref={end}/>
    </main>
    <form className="widget-composer" onSubmit={submit}><label className="sr-only" htmlFor="widget-question">{t.inputPlaceholder}</label><textarea ref={input} id="widget-question" value={draft} disabled={blocked} onChange={e=>setDraft(e.target.value)} placeholder={t.inputPlaceholder} rows={2} maxLength={4000} onKeyDown={e=>{if(e.key==='Enter'&&!e.shiftKey&&!e.nativeEvent.isComposing){e.preventDefault();if(!blocked)void ask(draft);}}}/>{busy?<button className="cancel-send" type="button" onClick={()=>chat.cancel()} aria-label={w.cancel} title={w.cancel}><Square size={15} fill="currentColor"/></button>:<button type="submit" disabled={blocked||!draft.trim()} aria-label={t.send}><Send size={18}/></button>}</form>
    <footer className="widget-footer">{locale==='ja'?'公式情報源をご確認ください。':locale==='fr'?'Vérifiez les informations sur les sources officielles.':'Please verify details with official sources.'}</footer>
  </div>;
}
