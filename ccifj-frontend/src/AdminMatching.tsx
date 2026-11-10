import { useEffect, useState } from 'react';
import type { FormEvent } from 'react';
import {
  draftProposal, getAdminEvents, getAdminMembers, getIntroRequests, matchMembers, matchMembersForEvent,
  updateIntroRequest,
} from './api';
import type { AdminEvent, IntroRequestItem, IntroStatus, MemberMatch } from './types';

function errorText(error: unknown) {
  return error instanceof Error ? error.message : 'Something went wrong.';
}

const STATUS_LABELS: Record<IntroStatus, string> = {
  new: 'New', proposed: 'Proposed to company', introduced: 'Introduced', declined: 'Declined', closed: 'Closed',
};

function strength(score: number, relevant: number) {
  if (score >= relevant + 0.02) return ['Strong', 'level-member'];
  if (score >= relevant) return ['Good', ''];
  return ['Weak', 'weak'];
}

export function MatchingPanel() {
  const [tab, setTab] = useState<'find' | 'intros'>('find');
  return <section aria-labelledby="matching-title">
    <h1 id="matching-title">Matching & introductions</h1>
    <div className="admin-tabs" role="tablist">
      <button type="button" role="tab" aria-selected={tab === 'find'} className={tab === 'find' ? 'active' : ''} onClick={() => setTab('find')}>Find matches</button>
      <button type="button" role="tab" aria-selected={tab === 'intros'} className={tab === 'intros' ? 'active' : ''} onClick={() => setTab('intros')}>Introduction requests</button>
    </div>
    {tab === 'find' ? <FindMatches /> : <IntroRequests />}
  </section>;
}

function FindMatches() {
  const [mode, setMode] = useState<'need' | 'event'>('need');
  const [need, setNeed] = useState('');
  const [sector, setSector] = useState('');
  const [region, setRegion] = useState('');
  const [sectors, setSectors] = useState<string[]>([]);
  const [events, setEvents] = useState<AdminEvent[]>([]);
  const [eventId, setEventId] = useState('');
  const [results, setResults] = useState<MemberMatch[] | null>(null);
  const [relevant, setRelevant] = useState(0.82);
  const [selected, setSelected] = useState<number[]>([]);
  const [requester, setRequester] = useState('');
  const [language, setLanguage] = useState('en');
  const [draft, setDraft] = useState('');
  const [busy, setBusy] = useState('');
  const [error, setError] = useState('');
  useEffect(() => {
    getAdminMembers('', '', 0).then(result => setSectors(result.sectors)).catch(() => undefined);
    getAdminEvents('', 'upcoming', 0).then(result => setEvents(result.items)).catch(() => undefined);
  }, []);
  const search = (event: FormEvent) => {
    event.preventDefault();
    setBusy('search'); setError(''); setDraft(''); setSelected([]);
    const request = mode === 'event' && eventId
      ? matchMembersForEvent(Number(eventId)).then(result => { setNeed(`Members to invite to "${result.need}"`); return result; })
      : matchMembers(need, sector, region);
    request.then(result => { setResults(result.items); setRelevant(result.relevant_score); })
      .catch(e => setError(errorText(e))).finally(() => setBusy(''));
  };
  const toggle = (id: number) => setSelected(old => old.includes(id) ? old.filter(item => item !== id) : [...old, id]);
  const writeDraft = () => {
    setBusy('draft'); setError('');
    draftProposal({ need, requester, member_ids: selected, language })
      .then(result => setDraft(result.draft)).catch(e => setError(errorText(e))).finally(() => setBusy(''));
  };
  return <div>
    <p className="admin-note">Describe what someone is looking for, or pick an event to see which members to invite. Results come from the member directory; a match on sector alone should be confirmed before an introduction.</p>
    <form className="admin-form" onSubmit={search}>
      <div className="admin-tabs small">
        <label className="admin-check"><input type="radio" name="mode" checked={mode === 'need'} onChange={() => setMode('need')} />A need</label>
        <label className="admin-check"><input type="radio" name="mode" checked={mode === 'event'} onChange={() => setMode('event')} />Who to invite to an event</label>
      </div>
      {mode === 'need' ? <>
        <label className="admin-wide">What are they looking for?<textarea rows={3} required minLength={3} value={need} onChange={e => setNeed(e.target.value)}
          placeholder="e.g. A French agrifood company that knows food distribution in Japan well" /></label>
        <div className="admin-form-grid">
          <label>Sector (optional)<select value={sector} onChange={e => setSector(e.target.value)}><option value="">Any sector</option>{sectors.map(item => <option key={item}>{item}</option>)}</select></label>
          <label>Place (optional)<input value={region} onChange={e => setRegion(e.target.value)} placeholder="Tokyo, Kansai, Osaka…" /></label>
        </div>
      </> : <label className="admin-wide">Event<select required value={eventId} onChange={e => setEventId(e.target.value)}>
        <option value="">Choose an upcoming event</option>
        {events.map(item => <option key={item.event_id} value={item.event_id}>{item.title}</option>)}
      </select></label>}
      <div className="admin-form-actions"><button type="submit" className="primary" disabled={busy !== ''}>{busy === 'search' ? 'Searching…' : 'Find members'}</button></div>
    </form>
    {error && <p role="alert" className="admin-error">{error}</p>}
    {results && <>
      <div className="admin-table-wrap"><table className="admin-table">
        <thead><tr><th><span className="sr-only">Select</span></th><th>Company</th><th>Fit</th><th>Why</th><th>Contact</th></tr></thead>
        <tbody>{results.map(match => {
          const [label, tone] = strength(match.score, relevant);
          return <tr key={match.id}>
            <td><input type="checkbox" aria-label={`Select ${match.name}`} checked={selected.includes(match.id)} onChange={() => toggle(match.id)} /></td>
            <td className="admin-wrap"><strong>{match.name}</strong><small>{[match.sector, match.region, match.country].filter(Boolean).join(' · ')}</small></td>
            <td><span className={`admin-badge ${tone}`}>{label}</span></td>
            <td className="admin-wrap">{match.why || '—'}</td>
            <td className="admin-wrap">{match.contact ?? '—'}</td>
          </tr>;
        })}</tbody>
      </table></div>
      {!results.length && <p>No members matched. Try a broader description or remove the filters.</p>}
      {results.length > 0 && <div className="admin-form">
        <h2>Matchmaking proposal</h2>
        <p className="admin-note">Select companies above, then let the assistant draft an email for you to check and send yourself.</p>
        <div className="admin-form-grid">
          <label>Who is it for?<input value={requester} onChange={e => setRequester(e.target.value)} placeholder="e.g. Marie Dupont, Example Foods SAS" /></label>
          <label>Language<select value={language} onChange={e => setLanguage(e.target.value)}><option value="en">English</option><option value="fr">French</option><option value="ja">Japanese</option></select></label>
        </div>
        <div className="admin-form-actions">
          <button type="button" className="primary" disabled={busy !== '' || !selected.length} onClick={writeDraft}>
            {busy === 'draft' ? 'Writing…' : `Draft proposal (${selected.length} selected)`}</button>
        </div>
        {draft && <>
          <label className="admin-wide">Draft (edit before sending)<textarea rows={14} value={draft} onChange={e => setDraft(e.target.value)} /></label>
          <div className="admin-form-actions"><button type="button" onClick={() => navigator.clipboard?.writeText(draft)}>Copy</button></div>
        </>}
      </div>}
    </>}
  </div>;
}

