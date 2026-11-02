import { ArrowUpRight, LogIn } from 'lucide-react';
import { getCopy } from './i18n';
import type { Locale } from './types';
import type { NextAction, PathAction } from './types';

export function FollowUpQuestions({
  questions = [],
  locale,
  disabled = false,
  onSelect,
  pathActions = [],
  onSelectPath,
  nextActions = [],
  onSelectAction,
}: {
  questions?: string[];
  locale: Locale;
  disabled?: boolean;
  onSelect: (question: string) => void;
  pathActions?: PathAction[];
  onSelectPath?: (action: PathAction) => void;
  nextActions?: NextAction[];
  onSelectAction?: (action: NextAction) => void;
}) {
  const actions = nextActions.filter(action => action.label?.trim() && (action.id === 'auth.login' || action.prompt?.trim())).slice(0, 2);
  // At most three buttons: actions first, then questions in the remaining slots.
  const uniqueQuestions = [...new Set(questions.map(question => question.trim()).filter(Boolean))].slice(0, 3 - actions.length);
  if (!uniqueQuestions.length && !pathActions.length && !actions.length) return null;

  return (
    <section className="response-section follow-up-section" aria-label={getCopy(locale).suggestedQuestions}>
      <div className="section-label"><span aria-hidden="true">✦</span>{getCopy(locale).suggestedQuestions}</div>
      <div className="follow-up-list">
        {actions.map((action, index) => (
          <button className="next-action" key={`next:${action.id}:${action.event_id ?? index}`} type="button" disabled={disabled} onClick={() => onSelectAction?.(action)}>
            <span dir="auto">{action.label}</span>{action.id === 'auth.login' ? <LogIn size={15} aria-hidden="true" /> : <ArrowUpRight size={15} aria-hidden="true" />}
          </button>
        ))}
        {uniqueQuestions.map(question => (
          <button className="follow-up-question" key={question} type="button" disabled={disabled} onClick={() => onSelect(question)}>
            <span dir="auto">{question}</span><ArrowUpRight size={15} aria-hidden="true" />
          </button>
        ))}
        {pathActions.map((action, index) => (
          <button className="next-action" key={`${action.id}:${action.event_id ?? index}`} type="button" disabled={disabled} onClick={() => onSelectPath?.(action)}>
            <span dir="auto">{action.label}</span><ArrowUpRight size={15} aria-hidden="true" />
          </button>
        ))}
      </div>
    </section>
  );
}
