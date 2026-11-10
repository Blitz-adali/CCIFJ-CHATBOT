import { useEffect, useState } from 'react';
import { getAnalytics, getLangsmithAnalytics } from './api';
import type { AnalyticsSummary, LangsmithSummary } from './types';

function errorText(error: unknown) {
  return error instanceof Error ? error.message : 'Something went wrong.';
}

const OUTCOMES: [keyof AnalyticsSummary['outcomes'], string][] = [
  ['event_bookings', 'Event bookings'], ['room_reservations', 'Room reservations'], ['staff_requests', 'Requests to staff'],
  ['introduction_requests', 'Introduction requests'], ['onboarding_messages_sent', 'Onboarding messages sent'],
];
const AUDIENCE: [keyof AnalyticsSummary['audience'], string][] = [
  ['guest', 'Guests'], ['user', 'Signed-in non-members'], ['member', 'Members'], ['staff', 'Staff'],
];

export function AnalyticsPanel() {
  const [days, setDays] = useState(14);
  const [data, setData] = useState<AnalyticsSummary | null>(null);
  const [ls, setLs] = useState<LangsmithSummary | null>(null);
  const [error, setError] = useState('');
  useEffect(() => {
    setData(null); setError('');
    getAnalytics(days).then(setData).catch(e => setError(errorText(e)));
  }, [days]);
  useEffect(() => { getLangsmithAnalytics(7).then(setLs).catch(() => setLs(null)); }, []);
  return <section aria-labelledby="analytics-title">
    <div className="admin-heading">
      <h1 id="analytics-title">Agent analytics</h1>
      <label className="admin-check">Period <select value={days} onChange={e => setDays(Number(e.target.value))}>
        <option value={7}>Last 7 days</option><option value={14}>Last 14 days</option><option value={30}>Last 30 days</option><option value={90}>Last 90 days</option>
      </select></label>
    </div>
    <p className="admin-note">How visitors use the assistant and what it achieves, from the app's own records. Model costs and errors come from LangSmith, below.</p>
    {error && <p role="alert" className="admin-error">{error}</p>}
    {!data && !error && <p role="status">Loading analytics…</p>}
    {data && <>
      <div className="admin-tiles">
        <Tile label="Questions asked" value={data.totals.questions} hint={`in ${data.totals.conversations} conversations`} />
        <Tile label="Answers with sources" value={pct(data.quality.answers_with_sources)} hint="share of answers citing CCIFJ sources" />
        <Tile label="Typical response time" value={secs(data.quality.median_response_seconds)} hint={`90% within ${secs(data.quality.p90_response_seconds)}`} />
        <Tile label="Answers with a fallback" value={pct(data.quality.answers_with_fallback_warnings)} hint="search or a service was degraded" />
      </div>
      <div className="analytics-grid">
        <figure className="analytics-card wide">
          <figcaption><strong>Questions per day</strong><small>Japan time</small></figcaption>
          <DailyBars daily={data.daily} />
        </figure>
        <figure className="analytics-card">
          <figcaption><strong>Topics</strong><small>an answer can cover several</small></figcaption>
          <HBars rows={data.topics.map(t => ({ label: t.topic, value: t.count }))} />
        </figure>
        <figure className="analytics-card">
          <figcaption><strong>Who is asking</strong><small>questions by visitor type</small></figcaption>
          <HBars rows={AUDIENCE.map(([key, label]) => ({ label, value: data.audience[key] }))} />
        </figure>
        <figure className="analytics-card">
          <figcaption><strong>Outcomes</strong><small>actions completed in the period</small></figcaption>
          <dl className="analytics-list">{OUTCOMES.map(([key, label]) => <div key={key}><dt>{label}</dt><dd>{data.outcomes[key]}</dd></div>)}</dl>
        </figure>
        <figure className="analytics-card">
          <figcaption><strong>Recent questions</strong><small>what people actually ask</small></figcaption>
          <ul className="analytics-questions">{data.recent_questions.map((q, i) => <li key={i}>
            <span dir="auto">{q.text}</span><small>{q.at.replace('T', ' ')} · {q.audience}</small></li>)}</ul>
        </figure>
      </div>
    </>}
    <LangsmithCard data={ls} />
  </section>;
}

