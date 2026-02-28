#!/usr/bin/env python3
"""post_deploy.py — Run after `cdk deploy` and console setup complete.

Prerequisites (manual, one-time):
  1. Register MCP gateway as third-party app in Connect console
  2. Create ORCHESTRATION AI agent in Q in Connect console with MCP tool
  3. Save and publish the agent

This script handles:
  1. Upload refund data to S3
  2. Create/update the AI orchestration prompt from config.yaml
  3. Update AI agent to use the latest prompt version, then version it
  4. Create/update the contact flow with resolved ARNs
  5. Claim a phone number and associate it with the flow
  6. Set up SMS channel
"""

import json
import os
import re
import sys
import time
from pathlib import Path
from urllib.parse import urljoin

import boto3
import yaml

REGION = "us-west-2"
STACK_NAME = "riverside-tax-refund"
AGENT_NAME = "tax-refund-agent"
PROMPT_NAME = "tax-refund-orchestration-prompt"
PROMPT_MODEL = "us.anthropic.claude-haiku-4-5-20251001-v1:0"
SCRIPT_DIR = Path(__file__).resolve().parent


def get_stack_outputs():
    cfn = boto3.client("cloudformation", region_name=REGION)
    resp = cfn.describe_stacks(StackName=STACK_NAME)
    return {o["OutputKey"]: o["OutputValue"] for o in resp["Stacks"][0]["Outputs"]}


def log(icon, msg):
    print(f"  {icon} {msg}")


def log_step(msg):
    print(f"\n▶ {msg}")


# ── Step 1: Upload refund data ──────────────────────────────

def upload_refund_data(bucket):
    log_step("Uploading refund data to S3...")
    data_file = SCRIPT_DIR / "refunds_demo_balanced.jsonl"
    if data_file.exists():
        s3 = boto3.client("s3", region_name=REGION)
        s3.upload_file(str(data_file), bucket, data_file.name)
        log("✅", "Done")
    else:
        log("⚠️", "refunds_demo_balanced.jsonl not found")


# ── Step 2: Create/update AI Prompt ─────────────────────────

def sync_ai_prompt(assistant_id):
    """Create or update the AI prompt from config.yaml, return (prompt_id, version_arn)."""
    log_step("Creating AI orchestration prompt...")
    qc = boto3.client("qconnect", region_name=REGION)

    with open(SCRIPT_DIR / "config.yaml") as f:
        config = yaml.safe_load(f)
    prompt_text = config["prompts"]["ai_orchestration"]
    template_cfg = {"textFullAIPromptEditTemplateConfiguration": {"text": prompt_text}}

    # Find existing prompt
    prompts = qc.list_ai_prompts(assistantId=assistant_id)["aiPromptSummaries"]
    existing = next((p for p in prompts if p["name"] == PROMPT_NAME), None)

    if existing:
        prompt_id = existing["aiPromptId"]
        log("📝", f"Updating existing prompt: {prompt_id}")
        qc.update_ai_prompt(
            assistantId=assistant_id,
            aiPromptId=prompt_id,
            visibilityStatus="PUBLISHED",
            templateConfiguration=template_cfg,
        )
        log("✅", "Updated")
    else:
        log("📝", "Creating new prompt...")
        resp = qc.create_ai_prompt(
            assistantId=assistant_id,
            name=PROMPT_NAME,
            type="ORCHESTRATION",
            apiFormat="MESSAGES",
            templateType="TEXT",
            modelId=PROMPT_MODEL,
            visibilityStatus="PUBLISHED",
            templateConfiguration=template_cfg,
        )
        prompt_id = resp["aiPrompt"]["aiPromptId"]
        log("✅", f"Created prompt: {prompt_id}")

    # Version the prompt
    try:
        ver = qc.create_ai_prompt_version(assistantId=assistant_id, aiPromptId=prompt_id)
        version_arn = ver["aiPrompt"]["aiPromptArn"]
        version_number = ver["aiPrompt"].get("versionNumber")
        log("✅", f"Prompt version: {version_arn}")
        return prompt_id, version_arn, version_number
    except Exception as e:
        log("⚠️", f"Prompt versioning failed: {e}")
        return prompt_id, None, None


# ── Step 3: Find agent, update prompt, version ──────────────

