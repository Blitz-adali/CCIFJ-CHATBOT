import { useEffect, useRef, useState } from 'react';
import type { FormEvent } from 'react';
import {
  deleteKnowledgeDoc, getAdminEvents, getAdminMembers, getImportTemplate, getKnowledgeDocs, importFile,
  saveAdminEvent, uploadKnowledgeDoc,
} from './api';
import type { EventInput } from './api';
import { toAttachmentUploads } from './attachments';
import type { AdminEvent, AdminMember, AttachmentUpload, ImportResult, KnowledgeDoc } from './types';

function errorText(error: unknown) {
  return error instanceof Error ? error.message : 'Something went wrong.';
}

const jst = new Intl.DateTimeFormat('en-GB', { dateStyle: 'medium', timeStyle: 'short' });
/** Event times are stored and edited as Japan time, without a time zone. */
function formatJst(value: string | null) {
  return value ? `${jst.format(new Date(value))} JST` : '—';
}

function price(event: AdminEvent) {
  const amount = (value: number | null) => value === null ? null : `${value.toLocaleString()} ${event.currency ?? ''}`.trim();
  const parts = [event.member_price !== null && `Members ${amount(event.member_price)}`,
    event.non_member_price !== null && `Non-members ${amount(event.non_member_price)}`,
    event.price !== null && amount(event.price)].filter(Boolean);
  return parts.join(' · ') || 'Price not set';
}

// ---------- Excel / CSV import (shared by events and members) ----------

function ImportBox({ kind, onDone }: { kind: 'events' | 'members'; onDone: () => void }) {
  const [file, setFile] = useState<AttachmentUpload | null>(null);
  const [result, setResult] = useState<ImportResult | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const input = useRef<HTMLInputElement>(null);
  const reset = () => { setFile(null); setResult(null); if (input.current) input.current.value = ''; };
  const run = (commit: boolean, upload = file) => {
    if (!upload) return;
    setBusy(true); setError('');
    importFile(kind, upload, commit).then(value => { setResult(value); if (commit) onDone(); })
      .catch(e => setError(errorText(e))).finally(() => setBusy(false));
  };
  const downloadTemplate = () => getImportTemplate(kind).then(text => {
    const link = document.createElement('a');
    link.href = URL.createObjectURL(new Blob([text], { type: 'text/csv' }));
    link.download = `ccifj-${kind}-template.csv`;
    link.click();
    URL.revokeObjectURL(link.href);
  }).catch(e => setError(errorText(e)));
  const problems = result?.rows.filter(row => row.action === 'error') ?? [];
  return <details className="admin-import">
    <summary>Import from Excel or CSV</summary>
    <p className="admin-note">Upload an .xlsx or .csv file with one row per {kind === 'events' ? 'event' : 'company'}. Nothing is saved until you confirm.
      Existing {kind === 'events' ? 'events (same title and date)' : 'companies (same name)'} are updated, and empty cells keep the stored value.{' '}
      <button type="button" className="admin-link" onClick={downloadTemplate}>Download the template</button></p>
    <div className="admin-search">
      <input ref={input} type="file" accept=".xlsx,.csv" aria-label={`Choose a ${kind} file`} onChange={async event => {
        const chosen = event.target.files?.[0];
        setResult(null);
        if (!chosen) { setFile(null); return; }
        const [upload] = await toAttachmentUploads([chosen]);
        setFile(upload);
        run(false, upload);
      }} />
    </div>
    {busy && <p role="status">Checking the file…</p>}
    {error && <p role="alert" className="admin-error">{error}</p>}
    {result && <div className="admin-import-result">
      <p><strong>{result.committed ? 'Imported' : 'Ready to import'}:</strong> {result.counts.create} new, {result.counts.update} updated
        {result.counts.error > 0 && <>, <span className="admin-error-text">{result.counts.error} rows with problems{result.committed ? ' (skipped)' : ' (will be skipped)'}</span></>}.</p>
      {problems.length > 0 && <ul className="admin-problems">{problems.slice(0, 50).map(row =>
        <li key={row.row}>Row {row.row}{row.label ? ` (${row.label})` : ''}: {row.errors.join('; ')}</li>)}
        {problems.length > 50 && <li>…and {problems.length - 50} more</li>}</ul>}
      {!result.committed
        ? <div className="admin-form-actions">
            <button type="button" className="primary" disabled={busy || result.counts.create + result.counts.update === 0} onClick={() => run(true)}>
              Import {result.counts.create + result.counts.update} rows</button>
            <button type="button" onClick={reset}>Cancel</button>
          </div>
        : <button type="button" onClick={reset}>Import another file</button>}
    </div>}
  </details>;
}

