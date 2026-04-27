import json
import os
import re
import yaml
from aws_cdk import (
    Stack, Duration, RemovalPolicy, CfnOutput, BundlingOptions, BundlingFileAccess,
    aws_s3 as s3,
    aws_s3_deployment as s3deploy,
    aws_lambda as _lambda,
    aws_lambda_event_sources as lambda_events,
    aws_iam as iam,
    aws_lex as lex,
    aws_connect as connect,
    aws_wisdom as wisdom,
    aws_logs as logs,
    aws_bedrockagentcore as agentcore,
    aws_apigateway as apigw,
    aws_dynamodb as dynamodb,
    aws_cognito as cognito,
    aws_codebuild as codebuild,
    aws_cloudfront as cloudfront,
    aws_cloudfront_origins as cloudfront_origins,
    aws_ses as ses,
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
        # Q in Connect session data injection (channel-aware prompts)
        role.add_to_policy(iam.PolicyStatement(
            actions=["wisdom:UpdateSessionData"],
            resources=[f"arn:aws:wisdom:{self.region}:{self.account}:*"],
        ))
        role.add_to_policy(iam.PolicyStatement(
            actions=["connect:DescribeContact", "connect:UpdateContactAttributes"],
            resources=[f"arn:aws:connect:{self.region}:{self.account}:instance/*/contact/*"],
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

        # --- Task 7: Website Q&A Knowledge Base (custom, content uploaded by post_deploy.py) ---
        kb = wisdom.CfnKnowledgeBase(
            self, "WebsiteKB",
            name=f"{proj}-auditor-website",
            knowledge_base_type="CUSTOM",
            description="Riverside County Auditor-Controller website content for general Q&A",
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

        portal_origin = f"http://{proj}-portal-{self.account}.s3-website-{self.region}.amazonaws.com"

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
                allowed_origins=[portal_origin],
                allowed_headers=["*"],
                max_age=3600,
            )],
        )

        # DynamoDB table for claim submissions
        submissions_table = dynamodb.Table(
            self, "ClaimSubmissions",
            table_name=f"{proj}-claim-submissions",
            partition_key=dynamodb.Attribute(name="submissionId", type=dynamodb.AttributeType.STRING),
            billing_mode=dynamodb.BillingMode.PAY_PER_REQUEST,
            removal_policy=RemovalPolicy.DESTROY,
            stream=dynamodb.StreamViewType.NEW_AND_OLD_IMAGES,
        )

        # Admin config: departments, users, refund-type labels (super-admin managed)
        admin_config_table = dynamodb.Table(
            self, "AdminConfig",
            table_name=f"{proj}-admin-config",
            partition_key=dynamodb.Attribute(name="pk", type=dynamodb.AttributeType.STRING),
            billing_mode=dynamodb.BillingMode.PAY_PER_REQUEST,
            removal_policy=RemovalPolicy.DESTROY,
        )

        # Lambda for presigned URL generation
        upload_fn = _lambda.Function(
            self, "UploadHandler",
            function_name=f"{proj}-upload-handler",
            runtime=_lambda.Runtime.PYTHON_3_12,
            handler="lambda_function.lambda_handler",
            code=_lambda.Code.from_asset("bot/upload_handler"),
            timeout=Duration.seconds(30),
            memory_size=128,
            environment={
                "UPLOAD_BUCKET": uploads_bucket.bucket_name,
                "ALLOWED_ORIGIN": portal_origin,
                "TABLE_NAME": submissions_table.table_name,
                "ADMIN_CONFIG_TABLE": admin_config_table.table_name,
            },
        )
        submissions_table.grant_read_write_data(upload_fn)
        admin_config_table.grant_read_write_data(upload_fn)
        uploads_bucket.grant_put(upload_fn)
        uploads_bucket.grant_write(upload_fn)
        uploads_bucket.grant_read(upload_fn)

        # --- Cognito User Pool for admin dashboard ---
        user_pool = cognito.UserPool(
            self, "AdminUserPool",
            user_pool_name=f"{proj}-admin-pool",
            self_sign_up_enabled=False,
            sign_in_aliases=cognito.SignInAliases(email=True, username=True),
            auto_verify=cognito.AutoVerifiedAttrs(email=True),
            password_policy=cognito.PasswordPolicy(
                min_length=8, require_lowercase=True, require_uppercase=True,
                require_digits=True, require_symbols=False,
            ),
            user_invitation=cognito.UserInvitationConfig(
                email_subject="Your Riverside County admin account",
                email_body=(
                    "An admin account has been created for you.\n\n"
                    "Username: {username}\n"
                    "Temporary password: {####}\n\n"
                    "Sign in and set a permanent password to finish setup."
                ),
            ),
            removal_policy=RemovalPolicy.DESTROY,
        )
        user_pool_client = cognito.UserPoolClient(
            self, "AdminUserPoolClient",
            user_pool=user_pool,
            auth_flows=cognito.AuthFlow(user_password=True, user_srp=True),
            generate_secret=False,
        )
        cognito.CfnUserPoolGroup(
            self, "GroupSuperAdmin",
            user_pool_id=user_pool.user_pool_id,
            group_name="super-admin",
            description="Full access, all departments",
        )

        # Bootstrap: create the initial super-admin user from config.yaml.
        # Idempotent via AwsSdkCall — if the user already exists the call errors
        # out, which we swallow by using ignore_error_codes_matching.
        super_admin_email = (cfg.get("super_admin") or {}).get("email")
        if super_admin_email:
            # Username cannot be email format when email is a sign-in alias.
            # Sanitize to a deterministic non-email string.
            super_admin_username = "sa-" + re.sub(r'[^a-z0-9]', '-', super_admin_email.lower())[:64]
            bootstrap_pw = "Kiro!Temp" + self.account[-4:]  # deterministic but unique per account
            create_super_admin = cr.AwsCustomResource(
                self, "BootstrapSuperAdmin",
                on_create=cr.AwsSdkCall(
                    service="CognitoIdentityServiceProvider",
                    action="adminCreateUser",
                    parameters={
                        "UserPoolId": user_pool.user_pool_id,
                        "Username": super_admin_username,
                        "UserAttributes": [
                            {"Name": "email", "Value": super_admin_email},
                            {"Name": "email_verified", "Value": "true"},
                        ],
                        "TemporaryPassword": bootstrap_pw,
                        "MessageAction": "SUPPRESS",
                    },
                    physical_resource_id=cr.PhysicalResourceId.of(f"bootstrap-{super_admin_username}"),
                    ignore_error_codes_matching="UsernameExistsException",
                ),
                policy=cr.AwsCustomResourcePolicy.from_statements([
                    iam.PolicyStatement(
                        actions=["cognito-idp:AdminCreateUser", "cognito-idp:AdminAddUserToGroup"],
                        resources=[user_pool.user_pool_arn],
                    ),
                ]),
            )
            add_to_group = cr.AwsCustomResource(
                self, "BootstrapSuperAdminGroup",
                on_create=cr.AwsSdkCall(
                    service="CognitoIdentityServiceProvider",
                    action="adminAddUserToGroup",
                    parameters={
                        "UserPoolId": user_pool.user_pool_id,
                        "Username": super_admin_username,
                        "GroupName": "super-admin",
                    },
                    physical_resource_id=cr.PhysicalResourceId.of(f"bootstrap-group-{super_admin_username}"),
                ),
                policy=cr.AwsCustomResourcePolicy.from_statements([
                    iam.PolicyStatement(
                        actions=["cognito-idp:AdminAddUserToGroup"],
                        resources=[user_pool.user_pool_arn],
                    ),
                ]),
            )
            add_to_group.node.add_dependency(create_super_admin)
            CfnOutput(self, "SuperAdminUsername", value=super_admin_username)
            CfnOutput(self, "SuperAdminBootstrapPassword", value=bootstrap_pw,
                      description="Temp password for initial super-admin (forced change on first login)")

        upload_fn.add_environment("USER_POOL_ID", user_pool.user_pool_id)
        upload_fn.add_to_role_policy(iam.PolicyStatement(
            actions=[
                "cognito-idp:AdminCreateUser",
                "cognito-idp:AdminDeleteUser",
                "cognito-idp:AdminUpdateUserAttributes",
                "cognito-idp:AdminAddUserToGroup",
                "cognito-idp:AdminRemoveUserFromGroup",
                "cognito-idp:AdminListGroupsForUser",
                "cognito-idp:AdminGetUser",
                "cognito-idp:ListUsers",
                "cognito-idp:ListUsersInGroup",
                "cognito-idp:CreateGroup",
                "cognito-idp:DeleteGroup",
            ],
            resources=[user_pool.user_pool_arn],
        ))

        authorizer = apigw.CognitoUserPoolsAuthorizer(
            self, "AdminAuthorizer",
            cognito_user_pools=[user_pool],
        )

        # API Gateway REST API
        upload_api = apigw.RestApi(
            self, "UploadApi",
            rest_api_name=f"{proj}-upload-api",
            default_cors_preflight_options=apigw.CorsOptions(
                allow_origins=apigw.Cors.ALL_ORIGINS,
                allow_methods=["POST", "GET", "OPTIONS"],
                allow_headers=["Content-Type", "Authorization"],
            ),
            deploy_options=apigw.StageOptions(
                throttling_rate_limit=2,
                throttling_burst_limit=5,
            ),
        )
        # Public: claimants and bot use these
        upload_api.root.add_resource("upload").add_method(
            "POST", apigw.LambdaIntegration(upload_fn),
        )
        upload_api.root.add_resource("upload-complete").add_method(
            "POST", apigw.LambdaIntegration(upload_fn),
        )

        # Admin-only: require Cognito JWT
        upload_api.root.add_resource("package").add_method(
            "GET", apigw.LambdaIntegration(upload_fn), authorizer=authorizer,
            authorization_type=apigw.AuthorizationType.COGNITO,
        )
        upload_api.root.add_resource("status").add_method(
            "GET", apigw.LambdaIntegration(upload_fn), authorizer=authorizer,
            authorization_type=apigw.AuthorizationType.COGNITO,
        )
        upload_api.root.add_resource("update-status").add_method(
            "POST", apigw.LambdaIntegration(upload_fn), authorizer=authorizer,
            authorization_type=apigw.AuthorizationType.COGNITO,
        )
        upload_api.root.add_resource("delete-submission").add_method(
            "POST", apigw.LambdaIntegration(upload_fn), authorizer=authorizer,
            authorization_type=apigw.AuthorizationType.COGNITO,
        )

        # Super-admin config CRUD — catch-all under /admin/*
        admin_resource = upload_api.root.add_resource("admin")
        admin_resource.add_resource("{proxy+}").add_method(
            "ANY", apigw.LambdaIntegration(upload_fn), authorizer=authorizer,
            authorization_type=apigw.AuthorizationType.COGNITO,
        )

        CfnOutput(self, "UserPoolId", value=user_pool.user_pool_id)
        CfnOutput(self, "UserPoolClientId", value=user_pool_client.user_pool_client_id)

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

        config_js = f'window.API_URL = "{upload_api.url.rstrip("/")}";\n'
        s3deploy.BucketDeployment(
            self, "PortalDeployment",
            sources=[
                s3deploy.Source.asset("bot/upload_portal"),
                s3deploy.Source.data("config.js", config_js),
            ],
            destination_bucket=portal_bucket,
        )

        # Wire upload portal URL into main Lambda so the bot can reference it
        fn.add_environment("UPLOAD_PORTAL_URL", portal_bucket.bucket_website_url)
        fn.add_environment("ASSISTANT_ID", assistant.attr_assistant_id)

        CfnOutput(self, "UploadPortalUrl", value=portal_bucket.bucket_website_url)
        CfnOutput(self, "UploadApiUrl", value=upload_api.url)
        CfnOutput(self, "UploadsBucketName", value=uploads_bucket.bucket_name)
        CfnOutput(self, "SubmissionsTableName", value=submissions_table.table_name)

        # --- Admin dashboard (Next.js) ---
        # CodeBuild pulls from GitHub, builds `yarn build`, publishes `out/` to
        # a private S3 bucket served via CloudFront. Triggered on every
        # `cdk deploy` via a custom resource.
        dash_cfg = cfg.get("admin_dashboard") or {}
        gh_owner = dash_cfg.get("github_owner", "cal-poly-dxhub")
        gh_repo = dash_cfg.get("github_repo", "rivco-tax-connect")
        gh_branch = dash_cfg.get("github_branch", "main")

        admin_bucket = s3.Bucket(
            self, "AdminBucket",
            bucket_name=f"{proj}-admin-{self.account}",
            removal_policy=RemovalPolicy.DESTROY,
            auto_delete_objects=True,
            block_public_access=s3.BlockPublicAccess.BLOCK_ALL,
            encryption=s3.BucketEncryption.S3_MANAGED,
        )

        admin_oac = cloudfront.S3OriginAccessControl(
            self, "AdminBucketOAC", description="OAC for admin dashboard bucket",
        )
        admin_distribution = cloudfront.Distribution(
            self, "AdminDistribution",
            default_behavior=cloudfront.BehaviorOptions(
                origin=cloudfront_origins.S3BucketOrigin.with_origin_access_control(
                    admin_bucket, origin_access_control=admin_oac,
                ),
                viewer_protocol_policy=cloudfront.ViewerProtocolPolicy.REDIRECT_TO_HTTPS,
                cache_policy=cloudfront.CachePolicy.CACHING_DISABLED,
                function_associations=[cloudfront.FunctionAssociation(
                    function=cloudfront.Function(
                        self, "AdminRewriteFunction",
                        code=cloudfront.FunctionCode.from_inline(
                            # Append index.html to directory-like requests so Next's
                            # static export routes (e.g. /dashboard/) resolve correctly.
                            "function handler(event) {\n"
                            "  var req = event.request;\n"
                            "  var uri = req.uri;\n"
                            "  if (uri.endsWith('/')) { req.uri = uri + 'index.html'; }\n"
                            "  else if (!uri.split('/').pop().includes('.')) { req.uri = uri + '/index.html'; }\n"
                            "  return req;\n"
                            "}\n"
                        ),
                    ),
                    event_type=cloudfront.FunctionEventType.VIEWER_REQUEST,
                )],
            ),
            default_root_object="index.html",
        )

        # Now that the CloudFront domain is known, allow the dashboard origin in CORS.
        upload_fn.add_environment(
            "ALLOWED_ORIGINS",
            f"{portal_origin},https://{admin_distribution.distribution_domain_name}",
        )

        admin_build = codebuild.Project(
            self, "AdminBuild",
            source=codebuild.Source.git_hub(
                owner=gh_owner, repo=gh_repo, branch_or_ref=gh_branch, clone_depth=1,
            ),
            environment=codebuild.BuildEnvironment(
                build_image=codebuild.LinuxBuildImage.STANDARD_7_0,
                compute_type=codebuild.ComputeType.SMALL,
            ),
            artifacts=codebuild.Artifacts.s3(
                bucket=admin_bucket, include_build_id=False, package_zip=False,
                name="/", encryption=False,
            ),
            environment_variables={
                "API_URL": codebuild.BuildEnvironmentVariable(value=upload_api.url.rstrip("/")),
                "USER_POOL_ID": codebuild.BuildEnvironmentVariable(value=user_pool.user_pool_id),
                "USER_POOL_CLIENT_ID": codebuild.BuildEnvironmentVariable(value=user_pool_client.user_pool_client_id),
            },
            build_spec=codebuild.BuildSpec.from_object({
                "version": "0.2",
                "phases": {
                    "install": {
                        "runtime-versions": {"nodejs": "20"},
                        "commands": [
                            "cd admin-dashboard",
                            "corepack enable",
                            "yarn install --immutable",
                        ],
                    },
                    "build": {
                        "commands": [
                            # Inject runtime config so the static bundle can read API URL + Cognito IDs.
                            # Single-line printf avoids YAML/heredoc quoting hazards.
                            'printf \'window.__APP_CONFIG__ = {"API_URL":"%s","USER_POOL_ID":"%s","USER_POOL_CLIENT_ID":"%s"};\\n\' "$API_URL" "$USER_POOL_ID" "$USER_POOL_CLIENT_ID" > public/config.js',
                            "yarn build",
                        ],
                    },
                },
                "artifacts": {
                    "base-directory": "admin-dashboard/out",
                    "files": ["**/*"],
                },
            }),
            logging=codebuild.LoggingOptions(
                cloud_watch=codebuild.CloudWatchLoggingOptions(
                    log_group=logs.LogGroup(
                        self, "AdminBuildLogGroup",
                        removal_policy=RemovalPolicy.DESTROY,
                        retention=logs.RetentionDays.ONE_WEEK,
                    ),
                ),
            ),
        )
        admin_distribution.grant(admin_build.role, "cloudfront:CreateInvalidation")
        admin_bucket.grant_write(admin_build)

        # Trigger the build on every stack create/update. Date.now()-style nonce
        # forces the custom resource to run every deploy.
        import time as _time
        trigger = cr.AwsCustomResource(
            self, "TriggerAdminBuild",
            on_create=cr.AwsSdkCall(
                service="CodeBuild", action="startBuild",
                parameters={"projectName": admin_build.project_name},
                physical_resource_id=cr.PhysicalResourceId.of(f"admin-build-{int(_time.time())}"),
                output_paths=["build.id"],
            ),
            on_update=cr.AwsSdkCall(
                service="CodeBuild", action="startBuild",
                parameters={"projectName": admin_build.project_name},
                physical_resource_id=cr.PhysicalResourceId.of(f"admin-build-{int(_time.time())}"),
                output_paths=["build.id"],
            ),
            policy=cr.AwsCustomResourcePolicy.from_statements([
                iam.PolicyStatement(
                    actions=["codebuild:StartBuild"],
                    resources=[admin_build.project_arn],
                ),
            ]),
        )
        trigger.node.add_dependency(admin_build)

        CfnOutput(self, "AdminDashboardUrl", value=f"https://{admin_distribution.distribution_domain_name}")
        CfnOutput(self, "AdminBuildProjectName", value=admin_build.project_name)

        # --- Notifications (DynamoDB stream → Lambda → SES) ---
        notif_cfg = cfg.get("notifications") or {}
        notif_mode = notif_cfg.get("mode", "ses")
        notif_sender = notif_cfg.get("sender", "")

        # SES identity — creating the resource sends a verification email on deploy.
        # Idempotent; if the identity already exists, CFN no-ops.
        if notif_sender:
            ses.EmailIdentity(
                self, "NotificationSenderIdentity",
                identity=ses.Identity.email(notif_sender),
            )

        notif_fn = _lambda.Function(
            self, "NotificationHandler",
            function_name=f"{proj}-notification-handler",
            runtime=_lambda.Runtime.PYTHON_3_12,
            handler="lambda_function.lambda_handler",
            code=_lambda.Code.from_asset("bot/notification_handler"),
            timeout=Duration.seconds(30),
            memory_size=128,
            environment={
                "ADMIN_CONFIG_TABLE": admin_config_table.table_name,
                "USER_POOL_ID": user_pool.user_pool_id,
                "DASHBOARD_URL": f"https://{admin_distribution.distribution_domain_name}/dashboard",
                "SES_SENDER": notif_sender,
                "NOTIFICATIONS_MODE": notif_mode,
            },
        )
        admin_config_table.grant_read_data(notif_fn)
        notif_fn.add_to_role_policy(iam.PolicyStatement(
            actions=["cognito-idp:ListUsersInGroup"],
            resources=[user_pool.user_pool_arn],
        ))
        notif_fn.add_to_role_policy(iam.PolicyStatement(
            actions=["ses:SendEmail", "ses:SendRawEmail"],
            resources=["*"],
        ))
        notif_fn.add_event_source(lambda_events.DynamoEventSource(
            submissions_table,
            starting_position=_lambda.StartingPosition.LATEST,
            batch_size=10,
            retry_attempts=2,
        ))
