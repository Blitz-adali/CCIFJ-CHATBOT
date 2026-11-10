import { useEffect, useState } from 'react';
import {
  actOnOutreach, getAutomationRules, getEngagement, getOutreach, rewriteOutreach, runAutomation, updateAutomationRule,
} from './api';
import type { AutomationRule, EngagementRow, OutreachItem, RunResult } from './types';

function errorText(error: unknown) {
  return error instanceof Error ? error.message : 'Something went wrong.';
}

const CHANNEL: Record<string, string> = { in_app: 'In-app', email: 'Email (mock)', line: 'LINE (mock)', staff_task: 'Staff task' };
const today = () => new Date(Date.now() + 9 * 3600_000).toISOString().slice(0, 10); // Japan date

export function AutomationPanel() {
  const [tab, setTab] = useState<'run' | 'outbox' | 'members'>('run');
  return <section aria-labelledby="automation-title">
    <h1 id="automation-title">Onboarding & automation</h1>
    <p className="admin-note">Rules welcome new members, suggest first steps, events and people, and re-engage inactive members.
      They run every day after 09:00 JST, or now with the button below. Each message is sent once, and the outbox shows why it was sent.</p>
    <div className="admin-tabs" role="tablist">
      {([['run', 'Rules & run'], ['outbox', 'Outbox'], ['members', 'Member engagement']] as const).map(([id, label]) =>
        <button key={id} type="button" role="tab" aria-selected={tab === id} className={tab === id ? 'active' : ''} onClick={() => setTab(id)}>{label}</button>)}
    </div>
    {tab === 'run' && <RulesAndRun onOpenOutbox={() => setTab('outbox')} />}
    {tab === 'outbox' && <Outbox />}
    {tab === 'members' && <Engagement />}
  </section>;
}

function RulesAndRun({ onOpenOutbox }: { onOpenOutbox: () => void }) {
  const [rules, setRules] = useState<AutomationRule[]>([]);
  const [asOf, setAsOf] = useState(today());
  const [result, setResult] = useState<RunResult | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  useEffect(() => { getAutomationRules().then(setRules).catch(e => setError(errorText(e))); }, []);
  const change = (rule: AutomationRule, patch: Partial<AutomationRule>) => {
    setRules(old => old.map(row => row.key === rule.key ? { ...row, ...patch } : row));
    updateAutomationRule(rule.key, patch).catch(e => setError(errorText(e)));
  };
  const run = () => {
    setBusy(true); setError('');
    runAutomation(asOf).then(setResult).catch(e => setError(errorText(e))).finally(() => setBusy(false));
  };
  const shift = (days: number) => {
    const base = new Date(`${asOf || today()}T00:00:00Z`);
    base.setUTCDate(base.getUTCDate() + days);
    setAsOf(base.toISOString().slice(0, 10));
  };
  return <div>
    {error && <p role="alert" className="admin-error">{error}</p>}
    <div className="admin-table-wrap"><table className="admin-table">
      <thead><tr><th>Rule</th><th>When it fires</th><th>On</th><th>Staff approves first</th></tr></thead>
      <tbody>{rules.map(rule => <tr key={rule.key}>
        <td><strong>{rule.title}</strong></td>
        <td className="admin-wrap">{rule.description}</td>
        <td><input type="checkbox" aria-label={`${rule.title} enabled`} checked={rule.enabled} onChange={e => change(rule, { enabled: e.target.checked })} /></td>
        <td>{rule.staff_task ? <span className="admin-muted">Creates a staff task</span>
          : <input type="checkbox" aria-label={`${rule.title} needs approval`} checked={rule.requires_approval} onChange={e => change(rule, { requires_approval: e.target.checked })} />}</td>
      </tr>)}</tbody>
    </table></div>
    <div className="admin-form">
      <h2>Run the rules</h2>
      <p className="admin-note">Pick the date to run as. Today is the real run; a later date simulates the future (for example +60 days to see inactivity rules).</p>
      <div className="admin-search">
        <label className="admin-check">Run as of <input type="date" value={asOf} onChange={e => setAsOf(e.target.value)} /></label>
        <button type="button" onClick={() => setAsOf(today())}>Today</button>
        <button type="button" onClick={() => shift(30)}>+30 days</button>
        <button type="button" onClick={() => shift(60)}>+60 days</button>
        <button type="button" className="primary" disabled={busy} onClick={run}>{busy ? 'Running…' : 'Run now'}</button>
      </div>
      {result && <div className="admin-run-result" role="status">
        {result.dry_run && <p className="admin-warning"><strong>Simulation only.</strong> This date is in the future, so nothing was saved or sent: this is what the rules would do on that day.</p>}
        <p><strong>{result.dry_run ? 'Simulated run' : `Run #${result.run_id}`}</strong> as of {result.as_of.replace('T', ' ')} JST: <strong>{result.created} {result.dry_run ? 'message' : 'new message'}{result.created === 1 ? '' : 's'}{result.dry_run ? ' would be created' : ''}</strong>,
          {' '}{result.duplicates} already sent before (skipped, never sent twice).</p>
        {result.items.length > 0 && <ul className="admin-run-items">{result.items.map(item => <li key={item.outreach_id}>
          <strong>{item.member}</strong> → {rules.find(rule => rule.key === item.rule)?.title ?? item.rule}
          {' '}<span className={`admin-badge ${item.status === 'pending' ? 'weak' : 'level-member'}`}>{item.status === 'pending' ? 'awaiting approval' : `sent · ${CHANNEL[item.channel] ?? item.channel}`}</span>
          <small>Why: {item.reason}</small>
        </li>)}</ul>}
        {result.items.length > 0 && <button type="button" className="admin-link" onClick={onOpenOutbox}>Open the outbox</button>}
      </div>}
    </div>
  </div>;
}

