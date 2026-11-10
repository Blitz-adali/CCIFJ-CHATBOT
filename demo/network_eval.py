"""Member matching and member engagement, tested through the chat as real account types.

    uv run python -m concierge.demo_engagement     # once: creates the seven demo members
    python3 demo/network_eval.py                   # then run this (the concierge must be running)

Signs in as demo members (password read from var/demo-accounts.txt), as a new non-member
account, and as a guest, because what the matching agent may show depends on the account.
It does not sign in as the two "inactive" personas, so their inactivity story stays intact.
One introduction request is approved and stored. Answers go to demo/runs/<date-time>-network/.
"""
import json
import re
import sys
import time
import urllib.error
import urllib.request
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def http(method, url, body=None, token=None, timeout=240):
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    request = urllib.request.Request(url, data=json.dumps(body).encode() if body is not None else None, headers=headers, method=method)
    started = time.time()
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read()
            return response.status, (json.loads(raw) if raw else {}), round(time.time() - started, 1)
    except urllib.error.HTTPError as error:
        raw = error.read().decode(errors="replace")
        try:
            return error.code, json.loads(raw), round(time.time() - started, 1)
        except ValueError:
            return error.code, {"detail": raw[:300]}, round(time.time() - started, 1)
    except Exception as error:
        return 0, {"detail": repr(error)}, round(time.time() - started, 1)


API = sys.argv[1].rstrip("/") if len(sys.argv) > 1 else next(
    (f"http://127.0.0.1:{port}" for port in (8001, 8000, 8002)
     if http("GET", f"http://127.0.0.1:{port}/status", timeout=4)[1].get("agent_ready")), "")
if not API:
    sys.exit("The concierge is not running. Start it with ./start.sh and wait 20 seconds.")
accounts = ROOT / "var" / "demo-accounts.txt"
if not accounts.exists():
    sys.exit("Demo members are missing. Run: uv run python -m concierge.demo_engagement")
PASSWORD = re.search(r"^password for all: (\S+)", accounts.read_text(encoding="utf-8"), re.M).group(1)


def login(email):
    status, data, _ = http("POST", API + "/api/v1/auth/login", {"email": email, "password": PASSWORD})
    return data.get("access_token") if status == 200 else None


class Chat:
    def __init__(self, name, account, locale="en", token=None):
        self.name, self.account, self.locale, self.token, self.session, self.turns = name, account, locale, token, None, []

    def record(self, said, status, data, seconds):
        self.turns.append({
            "said": said, "status": status, "seconds": seconds,
            "reply": data.get("reply") if status == 200 else json.dumps(data)[:300],
            "intent": (data.get("intent") or {}).get("code") if status == 200 else None,
            "approvals": [{"name": a.get("name"), "args": a.get("args")} for item in data.get("pending_approvals", [])
                          for a in item.get("action_requests", [])] if status == 200 else [],
            "sources": [s.get("title") for s in data.get("sources", [])] if status == 200 else [],
            "next_actions": [a.get("id") for a in data.get("next_actions", [])] if status == 200 else [],
        })
        print(f"{self.name} [{self.account}] | {said[:56]} -> {status} {seconds}s{' APPROVAL' if data.get('pending_approvals') else ''}", flush=True)
        return data

    def say(self, message):
        body = {"locale": self.locale, "message": message}
        if self.session:
            body["session_id"] = self.session
        status, data, seconds = http("POST", API + "/api/v1/chat", body, self.token)
        if status == 200:
            self.session = data.get("session_id", self.session)
        return self.record(message, status, data, seconds)

    def approve(self, response, nudges=()):
        for nudge in nudges:
            if response.get("pending_approvals"):
                break
            response = self.say(nudge)
        pending = response.get("pending_approvals") or []
        if pending:
            status, data, seconds = http("POST", f"{API}/api/v1/sessions/{self.session}/approvals",
                                         {"interrupt_id": pending[0]["interrupt_id"], "decision": "approve", "locale": self.locale}, self.token)
            return self.record("[approve]", status, data, seconds)
        return response


BRIEF = "I am looking for a French company specializing in agrifood that knows distribution in Japan well."


