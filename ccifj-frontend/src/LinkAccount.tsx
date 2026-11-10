import { useEffect, useState } from 'react';
import { createPortal } from 'react-dom';
import { ApiError, completeAccountLink, previewAccountLink, type ChatAccountLink } from './api';
import type { AuthSession, Locale } from './types';

const PLATFORM = { whatsapp: 'WhatsApp', slack: 'Slack' };

const words = {
  en: {
    title: 'Link your chat account',
    signIn: 'Sign in to your CCIFJ account to link it with the chat you came from. Your password is only ever entered here, never in the chat.',
    signInButton: 'Sign in to continue',
    checking: 'Checking the link…',
    confirm: (platform: string, hint: string, name: string) => `Link the ${platform} account ${hint} to the CCIFJ account of ${name}?`,
    warning: 'Only continue if you asked for this link yourself. Whoever uses that chat account will be able to make bookings as you.',
    link: 'Link account', cancel: 'Cancel', close: 'Close',
    done: (platform: string) => `Your account is linked. You can go back to ${platform} and continue there.`,
    error: 'This link could not be used. Send "link" in the chat to get a new one.',
  },
  fr: {
    title: 'Lier votre compte de messagerie',
    signIn: 'Connectez-vous à votre compte CCIFJ pour le lier à la conversation d’où vous venez. Votre mot de passe se saisit uniquement ici, jamais dans la conversation.',
    signInButton: 'Se connecter pour continuer',
    checking: 'Vérification du lien…',
    confirm: (platform: string, hint: string, name: string) => `Lier le compte ${platform} ${hint} au compte CCIFJ de ${name} ?`,
    warning: 'Ne continuez que si vous avez demandé ce lien vous-même. La personne qui utilise ce compte de messagerie pourra réserver en votre nom.',
    link: 'Lier le compte', cancel: 'Annuler', close: 'Fermer',
    done: (platform: string) => `Votre compte est lié. Vous pouvez retourner sur ${platform} et y poursuivre.`,
    error: 'Ce lien n’a pas pu être utilisé. Envoyez « link » dans la conversation pour en obtenir un nouveau.',
  },
  ja: {
    title: 'チャットアカウントの連携',
    signIn: 'チャットと連携するには、CCIFJアカウントにログインしてください。パスワードはこの画面でのみ入力し、チャットには送信しないでください。',
    signInButton: 'ログインして続ける',
    checking: 'リンクを確認しています…',
    confirm: (platform: string, hint: string, name: string) => `${platform}アカウント（${hint}）を${name}さんのCCIFJアカウントと連携しますか？`,
    warning: 'ご自身でこのリンクを依頼した場合のみ続行してください。このチャットアカウントの利用者は、あなたの名義で予約できるようになります。',
    link: '連携する', cancel: 'キャンセル', close: '閉じる',
    done: (platform: string) => `連携が完了しました。${platform}に戻って続けてください。`,
    error: 'このリンクは使用できませんでした。チャットで「link」と送信して新しいリンクを取得してください。',
  },
};

/** Read ?link=<token> once and remove it from the address bar, history and later Referer headers. */
export function takeLinkToken(): string | null {
  const token = new URLSearchParams(window.location.search).get('link');
  if (!token) return null;
  const url = new URL(window.location.href);
  url.searchParams.delete('link');
  window.history.replaceState(window.history.state, '', url);
  return token;
}

/**
 * Finishes linking a WhatsApp or Slack account. The token stays in memory only; the user
 * signs in normally, sees which chat account the link belongs to, and confirms.
 */
export function LinkAccount({ locale, token, auth, onLogin, onClose }: {
  locale: Locale;
  token: string;
  auth: AuthSession | null;
  onLogin: () => void;
  onClose: () => void;
}) {
  const t = words[locale];
  const [link, setLink] = useState<ChatAccountLink | null>(null);
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const [done, setDone] = useState(false);
  const userId = auth?.user.user_id;

  useEffect(() => {
    if (userId === undefined) { setLink(null); return; }
    let active = true;
    setError('');
    previewAccountLink(token).then(found => { if (active) setLink(found); }).catch(cause => {
      // A 401 means the saved login expired: the shared handler signs out and asks to log in again.
      if (active && !(cause instanceof ApiError && cause.status === 401)) setError(cause instanceof ApiError ? cause.message : t.error);
    });
    return () => { active = false; };
  }, [token, userId]);

  async function confirm() {
    setBusy(true);
    setError('');
    try {
      setLink(await completeAccountLink(token, locale));
      setDone(true);
    } catch (cause) {
      if (!(cause instanceof ApiError && cause.status === 401)) setError(cause instanceof ApiError ? cause.message : t.error);
    } finally {
      setBusy(false);
    }
  }

  const platform = link ? PLATFORM[link.platform] ?? link.platform : '';
  return createPortal(
    <div className="auth-backdrop" role="presentation">
      <section className="auth-dialog link-dialog" role="dialog" aria-modal="true" aria-labelledby="link-title">
        <button className="auth-close" type="button" aria-label={t.close} onClick={onClose}>×</button>
        <span className="auth-kicker">CCIFJ CONCIERGE</span>
        <h2 id="link-title">{t.title}</h2>
        {error ? <>
          <p className="auth-error" role="alert">{error}</p>
          <button className="auth-submit" type="button" onClick={onClose}>{t.close}</button>
        </> : done && link ? <>
          <p role="status">{t.done(platform)}</p>
          <button className="auth-submit" type="button" onClick={onClose}>{t.close}</button>
        </> : !auth ? <>
          <p>{t.signIn}</p>
          <button className="auth-submit" type="button" onClick={onLogin}>{t.signInButton}</button>
        </> : !link ? <p role="status">{t.checking}</p> : <>
          <p><strong>{t.confirm(platform, link.account_hint, auth.user.name)}</strong></p>
          <p className="link-warning">{t.warning}</p>
          <button className="auth-submit" type="button" disabled={busy} onClick={() => void confirm()}>{busy ? '…' : t.link}</button>
          <button className="auth-switch" type="button" disabled={busy} onClick={onClose}>{t.cancel}</button>
        </>}
      </section>
    </div>,
    document.body,
  );
}
