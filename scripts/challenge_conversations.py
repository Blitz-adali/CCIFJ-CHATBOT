#!/usr/bin/env python3
"""Run long concierge conversations, retaining one server session per scenario.

No model calls in --dry-run mode. Live mode creates anonymous test chats and
incurs normal provider usage. This runner never approves actions or replays a
failed request. Session capability tokens are held in memory and never saved.
"""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import time
import sys
from urllib.error import HTTPError
from urllib.parse import quote
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]


def post(base_url, path, payload, timeout):
    request = Request(base_url.rstrip('/')+path, data=json.dumps(payload).encode(),
                      headers={'Content-Type':'application/json'})
    with urlopen(request, timeout=timeout) as response:
        body = json.load(response)
    if not isinstance(body, dict):
        raise ValueError('Expected a JSON object')
    return body


def public_response(body):
    # An explicit allowlist keeps session capabilities and internal metadata out
    # of reports. Test content and public answer presentation remain reviewable.
    return {key:body.get(key) for key in (
        'reply','mode','request_id','sources','events','warnings','next_actions','suggested_questions',
    )} | {'pending_approval':bool(body.get('pending_approvals'))}


def approval_ids(body):
    return [item.get('interrupt_id') for item in body.get('pending_approvals') or []
            if isinstance(item,dict) and isinstance(item.get('interrupt_id'),str)]


