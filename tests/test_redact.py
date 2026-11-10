import unittest

from concierge.agents.redact import clean_button_text, scrub


class ScrubTest(unittest.TestCase):
    def test_leaks_seen_in_real_conversations(self):
        self.assertEqual(scrub("I'd like to book event ID 24: Christmas Dinner at Meiji Kinenkan."),
                         "I'd like to book Christmas Dinner at Meiji Kinenkan.")
        self.assertEqual(scrub("## [ID 82] Voyage au Coeur des Alpes by evian\n\n- **Category:** Business event"),
                         "## Voyage au Coeur des Alpes by evian\n\n- **Category:** Business event")
        self.assertEqual(scrub("- **Price:** unknown\n- **Event ID:** 23\n\n### GALA TOKYO 2026"),
                         "- **Price:** unknown\n\n### GALA TOKYO 2026")

    def test_other_forms(self):
        self.assertEqual(scrub("Can I book the GALA TOKYO 2026 (ID 23)?"), "Can I book the GALA TOKYO 2026?")
        self.assertEqual(scrub("Tell me more about ID: 65"), "Tell me more about")
        self.assertEqual(scrub("イベントID 24「Christmas Dinner」を予約したいです。"), "「Christmas Dinner」を予約したいです。")
        self.assertEqual(scrub("Je souhaite réserver l’événement 24 : Christmas Dinner."),
                         "Je souhaite réserver l’événement Christmas Dinner.")
        self.assertEqual(scrub("Answer.\n\n[Event IDs from the events database this turn: ID 24 = Christmas Dinner]"), "Answer.")

    def test_user_references_and_normal_text_stay(self):
        for text in ("Your booking reference is BK-2026-000123.", "Reference IR-2026-000012 was stored.",
                     "Space booking CCIFJ-SPC-00042 is confirmed.", "Bring your ID card.",
                     "It starts at 18:30 on 10 November 2026 for 2 people."):
            self.assertEqual(scrub(text), text)

    def test_buttons_with_code_names_are_dropped(self):
        self.assertEqual(clean_button_text("Can you run search_member_directory for wine?"), "")
        self.assertEqual(clean_button_text("Show my user_context"), "")
        self.assertEqual(clean_button_text("ID 24"), "")
        self.assertEqual(clean_button_text("How do I book event ID 24?"), "")
        self.assertEqual(clean_button_text("Book event ID 24: GALA TOKYO 2026"), "Book GALA TOKYO 2026")
        self.assertEqual(clean_button_text("What are the membership fees?"), "What are the membership fees?")


if __name__ == '__main__':
    unittest.main()
