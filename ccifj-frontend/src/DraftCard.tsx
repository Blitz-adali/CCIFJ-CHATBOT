import { useState } from 'react';
import { Check, Copy, Mail, TicketCheck } from 'lucide-react';
import type { Locale } from './types';

export type DraftKind = 'email' | 'ticket';

const words = {
  en: { email: 'Email draft', ticket: 'Ticket for CCIFJ team', to: 'To', from: 'From', cc: 'Cc', subject: 'Subject', copy: 'Copy', copied: 'Copied', drafting: 'Drafting…', team: 'CCIFJ team' },
  fr: { email: 'Brouillon d’e-mail', ticket: 'Ticket pour l’équipe CCIFJ', to: 'À', from: 'De', cc: 'Cc', subject: 'Objet', copy: 'Copier', copied: 'Copié', drafting: 'Rédaction…', team: 'Équipe CCIFJ' },
  ja: { email: 'メール下書き', ticket: 'CCIFJチームへのチケット', to: '宛先', from: '差出人', cc: 'CC', subject: '件名', copy: 'コピー', copied: 'コピーしました', drafting: '作成中…', team: 'CCIFJチーム' },
};

// Header names the agent may write, in any of the UI languages.
const HEADER_KEYS: Record<string, 'to' | 'from' | 'cc' | 'subject'> = {
  to: 'to', 'à': 'to', a: 'to', '宛先': 'to', '宛て先': 'to',
  from: 'from', de: 'from', '差出人': 'from', '送信者': 'from',
  cc: 'cc',
  subject: 'subject', objet: 'subject', '件名': 'subject',
};

export interface DraftFields {
  to?: string;
  from?: string;
  cc?: string;
  subject?: string;
  body: string;
}

/** Split a draft into its leading To/From/Cc/Subject lines and the message body. */
export function parseDraft(text: string): DraftFields {
  const lines = text.replace(/\r\n?/g, '\n').replace(/\n$/, '').split('\n');
  const fields: DraftFields = { body: '' };
  let index = 0;
  for (; index < lines.length; index += 1) {
    const match = lines[index].match(/^\s*([^:：]{1,12})\s*[:：]\s*(.*)$/);
    const key = match ? HEADER_KEYS[match[1].trim().toLowerCase()] : undefined;
    if (!match || !key) break;
    fields[key] = match[2].trim();
  }
  while (index < lines.length && !lines[index].trim()) index += 1;
  fields.body = lines.slice(index).join('\n');
  return fields;
}

/** An email (to a CCIFJ contact) or ticket (for the CCIFJ team) shown as a readable card. */
export function DraftCard({ kind, fields, locale, drafting = false, label }: { kind: DraftKind; fields: DraftFields; locale: Locale; drafting?: boolean; label?: string }) {
  const t = words[locale];
  const [copied, setCopied] = useState(false);
  const Icon = kind === 'email' ? Mail : TicketCheck;
  const rows = ([['to', fields.to ?? (kind === 'ticket' ? t.team : undefined)], ['cc', fields.cc], ['from', fields.from], ['subject', fields.subject]] as const)
    .filter((row): row is readonly ['to' | 'cc' | 'from' | 'subject', string] => Boolean(row[1]));

  async function copy() {
    try {
      await navigator.clipboard.writeText([fields.subject ? `${t.subject}: ${fields.subject}` : '', fields.body].filter(Boolean).join('\n\n'));
      setCopied(true);
      window.setTimeout(() => setCopied(false), 1800);
    } catch { /* Clipboard access can be refused; the text stays selectable. */ }
  }

  return <section className={`draft-card draft-${kind}${drafting ? ' is-drafting' : ''}`} aria-label={label ?? t[kind]}>
    <header className="draft-header">
      <span className="draft-badge"><Icon size={14} aria-hidden="true"/>{label ?? t[kind]}</span>
      {drafting
        ? <span className="draft-status" role="status">{t.drafting}</span>
        : <button type="button" className="draft-copy" onClick={() => void copy()}>{copied ? <Check size={14} aria-hidden="true"/> : <Copy size={14} aria-hidden="true"/>}{copied ? t.copied : t.copy}</button>}
    </header>
    {rows.length > 0 && <dl className="draft-fields">
      {rows.map(([key, value]) => <div key={key}><dt>{t[key]}</dt><dd dir="auto">{value}</dd></div>)}
    </dl>}
    <div className="draft-body" dir="auto">{fields.body}</div>
  </section>;
}
