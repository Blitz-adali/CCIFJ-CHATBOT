# 02 · Suggested-question buttons look like broken cards

**Area:** Frontend (CSS) · **Priority:** Low · **Related task:** 2

## Problem
Under an answer, the suggested-question options show as bare blue text with no card around them, so they look like the card failed to render. Next-action buttons (dark blue cards) look correct.

## Cause
Commit `73e5590` changed the styling on purpose to make questions look like links (`ccifj-frontend/src/widget.css:120-121`):

```css
.follow-up-list button.follow-up-question { padding: 8px 4px; border-color: transparent; background: transparent; color: #00669b; }
.follow-up-list button.follow-up-question:hover:not(:disabled) { border-color: transparent; background: transparent; text-decoration: underline; ... }
```

## Fix
Give questions a light outlined card that is still clearly different from the filled dark-blue `.next-action` buttons, for example:

```css
.follow-up-list button.follow-up-question { padding: 10px 14px; border: 1px solid #d8dfde; background: #faf8f4; color: #174965; }
.follow-up-list button.follow-up-question:hover:not(:disabled) { border-color: #8caaba; background: #fffdf9; text-decoration: none; }
```

Check with Sara before changing it, since the link style was a deliberate choice.

## Acceptance
- Suggested questions show as visible cards.
- Next actions (filled) and questions (outlined) are easy to tell apart.
- RTL and Japanese text still wrap correctly (`dir="auto"` is already set).
