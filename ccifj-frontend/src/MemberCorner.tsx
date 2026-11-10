import { useEffect, useState } from 'react';
import type { FormEvent } from 'react';
import { Bell, UserRound } from 'lucide-react';
import { getMyNotifications, getMyProfile, markNotificationRead, saveMyProfile } from './api';
import type { Locale, MemberNotification, MemberProfile } from './types';

const words = {
  en: {messages: 'Messages from CCIFJ', unread: 'new', profile: 'Complete my profile', editProfile: 'My member profile', goals: 'What do you want from CCIFJ?',
    interests: 'Your sectors, products or topics', interestsHint: 'e.g. wine import, distribution to restaurants', language: 'Preferred language',
    channel: 'How should we contact you?', inApp: 'In this app', email: 'Email', line: 'LINE', save: 'Save my profile', saved: 'Thank you! Your recommendations will now follow your goals.',
    close: 'Close', welcome: 'Welcome! Tell us your goals so we can recommend the right services, events and people.'},
  fr: {messages: 'Messages de la CCIFJ', unread: 'nouveau', profile: 'Compléter mon profil', editProfile: 'Mon profil membre', goals: 'Qu’attendez-vous de la CCIFJ ?',
    interests: 'Vos secteurs, produits ou sujets', interestsHint: 'ex. import de vins, distribution aux restaurants', language: 'Langue préférée',
    channel: 'Comment vous contacter ?', inApp: 'Dans cette application', email: 'E-mail', line: 'LINE', save: 'Enregistrer mon profil', saved: 'Merci ! Nos recommandations suivront désormais vos objectifs.',
    close: 'Fermer', welcome: 'Bienvenue ! Indiquez vos objectifs pour recevoir les services, événements et contacts adaptés.'},
  ja: {messages: 'CCIFJからのお知らせ', unread: '件の新着', profile: 'プロフィールを完成させる', editProfile: '会員プロフィール', goals: 'CCIFJに期待すること',
    interests: '業界・製品・関心分野', interestsHint: '例：ワイン輸入、レストランへの販売', language: '希望言語',
    channel: 'ご連絡方法', inApp: 'このアプリ内', email: 'メール', line: 'LINE', save: 'プロフィールを保存', saved: 'ありがとうございます。今後のおすすめは目標に沿って行います。',
    close: '閉じる', welcome: 'ようこそ！目標を教えていただければ、最適なサービス・イベント・会員をご紹介します。'},
};

/** For signed-in members: CCIFJ's onboarding messages and the profile that personalises them. */
export function MemberCorner({ locale, userId }: { locale: Locale; userId: number }) {
  const t = words[locale];
  const [profile, setProfile] = useState<MemberProfile | null>(null);
  const [notes, setNotes] = useState<MemberNotification[]>([]);
  const [open, setOpen] = useState<number | null>(null);
  const [editing, setEditing] = useState(false);
  useEffect(() => {
    let active = true;
    getMyProfile().then(value => {
      if (!active || value.access_level !== 'member') return;
      setProfile(value);
      getMyNotifications().then(list => active && setNotes(list)).catch(() => undefined);
    }).catch(() => undefined);
    return () => { active = false; };
  }, [userId]);
  if (!profile) return null;
  const unread = notes.filter(note => !note.read).length;
  const read = (note: MemberNotification) => {
    setOpen(open === note.id ? null : note.id);
    if (!note.read) {
      setNotes(old => old.map(item => item.id === note.id ? { ...item, read: true } : item));
      markNotificationRead(note.id).catch(() => undefined);
    }
  };
  return <aside className="member-corner" aria-label={t.messages}>
    <div className="member-corner-bar">
      {notes.length > 0 && <span className="member-corner-title"><Bell size={15} aria-hidden="true" />{t.messages}{unread > 0 && <b>{unread} {t.unread}</b>}</span>}
      {!profile.profile_completed && <span className="member-corner-welcome">{t.welcome}</span>}
      <button type="button" onClick={() => setEditing(!editing)}><UserRound size={15} aria-hidden="true" />{profile.profile_completed ? t.editProfile : t.profile}</button>
    </div>
    {notes.length > 0 && <ul className="member-notes">{notes.slice(0, 4).map(note => <li key={note.id} className={note.read ? '' : 'unread'}>
      <button type="button" aria-expanded={open === note.id} onClick={() => read(note)}>{note.subject}<small>{note.sent_at.slice(0, 10)}</small></button>
      {open === note.id && <p dir="auto">{note.body}</p>}
    </li>)}</ul>}
    {editing && <ProfileForm profile={profile} t={t} onSaved={value => { setProfile(value); }} onClose={() => setEditing(false)} />}
  </aside>;
}

function ProfileForm({ profile, t, onSaved, onClose }: { profile: MemberProfile; t: typeof words.en; onSaved: (p: MemberProfile) => void; onClose: () => void }) {
  const [goals, setGoals] = useState(profile.goals);
  const [interests, setInterests] = useState(profile.interests);
  const [language, setLanguage] = useState<string>(profile.preferred_language);
  const [channel, setChannel] = useState<string>(profile.preferred_channel);
  const [done, setDone] = useState(false);
  const [busy, setBusy] = useState(false);
  const submit = (event: FormEvent) => {
    event.preventDefault();
    setBusy(true);
    saveMyProfile({ goals, interests, preferred_language: language, preferred_channel: channel }).then(() => {
      setDone(true);
      onSaved({ ...profile, goals, interests, preferred_language: language as MemberProfile['preferred_language'],
        preferred_channel: channel as MemberProfile['preferred_channel'], profile_completed: true });
    }).finally(() => setBusy(false));
  };
  return <form className="member-profile" onSubmit={submit}>
    <fieldset><legend>{t.goals}</legend>
      {profile.goal_options.map(option => <label key={option.key}><input type="checkbox" checked={goals.includes(option.key)}
        onChange={e => setGoals(e.target.checked ? [...goals, option.key] : goals.filter(key => key !== option.key))} />{option.label}</label>)}
    </fieldset>
    <label>{t.interests}<input value={interests} maxLength={1000} placeholder={t.interestsHint} onChange={e => setInterests(e.target.value)} /></label>
    <div className="member-profile-row">
      <label>{t.language}<select value={language} onChange={e => setLanguage(e.target.value)}><option value="en">English</option><option value="fr">Français</option><option value="ja">日本語</option></select></label>
      <label>{t.channel}<select value={channel} onChange={e => setChannel(e.target.value)}><option value="in_app">{t.inApp}</option><option value="email">{t.email}</option><option value="line">{t.line}</option></select></label>
    </div>
    {done && <p role="status" className="member-profile-done">{t.saved}</p>}
    <div className="member-profile-actions"><button type="submit" disabled={busy}>{t.save}</button><button type="button" onClick={onClose}>{t.close}</button></div>
  </form>;
}
