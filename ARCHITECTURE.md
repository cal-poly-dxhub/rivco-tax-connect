# Architecture

```mermaid
flowchart TB
    subgraph Customer
        Voice["📞 Voice Call"]
        Chat["💬 Web Chat"]
        SMS["📱 SMS"]
    end

    subgraph Connect["Amazon Connect"]
        Instance["Connect Instance"]
        Flow["TaxRefundFlow"]
        LangSelect{"Language?"}
        Queue["TaxRefundLiveAgents Queue"]
        Agent["Live Agent"]
    end

    subgraph Lex["Amazon Lex"]
        Bot["TaxRefundBot"]
        EN["en_US Locale"]
        ES["es_US Locale"]
        Fallback["FallbackIntent → Lambda"]
        QIntent["QInConnectIntent"]
    end

    subgraph QConnect["Q in Connect"]
        Assistant["Wisdom Assistant"]
        AIAgent["Orchestration AI Agent"]
        Prompt["AI Orchestration Prompt"]
        KB["Website Knowledge Base\n(Web Crawler → auditorcontroller.org)"]
    end

    subgraph AgentCore["Bedrock AgentCore"]
        Gateway["MCP Gateway"]
    end

    subgraph Backend["Backend"]
        Lambda["Lambda\n(riverside-tax-lookup)"]
        S3Data["S3 — Refund Data\n(.jsonl)"]
        SNS["SNS — SMS Delivery"]
    end

    subgraph Upload["Document Upload Portal"]
        Portal["S3 Static Website\n(index.html)"]
        APIGW["API Gateway"]
        UploadFn["Upload Handler Lambda"]
        UploadS3["S3 — Encrypted Uploads\n(90-day lifecycle)"]
    end

    Voice --> Instance
    Chat --> Instance
    SMS --> Instance
    Instance --> Flow
    Flow --> LangSelect
    LangSelect -->|English| EN
    LangSelect -->|Spanish| ES
    LangSelect -->|Chat/SMS| Bot
    EN --> Bot
    ES --> Bot
    Bot --> QIntent
    Bot --> Fallback
    QIntent --> AIAgent
    Fallback --> Lambda
    AIAgent --> Prompt
    AIAgent --> KB
    AIAgent --> Gateway
    Gateway --> Lambda
    Lambda --> S3Data
    Lambda --> SNS
    Flow -->|"transferToAgent=true"| Queue --> Agent

    Lambda -.->|"claim_url + upload_portal_url"| Portal
    Portal --> APIGW --> UploadFn --> UploadS3
```

## Component Summary

| Component | Purpose |
|-----------|---------|
| Amazon Connect | Omnichannel contact center (voice, chat, SMS) |
| Amazon Lex | NLU bot with en_US and es_US locales |
| Q in Connect | Orchestration AI agent with tool use |
| Bedrock AgentCore | MCP gateway exposing `tax_lookup` and `send_sms` tools |
| Lambda (tax-lookup) | Fuzzy name matching against refund data, SMS sending via SNS |
| S3 (data) | JSONL refund records with deadline filtering |
| Knowledge Base | Web crawler indexing auditorcontroller.org for general Q&A |
| Upload Portal | Static site + API Gateway + Lambda for presigned S3 uploads |
| SNS | Sends claim form links via SMS to voice callers |
| Connect Queue | Live agent handoff when bot can't help or user is frustrated |