def sync_ai_agent(assistant_id, prompt_id, prompt_version_number):
    """Update the agent's orchestration prompt to the latest version, then version the agent."""
    log_step(f"Looking for ORCHESTRATION AI Agent '{AGENT_NAME}'...")
    qc = boto3.client("qconnect", region_name=REGION)

    agents = qc.list_ai_agents(assistantId=assistant_id)["aiAgentSummaries"]
    agent = next(
        (a for a in agents if a["name"] == AGENT_NAME and a["type"] == "ORCHESTRATION"),
        None,
    )

    if not agent:
        log("⚠️", f"No ORCHESTRATION agent named '{AGENT_NAME}' found.")
        print()
        print("  Create it in the Q in Connect console:")
        print("    1. AI agent designer → Create AI Agent → Orchestration type")
        print(f"    2. Name it '{AGENT_NAME}'")
        print("    3. Add MCP tools from the gateway: tax_lookup, send_sms")
        print(f"    4. Set prompt to '{PROMPT_NAME}'")
        print("    5. Save and publish, then re-run this script")
        return None, None

    agent_id = agent["aiAgentId"]
    log("✓", f"Found: {agent_id}")

    # Get current KB association ID
    associations = qc.list_assistant_associations(assistantId=assistant_id)["assistantAssociationSummaries"]
    kb_assoc = next((a for a in associations if a["associationType"] == "KNOWLEDGE_BASE"), None)
    kb_assoc_id = kb_assoc["assistantAssociationId"] if kb_assoc else None

    if prompt_id and prompt_version_number:
        full_agent = qc.get_ai_agent(assistantId=assistant_id, aiAgentId=agent_id)
        # get_ai_agent returns SDK_UNKNOWN_MEMBER for orchestration config; rebuild from scratch
        new_prompt_ref = f"{prompt_id}:{prompt_version_number}"

        orch_cfg = {
            "orchestrationAIPromptId": new_prompt_ref,
        }
        if kb_assoc_id:
            orch_cfg["associationConfigurations"] = [
                {
                    "associationType": "KNOWLEDGE_BASE",
                    "associationId": kb_assoc_id,
                    "associationConfigurationData": {
                        "knowledgeBaseAssociationConfigurationData": {
                            "overrideKnowledgeBaseSearchType": "HYBRID",
                            "maxResults": 5,
                        }
                    },
                }
            ]
            log("✓", f"KB association: {kb_assoc_id}")

        try:
            qc.update_ai_agent(
                assistantId=assistant_id,
                aiAgentId=agent_id,
                visibilityStatus="PUBLISHED",
                configuration={"orchestrationAIAgentConfiguration": orch_cfg},
            )
            log("✅", "Agent updated")
        except Exception as e:
            log("⚠️", f"Agent update failed: {e}")

    # Version the agent
    try:
        ver = qc.create_ai_agent_version(assistantId=assistant_id, aiAgentId=agent_id)
        versioned_arn = ver["aiAgent"]["aiAgentArn"]
        version_number = ver["aiAgent"].get("versionNumber") or versioned_arn.rsplit(":", 1)[-1]
        log("✅", f"Published version: {versioned_arn}")
        log("ℹ️", f"Set as ORCHESTRATION default in console: AI agent designer → AI agents → {AGENT_NAME} → Set as default")
        latest_arn = versioned_arn.rsplit(":", 1)[0] + ":$LATEST"
        return agent_id, latest_arn
    except Exception as e:
        log("❌", f"Versioning failed: {e}")
        sys.exit(1)


# ── Step 4: Create/update contact flow ──────────────────────

def sync_contact_flow(instance_id, assistant_arn, ai_agent_arn, bot_alias_arn, queue_arn, lambda_arn):
    log_step("Updating contact flow...")
    connect = boto3.client("connect", region_name=REGION)

    with open(SCRIPT_DIR / "TaxRefundFlow.json") as f:
        content = f.read()

    content = (
        content.replace("${AssistantArn}", assistant_arn)
        .replace("${AIAgentArn}", ai_agent_arn)
        .replace("${BotAliasArn}", bot_alias_arn)
        .replace("${QueueArn}", queue_arn)
        .replace("${LambdaArn}", lambda_arn)
    )
    json.loads(content)  # validate

    # Find existing flow
    flows = connect.list_contact_flows(
        InstanceId=instance_id, ContactFlowTypes=["CONTACT_FLOW"]
    )["ContactFlowSummaryList"]
    existing = next((f for f in flows if f["Name"] == "TaxRefundFlow"), None)

    if existing:
        flow_id = existing["Id"]
        try:
            connect.update_contact_flow_content(
                InstanceId=instance_id, ContactFlowId=flow_id, Content=content
            )
        except connect.exceptions.InvalidContactFlowException as e:
            log("❌", f"Flow validation failed: {e.response}")
            raise
        log("✅", f"Updated flow: {flow_id}")
    else:
        resp = connect.create_contact_flow(
            InstanceId=instance_id, Name="TaxRefundFlow", Type="CONTACT_FLOW", Content=content
        )
        flow_id = resp["ContactFlowId"]
        log("✅", f"Created flow: {flow_id}")

    return flow_id


