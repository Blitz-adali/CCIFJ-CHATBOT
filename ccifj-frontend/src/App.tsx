import { useEffect, useState } from 'react';
import { LogIn } from 'lucide-react';
import { WidgetChat } from './WidgetChat';
import { AUTH_EXPIRED_EVENT, clearAuthSession, getSavedAuthSession } from './api';
import { AuthDialog } from './AuthDialog';
import { readLocale, siteCopy } from './site-copy';
import type { AuthSession } from './types';
import './widget.css';

export default function App() {
  const [locale] = useState(readLocale);
  const [auth, setAuth] = useState<AuthSession | null>(getSavedAuthSession);
  const [authOpen, setAuthOpen] = useState(false);
  const t = siteCopy[locale];
  const authLabels = locale === 'fr' ? ['Connexion', 'Déconnexion'] : locale === 'ja' ? ['ログイン', 'ログアウト'] : ['Sign in', 'Sign out'];
  useEffect(() => {
    // The API rejected the saved login (expired or revoked): sign out and offer to log in again.
    const expired = () => { setAuth(null); setAuthOpen(true); };
    window.addEventListener(AUTH_EXPIRED_EVENT, expired);
    return () => window.removeEventListener(AUTH_EXPIRED_EVENT, expired);
  }, []);
  useEffect(() => {
    document.documentElement.lang = locale;
    document.title = `CCI ${t.name}`;
  }, [locale, t.name]);
  return <div className={`concierge-site locale-${locale}`}>
    <a className="skip-link" href="#main-content">{t.skip}</a>
    <header className="topbar">
      <a className="brand" href="#main-content" aria-label="CCI France Japon">
        <span className="brand-logo"><b>CCI</b><i>↗</i></span>
        <span><strong>{t.name}</strong><small>{t.chamber}</small></span>
      </a>
      <div className="topbar-right">
        {auth ? <><span className="account-name">{auth.user.name}</span><button className="signin-button" type="button" onClick={()=>{clearAuthSession();setAuth(null);}}>{authLabels[1]}</button></> : <button className="signin-button" type="button" onClick={()=>setAuthOpen(true)}><LogIn size={16}/>{authLabels[0]}</button>}
      </div>
    </header>
    <main id="main-content" className="chat-page">
      <section className="simple-chat" aria-label={t.assistant}>
        <WidgetChat locale={locale} isAuthenticated={Boolean(auth)} userId={auth?.user.user_id} onLogin={()=>setAuthOpen(true)}/>
      </section>
    </main>
    <AuthDialog locale={locale} open={authOpen} onClose={()=>setAuthOpen(false)} onSuccess={setAuth}/>
  </div>;
}
