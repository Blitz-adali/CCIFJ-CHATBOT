"""Run against the disposable fixture and Vite; requires optional Playwright.

API: CCIFJ_AUTH_SECRET=browser-test-secret-at-least-32-bytes-long PYTHONPATH=src uv run --with uvicorn uvicorn api_browser_fixture:app --app-dir tests --port 8800
Vite: CCIFJ_API_TARGET=http://127.0.0.1:8800 npm run dev -- --port 5188
Smoke: uv run --with playwright python tests/api_browser_smoke.py
"""
import os
from uuid import uuid4
from playwright.sync_api import sync_playwright, expect


def run():
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True, args=["--no-sandbox"])
        context = browser.new_context()
        page = context.new_page()
        run_id = uuid4().hex[:10]
        errors, sockets, websocket_results = [], [], []
        page.on("pageerror", lambda error: errors.append(str(error)))
        def opened(socket):
            sockets.append(socket.url)
            if "/api/v1/chat/ws" in socket.url:
                socket.on("framereceived", lambda frame: websocket_results.append(frame))
        page.on("websocket", opened)
        page.goto(os.getenv("CCIFJ_BROWSER_URL", "http://127.0.0.1:5188"))
        composer = page.locator("#widget-question")
        expect(composer).to_be_enabled()
        # A failed session preflight must leave an actionable retry, not uncertainty.
        page.route("**/api/v1/sessions", lambda route: route.abort(), times=1)
        composer.fill("first question")
        page.locator('.widget-composer button[type="submit"]').click()
        expect(page.locator(".widget-retry")).to_be_visible()
        expect(composer).to_be_enabled()
        expect(page.get_by_text("I have checked the outcome", exact=True)).to_have_count(0)
        page.locator(".widget-retry").click()
        expect(page.get_by_text("Answer: first question", exact=True)).to_be_visible()
        page.locator(".new-chat").click()
        composer.fill("hello")
        page.locator('.widget-composer button[type="submit"]').click()
        expect(page.get_by_text("Answer: hello", exact=True)).to_be_visible()
        assert any("/api/v1/chat/ws" in url for url in sockets), "Real WebSocket proxy was not used"
        assert any('"type":"result"' in frame for frame in websocket_results if isinstance(frame, str)), "Chat silently fell back instead of using the real WebSocket proxy"
        expect(page.locator(".action-card")).to_have_count(1)
        page.reload()
        expect(page.get_by_text("Answer: hello", exact=True)).to_be_visible()
        page.locator(".new-chat").click()
        composer.fill("subscribe")
        page.locator('.widget-composer button[type="submit"]').click()
        expect(page.locator(".approval-panel")).to_have_count(2)
        expect(composer).to_be_disabled()
        page.reload()
        expect(page.locator(".approval-panel")).to_have_count(2)
        page.locator(".approval-reject").first.click()
        expect(page.locator(".approval-panel")).to_have_count(1)
        # Login while an approval is pending must retain and claim the guest session.
        page.locator(".header-actions button.auth-button").click()
        page.locator(".auth-switch").click()
        page.get_by_label("Name", exact=True).fill("Browser User")
        page.get_by_label("Email", exact=True).fill(f"browser-{run_id}@example.com")
        expect(page.get_by_label("Password", exact=True)).to_have_attribute("maxlength", "1024")
        page.get_by_label("Password", exact=True).fill("browser-password-" + "x" * 200)
        page.locator(".auth-submit").click()
        expect(page.locator(".account-name")).to_have_text("Browser User")
        expect(page.locator(".approval-panel")).to_have_count(1)
        page.locator(".approval-reject").click()
        expect(page.locator(".approval-panel")).to_have_count(0)
        expect(composer).to_be_enabled()
        # Signed-in history survives logout/re-login, but is hidden while logged out.
        page.locator(".header-actions button.auth-button").click()
        expect(page.get_by_text("Review these subscriptions", exact=True)).to_have_count(0)
        page.locator(".header-actions button.auth-button").click()
        page.locator(".auth-switch").click()
        page.get_by_label("Email", exact=True).fill(f"browser-{run_id}@example.com")
        expect(page.get_by_label("Password", exact=True)).to_have_attribute("maxlength", "1024")
        page.get_by_label("Password", exact=True).fill("browser-password-" + "x" * 200)
        page.locator(".auth-submit").click()
        expect(page.get_by_text("Review these subscriptions", exact=True)).to_be_visible()
        # A selected language reaches the backend; answer and sources are restored.
        page.locator(".site-language select").select_option("fr")
        composer.fill("hello again")
        page.locator('.widget-composer button[type="submit"]').click()
        expect(page.get_by_text("Réponse française", exact=True)).to_be_visible()
        page.locator(".site-language select").select_option("en")
        # A running write is never presented as rolled back by the cancel control.
        composer.fill("cancel")
        page.locator('.widget-composer button[type="submit"]').click()
        page.locator(".cancel-send").click()
        expect(page.get_by_text("I have checked the outcome", exact=True)).to_be_visible(timeout=10000)
        expect(composer).to_be_disabled()
        expect(page.get_by_text("Request cancelled.", exact=True)).to_have_count(0)
        page.get_by_text("I have checked the outcome", exact=True).click()
        expect(composer).to_be_enabled()
        # Both tabs share authentication, but never retain the previous account view.
        other = page.context.new_page()
        other.goto(os.getenv("CCIFJ_BROWSER_URL", "http://127.0.0.1:5188"))
        expect(other.locator(".account-name")).to_have_text("Browser User")
        page.locator(".header-actions button.auth-button").click()
        expect(other.locator(".account-name")).to_have_count(0)
        expect(other.get_by_text("Review these subscriptions", exact=True)).to_have_count(0)
        page.locator(".header-actions button.auth-button").click()
        page.locator(".auth-switch").click()
        page.get_by_label("Name", exact=True).fill("Other User")
        page.get_by_label("Email", exact=True).fill(f"other-browser-{run_id}@example.com")
        page.get_by_label("Password", exact=True).fill("other-browser-password")
        page.locator(".auth-submit").click()
        expect(other.locator(".account-name")).to_have_text("Other User")
        expect(other.get_by_text("Review these subscriptions", exact=True)).to_have_count(0)
        other.close()
        assert not errors, errors
        browser.close()
        print("Browser smoke passed: real WebSocket proxy, restoration, guest approvals, login claim, account restoration, locale, uncertain cancellation, session preflight retry, long passwords, and cross-tab auth")


if __name__ == "__main__":
    run()