def run_scenario(scenario, *, base_url, timeout, on_approval, save, pace=0):
    transcript = {'id':scenario['id'],'title':scenario['title'],'focus':scenario['focus'],
                  'planned_turns':len(scenario['turns']),'status':'running','turns':[]}
    save(transcript)
    session_id = None
    for index, turn in enumerate(scenario['turns'],1):
        print(f"\n[{scenario['id']} {index}/{len(scenario['turns'])}] {turn['prompt']}",flush=True)
        row = {'kind':'question','index':index,**turn}
        started = time.perf_counter()
        try:
            body = post(base_url,'/api/v1/chat',{
                'message':turn['prompt'],'locale':turn['locale'],'session_id':session_id,
            },timeout)
            returned_session = body.get('session_id')
            if not isinstance(returned_session,str) or not returned_session:
                raise ValueError('Response has no session token; refusing a silent context reset')
            if session_id is not None and returned_session != session_id:
                raise ValueError('Server changed the session token within a conversation')
            if not isinstance(body.get('reply'),str) or not body['reply'].strip():
                raise ValueError('Response has no answer')
            session_id = returned_session
            degraded = body.get('mode') == 'recovery' or 'answer_unavailable' in (body.get('warnings') or [])
            row.update(status='degraded' if degraded else 'answered',**public_response(body))
            if degraded: transcript['status']='degraded'
            print(body['reply'],flush=True)
        except Exception as error:
            row.update(status='failed',error_type=type(error).__name__)
            if isinstance(error,HTTPError):
                row['http_status']=error.code
                try:
                    detail=json.load(error).get('detail',{})
                    if isinstance(detail,dict):
                        row.update({key:detail[key] for key in ('error_code','error_reference') if isinstance(detail.get(key),str)})
                except Exception:
                    pass  # Never expose arbitrary server error bodies or capabilities.
            # The next fixed prompt may depend on the failed turn. Stop this
            # conversation; never reset its context and pretend it continued.
            transcript['status']='failed'
            print(f"Stopped this conversation: {type(error).__name__}",flush=True)
        row['seconds']=round(time.perf_counter()-started,2)
        transcript['turns'].append(row)
        save(transcript)
        if row['status'] in {'failed','degraded'}:
            return transcript

        pending = approval_ids(body)
        if body.get('pending_approvals') and not pending:
            transcript['status']='blocked_invalid_approval'
            save(transcript)
            return transcript
        if pending and on_approval=='stop':
            transcript['status']='stopped_at_approval'
            save(transcript)
            return transcript
        rejected = set()
        while pending:
            interrupt_id = pending[0]
            if interrupt_id in rejected or len(rejected)>=8:
                transcript['status']='blocked_repeated_approval'
                save(transcript)
                return transcript
            rejected.add(interrupt_id)
            decision = {'kind':'approval_rejection','after_turn':index,'decision':'reject'}
            started = time.perf_counter()
            try:
                body = post(base_url,f'/api/v1/sessions/{quote(session_id,safe="")}/approvals',
                            {'interrupt_id':interrupt_id,'decision':'reject','locale':turn['locale']},timeout)
                if body.get('session_id') != session_id:
                    raise ValueError('Approval response changed the session')
                if not isinstance(body.get('reply'),str) or not body['reply'].strip():
                    raise ValueError('Approval response has no answer')
                decision.update(status='answered',**public_response(body))
                print('[Proposed action rejected; continuing in the SAME conversation]',flush=True)
                pending = approval_ids(body)
                if body.get('pending_approvals') and not pending:
                    raise ValueError('Invalid remaining approval')
            except Exception as error:
                decision.update(status='failed',error_type=type(error).__name__)
                if isinstance(error,HTTPError): decision['http_status']=error.code
                transcript['status']='failed'
            decision['seconds']=round(time.perf_counter()-started,2)
            transcript['turns'].append(decision)
            save(transcript)
            if decision['status']=='failed':
                return transcript
        if pace:
            time.sleep(pace)
    transcript['status']='completed'
    save(transcript)
    return transcript


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--base-url',default='http://127.0.0.1:8000')
    parser.add_argument('--suite',type=Path,default=ROOT/'tests/concierge_challenges.json')
    parser.add_argument('--scenario',action='append',help='Run a scenario ID; repeat to select several')
    parser.add_argument('--list',action='store_true',help='List conversations without making requests')
    parser.add_argument('--dry-run',action='store_true',help='Print selected questions and expectations; no requests')
    parser.add_argument('--on-approval',choices=('stop','reject'),default='stop',help='Stop the scenario, or reject proposals and continue; never approve')
    parser.add_argument('--timeout',type=float,default=200)
    parser.add_argument('--pace',type=float,default=1,help='Seconds between question turns')
    parser.add_argument('--output',type=Path)
    parser.add_argument('--require-upgrade',action='store_true',help='Require the running backend fingerprint to match this checkout before testing')
    args = parser.parse_args()
    if args.timeout<=0 or args.pace<0:
        parser.error('timeout must be positive and pace nonnegative')
    suite=json.loads(args.suite.read_text(encoding='utf-8'))
    scenarios=suite['scenarios']
    unknown=set(args.scenario or [])-{case['id'] for case in scenarios}
    if unknown: parser.error('Unknown scenarios: '+', '.join(sorted(unknown)))
    if args.scenario:
        scenarios=[case for case in scenarios if case['id'] in args.scenario]
    total=sum(len(case['turns']) for case in scenarios)
    print(f'{len(scenarios)} conversations, {total} question turns. One session per conversation.',flush=True)
    if args.list or args.dry_run:
        for case in scenarios:
            print(f"{case['id']}: {case['title']} ({len(case['turns'])} turns)")
            if args.dry_run:
                for index,turn in enumerate(case['turns'],1):
                    print(f"  {index}. [{turn['locale']}] {turn['prompt']}\n     Expected: {turn['expectation']}")
        return 0
    stamp=datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
    output=args.output or ROOT/'docs/challenge-runs'/f'{stamp}.json'
    output.parent.mkdir(parents=True,exist_ok=True)
    report={'generated_at':datetime.now(timezone.utc).isoformat(),'base_url':args.base_url,
            'suite_version':suite['version'],'rubric':suite['rubric'],'on_approval':args.on_approval,
            'planned_conversations':len(scenarios),'planned_question_turns':total,'status':'running',
            'note':'Scripted multi-turn conversations, not adaptive user simulation. Review answers against expectations; no fabricated automatic quality score. Rejections are harness decisions, not user consent.','conversations':[]}

    def save(transcript=None):
        if transcript is not None:
            existing=next((i for i,c in enumerate(report['conversations']) if c['id']==transcript['id']),None)
            if existing is None: report['conversations'].append(transcript)
            else: report['conversations'][existing]=transcript
        temporary=output.with_suffix(output.suffix+'.tmp')
        temporary.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
        temporary.replace(output)

    save()
    if args.require_upgrade:
        try:
            with urlopen(args.base_url.rstrip('/')+'/api/v1/status',timeout=min(args.timeout,6)) as response:
                report['backend']=json.load(response)
            sys.path.insert(0,str(ROOT/'src'))
            from concierge.version import build_signature
            report['expected_backend_version']=build_signature()
            if report['backend'].get('version') != report['expected_backend_version']:
                report['status']='backend_version_mismatch'
                save()
                print('Backend differs from this checkout. Restart the API, then rerun. No challenge prompts sent.',flush=True)
                print(f'Report: {output}',flush=True)
                return 1
        except Exception as error:
            report.update(status='preflight_failed',preflight_error=type(error).__name__)
            save()
            print('Could not verify the running backend. Check the URL and restart the API. No challenge prompts sent.',flush=True)
            print(f'Report: {output}',flush=True)
            return 1
        print('Backend version verified: '+report['backend']['version'],flush=True)
    failures=0
    try:
        for case in scenarios:
            result=run_scenario(case,base_url=args.base_url,timeout=args.timeout,on_approval=args.on_approval,save=save,pace=args.pace)
            failures=failures+1 if result['status']=='failed' else 0
            if failures>=2:
                report['status']='stopped_after_failures'
                print('Stopped after two consecutive failed conversations; inspect server readiness.',flush=True)
                break
        else:
            report['status']='completed' if all(c['status']=='completed' for c in report['conversations']) else 'incomplete'
    except KeyboardInterrupt:
        report['status']='interrupted'
        # Outstanding request outcomes are not known; no automatic resume/retry.
    finally:
        save()
        print(f'Transcripts saved to {output}',flush=True)
    return 0 if report['status']=='completed' else 1


if __name__=='__main__':
    raise SystemExit(main())
