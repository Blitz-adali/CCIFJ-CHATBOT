import {describe,expect,it} from 'vitest';
import {renderToStaticMarkup} from 'react-dom/server';
import {ServiceNotice} from './ServiceNotice';

describe('partial service failures',()=>{
  it('shows a clear localized evidence limitation without internal details',()=>{
    const html=renderToStaticMarkup(<ServiceNotice warnings={['tool_unavailable']} locale="fr"/>);
    expect(html).toContain('n’ont pas pu être vérifiées');
    expect(html).not.toContain('tool_unavailable');
  });
  it('does not duplicate a recovery answer or expose ranking internals',()=>{
    expect(renderToStaticMarkup(<ServiceNotice mode="recovery" warnings={['tool_unavailable']} locale="en"/>)).toBe('');
    expect(renderToStaticMarkup(<ServiceNotice warnings={['retrieval_rerank_fallback']} locale="en"/>)).toBe('');
  });
});
