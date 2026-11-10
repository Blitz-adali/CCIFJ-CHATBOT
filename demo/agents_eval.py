"""The two extra agents, live: member matching and member engagement.

Usage: python agents_eval.py <base_url> <out.json>
Guest sessions; stores two drafts for staff review in the local database.
"""
import json
import sys
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor

BASE, OUT = sys.argv[1].rstrip("/"), sys.argv[2]


def call(path, body, timeout=240):
    request = urllib.request.Request(BASE + path, data=json.dumps(body).encode(), headers={"Content-Type": "application/json"})
    started = time.time()
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.status, json.loads(response.read() or b"{}"), round(time.time() - started, 1)
    except urllib.error.HTTPError as error:
        try:
            detail = json.loads(error.read())
        except Exception:
            detail = {}
        return error.code, detail, round(time.time() - started, 1)
    except Exception as error:
        return 0, {"error": repr(error)}, round(time.time() - started, 1)


class Chat:
    def __init__(self, name, locale="en"):
        self.name, self.locale, self.session, self.turns = name, locale, None, []

    def record(self, said, status, body, seconds):
        self.turns.append({
            "said": said, "status": status, "seconds": seconds,
            "reply": body.get("reply") if status == 200 else json.dumps(body)[:300],
            "intent": (body.get("intent") or {}).get("code"),
            "approvals": [{"name": a.get("name"), "args": a.get("args")} for item in body.get("pending_approvals", []) for a in item.get("action_requests", [])],
            "sources": [{"title": s.get("title"), "cited": s.get("cited")} for s in body.get("sources", [])],
        })
        print(self.name, "|", said[:70], "->", status, seconds, "s", "APPROVAL" if body.get("pending_approvals") else "", flush=True)
        return body

    def say(self, message):
        body = {"locale": self.locale, "message": message}
        if self.session:
            body["session_id"] = self.session
        status, data, seconds = call("/api/v1/chat", body)
        if status == 200:
            self.session = data.get("session_id", self.session)
        return self.record(message, status, data, seconds)

    def approve(self, response, nudges=("Yes, I confirm. Please store it now.",)):
        for nudge in nudges:
            if response.get("pending_approvals"):
                break
            response = self.say(nudge)
        pending = response.get("pending_approvals") or []
        if not pending:
            return response
        status, data, seconds = call(f"/api/v1/sessions/{self.session}/approvals",
                                     {"interrupt_id": pending[0]["interrupt_id"], "decision": "approve", "locale": self.locale})
        return self.record("[approve]", status, data, seconds)


def matching():
    chat = Chat("matching")
    chat.say("I am looking for a French food company with experience in Japanese distribution.")
    chat.say("Please prepare an introduction to the best contact at COMPANY 25. I am Marie Laurent from Maison Laurent, "
             "a French organic jam producer (marie.laurent@example.test). We would like to discuss distributing our jams in Japan.")
    chat.approve(chat.say("That is good. Please store it for CCIFJ to review."))
    return chat


def matching_staff_fr():
    chat = Chat("matching-staff-fr", "fr")
    chat.say("Trouve-moi 5 membres pertinents pour une startup française de logiciels de gestion d'énergie pour bâtiments, "
             "et rédige une proposition de mise en relation.")
    return chat


def matching_none():
    chat = Chat("matching-no-fit")
    chat.say("Find me a member company that builds nuclear submarines.")
    return chat


def engagement():
    chat = Chat("engagement")
    chat.say("Which members need a follow-up right now?")
    chat.say("Prepare the follow-up for Luc Nakamura. I am Tomoyo Funabashi, t.funabashi@ccifj.or.jp.")
    chat.approve(chat.say("Perfect. Please store it."))
    return chat


def engagement_ja():
    chat = Chat("engagement-inactive-ja", "ja")
    chat.say("COMPANY 19のAntoine Petitさんは最近活動がありません。再エンゲージメントのための提案とフォローアップ文を用意してください。")
    return chat


def engagement_unknown():
    chat = Chat("engagement-unknown-member")
    chat.say("Prepare a follow-up for our member Jean Valjean at COMPANY 99.")
    return chat


with ThreadPoolExecutor(max_workers=2) as pool:
    chats = list(pool.map(lambda job: job(), [matching, engagement, matching_staff_fr, engagement_ja, matching_none, engagement_unknown]))
with open(OUT, "w", encoding="utf-8") as handle:
    json.dump([{"name": c.name, "locale": c.locale, "turns": c.turns} for c in chats], handle, ensure_ascii=False, indent=1)
print("DONE", OUT)
