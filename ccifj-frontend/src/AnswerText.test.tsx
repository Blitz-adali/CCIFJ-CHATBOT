import { describe, expect, it } from 'vitest';
import { renderToStaticMarkup } from 'react-dom/server';
import { AnswerText } from './AnswerText';
import { Sources } from './Sources';
const sources=[{title:'Set up and Business Center',url:'https://www.ccifj.or.jp/en/services.html',snippet:'Long extracted material should not appear'}];
describe('compact answer',()=>{
 it('turns numbered references into small links that name their page',()=>{
  const html=renderToStaticMarkup(<AnswerText text="CCIFJ can help with setup [1]." sources={[{...sources[0],cited:true}]}/>);
  expect(html).toContain('<sup class="citation">');
  expect(html).toContain('href="https://www.ccifj.or.jp/en/services.html"');
  expect(html).toContain('title="Set up and Business Center"');
  expect(html).not.toContain('[1]');
  expect(html).not.toContain('Long extracted');
  // The cited page is listed under the answer, so it is not repeated as a primary source.
  expect(html).not.toContain('answer-primary-source');
 });
 it('keeps the number of a cited document that has no page, and drops uncited numbers',()=>{
  const html=renderToStaticMarkup(<AnswerText text="Rates start at ¥30,000 [1]. See also [2]." sources={[{title:'Media Kit FJE 2026',url:null,cited:true},{title:'Events',url:'https://www.ccifj.or.jp/en/events.html',cited:false}]}/>);
  expect(html).toContain('¥30,000<sup class="citation"><span title="Media Kit FJE 2026"');
  expect(html).not.toContain('<a');
  expect(html).not.toContain('See also 2');
  expect(html).not.toContain('[2]');
 });
 it('lists cited sources with their numbers, official pages and documents alike',()=>{
  const html=renderToStaticMarkup(<Sources locale="fr" sources={[{title:'Membership',url:'https://www.ccifj.or.jp/en/membership.html',cited:true},{title:'Media Kit FJE 2026',url:null,cited:true},{title:'Form',url:'https://survey.ccifi.net/1',cited:false},{title:'bad',url:'javascript:alert(1)',cited:true}]}/>);
  expect(html).toContain('Sources officielles');
  expect(html).toContain('<li value="1"><a href="https://www.ccifj.or.jp/en/membership.html"');
  expect(html).toContain('<li value="2">');
  expect(html).toContain('document CCIFJ');
  expect(html).not.toContain('survey.ccifi.net');
  expect(html).not.toContain('javascript:');
  expect(renderToStaticMarkup(<Sources locale="en" sources={[{title:'Form',url:'https://survey.ccifi.net/1',cited:false}]}/>)).toBe('');
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
 it('keeps paragraphs separated by a blank line as separate paragraphs',()=>{
  expect(renderToStaticMarkup(<AnswerText text={'First paragraph.\n\nSecond paragraph.'}/>))
   .toBe('<div class="answer-markdown"><p>First paragraph.</p>\n<p>Second paragraph.</p></div>');
  const contacts=renderToStaticMarkup(<AnswerText text={'**Tomoyo Funabashi**  \nMembership Manager  \nPhone: +81 3-4500-6644\n\n\n**Marianne Barges**  \nPhone: +81 3-4500-6522\n\nI can help you draft an email to either of them.'}/>);
  expect(contacts.match(/<p>/g)).toHaveLength(3);
  expect(contacts).not.toContain('6644 Marianne');
  expect(contacts).not.toContain('6522 I can help');
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