# ── Step 5: Claim phone number ──────────────────────────────

def setup_phone_number(instance_id, instance_arn, contact_flow_id):
    log_step("Checking phone number...")
    connect = boto3.client("connect", region_name=REGION)

    numbers = connect.list_phone_numbers_v2(TargetArn=instance_arn)["ListPhoneNumbersSummaryList"]
    phone_number_id = numbers[0]["PhoneNumberId"] if numbers else None

    if phone_number_id:
        log("✓", f"Phone already claimed: {phone_number_id}")
    else:
        try:
            avail = connect.search_available_phone_numbers(
                TargetArn=instance_arn,
                PhoneNumberCountryCode="US",
                PhoneNumberType="TOLL_FREE",
                MaxResults=1,
            )["AvailableNumbersList"]
            if avail:
                resp = connect.claim_phone_number(
                    PhoneNumber=avail[0]["PhoneNumber"], TargetArn=instance_arn
                )
                phone_number_id = resp["PhoneNumberId"]
                log("✅", f"Claimed: {avail[0]['PhoneNumber']} (ID: {phone_number_id})")
            else:
                log("⚠️", "No toll-free numbers available")
        except Exception as e:
            log("⚠️", f"Phone claim failed: {e}")

    if phone_number_id and contact_flow_id:
        time.sleep(5)
        try:
            connect.associate_phone_number_contact_flow(
                PhoneNumberId=phone_number_id,
                InstanceId=instance_id,
                ContactFlowId=contact_flow_id,
            )
            log("✅", "Associated with TaxRefundFlow")
        except Exception as e:
            log("⚠️", f"Association failed — do it in the Connect console: {e}")

    return phone_number_id


# ── Step 6: SMS Channel Setup ───────────────────────────────

def setup_sms_channel(instance_id, instance_arn, contact_flow_id, voice_phone_id):
    log_step("Setting up SMS channel...")
    sms = boto3.client("pinpoint-sms-voice-v2", region_name=REGION)
    connect = boto3.client("connect", region_name=REGION)
    sts = boto3.client("sts", region_name=REGION)

    # Find SMS-capable toll-free number
    try:
        phone_numbers = sms.describe_phone_numbers()["PhoneNumbers"]
        sms_phone = next(
            (p for p in phone_numbers if p["NumberType"] == "TOLL_FREE" and "SMS" in p.get("NumberCapabilities", [])),
            None,
        )
    except Exception:
        sms_phone = None

    if not sms_phone:
        log("⚠️", "No SMS-capable toll-free number found in End User Messaging.")
        log("", "Request one in the End User Messaging SMS console first.")
        return "NO_NUMBER"

    sms_phone_id = sms_phone["PhoneNumberId"]
    sms_phone_number = sms_phone["PhoneNumber"]
    log("✓", f"Found SMS number: {sms_phone_id}")

    # Check registration status
    reg_id = sms_phone.get("RegistrationId")
    reg_status = "UNKNOWN"
    if reg_id:
        try:
            regs = sms.describe_registrations(RegistrationIds=[reg_id])["Registrations"]
            reg_status = regs[0]["RegistrationStatus"] if regs else "UNKNOWN"
            log("ℹ️", f"Registration status: {reg_status}")
        except Exception:
            pass

    if reg_status not in ("COMPLETE", "APPROVED"):
        log("⚠️", f"Toll-free registration not yet approved ({reg_status}).")
        log("", "SMS import into Connect will be attempted but may fail until approved.")
        sms_status = "REG_PENDING"
    else:
        sms_status = "READY"

    # Check if already imported into Connect
    connect_numbers = connect.list_phone_numbers_v2(
        TargetArn=instance_arn, PhoneNumberTypes=["TOLL_FREE"]
    )["ListPhoneNumbersSummaryList"]
    existing_sms = next(
        (n for n in connect_numbers if n.get("PhoneNumber") == sms_phone_number and n["PhoneNumberId"] != voice_phone_id),
        None,
    )

    sms_connect_id = None
    if existing_sms:
        sms_connect_id = existing_sms["PhoneNumberId"]
        log("✓", f"SMS number already imported into Connect: {sms_connect_id}")
    else:
        log("📝", "Importing SMS number into Connect...")
        try:
            account_id = sts.get_caller_identity()["Account"]
            source_arn = f"arn:aws:sms-voice:{REGION}:{account_id}:phone-number/{sms_phone_id}"
            resp = connect.import_phone_number(
                InstanceId=instance_id,
                SourcePhoneNumberArn=source_arn,
                PhoneNumberDescription="SMS channel for tax refund bot",
            )
            sms_connect_id = resp["PhoneNumberId"]
            log("✅", f"Imported: {sms_connect_id}")
        except Exception as e:
            log("⚠️", f"Import failed (registration may still be pending): {e}")

    # Associate with contact flow
    if sms_connect_id and contact_flow_id:
        time.sleep(3)
        try:
            connect.associate_phone_number_contact_flow(
                PhoneNumberId=sms_connect_id,
                InstanceId=instance_id,
                ContactFlowId=contact_flow_id,
            )
            log("✅", f"SMS number ({sms_phone_number}) associated with TaxRefundFlow")
        except Exception as e:
            log("⚠️", f"Flow association failed — retry after registration is approved: {e}")

    return sms_status


