import {useEffect,useId,useRef,useState,type FormEvent} from 'react';
import {Send,X,ArrowUpRight,Sparkles,Square,Paperclip,Pencil,FileText,PanelLeftOpen,PanelLeftClose,SquarePen,MessageCircle,LogIn,CalendarDays,UsersRound,BriefcaseBusiness} from 'lucide-react';
import {sendChatStream,getStarterPaths,resolveApproval,reconcileSession,ApiError} from './api';
import {AnswerText} from './AnswerText';
import {NextActions} from './NextActions';
import {FollowUpQuestions} from './FollowUpQuestions';
import {ApprovalPanel} from './ApprovalPanel';
import {getCopy} from './i18n';
import {extractAttachmentText} from './attachments';
import {detectMessageLocale,readLocale} from './site-copy';
import {readConversationHistory,saveConversation,type SavedConversation} from './conversation-history';
import type {ChatResponse,Locale,PathAction} from './types';

type Turn={id:number;role:'user'|'assistant';text:string;response?:ChatResponse;error?:boolean;cancelled?:boolean;retry?:string};
const suggestionIcons=[CalendarDays,UsersRound,BriefcaseBusiness];
const defaultSuggestions={
  en:[{label:'Find an event',prompt:'What upcoming CCIFJ events can I attend?'},{label:'Explore membership',prompt:'How can I become a CCIFJ member?'},{label:'Start a business in Japan',prompt:'How can CCIFJ help me start a business in Japan?'}],
  fr:[{label:'Trouver un événement',prompt:'Quels événements de la CCIFJ sont à venir ?'},{label:'Découvrir l’adhésion',prompt:'Comment devenir membre de la CCIFJ ?'},{label:'Entreprendre au Japon',prompt:'Comment la CCIFJ peut-elle m’aider à créer mon entreprise au Japon ?'}],
  ja:[{label:'イベントを探す',prompt:'これから参加できるCCIFJのイベントを教えてください。'},{label:'会員制度について',prompt:'CCIFJの会員になるにはどうすればよいですか？'},{label:'日本でビジネスを始める',prompt:'日本で事業を始めるためにCCIFJからどのような支援を受けられますか？'}],
};
const words={
  en:{title:'Your connection to Japan',intro:'Hello! How can I help you today?',subtitle:'Membership, events and business support — let’s find your next step.',close:'Close chat',maximize:'Maximize chat',restore:'Restore chat size',new:'New conversation',note:'CCIFJ digital concierge',retry:'Try again',suggestions:'Explore a topic',starting:'The CCIFJ Concierge is reviewing your request…',working:'YOUR CCIFJ TEAM',cancel:'Cancel request',cancelled:'Request cancelled.',sendError:'I couldn’t send that just now. Please try again.',outcome:'The last request may not have finished. Check any bookings or reservations before continuing.',checked:'I’ve checked — continue'},
  fr:{title:'Votre lien avec le Japon',intro:'Bonjour ! Comment puis-je vous aider ?',subtitle:'Adhésion, événements et services aux entreprises : trouvons votre prochaine étape.',close:'Fermer le chat',maximize:'Agrandir le chat',restore:'Restaurer la taille du chat',new:'Nouvelle conversation',note:'Concierge numérique CCIFJ',retry:'Réessayer',suggestions:'Explorez un sujet',starting:'Le concierge CCIFJ examine votre demande…',working:'VOTRE ÉQUIPE CCIFJ',cancel:'Annuler la demande',cancelled:'Demande annulée.',sendError:'Je n’ai pas pu envoyer votre message. Veuillez réessayer.',outcome:'La dernière demande n’a peut-être pas abouti. Vérifiez vos réservations avant de continuer.',checked:'J’ai vérifié, continuer'},
  ja:{title:'日本での次の一歩へ',intro:'こんにちは！何をお手伝いできますか？',subtitle:'会員制度、イベント、ビジネス支援についてご案内します。',close:'チャットを閉じる',maximize:'チャットを最大化',restore:'チャットサイズを戻す',new:'新しい会話',note:'CCIFJ デジタルコンシェルジュ',retry:'再試行',suggestions:'相談できる内容',starting:'CCIFJコンシェルジュがご質問を確認しています…',working:'CCIFJコンシェルジュ',cancel:'リクエストをキャンセル',cancelled:'リクエストをキャンセルしました。',sendError:'メッセージを送信できませんでした。もう一度お試しください。',outcome:'前回のリクエストが完了していない可能性があります。続行する前に予約状況をご確認ください。',checked:'確認しました（続行）'}
};
export function WidgetChat({initialLocale,locale:controlledLocale,initialDraft='',draftVersion=0,onClose,isAuthenticated=false,userId,onLogin=()=>{}}:{initialLocale?:Locale;locale?:Locale;initialDraft?:string;draftVersion?:number;onClose?:()=>void;isAuthenticated?:boolean;userId?:number;onLogin?:()=>void}={}){
  const [localLocale]=useState<Locale>(initialLocale??readLocale());
  const locale=controlledLocale??localLocale;
  const [turns,setTurns]=useState<Turn[]>([]),[draft,setDraft]=useState(initialDraft),[busy,setBusy]=useState(false),[progressMessages,setProgressMessages]=useState<string[]>([]),[progressIndex,setProgressIndex]=useState(0),[starterPaths,setStarterPaths]=useState<PathAction[]>([]),[activePath,setActivePath]=useState<{id:string;context:Record<string,unknown>}|null>(null),[files,setFiles]=useState<File[]>([]),[readingFiles,setReadingFiles]=useState(false),[editId,setEditId]=useState<number|null>(null),[errorNote,setErrorNote]=useState(''),[history,setHistory]=useState<SavedConversation[]>([]),[historyOpen,setHistoryOpen]=useState(false),[outcomeCheck,setOutcomeCheck]=useState(false),[checking,setChecking]=useState(false);
  const session=useRef<string|null>(null),nextId=useRef(0),pending=useRef(false);
  const historyUserId=isAuthenticated&&typeof userId==='number'?userId:null;
  const activeRequest=useRef<AbortController|null>(null);
  const priorAuthState=useRef(isAuthenticated);
  const priorHistoryUserId=useRef(historyUserId);
  const historyToggle=useRef<HTMLButtonElement>(null);
  const historyPanelId=useId();
  const input=useRef<HTMLTextAreaElement>(null),fileInput=useRef<HTMLInputElement>(null),end=useRef<HTMLDivElement>(null);
  const t=getCopy(locale),w=words[locale];
  const historyCloseLabel=locale==='fr'?'Fermer l’historique':locale==='ja'?'履歴を閉じる':'Close chat history';
  const historyOpenLabel=locale==='fr'?'Ouvrir l’historique':locale==='ja'?'チャット履歴を開く':'Open chat history';
  function closeHistory(){setHistoryOpen(false);historyToggle.current?.focus();}
  useEffect(()=>{
    if(!historyOpen)return;
    const handleKey=(event:KeyboardEvent)=>{if(event.key==='Escape'){setHistoryOpen(false);historyToggle.current?.focus();}};
    window.addEventListener('keydown',handleKey);
    return()=>window.removeEventListener('keydown',handleKey);
  },[historyOpen]);
  useEffect(()=>{if(draftVersion){setDraft(initialDraft);input.current?.focus();}},[initialDraft,draftVersion]);
  useEffect(()=>{
    setProgressIndex(0);
    if(progressMessages.length<2)return;
    const timer=window.setInterval(()=>setProgressIndex(index=>(index+1)%progressMessages.length),2600);
    return()=>window.clearInterval(timer);
  },[progressMessages]);
  useEffect(()=>{if(priorAuthState.current!==isAuthenticated||priorHistoryUserId.current!==historyUserId){activeRequest.current?.abort();session.current=null;setTurns([]);setDraft('');setFiles([]);setEditId(null);setActivePath(null);setErrorNote('');setOutcomeCheck(false);priorAuthState.current=isAuthenticated;priorHistoryUserId.current=historyUserId;}},[isAuthenticated,historyUserId]);
  useEffect(()=>{setHistory(readConversationHistory(historyUserId));},[historyUserId]);
  useEffect(()=>{
    if(!session.current||!turns.some(turn=>turn.role==='user'))return;
    const firstUser=turns.find(turn=>turn.role==='user');
    const title=(firstUser?.text.split('\n\n[Extracted from ')[0]||'New conversation').replace(/\s+/g,' ').trim().slice(0,72)||'New conversation';
    setHistory(saveConversation(historyUserId,{sessionId:session.current,title,updatedAt:Date.now(),turns}));
  },[turns,historyUserId]);
  useEffect(()=>{let active=true;getStarterPaths(locale).then(paths=>{if(active)setStarterPaths(paths);});return()=>{active=false;};},[locale]);
  useEffect(()=>{document.documentElement.lang=locale;},[locale]);
  useEffect(()=>{
    const thread=end.current?.parentElement;
    if(!thread)return;
    const reducedMotion=window.matchMedia('(prefers-reduced-motion: reduce)').matches;
    thread.scrollTo({top:turns.length||busy?thread.scrollHeight:0,behavior:turns.length&&!reducedMotion?'smooth':'auto'});
  },[turns,busy]);
  useEffect(()=>{
    const textarea=input.current;
    if(!textarea)return;
    const resize=()=>{textarea.style.height='auto';textarea.style.height=`${Math.min(textarea.scrollHeight,160)}px`;};
    resize();
    let lastWidth=textarea.clientWidth;
    const observer=new ResizeObserver(()=>{
      if(textarea.clientWidth!==lastWidth){lastWidth=textarea.clientWidth;resize();}
    });
    observer.observe(textarea);
    return()=>observer.disconnect();
  },[draft]);
  useEffect(()=>{
    function focus(event:MessageEvent){if(event.source===window.parent&&event.data?.type==='ccifj:focus')input.current?.focus();}
    window.addEventListener('message',focus);
    return()=>{window.removeEventListener('message',focus);};
  },[]);
  async function ask(text:string,append=true,path?:{id:string;event_id?:string;data?:Record<string,unknown>},attachments:File[]=[],replaceId:number|null=null){
    let message=text.trim();if((!message&&!attachments.length)||pending.current||outcomeCheck)return;
    const requestOwner=historyUserId,requestAuth=isAuthenticated;
    const sameOwner=()=>priorHistoryUserId.current===requestOwner&&priorAuthState.current===requestAuth;
    setErrorNote('');
    pending.current=true;
    if(attachments.length)setReadingFiles(true);
    try{if(attachments.length){setProgressMessages([locale==='fr'?'Lecture des fichiers dans votre navigateur…':locale==='ja'?'ファイルをブラウザー内で読み取り中…':'Reading files in your browser…']);const extracted=await extractAttachmentText(attachments,setProgress=>setProgressMessages([setProgress]));message=[message,extracted].filter(Boolean).join('\n\n').slice(0,4000);}}catch(error){setErrorNote(error instanceof Error?error.message:'Could not read the selected files.');setProgressMessages([]);setReadingFiles(false);pending.current=false;return;}
    setReadingFiles(false);
    if(!sameOwner()){pending.current=false;setProgressMessages([]);return;}
    const displayMessage=message;
    const requestMessage=replaceId===null?message:`Edited user message (replace the previous request):\n\n${message.slice(0,3900)}`;
    const promptLocale=detectMessageLocale(displayMessage.split('\n\n[Extracted from ')[0]??displayMessage,locale);
    const controller=new AbortController();
    activeRequest.current=controller;
    pending.current=true;setBusy(true);setProgressMessages([w.starting]);setDraft('');setFiles([]);setEditId(null);
    if(append)setTurns(old=>{const retained=replaceId===null?old:old.filter(turn=>turn.id<replaceId);return [...retained,{id:replaceId??++nextId.current,role:'user',text:displayMessage}];});
    try{
      const pathStepId=path?.id??activePath?.id;
      const pathContext=path?.data??activePath?.context;
      const response=await sendChatStream({message:requestMessage,locale:promptLocale,session_id:session.current,...(pathStepId?{path_step_id:pathStepId}:{}),...(path?.event_id?{path_event_id:path.event_id}:{}),...(pathContext?{path_context:pathContext}:{})},setProgressMessages,controller.signal);
      if(!sameOwner())return;
      session.current=response.session_id??session.current;
      setActivePath(response.active_path_step?{id:response.active_path_step,context:response.path_context??pathContext??{}}:null);
      setTurns(old=>[...old,{id:++nextId.current,role:'assistant',text:response.answer,response}]);
    }catch(error){
      if(!sameOwner())return;
      // The server records an interrupted or failed turn as uncertain; the user must confirm before continuing.
      const uncertain=Boolean(session.current)&&(controller.signal.aborted||(error instanceof ApiError&&error.outcomeUnknown));
      if(uncertain)setOutcomeCheck(true);
      if(controller.signal.aborted){setTurns(old=>[...old,{id:++nextId.current,role:'assistant',text:w.cancelled,cancelled:true}]);}
      else setTurns(old=>[...old,{id:++nextId.current,role:'assistant',text:error instanceof ApiError&&(error.status===undefined||error.status>=500)?w.sendError:error instanceof ApiError?error.message:t.errorBody,error:true,retry:uncertain?undefined:message}]);
    }finally{if(activeRequest.current===controller)activeRequest.current=null;pending.current=false;setBusy(false);setProgressMessages([]);input.current?.focus();}
  }
  async function decide(approvalId:string,decision:'approve'|'reject'){
    if(!session.current||pending.current)return;
    pending.current=true;setBusy(true);
    try{
      const response=await resolveApproval({session_id:session.current,interrupt_id:approvalId,decision,locale});
      // The decision response lists every approval still pending, so earlier cards are superseded.
      setTurns(old=>[...old.map(turn=>turn.response?.pending_approvals?.length
        ?{...turn,response:{...turn.response,pending_approvals:[]}}:turn),{id:++nextId.current,role:'assistant',text:response.answer,response}]);
    }catch(error){
      if(error instanceof ApiError&&error.outcomeUnknown)setOutcomeCheck(true);
      setTurns(old=>[...old,{id:++nextId.current,role:'assistant',text:error instanceof ApiError?error.message:t.errorBody,error:true}]);
    }finally{pending.current=false;setBusy(false);input.current?.focus();}
  }
  async function acknowledgeOutcome(){
    if(!session.current||checking)return;
    setChecking(true);setErrorNote('');
    try{await reconcileSession(session.current);setOutcomeCheck(false);input.current?.focus();}
    catch(error){setErrorNote(error instanceof ApiError?error.message:t.errorBody);}
    finally{setChecking(false);}
  }
  function startNewConversation(){if(busy||readingFiles)return;session.current=null;setTurns([]);setDraft('');setFiles([]);setEditId(null);setActivePath(null);setErrorNote('');setOutcomeCheck(false);input.current?.focus();}
  function submit(event:FormEvent){event.preventDefault();void ask(draft,true,undefined,files,editId);}
  return <div className={`widget-chat ${turns.length?'has-messages':'is-empty'} ${historyOpen?'history-open':''}`}>
    {historyOpen&&<button type="button" className="history-scrim" aria-label={historyCloseLabel} onClick={closeHistory} tabIndex={-1}/>}
    <aside className="history-sidebar" aria-label={locale==='fr'?'Conversations':locale==='ja'?'会話':'Conversations'}>
      <button ref={historyToggle} className="history-rail-button" type="button" aria-label={historyOpen?historyCloseLabel:historyOpenLabel} aria-expanded={historyOpen} aria-controls={historyPanelId} title={historyOpen?historyCloseLabel:historyOpenLabel} onClick={()=>historyOpen?closeHistory():setHistoryOpen(true)}>
        {historyOpen?<PanelLeftClose size={21}/>:<PanelLeftOpen size={21}/>}<span className="history-toggle-label">{locale==='fr'?'Conversations':locale==='ja'?'会話':'Conversations'}</span>
      </button>
      <button className="new-chat-rail-button" type="button" aria-label={w.new} title={w.new} disabled={busy||readingFiles} onClick={()=>{startNewConversation();if(window.matchMedia('(max-width: 720px)').matches)setHistoryOpen(false);}}><SquarePen size={20}/><span>{w.new}</span></button>
      <div id={historyPanelId} className="history-content" hidden={!historyOpen}>
        <div className="history-heading"><div><strong>{locale==='fr'?'Vos conversations':locale==='ja'?'会話履歴':'Your conversations'}</strong><small>{isAuthenticated?(locale==='fr'?'Enregistrées sur cet appareil':locale==='ja'?'この端末に保存':'Saved on this device'):(locale==='fr'?'Connectez-vous pour conserver vos conversations':locale==='ja'?'ログインすると会話を保存できます':'Sign in to save your chats')}</small></div></div>
        {history.length===0?<div className="history-empty"><MessageCircle size={25} aria-hidden="true"/><p>{isAuthenticated?(locale==='fr'?'Vos nouvelles conversations apparaîtront ici.':locale==='ja'?'新しい会話はここに表示されます。':'New conversations will appear here.'):(locale==='fr'?'Retrouvez vos conversations en vous connectant. Vos chats invités restent temporaires.':locale==='ja'?'ログインすると過去の会話を保存できます。ゲストの会話は一時的です。':'Sign in to keep your conversations. Guest chats stay temporary.')}</p>{!isAuthenticated&&<button className="history-signin" type="button" onClick={onLogin}><LogIn size={16}/>{locale==='fr'?'Se connecter':locale==='ja'?'ログイン':'Sign in'}</button>}</div>:<div className="history-list">{history.map(item=><button type="button" disabled={busy||readingFiles} className={`history-item ${session.current===item.sessionId?'selected':''}`} key={item.sessionId} onClick={()=>{if(busy||readingFiles)return;session.current=item.sessionId;setTurns(item.turns as Turn[]);nextId.current=Math.max(0,...item.turns.map(turn=>turn.id));setDraft('');setFiles([]);setEditId(null);setActivePath(null);setErrorNote('');setOutcomeCheck(false);if(window.matchMedia('(max-width: 720px)').matches)setHistoryOpen(false);input.current?.focus();}}><strong>{item.title}</strong><span>{new Date(item.updatedAt).toLocaleString(locale,{dateStyle:'medium',timeStyle:'short'})}</span></button>)}</div>}
      </div>
    </aside>
    <div className="chat-main">

    <div className="widget-thread" aria-label={w.title}>
      {!turns.length&&<div className="widget-welcome">
        <div className="welcome-brand-mark" aria-hidden="true"><MessageCircle size={31} strokeWidth={1.6}/><span/></div>
        <p className="eyebrow">{w.title}</p><h1>{w.intro}</h1><p>{w.subtitle}</p>
        <section className="starter-section" aria-label={w.suggestions}>
          <div className="starter-heading"><span>{w.suggestions}</span></div>
          <div className="widget-suggestions">
            {starterPaths.length>0?starterPaths.map((path,index)=>{const Icon=suggestionIcons[index%suggestionIcons.length];return <button key={path.id} disabled={busy||readingFiles} onClick={()=>void ask(path.label,true,{id:path.id,data:path.data})}><span className="suggestion-icon"><Icon size={19}/></span><span>{path.label}</span><ArrowUpRight size={16}/></button>;}):defaultSuggestions[locale].map((suggestion,index)=>{const Icon=suggestionIcons[index];return <button key={suggestion.label} disabled={busy||readingFiles} onClick={()=>void ask(suggestion.prompt)}><span className="suggestion-icon"><Icon size={19}/></span><span>{suggestion.label}</span><ArrowUpRight size={16}/></button>;})}
          </div>
        </section>
      </div>}
      <div role="log" aria-live="polite" aria-relevant="additions text">{turns.map(turn=><article key={turn.id} className={`widget-turn ${turn.role} ${turn.error?'error':''} ${turn.cancelled?'cancelled':''}`}><div className="widget-bubble" dir="auto">{turn.role==='assistant'?<AnswerText text={turn.text} sources={turn.response?.sources}/>:turn.text}</div>{turn.role==='user'&&!busy&&<button className="widget-edit" type="button" aria-label={locale==='fr'?'Modifier le message':locale==='ja'?'メッセージを編集':'Edit message'} onClick={()=>{setEditId(turn.id);setDraft(turn.text);setErrorNote('');input.current?.focus();}}><Pencil size={13}/>{locale==='fr'?'Modifier':locale==='ja'?'編集':'Edit'}</button>}{turn.retry&&<button className="widget-retry" disabled={busy} onClick={()=>void ask(turn.retry!,false)}>{w.retry}</button>}<NextActions actions={turn.response?.actions} locale={locale}/>{turn.response?.pending_approvals?.map(approval=><ApprovalPanel key={approval.interrupt_id} approval={approval} locale={locale} authenticated={isAuthenticated} disabled={busy} onLogin={onLogin} onDecision={decision=>void decide(approval.interrupt_id,decision)}/>)}{turn.role==='assistant'&&!turn.error&&!turn.cancelled&&<FollowUpQuestions questions={turn.response?.suggested_questions} pathActions={turn.response?.path_actions} locale={locale} disabled={busy} onSelect={question=>void ask(question)} onSelectPath={action=>void ask(action.label,true,{id:action.id,event_id:action.event_id,data:action.data})} nextActions={turn.response?.next_actions} onSelectAction={action=>action.id==='auth.login'?onLogin():void ask(action.prompt)}/>}</article>)}</div>
      {busy&&<div className="widget-thinking" role="status"><span className="thinking-badge"><Sparkles size={17}/></span><span className="thinking-copy"><span className="thinking-kicker">{w.working}</span><strong className="widget-progress-label">{progressMessages[progressIndex]||w.starting}</strong></span><span className="thinking-indicator" aria-hidden="true"><i/><i/><i/></span></div>}<div ref={end}/>
    </div>
    {readingFiles&&<div className="file-progress" role="status">{progressMessages[0]||'Reading your files…'}</div>}{editId!==null&&<div className="edit-banner">{locale==='fr'?'Modification du message précédent':locale==='ja'?'前のメッセージを編集中':'Editing your previous message'}<button type="button" onClick={()=>{setEditId(null);setDraft('');}} aria-label={w.close}><X size={14}/></button></div>}{files.length>0&&<div className="attachment-list">{files.map((file,index)=><span key={`${file.name}-${index}`}><FileText size={14}/>{file.name}<button type="button" aria-label={`Remove ${file.name}`} onClick={()=>setFiles(old=>old.filter((_,i)=>i!==index))}><X size={13}/></button></span>)}</div>}{outcomeCheck&&<div className="outcome-check" role="alert"><p>{w.outcome}</p><button type="button" disabled={checking||busy} onClick={()=>void acknowledgeOutcome()}>{w.checked}</button></div>}{errorNote&&<p className="attachment-error" role="alert">{errorNote}</p>}<form className="widget-composer" onSubmit={submit}><label className="sr-only" htmlFor="widget-question">{t.inputPlaceholder}</label><input ref={fileInput} className="sr-only" type="file" multiple accept="image/*,.pdf,.docx,.txt,.md,.csv,.json,.xml,.html" onChange={event=>{const picked=Array.from(event.target.files??[]);setFiles(old=>[...old,...picked].slice(0,5));event.target.value='';}}/><button className="attach-button" type="button" disabled={busy||readingFiles} aria-label={locale==='fr'?'Joindre une image ou un document':locale==='ja'?'画像または書類を添付':'Attach an image or document'} title={locale==='fr'?'Joindre une image ou un document':locale==='ja'?'画像または書類を添付':'Attach an image or document'} onClick={()=>fileInput.current?.click()}><Paperclip size={18}/></button><textarea ref={input} id="widget-question" dir="auto" value={draft} onChange={e=>setDraft(e.target.value)} placeholder={t.inputPlaceholder} rows={1} maxLength={4000} onKeyDown={e=>{if(e.key==='Enter'&&!e.shiftKey&&!e.nativeEvent.isComposing){e.preventDefault();if(!busy&&!readingFiles)void ask(draft,true,undefined,files,editId);}}}/>{busy?<button className="cancel-send" type="button" onClick={()=>activeRequest.current?.abort()} aria-label={w.cancel} title={w.cancel}><Square size={15} fill="currentColor"/></button>:<button type="submit" disabled={readingFiles||outcomeCheck||(!draft.trim()&&!files.length)} aria-label={t.send}><Send size={18}/></button>}</form><div className="attachment-hint">{locale==='fr'?'Ajoutez une image ou un document.':locale==='ja'?'画像や書類を添付できます。':'Add an image or document.'}</div>
    <footer className="widget-footer">{locale==='ja'?'公式情報源をご確認ください。':locale==='fr'?'Vérifiez les informations sur les sources officielles.':'Please verify details with official sources.'}</footer>
    </div>
  </div>;
}
