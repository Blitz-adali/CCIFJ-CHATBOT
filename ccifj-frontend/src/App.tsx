import { useEffect, useState } from 'react';
import { LogIn } from 'lucide-react';
import { WidgetChat } from './WidgetChat';
import { AUTH_EXPIRED_EVENT, clearAuthSession, getSavedAuthSession, getCurrentUser } from './api';
import { AuthDialog } from './AuthDialog';
import { LinkAccount, takeLinkToken } from './LinkAccount';
import { readLocale, saveLocale, siteCopy } from './site-copy';
import { AdminDashboard } from './AdminDashboard';
import { MemberCorner } from './MemberCorner';
import type { AuthSession, Locale } from './types';
import './widget.css';

export default function App() {
  const [locale, setLocale] = useState(readLocale);
  const [inboxOpen, setInboxOpen] = useState(false);
  const [auth, setAuth] = useState<AuthSession | null>(getSavedAuthSession);
  const [authOpen, setAuthOpen] = useState(false);
  // Set when the page was opened from a WhatsApp/Slack account-linking link (?link=...).
  const [linkToken, setLinkToken] = useState(takeLinkToken);
  const t = siteCopy[locale];
  const authLabels = locale === 'fr' ? ['Connexion', 'Déconnexion'] : locale === 'ja' ? ['ログイン', 'ログアウト'] : ['Sign in', 'Sign out'];
  useEffect(() => {
    // The API rejected the saved login (expired or revoked): sign out and offer to log in again.
    const expired = () => { setAuth(null); setAuthOpen(true); };
    window.addEventListener(AUTH_EXPIRED_EVENT, expired);
    return () => window.removeEventListener(AUTH_EXPIRED_EVENT, expired);
  }, []);
  useEffect(() => {
    if (!auth) { setInboxOpen(false); return; }
    let active = true;
    getCurrentUser().then(user => {
      if (active) setAuth(current => current?.user.user_id === user.user_id ? {...current, user} : current);
    }).catch(() => { /* Auth errors use the shared expiry handler. */ });
    return () => { active = false; };
  }, [auth?.user.user_id]);
  useEffect(() => {
    document.documentElement.lang = locale;
    document.title = `CCI ${t.name}`;
  }, [locale, t.name]);
  // ?embed=1 is set by public/embed.js when the chat runs inside a host page (e.g. TYPO3).
  const embedded = new URLSearchParams(window.location.search).get('embed') === '1';
  return <div className={`concierge-site locale-${locale}${embedded ? ' is-embedded' : ''}`}>
    <a className="skip-link" href="#main-content">{t.skip}</a>
    <header className="topbar">
      <a className="brand" href="#main-content" aria-label="CCI France Japon">
        <span className="brand-logo"><b>CCI</b><i>↗</i></span>
        <span><strong>{t.name}</strong><small>{t.chamber}</small></span>
      </a>
      <div className="topbar-right">
        <label className="language-control"><span className="sr-only">{t.language}</span>
          <select aria-label={t.language} value={locale} onChange={event=>{const selected=event.target.value as Locale;setLocale(selected);saveLocale(selected);}}>
            <option value="en" lang="en">EN</option><option value="fr" lang="fr">FR</option><option value="ja" lang="ja">日本語</option>
          </select>
        </label>
        {auth?.user.is_staff && <button type="button" className="signin-button" aria-pressed={inboxOpen} onClick={()=>setInboxOpen(!inboxOpen)}>{inboxOpen ? (locale==='fr'?'Conversation':locale==='ja'?'チャット':'Chat') : (locale==='fr'?'Tableau de bord':locale==='ja'?'ダッシュボード':'Dashboard')}</button>}
        {auth ? <><span className="account-name">{auth.user.name}</span><button className="signin-button" type="button" onClick={()=>{clearAuthSession();setAuth(null);}}>{authLabels[1]}</button></> : <button className="signin-button" type="button" onClick={()=>setAuthOpen(true)}><LogIn size={16}/>{authLabels[0]}</button>}
      </div>
    </header>
    <main id="main-content" className="chat-page">
      {inboxOpen && auth?.user.is_staff && <AdminDashboard key={auth.user.user_id} locale={locale} currentUserId={auth.user.user_id}/>}
      <section className="simple-chat" aria-label={t.assistant} hidden={inboxOpen && Boolean(auth?.user.is_staff)}>
        {auth && !auth.user.is_staff && <MemberCorner key={auth.user.user_id} locale={locale} userId={auth.user.user_id}/>}
        <WidgetChat locale={locale} isAuthenticated={Boolean(auth)} userId={auth?.user.user_id} onLogin={()=>setAuthOpen(true)}/>
      </section>
    </main>
    {linkToken && <LinkAccount locale={locale} token={linkToken} auth={auth} onLogin={()=>setAuthOpen(true)} onClose={()=>setLinkToken(null)}/>}
    <AuthDialog locale={locale} open={authOpen} onClose={()=>setAuthOpen(false)} onSuccess={setAuth}/>
  </div>;
}