// ---------- Events ----------

const EMPTY_EVENT: EventInput = { title: '', description: '', category: 'Networking', start_at: '', end_at: '', location: '', member_price: null, non_member_price: null, price: null, currency: 'JPY', reg_deadline: '', languages: ['en'], contact: '' };
const LANGUAGES = [['en', 'English'], ['fr', 'French'], ['ja', 'Japanese']] as const;

export function EventsPanel() {
  const [items, setItems] = useState<AdminEvent[]>([]);
  const [categories, setCategories] = useState<string[]>([]);
  const [scope, setScope] = useState<'upcoming' | 'past' | 'all'>('upcoming');
  const [query, setQuery] = useState('');
  const [next, setNext] = useState<number | null>(null);
  const [editing, setEditing] = useState<{ id: number | null; form: EventInput } | null>(null);
  const [error, setError] = useState('');
  const [formError, setFormError] = useState('');
  const [busy, setBusy] = useState(false);
  const load = (offset = 0, q = query, s = scope) => {
    setBusy(true); setError('');
    getAdminEvents(q, s, offset).then(result => {
      setItems(old => offset ? [...old, ...result.items] : result.items);
      setNext(result.next_offset); setCategories(result.categories);
    }).catch(e => setError(errorText(e))).finally(() => setBusy(false));
  };
  useEffect(() => load(), [scope]);
  const set = (patch: EventInput) => setEditing(current => current && { ...current, form: { ...current.form, ...patch } });
  const numberField = (value: string) => value.trim() === '' ? null : Number(value);
  const save = (event: FormEvent) => {
    event.preventDefault();
    if (!editing) return;
    setBusy(true); setFormError('');
    saveAdminEvent(editing.id, editing.form).then(() => { setEditing(null); load(); })
      .catch(e => setFormError(errorText(e))).finally(() => setBusy(false));
  };
  const form = editing?.form;
  return <section aria-labelledby="events-title">
    <div className="admin-heading">
      <h1 id="events-title">Events</h1>
      <button type="button" onClick={() => { setFormError(''); setEditing({ id: null, form: { ...EMPTY_EVENT } }); }}>Add event</button>
    </div>
    <p className="admin-note">The agent reads these events live: new and changed events show up in the next chat answer. Times are Japan time.</p>
    <ImportBox kind="events" onDone={() => load()} />
    {editing && form && <form className="admin-form" onSubmit={save}>
      <h2>{editing.id === null ? 'New event' : 'Edit event'}</h2>
      <div className="admin-form-grid">
        <label>Title<input required maxLength={255} value={form.title ?? ''} onChange={e => set({ title: e.target.value })} /></label>
        <label>Format<select value={form.category ?? ''} onChange={e => set({ category: e.target.value })}>
          {categories.map(category => <option key={category}>{category}</option>)}</select></label>
        <label>Starts (JST)<input type="datetime-local" required value={form.start_at ?? ''} onChange={e => set({ start_at: e.target.value })} /></label>
        <label>Ends (JST)<input type="datetime-local" value={form.end_at ?? ''} onChange={e => set({ end_at: e.target.value })} /></label>
        <label>Registration deadline (JST)<input type="datetime-local" value={form.reg_deadline ?? ''} onChange={e => set({ reg_deadline: e.target.value })} /></label>
        <label>Location<input value={form.location ?? ''} onChange={e => set({ location: e.target.value })} placeholder="Venue, city" /></label>
        <label>Member price<input type="number" min="0" value={form.member_price ?? ''} onChange={e => set({ member_price: numberField(e.target.value) })} /></label>
        <label>Non-member price<input type="number" min="0" value={form.non_member_price ?? ''} onChange={e => set({ non_member_price: numberField(e.target.value) })} /></label>
        <label>Currency<select value={form.currency ?? 'JPY'} onChange={e => set({ currency: e.target.value })}><option>JPY</option><option>EUR</option></select></label>
        <label>Contact<input value={form.contact ?? ''} onChange={e => set({ contact: e.target.value })} placeholder="events@ccifj.or.jp" /></label>
      </div>
      <label className="admin-wide">Description<textarea required rows={5} value={form.description ?? ''} onChange={e => set({ description: e.target.value })} /></label>
      <fieldset><legend>Languages</legend>
        {LANGUAGES.map(([code, label]) => <label key={code} className="admin-check">
          <input type="checkbox" checked={form.languages?.includes(code) ?? false}
            onChange={e => set({ languages: e.target.checked ? [...(form.languages ?? []), code] : (form.languages ?? []).filter(item => item !== code) })} />{label}
        </label>)}
      </fieldset>
      {formError && <p role="alert" className="admin-error">{formError}</p>}
      <div className="admin-form-actions">
        <button type="submit" className="primary" disabled={busy}>{busy ? 'Saving…' : 'Save event'}</button>
        <button type="button" onClick={() => setEditing(null)}>Cancel</button>
      </div>
    </form>}
    <form className="admin-search" onSubmit={(event: FormEvent) => { event.preventDefault(); load(0); }}>
      <input type="search" placeholder="Search title or location" aria-label="Search events" value={query} onChange={e => setQuery(e.target.value)} />
      <select aria-label="Which events" value={scope} onChange={e => setScope(e.target.value as typeof scope)}>
        <option value="upcoming">Upcoming</option><option value="past">Past</option><option value="all">All</option>
      </select>
      <button type="submit" disabled={busy}>Search</button>
    </form>
    {error && <p role="alert" className="admin-error">{error}</p>}
    <div className="admin-table-wrap"><table className="admin-table">
      <thead><tr><th>Event</th><th>When</th><th>Format</th><th>Price</th><th>Languages</th><th></th></tr></thead>
      <tbody>{items.map(item => <tr key={item.event_id}>
        <td className="admin-wrap"><strong>{item.title}</strong><small>{item.location ?? ''}</small></td>
        <td>{formatJst(item.start_at)}</td><td>{item.category}</td><td>{price(item)}</td>
        <td>{item.languages.join(', ') || '—'}</td>
        <td><button type="button" className="admin-link" onClick={() => { setFormError(''); setEditing({ id: item.event_id, form: { ...item, member_price: item.member_price, non_member_price: item.non_member_price } }); window.scrollTo?.(0, 0); }}>Edit</button></td>
      </tr>)}</tbody>
    </table></div>
    {!busy && !items.length && !error && <p>No events found.</p>}
    {next !== null && <button type="button" disabled={busy} onClick={() => load(next)}>Load more</button>}
  </section>;
}