const pct = (value: number | null) => value === null ? '—' : `${value}%`;
const secs = (value: number | null) => value === null ? '—' : `${value} s`;

function Tile({ label, value, hint }: { label: string; value: string | number; hint?: string }) {
  return <article className="admin-tile"><span>{label}</span><strong>{value}</strong>{hint && <small>{hint}</small>}</article>;
}

/** One series, so one hue and no legend; each bar has a tooltip and the table below has the values. */
function DailyBars({ daily }: { daily: AnalyticsSummary['daily'] }) {
  const max = Math.max(1, ...daily.map(d => d.questions));
  const labelEvery = Math.ceil(daily.length / 7);
  return <>
    <div className="daily-bars" role="img" aria-label={`Questions per day, highest ${max}`}>
      {daily.map((d, i) => <div key={d.day} className="daily-bar" title={`${d.day}: ${d.questions} question${d.questions === 1 ? '' : 's'}`}>
        <span style={{ height: `${(d.questions / max) * 100}%` }} className={d.questions ? '' : 'zero'} />
        <small>{i % labelEvery === 0 || i === daily.length - 1 ? d.day.slice(5) : ''}</small>
      </div>)}
    </div>
    <details className="analytics-table"><summary>Show as table</summary>
      <table className="admin-table"><thead><tr><th>Day</th><th>Questions</th></tr></thead>
        <tbody>{daily.map(d => <tr key={d.day}><td>{d.day}</td><td>{d.questions}</td></tr>)}</tbody></table>
    </details>
  </>;
}

function HBars({ rows }: { rows: { label: string; value: number }[] }) {
  const max = Math.max(1, ...rows.map(r => r.value));
  return <ul className="hbars">{rows.map(row => <li key={row.label} title={`${row.label}: ${row.value}`}>
    <span className="hbar-label">{row.label}</span>
    <span className="hbar-track"><span className="hbar-fill" style={{ width: row.value ? `max(2px, ${(row.value / max) * 100}%)` : 0 }} /></span>
    <span className="hbar-value">{row.value}</span>
  </li>)}</ul>;
}

function LangsmithCard({ data }: { data: LangsmithSummary | null }) {
  return <figure className="analytics-card langsmith">
    <figcaption><strong>Model monitoring (LangSmith)</strong><small>{data ? `project ${data.project} · last ${data.days} days` : 'checking the connection…'}</small></figcaption>
    {data && data.status !== 'connected' && <p className="admin-note"><strong>{data.status === 'off' ? 'Not configured.' : 'Not available.'}</strong> {data.message}</p>}
    {data?.status === 'connected' && <>
      <div className="admin-tiles">
        <Tile label="Chat turns traced" value={(data.chat_turns ?? 0).toLocaleString()} hint={`${(data.runs ?? 0).toLocaleString()} runs in total`} />
        <Tile label="Turns with an error" value={data.error_rate == null ? '—' : `${(data.error_rate * 100).toFixed(1)}%`} />
        <Tile label="Median turn time" value={secs(data.median_latency_seconds ?? null)} hint={`99% within ${secs(data.p99_latency_seconds ?? null)}`} />
        <Tile label="Model cost" value={data.total_cost == null ? '—' : `$${data.total_cost.toFixed(2)}`} hint={`${(data.total_tokens ?? 0).toLocaleString()} tokens`} />
      </div>
      <p className="analytics-sub">Most used tools (latest {data.tool_sample ?? 0} tool calls)</p>
      <HBars rows={(data.top_tools ?? []).map(t => ({ label: t.tool, value: t.calls }))} />
    </>}
  </figure>;
}
