"""Real-browser smoke test of ccifj-frontend against the disposable fixture API.

API: CCIFJ_AUTH_SECRET=browser-test-secret-at-least-32-bytes-long PYTHONPATH=src uv run --with uvicorn uvicorn api_browser_fixture:app --app-dir tests --port 8800
Vite: CCIFJ_API_TARGET=http://127.0.0.1:8800 npm run dev -- --port 5188
Smoke: uv run --with playwright python tests/api_browser_smoke.py

Set CCIFJ_CHROMIUM to use an installed Chromium instead of Playwright's download.
"""
import os
from uuid import uuid4

from playwright.sync_api import expect, sync_playwright


def run():
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(
            headless=True, args=["--no-sandbox"], executable_path=os.getenv("CCIFJ_CHROMIUM") or None,
        )
        page = browser.new_context(locale="en-US").new_page()
        errors = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.goto(os.getenv("CCIFJ_BROWSER_URL", "http://127.0.0.1:5188"))
        composer = page.locator("#widget-question")
        send = page.locator('.widget-composer button[type="submit"]')

        def ask(text):
            composer.fill(text)
            send.click()

        # Localized starter paths come from /suggestions.
        expect(page.get_by_role("button", name="Check space availability and book")).to_be_visible()

        # A plain agent turn: the backend `reply` is rendered with follow-ups and links.
        ask("hello")
        expect(page.get_by_text("Answer: hello", exact=True)).to_be_visible()
        expect(page.get_by_role("button", name="What is CCIFJ?")).to_be_visible()
        expect(page.locator(".next-action-link")).to_have_count(1)

        # Guided space path: the follow-up free-text message continues the active step.
        page.locator(".new-chat-rail-button").click()
        page.get_by_role("button", name="Check space availability and book").click()
        expect(page.get_by_role("heading", name="Check space availability")).to_be_visible()
        ask("2026-11-12 09:00 to 2026-11-12 12:00")
        expect(page.get_by_role("heading", name="Space availability")).to_be_visible()

        # Cancelling leaves an uncertain outcome that must be acknowledged before continuing.
        ask("cancel")
        page.locator(".cancel-send").click()
        expect(page.get_by_text("Request cancelled.", exact=True)).to_be_visible()
        expect(page.locator(".outcome-check")).to_be_visible()
        expect(send).to_be_disabled()
        page.get_by_role("button", name="I’ve checked — continue").click()
        expect(page.locator(".outcome-check")).to_have_count(0)
        ask("after check")
        expect(page.get_by_text("Answer: after check", exact=True)).to_be_visible()

        # Accounts and approvals use the session approvals route.
        page.locator(".signin-button").click()
        page.get_by_role("button", name="New to CCIFJ? Create an account").click()
        page.get_by_label("Name").fill("Browser Test")
        page.get_by_label("Email").fill(f"browser-{uuid4().hex[:10]}@example.com")
        page.get_by_label("Password").fill("browser-password")
        page.locator(".auth-submit").click()
        expect(page.locator(".account-name")).to_have_text("Browser Test")
        ask("subscribe")
        expect(page.locator(".approval-panel")).to_have_count(2)
        page.locator(".approval-accept").first.click()
        expect(page.get_by_text("Decision recorded", exact=True)).to_be_visible()
        expect(page.locator(".approval-panel")).to_have_count(1)

        # A rejected saved login signs the user out and reopens the login dialog.
        page.evaluate("""() => {
            const saved = JSON.parse(localStorage.getItem('ccifj-auth-session'));
            localStorage.setItem('ccifj-auth-session', JSON.stringify({...saved, access_token: 'revoked'}));
        }""")
        ask("hello again")
        expect(page.locator(".auth-dialog")).to_be_visible()
        expect(page.locator(".signin-button")).to_be_visible()

        assert not errors, errors
        browser.close()


if __name__ == "__main__":
    run()
    print("browser smoke test passed")
