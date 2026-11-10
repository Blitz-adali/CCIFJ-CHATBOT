"""User-facing progress labels derived from agent graph events.

Only fixed, localized labels are exposed; internal tool arguments, outputs,
and model text are never streamed through progress events.
"""

from __future__ import annotations

_NODE_LABELS = {
    "orchestrator_node": {
        "en": "The CCIFJ Concierge is reviewing your request…", "fr": "Le concierge CCIFJ examine votre demande…", "ja": "CCIFJコンシェルジュがご質問を確認しています…",
    },
    "events_node": {
        "en": "Our Events Agent is finding relevant events for you…", "fr": "Notre agent spécialisé en événements recherche les événements qui vous correspondent…", "ja": "イベント担当エージェントが、ご希望に合うイベントを探しています…",
    },
    "membership_node": {
        "en": "Our Membership Agent is checking the details for you…", "fr": "Notre agent spécialisé en adhésion vérifie les informations pour vous…", "ja": "会員担当エージェントが、詳しい情報を確認しています…",
    },
    "general_node": {
        "en": "Our Business Support Agent is looking through CCIFJ resources…", "fr": "Notre agent Appui aux entreprises consulte les ressources CCIFJ…", "ja": "ビジネスサポート担当エージェントが、CCIFJの情報を確認しています…",
    },
    "matching_node": {
        "en": "Our Member Matching Agent is searching the member directory…", "fr": "Notre agent de mise en relation recherche dans l’annuaire des membres…", "ja": "会員マッチング担当エージェントが、会員ディレクトリを検索しています…",
    },
    "engagement_node": {
        "en": "Our Member Engagement Agent is reviewing member activity…", "fr": "Notre agent d’engagement des membres analyse l’activité des membres…", "ja": "会員エンゲージメント担当エージェントが、会員の活動状況を確認しています…",
    },
    "collector_node": {
        "en": "The CCIFJ Concierge is putting your answer together…", "fr": "Le concierge CCIFJ prépare votre réponse…", "ja": "CCIFJコンシェルジュが回答をまとめています…",
    },
}

_WORKER_NAMES = {
    "events_node": {"en": "Events", "fr": "Événements", "ja": "イベント"},
    "membership_node": {"en": "Membership", "fr": "Adhésion", "ja": "会員"},
    "general_node": {"en": "Business Support", "fr": "Appui aux entreprises", "ja": "ビジネスサポート"},
    "matching_node": {"en": "Member Matching", "fr": "Mise en relation", "ja": "会員マッチング"},
    "engagement_node": {"en": "Member Engagement", "fr": "Engagement des membres", "ja": "会員エンゲージメント"},
}

_TOOL_LABELS = {
    "list_available_events": {"en": "Checking upcoming CCIFJ events…", "fr": "Consultation des prochains événements de la CCIFJ…", "ja": "CCIFJの今後のイベントを確認しています…"},
    "get_event_details": {"en": "Looking up the event details…", "fr": "Recherche des détails de l’événement…", "ja": "イベントの詳細を確認しています…"},
    "get_membership_tier_info": {"en": "Checking CCIFJ membership options…", "fr": "Consultation des formules d’adhésion de la CCIFJ…", "ja": "CCIFJの会員プランを確認しています…"},
    "search_member_directory": {"en": "Searching our member company directory…", "fr": "Recherche dans notre annuaire des entreprises membres…", "ja": "会員企業ディレクトリを検索しています…"},
    "list_available_industries_and_regions": {"en": "Checking the available industries and regions…", "fr": "Vérification des secteurs et régions disponibles…", "ja": "検索可能な業種と地域を確認しています…"},
    "search_vectordb": {"en": "Looking through CCIFJ information…", "fr": "Recherche dans les informations de la CCIFJ…", "ja": "CCIFJの情報を確認しています…"},
    "get_ccifj_contact": {"en": "Finding the right CCIFJ contact…", "fr": "Recherche du bon contact à la CCIFJ…", "ja": "適切なCCIFJの問い合わせ先を探しています…"},
    "get_official_form": {"en": "Finding the official CCIFJ form…", "fr": "Recherche du formulaire officiel de la CCIFJ…", "ja": "CCIFJの公式フォームを探しています…"},
    "book_event_ticket": {"en": "Preparing the event booking for your review…", "fr": "Préparation de la réservation pour votre validation…", "ja": "イベント予約の確認準備をしています…"},
    "book_space": {"en": "Preparing the space booking for your review…", "fr": "Préparation de la réservation d’espace pour votre validation…", "ja": "スペース予約の確認準備をしています…"},
    "find_member_matches": {"en": "Matching your need with member companies…", "fr": "Recherche des membres correspondant à votre besoin…", "ja": "ご要望に合う会員企業を探しています…"},
    "get_member_record": {"en": "Reading the member's directory record…", "fr": "Lecture de la fiche du membre dans l’annuaire…", "ja": "会員企業の登録情報を確認しています…"},
    "list_members_to_follow_up": {"en": "Checking which members need a follow-up…", "fr": "Vérification des membres à relancer…", "ja": "フォローが必要な会員を確認しています…"},
    "get_member_engagement": {"en": "Reading the member's profile and activity…", "fr": "Lecture du profil et de l’activité du membre…", "ja": "会員のプロフィールと活動状況を確認しています…"},
    "save_introduction_request": {"en": "Preparing the introduction for your review…", "fr": "Préparation de la mise en relation pour votre validation…", "ja": "ご紹介内容の確認準備をしています…"},
    "save_member_followup": {"en": "Preparing the follow-up for your review…", "fr": "Préparation du message de suivi pour votre validation…", "ja": "フォローアップの確認準備をしています…"},
    "subscribe_newsletter": {"en": "Preparing your newsletter subscription for review…", "fr": "Préparation de votre inscription à la newsletter pour validation…", "ja": "ニュースレター登録の確認準備をしています…"},
}