function Outbox() {
  const [status, setStatus] = useState('pending');
  const [items, setItems] = useState<OutreachItem[]>([]);
  const [busy, setBusy] = useState(true);
  const [error, setError] = useState('');
  const load = () => {
    setBusy(true); setError('');
    getOutreach(status).then(setItems).catch(e => setError(errorText(e))).finally(() => setBusy(false));
  };
  useEffect(load, [status]);
  return <div>
    <label className="inbox-filter"><span>Show</span><select value={status} onChange={e => setStatus(e.target.value)}>
      <option value="pending">Awaiting approval</option><option value="sent">Sent</option><option value="skipped">Skipped</option><option value="">All</option>
    </select></label>
    <p className="admin-note">Email and LINE are a mock outbox in this prototype: messages are recorded here and shown to the member in the app.
      In production they would go through GetResponse and the LINE Messaging API.</p>
    {busy && <p role="status">Loading…</p>}
    {error && <p role="alert" className="admin-error">{error}</p>}
    {!busy && !items.length && !error && <p>No messages in this view.</p>}
    <div className="inbox-items">{items.map(item => <OutboxItem key={item.outreach_id} item={item} onDone={load} />)}</div>
  </div>;
}

function OutboxItem({ item, onDone }: { item: OutreachItem; onDone: () => void }) {
  const [subject, setSubject] = useState(item.subject);
  const [body, setBody] = useState(item.body);
  const [busy, setBusy] = useState('');
  const [error, setError] = useState('');
  const pending = item.status === 'pending';
  const act = (action: 'send' | 'skip') => {
    setBusy(action); setError('');
    actOnOutreach(item.outreach_id, action, { subject, body }).then(onDone).catch(e => setError(errorText(e))).finally(() => setBusy(''));
  };
  const rewrite = (language: string) => {
    setBusy('rewrite'); setError('');
    rewriteOutreach(item.outreach_id, language).then(text => { setSubject(text.subject); setBody(text.body); })
      .catch(e => setError(errorText(e))).finally(() => setBusy(''));
  };
  return <article className="inbox-request">
    <div className="inbox-request-meta">
      <span className="admin-badge">{item.rule_title}</span><span>{CHANNEL[item.channel] ?? item.channel}</span>
      <span>{item.status}{item.read_at ? ' · read by member' : ''}</span>
      <time>{(item.sent_at ?? item.created_at).replace('T', ' ')}</time>
    </div>
    <h2>{item.member} <small className="admin-muted">&lt;{item.email}&gt;</small></h2>
    <p className="admin-why"><strong>Why:</strong> {item.reason}</p>
    {pending ? <>
      <label className="admin-wide">Subject<input value={subject} onChange={e => setSubject(e.target.value)} /></label>
      <label className="admin-wide">Message<textarea rows={8} value={body} onChange={e => setBody(e.target.value)} /></label>
      {error && <p role="alert" className="admin-error">{error}</p>}
      <div className="admin-form-actions">
        <button type="button" className="primary" disabled={busy !== ''} onClick={() => act('send')}>{busy === 'send' ? 'Sending…' : 'Approve and send'}</button>
        <button type="button" disabled={busy !== ''} onClick={() => act('skip')}>Skip</button>
        <select aria-label="Rewrite language" disabled={busy !== ''} value="" onChange={e => e.target.value && rewrite(e.target.value)}>
          <option value="">{busy === 'rewrite' ? 'Rewriting…' : 'Rewrite with AI in…'}</option>
          <option value="en">English</option><option value="fr">French</option><option value="ja">Japanese</option>
        </select>
      </div>
    </> : <details><summary>Read message</summary><p className="inbox-message"><strong>{item.subject}</strong>{'\n\n'}{item.body}</p></details>}
  </article>;
}