function IntroRequests() {
  const [status, setStatus] = useState('');
  const [items, setItems] = useState<IntroRequestItem[]>([]);
  const [busy, setBusy] = useState(true);
  const [error, setError] = useState('');
  const load = () => {
    setBusy(true); setError('');
    getIntroRequests(status).then(result => setItems(result.items)).catch(e => setError(errorText(e))).finally(() => setBusy(false));
  };
  useEffect(load, [status]);
  const save = (item: IntroRequestItem, patch: { status?: IntroStatus; staff_note?: string }) => {
    setItems(old => old.map(row => row.intro_id === item.intro_id ? { ...row, ...patch } : row));
    updateIntroRequest(item.intro_id, patch).catch(e => { setError(errorText(e)); load(); });
  };
  return <div>
    <p className="admin-note">Members ask the assistant to be introduced to other members. Contact the other company first; nothing is sent to it automatically.</p>
    <label className="inbox-filter"><span>Show</span><select value={status} onChange={e => setStatus(e.target.value)}>
      <option value="">All requests</option>{(Object.keys(STATUS_LABELS) as IntroStatus[]).map(key => <option key={key} value={key}>{STATUS_LABELS[key]}</option>)}
    </select></label>
    {busy && <p role="status">Loading requests…</p>}
    {error && <p role="alert" className="admin-error">{error}</p>}
    {!busy && !items.length && !error && <p>No introduction requests in this view.</p>}
    <div className="inbox-items">{items.map(item => <article className="inbox-request" key={item.intro_id}>
      <div className="inbox-request-meta">
        <code>{item.reference}</code>
        <select className="inbox-status" aria-label={`Status of ${item.reference}`} value={item.status} onChange={e => save(item, { status: e.target.value as IntroStatus })}>
          {(Object.keys(STATUS_LABELS) as IntroStatus[]).map(key => <option key={key} value={key}>{STATUS_LABELS[key]}</option>)}
        </select>
        <time dateTime={item.created_at}>{new Date(item.created_at).toLocaleString()}</time>
      </div>
      <h2>{item.requester_company?.name ?? item.requester?.name ?? 'A member'} → {item.target?.name ?? 'unknown company'}</h2>
      <p>From: {item.requester ? `${item.requester.name} <${item.requester.email}>` : '—'}{item.requester_company ? ` · ${item.requester_company.name}` : ''}</p>
      <p>To: {item.target ? [item.target.name, item.target.sector, item.target.place].filter(Boolean).join(' · ') : '—'}</p>
      <p className="inbox-message" dir="auto">{item.message}</p>
      <label className="admin-wide">Staff note<textarea rows={2} defaultValue={item.staff_note ?? ''} onBlur={e => { if (e.target.value !== (item.staff_note ?? '')) save(item, { staff_note: e.target.value }); }} placeholder="Who contacted whom, and when" /></label>
    </article>)}</div>
  </div>;
}
