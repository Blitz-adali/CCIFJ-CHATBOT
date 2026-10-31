import { useState, type FormEvent } from 'react';
import { ApiError, loginAccount, registerAccount } from './api';
import type { AuthSession, Locale } from './types';

const words = {
  en: { login: 'Log in', register: 'Create account', name: 'Name', email: 'Email', password: 'Password', create: 'Create account', submitLogin: 'Log in', switchToRegister: 'New to CCIFJ? Create an account', switchToLogin: 'Already registered? Log in', close: 'Close', error: 'Could not complete sign in.' },
  fr: { login: 'Connexion', register: 'Créer un compte', name: 'Nom', email: 'E-mail', password: 'Mot de passe', create: 'Créer un compte', submitLogin: 'Se connecter', switchToRegister: 'Nouveau sur CCIFJ ? Créez un compte', switchToLogin: 'Déjà inscrit ? Connectez-vous', close: 'Fermer', error: 'Connexion impossible.' },
  ja: { login: 'ログイン', register: 'アカウント登録', name: 'お名前', email: 'メールアドレス', password: 'パスワード', create: 'アカウントを作成', submitLogin: 'ログイン', switchToRegister: '初めての方はこちら：アカウント登録', switchToLogin: '登録済みの方はこちら：ログイン', close: '閉じる', error: 'ログインできませんでした。' },
};

export function AuthDialog({
  locale,
  open,
  onClose,
  onSuccess,
}: {
  locale: Locale;
  open: boolean;
  onClose: () => void;
  onSuccess: (session: AuthSession) => void;
}) {
  const [mode, setMode] = useState<'login' | 'register'>('login');
  const [name, setName] = useState('');
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  if (!open) return null;
  const t = words[locale];

  async function submit(event: FormEvent) {
    event.preventDefault();
    setError('');
    setBusy(true);
    try {
      const session = mode === 'login'
        ? await loginAccount(email, password)
        : await registerAccount(name, email, password);
      onSuccess(session);
      setPassword('');
      onClose();
    } catch (cause) {
      setError(cause instanceof ApiError ? cause.message : t.error);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="auth-backdrop" role="presentation" onMouseDown={event => { if (event.target === event.currentTarget) onClose(); }}>
      <section className="auth-dialog" role="dialog" aria-modal="true" aria-labelledby="auth-title">
        <button className="auth-close" type="button" aria-label={t.close} onClick={onClose}>×</button>
        <span className="auth-kicker">CCIFJ CONCIERGE</span>
        <h2 id="auth-title">{mode === 'login' ? t.login : t.register}</h2>
        <form onSubmit={event => void submit(event)}>
          {mode === 'register' && <label>{t.name}<input autoComplete="name" value={name} onChange={event => setName(event.target.value)} required maxLength={100}/></label>}
          <label>{t.email}<input type="email" autoComplete="email" value={email} onChange={event => setEmail(event.target.value)} required maxLength={255}/></label>
          <label>{t.password}<input type="password" autoComplete={mode === 'login' ? 'current-password' : 'new-password'} value={password} onChange={event => setPassword(event.target.value)} required minLength={mode === 'register' ? 8 : 1} maxLength={1024}/></label>
          {error && <p className="auth-error" role="alert">{error}</p>}
          <button className="auth-submit" type="submit" disabled={busy}>{busy ? '…' : mode === 'login' ? t.submitLogin : t.create}</button>
        </form>
        <button className="auth-switch" type="button" disabled={busy} onClick={() => { setError(''); setMode(mode === 'login' ? 'register' : 'login'); }}>
          {mode === 'login' ? t.switchToRegister : t.switchToLogin}
        </button>
      </section>
    </div>
  );
}