// ---------- Member companies ----------

export function MembersPanel() {
  const [items, setItems] = useState<AdminMember[]>([]);
  const [sectors, setSectors] = useState<string[]>([]);
  const [query, setQuery] = useState('');
  const [sector, setSector] = useState('');
  const [next, setNext] = useState<number | null>(null);
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const load = (offset = 0) => {
    setBusy(true); setError('');
    getAdminMembers(query, sector, offset).then(result => {
      setItems(old => offset ? [...old, ...result.items] : result.items);
      setNext(result.next_offset); setSectors(result.sectors);
    }).catch(e => setError(errorText(e))).finally(() => setBusy(false));
  };
  useEffect(() => load(), [sector]);
  return <section aria-labelledby="members-title">
    <h1 id="members-title">Member companies</h1>
    <p className="admin-note">The member directory the agent searches. Descriptions matter: matching will use them to explain why a company fits.</p>
    <ImportBox kind="members" onDone={() => load()} />
    <form className="admin-search" onSubmit={(event: FormEvent) => { event.preventDefault(); load(0); }}>
      <input type="search" placeholder="Search name, description or place" aria-label="Search members" value={query} onChange={e => setQuery(e.target.value)} />
      <select aria-label="Sector" value={sector} onChange={e => setSector(e.target.value)}>
        <option value="">All sectors</option>{sectors.map(item => <option key={item}>{item}</option>)}
      </select>
      <button type="submit" disabled={busy}>Search</button>
    </form>
    {error && <p role="alert" className="admin-error">{error}</p>}
    <div className="admin-table-wrap"><table className="admin-table">
      <thead><tr><th>Company</th><th>Sector</th><th>Category</th><th>Place</th><th>Staff size</th><th>Accounts</th></tr></thead>
      <tbody>{items.map(item => <tr key={item.member_id}>
        <td className="admin-wrap"><strong>{item.name}</strong>{item.description && <small>{item.description.slice(0, 140)}{item.description.length > 140 ? '…' : ''}</small>}</td>
        <td>{item.sector ?? '—'}</td><td>{item.category ?? '—'}</td>
        <td>{[item.region, item.country].filter(Boolean).join(', ') || '—'}</td>
        <td>{item.manpower ?? '—'}</td><td>{item.accounts}</td>
      </tr>)}</tbody>
    </table></div>
    {!busy && !items.length && !error && <p>No companies found.</p>}
    {next !== null && <button type="button" disabled={busy} onClick={() => load(next)}>Load more</button>}
  </section>;
}

