import type { Locale } from './types';
export const siteCopy = {
  en: {
    name: 'FRANCE JAPAN', chamber: 'FRENCH CHAMBER OF COMMERCE AND INDUSTRY', language: 'Language',
    title: 'Make your next move in Japan.', subtitle: 'A clear starting point for business, membership and connections between France and Japan.',
    assistant: 'Your assistant', checking: 'Connecting…', online: 'Concierge online', offline: 'Concierge offline',
    footer: 'CCI France Japan · Your bridge to the Japanese market', skip: 'Skip to content',
  },
  fr: {
    name: 'FRANCE JAPON', chamber: 'CHAMBRE DE COMMERCE ET D’INDUSTRIE', language: 'Langue',
    title: 'Donnez vie à vos projets au Japon.', subtitle: 'Un point de départ clair pour vos activités, votre adhésion et vos échanges entre la France et le Japon.',
    assistant: 'Votre assistant', checking: 'Connexion…', online: 'Concierge en ligne', offline: 'Concierge hors ligne',
    footer: 'CCI France Japon · Votre passerelle vers le marché japonais', skip: 'Aller au contenu',
  },
  ja: {
    name: '在日フランス商工会議所', chamber: 'CCI FRANCE JAPON', language: '言語',
    title: '日本での次の一歩を、ここから。', subtitle: 'ビジネス、会員サービス、フランスと日本をつなぐネットワークをご案内します。',
    assistant: 'アシスタント', checking: '接続中…', online: 'コンシェルジュ接続中', offline: 'コンシェルジュに接続できません',
    footer: 'CCI France Japon · 日本市場への架け橋', skip: 'コンテンツへ移動',
  },
} satisfies Record<Locale, {name:string;chamber:string;language:string;title:string;subtitle:string;assistant:string;checking:string;online:string;offline:string;footer:string;skip:string}>;

export function saveLocale(locale: Locale): void {
  try { window.localStorage.setItem('ccifj-locale', locale); } catch { /* Storage is optional. */ }
  // Keep explicit embed/URL language consistent with a manual selection.
  try {
    const url = new URL(window.location.href);
    url.searchParams.set('lang', locale);
    window.history.replaceState(null, '', url);
  } catch { /* Hosts may restrict history updates. */ }
}

/** Explicit URL, saved preference, then the browser's preferred language. */
export function readLocale(): Locale {
  const query = new URLSearchParams(typeof window === 'undefined' ? '' : window.location.search).get('lang');
  if (query === 'fr' || query === 'en' || query === 'ja') return query;
  try {
    const saved = window.localStorage.getItem('ccifj-locale');
    if (saved === 'fr' || saved === 'en' || saved === 'ja') return saved;
  } catch { /* Storage is optional. */ }
  const preferences = typeof navigator === 'undefined' ? [] : [...(navigator.languages ?? []), navigator.language];
  for (const preference of preferences) {
    const language = preference.toLowerCase().split(/[-_]/)[0];
    if (language === 'fr' || language === 'ja' || language === 'en') return language;
  }
  return 'en';
}

/** Backend UI locale is limited to supported locales; free-text user content stays untouched. */
export function detectMessageLocale(text: string, fallback: Locale): Locale {
  if (/[\u3040-\u30ff\u3400-\u9fff]/u.test(text)) return 'ja';
  if (/[àâçéèêëîïôùûüÿœæ]/iu.test(text) || /\b(bonjour|merci|adhésion|événement|entreprise|comment|pourquoi|quels?)\b/iu.test(text)) return 'fr';
  return fallback;
}