_WORKERS = ("events_node", "membership_node", "general_node", "matching_node", "engagement_node")


def initial_label(locale: str) -> str:
    return _NODE_LABELS["orchestrator_node"][locale]


def _parallel_label(nodes: set[str], locale: str) -> str | None:
    active = [name for name in _WORKERS if name in nodes]
    if len(active) < 2:
        return None
    names = [_WORKER_NAMES[node][locale] for node in active]
    if locale == "en":
        joined = ", ".join(names[:-1]) + f" and {names[-1]}" if len(names) > 2 else " and ".join(names)
        return f"Our {joined} Agents are working in parallel…"
    if locale == "fr":
        joined = ", ".join(names[:-1]) + f" et {names[-1]}" if len(names) > 2 else " et ".join(names)
        return f"Nos agents {joined} travaillent en parallèle…"
    return f"{'・'.join(names)}の各担当エージェントが並行して対応しています…"


class ProgressTracker:
    """Turn LangGraph ``astream_events`` (v2) events into progress messages."""

    def __init__(self, locale: str):
        self.locale = locale
        self.current = initial_label(locale)
        self.workers: dict[str, set[str]] = {}
        self.tools: dict[str, str] = {}

    def _messages(self) -> list[str]:
        tool_messages = list(dict.fromkeys(self.tools.values()))
        parallel = _parallel_label(set(self.workers), self.locale)
        if parallel:
            return [*tool_messages, parallel]
        if tool_messages:
            return tool_messages
        if self.workers:
            label = _NODE_LABELS.get(next(iter(self.workers)), {}).get(self.locale)
            return [label] if label else []
        return []

    def update(self, event: dict) -> list[str] | None:
        """Return new progress messages to show, or None when nothing changed."""
        node = (event.get("metadata") or {}).get("langgraph_node")
        name = event.get("event")
        run_id = str(event.get("run_id", ""))
        if name == "on_tool_start":
            label = _TOOL_LABELS.get(str(event.get("name", "")), {}).get(self.locale)
            if not label:
                return None
            self.tools[run_id] = label
            return self._emit(self._messages())
        if name in {"on_tool_end", "on_tool_error"}:
            if self.tools.pop(run_id, None) is None:
                return None
            return self._emit(self._messages())
        if node in _WORKERS:
            if name == "on_chain_start":
                self.workers.setdefault(node, set()).add(run_id)
            elif name in {"on_chain_end", "on_chain_error"}:
                self.workers.get(node, set()).discard(run_id)
                if not self.workers.get(node):
                    self.workers.pop(node, None)
            else:
                return None
            messages = self._messages()
            if messages and (messages[0] != self.current or len(messages) > 1):
                return self._emit(messages)
            return None
        if name == "on_chain_start":
            label = _NODE_LABELS.get(node, {}).get(self.locale)
            if label and label != self.current:
                return self._emit([label])
        return None

    def _emit(self, messages: list[str]) -> list[str] | None:
        if not messages:
            return None
        self.current = messages[0]
        return messages
