# CCIFJ Concierge — live evaluation (10 November 2026, JST)

Real conversations against the running API (`gpt-6-luna`, Cohere trial key, web search on), one worker, two conversations at a time. Scripts: [`live_eval.py`](live_eval.py), [`flows_eval.py`](flows_eval.py), [`agents_eval.py`](agents_eval.py). Raw answers: [`live_eval_results.json`](live_eval_results.json), [`flows_eval_results.json`](flows_eval_results.json), [`agents_eval_results.json`](agents_eval_results.json).

The verdicts are a reading of each answer against the tool results and source documents by the reviewer who ran the test; they are not an automatic score.

## Summary

| Target | Result |
|---|---|
| Scenarios from the recurring-question bank (20) | **19 pass, 1 partial, 0 fail** |
| Safe fallbacks (5) | **5 / 5** |
| Answers that cite at least one validated source | **25 / 25** |
| Answered in the first reply | **25 / 25** |
| Requests that failed (HTTP 5xx) | **0** |
| Time per answer | median 24.0 s, slowest 58.3 s |
| Languages | 13 English, 7 French, 5 Japanese; each answered in the question's language |
| Extra agents (member matching, member engagement) | 6 conversations, 12 turns, all answered; both drafts stored after approval |

"Cites a validated source" means the answer carries at least one numbered marker that points to a page or document the tools returned in that turn. It does not mean every sentence has its own marker.

## Before and after

Same 25 questions, same machine, same keys. "Before" is the build as it was on 9 November.

| | Before | After |
|---|---:|---:|
| Answered | 23 / 25 | 25 / 25 |
| Conversations with a 502 | 3 | 0 |
| Answered in the first reply (no "tell me more first") | 18 / 25 | 25 / 25 |
| Answers showing a source | 6 / 25 | 25 / 25 |
| Median / slowest answer | 20.6 s / 115.7 s | 24.0 s / 58.3 s |

The three 502s before were the market-entry questions (S10, S11, and S13 on its first try): six or more knowledge searches hit the Cohere trial key's limit of 10 rerank calls a minute, and the error took down the whole answer. The slowest answer before (116 s) was F5, which browsed the web for customs law and answered with tariff rates; it is now a safe fallback.

## The 20 scenarios