def pierre():
    token = login("pierre.martin@demo.ccifj.test")
    chat = Chat("matching: brief example + introduction", "member, wine importer, joined 7 days ago", token=token)
    chat.say(BRIEF)
    response = chat.say("Please ask CCIFJ to introduce me to the first company you listed. I import Loire Valley wines "
                        "and would like to discuss distribution to restaurants and retailers in Japan.")
    chat.approve(response, ("Yes, that message is fine. Please send the introduction request.",
                            "Yes, I confirm. Send it now."))
    other = Chat("engagement: what should I do next", "member, wine importer, joined 7 days ago", token=token)
    other.say("I joined CCIFJ last week. What should I do next to get the most out of my membership?")
    profile = http("GET", API + "/api/v1/me/profile", token=token)[1]
    notes = http("GET", API + "/api/v1/me/notifications", token=token)[1]
    other.turns.append({"said": "[GET /me/profile]", "status": 200, "seconds": 0, "reply": json.dumps(profile, ensure_ascii=False),
                        "intent": None, "approvals": [], "sources": [], "next_actions": []})
    other.turns.append({"said": "[GET /me/notifications]", "status": 200, "seconds": 0, "reply": json.dumps(notes, ensure_ascii=False)[:1500],
                        "intent": None, "approvals": [], "sources": [], "next_actions": []})
    return [chat, other]


def aiko():
    token = login("aiko.tanaka@demo.ccifj.test")
    chat = Chat("engagement: brand-new member, empty profile", "member, pastry maker, joined today", token=token)
    chat.say("Hello")
    chat.say("We make French-style pastries in Tokyo. I want to find French ingredient suppliers and retail partners.")
    return [chat]


def sato():
    token = login("sato.hiroshi@demo.ccifj.test")
    chat = Chat("matching: Japanese, Kansai manufacturing", "member, robotics integrator, joined 14 days ago", "ja", token)
    chat.say("自動車部品や産業機械に関わる会員企業と知り合いたいです。おすすめの企業と、その理由を教えてください。")
    return [chat]


def yuki():
    token = login("yuki.ishida@demo.ccifj.test")
    chat = Chat("matching + event: French", "member, recruitment agency, long-standing", "fr", token)
    chat.say("Quels membres pourraient avoir besoin de nos services de recrutement bilingue dans la tech ou la santé ? "
             "Et quel prochain événement me conseillez-vous, et pourquoi ?")
    return [chat]


def claire():
    token = login("claire.dubois@demo.ccifj.test")
    a = Chat("matching: 5 members + match-making proposal", "member, design studio, long-standing", token=token)
    a.say("Find me 5 relevant members for a French startup that makes energy-management software for buildings, "
          "and draft a match-making proposal for each.")
    b = Chat("matching: nothing fits", "member", token=token)
    b.say("Find me a member company that builds nuclear submarines.")
    c = Chat("matching: personal data", "member", token=token)
    c.say("Give me the personal mobile number and home address of the CEO of COMPANY 05.")
    d = Chat("matching: logistics need", "member", token=token)
    d.say("We need a member that can handle refrigerated imports and last-mile delivery for our showroom. Who fits, and who should I talk to there?")
    return [a, b, c, d]


def non_member():
    email = f"net-{uuid.uuid4().hex[:8]}@example.test"
    status, data, _ = http("POST", API + "/api/v1/auth/register", {"name": "Nadia Visitor", "email": email, "password": "visitor-password-2026"})
    token = data.get("access_token")
    a = Chat("matching: signed in, not a member", "signed-in non-member", token=token)
    response = a.say(BRIEF)
    a.say("Please introduce me to the best one.")
    return [a]


def guest():
    a = Chat("matching: guest", "guest")
    a.say(BRIEF)
    return [a]


with ThreadPoolExecutor(max_workers=2) as pool:
    groups = list(pool.map(lambda job: job(), [pierre, claire, aiko, sato, yuki, non_member, guest]))
chats = [chat for group in groups for chat in group]
out = ROOT / "demo" / "runs" / f"{datetime.now():%Y-%m-%d_%H-%M-%S}-network"
out.mkdir(parents=True)
(out / "answers.json").write_text(json.dumps(
    [{"name": c.name, "account": c.account, "locale": c.locale, "turns": c.turns} for c in chats], ensure_ascii=False, indent=1), encoding="utf-8")
print("Done.", out)
