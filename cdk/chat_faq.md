# Chat FAQ

Plain-text Q&A used as a knowledge base for the chatbot. Append entries as
the office shares them. The format is strict so the model can match against
it: each entry is a `Q:` line followed by an `A:` line. Blank lines separate
entries. Don't add prose between blocks — the whole file is concatenated
into the system prompt verbatim.

If a customer asks something that isn't covered here, the bot is instructed
to offer a live-agent handoff rather than guess.

---

Q: Is there a deadline to claim my money?
A: Yes. The County holds funds for four years, and three years for property tax refunds. After that, the money is transferred to the County's General Fund and cannot be claimed.

Q: How long does it take to get a replacement check?
A: After submitting a valid claim, it usually takes 45 to 60 days. It may take up to 12 weeks if there are many claims or complex documentation. Claims over $10,000 may take longer because they need Board approval.

Q: What is the office phone number?
A: (951) 955-3800.

Q: Where is the Auditor-Controller's office located?
A: 4080 Lemon Street, 6th Floor, Riverside, CA 92501.

Q: What are your office hours?
A: Monday through Thursday 8:00 AM to 5:00 PM, Friday 8:00 AM to 4:30 PM.

Q: What kinds of refunds can I look up here?
A: Unclaimed refunds, stale-dated warrants, payroll, and property tax. If you give me your name I can search for any refunds the County has on file for you.

Q: How does the identity verification work?
A: Before sharing any refund details, I'll ask you to pick the street name from your address among a few options, and then to enter the house number. This protects your information from someone who only knows your name.

Q: What if I picked the wrong street or entered the wrong number?
A: For security, the system can't tell you which answer was correct. You can try again on the same conversation up to a limit. If you're locked out, please call the office at (951) 955-3800 and they can verify you another way.

Q: I want to talk to a real person.
A: I can connect you. Just say "I'd like to talk to a person" and I'll generate a reference number — you call (951) 955-3800 during office hours, give the agent that number, and they'll continue from where we left off with the full transcript in front of them.

Q: Can I claim a refund on someone else's behalf, like a deceased relative or a business I own?
A: Yes, but you'll need to provide additional documentation through the claim form to prove you're the rightful claimant (e.g. proof of entitlement for stale-dated warrants, proof of ownership for property tax, business documentation, or estate paperwork). I don't have the full requirements list — for the specific paperwork your situation needs, please call (951) 955-3800.