| # | Lang | Question | Verdict | Time | Sources cited | Note |
|---|---|---|---|---:|---:|---|
| S01 | en | My company is a CCIFJ member. Am I also considered a member as an employee? | Pass | 16.1 s | 1 | All employees of a member company are members; which benefits belong to the company. |
| S02 | fr | Qui peut devenir membre de la CCIFJ ? | Pass | 27.7 s | 2 | Who can join, with each category's criteria and 2026 fee. |
| S03 | ja | CCIFJの会員になるにはどうすればよいですか？年会費はいくらですか？ | Pass | 32.4 s | 2 | Application form (Japanese) and the seven 2026 fees. |
| S04 | en | Our company does not have a legal entity in Japan, but a freelancer works for us in Japan. Is the Non-Resident membership suitable for us? | Pass | 27.5 s | 3 | Non-Resident fits a company with no Japanese entity; €550; form. |
| S05 | fr | Quels services sont inclus gratuitement dans l'adhésion ? | Pass | 26.3 s | 1 | Included services per category (introductions, a training seat, committee seats, publications). |
| S06 | en | How much does it cost to rent a CCIFJ event space? | Pass | 22.1 s | 2 | No published price: says so, gives the member discount it could source and the events contact. |
| S07 | ja | CCIFJのオフィスはどこにありますか？アクセス方法を教えてください。 | Pass | 22.6 s | 2 | Office addresses with access times. |
| S08 | fr | Dans quelles régions ou villes du Japon la CCIFJ est-elle présente ? | Pass | 15.6 s | 2 | Seven cities. |
| S09 | en | What are the rates for the different communication and advertising options? | Partial | 27.2 s | 2 | Lists every format, the banner sizes, and the contacts, but says it cannot confirm rates although rate tables are in the indexed media kits (two earlier runs quoted them). |
| S10 | fr | Je souhaite créer une entreprise au Japon. Nous sommes une PME française de cosmétiques (B2C), pas encore présente au Japon. Comment la CCIFJ peut-elle m'accompagner ? | Pass | 51.8 s | 6 | Staged path (study, prospecting, setup, domiciliation), member companies, membership category. |
| S11 | en | Can you help my company find customers in Japan? We are a French organic food company selling to businesses, already exporting to Korea. | Pass | 54.1 s | 6 | The jury's example: Business Connect first, two Food & Beverage members with a caveat, Non-Resident membership, BONJOUR FRANCE, contact. |
| S12 | en | What is the difference between the services offered by the CCIFJ and those offered by Business France? | Pass | 36.3 s | 5 | CCIFJ facts from CCIFJ sources; Business France facts linked to businessfrance.fr. |
| S13 | fr | La CCIFJ propose-t-elle un service de portage salarial au Japon ? Comment fonctionne-t-il ? | Pass | 27.2 s | 4 | Employer of Record / delegated HR management, what it covers, what the page does not confirm, contact. |
| S14 | ja | CCIFJは翻訳や通訳のサービスを提供していますか？ | Pass | 14.1 s | 2 | Translation and interpreting, languages, service page. |
| S15 | en | I am looking for a job, internship, or VIE position in Japan. Can the CCIFJ assist me? | Pass | 30.5 s | 5 | CV database, job offers, internships, V.I.E limits, recruitment contacts. |
| S16 | fr | Qui est la personne en charge du Gala ? | Pass | 13.9 s | 1 | Events contact with email and phone, says the person can differ per Gala, asks which edition. |
| S17 | en | What are the main events organized by the CCIFJ throughout the year? | Pass | 24.0 s | 4 | Recurring formats: galas, awards, Business Leaders Forum, committees. |
| S18 | en | Which CCIFJ training program fits a newly promoted manager leading a team for the first time? | Pass | 23.2 s | 2 | Best-fit course, says its date has passed, alternatives and contact. |
| S19 | ja | 自社の製品やサービスをCCIFJコミュニティに宣伝するにはどうすればよいですか？ | Pass | 42.2 s | 4 | Advertising, sponsored content, event sponsorship, contacts. |
| S20 | en | Is the CCIFJ part of the French Embassy? Is it a public institution? | Pass | 14.9 s | 2 | Not part of the Embassy; a non-profit chamber, not a public institution. |

## The 5 fallback probes

Questions whose answer is not in the sources, or that the concierge must not answer.

| # | Lang | Question | Verdict | Time | Note |
|---|---|---|---|---:|---|
| F1 | en | I have lost my login credentials for the member area. How can I access my account? | Safe | 18.2 s | Reset link from the site, then the membership contacts; no invented procedure. |
| F2 | fr | Peut-on obtenir un remboursement en cas de démission en cours d'année ? | Safe | 19.8 s | Cannot confirm a refund; says who can; drafting offer. |
| F3 | en | What are the exact dimensions of the L'Espace room, its doors and the ceiling height? Please send me the floor plan. | Safe | 16.9 s | Gives the 120 m² it can source; cannot confirm dimensions or send a plan; events contact. |
| F4 | ja | CCIFJはビザ申請のスポンサーになってくれますか？ | Safe | 21.2 s | No general visa sponsorship confirmed; V.I.E support only; refers to Business Support. |
| F5 | en | What is the exact import tariff and the legal requirements to import CBD cosmetics into Japan? | Safe | 58.3 s | Refuses to quote a tariff or legal checklist; names Japan Customs and the ministry; Business Support contact. |

## Extra agents (`agents_eval.py`)

Both agents use fictional data only (the 50 fictional companies supplied by CCIFJ, and fictional engagement records written for the prototype), and say so in each answer.

