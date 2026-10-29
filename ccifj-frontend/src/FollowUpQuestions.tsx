import { ArrowUpRight } from 'lucide-react';
import { getCopy } from './i18n';
import type { Locale } from './types';

export function FollowUpQuestions({
  questions = [],
  locale,
  disabled = false,
  onSelect,
}: {
  questions?: string[];
  locale: Locale;
  disabled?: boolean;
  onSelect: (question: string) => void;
}) {
  const uniqueQuestions = [...new Set(questions.map(question => question.trim()).filter(Boolean))].slice(0, 3);
  if (!uniqueQuestions.length) return null;

  return (
    <section className="response-section follow-up-section" aria-label={getCopy(locale).suggestedQuestions}>
      <div className="section-label"><span aria-hidden="true">✦</span>{getCopy(locale).suggestedQuestions}</div>
      <div className="follow-up-list">
        {uniqueQuestions.map(question => (
          <button key={question} type="button" disabled={disabled} onClick={() => onSelect(question)}>
            <span>{question}</span><ArrowUpRight size={15} aria-hidden="true" />
          </button>
        ))}
      </div>
    </section>
  );
}
