import { useEffect, useState } from 'react';
import type { FormEvent } from 'react';
import { BarChart3, BookOpen, Building2, CalendarDays, Clock, Inbox, LayoutDashboard, Network, Shield, Users } from 'lucide-react';
import {
  getAdminOverview, getAdminUsers, getStaffContacts, getStaffTopics, saveStaffContact, setUserRole,
} from './api';
import type { StaffContactInput } from './api';
import { EventsPanel, KnowledgePanel, MembersPanel } from './AdminContent';
import { StaffInbox } from './StaffInbox';
import type { AdminOverview, AdminUser, Locale, StaffContact, StaffTopic } from './types';

type Section = 'overview' | 'requests' | 'events' | 'members' | 'knowledge' | 'accounts' | 'directory';

/** Pages planned for the next phases; shown so staff see where the dashboard is going. */
const UPCOMING = [
  { label: 'Matching', icon: Network },
  { label: 'Automation', icon: Clock },
  { label: 'Agent analytics', icon: BarChart3 },
];

function errorText(error: unknown) {
  return error instanceof Error ? error.message : 'Something went wrong.';
}

export function AdminDashboard({ locale, currentUserId }: { locale: Locale; currentUserId: number }) {
  const [section, setSection] = useState<Section>('overview');
  const nav: { id: Section; label: string; icon: typeof Inbox }[] = [
    { id: 'overview', label: 'Overview', icon: LayoutDashboard },
    { id: 'requests', label: 'Requests', icon: Inbox },
    { id: 'events', label: 'Events', icon: CalendarDays },
    { id: 'members', label: 'Member companies', icon: Building2 },
    { id: 'knowledge', label: 'Knowledge files', icon: BookOpen },
    { id: 'accounts', label: 'Accounts & roles', icon: Users },
    { id: 'directory', label: 'Staff directory', icon: Shield },
  ];
  return <div className="admin-shell">
    <nav className="admin-nav" aria-label="Dashboard sections">
      <p className="eyebrow">Staff dashboard</p>
      {nav.map(item => <button key={item.id} type="button" aria-current={section === item.id ? 'page' : undefined}
        className={section === item.id ? 'active' : ''} onClick={() => setSection(item.id)}>
        <item.icon size={16} aria-hidden="true" />{item.label}
      </button>)}
      <p className="admin-nav-soon">Coming next</p>
      {UPCOMING.map(item => <button key={item.label} type="button" disabled title="Planned for a later phase">
        <item.icon size={16} aria-hidden="true" />{item.label}
      </button>)}
    </nav>
    <div className="admin-main">
      {section === 'overview' && <OverviewPanel onOpen={setSection} />}
      {section === 'requests' && <StaffInbox locale={locale} />}
      {section === 'events' && <EventsPanel />}
      {section === 'members' && <MembersPanel />}
      {section === 'knowledge' && <KnowledgePanel />}
      {section === 'accounts' && <AccountsPanel currentUserId={currentUserId} />}
      {section === 'directory' && <DirectoryPanel />}
    </div>
  </div>;
}

function OverviewPanel({ onOpen }: { onOpen: (section: Section) => void }) {
  const [data, setData] = useState<AdminOverview | null>(null);
  const [error, setError] = useState('');
  useEffect(() => { getAdminOverview().then(setData).catch(e => setError(errorText(e))); }, []);
  if (error) return <p role="alert" className="admin-error">{error}</p>;
  if (!data) return <p role="status">Loading overview…</p>;
  const tiles: { label: string; value: number; hint?: string; open?: Section }[] = [
    { label: 'New requests', value: data.new_requests, hint: `${data.open_requests} open in total`, open: 'requests' },
    { label: 'Accounts', value: data.users, hint: `${data.members} linked to a member company`, open: 'accounts' },
    { label: 'Staff accounts', value: data.staff, open: 'accounts' },
    { label: 'Member companies', value: data.member_companies, open: 'members' },
    { label: 'Upcoming events', value: data.upcoming_events, open: 'events' },
    { label: 'Active staff contacts', value: data.active_contacts, open: 'directory' },
  ];
  return <section aria-labelledby="overview-title">
    <h1 id="overview-title">Overview</h1>
    <div className="admin-tiles">
      {tiles.map(tile => <article className="admin-tile" key={tile.label}>
        <span>{tile.label}</span><strong>{tile.value}</strong>
        {tile.hint && <small>{tile.hint}</small>}
        {tile.open && <button type="button" className="admin-link" onClick={() => onOpen(tile.open!)}>Open</button>}
      </article>)}
    </div>
    {(!data.has_triage_contact || data.uncovered_topics.length > 0) && <div className="admin-warning" role="note">
      <h2>Routing gaps</h2>
      {!data.has_triage_contact && <p>No triage contact is set. Requests the agent cannot route become team tickets with no owner.</p>}
      {data.uncovered_topics.length > 0 && <p>No active contact covers: {data.uncovered_topics.join(', ')}.</p>}
      <button type="button" className="admin-link" onClick={() => onOpen('directory')}>Fix in the staff directory</button>
    </div>}
  </section>;
}