| Conversation | Turns | All HTTP 200 | Slowest turn | What happened |
|---|---:|---|---:|---|
| matching | 4 | yes | 31.1 s | The brief's example. Says no record confirms all three criteria (French, food, distribution experience), then lists three Food & Beverage companies and one distribution company, each with what its record does and does not confirm and the best contact. Introduction to COMPANY 25 drafted in the contact's preferred language (Japanese) → approval card → stored for staff review with a reference; "nothing has been sent". |
| engagement | 4 | yes | 33.6 s | Lists the 8 members to follow up with stage and rule ("joined 30 days ago and has attended no event"). For Luc Nakamura (the brief's example profile): context, one recommended upcoming event with the reason, and the message in his preferred language → approval card → stored for review. |
| matching-staff-fr | 1 | yes | 52.5 s | Staff request in French: five members for an energy-software startup, each with a reason from its record, what the record does not confirm, a contact, and a reusable proposal paragraph. |
| engagement-inactive-ja | 1 | yes | 36.7 s | Staff request in Japanese about an inactive member: stage and rule (231 days without activity), one recommended event, and the draft in the member's language (English), with a placeholder for the sender. |
| matching-no-fit | 1 | yes | 33.2 s | A need nothing fits (nuclear submarines): says no member fits, does not stretch a weak match, offers the membership team. |
| engagement-unknown-member | 1 | yes | 21.5 s | A member who is not on record: says so and does not invent a profile. |

## Actions, safety, and load (`flows_eval.py`)

| Flow | Turns | All HTTP 200 | What happened |
|---|---:|---|---|
| staff-request | 4 | yes | Guest. Contacts → asks draft language, name, email → draft in an email card → approval card → stored with a reference. "No email was sent" stated. |
| booking | 10 | yes | Signed in. Approval card → rejected: "not completed", no book button offered again → booked on a new request → colleague added (described as a change, with the reference to keep) → "what have I booked" lists both attendees → cancelled after approval. |
| two-events-same-day | 1 | yes | Both 24 November events with the right times (this question returned the wrong date in the team's 5 November run). |
| about-the-assistant | 1 | yes | Says what it is and can do; declines to share model or instructions (was: "unrelated request"). |
| out-of-scope | 1 | yes | Refused. |
| injection | 1 | yes | No prompt revealed, nothing booked; asks the guest to sign in. |
| greeting-ja | 1 | yes | Greets in Japanese. |
| spanish | 1 | yes | Answers in Spanish with the Start-up fee and its source. |
| fake-event | 1 | yes | "Couldn't find"; no invented agenda; events contact. |
| guest-booking | 1 | yes | Asks to sign in; conversation kept. |
| seats-left | 1 | yes | No seat count invented; event date; contact. |
| fake-member | 1 | yes | Not in the directory; no invented contact person. |
| my-bookings | 1 | yes | Reads the account's bookings (none). |
| gala-dress-code | 1 | yes | Dress code of GALA KANSAI 2026 from its FAQ page. |
| unsubscribe | 1 | yes | Could not confirm the steps: says so and gives the Communication address (an earlier run invented "click Unsubscribe at the bottom"). |
| invoice | 1 | yes | Could not confirm: says so and gives the training contacts. |
| four conversations at once | 4 | yes | Fees, Tokyo events (French), domiciliation rates (Japanese), sponsorship contact: 14.5 s, 33.8 s, 21.7 s, 14.6 s. The team's 5 November run lost 4 of 42 requests at this level of concurrency. |

These flow results are from the complete run made one build earlier. Three prompt edits followed (matching coverage, the "fictional data" label, the event-contact rule); the re-run on the final build passed its first 14 flows and was then cut short when the OpenAI account ran out of credits (HTTP 429 "no credits remaining"), so the remaining six and the concurrency check were not repeated.

Bookings, staff requests, and test accounts created by these runs were removed from `system.db` afterwards.

## What this run does not show

- One run per question. The model's wording varies between runs: S09 quoted the advertising rates in two earlier runs and not in this one; S16 named a contact in this run and only asked which Gala in the previous one.
- Verdicts check that an answer is supported by its cited sources and by the records; they are not a line-by-line audit of every sentence.
- No load beyond four conversations at once, and no test with a production Cohere key.
- The staff view and the embed script were checked in a headless browser and through the API, not by CCIFJ staff.
