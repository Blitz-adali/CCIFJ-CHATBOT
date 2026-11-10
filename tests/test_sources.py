"""Sources and citations, and knowledge search that keeps working when a dependency fails."""
import os
import unittest
from unittest.mock import patch

os.environ.setdefault("CCIFJ_AUTH_SECRET", "test-secret-for-contract-checks-only-123456789")
os.environ.setdefault("model_type", "qwen")
os.environ.setdefault("model_name", "test-model")
os.environ.setdefault("router_model_name", "test-model")
os.environ.setdefault("COHERE_API_KEY", "test-cohere-key")

from concierge.agents import sources
from concierge.rag.src import rag_search

PAGE = "https://www.ccifj.or.jp/en/membership/how-to-become-a-member.html"
TOOL_OUTPUT = (
    "# Set up and Business Center\n\nurl: https://www.ccifj.or.jp/en/commercial-support/set-up-and-business-center.html\n"
    "Permanent domiciliation 125,000 yen\n" + "=" * 80 + "\n"
    "# Media Kit FJE 2026\n\ndocument: Media Kit FJE 2026\nMore than 30,000 readers\n"
    f"Source: [How to become a member]({PAGE})"
)


class SourceTrackingTest(unittest.TestCase):
    def setUp(self):
        self.retrieved = sources.sources_in_tool_output(TOOL_OUTPUT)

    def test_tool_output_names_pages_documents_and_records(self):
        self.assertEqual([s["title"] for s in self.retrieved],
                         ["Set up and Business Center", "Media Kit FJE 2026", "How to become a member"])
        self.assertIsNone(self.retrieved[1]["url"])
        self.assertEqual(self.retrieved[2]["url"], PAGE)

    def test_reducer_adds_once_and_none_starts_a_new_turn(self):
        merged = sources.merge_sources(self.retrieved, [{"title": "Again", "url": PAGE + "/"}, {"title": "New", "url": "https://www.ccifj.or.jp/en/events.html"}])
        self.assertEqual(len(merged), 4)
        self.assertEqual(sources.merge_sources(merged, None), [])

    def test_tags_become_numbers_and_unknown_sources_are_dropped(self):
        findings = {"general": (
            f"Active membership is ¥295,000 (source: {PAGE}). "
            "The magazine has 30,000 readers (source: document “Media Kit FJE 2026”). "
            "A made-up fact (source: https://example.com/invented)."
        )}
        rewritten, numbered = sources.number_tags(findings, self.retrieved)
        self.assertEqual([s["title"] for s in numbered], ["How to become a member", "Media Kit FJE 2026"])
        self.assertIn("¥295,000 [1].", rewritten["general"])
        self.assertIn("readers [2].", rewritten["general"])
        self.assertNotIn("example.com", rewritten["general"])
        self.assertNotIn("source:", rewritten["general"])
        self.assertIn("[2] Media Kit FJE 2026 (CCIFJ document)", sources.sources_note(numbered))

    def test_answer_keeps_only_valid_markers_renumbered_in_order(self):
        _, numbered = sources.number_tags({"g": f"a (source: {PAGE}) b (source: document Media Kit FJE 2026)"}, self.retrieved)
        answer = "Bonjour ! Readers: 30,000 [2]. Fee: ¥295,000 [1] [1]. Invented [7].\n```email\nSubject: Hi [1]\n```"
        text, cited = sources.apply_citations(answer, numbered)
        self.assertEqual([s["title"] for s in cited], ["Media Kit FJE 2026", "How to become a member"])
        self.assertIn("Bonjour ! Readers: 30,000 [1]. Fee: ¥295,000 [2]. Invented.", text)
        self.assertIn("Subject: Hi\n", text)  # no markers inside a draft
        self.assertEqual(sources.strip_markers("Fee ¥295,000 [2]. See [form](https://a.example)"), "Fee ¥295,000. See [form](https://a.example)")

    def test_official_sources_are_told_apart_from_public_web_pages(self):
        self.assertTrue(sources.is_official(PAGE))
        self.assertTrue(sources.is_official(None))  # an approved document
        self.assertFalse(sources.is_official("https://www.jetro.go.jp/en/"))
        self.assertFalse(sources.is_official("https://ccifj.or.jp.evil.example/"))

    def test_api_lists_cited_sources_first(self):
        from concierge.api.agent_backend import answer_sources
        result = {"sources": [{"title": "Media Kit FJE 2026", "url": None}, {"title": "How to become a member", "url": PAGE}]}
        linked = [{"title": "Member page", "url": PAGE + "#fees"}, {"title": "JETRO", "url": "https://www.jetro.go.jp/en/"}]
        listed = answer_sources(result, linked)
        self.assertEqual([(s["title"], s["cited"], s["official"]) for s in listed],
                         [("Media Kit FJE 2026", True, True), ("How to become a member", True, True), ("JETRO", False, False)])