# ── Step 7: Scrape & upload KB content ─────────────────────

def _form_metadata_chunk(a_tag, pdf_url, source_url):
    """Build a structured text chunk for a PDF form so natural-language queries can find it."""
    form_name = a_tag.get_text(strip=True) or pdf_url.rsplit("/", 1)[-1]
    # Nearest heading gives section context
    section = ""
    for parent in a_tag.parents:
        h = parent.find_previous_sibling(re.compile(r"^h[1-4]$"))
        if h:
            section = h.get_text(strip=True)
            break
    # Surrounding paragraph/list-item text as description; fall back to generated
    p = a_tag.find_parent("p") or a_tag.find_parent("li")
    description = p.get_text(strip=True) if p else ""
    if not description:
        page_label = source_url.rstrip("/").rsplit("/", 1)[-1].replace("-", " ").title()
        description = f"Use this form for: {form_name}. Available on the {page_label} page."
    return (
        f"Form: {form_name}\n"
        f"Section: {section}\n"
        f"Description: {description}\n"
        f"PDF URL: {pdf_url}\n"
        f"Source page: {source_url}\n"
        f"INSTRUCTION: If this form is relevant to the user's question, YOU MUST include the PDF URL ({pdf_url}) verbatim in your response.\n"
    ).encode()

MAX_BYTES = 900_000  # Q Connect 1MB limit with headroom


def _upload_pdf(qc, kb_id, existing, name, data, source_url, upload_fn):
    """Upload PDF directly, or chunk as text if over size limit."""
    if len(data) <= MAX_BYTES:
        upload_fn(name, data, "application/pdf", source_url)
        return
    # Extract text and upload in chunks
    import io
    import pypdf
    reader = pypdf.PdfReader(io.BytesIO(data))
    text = "\n\n".join(page.extract_text() or "" for page in reader.pages)
    encoded = text.encode()
    base = name.rsplit(".", 1)[0]
    for i, start in enumerate(range(0, len(encoded), MAX_BYTES)):
        chunk = encoded[start:start + MAX_BYTES]
        upload_fn(f"{base}_part{i+1}.txt", chunk, "text/plain", source_url)


