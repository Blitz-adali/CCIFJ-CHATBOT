import {afterEach,describe,expect,it,vi} from 'vitest';
import {renderToStaticMarkup} from 'react-dom/server';
import {WidgetChat} from './WidgetChat';

afterEach(()=>vi.unstubAllGlobals());
describe('compact concierge',()=>{
  it('opens in the host language with accessible close and message controls',()=>{
    vi.stubGlobal('window',{location:{search:'?lang=fr'}});
    const html=renderToStaticMarkup(<WidgetChat/>);
    expect(html).toContain('Bonjour ! Comment puis-je vous aider ?');
    expect(html).toContain('aria-label="Fermer le chat"');
    expect(html).toContain('aria-label="Envoyer"');
    expect(html).toContain('id="widget-question"');
  });
  it('defaults to English for an unknown host language',()=>{
    vi.stubGlobal('window',{location:{search:'?lang=invalid'}});
    const html=renderToStaticMarkup(<WidgetChat/>);
    expect(html).toContain('Hello! How can I help you today?');
    expect(html).not.toContain('Dashboard');
  });
});
