import {describe,expect,it} from 'vitest';
import {renderToStaticMarkup} from 'react-dom/server';
import {EventCards,formatJapanDate} from './EventCards';
import {SourceList} from './SourceList';
import type {EventCardData} from './types';

const event: EventCardData={event_id:1,title:'Business Summit',category:'Networking',location:'Tokyo',start_at:'2026-12-01T09:00:00+09:00',end_at:null,reg_deadline:null,price:'5000',member_price:'3000',non_member_price:null,currency:'JPY',registration_open:true};
const noop=()=>{};

describe('database event cards',()=>{
  it('shows Japan-local dates regardless of browser timezone',()=>{
    expect(formatJapanDate('2026-12-01T00:00:00Z','en')).toContain('9:00');
    const html=renderToStaticMarkup(<EventCards events={[event]} locale="en" disabled={false} onDetails={noop} onBook={noop}/>);
    expect(html).toContain('Business Summit');expect(html).toContain('Registration open');
    expect(html).toContain('Members');expect(html).toContain('JST');
  });
  it('disables booking for closed registration and never treats missing prices as free',()=>{
    const html=renderToStaticMarkup(<EventCards events={[{...event,registration_open:false,price:null,member_price:null}]} locale="fr" disabled={false} onDetails={noop} onBook={noop}/>);
    expect(html).toContain('Inscriptions closes');expect(html).not.toContain('Non précisé');
    expect(html).not.toContain('Préparer la réservation');
    expect(html).not.toContain('gratuit');
  });
  it('does not label a missing deadline as open or closed',()=>{
    const html=renderToStaticMarkup(<EventCards events={[{...event,registration_open:null,reg_deadline:null}]} locale="en" disabled={false} onDetails={noop} onBook={noop}/>);
    expect(html).toContain('Registration unconfirmed');
    expect(html).not.toContain('Registration open');
    expect(html).not.toContain('Registration closed');
    expect(html).not.toContain('Prepare booking');
  });
  it('keeps missing fields quiet without implying a free event or a booking option',()=>{
    const html=renderToStaticMarkup(<EventCards events={[{...event,location:null,price:null,member_price:null,registration_open:null}]} locale="en" disabled={false} onDetails={noop} onBook={noop}/>);
    expect(html).not.toContain('Not specified');
    expect(html).not.toContain('Prepare booking');
    expect(html).not.toContain('No payment is taken');
    expect(html).toContain('Details');
    expect(html).toContain('Registration unconfirmed');
  });
  it('only labels retrieved, safe URLs as sources',()=>{
    const html=renderToStaticMarkup(<SourceList locale="en" sources={[
      {title:'Generated',url:'https://example.com/'},
      {title:'Database fees',url:'https://www.ccifj.or.jp/en/membership/how-to-become-a-member.html',provenance:'database'},
      {title:'Unsafe',url:'javascript:alert(1)',provenance:'retrieved'},
      {title:'Verified passage',url:'https://www.ccifj.or.jp/en.html',provenance:'retrieved'},
    ]}/>);
    expect(html).toContain('Database fees');expect(html).toContain('Verified passage');expect(html).not.toContain('Generated');expect(html).not.toContain('javascript:');
  });
});
