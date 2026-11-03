import { describe, expect, it } from 'vitest';
import { renderToStaticMarkup } from 'react-dom/server';
import { AnswerText } from './AnswerText';
const sources=[{title:'Set up and Business Center',url:'https://www.ccifj.or.jp/en/services.html',snippet:'Long extracted material should not appear'}];
describe('compact answer',()=>{
 it('replaces numbered references with inline clickable page titles',()=>{
  const html=renderToStaticMarkup(<AnswerText text="CCIFJ can help through [1]." sources={sources}/>);
  expect(html).toContain('href="https://www.ccifj.or.jp/en/services.html"');
  expect(html).toContain('Set up and Business Center</a>');
  expect(html).not.toContain('[1]');
  expect(html).not.toContain('Long extracted');
 });
 it('links the primary source when the answer has no reference',()=>{
  const html=renderToStaticMarkup(<AnswerText text="CCIFJ can help." sources={sources}/>);
  expect(html).toContain('Set up and Business Center</a>');
 });
 it('does not create unsafe links or leave unresolved numbered references',()=>{
  const html=renderToStaticMarkup(<AnswerText text="See [1] or [9]." sources={[{title:'bad',url:'javascript:alert(1)'}]}/>);
  expect(html).not.toContain('<a');
  expect(html).not.toContain('[9]');
 });
 it('renders markdown links inline and escapes markup',()=>{
  const html=renderToStaticMarkup(<AnswerText text={'Visit [CCIFJ](https://www.ccifj.or.jp/). <script>alert(1)</script>'}/>);
  expect(html).toContain('>CCIFJ</a>');
  expect(html).not.toContain('<script>');
 });
 it('renders email and ticket drafts as cards and leaves other code fences alone',()=>{
  const email=renderToStaticMarkup(<AnswerText locale="fr" text={'Voici le brouillon :\n\n```email\nTo: Elena SUZUKI <e.suzuki@ccifj.or.jp>\nFrom: Jane Doe <jane@example.com>\nSubject: Late registration\n\nDear Ms Suzuki,\nThank you.\n```\n\n```python\nprint(1)\n```'}/>);
  expect(email).toContain('Brouillon d’e-mail');
  expect(email).toContain('Elena SUZUKI &lt;e.suzuki@ccifj.or.jp&gt;');
  expect(email).toContain('Late registration');
  expect(email).toContain('Dear Ms Suzuki,\nThank you.');
  expect(email).toContain('Copier');
  expect(email).toContain('<pre><code class="language-python">');
  const ticket=renderToStaticMarkup(<AnswerText text={'```ticket\nSubject: Refund\n\nPlease help'}/>);
  expect(ticket).toContain('Ticket for CCIFJ team');
  expect(ticket).toContain('is-drafting');
  expect(ticket).toContain('Drafting…');
 });
});
