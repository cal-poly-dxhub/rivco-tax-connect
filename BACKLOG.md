# Backlog

Items deferred from the current sprint.

## Tagalog Language Support (Text/Chat Only)

Add Tagalog as a supported language for text-based channels (chat, SMS). No Lex locale or TTS voice exists for Tagalog, so this is chat/SMS only. The AI model handles Tagalog natively — just needs a prompt instruction: "If the user writes in Tagalog, respond in Tagalog."

## HTTPS for Upload Portal (CloudFront)

The upload portal currently uses S3 static website hosting (HTTP only). Add a CloudFront distribution in front for HTTPS. Important since the page collects names and document uploads.

## Per-Session Upload Auth

Replace the shared password with per-session tokens: bot generates a short-lived token when giving the user the portal link, token goes in the URL query string, upload Lambda validates it against DynamoDB (TTL-enabled). Eliminates the shared password.

## SMS as a Conversational Channel

~~Automated in `post-deploy.sh` Step 6.~~ The script imports the End User Messaging SMS toll-free number into Connect and associates it with the contact flow. Toll-free registration (`registration-1368a92929fe4ab582652229d9f6fe7f`) is currently in REVIEWING status — re-run `post-deploy.sh` after approval to complete setup.

## ~~Mermaid Architecture Diagram~~

Done — see [ARCHITECTURE.md](ARCHITECTURE.md).
