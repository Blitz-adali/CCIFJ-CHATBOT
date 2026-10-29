import { useEffect, useState } from 'react';
import { WidgetChat } from './WidgetChat';
import { clearAuthSession, getApiStatus, getSavedAuthSession } from './api';
import { AuthDialog } from './AuthDialog';
import { readLocale, siteCopy } from './site-copy';
import type { AuthSession, Locale } from './types';
import './widget.css';

export default function App() {
  const [locale, setLocale] = useState<Locale>(readLocale);
  const [status, setStatus] = useState<'checking' | 'online' | 'offline'>('checking');
  const [auth, setAuth] = useState<AuthSession | null>(getSavedAuthSession);
  const [authOpen, setAuthOpen] = useState(false);
  const t = siteCopy[locale];
  const authLabel = locale === 'fr'
    ? { login: 'Connexion', logout: 'Déconnexion' }
    : locale === 'ja'
      ? { login: 'ログイン', logout: 'ログアウト' }
      : { login: 'Log in', logout: 'Log out' };
  useEffect(() => {
    let active = true;
    getApiStatus().then(result => { if (active) setStatus(result.status === 'ok' ? 'online' : 'offline'); })
      .catch(() => { if (active) setStatus('offline'); });
    return () => { active = false; };
  }, []);
  useEffect(() => {
    document.documentElement.lang = locale;
    document.title = `CCI ${siteCopy[locale].name}`;
    try { window.localStorage.setItem('ccifj-language', locale); } catch { /* Optional persistence. */ }
    const url = new URL(window.location.href);
    url.searchParams.set('lang', locale);
    window.history.replaceState(null, '', url);
  }, [locale]);
  return <div className="simple-site">
    <a className="skip-link" href="#main-content">{t.skip}</a>
    <header className="simple-header"><div className="simple-container header-inner">
      <a className="site-brand" href={`?lang=${locale}`}><span className="brand-emblem">CCI<span>↗</span></span><span><strong>{t.name}</strong><small>{t.chamber}</small></span></a>
      <div className="header-actions">
        {auth ? <><span className="account-name">{auth.user.name}</span><button className="auth-button secondary" type="button" onClick={()=>{clearAuthSession();setAuth(null);}}>{authLabel.logout}</button></> : <button className="auth-button" type="button" onClick={()=>setAuthOpen(true)}>{authLabel.login}</button>}
        <label className="site-language"><span>{t.language}</span><select value={locale} onChange={event=>setLocale(event.target.value as Locale)}><option value="en">English</option><option value="fr">Français</option><option value="ja">日本語</option></select></label>
      </div>
    </div></header>
    <main id="main-content" className="simple-container simple-main">
      <section className="simple-intro">
        <div className="intro-topline"><span className="intro-label">CCI FRANCE JAPON</span><span className={`connection-label ${status}`} role="status"><span/>{t[status]}</span></div>
        <h1>{t.title}</h1><p>{t.subtitle}</p>
      </section>
      <section className="simple-chat" aria-label={t.assistant}><WidgetChat locale={locale} isAuthenticated={Boolean(auth)} onLogin={()=>setAuthOpen(true)}/></section>
    </main><footer className="simple-footer simple-container">{t.footer}</footer>
    <AuthDialog locale={locale} open={authOpen} onClose={()=>setAuthOpen(false)} onSuccess={setAuth}/>
  </div>;
}
