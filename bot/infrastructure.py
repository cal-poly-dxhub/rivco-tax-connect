import json
import jsii
import os
import re
import shutil
import subprocess
import yaml
from aws_cdk import (
    Stack, Duration, RemovalPolicy, CfnOutput, BundlingOptions, ILocalBundling,
    aws_s3 as s3,
    aws_s3_deployment as s3deploy,
    aws_lambda as _lambda,
    aws_lambda_event_sources as lambda_events,
    aws_iam as iam,
    aws_kms as kms,
    aws_logs as logs,
    aws_apigateway as apigw,
    aws_apigatewayv2 as apigwv2,
    aws_apigatewayv2_integrations as apigwv2_int,
    aws_bedrock as bedrock,
    aws_dynamodb as dynamodb,
    aws_cognito as cognito,
    aws_codebuild as codebuild,
    aws_cloudfront as cloudfront,
    aws_cloudfront_origins as cloudfront_origins,
    aws_guardduty as guardduty,
    aws_ses as ses,
    aws_ssm as ssm,
    aws_wafv2 as wafv2,
    aws_cloudwatch as cloudwatch,
    custom_resources as cr,
)
from constructs import Construct

@jsii.implements(ILocalBundling)
class _LocalBundling:
    """Bundle a Lambda asset locally without Docker.

    pip-installs the asset's requirements.txt (if any) into the output dir,
    then copies every other file from the source dir alongside it. Idempotent.
    """
    def __init__(self, source_dir: str) -> None:
        self._source = source_dir

    def try_bundle(self, output_dir: str, *, image=None, asset_hash=None,
                   bundling_file_access=None, command=None, entrypoint=None,
                   environment=None, local=None, network=None, output_type=None,
                   platform=None, security_opt=None, user=None, volumes=None,
                   volumes_from=None, working_directory=None) -> bool:
        req = os.path.join(self._source, "requirements.txt")
        if os.path.exists(req):
            subprocess.check_call(
                ["pip", "install", "-r", req, "-t", output_dir, "--quiet",
                 "--platform", "manylinux2014_x86_64",
                 "--only-binary=:all:", "--implementation", "cp",
                 "--python-version", "3.12", "--upgrade"]
            )
        for item in os.listdir(self._source):
            s = os.path.join(self._source, item)
            d = os.path.join(output_dir, item)
            if os.path.isdir(s):
                shutil.copytree(s, d, dirs_exist_ok=True)
            else:
                shutil.copy2(s, d)
        return True


def load_config():
    with open('config.yaml') as f:
        return yaml.safe_load(f)