function AccountsPanel({ currentUserId }: { currentUserId: number }) {
  const [query, setQuery] = useState('');
  const [items, setItems] = useState<AdminUser[]>([]);
  const [next, setNext] = useState<number | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const load = (q: string, offset = 0) => {
    setBusy(true); setError('');
    getAdminUsers(q, offset).then(result => {
      setItems(old => offset ? [...old, ...result.items] : result.items); setNext(result.next_offset);
    }).catch(e => setError(errorText(e))).finally(() => setBusy(false));
  };
  useEffect(() => load(''), []);
  const changeRole = (user: AdminUser, role: 'user' | 'staff') => {
    setError('');
    setUserRole(user.user_id, role)
      .then(updated => setItems(old => old.map(row => row.user_id === user.user_id ? { ...row, ...updated } : row)))
      .catch(e => setError(errorText(e)));
  };
  return <section aria-labelledby="accounts-title">
    <h1 id="accounts-title">Accounts & roles</h1>
    <p className="admin-note">Members are accounts linked to a member company. Staff can open this dashboard.</p>
    <form className="admin-search" onSubmit={(event: FormEvent) => { event.preventDefault(); load(query); }}>
      <input type="search" placeholder="Search by name or email" aria-label="Search accounts" value={query} onChange={e => setQuery(e.target.value)} />
      <button type="submit" disabled={busy}>Search</button>
    </form>
    {error && <p role="alert" className="admin-error">{error}</p>}
    <div className="admin-table-wrap"><table className="admin-table">
      <thead><tr><th>Name</th><th>Email</th><th>Access</th><th>Company</th><th>Role</th></tr></thead>
      <tbody>{items.map(user => <tr key={user.user_id}>
        <td>{user.name}</td><td>{user.email}</td>
        <td><span className={`admin-badge level-${user.access_level}`}>{user.access_level}</span></td>
        <td>{user.companies.join(', ') || '—'}</td>
        <td><select aria-label={`Role for ${user.email}`} value={user.role} disabled={user.user_id === currentUserId}
          onChange={e => changeRole(user, e.target.value as 'user' | 'staff')}>
          <option value="user">User</option><option value="staff">Staff</option>
        </select></td>
      </tr>)}</tbody>
    </table></div>
    {!busy && !items.length && !error && <p>No accounts found.</p>}
    {next !== null && <button type="button" disabled={busy} onClick={() => load(query, next)}>Load more</button>}
  </section>;
}

const EMPTY: StaffContactInput = { name: '', role: '', email: '', mobile: '', category: '', sub_category: '', languages: '', topics: [], is_triage: false, is_active: true };

