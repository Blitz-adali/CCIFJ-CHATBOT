import { useEffect, useRef, useState } from 'react';
import type { FormEvent } from 'react';
import {
  approveMembership, deleteKnowledgeDoc, getAdminEvents, getAdminMembers, getImportTemplate, getKnowledgeDocs, importFile,
  linkKnowledgeDoc, saveAdminEvent, setAdminEventCancelled, setAdminMemberVisible, syncKnowledgeDoc, uploadKnowledgeDoc,
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
        <td className="admin-wrap"><strong>{item.title}</strong>{item.cancelled && <span className="admin-badge level-staff">Cancelled</span>}<small>{item.location ?? ''}</small></td>
        <td>{formatJst(item.start_at)}</td><td>{item.category}</td><td>{price(item)}</td>
        <td>{item.languages.join(', ') || '—'}</td>
        <td><button type="button" className="admin-link" onClick={() => { setFormError(''); setEditing({ id: item.event_id, form: { ...item, member_price: item.member_price, non_member_price: item.non_member_price } }); window.scrollTo?.(0, 0); }}>Edit</button>{' '}
          <button type="button" className={item.cancelled ? 'admin-link' : 'admin-link danger'} onClick={() => {
            const cancel = !item.cancelled;
            if (cancel && !window.confirm(`Cancel "${item.title}"? The agent will stop listing and booking it. Nothing is deleted: existing bookings are kept so you can contact the attendees.`)) return;
            setError('');
            setAdminEventCancelled(item.event_id, cancel).then(updated => {
              setItems(old => old.map(row => row.event_id === item.event_id ? { ...row, cancelled: updated.cancelled } : row));
              if (cancel && updated.confirmed_bookings) window.alert(`${updated.confirmed_bookings} confirmed booking(s) exist for this event. Please contact the attendees.`);
            }).catch(e => setError(errorText(e)));
          }}>{item.cancelled ? 'Restore' : 'Cancel event'}</button></td>
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
  const [approving, setApproving] = useState<AdminMember | null>(null);
  const [approveEmail, setApproveEmail] = useState('');
  const [approveNote, setApproveNote] = useState('');
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
    {approving && <form className="admin-form" onSubmit={(event: FormEvent) => {
      event.preventDefault();
      setBusy(true); setApproveNote('');
      approveMembership(approving.member_id, approveEmail)
        .then(result => { setApproveNote(`Membership approved: the account is now linked to ${result.member} and the welcome message was ${result.welcome.length ? 'sent' : 'already sent before'}.`); load(); })
        .catch(e => setApproveNote(errorText(e))).finally(() => setBusy(false));
    }}>
      <h2>Approve membership: {approving.name}</h2>
      <p className="admin-note">Link a person's CCIFJ account to this company once their membership application is accepted. This starts their onboarding: the welcome message is sent right away and the daily rules follow up. In production, Odoo would trigger this when the membership is validated.</p>
      <label className="admin-wide">Account email<input type="email" required value={approveEmail} onChange={e => setApproveEmail(e.target.value)} placeholder="The email they signed up with" /></label>
      {approveNote && <p role="status" className="admin-note">{approveNote}</p>}
      <div className="admin-form-actions"><button type="submit" className="primary" disabled={busy}>Approve and send welcome</button><button type="button" onClick={() => setApproving(null)}>Close</button></div>
    </form>}
    <form className="admin-search" onSubmit={(event: FormEvent) => { event.preventDefault(); load(0); }}>
      <input type="search" placeholder="Search name, description or place" aria-label="Search members" value={query} onChange={e => setQuery(e.target.value)} />
      <select aria-label="Sector" value={sector} onChange={e => setSector(e.target.value)}>
        <option value="">All sectors</option>{sectors.map(item => <option key={item}>{item}</option>)}
      </select>
      <button type="submit" disabled={busy}>Search</button>
    </form>
    {error && <p role="alert" className="admin-error">{error}</p>}
    <div className="admin-table-wrap"><table className="admin-table">
      <thead><tr><th>Company</th><th>Sector</th><th>Category</th><th>Place</th><th>Contact people</th><th>Accounts</th></tr></thead>
      <tbody>{items.map(item => <tr key={item.member_id}>
        <td className="admin-wrap"><strong>{item.name}</strong>{item.directory_visible === false && <span className="admin-badge">Hidden from directory</span>}{item.description && <small>{item.description.slice(0, 140)}{item.description.length > 140 ? '…' : ''}</small>}</td>
        <td>{item.sector ?? '—'}{item.secondary_sectors && <small>{item.secondary_sectors}</small>}</td><td>{item.category ?? '—'}</td>
        <td>{[item.region, item.country].filter(Boolean).join(', ') || '—'}</td>
        <td className="admin-wrap">{item.contacts?.length ? item.contacts.map(person => <small key={person.name + (person.email ?? '')}>
          <strong>{person.name}</strong>{person.job_title ? `, ${person.job_title}` : ''}{person.language ? ` · ${person.language}` : ''}
        </small>) : (item.contact ?? '—')}</td>
        <td>{item.accounts} <button type="button" className="admin-link" onClick={() => { setApproving(item); setApproveEmail(''); setApproveNote(''); }}>Add account</button>{' '}
          <button type="button" className={item.directory_visible === false ? 'admin-link' : 'admin-link danger'} onClick={() => {
            const show = item.directory_visible === false;
            if (!show && !window.confirm(`Hide "${item.name}" from the directory? The agent will stop showing it in member search and matching. Nothing is deleted.`)) return;
            setError('');
            setAdminMemberVisible(item.member_id, show).then(result =>
              setItems(old => old.map(row => row.member_id === item.member_id ? { ...row, directory_visible: result.directory_visible } : row)))
              .catch(e => setError(errorText(e)));
          }}>{item.directory_visible === false ? 'Show' : 'Hide'}</button></td>
      </tr>)}</tbody>
    </table></div>
    {!busy && !items.length && !error && <p>No companies found.</p>}
    {next !== null && <button type="button" disabled={busy} onClick={() => load(next)}>Load more</button>}
  </section>;
}

