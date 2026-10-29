import type { Locale } from './types';
export const siteCopy = {
  en: {
    name: 'FRANCE JAPAN', chamber: 'FRENCH CHAMBER OF COMMERCE AND INDUSTRY', language: 'Language',
    title: 'How can we help you in Japan?', subtitle: 'Ask the CCIFJ concierge about membership, events, or doing business in Japan.',
    assistant: 'Your assistant',
    checking: 'Connecting…', online: 'Assistant available', offline: 'Assistant offline',
    footer: 'Independent portal · CCI France Japan', skip: 'Skip to content',
  },
  fr: {
    name: 'FRANCE JAPON', chamber: 'CHAMBRE DE COMMERCE ET D’INDUSTRIE', language: 'Langue',
    title: 'Comment vous aider au Japon ?', subtitle: 'Interrogez le concierge CCIFJ sur l’adhésion, les événements ou le développement de votre activité au Japon.',
    assistant: 'Votre assistant',
    checking: 'Connexion en cours…', online: 'Assistant disponible', offline: 'Assistant hors ligne',
    footer: 'Portail indépendant · CCI France Japon', skip: 'Aller au contenu',
  },
  ja: {
    name: '在日フランス商工会議所', chamber: 'CCI FRANCE JAPON', language: '言語',
    title: '日本でのビジネスをお手伝いします', subtitle: '会員制度、イベント、日本でのビジネスについて CCIFJ コンシェルジュにご相談ください。',
    assistant: 'アシスタント',
    checking: '接続を確認中…', online: 'アシスタントに接続済み', offline: 'アシスタントはオフラインです',
    footer: '独立ポータル · 在日フランス商工会議所', skip: 'コンテンツへ移動',
  },
} satisfies Record<Locale, {name:string;chamber:string;language:string;title:string;subtitle:string;assistant:string;checking:string;online:string;offline:string;footer:string;skip:string}>;
export function readLocale(): Locale {
  const query = new URLSearchParams(window.location.search).get('lang');
  if (query === 'fr' || query === 'en' || query === 'ja') return query;
  try {
    const saved = window.localStorage.getItem('ccifj-language');
    if (saved === 'fr' || saved === 'en' || saved === 'ja') return saved;
  } catch { /* Storage may be unavailable in private browser contexts. */ }
  return 'en';
}