class RiversideTaxRefundStack(Stack):
    def __init__(self, scope: Construct, id: str, **kwargs) -> None:
        super().__init__(scope, id, **kwargs)

        cfg = load_config()
        proj = cfg['project']['name']

        # ── Shared KMS key for all S3 buckets ───────────────────────────
        s3_key = kms.Key(
            self, "S3Key",
            alias=f"alias/{proj}-s3",
            description=f"{proj} — S3 encryption key",
            enable_key_rotation=True,
            removal_policy=RemovalPolicy.DESTROY,
        )

        # S3 bucket
        bucket = s3.Bucket(
            self, "DataBucket",
            bucket_name=f"{proj}-{cfg['s3']['bucket_suffix']}-{self.account}",
            removal_policy=RemovalPolicy.DESTROY,
            block_public_access=s3.BlockPublicAccess.BLOCK_ALL,
            auto_delete_objects=True,
            encryption=s3.BucketEncryption.KMS,
            encryption_key=s3_key,
        )

        # Ship the demo refund dataset as part of the deploy. The file is
        # gitignored (large, demo-only), so it must exist locally at synth
        # time. We stage it into a tmpdir-style location to keep the asset
        # source minimal — Source.asset(".") would tar up the entire repo.
        _data_stage = os.path.join(os.path.dirname(__file__), "..", "cdk.out", ".data-stage")
        os.makedirs(_data_stage, exist_ok=True)
        _data_src = os.path.join(os.path.dirname(__file__), "..", cfg['s3']['data_file'])
        if os.path.exists(_data_src):
            shutil.copy2(_data_src, os.path.join(_data_stage, cfg['s3']['data_file']))
        s3deploy.BucketDeployment(
            self, "DataBucketDeployment",
            sources=[s3deploy.Source.asset(_data_stage)],
            destination_bucket=bucket,
            retain_on_delete=False,
            prune=False,
        )

        # Lambda role
        role = iam.Role(
            self, "LambdaRole",
            role_name=f"{proj}-lambda-role",
            assumed_by=iam.ServicePrincipal("lambda.amazonaws.com"),
            managed_policies=[iam.ManagedPolicy.from_aws_managed_policy_name("service-role/AWSLambdaBasicExecutionRole")],
        )
        bucket.grant_read(role)

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
                    local=_LocalBundling(os.path.join(os.path.dirname(__file__), "runtime")),
                ),
            ),
            role=role,
            timeout=Duration.seconds(cfg['lambda']['timeout_seconds']),
            memory_size=cfg['lambda']['memory_mb'],
            environment=env,
        )
        # ── DynamoDB chat session table ─────────────────────────────────
        # Single-table for chat sessions:
        #   pk = SESSION#<id>  sk = META          → session record
        #   pk = SESSION#<id>  sk = MSG#<ts>      → one row per message
        #   pk = SESSION#<id>  sk = HANDOFF       → present when user asked for agent
        # GSI handoffIx exposes pending handoffs to the admin dashboard.
        chat_table = dynamodb.Table(
            self, "ChatSessions",
            table_name=f"{proj}-chat-sessions",
            partition_key=dynamodb.Attribute(name="pk", type=dynamodb.AttributeType.STRING),
            sort_key=dynamodb.Attribute(name="sk", type=dynamodb.AttributeType.STRING),
            billing_mode=dynamodb.BillingMode.PAY_PER_REQUEST,
            stream=dynamodb.StreamViewType.NEW_AND_OLD_IMAGES,
            time_to_live_attribute="ttl",
            removal_policy=RemovalPolicy.DESTROY,
        )
        chat_table.add_global_secondary_index(
            index_name="handoffIx",
            partition_key=dynamodb.Attribute(name="gsi1pk", type=dynamodb.AttributeType.STRING),
            sort_key=dynamodb.Attribute(name="gsi1sk", type=dynamodb.AttributeType.STRING),
        )

        # ── Chat agent system prompt (SSM Parameter for runtime edits) ──
        prompt_param = ssm.StringParameter(
            self, "ChatSystemPrompt",
            parameter_name=f"/{proj}/chat/system-prompt",
            string_value=cfg["prompts"]["ai_orchestration"],
            description="Bedrock Claude system prompt for the auditor chat agent",
            # Advanced tier — Standard caps at 4KB; the prompt with the
            # decoy-quiz instructions is ~4.4KB. Advanced costs ~$0.05/mo.
            tier=ssm.ParameterTier.ADVANCED,
        )

        # ── Bedrock Guardrail (deny prompt-injection / harmful prompts) ──
        # Applied on every chat handler invocation. Default deny on prompt
        # attacks + the four harm categories. Cheap (~$0.15/1k checks).
        guardrail = bedrock.CfnGuardrail(
            self, "ChatGuardrail",
            name=f"{proj}-chat-guardrail",
            blocked_input_messaging=(
                "I can't help with that request. If you're trying to look up a "
                "refund or get information about an Auditor-Controller service, "
                "let me know and I'll help."
            ),
            blocked_outputs_messaging=(
                "I can't share that. If you have a question about a refund or "
                "Auditor-Controller service, please let me know."
            ),
            content_policy_config=bedrock.CfnGuardrail.ContentPolicyConfigProperty(
                filters_config=[
                    bedrock.CfnGuardrail.ContentFilterConfigProperty(
                        type=t, input_strength="HIGH", output_strength="HIGH"
                    ) for t in ("SEXUAL", "VIOLENCE", "HATE", "INSULTS", "MISCONDUCT")
                ] + [
                    # PROMPT_ATTACK only supports input filtering
                    bedrock.CfnGuardrail.ContentFilterConfigProperty(
                        type="PROMPT_ATTACK", input_strength="HIGH", output_strength="NONE"
                    ),
                ],
            ),
        )
        guardrail_version = bedrock.CfnGuardrailVersion(
            self, "ChatGuardrailVersion",
            guardrail_identifier=guardrail.attr_guardrail_id,
        )

        # ── Chat handler Lambda (Bedrock Claude + WebSocket streaming) ──
        chat_fn = _lambda.Function(
            self, "ChatHandler",
            function_name=f"{proj}-chat-handler",
            runtime=_lambda.Runtime.PYTHON_3_12,
            handler="lambda_function.lambda_handler",
            code=_lambda.Code.from_asset(
                "bot/chat_handler",
                bundling=BundlingOptions(
                    image=_lambda.Runtime.PYTHON_3_12.bundling_image,
                    local=_LocalBundling(os.path.join(os.path.dirname(__file__), "chat_handler")),
                ),
            ),
            timeout=Duration.minutes(5),
            memory_size=1024,
            environment={
                "CHAT_TABLE": chat_table.table_name,
                "BEDROCK_MODEL_ID": cfg["bedrock"]["model_id"],
                "TAX_LOOKUP_FN": fn.function_name,
                "AI_PROMPT_PARAM": prompt_param.parameter_name,
                "SES_SENDER": (cfg.get("notifications") or {}).get("sender", ""),
                "GUARDRAIL_ID": guardrail.attr_guardrail_id,
                "GUARDRAIL_VERSION": guardrail_version.attr_version,
            },
        )
        chat_table.grant_read_write_data(chat_fn)
        prompt_param.grant_read(chat_fn)
        fn.grant_invoke(chat_fn)
        chat_fn.add_to_role_policy(iam.PolicyStatement(
            actions=["bedrock:InvokeModel", "bedrock:InvokeModelWithResponseStream"],
            resources=[f"arn:aws:bedrock:*::foundation-model/*",
                       f"arn:aws:bedrock:*:{self.account}:inference-profile/*"],
        ))
        chat_fn.add_to_role_policy(iam.PolicyStatement(
            actions=["bedrock:ApplyGuardrail"],
            resources=[guardrail.attr_guardrail_arn],
        ))
        chat_fn.add_to_role_policy(iam.PolicyStatement(
            actions=["ses:SendEmail", "ses:SendRawEmail"],
            resources=["*"],
        ))
        chat_fn.add_to_role_policy(iam.PolicyStatement(
            actions=["cloudwatch:PutMetricData"],
            resources=["*"],
        ))
        chat_fn.add_environment("PROJECT_NAME", proj)

        # ── WebSocket API Gateway for the chat widget ───────────────────
        ws_api = apigwv2.WebSocketApi(
            self, "ChatWebSocket",
            api_name=f"{proj}-chat-ws",
            connect_route_options=apigwv2.WebSocketRouteOptions(
                integration=apigwv2_int.WebSocketLambdaIntegration("ConnectInt", chat_fn),
            ),
            disconnect_route_options=apigwv2.WebSocketRouteOptions(
                integration=apigwv2_int.WebSocketLambdaIntegration("DisconnectInt", chat_fn),
            ),
            default_route_options=apigwv2.WebSocketRouteOptions(
                integration=apigwv2_int.WebSocketLambdaIntegration("DefaultInt", chat_fn),
            ),
        )
        ws_api.add_route(
            "sendMessage",
            integration=apigwv2_int.WebSocketLambdaIntegration("SendInt", chat_fn),
        )
        ws_stage = apigwv2.WebSocketStage(
            self, "ChatWsStage",
            web_socket_api=ws_api,
            stage_name="prod",
            auto_deploy=True,
        )
        chat_fn.add_environment(
            "WS_ENDPOINT",
            f"https://{ws_api.api_id}.execute-api.{self.region}.amazonaws.com/{ws_stage.stage_name}",
        )
        ws_stage.grant_management_api_access(chat_fn)

        CfnOutput(self, "BucketName", value=bucket.bucket_name)
        CfnOutput(self, "LambdaArn", value=fn.function_arn)
        CfnOutput(self, "ChatTableName", value=chat_table.table_name)
        CfnOutput(self, "ChatWebSocketUrl", value=f"wss://{ws_api.api_id}.execute-api.{self.region}.amazonaws.com/{ws_stage.stage_name}")

        # --- Task 10: Secure Document Upload Portal ---

        portal_origin = f"http://{proj}-portal-{self.account}.s3-website-{self.region}.amazonaws.com"

        # S3 bucket for uploaded documents (encrypted, tiered lifecycle, no public access)
        ret = cfg.get('retention', {})
        hot_days = ret.get('hot_days', 90)
        warm_days = ret.get('warm_days', 365)
        cold_days = ret.get('cold_days', 1825)
        expire_days = ret.get('expire_days', 2555)

        uploads_bucket = s3.Bucket(
            self, "UploadsBucket",
            bucket_name=f"{proj}-uploads-{self.account}",
            encryption=s3.BucketEncryption.KMS,
            encryption_key=s3_key,
            bucket_key_enabled=True,
            block_public_access=s3.BlockPublicAccess.BLOCK_ALL,
            removal_policy=RemovalPolicy.DESTROY,
            auto_delete_objects=True,
            lifecycle_rules=[s3.LifecycleRule(
                transitions=[
                    s3.Transition(
                        storage_class=s3.StorageClass.GLACIER_INSTANT_RETRIEVAL,
                        transition_after=Duration.days(hot_days),
                    ),
                    s3.Transition(
                        storage_class=s3.StorageClass.GLACIER,
                        transition_after=Duration.days(warm_days),
                    ),
                    s3.Transition(
                        storage_class=s3.StorageClass.DEEP_ARCHIVE,
                        transition_after=Duration.days(cold_days),
                    ),
                ],
                expiration=Duration.days(expire_days),
            )],
            cors=[
                s3.CorsRule(  # Claimant upload — presigned URLs are already scoped/signed/time-limited
                    allowed_methods=[s3.HttpMethods.PUT],
                    allowed_origins=["*"],
                    allowed_headers=["*"],
                    max_age=3600,
                ),
                s3.CorsRule(  # Admin dashboard inline previews (e.g. JSON fetch)
                    allowed_methods=[s3.HttpMethods.GET],
                    allowed_origins=["*"],
                    allowed_headers=["*"],
                    max_age=3600,
                ),
            ],
        )

        # Single application table: submissions + per-submission audit + future
        # per-submission entities. Single-table pattern with pk+sk.
        #   pk = SUBMISSION#<id>     sk = META         → submission record
        #   pk = SUBMISSION#<id>     sk = AUDIT#<ts>   → audit entry
        # GSI "listIx" exposes SUBMISSION_LIST for efficient queries over all
        # submissions, sorted by submittedAt.
        submissions_table = dynamodb.Table(
            self, "AppData",
            table_name=f"{proj}-app-data",
            partition_key=dynamodb.Attribute(name="pk", type=dynamodb.AttributeType.STRING),
            sort_key=dynamodb.Attribute(name="sk", type=dynamodb.AttributeType.STRING),
            billing_mode=dynamodb.BillingMode.PAY_PER_REQUEST,
            removal_policy=RemovalPolicy.DESTROY,
            stream=dynamodb.StreamViewType.NEW_AND_OLD_IMAGES,
        )
        submissions_table.add_global_secondary_index(
            index_name="listIx",
            partition_key=dynamodb.Attribute(name="gsi1pk", type=dynamodb.AttributeType.STRING),
            sort_key=dynamodb.Attribute(name="gsi1sk", type=dynamodb.AttributeType.STRING),
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
                "CHAT_TABLE": chat_table.table_name,
            },
        )
        submissions_table.grant_read_write_data(upload_fn)
        admin_config_table.grant_read_write_data(upload_fn)
        chat_table.grant_read_write_data(upload_fn)
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
                allow_methods=apigw.Cors.ALL_METHODS,
                allow_headers=["Content-Type", "Authorization", "X-Claimant-Token"],
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
        upload_api.root.add_resource("form-schemas").add_method(
            "GET", apigw.LambdaIntegration(upload_fn),
        )
        upload_api.root.add_resource("doc-requirements").add_method(
            "GET", apigw.LambdaIntegration(upload_fn),
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
        audit_resource = upload_api.root.add_resource("audit").add_resource("{submissionId}")
        audit_resource.add_method(
            "GET", apigw.LambdaIntegration(upload_fn), authorizer=authorizer,
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

        ws_url = f"wss://{ws_api.api_id}.execute-api.{self.region}.amazonaws.com/{ws_stage.stage_name}"
        config_js = (
            f'window.API_URL = "{upload_api.url.rstrip("/")}";\n'
            f'window.WS_ENDPOINT = "{ws_url}";\n'
        )
        s3deploy.BucketDeployment(
            self, "PortalDeployment",
            sources=[
                s3deploy.Source.asset("bot/upload_portal"),
                s3deploy.Source.data("config.js", config_js),
            ],
            destination_bucket=portal_bucket,
        )

        # UPLOAD_PORTAL_URL is set below once the claimant portal CloudFront URL is known.

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

        # ALLOWED_ORIGINS is set after the claimant portal distribution is created below
        # so it can include all three origins in one call.

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
                        "runtime-versions": {"nodejs": "22"},
                        "commands": [
                            "cd admin-dashboard",
                            "corepack enable",
                            "yarn install --immutable --ignore-engines",
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

        # --- Claimant portal (separate Next.js app, public) ---
        claimant_bucket = s3.Bucket(
            self, "ClaimantBucket",
            bucket_name=f"{proj}-claimant-{self.account}",
            removal_policy=RemovalPolicy.DESTROY,
            auto_delete_objects=True,
            block_public_access=s3.BlockPublicAccess.BLOCK_ALL,
            encryption=s3.BucketEncryption.S3_MANAGED,
        )

        claimant_oac = cloudfront.S3OriginAccessControl(
            self, "ClaimantBucketOAC", description="OAC for claimant portal bucket",
        )
        claimant_distribution = cloudfront.Distribution(
            self, "ClaimantDistribution",
            default_behavior=cloudfront.BehaviorOptions(
                origin=cloudfront_origins.S3BucketOrigin.with_origin_access_control(
                    claimant_bucket, origin_access_control=claimant_oac,
                ),
                viewer_protocol_policy=cloudfront.ViewerProtocolPolicy.REDIRECT_TO_HTTPS,
                cache_policy=cloudfront.CachePolicy.CACHING_DISABLED,
                function_associations=[cloudfront.FunctionAssociation(
                    function=cloudfront.Function(
                        self, "ClaimantRewriteFunction",
                        code=cloudfront.FunctionCode.from_inline(
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

        claimant_build = codebuild.Project(
            self, "ClaimantBuild",
            source=codebuild.Source.git_hub(
                owner=gh_owner, repo=gh_repo, branch_or_ref=gh_branch, clone_depth=1,
            ),
            environment=codebuild.BuildEnvironment(
                build_image=codebuild.LinuxBuildImage.STANDARD_7_0,
                compute_type=codebuild.ComputeType.SMALL,
            ),
            artifacts=codebuild.Artifacts.s3(
                bucket=claimant_bucket, include_build_id=False, package_zip=False,
                name="/", encryption=False,
            ),
            environment_variables={
                "API_URL": codebuild.BuildEnvironmentVariable(value=upload_api.url.rstrip("/")),
            },
            build_spec=codebuild.BuildSpec.from_object({
                "version": "0.2",
                "phases": {
                    "install": {
                        "runtime-versions": {"nodejs": "22"},
                        "commands": [
                            "cd claimant-portal",
                            "corepack enable",
                            "yarn install --immutable",
                        ],
                    },
                    "build": {
                        "commands": [
                            'printf \'window.__CLAIMANT_CONFIG__ = {"API_URL":"%s"};\\n\' "$API_URL" > public/config.js',
                            "yarn build",
                        ],
                    },
                },
                "artifacts": {
                    "base-directory": "claimant-portal/out",
                    "files": ["**/*"],
                },
            }),
            logging=codebuild.LoggingOptions(
                cloud_watch=codebuild.CloudWatchLoggingOptions(
                    log_group=logs.LogGroup(
                        self, "ClaimantBuildLogGroup",
                        removal_policy=RemovalPolicy.DESTROY,
                        retention=logs.RetentionDays.ONE_WEEK,
                    ),
                ),
            ),
        )
        claimant_distribution.grant(claimant_build.role, "cloudfront:CreateInvalidation")
        claimant_bucket.grant_write(claimant_build)

        claimant_trigger = cr.AwsCustomResource(
            self, "TriggerClaimantBuild",
            on_create=cr.AwsSdkCall(
                service="CodeBuild", action="startBuild",
                parameters={"projectName": claimant_build.project_name},
                physical_resource_id=cr.PhysicalResourceId.of(f"claimant-build-{int(_time.time())}"),
                output_paths=["build.id"],
            ),
            on_update=cr.AwsSdkCall(
                service="CodeBuild", action="startBuild",
                parameters={"projectName": claimant_build.project_name},
                physical_resource_id=cr.PhysicalResourceId.of(f"claimant-build-{int(_time.time())}"),
                output_paths=["build.id"],
            ),
            policy=cr.AwsCustomResourcePolicy.from_statements([
                iam.PolicyStatement(
                    actions=["codebuild:StartBuild"],
                    resources=[claimant_build.project_arn],
                ),
            ]),
        )
        claimant_trigger.node.add_dependency(claimant_build)

        # Add /claimant/* routes to API Gateway (no authorizer — public)
        claimant_resource = upload_api.root.add_resource("claimant")
        claimant_resource.add_resource("{proxy+}").add_method(
            "ANY", apigw.LambdaIntegration(upload_fn),
        )

        # Override the bot Lambda's UPLOAD_PORTAL_URL to point to the new claimant portal.
        # (The old portal_bucket is kept in place for backward compatibility.)
        fn.add_environment("UPLOAD_PORTAL_URL", f"https://{claimant_distribution.distribution_domain_name}")
        # Placeholder CLAIMANT_SECRET — must be overridden post-deploy via SSM/env update.
        upload_fn.add_environment("CLAIMANT_SECRET", "CHANGEME-set-in-ssm")

        # Allow the claimant portal CloudFront domain in upload bucket CORS.
        # Appended to the already-set ALLOWED_ORIGINS from the admin distribution block.
        upload_fn.add_environment(
            "ALLOWED_ORIGINS",
            f"{portal_origin},"
            f"https://{admin_distribution.distribution_domain_name},"
            f"https://{claimant_distribution.distribution_domain_name}",
        )

        CfnOutput(self, "ClaimantPortalUrl", value=f"https://{claimant_distribution.distribution_domain_name}")
        CfnOutput(self, "ClaimantBuildProjectName", value=claimant_build.project_name)

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

        # ── GuardDuty Malware Protection for uploaded documents ─────────
        # Scans every object written to the uploads bucket. The detector is
        # account-scoped; we create one and tag the uploads bucket as a
        # protected resource.
        gd_detector = guardduty.CfnDetector(
            self, "GuardDutyDetector",
            enable=True,
            features=[guardduty.CfnDetector.CFNFeatureConfigurationProperty(
                name="S3_DATA_EVENTS",
                status="ENABLED",
            )],
        )
        malware_role = iam.Role(
            self, "MalwareProtectionRole",
            assumed_by=iam.ServicePrincipal("malware-protection-plan.guardduty.amazonaws.com"),
            inline_policies={
                "ScanPolicy": iam.PolicyDocument(statements=[
                    iam.PolicyStatement(
                        actions=["s3:GetObject", "s3:GetObjectVersion", "s3:GetObjectTagging",
                                 "s3:PutObjectTagging", "s3:PutObjectVersionTagging"],
                        resources=[uploads_bucket.arn_for_objects("*")],
                    ),
                    iam.PolicyStatement(
                        actions=["s3:ListBucket", "s3:GetBucketNotification", "s3:PutBucketNotification"],
                        resources=[uploads_bucket.bucket_arn],
                    ),
                    iam.PolicyStatement(
                        actions=["events:PutRule", "events:DeleteRule", "events:PutTargets", "events:RemoveTargets"],
                        resources=["*"],
                    ),
                    iam.PolicyStatement(
                        actions=["kms:GenerateDataKey", "kms:Decrypt"],
                        resources=[s3_key.key_arn],
                    ),
                ]),
            },
        )
        guardduty.CfnMalwareProtectionPlan(
            self, "MalwareProtection",
            protected_resource=guardduty.CfnMalwareProtectionPlan.CFNProtectedResourceProperty(
                s3_bucket=guardduty.CfnMalwareProtectionPlan.S3BucketProperty(
                    bucket_name=uploads_bucket.bucket_name,
                    object_prefixes=["submissions/"],
                ),
            ),
            actions=guardduty.CfnMalwareProtectionPlan.CFNActionsProperty(
                tagging=guardduty.CfnMalwareProtectionPlan.CFNTaggingProperty(
                    status="ENABLED",
                ),
            ),
            role=malware_role.role_arn,
        )

        # ── WAF WebACL in front of API Gateway (REST) ───────────────────
        # Blocks common web exploits and rate-limits per IP to 100 req/5min.
        waf_acl = wafv2.CfnWebACL(
            self, "ApiWaf",
            name=f"{proj}-api-waf",
            scope="REGIONAL",
            default_action=wafv2.CfnWebACL.DefaultActionProperty(allow={}),
            visibility_config=wafv2.CfnWebACL.VisibilityConfigProperty(
                cloud_watch_metrics_enabled=True,
                metric_name=f"{proj}-api-waf",
                sampled_requests_enabled=True,
            ),
            rules=[
                wafv2.CfnWebACL.RuleProperty(
                    name="AWSManagedRulesCommonRuleSet",
                    priority=10,
                    statement=wafv2.CfnWebACL.StatementProperty(
                        managed_rule_group_statement=wafv2.CfnWebACL.ManagedRuleGroupStatementProperty(
                            vendor_name="AWS", name="AWSManagedRulesCommonRuleSet",
                        ),
                    ),
                    override_action=wafv2.CfnWebACL.OverrideActionProperty(none={}),
                    visibility_config=wafv2.CfnWebACL.VisibilityConfigProperty(
                        cloud_watch_metrics_enabled=True,
                        metric_name="CommonRuleSet",
                        sampled_requests_enabled=True,
                    ),
                ),
                wafv2.CfnWebACL.RuleProperty(
                    name="AWSManagedRulesKnownBadInputsRuleSet",
                    priority=20,
                    statement=wafv2.CfnWebACL.StatementProperty(
                        managed_rule_group_statement=wafv2.CfnWebACL.ManagedRuleGroupStatementProperty(
                            vendor_name="AWS", name="AWSManagedRulesKnownBadInputsRuleSet",
                        ),
                    ),
                    override_action=wafv2.CfnWebACL.OverrideActionProperty(none={}),
                    visibility_config=wafv2.CfnWebACL.VisibilityConfigProperty(
                        cloud_watch_metrics_enabled=True,
                        metric_name="KnownBadInputs",
                        sampled_requests_enabled=True,
                    ),
                ),
                wafv2.CfnWebACL.RuleProperty(
                    name="RateLimit",
                    priority=30,
                    statement=wafv2.CfnWebACL.StatementProperty(
                        rate_based_statement=wafv2.CfnWebACL.RateBasedStatementProperty(
                            limit=100,
                            aggregate_key_type="IP",
                            evaluation_window_sec=300,
                        ),
                    ),
                    action=wafv2.CfnWebACL.RuleActionProperty(block={}),
                    visibility_config=wafv2.CfnWebACL.VisibilityConfigProperty(
                        cloud_watch_metrics_enabled=True,
                        metric_name="RateLimit",
                        sampled_requests_enabled=True,
                    ),
                ),
            ],
        )
        wafv2.CfnWebACLAssociation(
            self, "ApiWafAssociation",
            resource_arn=f"arn:aws:apigateway:{self.region}::/restapis/{upload_api.rest_api_id}/stages/prod",
            web_acl_arn=waf_acl.attr_arn,
        )

        # ── CloudWatch Alarms ────────────────────────────────────────────
        alarm_topic_arn = (cfg.get("monitoring") or {}).get("alarm_sns_topic_arn", "")

        def _alarm(construct_id, metric, threshold, description, comparison=None, periods=1):
            kwargs = dict(
                alarm_name=f"{proj}-{construct_id}",
                alarm_description=description,
                metric=metric,
                threshold=threshold,
                evaluation_periods=periods,
                comparison_operator=comparison or cloudwatch.ComparisonOperator.GREATER_THAN_OR_EQUAL_TO_THRESHOLD,
                treat_missing_data=cloudwatch.TreatMissingData.NOT_BREACHING,
            )
            alarm = cloudwatch.Alarm(self, construct_id, **kwargs)
            if alarm_topic_arn:
                import aws_cdk.aws_sns as sns
                import aws_cdk.aws_cloudwatch_actions as cw_actions
                topic = sns.Topic.from_topic_arn(self, f"Topic-{construct_id}", alarm_topic_arn)
                alarm.add_alarm_action(cw_actions.SnsAction(topic))
            return alarm

        # Lambda errors
        for fn_obj, name in [
            (fn, "runtime"), (chat_fn, "chat"), (upload_fn, "upload"), (notif_fn, "notif"),
        ]:
            _alarm(
                f"lambda-{name}-errors",
                fn_obj.metric_errors(period=Duration.minutes(5)),
                threshold=3,
                description=f"{name} Lambda error rate ≥ 3 in 5 min",
            )

        # API Gateway 4xx / 5xx
        _alarm(
            "api-4xx",
            upload_api.metric_client_error(period=Duration.minutes(5)),
            threshold=20,
            description="Upload API 4xx errors ≥ 20 in 5 min",
        )
        _alarm(
            "api-5xx",
            upload_api.metric_server_error(period=Duration.minutes(5)),
            threshold=5,
            description="Upload API 5xx errors ≥ 5 in 5 min",
        )

        # Too many failed verifications (custom metric emitted by chat handler)
        _alarm(
            "failed-verifications",
            cloudwatch.Metric(
                namespace=f"{proj}/Chat",
                metric_name="VerificationFailure",
                period=Duration.minutes(10),
                statistic="Sum",
            ),
            threshold=10,
            description="≥ 10 identity verification failures in 10 min",
        )

        # Ingestion failures (DynamoDB stream errors on the notification handler)
        _alarm(
            "ingestion-failures",
            notif_fn.metric_errors(period=Duration.minutes(5)),
            threshold=1,
            description="Notification/ingestion Lambda any error",
        )

        CfnOutput(self, "WafAclArn", value=waf_acl.attr_arn)
