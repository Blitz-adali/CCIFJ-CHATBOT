"""Real-browser smoke test of ccifj-frontend against the disposable fixture API.

API: CCIFJ_STAFF_USER_IDS=1 CCIFJ_AUTH_SECRET=browser-test-secret-at-least-32-bytes-long PYTHONPATH=src uv run --with uvicorn uvicorn api_browser_fixture:app --app-dir tests --port 8800
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

        # The welcome screen offers starter prompts.
        expect(page.locator(".widget-suggestions button")).to_have_count(6)
        expect(page.get_by_role("button", name="Book a meeting room")).to_be_visible()

        # Language selection updates the interface and survives a refresh.
        page.get_by_role('combobox', name='Language').select_option('fr')
        expect(page.get_by_role('heading', name='Bonjour ! Comment puis-je vous aider ?')).to_be_visible()
        page.reload()
        expect(page.get_by_role('combobox', name='Langue')).to_have_value('fr')
        page.get_by_role('combobox', name='Langue').select_option('en')

        # Database event cards work with no real model and on a narrow viewport.
        page.get_by_role('button', name='Upcoming events').click()
        expect(page.locator('.event-card')).to_have_count(1)
        expect(page.locator('.event-card')).to_contain_text('Browser Business Summit')
        expect(page.locator('.event-card')).to_contain_text('Registration open')
        page.set_viewport_size({'width':390,'height':844})
        assert page.evaluate('document.documentElement.scrollWidth <= window.innerWidth')
        page.screenshot(path='/tmp/ccifj-events-mobile.png', full_page=True)
        page.set_viewport_size({'width':1280,'height':900})
        page.locator('.event-card').get_by_role('button', name='Details', exact=True).click()
        expect(page.get_by_text('Answer: Tell me more about the event "Browser Business Summit".', exact=True)).to_be_visible()
        page.locator('.new-chat-rail-button').click()

        # A plain agent turn: the backend `reply` is rendered with follow-ups and links.
        ask("hello")
        expect(page.get_by_text("Answer: hello", exact=True)).to_be_visible()
        expect(page.get_by_role("button", name="What is CCIFJ?")).to_be_visible()
        expect(page.locator(".next-action-link")).to_have_count(1)

        # A starter prompt is sent to the agent as an ordinary message.
        page.locator(".new-chat-rail-button").click()
        page.get_by_role("button", name="Book a meeting room").click()
        expect(page.get_by_text("Answer: Is a CCIFJ meeting room free next Tuesday from 2 pm to 5 pm for 10 people?", exact=True)).to_be_visible()

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

        # Only the configured operator account can open the staff inbox.
        page.get_by_label('Email',exact=True).fill('operator@demo.ccifj.test')
        page.get_by_label('Password',exact=True).fill('browser-operator-password')
        page.locator('.auth-submit').click()
        page.get_by_role('button', name='Staff inbox',exact=True).click()
        expect(page.get_by_role('heading',name='Approved market entry inquiry')).to_be_visible()
        page.get_by_text('Read message',exact=True).click()
        expect(page.get_by_text('Please help us enter the Japanese market.',exact=True)).to_be_visible()
        page.screenshot(path='/tmp/ccifj-staff-inbox.png',full_page=True)
        page.get_by_role('button',name='Chat',exact=True).click()
        expect(composer).to_be_visible()

        assert not errors, errors
        browser.close()


if __name__ == "__main__":
    run()
    print("browser smoke test passed")
