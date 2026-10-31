import {afterEach, beforeEach, describe, expect, it, vi} from 'vitest';

beforeEach(()=>{vi.resetModules();vi.useFakeTimers();});
afterEach(()=>{vi.unstubAllGlobals();vi.useRealTimers();});
const account = (token='opaque') => ({access_token:token,token_type:'bearer' as const,user:{user_id:12,name:'Demo',email:'demo@example.com'},expires_in:3600});

describe('authentication recovery',()=>{
  it('uses memory credentials when all storage operations throw',async()=>{
    vi.stubGlobal('localStorage',{getItem(){throw Error('denied');},setItem(){throw Error('denied');},removeItem(){throw Error('denied');}});
    const auth=await import('./auth-session');
    auth.saveAuthSession(account());
    vi.stubGlobal('fetch',vi.fn().mockResolvedValue(new Response(JSON.stringify({reply:'Hello'}))));
    const api=await import('./api');
    await api.sendChat({session_id:null,message:'Hello',locale:'en'});
    expect(fetch).toHaveBeenCalledWith('/api/v1/chat',expect.objectContaining({headers:expect.objectContaining({Authorization:'Bearer opaque'})}));
    auth.clearAuthSession();
    expect(auth.getSavedAuthSession()).toBeNull();
  });
  it('expires credentials and notifies the UI without waiting for a request',async()=>{
    const auth=await import('./auth-session');
    auth.saveAuthSession({...account(),expires_in:2});
    const listener=vi.fn();
    const stop=auth.subscribeAuth(listener);
    await vi.advanceTimersByTimeAsync(2001);
    expect(auth.getSavedAuthSession()).toBeNull();
    expect(listener).toHaveBeenCalledWith(expect.objectContaining({reason:'expired',current:null}));
    stop();
  });
  it('rejects an expired restored JWT',async()=>{
    const token=`e30.${btoa(JSON.stringify({exp:Math.floor(Date.now()/1000)-10}))}.signature`;
    vi.stubGlobal('localStorage',{getItem:()=>JSON.stringify(account(token)),removeItem:vi.fn()});
    const auth=await import('./auth-session');
    expect(auth.getSavedAuthSession()).toBeNull();
  });
  it('invalidates the matching token on 401 and preserves structured error context',async()=>{
    const auth=await import('./auth-session');
    auth.saveAuthSession(account());
    const listener=vi.fn();auth.subscribeAuth(listener);
    vi.stubGlobal('fetch',vi.fn().mockResolvedValue(new Response(JSON.stringify({detail:{message:'Expired',session_id:'conversation',outcome_unknown:false}}),{status:401})));
    const {sendChat}=await import('./api');
    await expect(sendChat({session_id:'conversation',message:'hi',locale:'en'})).rejects.toMatchObject({status:401,sessionId:'conversation',outcomeUnknown:false});
    expect(auth.getSavedAuthSession()).toBeNull();
    expect(listener).toHaveBeenCalledWith(expect.objectContaining({reason:'expired'}));
  });
  it('does not clear a newer login when an older token receives a late 401',async()=>{
    const auth=await import('./auth-session');
    auth.saveAuthSession(account('old'));
    auth.saveAuthSession(account('new'));
    auth.invalidateAuth('old');
    expect(auth.getSavedAuthSession()?.access_token).toBe('new');
    auth.clearAuthSession();
  });
});

describe('cross-tab authentication',()=>{
  function environment(){
    const target=new EventTarget();
    const values=new Map<string,string>();
    const storage={getItem:(key:string)=>values.get(key)??null,setItem:(key:string,value:string)=>values.set(key,value),removeItem:(key:string)=>values.delete(key)};
    vi.stubGlobal('window',target);vi.stubGlobal('localStorage',storage);
    function notify(key:string|null,newValue:string|null){target.dispatchEvent(Object.assign(new Event('storage'),{key,newValue,storageArea:storage}));}
    return {target,values,storage,notify};
  }
  it('clears credentials and notifies conversation owners when another tab logs out',async()=>{
    const env=environment();const auth=await import('./auth-session');auth.saveAuthSession(account());
    const listener=vi.fn();auth.subscribeAuth(listener);
    env.storage.removeItem('ccifj-auth-session');env.notify('ccifj-auth-session',null);
    expect(auth.getSavedAuthSession()).toBeNull();
    expect(listener).toHaveBeenCalledWith(expect.objectContaining({reason:'logout',current:null}));
  });
  it('switches to a different account without writing a notification back to storage',async()=>{
    const env=environment();const auth=await import('./auth-session');auth.saveAuthSession(account());
    const listener=vi.fn();auth.subscribeAuth(listener);
    const next={...account('other-token'),expires_at:Date.now()+3600000,user:{user_id:99,name:'Other',email:'other@example.com'}};
    const raw=JSON.stringify(next);env.storage.setItem('ccifj-auth-session',raw);
    const setter=vi.spyOn(env.storage,'setItem');env.notify('ccifj-auth-session',raw);
    expect(auth.getSavedAuthSession()?.user.user_id).toBe(99);
    expect(listener).toHaveBeenCalledWith(expect.objectContaining({reason:'login',current:next}));
    expect(setter).not.toHaveBeenCalled();auth.clearAuthSession();
  });
  it('ignores a delayed logout event after a newer login and handles storage.clear',async()=>{
    const env=environment();const auth=await import('./auth-session');auth.saveAuthSession(account('new-token'));
    const listener=vi.fn();auth.subscribeAuth(listener);
    env.notify('ccifj-auth-session',null);
    expect(auth.getSavedAuthSession()?.access_token).toBe('new-token');expect(listener).not.toHaveBeenCalled();
    env.values.clear();env.notify(null,null);
    expect(auth.getSavedAuthSession()).toBeNull();expect(listener).toHaveBeenCalledOnce();
  });
});
