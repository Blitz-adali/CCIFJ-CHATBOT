import {afterEach,describe,expect,it,vi} from 'vitest';
import {renderToStaticMarkup} from 'react-dom/server';
import App from './App';
import {WidgetChat} from './WidgetChat';
import {readLocale,siteCopy} from './site-copy';
import {sendChat} from './api';
import type {Locale} from './types';

afterEach(()=>vi.unstubAllGlobals());
function mockWindow(search:string,saved:string|null=null){
  vi.stubGlobal('window',{location:{search,protocol:'http:'},localStorage:{getItem:()=>saved}});
}
describe('site language',()=>{
  it.each<Locale>(['en','fr','ja'])('renders the entire page and assistant in %s',locale=>{
    mockWindow(`?lang=${locale}`);
    const html=renderToStaticMarkup(<App/>);
    expect(html).toContain(siteCopy[locale].title);
    expect(html).not.toContain('topic-buttons');
    expect(html).toContain(siteCopy[locale].footer);
    expect(html).toContain(locale==='en'?'How can I help you today?':locale==='fr'?'Comment puis-je vous aider':'何をお手伝いできますか');
    expect((html.match(/<select/g)||[])).toHaveLength(1);
    if(locale==='en'){expect(html).not.toContain('Votre assistant');expect(html).not.toMatch(/bonjour/i);}
    if(locale==='ja')expect(html).not.toContain('Adhésion');
  });
  it('prefers explicit URL language to stored language',()=>{
    mockWindow('?lang=ja','fr');expect(readLocale()).toBe('ja');
  });
  it('restores a saved language and safely handles unavailable storage',()=>{
    mockWindow('','fr');expect(readLocale()).toBe('fr');
    vi.stubGlobal('window',{location:{search:''},localStorage:{getItem:()=>{throw Error('Storage disabled');}}});
    expect(readLocale()).toBe('en');
  });
  it('uses the controlled site language instead of the widget URL',()=>{
    mockWindow('?lang=fr');
    const html=renderToStaticMarkup(<WidgetChat locale="ja"/>);
    expect(html).toContain('何をお手伝いできますか');
    expect(html).not.toContain('<select');
  });
  it.each<Locale>(['en','fr','ja'])('includes %s in the backend chat request',async locale=>{
    mockWindow('');
    const fetchMock=vi.fn().mockResolvedValue(new Response(JSON.stringify({answer:'ok'})));
    vi.stubGlobal('fetch',fetchMock);
    const message = locale === 'en' ? 'What are the benefits of joining CCIFJ?'
      : locale === 'fr' ? 'Quels sont les avantages de l’adhésion à la CCIFJ ?'
      : 'CCIFJ に加入するメリットは何ですか？';
    await sendChat({message,locale,session_id:'existing-conversation'});
    expect(JSON.parse(fetchMock.mock.calls[0][1].body)).toMatchObject({locale,session_id:'existing-conversation'});
  });
});
