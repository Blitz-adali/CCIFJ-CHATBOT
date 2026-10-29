import type { Locale, PendingApproval } from './types';

const words = {
  en: { title: 'Review this action', login: 'Log in to continue', approve: 'Approve', reject: 'Reject', details: 'Action details' },
  fr: { title: 'Vérifier cette action', login: 'Connectez-vous pour continuer', approve: 'Approuver', reject: 'Refuser', details: 'Détails de l’action' },
  ja: { title: '操作内容を確認', login: 'ログインして続行', approve: '承認', reject: '拒否', details: '操作の詳細' },
};

export function ApprovalPanel({
  approval,
  locale,
  authenticated,
  disabled,
  onLogin,
  onDecision,
}: {
  approval: PendingApproval;
  locale: Locale;
  authenticated: boolean;
  disabled: boolean;
  onLogin: () => void;
  onDecision: (decision: 'approve' | 'reject') => void;
}) {
  const t = words[locale];
  return (
    <section className="approval-panel" aria-label={t.title}>
      <div className="approval-heading">{t.title}</div>
      {approval.actions.map((action, index) => (
        <details className="approval-detail" key={`${action.name}-${index}`}>
          <summary>{action.name} · {t.details}</summary>
          <pre>{JSON.stringify(action.args, null, 2)}</pre>
        </details>
      ))}
      {authenticated ? (
        <div className="approval-buttons">
          <button type="button" className="approval-reject" disabled={disabled} onClick={() => onDecision('reject')}>{t.reject}</button>
          <button type="button" className="approval-accept" disabled={disabled} onClick={() => onDecision('approve')}>{t.approve}</button>
        </div>
      ) : (
        <button type="button" className="auth-button" disabled={disabled} onClick={onLogin}>{t.login}</button>
      )}
    </section>
  );
}