// ---------- Knowledge files ----------

const KNOWLEDGE_TYPES = '.pdf,.docx,.txt,.md,.html,.htm,.png,.jpg,.jpeg';
const KNOWLEDGE_MAX_MB = 40; // matches KNOWLEDGE_MAX_BYTES in api/admin_content.py

export function KnowledgePanel() {
  const [docs, setDocs] = useState<KnowledgeDoc[]>([]);
  const [files, setFiles] = useState<File[]>([]);
  const [title, setTitle] = useState('');
  const [sourceUrl, setSourceUrl] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [progress, setProgress] = useState<{ name: string; state: 'waiting' | 'indexing' | 'done' | 'failed'; detail?: string }[]>([]);
  const input = useRef<HTMLInputElement>(null);
  const single = files.length === 1;
  const load = () => getKnowledgeDocs().then(setDocs).catch(e => setError(errorText(e)));
  useEffect(() => { load(); }, []);
  const mark = (index: number, state: 'indexing' | 'done' | 'failed', detail?: string) =>
    setProgress(old => old.map((item, i) => i === index ? { ...item, state, detail } : item));
  const upload = async (event: FormEvent) => {
    event.preventDefault();
    if (!files.length) return;
    const queue = files;
    setBusy(true); setError('');
    setProgress(queue.map(file => ({ name: file.name, state: 'waiting' as const })));
    // One file at a time: each is read (with OCR), embedded and indexed on the server.
    // A failure (e.g. a duplicate) is reported for that file and the next one still goes.
    for (const [index, file] of queue.entries()) {
      const sizeMb = file.size / (1024 * 1024);
      if (sizeMb > KNOWLEDGE_MAX_MB) {
        mark(index, 'failed', `too large (${sizeMb.toFixed(1)} MB; the limit is ${KNOWLEDGE_MAX_MB} MB). Compress the PDF and try again.`);
        continue;
      }
      mark(index, 'indexing');
      try {
        const [encoded] = await toAttachmentUploads([file]);
        const doc = await uploadKnowledgeDoc(encoded, single ? title : '', single ? sourceUrl : '');
        mark(index, 'done', `added as ${doc.chunk_count} passages`);
      } catch (e) { mark(index, 'failed', errorText(e)); }
    }
    setFiles([]); setTitle(''); setSourceUrl('');
    if (input.current) input.current.value = '';
    setBusy(false);
    load();
  };
  const remove = (doc: KnowledgeDoc) => {
    if (!window.confirm(`Remove "${doc.title}" from the agent's knowledge? The agent will stop using it.`)) return;
    setError('');
    deleteKnowledgeDoc(doc.doc_id).then(() => setDocs(old => old.filter(item => item.doc_id !== doc.doc_id)))
      .catch(e => setError(errorText(e)));
  };
  // ---- Linked sources (Calameo, web pages, PDF links): re-checked on a schedule ----
  const [linkUrl, setLinkUrl] = useState('');
  const [linkTitle, setLinkTitle] = useState('');
  const [downloadUrl, setDownloadUrl] = useState('');
  const [linkStatus, setLinkStatus] = useState('');
  const [linkError, setLinkError] = useState('');
  const [syncing, setSyncing] = useState<number | null>(null);
  const isCalameo = /(^|\.)calameo\.com\//i.test(linkUrl.replace(/^https:\/\//i, ''));
  const addLink = async (event: FormEvent) => {
    event.preventDefault();
    setBusy(true); setLinkError(''); setLinkStatus('Reading the link and indexing… (long PDFs can take a minute)');
    try {
      const doc = await linkKnowledgeDoc(linkUrl.trim(), linkTitle.trim(), downloadUrl.trim());
      setLinkStatus(`Added "${doc.title}" as ${doc.chunk_count} passages.${doc.sync_note ? ' ' + doc.sync_note : ''}`);
      setLinkUrl(''); setLinkTitle(''); setDownloadUrl('');
      load();
    } catch (e) { setLinkStatus(''); setLinkError(errorText(e)); }
    setBusy(false);
  };
  const syncNow = async (doc: KnowledgeDoc) => {
    setSyncing(doc.doc_id); setLinkError('');
    try {
      const updated = await syncKnowledgeDoc(doc.doc_id);
      setDocs(old => old.map(item => item.doc_id === doc.doc_id ? updated : item));
      setLinkStatus(updated.changed ? `"${doc.title}" changed and was re-indexed.` : `"${doc.title}" is up to date.`);
    } catch (e) { setLinkError(errorText(e)); }
    setSyncing(null);
  };
  return <section aria-labelledby="knowledge-title">
    <h1 id="knowledge-title">Knowledge files and links</h1>
    <p className="admin-note">Brochures, price lists, procedures and FAQs the agent can quote. Files are read (with OCR for scans), split into passages and searched together with the website.
      Prefer validated, current documents: the agent treats them as official CCIFJ sources.</p>
    <form className="admin-form" onSubmit={upload}>
      <h2>Add files</h2>
      <label className="admin-file-field">Files (PDF, Word, text, image; up to {KNOWLEDGE_MAX_MB} MB each; you can select several at once)
        <input ref={input} type="file" multiple required accept={KNOWLEDGE_TYPES} disabled={busy} onChange={e => {
          const chosen = Array.from(e.target.files ?? []);
          setFiles(chosen); setProgress([]);
          setTitle(chosen.length === 1 ? chosen[0].name.replace(/\.[^.]+$/, '') : '');
          if (chosen.length !== 1) setSourceUrl('');
        }} /></label>
      {files.length <= 1 ? <div className="admin-form-grid admin-file-details">
        <label>Title<input value={title} maxLength={255} onChange={e => setTitle(e.target.value)} placeholder="e.g. Membership brochure 2027" /></label>
        <label>Public link (optional)<input type="url" value={sourceUrl} onChange={e => setSourceUrl(e.target.value)} placeholder="https://… (website or Calameo page)" /></label>
      </div> : <p className="admin-note">{files.length} files selected. Each one is titled after its file name and cited as an internal document; to set a title or a public link, upload that file on its own.</p>}
      {progress.length > 0 && <ul className="admin-note" aria-live="polite">{progress.map((item, index) => <li key={`${index}:${item.name}`}>
        <strong>{item.name}</strong>: {item.state === 'waiting' ? 'waiting' : item.state === 'indexing' ? 'reading and indexing… (long PDFs can take a minute)' : item.detail}
        {item.state === 'failed' && <span className="admin-error"> (failed)</span>}
      </li>)}</ul>}
      {!busy && progress.length > 1 && <p role="status" className="admin-success">
        {progress.filter(item => item.state === 'done').length} of {progress.length} files added.</p>}
      {error && <p role="alert" className="admin-error">{error}</p>}
      <div className="admin-form-actions"><button type="submit" className="primary" disabled={busy || !files.length}>
        {busy ? 'Indexing…' : files.length > 1 ? `Upload and index ${files.length} files` : 'Upload and index'}</button></div>
    </form>
    <form className="admin-form" onSubmit={addLink}>
      <h2>Link a source (auto-updated)</h2>
      <p className="admin-note">Paste a Calameo publication, a web page or a link to a PDF. The agent re-reads it every few hours,
        so when you update the publication or page, the agent follows without a new upload.</p>
      <div className="admin-form-grid admin-file-details">
        <label>Link<input type="url" required value={linkUrl} disabled={busy} onChange={e => setLinkUrl(e.target.value)}
          placeholder="https://www.calameo.com/read/… or https://…/brochure.pdf" /></label>
        <label>Title (optional)<input value={linkTitle} maxLength={255} disabled={busy} onChange={e => setLinkTitle(e.target.value)}
          placeholder="Taken from the page if empty" /></label>
        {isCalameo && <label>PDF download link (optional, for the full text)<input type="url" value={downloadUrl} disabled={busy}
          onChange={e => setDownloadUrl(e.target.value)} placeholder="https://… link to the same PDF" /></label>}
      </div>
      {isCalameo && !downloadUrl && <p className="admin-note">Without a PDF link, only the publication's title and summary are indexed
        (Calameo does not share the full text). Enable "Allow download" on Calameo, or paste a link to the same PDF, to index every page.</p>}
      {linkStatus && <p role="status" className="admin-success">{linkStatus}</p>}
      {linkError && <p role="alert" className="admin-error">{linkError}</p>}
      <div className="admin-form-actions"><button type="submit" className="primary" disabled={busy || !linkUrl.trim()}>
        {busy ? 'Indexing…' : 'Add link'}</button></div>
    </form>
    <div className="admin-table-wrap"><table className="admin-table">
      <thead><tr><th>Document</th><th>Passages</th><th>Added</th><th>Link</th><th></th></tr></thead>
      <tbody>{docs.map(doc => <tr key={doc.doc_id}>
        <td className="admin-wrap"><strong>{doc.title}</strong><small>{doc.filename} · {doc.char_count.toLocaleString()} characters</small>
          {doc.linked && <small>Auto-updated · checked {doc.checked_at ? new Date(doc.checked_at).toLocaleString() : 'never'}
            {doc.changed_at && ` · content from ${new Date(doc.changed_at).toLocaleString()}`}</small>}
          {doc.sync_note && <small className={doc.sync_note.startsWith('Last check failed') ? 'admin-error' : undefined}>{doc.sync_note}</small>}</td>
        <td>{doc.chunk_count}</td>
        <td>{new Date(doc.created_at).toLocaleDateString()}</td>
        <td>{doc.source_url ? <a className="admin-link" href={doc.source_url} target="_blank" rel="noreferrer">Open</a> : 'Internal'}</td>
        <td>{doc.linked && <button type="button" className="admin-link" disabled={syncing !== null} onClick={() => syncNow(doc)}>
          {syncing === doc.doc_id ? 'Syncing…' : 'Sync now'}</button>}{' '}
          <button type="button" className="admin-link danger" onClick={() => remove(doc)}>Remove</button></td>
      </tr>)}</tbody>
    </table></div>
    {!docs.length && !error && <p>No files yet. The agent currently uses the website content only.</p>}
  </section>;
}