class _RateLimited(Exception):
    status_code = 429


class SearchResilienceTest(unittest.TestCase):
    def setUp(self):
        rag_search._rerank_calls.clear()
        rag_search._rerank_paused_until = 0.0
        rag_search._search_cache.clear()
        self.docs = [{"id": n, "text": f"passage {n}"} for n in range(10)]

    def test_rate_limit_falls_back_to_the_fused_ranking_and_pauses(self):
        with patch.object(rag_search, "COHERE_API_KEY", "key"), patch.object(rag_search, "rerank", side_effect=_RateLimited()) as rerank:
            docs, reranked = rag_search.rerank_or_fused("q", self.docs, 5)
            self.assertEqual(([d["id"] for d in docs], reranked), ([0, 1, 2, 3, 4], False))
            rag_search.rerank_or_fused("q2", self.docs, 5)
            self.assertEqual(rerank.call_count, 1)  # paused: no second call

    def test_stays_under_the_per_minute_budget(self):
        with patch.object(rag_search, "COHERE_API_KEY", "key"), patch.object(rag_search, "RERANK_PER_MINUTE", 2), \
                patch.object(rag_search, "rerank", return_value=self.docs[:5]) as rerank:
            outcomes = [rag_search.rerank_or_fused(f"q{n}", self.docs, 5)[1] for n in range(4)]
        self.assertEqual(outcomes, [True, True, False, False])
        self.assertEqual(rerank.call_count, 2)

    def test_missing_key_never_raises(self):
        with patch.object(rag_search, "COHERE_API_KEY", None):
            self.assertEqual(rag_search.rerank_or_fused("q", self.docs, 3), (self.docs[:3], False))

    def test_search_tool_reports_an_outage_instead_of_failing_the_answer(self):
        from concierge.agents import tools
        with patch.object(tools, "_get_embed_model", side_effect=RuntimeError("GPU unavailable")):
            result = tools.search_vectordb.invoke({"query": "membership fees"})
        self.assertEqual(result, tools.SEARCH_UNAVAILABLE)
        self.assertIn("Do not answer this part from memory", result)

    def test_record_tools_name_their_official_page(self):
        from concierge.agents import tools
        contact = tools.get_ccifj_contact.invoke({"topic": "membership"})
        self.assertEqual(sources.sources_in_tool_output(contact),
                         [{"title": "Membership: contact us", "url": "https://www.ccifj.or.jp/en/membership/contact-us.html"}])


class OdooConnectorTest(unittest.TestCase):
    REQUEST = {
        "reference": "SR-2026-000007", "request_type": "staff_email", "category": "business_support",
        "subject": "Market study for a cosmetics brand", "message": "Hello, we would like a quote.",
        "sender_name": "Jane Doe", "sender_email": "jane@example.com",
        "recipient_emails": ["appui.entreprises@ccifj.or.jp"],
    }
    ODOO = {"ODOO_URL": "https://odoo.example.test", "ODOO_DB": "ccifj", "ODOO_USER": "bot", "ODOO_API_KEY": "key"}

    def test_off_until_configured(self):
        from concierge.integrations import odoo
        with patch.dict(os.environ, {name: "" for name in self.ODOO}):
            self.assertFalse(odoo.enabled())
            self.assertIsNone(odoo.push_staff_request(self.REQUEST))

    def test_creates_a_lead_with_the_request_and_its_reference(self):
        from concierge.integrations import odoo
        calls = []

        class FakeServer:
            def __init__(self, path):
                self.path = path

            def authenticate(self, db, user, key, context):
                calls.append(("authenticate", db, user))
                return 7

            def execute_kw(self, db, uid, key, model, method, args):
                calls.append((model, method, uid, args[0]))
                return 42

        with patch.dict(os.environ, self.ODOO), patch.object(odoo, "_proxy", side_effect=lambda url, path, timeout: FakeServer(path)):
            self.assertEqual(odoo.push_staff_request(self.REQUEST), 42)
        self.assertEqual(calls[0], ("authenticate", "ccifj", "bot"))
        model, method, uid, lead = calls[1]
        self.assertEqual((model, method, uid), ("crm.lead", "create", 7))
        self.assertEqual(lead["email_from"], "jane@example.com")
        self.assertIn("SR-2026-000007", lead["name"])
        self.assertIn("appui.entreprises@ccifj.or.jp", lead["description"])

    def test_an_odoo_outage_is_logged_and_never_raised(self):
        from concierge.integrations import odoo
        with patch.dict(os.environ, self.ODOO), patch.object(odoo, "_proxy", side_effect=OSError("down")), \
                self.assertLogs("concierge.integrations.odoo", level="ERROR"):
            self.assertIsNone(odoo.push_staff_request(self.REQUEST))


if __name__ == "__main__":
    unittest.main()