// ---------- Knowledge files ----------

const KNOWLEDGE_TYPES = '.pdf,.docx,.txt,.md,.html,.htm,.png,.jpg,.jpeg';

export function KnowledgePanel() {
  const [docs, setDocs] = useState<KnowledgeDoc[]>([]);
  const [file, setFile] = useState<File | null>(null);
  const [title, setTitle] = useState('');
  const [sourceUrl, setSourceUrl] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const input = useRef<HTMLInputElement>(null);
  const load = () => getKnowledgeDocs().then(setDocs).catch(e => setError(errorText(e)));
  useEffect(() => { load(); }, []);
  const upload = async (event: FormEvent) => {
    event.preventDefault();
    if (!file) return;
    setBusy(true); setError(''); setNotice('');
    try {
      const [encoded] = await toAttachmentUploads([file]);
      const doc = await uploadKnowledgeDoc(encoded, title, sourceUrl);
      setNotice(`"${doc.title}" was added as ${doc.chunk_count} searchable passages.`);
      setFile(null); setTitle(''); setSourceUrl('');
      if (input.current) input.current.value = '';
      load();
    } catch (e) { setError(errorText(e)); } finally { setBusy(false); }
  };
  const remove = (doc: KnowledgeDoc) => {
    if (!window.confirm(`Remove "${doc.title}" from the agent's knowledge? The agent will stop using it.`)) return;
    setError('');
    deleteKnowledgeDoc(doc.doc_id).then(() => setDocs(old => old.filter(item => item.doc_id !== doc.doc_id)))
      .catch(e => setError(errorText(e)));
  };
  return <section aria-labelledby="knowledge-title">
    <h1 id="knowledge-title">Knowledge files</h1>
    <p className="admin-note">Brochures, price lists, procedures and FAQs the agent can quote. Files are read (with OCR for scans), split into passages and searched together with the website.
      Prefer validated, current documents: the agent treats them as official CCIFJ sources.</p>
    <form className="admin-form" onSubmit={upload}>
      <h2>Add a file</h2>
      <div className="admin-form-grid">
        <label>File (PDF, Word, text, image; up to 15 MB)<input ref={input} type="file" required accept={KNOWLEDGE_TYPES} onChange={e => {
          const chosen = e.target.files?.[0] ?? null;
          setFile(chosen);
          if (chosen && !title) setTitle(chosen.name.replace(/\.[^.]+$/, ''));
        }} /></label>
        <label>Title<input value={title} maxLength={255} onChange={e => setTitle(e.target.value)} placeholder="e.g. Membership brochure 2027" /></label>
        <label>Public link (optional)<input type="url" value={sourceUrl} onChange={e => setSourceUrl(e.target.value)} placeholder="https://… (website or Calameo page)" /></label>
      </div>
      {busy && <p role="status">Reading and indexing the file. Long PDFs can take a minute…</p>}
      {notice && <p role="status" className="admin-success">{notice}</p>}
      {error && <p role="alert" className="admin-error">{error}</p>}
      <div className="admin-form-actions"><button type="submit" className="primary" disabled={busy || !file}>{busy ? 'Indexing…' : 'Upload and index'}</button></div>
    </form>
    <div className="admin-table-wrap"><table className="admin-table">
      <thead><tr><th>Document</th><th>Passages</th><th>Added</th><th>Link</th><th></th></tr></thead>
      <tbody>{docs.map(doc => <tr key={doc.doc_id}>
        <td className="admin-wrap"><strong>{doc.title}</strong><small>{doc.filename} · {doc.char_count.toLocaleString()} characters</small></td>
        <td>{doc.chunk_count}</td>
        <td>{new Date(doc.created_at).toLocaleDateString()}</td>
        <td>{doc.source_url ? <a className="admin-link" href={doc.source_url} target="_blank" rel="noreferrer">Open</a> : 'Internal'}</td>
        <td><button type="button" className="admin-link danger" onClick={() => remove(doc)}>Remove</button></td>
      </tr>)}</tbody>
    </table></div>
    {!docs.length && !error && <p>No files yet. The agent currently uses the website content only.</p>}
  </section>;
}
