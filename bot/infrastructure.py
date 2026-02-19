import json
import os
import yaml
from aws_cdk import (
    Stack, Duration, RemovalPolicy, CfnOutput, BundlingOptions, BundlingFileAccess,
    aws_s3 as s3,
    aws_s3_deployment as s3deploy,
    aws_lambda as _lambda,
    aws_iam as iam,
    aws_lex as lex,
    aws_connect as connect,
    aws_wisdom as wisdom,
    aws_logs as logs,
    aws_bedrockagentcore as agentcore,
    aws_apigateway as apigw,
    custom_resources as cr,
)
from constructs import Construct

def load_config():
    with open('config.yaml') as f:
        return yaml.safe_load(f)

class NovaSonicConnectStack(Stack):
    def __init__(self, scope: Construct, id: str, **kwargs) -> None:
        super().__init__(scope, id, **kwargs)

        cfg = load_config()
        proj = cfg['project']['name']

        # S3 bucket
        bucket = s3.Bucket(
            self, "DataBucket",
            bucket_name=f"{proj}-{cfg['s3']['bucket_suffix']}-{self.account}",
            removal_policy=RemovalPolicy.DESTROY,
            block_public_access=s3.BlockPublicAccess.BLOCK_ALL,
        )

        # Lambda role
        role = iam.Role(
            self, "LambdaRole",
            role_name=f"{proj}-lambda-role",
            assumed_by=iam.ServicePrincipal("lambda.amazonaws.com"),
            managed_policies=[iam.ManagedPolicy.from_aws_managed_policy_name("service-role/AWSLambdaBasicExecutionRole")],
        )
        bucket.grant_read(role)
        # SMS sending — scoped to direct phone publish only (no topic ARNs).
        # Invocation chain security: Q in Connect agent → MCP gateway → Lambda.
        role.add_to_policy(iam.PolicyStatement(
            actions=["sns:Publish"],
            resources=["*"],
        ))

        # Lambda environment (UPLOAD_PORTAL_URL added after portal_bucket is created below)
        env = {
            "S3_BUCKET": bucket.bucket_name,
            "DATA_FILE": cfg['s3']['data_file'],
            **cfg['lambda']['environment']
        }

        # Lambda function
        fn = _lambda.Function(
            self, "Function",
            function_name=cfg['lambda']['function_name'],
            runtime=_lambda.Runtime.PYTHON_3_12,
            handler="lambda_function.lambda_handler",
            code=_lambda.Code.from_asset(
                "bot/runtime",
                bundling=BundlingOptions(
                    image=_lambda.Runtime.PYTHON_3_12.bundling_image,
                    platform="linux/amd64",
                    bundling_file_access=BundlingFileAccess.VOLUME_COPY,
                    command=[
                        "bash", "-c",
                        "pip install -r requirements.txt -t /asset-output && cp -au . /asset-output"
                    ],
                ),
            ),
            role=role,
            timeout=Duration.seconds(cfg['lambda']['timeout_seconds']),
            memory_size=cfg['lambda']['memory_mb'],
            environment=env,
        )
        fn.add_permission("LexInvoke", principal=iam.ServicePrincipal("lexv2.amazonaws.com"), source_account=self.account)

        # Lex bot role
        bot_role = iam.Role(self, "BotRole", assumed_by=iam.CompositePrincipal(
            iam.ServicePrincipal("lexv2.amazonaws.com"),
            iam.ServicePrincipal("lexv2.aws.internal"),
        ))
        bot_role.add_to_policy(iam.PolicyStatement(
            actions=["wisdom:*", "qconnect:*"],
            resources=[
                f"arn:aws:wisdom:{self.region}:{self.account}:*",
                f"arn:aws:qconnect:{self.region}:{self.account}:*",
            ],
        ))
        bot_role.add_to_policy(iam.PolicyStatement(
            actions=["bedrock:InvokeModel", "bedrock:InvokeModelWithResponseStream"],
            resources=["*"],
        ))

        # Wisdom (Q in Connect) Assistant
        assistant = wisdom.CfnAssistant(
            self, "Assistant",
            name=cfg['wisdom']['assistant_name'],
            type="AGENT",
        )

        # --- Task 7: Website Q&A Knowledge Base (web crawler) ---
        kb = wisdom.CfnKnowledgeBase(
            self, "WebsiteKB",
            name=f"{proj}-auditor-website",
            knowledge_base_type="MANAGED",
            description="Riverside County Auditor-Controller website content for general Q&A",
            source_configuration={
                "managedSourceConfiguration": {
                    "webCrawlerConfiguration": {
                        "urlConfiguration": {
                            "seedUrls": [{"url": "https://auditorcontroller.org/"}]
                        },
                        "scope": "HOST_ONLY",
                        "crawlerLimits": {"rateLimit": 10},
                        "inclusionFilters": [".*auditorcontroller\\.org.*"],
                    }
                }
            },
        )

        kb_assoc = wisdom.CfnAssistantAssociation(
            self, "WebsiteKBAssociation",
            assistant_id=assistant.attr_assistant_id,
            association_type="KNOWLEDGE_BASE",
            association={"knowledgeBaseId": kb.attr_knowledge_base_id},
        )

        # --- Task 5: Shared intent definitions for both locales ---
        def make_intents(assistant_arn):
            return [
                {
                    "name": "FallbackIntent",
                    "parentIntentSignature": "AMAZON.FallbackIntent",
                    "initialResponseSetting": {
                        "nextStep": {"dialogAction": {"type": "InvokeDialogCodeHook"}},
                        "codeHook": {
                            "enableCodeHookInvocation": True,
                            "isActive": True,
                            "postCodeHookSpecification": {
                                "successNextStep": {"dialogAction": {"type": "EndConversation"}},
                                "failureNextStep": {"dialogAction": {"type": "EndConversation"}},
                                "timeoutNextStep": {"dialogAction": {"type": "EndConversation"}},
                            }
                        }
                    },
                },
                {
                    "name": "QInConnectIntent",
                    "parentIntentSignature": "AMAZON.QInConnectIntent",
                    "dialogCodeHook": {"enabled": True},
                    "fulfillmentCodeHook": {
                        "enabled": False,
                        "isActive": True,
                        "postFulfillmentStatusSpecification": {
                            "successResponse": {
                                "messageGroupsList": [{
                                    "message": {
                                        "plainTextMessage": {"value": "((x-amz-lex:q-in-connect-response))"}
                                    }
                                }],
                                "allowInterrupt": True,
                            },
                            "successNextStep": {"dialogAction": {"type": "EndConversation"}},
                            "failureNextStep": {"dialogAction": {"type": "EndConversation"}},
                            "timeoutNextStep": {"dialogAction": {"type": "EndConversation"}},
                        }
                    },
                    "qInConnectIntentConfiguration": {
                        "qInConnectAssistantConfiguration": {
                            "assistantArn": assistant_arn
                        }
                    },
                }
            ]

        nlu = cfg['lex']['nlu_threshold']

        # Lex bot — en_US + es_US locales
        bot = lex.CfnBot(
            self, "Bot",
            name=cfg['lex']['bot_name'],
            role_arn=bot_role.role_arn,
            data_privacy={"ChildDirected": False},
            idle_session_ttl_in_seconds=300,
            auto_build_bot_locales=True,
            bot_locales=[
                {
                    "localeId": "en_US",
                    "nluConfidenceThreshold": nlu,
                    "intents": make_intents(assistant.attr_assistant_arn),
                },
                {
                    "localeId": "es_US",
                    "nluConfidenceThreshold": nlu,
                    "intents": make_intents(assistant.attr_assistant_arn),
                },
            ],
        )
        bot.add_dependency(assistant)

        # Bot version — both locales
        bot_version = lex.CfnBotVersion(
            self, "BotVersion",
            bot_id=bot.attr_id,
            bot_version_locale_specification=[
                {"localeId": "en_US", "botVersionLocaleDetails": {"sourceBotVersion": "DRAFT"}},
                {"localeId": "es_US", "botVersionLocaleDetails": {"sourceBotVersion": "DRAFT"}},
            ],
        )

        # CloudWatch log group for Lex conversation logs
        lex_log_group = logs.LogGroup(
            self, "LexLogGroup",
            log_group_name=f"/aws/lex/{cfg['lex']['bot_name']}",
            removal_policy=RemovalPolicy.DESTROY,
        )

        # Bot alias — both locales with Lambda code hooks
        lambda_hook = {
            "enabled": True,
            "codeHookSpecification": {
                "lambdaCodeHook": {
                    "lambdaArn": fn.function_arn,
                    "codeHookInterfaceVersion": "1.0",
                }
            }
        }

        alias = lex.CfnBotAlias(
            self, "BotAlias",
            bot_alias_name=cfg['lex']['alias_name'],
            bot_id=bot.attr_id,
            bot_version=bot_version.attr_bot_version,
            bot_alias_locale_settings=[
                {"localeId": "en_US", "botAliasLocaleSetting": lambda_hook},
                {"localeId": "es_US", "botAliasLocaleSetting": lambda_hook},
            ],
            conversation_log_settings=lex.CfnBotAlias.ConversationLogSettingsProperty(
                text_log_settings=[lex.CfnBotAlias.TextLogSettingProperty(
                    enabled=True,
                    destination=lex.CfnBotAlias.TextLogDestinationProperty(
                        cloud_watch=lex.CfnBotAlias.CloudWatchLogGroupLogDestinationProperty(
                            cloud_watch_log_group_arn=lex_log_group.log_group_arn,
                            log_prefix="conversation",
                        )
                    ),
                )],
            ),
        )
        alias.add_dependency(bot_version)

        # Connect instance
        instance = connect.CfnInstance(
            self, "ConnectInstance",
            instance_alias=proj,
            identity_management_type="CONNECT_MANAGED",
            attributes=connect.CfnInstance.AttributesProperty(
                inbound_calls=True,
                outbound_calls=True,
                contactflow_logs=True,
            ),
        )

        # --- Task 4: Live Agent Handoff — Hours, Queue, Routing Profile ---
        hours = connect.CfnHoursOfOperation(
            self, "HoursOfOperation",
            instance_arn=instance.attr_arn,
            name="TaxRefundHours",
            time_zone="America/Los_Angeles",
            config=[{
                "day": day,
                "startTime": {"hours": 8, "minutes": 0},
                "endTime": {"hours": 17, "minutes": 0},
            } for day in ["MONDAY", "TUESDAY", "WEDNESDAY", "THURSDAY", "FRIDAY"]],
        )

        queue = connect.CfnQueue(
            self, "LiveAgentQueue",
            instance_arn=instance.attr_arn,
            name="TaxRefundLiveAgents",
            hours_of_operation_arn=hours.attr_hours_of_operation_arn,
            description="Queue for live agent handoff from tax refund bot",
        )

        routing_profile = connect.CfnRoutingProfile(
            self, "AgentRoutingProfile",
            instance_arn=instance.attr_arn,
            name="TaxRefundAgentProfile",
            description="Routing profile for tax refund live agents",
            default_outbound_queue_arn=queue.attr_queue_arn,
            media_concurrencies=[
                {"channel": "VOICE", "concurrency": 1},
                {"channel": "CHAT", "concurrency": 5},
            ],
            queue_configs=[
                {
                    "delay": 0,
                    "priority": 1,
                    "queueReference": {
                        "channel": "VOICE",
                        "queueArn": queue.attr_queue_arn,
                    },
                },
                {
                    "delay": 0,
                    "priority": 1,
                    "queueReference": {
                        "channel": "CHAT",
                        "queueArn": queue.attr_queue_arn,
                    },
                },
            ],
        )

        # Associate Lambda with Connect instance (required for Lex code hooks within Connect)
        lambda_assoc = connect.CfnIntegrationAssociation(
            self, "LambdaAssociation",
            instance_id=instance.attr_arn,
            integration_type="LAMBDA_FUNCTION",
            integration_arn=fn.function_arn,
        )

        # Associate Wisdom assistant with Connect via custom resource
        # (WISDOM_ASSISTANT is not a valid IntegrationType for CfnIntegrationAssociation)
        wisdom_assoc_cr = cr.AwsCustomResource(
            self, "WisdomAssociation",
            on_create=cr.AwsSdkCall(
                service="Connect",
                action="createIntegrationAssociation",
                parameters={
                    "InstanceId": instance.attr_id,
                    "IntegrationType": "WISDOM_ASSISTANT",
                    "IntegrationArn": assistant.attr_assistant_arn,
                },
                physical_resource_id=cr.PhysicalResourceId.from_response("IntegrationAssociationId"),
            ),
            on_delete=cr.AwsSdkCall(
                service="Connect",
                action="deleteIntegrationAssociation",
                parameters={
                    "InstanceId": instance.attr_id,
                    "IntegrationAssociationId": cr.PhysicalResourceIdReference(),
                },
            ),
            policy=cr.AwsCustomResourcePolicy.from_statements([
                iam.PolicyStatement(
                    actions=[
                        "connect:CreateIntegrationAssociation",
                        "connect:DeleteIntegrationAssociation",
                        "connect:ListIntegrationAssociations",
                    ],
                    resources=[instance.attr_arn, f"{instance.attr_arn}/*"],
                ),
                iam.PolicyStatement(
                    actions=["wisdom:GetAssistant", "wisdom:TagResource"],
                    resources=[assistant.attr_assistant_arn],
                ),
            ]),
        )

        # Contact flow ARNs needed for post-deploy script
        bot_alias_arn = f"arn:aws:lex:{self.region}:{self.account}:bot-alias/{bot.attr_id}/{alias.attr_bot_alias_id}"

        # Associate Lex bot with Connect instance (required for ConnectParticipantWithLexBot)
        lex_assoc = connect.CfnIntegrationAssociation(
            self, "LexBotAssociation",
            instance_id=instance.attr_arn,
            integration_type="LEX_BOT",
            integration_arn=bot_alias_arn,
        )

        # --- AgentCore MCP Gateway for tax_lookup tool ---
        gateway_role = iam.Role(
            self, "GatewayRole",
            role_name=f"{proj}-gateway-role",
            assumed_by=iam.ServicePrincipal("bedrock-agentcore.amazonaws.com"),
        )
        fn.grant_invoke(gateway_role)

        # Load tool schemas from file
        with open("bot/tool-schema.json") as f:
            tool_schemas = json.load(f)

        gateway = agentcore.CfnGateway(
            self, "McpGateway",
            name=f"{proj}-mcp-gateway",
            protocol_type="MCP",
            authorizer_type="NONE",
            role_arn=gateway_role.role_arn,
            description="MCP gateway for tax refund lookup tool",
        )

        gateway_target = agentcore.CfnGatewayTarget(
            self, "McpGatewayTarget",
            gateway_identifier=gateway.attr_gateway_identifier,
            name="tax-lookup-target",
            target_configuration={
                "mcp": {
                    "lambda": {
                        "lambdaArn": fn.function_arn,
                        "toolSchema": {
                            "inlinePayload": tool_schemas,
                        },
                    }
                }
            },
            credential_provider_configurations=[{
                "credentialProviderType": "GATEWAY_IAM_ROLE",
            }],
        )

        CfnOutput(self, "GatewayArn", value=gateway.attr_gateway_arn)
        CfnOutput(self, "GatewayId", value=gateway.attr_gateway_identifier)
        CfnOutput(self, "GatewayUrl", value=gateway.attr_gateway_url)

        CfnOutput(self, "BucketName", value=bucket.bucket_name)
        CfnOutput(self, "LambdaArn", value=fn.function_arn)
        CfnOutput(self, "BotId", value=bot.attr_id)
        CfnOutput(self, "BotAliasId", value=alias.attr_bot_alias_id)
        CfnOutput(self, "BotAliasArn", value=bot_alias_arn)
        CfnOutput(self, "AssistantArn", value=assistant.attr_assistant_arn)
        CfnOutput(self, "ConnectInstanceId", value=instance.attr_id)
        CfnOutput(self, "ConnectInstanceArn", value=instance.attr_arn)
        CfnOutput(self, "QueueArn", value=queue.attr_queue_arn)
        CfnOutput(self, "RoutingProfileArn", value=routing_profile.attr_routing_profile_arn)
        CfnOutput(self, "KnowledgeBaseId", value=kb.attr_knowledge_base_id)

        # --- Task 10: Secure Document Upload Portal ---

        # S3 bucket for uploaded documents (encrypted, lifecycle, no public access)
        uploads_bucket = s3.Bucket(
            self, "UploadsBucket",
            bucket_name=f"{proj}-uploads-{self.account}",
            encryption=s3.BucketEncryption.S3_MANAGED,
            block_public_access=s3.BlockPublicAccess.BLOCK_ALL,
            removal_policy=RemovalPolicy.DESTROY,
            auto_delete_objects=True,
            lifecycle_rules=[s3.LifecycleRule(expiration=Duration.days(90))],
            cors=[s3.CorsRule(
                allowed_methods=[s3.HttpMethods.PUT],
                allowed_origins=["*"],
                allowed_headers=["*"],
                max_age=3600,
            )],
        )

        # Lambda for presigned URL generation
        upload_fn = _lambda.Function(
            self, "UploadHandler",
            function_name=f"{proj}-upload-handler",
            runtime=_lambda.Runtime.PYTHON_3_12,
            handler="lambda_function.lambda_handler",
            code=_lambda.Code.from_asset("bot/upload_handler"),
            timeout=Duration.seconds(10),
            memory_size=128,
            environment={
                "UPLOAD_BUCKET": uploads_bucket.bucket_name,
                "UPLOAD_PASSWORD": os.environ.get("UPLOAD_PASSWORD", ""),
            },
        )
        uploads_bucket.grant_put(upload_fn)

        # API Gateway REST API
        upload_api = apigw.RestApi(
            self, "UploadApi",
            rest_api_name=f"{proj}-upload-api",
            default_cors_preflight_options=apigw.CorsOptions(
                allow_origins=apigw.Cors.ALL_ORIGINS,
                allow_methods=["POST", "OPTIONS"],
                allow_headers=["Content-Type"],
            ),
            deploy_options=apigw.StageOptions(
                throttling_rate_limit=2,
                throttling_burst_limit=5,
            ),
        )
        upload_api.root.add_resource("upload").add_method(
            "POST", apigw.LambdaIntegration(upload_fn),
        )

        # S3 bucket for static portal site (public website hosting)
        portal_bucket = s3.Bucket(
            self, "PortalBucket",
            bucket_name=f"{proj}-portal-{self.account}",
            removal_policy=RemovalPolicy.DESTROY,
            auto_delete_objects=True,
            website_index_document="index.html",
            public_read_access=True,
            block_public_access=s3.BlockPublicAccess(
                block_public_acls=False,
                block_public_policy=False,
                ignore_public_acls=False,
                restrict_public_buckets=False,
            ),
        )

        s3deploy.BucketDeployment(
            self, "PortalDeployment",
            sources=[s3deploy.Source.asset("bot/upload_portal")],
            destination_bucket=portal_bucket,
        )

        config_js = f'window.API_URL = "{upload_api.url.rstrip("/")}";\n'
        s3deploy.BucketDeployment(
            self, "PortalConfig",
            sources=[s3deploy.Source.data("config.js", config_js)],
            destination_bucket=portal_bucket,
            prune=False,
        )

        # Wire upload portal URL into main Lambda so the bot can reference it
        fn.add_environment("UPLOAD_PORTAL_URL", portal_bucket.bucket_website_url)

        CfnOutput(self, "UploadPortalUrl", value=portal_bucket.bucket_website_url)
        CfnOutput(self, "UploadApiUrl", value=upload_api.url)
        CfnOutput(self, "UploadsBucketName", value=uploads_bucket.bucket_name)