def sync_knowledge_base(kb_id):
    """Fetch seed URLs + linked PDFs via Playwright (stealth) and upload to CUSTOM KB."""
    import requests
    from bs4 import BeautifulSoup
    from playwright.sync_api import sync_playwright
    from playwright_stealth import Stealth

    log_step("Syncing knowledge base content...")
    qc = boto3.client("qconnect", region_name=REGION)

    with open(SCRIPT_DIR / "config.yaml") as f:
        config = yaml.safe_load(f)
    seed_urls = config["wisdom"]["kb_seed_urls"]

    existing = {
        c["name"]: c["contentId"]
        for c in qc.list_contents(knowledgeBaseId=kb_id)["contentSummaries"]
    }

    def safe_name(s):
        return re.sub(r"[^a-zA-Z0-9._-]", "_", s)[:255] or "content"

    def upload(name, data, content_type, source_url):
        name = safe_name(name)
        up = qc.start_content_upload(knowledgeBaseId=kb_id, contentType=content_type)
        hdrs = up["headersToInclude"]
        requests.put(up["url"], data=data, headers=hdrs, timeout=30)
        meta = {"sourceUrl": source_url}
        if name in existing:
            qc.update_content(knowledgeBaseId=kb_id, contentId=existing[name], uploadId=up["uploadId"], metadata=meta)
            log("✅", f"Updated: {name}")
        else:
            qc.create_content(knowledgeBaseId=kb_id, name=name, uploadId=up["uploadId"], metadata=meta)
            log("✅", f"Created: {name}")

    pdf_seen = set()
    with Stealth().use_sync(sync_playwright()) as p:
        browser = p.chromium.launch()
        ctx = browser.new_context()
        page = ctx.new_page()

        for url in seed_urls:
            try:
                # Try plain HTTP first (works for server-rendered pages, gets full content)
                r = requests.get(url, timeout=15, headers={
                    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
                    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                    "Accept-Language": "en-US,en;q=0.5",
                })
                if r.status_code == 200 and "enable javascript" not in r.text.lower():
                    html = r.content
                else:
                    # Fall back to Playwright for JS-rendered pages
                    page.goto(url, wait_until="domcontentloaded", timeout=30000)
                    page.wait_for_timeout(3000)
                    page.evaluate("""() => {
                        document.querySelectorAll('details').forEach(d => d.open = true);
                        document.querySelectorAll('[aria-expanded="false"]').forEach(el => el.click());
                        document.querySelectorAll('.coh-accordion-title a').forEach(el => el.click());
                    }""")
                    page.wait_for_timeout(1500)
                    html = page.content().encode()
            except Exception as e:
                log("⚠️", f"Skipped {url}: {e}")
                continue

            page_name = url.rstrip("/").rsplit("/", 1)[-1] or "home"
            soup = BeautifulSoup(html, "html.parser")
            for tag in soup(["nav", "footer", "script", "style", "header"]):
                tag.decompose()

            # For FAQ pages: upload each Q&A as a separate document for better KB retrieval
            accordion_titles = soup.find_all("h4", class_="coh-accordion-title")
            if accordion_titles:
                # Delete old monolithic file if it exists
                if f"{page_name}.txt" in existing:
                    qc.delete_content(knowledgeBaseId=kb_id, contentId=existing[f"{page_name}.txt"])
                    log("🗑️", f"Deleted monolithic: {page_name}.txt")
                for h4 in accordion_titles:
                    question = h4.get_text(strip=True)
                    # Find the sibling content div
                    content_div = h4.find_next_sibling("div", class_="coh-accordion-tabs-content")
                    answer = content_div.get_text(separator="\n", strip=True) if content_div else ""
                    if not answer:
                        continue
                    qa_text = f"Q: {question}\nA: {answer}\n".encode()
                    safe_q = re.sub(r"[^a-zA-Z0-9._-]", "_", question)[:80]
                    upload(f"faq_{safe_q}.txt", qa_text, "text/plain", url)
            else:
                clean_text = soup.get_text(separator="\n", strip=True).encode()
                upload(f"{page_name}.txt", clean_text, "text/plain", url)

            soup = BeautifulSoup(html, "html.parser")
            for a in soup.find_all("a", href=True):
                href = a["href"]
                if not href.lower().endswith(".pdf"):
                    continue
                pdf_url = href if href.startswith("http") else urljoin(url, href)
                if pdf_url in pdf_seen:
                    continue
                pdf_seen.add(pdf_url)
                # Upload structured metadata chunk so NL queries can find this form
                meta = _form_metadata_chunk(a, pdf_url, url)
                upload(f"form_meta_{safe_name(pdf_url.rsplit('/', 1)[-1])}.txt", meta, "text/plain", pdf_url)
                try:
                    r = requests.get(pdf_url, timeout=30)
                    if r.status_code == 200:
                        _upload_pdf(qc, kb_id, existing, pdf_url.rsplit("/", 1)[-1], r.content, pdf_url, upload)
                    else:
                        log("⚠️", f"PDF skipped: {pdf_url} ({r.status_code})")
                except Exception as e:
                    log("⚠️", f"PDF skipped: {pdf_url}: {e}")

        browser.close()