function DirectoryPanel() {
  const [contacts, setContacts] = useState<StaffContact[]>([]);
  const [topics, setTopics] = useState<StaffTopic[]>([]);
  const [editing, setEditing] = useState<{ id: number | null; form: StaffContactInput } | null>(null);
  const [error, setError] = useState('');
  const [saving, setSaving] = useState(false);
  useEffect(() => {
    Promise.all([getStaffContacts(), getStaffTopics()])
      .then(([rows, keys]) => { setContacts(rows); setTopics(keys); })
      .catch(e => setError(errorText(e)));
  }, []);
  const topicLabel = (key: string) => topics.find(topic => topic.key === key)?.description ?? key;
  const save = (event: FormEvent) => {
    event.preventDefault();
    if (!editing) return;
    setSaving(true); setError('');
    saveStaffContact(editing.id, editing.form).then(saved => {
      setContacts(old => editing.id === null ? [...old, saved] : old.map(row => row.contact_id === saved.contact_id ? saved : row));
      setEditing(null);
    }).catch(e => setError(errorText(e))).finally(() => setSaving(false));
  };
  const set = (patch: StaffContactInput) => setEditing(current => current && { ...current, form: { ...current.form, ...patch } });
  return <section aria-labelledby="directory-title">
    <div className="admin-heading">
      <h1 id="directory-title">Staff directory</h1>
      <button type="button" onClick={() => setEditing({ id: null, form: { ...EMPTY } })}>Add contact</button>
    </div>
    <p className="admin-note">The agent sends each request to the active contacts for its topic. A triage contact gets anything no one else covers and reassigns it.</p>
    {error && <p role="alert" className="admin-error">{error}</p>}
    {editing && <form className="admin-form" onSubmit={save}>
      <h2>{editing.id === null ? 'New contact' : 'Edit contact'}</h2>
      <div className="admin-form-grid">
        <label>Name<input value={editing.form.name ?? ''} onChange={e => set({ name: e.target.value })} placeholder="Leave empty for a department mailbox" /></label>
        <label>Job title<input value={editing.form.role ?? ''} onChange={e => set({ role: e.target.value })} maxLength={50} /></label>
        <label>Email<input type="email" required value={editing.form.email ?? ''} onChange={e => set({ email: e.target.value })} /></label>
        <label>Phone<input value={editing.form.mobile ?? ''} onChange={e => set({ mobile: e.target.value })} /></label>
        <label>Department<input required value={editing.form.category ?? ''} onChange={e => set({ category: e.target.value })} /></label>
        <label>Languages<input value={editing.form.languages ?? ''} onChange={e => set({ languages: e.target.value })} placeholder="en,fr,ja" /></label>
      </div>
      <label className="admin-wide">What they handle<input value={editing.form.sub_category ?? ''} onChange={e => set({ sub_category: e.target.value })} /></label>
      <fieldset><legend>Agent topics</legend>
        {topics.filter(topic => topic.key !== 'general_or_unsure').map(topic => <label key={topic.key} className="admin-check">
          <input type="checkbox" checked={editing.form.topics?.includes(topic.key) ?? false}
            onChange={e => set({ topics: e.target.checked ? [...(editing.form.topics ?? []), topic.key] : (editing.form.topics ?? []).filter(key => key !== topic.key) })} />
          {topic.description}
        </label>)}
      </fieldset>
      <label className="admin-check"><input type="checkbox" checked={editing.form.is_triage ?? false} onChange={e => set({ is_triage: e.target.checked })} />Triage contact (routes requests nobody else covers)</label>
      <label className="admin-check"><input type="checkbox" checked={editing.form.is_active ?? true} onChange={e => set({ is_active: e.target.checked })} />Active (the agent can suggest this contact)</label>
      <div className="admin-form-actions">
        <button type="submit" className="primary" disabled={saving}>{saving ? 'Saving…' : 'Save'}</button>
        <button type="button" onClick={() => setEditing(null)}>Cancel</button>
      </div>
    </form>}
    <div className="admin-cards">{contacts.map(contact => <article key={contact.contact_id} className={`admin-contact${contact.is_active ? '' : ' inactive'}`}>
      <div className="admin-contact-head">
        <div><strong>{contact.name ?? contact.role ?? contact.category}</strong><small>{contact.name ? contact.role : 'Department mailbox'}</small></div>
        <button type="button" className="admin-link" onClick={() => setEditing({ id: contact.contact_id, form: { ...contact } })}>Edit</button>
      </div>
      <p>{contact.email}{contact.mobile ? ` · ${contact.mobile}` : ''}</p>
      <div className="admin-tags">
        {contact.is_triage && <span className="admin-badge level-staff">Triage</span>}
        {!contact.is_active && <span className="admin-badge">Inactive</span>}
        {contact.topics.map(key => <span key={key} className="admin-badge">{topicLabel(key)}</span>)}
        {!contact.topics.length && !contact.is_triage && <span className="admin-muted">Not used by the agent</span>}
      </div>
    </article>)}</div>
  </section>;
}
