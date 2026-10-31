import {afterEach,beforeEach,describe,expect,it,vi} from 'vitest';
import type {AuthChange} from './auth-session';
import type {ChatHistory} from './types';
const mock=vi.hoisted(()=>({create:vi.fn(),history:vi.fn(),claim:vi.fn(),reconcile:vi.fn(),send:vi.fn(),approve:vi.fn(),auth:null as {user:{user_id:number}}|null,listeners:new Set<(change:AuthChange)=>void>()}));
vi.mock('./api',async original=>({...(await original<typeof import('./api')>()),createChatSession:mock.create,getChatHistory:mock.history,claimChatSession:mock.claim,reconcileChatSession:mock.reconcile,sendLiveChat:mock.send,resolveApproval:mock.approve,getSavedAuthSession:()=>mock.auth,subscribeAuth:(listener:(change:AuthChange)=>void)=>{mock.listeners.add(listener);return()=>mock.listeners.delete(listener);}}));
const approval=(id:string)=>({interrupt_id:id,actions:[{name:'subscribe_newsletter',args:{}}]});
function history(overrides:Partial<ChatHistory>={}):ChatHistory{return {session_id:'signed',messages:[{message_id:1,role:'user',content:'hello',created_at:''},{message_id:2,role:'assistant',content:'Hi',created_at:''}],pending_approvals:[],next_after_id:null,last_request_id:null,turn_status:'ready',...overrides};}
const flush=async()=>{for(let i=0;i<12;i++)await Promise.resolve();};
let active:import('./chat-session').ChatSession;
beforeEach(async()=>{
  vi.resetModules();vi.useFakeTimers();mock.auth=null;mock.listeners.clear();
  for(const fn of [mock.create,mock.history,mock.claim,mock.reconcile,mock.send,mock.approve])fn.mockReset();
  const storage=new Map<string,string>();
  vi.stubGlobal('localStorage',{getItem:(key:string)=>storage.get(key)??null,setItem:(key:string,value:string)=>storage.set(key,value),removeItem:(key:string)=>storage.delete(key)});
  mock.create.mockResolvedValue('signed');mock.history.mockResolvedValue(history());mock.claim.mockResolvedValue(undefined);mock.reconcile.mockResolvedValue(undefined);
  mock.send.mockImplementation(async request=>({session_id:'signed',answer:'Hi',request_id:request.request_id,pending_approvals:[]}));
  const {ChatSession}=await import('./chat-session');active=new ChatSession();active.start();
});
afterEach(()=>{active.stop();vi.useRealTimers();vi.unstubAllGlobals();});
describe('conversation lifecycle',()=>{
  it('restores messages and approval controls after recreation',async()=>{
    await active.ask('hello','en');active.stop();
    mock.history.mockResolvedValue(history({pending_approvals:[approval('a')]}));
    const {ChatSession}=await import('./chat-session');active=new ChatSession();active.start();await flush();
    expect(active.state.sessionId).toBe('signed');expect(active.state.turns.map(t=>t.text)).toEqual(['hello','Hi']);expect(active.state.approvals).toHaveLength(1);
    await active.ask('another','en');expect(mock.send).toHaveBeenCalledTimes(1);
  });
  it('claims and preserves a guest conversation on login',async()=>{
    await active.ask('hello','en');
    const current={access_token:'login',token_type:'bearer' as const,user:{user_id:7,name:'A',email:'a@example.com'}};
    mock.auth=current;
    mock.listeners.forEach(listener=>listener({previous:null,current,reason:'login'}));await flush();
    expect(mock.claim).toHaveBeenCalledWith('signed',expect.any(AbortSignal));expect(active.state.sessionId).toBe('signed');expect(active.state.turns).toHaveLength(2);
  });
  it('ignores an old response after logout even when the transport ignores abort',async()=>{
    const current={access_token:'login',token_type:'bearer' as const,user:{user_id:7,name:'A',email:'a@example.com'}};
    mock.auth=current;mock.listeners.forEach(listener=>listener({previous:null,current,reason:'login'}));await flush();
    let finish!:(value:unknown)=>void;mock.send.mockImplementation(()=>new Promise(resolve=>{finish=resolve;}));
    const pending=active.ask('private question','en');await flush();
    mock.auth=null;mock.listeners.forEach(listener=>listener({previous:current,current:null,reason:'logout'}));await flush();
    finish({session_id:'private',answer:'Private answer'});await pending;
    expect(active.state.turns).toEqual([]);expect(active.state.sessionId).toBeNull();
  });
  it('allows guests to decide approvals and replaces the full pending set',async()=>{
    mock.send.mockResolvedValue({session_id:'signed',answer:'Review',pending_approvals:[approval('a'),approval('b')]});await active.ask('subscribe','en');
    mock.approve.mockResolvedValue({session_id:'signed',answer:'More review',pending_approvals:[approval('b')]});await active.decide('a','approve','fr');
    expect(mock.approve).toHaveBeenCalledWith(expect.objectContaining({interrupt_id:'a',locale:'fr'}),expect.any(AbortSignal));expect(active.state.approvals.map(a=>a.interrupt_id)).toEqual(['b']);
  });
  it('keeps unknown writes blocked until explicit reconciliation',async()=>{
    const {ApiError}=await import('./api');mock.send.mockRejectedValue(new ApiError('Unknown write',502,undefined,{session_id:'signed',outcome_unknown:true}));
    mock.history.mockResolvedValue(history({turn_status:'unknown'}));await active.ask('book','en');
    expect(active.state.outcome).toBe('unknown');expect(active.state.turns.some(t=>t.retry)).toBe(false);
    await active.ask('book','en');expect(mock.send).toHaveBeenCalledTimes(1);
    mock.history.mockResolvedValue(history());await active.acknowledge();expect(mock.reconcile).toHaveBeenCalledOnce();expect(active.state.outcome).toBe('ready');
  });
  it('does not interpret an old successful answer as completion of a disconnected new request',async()=>{
    const {ApiError}=await import('./api');mock.send.mockRejectedValue(new ApiError('Disconnected'));await active.ask('book','en');
    expect(active.state.outcome).toBe('unknown');expect(mock.send).toHaveBeenCalledTimes(1);
  });
  it('polls server processing state until this request completes',async()=>{
    const {ApiError}=await import('./api');mock.send.mockRejectedValue(new ApiError('Disconnected'));
    mock.history.mockResolvedValueOnce(history({turn_status:'processing'}));
    const pending=active.ask('book','en');await pending;
    expect(active.state.outcome).toBe('processing');
    const request=mock.send.mock.calls[0][0];mock.history.mockResolvedValue(history({last_request_id:request.request_id}));await vi.advanceTimersByTimeAsync(2001);
    expect(active.state.outcome).toBe('ready');expect(mock.send).toHaveBeenCalledTimes(1);
  });
  it('invalidates streamed deltas after switching identities',async()=>{
    let delta!:(text:string)=>void;let finish!:(value:unknown)=>void;
    mock.send.mockImplementation((_request,_progress,_signal,onDelta)=>{delta=onDelta;return new Promise(resolve=>{finish=resolve;});});
    const pending=active.ask('question','en');await flush();delta('Partial');expect(active.state.streamingText).toBe('Partial');
    active.newConversation();delta('Stale');finish({answer:'Stale',session_id:'old'});await pending;
    expect(active.state.streamingText).toBe('');expect(active.state.turns).toEqual([]);
  });
  it('keeps a safe pre-execution error and question visible without replacing them with empty history',async()=>{
    const {ApiError}=await import('./api');
    mock.send.mockRejectedValue(new ApiError('Model unavailable',503,undefined,{safe_to_retry:true,outcome_unknown:false}));
    mock.history.mockResolvedValue(history({messages:[]}));
    await active.ask('My question','en');
    expect(active.state.turns.map(turn=>turn.text)).toEqual(['My question','Model unavailable']);
    expect(active.state.notice).toBe('Model unavailable');expect(active.state.outcome).toBe('ready');
    expect(mock.history).not.toHaveBeenCalled();expect(active.state.turns.at(-1)?.retry).toBe('My question');
    mock.send.mockResolvedValue({session_id:'signed',answer:'Recovered',pending_approvals:[]});
    await active.retry('My question','en');
    expect(active.state.turns.map(turn=>turn.text)).toEqual(['My question','Recovered']);
  });
  it('permits retry when initial session creation fails, without dispatching or reconciling an action',async()=>{
    const {ApiError}=await import('./api');
    mock.create.mockRejectedValueOnce(new ApiError('Offline'));
    await active.ask('First question','en');
    expect(active.state.sessionId).toBeNull();expect(active.state.outcome).toBe('ready');
    expect(active.state.turns.at(-1)?.retry).toBe('First question');
    expect(mock.send).not.toHaveBeenCalled();expect(mock.reconcile).not.toHaveBeenCalled();
    await active.retry('First question','en');
    expect(mock.send).toHaveBeenCalledOnce();expect(active.state.turns).toHaveLength(2);
  });
  it('retains diagnostics when a safe conflict refreshes pending approvals',async()=>{
    const {ApiError}=await import('./api');
    mock.send.mockRejectedValue(new ApiError('Review the pending action',409));
    mock.history.mockResolvedValue(history({pending_approvals:[approval('pending')]}));
    await active.ask('Another question','en');
    expect(active.state.approvals).toHaveLength(1);
    expect(active.state.notice).toBe('Review the pending action');
    expect(active.state.turns.at(-1)?.text).toBe('Review the pending action');
  });

});
