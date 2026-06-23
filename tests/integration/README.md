# Integration tests

End-to-end tests that hit the **deployed** stack — REST API + chat
WebSocket — and assert behaviour the manual scenarios in
`INTEGRATION_TESTS.md` describe. Catches regressions in the bot-handoff
flow (reserve → quiz → verify → save-draft → status → upload), which
otherwise only surface when a tester manually retraces them.

These are not unit tests. They make real HTTP calls, real WebSocket
connections, and real Bedrock invocations. Each WebSocket case spends
~3-5 seconds streaming tokens; the full suite runs in ~25 seconds.

## Run

```bash
# Full suite against rivco v3 (defaults):
python -m tests.integration.runner

# One case:
python -m tests.integration.runner --case chat-smoke

# Subset:
python -m tests.integration.runner \
    --case reserve-returns-token \
    --case verify-round-trip

# List discovered cases:
python -m tests.integration.runner --list

# Different stack (e.g. a future production deploy):
python -m tests.integration.runner \
    --api-url https://NEW.execute-api.us-west-2.amazonaws.com/prod \
    --ws-url  wss://NEW.execute-api.us-west-2.amazonaws.com/prod
```

Exit code: `0` on success, `1` on any failure, `2` if you asked for an
unknown case.

## Dependencies

Standard library handles everything except WebSocket:

```bash
pip install websocket-client certifi
```

`certifi` is only needed on Python builds without a system CA bundle
(notably the python.org installer on macOS). The runner falls back to
`ssl.create_default_context()` if certifi isn't installed.

## What's covered today

REST flows:
- `reserve-returns-token` — bot-handoff path mints both a submission id
  AND a session token, so save-draft works on first click
- `quiz-decoys-are-stable` — same claimant always gets the same 4 quiz
  options in the same order
- `verify-round-trip` — verify with the right answer, status returns the
  draft
- `save-draft-persists-form-fields` — round-trips formData through
  draftFormData
- `save-draft-rejects-after-submit` — autosave can't clobber a real
  submission (409)

Chat flows:
- `chat-smoke` — bot greets without errors
- `chat-no-match` — unknown name produces a deflection (not a fake match)
- `chat-address-quiz-starts` — known name triggers tax_lookup, returns
  street_options, never leaks dollar amounts pre-verification
- `chat-handoff-returns-reference` — agent handoff produces a `REF-XXXXX`
- `chat-first-name-only-asks-for-full` — single-word name doesn't
  trigger tax_lookup; bot asks for full name first

## Adding a case

1. Add `case_<slug>(eps: c.StackEndpoints) -> None` to `cases.py`.
2. Use `assert` for every contract you care about.
3. Catch `HttpError` if the case checks for an expected error response.
4. Run `python -m tests.integration.runner --case <slug>` to verify.
5. Commit.

Naming: cases are auto-discovered by prefix, and the runner converts
`case_save_draft_persists_form_fields` → `save-draft-persists-form-fields`.
