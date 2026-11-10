"""Live check of the CCIFJ concierge: 20 scenarios from the official question bank + 5 fallback probes.

Guest sessions only; no approvals are granted, so nothing is booked or stored.
Usage: python live_eval.py <base_url> <out.json>
"""
import json
import sys
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor

BASE, OUT = sys.argv[1].rstrip("/"), sys.argv[2]
SKIP = {
    "en": "Please skip the details and give me a general answer.",
    "fr": "Passez les détails et donnez-moi une réponse générale, s'il vous plaît.",
    "ja": "詳細は省いて、一般的な回答をお願いします。",
}

CASES = [
    # id, locale, message
    ("S01", "en", "My company is a CCIFJ member. Am I also considered a member as an employee?"),
    ("S02", "fr", "Qui peut devenir membre de la CCIFJ ?"),
    ("S03", "ja", "CCIFJの会員になるにはどうすればよいですか？年会費はいくらですか？"),
    ("S04", "en", "Our company does not have a legal entity in Japan, but a freelancer works for us in Japan. Is the Non-Resident membership suitable for us?"),
    ("S05", "fr", "Quels services sont inclus gratuitement dans l'adhésion ?"),
    ("S06", "en", "How much does it cost to rent a CCIFJ event space?"),
    ("S07", "ja", "CCIFJのオフィスはどこにありますか？アクセス方法を教えてください。"),
    ("S08", "fr", "Dans quelles régions ou villes du Japon la CCIFJ est-elle présente ?"),
    ("S09", "en", "What are the rates for the different communication and advertising options?"),
    ("S10", "fr", "Je souhaite créer une entreprise au Japon. Nous sommes une PME française de cosmétiques (B2C), pas encore présente au Japon. Comment la CCIFJ peut-elle m'accompagner ?"),
    ("S11", "en", "Can you help my company find customers in Japan? We are a French organic food company selling to businesses, already exporting to Korea."),
    ("S12", "en", "What is the difference between the services offered by the CCIFJ and those offered by Business France?"),
    ("S13", "fr", "La CCIFJ propose-t-elle un service de portage salarial au Japon ? Comment fonctionne-t-il ?"),
    ("S14", "ja", "CCIFJは翻訳や通訳のサービスを提供していますか？"),
    ("S15", "en", "I am looking for a job, internship, or VIE position in Japan. Can the CCIFJ assist me?"),
    ("S16", "fr", "Qui est la personne en charge du Gala ?"),
    ("S17", "en", "What are the main events organized by the CCIFJ throughout the year?"),
    ("S18", "en", "Which CCIFJ training program fits a newly promoted manager leading a team for the first time?"),
    ("S19", "ja", "自社の製品やサービスをCCIFJコミュニティに宣伝するにはどうすればよいですか？"),
    ("S20", "en", "Is the CCIFJ part of the French Embassy? Is it a public institution?"),
    # Fallback probes: reliable information is probably missing or the action is impossible.
    ("F1", "en", "I have lost my login credentials for the member area. How can I access my account?"),
    ("F2", "fr", "Peut-on obtenir un remboursement en cas de démission en cours d'année ?"),
    ("F3", "en", "What are the exact dimensions of the L'Espace room, its doors and the ceiling height? Please send me the floor plan."),
    ("F4", "ja", "CCIFJはビザ申請のスポンサーになってくれますか？"),
    ("F5", "en", "What is the exact import tariff and the legal requirements to import CBD cosmetics into Japan?"),
]


def post(path, body, timeout=240):
    request = urllib.request.Request(
        BASE + path, data=json.dumps(body).encode(), headers={"Content-Type": "application/json"}
    )
    started = time.time()
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.status, json.loads(response.read()), time.time() - started
    except urllib.error.HTTPError as error:
        try:
            detail = json.loads(error.read())
        except Exception:
            detail = {}
        return error.code, detail, time.time() - started
    except Exception as error:  # timeout, connection reset
        return 0, {"error": repr(error)}, time.time() - started


def turn_record(status, body, seconds):
    return {
        "status": status,
        "seconds": round(seconds, 1),
        "reply": body.get("reply") if status == 200 else None,
        "error": None if status == 200 else body,
        "intent": (body.get("intent") or {}).get("code") if status == 200 else None,
        "sources": [{"title": s.get("title"), "url": s.get("url"), "cited": s.get("cited")} for s in body.get("sources", [])] if status == 200 else [],
        "next_actions": [a.get("id") for a in body.get("next_actions", [])] if status == 200 else [],
        "suggested_questions": body.get("suggested_questions", []) if status == 200 else [],
        "pending_approvals": len(body.get("pending_approvals", [])) if status == 200 else 0,
    }


def run(case):
    case_id, locale, message = case
    turns = []
    status, body, seconds = post("/api/v1/chat", {"locale": locale, "message": message})
    if status != 200:
        # One retry in a fresh conversation, recorded separately.
        turns.append({"message": message, **turn_record(status, body, seconds), "retried": True})
        status, body, seconds = post("/api/v1/chat", {"locale": locale, "message": message})
    turns.append({"message": message, **turn_record(status, body, seconds)})
    # The orchestrator answered alone (asked for details / clarification): skip once.
    if status == 200 and not (body.get("intent") or {}).get("code") and body.get("session_id"):
        follow = SKIP[locale]
        status2, body2, seconds2 = post(
            "/api/v1/chat", {"locale": locale, "message": follow, "session_id": body["session_id"]}
        )
        turns.append({"message": follow, **turn_record(status2, body2, seconds2)})
    print(case_id, [(t["status"], t["seconds"]) for t in turns], flush=True)
    return {"id": case_id, "locale": locale, "turns": turns}


with ThreadPoolExecutor(max_workers=2) as pool:
    results = list(pool.map(run, CASES))

with open(OUT, "w", encoding="utf-8") as handle:
    json.dump(results, handle, ensure_ascii=False, indent=1)
print("DONE", OUT)
