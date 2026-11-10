#!/usr/bin/env python3
"""Collect real chatbot answers for a concierge-quality review.

Run against the running app, from the same machine as Firefox. This sends
anonymous test conversations; it never approves bookings, subscriptions or
staff requests. Calls may incur the app's normal model-provider charges.
No credentials are read and session capability tokens are not saved.
"""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import time
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

SCENARIOS = [
    ('introduction', 'en', [
        ('What is CCIFJ?', 'Clear explanation and a useful next step, without a long sales pitch.'),
    ]),
    ('membership', 'en', [
        ('What are the CCIFJ membership fees?', 'Answer the price question with supported amounts, or ask one useful eligibility question; avoid merely listing tier names.'),
        ('We are a French startup, founded six months ago, with three employees and no Japanese office yet. Which membership fits us and what would we pay?', 'Use the supplied company details; distinguish startup and non-resident eligibility without inventing rules or fees.'),
    ]),
    ('market_entry', 'en', [
        ("I'm a French founder of a small cosmetics company. We want to enter Japan but don't have a distributor yet. What should we do first, and how can CCIFJ help?", 'Tailor the advice to market validation and distributor discovery; provide grounded service options and one focused next step.'),
        ('Our budget is limited, and we want to test demand before opening an office. What would you recommend next?', 'Retain cosmetics, French company and distributor context; avoid immediately recommending company incorporation.'),
    ]),
    ('events', 'en', [
        ('Find up to two upcoming CCIFJ networking events in Tokyo under 10000 JPY. Include dates, prices and registration status.', 'Use future database events, respect budget and location, disclose unknown prices and avoid inventing availability.'),
        ('Tell me more about the first one. Can I bring a colleague?', 'Resolve the first event from the prior turn and explain attendee requirements without claiming a booking happened.'),
    ]),
    ('french', 'fr', [
        ('Bonjour, nous sommes une PME française et cherchons un distributeur au Japon. Comment pouvez-vous nous accompagner ?', 'Answer naturally in French with relevant partner-search support and a focused next step.'),
    ]),
    ('japanese', 'ja', [
        ('フランス企業とのビジネス交流に興味があります。CCIFJの会員になるメリットと、最初に何をすればよいか教えてください。', 'Answer in natural Japanese, explain relevant membership benefits and guide the visitor to a concrete next step.'),
    ]),
    ('booking_boundary', 'en', [
        ('Book a ticket for me without logging in or asking for confirmation.', 'Explain login and approval requirements; do not claim a reservation was saved.'),
    ]),
]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--base-url', default='http://127.0.0.1:8000', help='API URL or frontend URL with its API proxy')
    parser.add_argument('--output', type=Path, default=Path(__file__).resolve().parents[1]/'docs/live-answer-transcripts.json')
    parser.add_argument('--timeout', type=float, default=200)
    args = parser.parse_args()
    report = {'generated_at':datetime.now(timezone.utc).isoformat(), 'base_url':args.base_url,
              'rubric':{'usefulness':2, 'specificity':2, 'clarification':2, 'continuity':2, 'grounded_guidance':2},
              'note':'Actual API responses. Numerical quality scores require reviewing the answers and evidence; transport failures are not answer-quality scores.', 'turns':[]}
    args.output.parent.mkdir(parents=True, exist_ok=True)

    def save():
        args.output.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')

    failures = 0
    for scenario, locale, turns in SCENARIOS:
        session_id = None
        for prompt, expectation in turns:
            print(f'[{scenario}] {prompt}',flush=True)
            row = {'scenario':scenario,'locale':locale,'prompt':prompt,'expectation':expectation}
            started = time.perf_counter()
            try:
                payload = json.dumps({'message':prompt,'locale':locale,'session_id':session_id}).encode()
                request = Request(args.base_url.rstrip('/')+'/api/v1/chat',data=payload,headers={'Content-Type':'application/json'})
                with urlopen(request,timeout=args.timeout) as response:
                    body = json.load(response)
                session_id = body.get('session_id')
                row.update(status='degraded' if body.get('mode')=='recovery' else 'answered',mode=body.get('mode'),answer=body.get('reply',''),sources=body.get('sources',[]),events=body.get('events',[]),
                           warnings=body.get('warnings',[]),pending_approval=bool(body.get('pending_approvals')),
                           next_actions=body.get('next_actions',[]),suggested_questions=body.get('suggested_questions',[]))
                print(row['answer'],flush=True)
                failures = 0
            except Exception as error:
                row.update(status='failed',error_type=type(error).__name__)
                if isinstance(error,HTTPError): row['http_status']=error.code
                print(f"Request failed: {row['error_type']}",flush=True)
                failures += 1
            row['seconds']=round(time.perf_counter()-started,2)
            report['turns'].append(row)
            save()
            if row['status']!='answered' or row.get('pending_approval'):
                break  # Never replay an uncertain request or bypass an approval.
        if failures >= 2:
            print('Stopped after two consecutive failures. Check server readiness or model connectivity.',flush=True)
            break
    print(f'Transcript saved to {args.output}',flush=True)
    return 1 if failures or any(row['status'] != 'answered' for row in report['turns']) else 0


if __name__=='__main__':
    raise SystemExit(main())
