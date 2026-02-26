# E2E Test Report
2026-02-26 15:07 | 142s | Judge: `us.anthropic.claude-haiku-4-5-20251001-v1:0` | 18/19 passed | tokens: 12,693 in / 3,393 out

| # | Scenario | Result | In | Out | Reason |
|---|----------|--------|----|-----|--------|
| 1 | Single match — stale warrant | PASS | 811 | 211 | The bot successfully executed all required steps in the correct order. |
| 2 | Single match — property tax | PASS | 804 | 200 | The bot successfully meets all critical checks. |
| 3 | Single match — payroll | FAIL | 827 | 280 | The bot revealed the refund amount ($1,788.33) in the same message as address verification, rather than waiting for explicit address confirmation before disclosing amounts. |
| 4 | Single match — business name | PASS | 757 | 213 | The bot successfully met all critical requirements. |
| 5 | Multiple refunds — same person, mixed types | PASS | 985 | 208 | The bot successfully completed all required tasks in the correct order. |
| 6 | Disambiguation — multiple people, same name | PASS | 921 | 213 | The bot successfully met all critical requirements. |
| 7 | Disambiguation — wrong address given | PASS | 688 | 159 | The bot successfully handled the scenario as expected. |
| 8 | Address verification denied | PASS | 726 | 190 | The bot successfully followed the expected behavior protocol. |
| 9 | No match — unknown name | PASS | 621 | 164 | The bot successfully met all critical checks. |
| 10 | Fuzzy match — typo in name | PASS | 763 | 236 | The bot successfully handled the typo-laden input "Jayne Doh" and correctly matched it to the expected record for "Jane Doe" in the system. |
| 11 | Knowledge base — office hours | PASS | 564 | 147 | The bot correctly provided both required office hour ranges. |
| 12 | Knowledge base — phone number | PASS | 506 | 127 | The bot successfully provided the office phone number (951) 955-3800 in response to the user's direct question. |
| 13 | Knowledge base — office location | PASS | 555 | 140 | The bot successfully provided the complete office address when asked by the user. |
| 14 | Knowledge base — claim deadline FAQ | PASS | 505 | 119 | The bot's response directly addresses the user's question about deadlines and covers all three critical checks. |
| 15 | Knowledge base — replacement check timeline FAQ | PASS | 548 | 169 | The bot's response directly addresses the user's question about processing timelines and covers all three critical checks. |
| 16 | Escalation — request live agent | PASS | 523 | 148 | The bot successfully met both critical checks. |
| 17 | Edge case — gibberish input | PASS | 530 | 145 | The bot handled gibberish input gracefully without crashing or returning an error message. |
| 18 | Edge case — partial name only | PASS | 528 | 160 | The bot successfully handled the partial name "Gloria" without crashing or returning an error. |
| 19 | Edge case — off-topic question | PASS | 531 | 164 | The bot successfully handled an off-topic question by declining to answer the weather query and politely redirecting the user to its intended purpose. |