"""Remove internal identifiers from text the user will see.

The prompts already forbid them, but a model sometimes copies tool output such as
"[ID 24] Christmas Dinner" into an answer, or writes "Book event ID 24" as a
suggested question (which the user then sends as their own message). This is the
deterministic last line: database IDs, the collector's metadata line and code
names of tools or state never reach the user. Booking and request references the
user keeps (BK-..., IR-2026-000012, CCIFJ-SPC-...) are not IDs and stay.
"""
import re

_ID = r"(?:ID|Id|id|ＩＤ)"
_NUMBER = r"\d{1,9}"

_PATTERNS = [
    # Collector metadata copied verbatim.
    (re.compile(r"\n*\[Event IDs from the events database this turn:[^\]]*\]", re.S), ""),
    # Markdown field lines: "- **Event ID:** 23".
    (re.compile(r"^[ \t]*(?:[-*•][ \t]*)?\**(?:Event |Member |Booking |User )?" + _ID + r"\**[ \t]*:?[ \t]*\**[ \t]*:?[ \t]*" + _NUMBER + r"[ \t]*$\n?", re.M), ""),
    # Tool-line prefixes: "[ID 24] Title", "[ID: 24]".
    (re.compile(r"\[" + _ID + r"[\s:#]*" + _NUMBER + r"\]\s*"), ""),
    # Parenthesised: "(ID 24)", "(event ID: 24)", "（ID 24）".
    (re.compile(r"\s*[(（]\s*(?:event|member|user|booking|イベント)?\s*" + _ID + r"[\s:#＃=]*" + _NUMBER + r"\s*[)）]", re.I), ""),
    # "event ID 24: Title" -> "Title"; "event_id=24" -> "".
    (re.compile(r"\b(?:event|member|user|contact|booking|speaker)[ _-]?" + _ID + r"\s*[:#=]?\s*" + _NUMBER + r"\s*[:：\-–]?\s*", re.I), ""),
    (re.compile(r"(?:イベント|会員|ユーザー)\s*" + _ID + r"\s*[:：#]?\s*" + _NUMBER + r"\s*[:：]?\s*"), ""),
    (re.compile(r"\b(?:l[’']\s*)?(?:événement|evenement)\s+(?:n[°o]\s*)?" + _NUMBER + r"\s*:\s*", re.I), "l’événement "),
    # A bare "ID 24" / "ID: 24" / "ID #24".
    (re.compile(r"(?<![\w-])(?:ID|ＩＤ)\s*[:#]?\s*" + _NUMBER + r"(?![\w-])\s*[:：]?\s*"), ""),
]

# Code names that mean nothing to a user: tool and state names.
_INTERNAL_NAME = re.compile(
    r"\b(?:user_context|user_id|member_id|event_id|access_level|worker_results|next_actions|"
    r"suggested_questions|search_member_directory|find_matching_members|request_introduction|"
    r"recommend_events|list_available_events|get_event_details|book_event_ticket|search_vectordb|"
    r"submit_staff_request|get_ccifj_contact|get_official_form|get_membership_tier_info|"
    r"list_available_industries_and_regions|return_to_main_menu)\b"
    r"|SPECIALIST FINDINGS|\[Focus Task"
)


_ENDS_WITH_ID = re.compile(r"(?:ID|ＩＤ|\bid)\s*[:#＃]?\s*" + _NUMBER + r"\s*[)）]?\s*[?.!？。]*\s*$")


def scrub(text: str) -> str:
    """`text` without database IDs or the collector's metadata line."""
    if not isinstance(text, str) or not text:
        return text
    original = text
    for pattern, replacement in _PATTERNS:
        text = pattern.sub(replacement, text)
    if text == original:
        return text.strip()  # untouched text keeps its own typography (French "entreprise ?")
    # Tidy what a removal can leave behind: "book  the", "GALA ?", empty bullets.
    text = re.sub(r"[ \t]{2,}", " ", text)
    text = re.sub(r"[ \t]+([,.?!])(?=\s|$)", r"\1", text)
    text = re.sub(r"^[ \t]*[-*•][ \t]*$\n?", "", text, flags=re.M)
    return text.strip()


def has_internal_name(text: str) -> bool:
    return bool(_INTERNAL_NAME.search(text or ""))


def clean_button_text(text: str) -> str:
    """A suggestion or button text, or "" when it is not fit to show at all."""
    text = " ".join(str(text or "").split())
    if _ENDS_WITH_ID.search(text):
        return ""  # the ID was the only thing naming it: "How do I book event ID 24?"
    text = scrub(text)
    if not text or has_internal_name(text) or not re.search(r"[^\W\d_]", text):
        return ""
    return text