function Engagement() {
  const [rows, setRows] = useState<EngagementRow[]>([]);
  const [busy, setBusy] = useState(true);
  const [error, setError] = useState('');
  useEffect(() => { getEngagement().then(setRows).catch(e => setError(errorText(e))).finally(() => setBusy(false)); }, []);
  const atRisk = rows.filter(row => row.stage === 'at risk').length;
  return <div>
    <p className="admin-note">Activity counts logins, chats, bookings, room reservations and introduction requests. The score falls to 0 after 90 quiet days.</p>
    {busy && <p role="status">Loading members…</p>}
    {error && <p role="alert" className="admin-error">{error}</p>}
    {!busy && <p><strong>{rows.length}</strong> member accounts · <strong className="admin-error-text">{atRisk}</strong> at risk ·{' '}
      <strong>{rows.filter(row => row.profile_completed).length}</strong> profiles completed</p>}
    <div className="admin-table-wrap"><table className="admin-table">
      <thead><tr><th>Member</th><th>Stage</th><th>Score</th><th>Last activity</th><th>Joined</th><th>Profile</th><th>Messages</th></tr></thead>
      <tbody>{rows.map(row => <tr key={row.user_id}>
        <td className="admin-wrap"><strong>{row.name}</strong><small>{row.company ?? '—'}</small></td>
        <td><span className={`admin-badge ${row.stage === 'at risk' ? 'level-staff' : row.stage.startsWith('new') || row.stage === 'onboarding' ? 'level-member' : ''}`}>{row.stage}</span></td>
        <td><meter min={0} max={100} low={34} high={67} optimum={100} value={row.score} aria-label={`Score ${row.score}`} /> {row.score}</td>
        <td>{row.days_inactive === 0 ? 'today' : `${row.days_inactive} days ago`} <small className="admin-muted">({row.last_activity_kind})</small></td>
        <td>{row.joined_at ? row.joined_at.slice(0, 10) : '—'}</td>
        <td>{row.profile_completed ? `Done${row.goals.length ? ` · ${row.goals.length} goals` : ''}` : 'Missing'}</td>
        <td>{row.messages_sent}</td>
      </tr>)}</tbody>
    </table></div>
  </div>;
}
