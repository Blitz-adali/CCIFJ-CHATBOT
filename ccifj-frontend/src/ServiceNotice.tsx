import type { Locale } from './types';

/** Explain evidence limitations without exposing tool names or stack traces. */
export function ServiceNotice({warnings = [], mode, locale}: {warnings?: string[]; mode?: string; locale: Locale}) {
  if (mode === 'recovery' || !warnings.includes('tool_unavailable')) return null;
  const text = {
    en: 'Some requested details could not be checked. Please confirm any unverified information before making arrangements.',
    fr: 'Certaines informations demandées n’ont pas pu être vérifiées. Confirmez les points non vérifiés avant de prendre des dispositions.',
    ja: 'ご希望の情報の一部を確認できませんでした。手配を進める前に、未確認の内容をご確認ください。',
  }[locale];
  return <p className="service-notice" role="status">{text}</p>;
}
