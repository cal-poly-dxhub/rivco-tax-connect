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
import sys
import time
from pathlib import Path

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
        try:
            qc.update_ai_prompt(
                assistantId=assistant_id,
                aiPromptId=prompt_id,
                visibilityStatus="PUBLISHED",
                templateConfiguration=template_cfg,
            )
            log("✅", "Updated")
        except Exception as e:
            log("⚠️", f"Update failed (prompt may be unchanged): {e}")
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

    # Get current config and update the prompt reference
    if prompt_id and prompt_version_number:
        full_agent = qc.get_ai_agent(assistantId=assistant_id, aiAgentId=agent_id)
        config = full_agent["aiAgent"]["configuration"]

        orch_cfg = config.get("orchestrationAIAgentConfiguration", {})
        new_prompt_ref = f"{prompt_id}:{prompt_version_number}"
        old_prompt_ref = orch_cfg.get("orchestrationAIPromptId", "")

        if old_prompt_ref != new_prompt_ref:
            log("📝", f"Updating agent prompt: {old_prompt_ref} → {new_prompt_ref}")
            orch_cfg["orchestrationAIPromptId"] = new_prompt_ref
            try:
                qc.update_ai_agent(
                    assistantId=assistant_id,
                    aiAgentId=agent_id,
                    visibilityStatus="PUBLISHED",
                    configuration={"orchestrationAIAgentConfiguration": orch_cfg},
                )
                log("✅", "Agent prompt updated")
            except Exception as e:
                log("⚠️", f"Agent update failed: {e}")
        else:
            log("✓", "Agent already using latest prompt version")

    # Version the agent
    try:
        ver = qc.create_ai_agent_version(assistantId=assistant_id, aiAgentId=agent_id)
        versioned_arn = ver["aiAgent"]["aiAgentArn"]
        log("✅", f"Published version: {versioned_arn}")
        # Use $LATEST qualifier
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
        connect.update_contact_flow_content(
            InstanceId=instance_id, ContactFlowId=flow_id, Content=content
        )
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
    assistant_id = assistant_arn.rsplit("/", 1)[-1]

    log("ℹ️", f"Instance:  {instance_id}")
    log("ℹ️", f"Assistant: {assistant_id}")
    log("ℹ️", f"Gateway:   {gateway_id}")

    # Step 1
    upload_refund_data(bucket)

    # Step 2
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


if __name__ == "__main__":
    main()