# ── Main ────────────────────────────────────────────────────

def main():
    print("=" * 44)
    print(f"  Post-Deploy Setup for {STACK_NAME}")
    print("=" * 44)

    log_step("Reading stack outputs...")
    outputs = get_stack_outputs()

    bucket = outputs["BucketName"]
    instance_id = outputs["ConnectInstanceId"]
    instance_arn = outputs["ConnectInstanceArn"]
    queue_arn = outputs["QueueArn"]
    assistant_arn = outputs["AssistantArn"]
    bot_alias_arn = outputs["BotAliasArn"]
    gateway_id = outputs["GatewayId"]
    lambda_arn = outputs["LambdaArn"]
    kb_id = outputs["KnowledgeBaseId"]
    assistant_id = assistant_arn.rsplit("/", 1)[-1]
    log("ℹ️", f"Instance:  {instance_id}")
    log("ℹ️", f"Assistant: {assistant_id}")
    log("ℹ️", f"Gateway:   {gateway_id}")

    # Step 1
    upload_refund_data(bucket)

    # Step 2 — KB content
    sync_knowledge_base(kb_id)

    # Step 3
    prompt_id, prompt_version_arn, prompt_version_number = sync_ai_prompt(assistant_id)

    # Step 3
    agent_id, ai_agent_arn = sync_ai_agent(assistant_id, prompt_id, prompt_version_number)

    if not agent_id:
        print()
        print("=" * 44)
        print("  ⚠️  Partial Deploy (prompt created, agent pending)")
        print("=" * 44)
        print(f"  Prompt ARN: {prompt_version_arn or 'N/A'}")
        print(f"  Gateway:    {gateway_id}")
        sys.exit(0)

    # Step 4
    contact_flow_id = sync_contact_flow(
        instance_id, assistant_arn, ai_agent_arn, bot_alias_arn, queue_arn, lambda_arn
    )

    # Step 5
    phone_number_id = setup_phone_number(instance_id, instance_arn, contact_flow_id)

    # Step 6
    sms_status = setup_sms_channel(instance_id, instance_arn, contact_flow_id, phone_number_id)

    # Done
    print()
    print("=" * 44)
    print("  ✅ Post-Deploy Complete")
    print("=" * 44)
    print()
    print(f"  AI Agent ARN: {ai_agent_arn} (latest version)")
    print(f"  Prompt ARN:   {prompt_version_arn or 'N/A'}")
    print(f"  Flow:         {contact_flow_id}")
    print(f"  Gateway:      {gateway_id}")
    print(f"  SMS Status:   {sms_status or 'N/A'}")
    print()

    if sms_status == "REG_PENDING":
        print("  ⚠️  SMS: Toll-free registration is pending. Re-run this script after approval")
        print("     to complete the SMS import and flow association.")
        print()


def flow_only():
    """Deploy only the contact flow."""
    print("=" * 44)
    print(f"  Contact Flow Update for {STACK_NAME}")
    print("=" * 44)

    log_step("Reading stack outputs...")
    outputs = get_stack_outputs()

    instance_id = outputs["ConnectInstanceId"]
    assistant_arn = outputs["AssistantArn"]
    bot_alias_arn = outputs["BotAliasArn"]
    queue_arn = outputs["QueueArn"]
    lambda_arn = outputs["LambdaArn"]
    assistant_id = assistant_arn.rsplit("/", 1)[-1]

    # Resolve AI agent ARN
    qc = boto3.client("qconnect", region_name=REGION)
    agents = qc.list_ai_agents(assistantId=assistant_id)["aiAgentSummaries"]
    agent = next(
        (a for a in agents if a["name"] == AGENT_NAME and a["type"] == "ORCHESTRATION"),
        None,
    )
    if not agent:
        log("❌", f"No ORCHESTRATION agent named '{AGENT_NAME}' found.")
        sys.exit(1)
    ai_agent_arn = agent["aiAgentArn"] + ":$LATEST"
    log("ℹ️", f"Agent ARN: {ai_agent_arn}")

    flow_id = sync_contact_flow(
        instance_id, assistant_arn, ai_agent_arn, bot_alias_arn, queue_arn, lambda_arn
    )
    print(f"\n  ✅ Flow updated: {flow_id}\n")


if __name__ == "__main__":
    if "--flow-only" in sys.argv:
        flow_only()
    else:
        main()
