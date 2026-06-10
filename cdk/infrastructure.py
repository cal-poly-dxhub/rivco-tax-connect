import jsii
import os
import re
import shutil
import subprocess
import time as _time
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
    """Bundle a Lambda asset locally without Docker."""
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


# ── Section builders ─────────────────────────────────────────────────────────
# Each function scopes all CDK constructs directly to `stack` (not a nested
# Construct) so logical IDs stay stable across refactors.

def _build_storage(stack, cfg, proj):
    """KMS key, data bucket, data deployment, tax-lookup Lambda."""
    s3_key = kms.Key(
        stack, "S3Key",
        alias=f"alias/{proj}-s3",
        description=f"{proj} — S3 encryption key",
        enable_key_rotation=True,
        removal_policy=RemovalPolicy.DESTROY,
    )

    bucket = s3.Bucket(
        stack, "DataBucket",
        bucket_name=f"{proj}-{cfg['s3']['bucket_suffix']}-{stack.account}",
        removal_policy=RemovalPolicy.DESTROY,
        block_public_access=s3.BlockPublicAccess.BLOCK_ALL,
        auto_delete_objects=True,
        encryption=s3.BucketEncryption.KMS,
        encryption_key=s3_key,
    )

    _data_stage = os.path.join(os.path.dirname(__file__), "..", "cdk.out", ".data-stage")
    os.makedirs(_data_stage, exist_ok=True)
    _data_src = os.path.join(os.path.dirname(__file__), "..", cfg['s3']['data_file'])
    if os.path.exists(_data_src):
        shutil.copy2(_data_src, os.path.join(_data_stage, cfg['s3']['data_file']))
    s3deploy.BucketDeployment(
        stack, "DataBucketDeployment",
        sources=[s3deploy.Source.asset(_data_stage)],
        destination_bucket=bucket,
        retain_on_delete=False,
        prune=False,
    )

    role = iam.Role(
        stack, "LambdaRole",
        role_name=f"{proj}-lambda-role",
        assumed_by=iam.ServicePrincipal("lambda.amazonaws.com"),
        managed_policies=[iam.ManagedPolicy.from_aws_managed_policy_name("service-role/AWSLambdaBasicExecutionRole")],
    )
    bucket.grant_read(role)

    fn = _lambda.Function(
        stack, "Function",
        function_name=cfg['lambda']['function_name'],
        runtime=_lambda.Runtime.PYTHON_3_12,
        handler="lambda_function.lambda_handler",
        code=_lambda.Code.from_asset(
            "cdk/runtime",
            bundling=BundlingOptions(
                image=_lambda.Runtime.PYTHON_3_12.bundling_image,
                local=_LocalBundling(os.path.join(os.path.dirname(__file__), "runtime")),
            ),
        ),
        role=role,
        timeout=Duration.seconds(cfg['lambda']['timeout_seconds']),
        memory_size=cfg['lambda']['memory_mb'],
        environment={
            "S3_BUCKET": bucket.bucket_name,
            "DATA_FILE": cfg['s3']['data_file'],
            **cfg['lambda']['environment']
        },
    )

    CfnOutput(stack, "BucketName", value=bucket.bucket_name)
    CfnOutput(stack, "LambdaArn", value=fn.function_arn)
    return bucket, fn, s3_key


def _build_chat(stack, cfg, proj, fn):
    """DynamoDB chat table, SSM prompt, Bedrock guardrail, chat Lambda, WebSocket API."""
    chat_table = dynamodb.Table(
        stack, "ChatSessions",
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

    # System prompt lives next to the CDK code (not in config.yaml) so it can
    # be edited as plain markdown. SSM still gets the value on every deploy;
    # the parameter itself remains the live-editable source of truth at
    # runtime, so post-deploy edits should go through SSM, not by re-running
    # `cdk deploy` (which would clobber them).
    _prompt_path = os.path.join(os.path.dirname(__file__), "chat_system_prompt.md")
    with open(_prompt_path) as _pf:
        _prompt_text = _pf.read()
    prompt_param = ssm.StringParameter(
        stack, "ChatSystemPrompt",
        parameter_name=f"/{proj}/chat/system-prompt",
        string_value=_prompt_text,
        description="Bedrock Claude system prompt for the auditor chat agent",
        tier=ssm.ParameterTier.ADVANCED,
    )

    guardrail = bedrock.CfnGuardrail(
        stack, "ChatGuardrail",
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
                bedrock.CfnGuardrail.ContentFilterConfigProperty(
                    type="PROMPT_ATTACK", input_strength="HIGH", output_strength="NONE"
                ),
            ],
        ),
    )
    guardrail_version = bedrock.CfnGuardrailVersion(
        stack, "ChatGuardrailVersion",
        guardrail_identifier=guardrail.attr_guardrail_id,
    )

    chat_fn = _lambda.Function(
        stack, "ChatHandler",
        function_name=f"{proj}-chat-handler",
        runtime=_lambda.Runtime.PYTHON_3_12,
        handler="lambda_function.lambda_handler",
        code=_lambda.Code.from_asset(
            "cdk/chat_handler",
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
                   f"arn:aws:bedrock:*:{stack.account}:inference-profile/*"],
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

    ws_api = apigwv2.WebSocketApi(
        stack, "ChatWebSocket",
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
        stack, "ChatWsStage",
        web_socket_api=ws_api,
        stage_name="prod",
        auto_deploy=True,
    )
    chat_fn.add_environment(
        "WS_ENDPOINT",
        f"https://{ws_api.api_id}.execute-api.{stack.region}.amazonaws.com/{ws_stage.stage_name}",
    )
    ws_stage.grant_management_api_access(chat_fn)

    CfnOutput(stack, "ChatTableName", value=chat_table.table_name)
    CfnOutput(stack, "ChatWebSocketUrl", value=f"wss://{ws_api.api_id}.execute-api.{stack.region}.amazonaws.com/{ws_stage.stage_name}")
    return chat_fn, ws_stage, ws_api, chat_table


def _build_upload_api(stack, cfg, proj, uploads_bucket, chat_table, portal_origin):
    """Submissions table, admin config table, upload Lambda, Cognito, REST API."""
    ret = cfg.get('retention', {})
    submissions_table = dynamodb.Table(
        stack, "AppData",
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

    admin_config_table = dynamodb.Table(
        stack, "AdminConfig",
        table_name=f"{proj}-admin-config",
        partition_key=dynamodb.Attribute(name="pk", type=dynamodb.AttributeType.STRING),
        billing_mode=dynamodb.BillingMode.PAY_PER_REQUEST,
        removal_policy=RemovalPolicy.DESTROY,
    )

    upload_fn = _lambda.Function(
        stack, "UploadHandler",
        function_name=f"{proj}-upload-handler",
        runtime=_lambda.Runtime.PYTHON_3_12,
        handler="lambda_function.lambda_handler",
        code=_lambda.Code.from_asset("cdk/upload_handler"),
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

    user_pool = cognito.UserPool(
        stack, "AdminUserPool",
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
        stack, "AdminUserPoolClient",
        user_pool=user_pool,
        auth_flows=cognito.AuthFlow(user_password=True, user_srp=True),
        generate_secret=False,
    )
    cognito.CfnUserPoolGroup(
        stack, "GroupSuperAdmin",
        user_pool_id=user_pool.user_pool_id,
        group_name="super-admin",
        description="Full access, all departments",
    )

    super_admin_email = (cfg.get("super_admin") or {}).get("email")
    if super_admin_email:
        super_admin_username = "sa-" + re.sub(r'[^a-z0-9]', '-', super_admin_email.lower())[:64]
        bootstrap_pw = "Kiro!Temp" + stack.account[-4:]
        create_super_admin = cr.AwsCustomResource(
            stack, "BootstrapSuperAdmin",
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
            stack, "BootstrapSuperAdminGroup",
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
        CfnOutput(stack, "SuperAdminUsername", value=super_admin_username)
        CfnOutput(stack, "SuperAdminBootstrapPassword", value=bootstrap_pw,
                  description="Temp password for initial super-admin (forced change on first login)")

    upload_fn.add_environment("USER_POOL_ID", user_pool.user_pool_id)
    upload_fn.add_to_role_policy(iam.PolicyStatement(
        actions=[
            "cognito-idp:AdminCreateUser", "cognito-idp:AdminDeleteUser",
            "cognito-idp:AdminUpdateUserAttributes", "cognito-idp:AdminAddUserToGroup",
            "cognito-idp:AdminRemoveUserFromGroup", "cognito-idp:AdminListGroupsForUser",
            "cognito-idp:AdminGetUser", "cognito-idp:ListUsers",
            "cognito-idp:ListUsersInGroup", "cognito-idp:CreateGroup", "cognito-idp:DeleteGroup",
            "cognito-idp:GetGroup",
        ],
        resources=[user_pool.user_pool_arn],
    ))

    authorizer = apigw.CognitoUserPoolsAuthorizer(
        stack, "AdminAuthorizer",
        cognito_user_pools=[user_pool],
    )

    upload_api = apigw.RestApi(
        stack, "UploadApi",
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
    upload_api.root.add_resource("upload").add_method("POST", apigw.LambdaIntegration(upload_fn))
    upload_api.root.add_resource("upload-complete").add_method("POST", apigw.LambdaIntegration(upload_fn))
    upload_api.root.add_resource("form-schemas").add_method("GET", apigw.LambdaIntegration(upload_fn))
    upload_api.root.add_resource("doc-requirements").add_method("GET", apigw.LambdaIntegration(upload_fn))

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
    upload_api.root.add_resource("audit").add_resource("{submissionId}").add_method(
        "GET", apigw.LambdaIntegration(upload_fn), authorizer=authorizer,
        authorization_type=apigw.AuthorizationType.COGNITO,
    )
    upload_api.root.add_resource("admin").add_resource("{proxy+}").add_method(
        "ANY", apigw.LambdaIntegration(upload_fn), authorizer=authorizer,
        authorization_type=apigw.AuthorizationType.COGNITO,
    )
    upload_api.root.add_resource("claimant").add_resource("{proxy+}").add_method(
        "ANY", apigw.LambdaIntegration(upload_fn),
    )

    CfnOutput(stack, "UserPoolId", value=user_pool.user_pool_id)
    CfnOutput(stack, "UserPoolClientId", value=user_pool_client.user_pool_client_id)
    CfnOutput(stack, "UploadApiUrl", value=upload_api.url)
    CfnOutput(stack, "UploadsBucketName", value=uploads_bucket.bucket_name)
    CfnOutput(stack, "SubmissionsTableName", value=submissions_table.table_name)
    return upload_fn, upload_api, submissions_table, admin_config_table, user_pool, user_pool_client, authorizer


def _build_portal(stack, cfg, proj, upload_api, ws_api, ws_stage):
    """Legacy S3 static portal (chat widget host)."""
    portal_bucket = s3.Bucket(
        stack, "PortalBucket",
        bucket_name=f"{proj}-portal-{stack.account}",
        removal_policy=RemovalPolicy.DESTROY,
        auto_delete_objects=True,
        website_index_document="index.html",
        public_read_access=True,
        block_public_access=s3.BlockPublicAccess(
            block_public_acls=False, block_public_policy=False,
            ignore_public_acls=False, restrict_public_buckets=False,
        ),
    )
    ws_url = f"wss://{ws_api.api_id}.execute-api.{stack.region}.amazonaws.com/{ws_stage.stage_name}"
    config_js = (
        f'window.API_URL = "{upload_api.url.rstrip("/")}";\n'
        f'window.WS_ENDPOINT = "{ws_url}";\n'
    )
    s3deploy.BucketDeployment(
        stack, "PortalDeployment",
        sources=[
            s3deploy.Source.asset("cdk/upload_portal"),
            s3deploy.Source.data("config.js", config_js),
        ],
        destination_bucket=portal_bucket,
    )
    CfnOutput(stack, "UploadPortalUrl", value=portal_bucket.bucket_website_url)
    return portal_bucket


def _build_admin_dashboard(stack, cfg, proj, upload_api, user_pool, user_pool_client):
    """Admin Next.js app — CloudFront + CodeBuild."""
    dash_cfg = cfg.get("admin_dashboard") or {}
    gh_owner = dash_cfg.get("github_owner", "cal-poly-dxhub")
    gh_repo = dash_cfg.get("github_repo", "rivco-tax-connect")
    gh_branch = dash_cfg.get("github_branch", "main")

    admin_bucket = s3.Bucket(
        stack, "AdminBucket",
        bucket_name=f"{proj}-admin-{stack.account}",
        removal_policy=RemovalPolicy.DESTROY,
        auto_delete_objects=True,
        block_public_access=s3.BlockPublicAccess.BLOCK_ALL,
        encryption=s3.BucketEncryption.S3_MANAGED,
    )
    admin_oac = cloudfront.S3OriginAccessControl(
        stack, "AdminBucketOAC", description="OAC for admin dashboard bucket",
    )
    admin_distribution = cloudfront.Distribution(
        stack, "AdminDistribution",
        default_behavior=cloudfront.BehaviorOptions(
            origin=cloudfront_origins.S3BucketOrigin.with_origin_access_control(
                admin_bucket, origin_access_control=admin_oac,
            ),
            viewer_protocol_policy=cloudfront.ViewerProtocolPolicy.REDIRECT_TO_HTTPS,
            cache_policy=cloudfront.CachePolicy.CACHING_DISABLED,
            function_associations=[cloudfront.FunctionAssociation(
                function=cloudfront.Function(
                    stack, "AdminRewriteFunction",
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

    admin_build = codebuild.Project(
        stack, "AdminBuild",
        source=codebuild.Source.git_hub(owner=gh_owner, repo=gh_repo, branch_or_ref=gh_branch, clone_depth=1),
        environment=codebuild.BuildEnvironment(
            build_image=codebuild.LinuxBuildImage.STANDARD_7_0,
            compute_type=codebuild.ComputeType.SMALL,
        ),
        # No `artifacts` here — we sync directly with `aws s3 sync --delete`
        # in post_build. CodeBuild's default Artifacts.s3 only ADDs files;
        # stale chunks from prior builds linger forever and get re-served
        # from S3 even after the new index.html stops referencing them.
        environment_variables={
            "API_URL": codebuild.BuildEnvironmentVariable(value=upload_api.url.rstrip("/")),
            "USER_POOL_ID": codebuild.BuildEnvironmentVariable(value=user_pool.user_pool_id),
            "USER_POOL_CLIENT_ID": codebuild.BuildEnvironmentVariable(value=user_pool_client.user_pool_client_id),
            "TARGET_BUCKET": codebuild.BuildEnvironmentVariable(value=admin_bucket.bucket_name),
            "DISTRIBUTION_ID": codebuild.BuildEnvironmentVariable(value=admin_distribution.distribution_id),
        },
        build_spec=codebuild.BuildSpec.from_object({
            "version": "0.2",
            "phases": {
                "install": {
                    "runtime-versions": {"nodejs": "22"},
                    "commands": ["cd admin-dashboard", "corepack enable", "yarn install --immutable --ignore-engines"],
                },
                "build": {
                    "commands": [
                        'printf \'window.__APP_CONFIG__ = {"API_URL":"%s","USER_POOL_ID":"%s","USER_POOL_CLIENT_ID":"%s"};\\n\' "$API_URL" "$USER_POOL_ID" "$USER_POOL_CLIENT_ID" > public/config.js',
                        "yarn build",
                    ],
                },
                "post_build": {
                    "commands": [
                        'cd "$CODEBUILD_SRC_DIR"',
                        'aws s3 sync admin-dashboard/out/ "s3://$TARGET_BUCKET/" --delete',
                        'aws cloudfront create-invalidation --distribution-id "$DISTRIBUTION_ID" --paths "/*"',
                    ],
                },
            },
        }),
        logging=codebuild.LoggingOptions(
            cloud_watch=codebuild.CloudWatchLoggingOptions(
                log_group=logs.LogGroup(
                    stack, "AdminBuildLogGroup",
                    removal_policy=RemovalPolicy.DESTROY,
                    retention=logs.RetentionDays.ONE_WEEK,
                ),
            ),
        ),
    )
    admin_distribution.grant(admin_build.role, "cloudfront:CreateInvalidation")
    # `aws s3 sync --delete` reads existing objects to compare ETags + lists
    # the bucket; grant_write alone misses ListBucket/GetObject.
    admin_bucket.grant_read_write(admin_build)

    trigger = cr.AwsCustomResource(
        stack, "TriggerAdminBuild",
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
            iam.PolicyStatement(actions=["codebuild:StartBuild"], resources=[admin_build.project_arn]),
        ]),
    )
    trigger.node.add_dependency(admin_build)

    CfnOutput(stack, "AdminDashboardUrl", value=f"https://{admin_distribution.distribution_domain_name}")
    CfnOutput(stack, "AdminBuildProjectName", value=admin_build.project_name)
    return admin_distribution, admin_bucket


def _build_claimant_portal(stack, cfg, proj, upload_api, upload_fn, fn, admin_distribution, portal_origin):
    """Claimant Next.js app — CloudFront + CodeBuild. Also wires env vars that depend on both distributions."""
    dash_cfg = cfg.get("admin_dashboard") or {}
    gh_owner = dash_cfg.get("github_owner", "cal-poly-dxhub")
    gh_repo = dash_cfg.get("github_repo", "rivco-tax-connect")
    gh_branch = dash_cfg.get("github_branch", "main")

    claimant_bucket = s3.Bucket(
        stack, "ClaimantBucket",
        bucket_name=f"{proj}-claimant-{stack.account}",
        removal_policy=RemovalPolicy.DESTROY,
        auto_delete_objects=True,
        block_public_access=s3.BlockPublicAccess.BLOCK_ALL,
        encryption=s3.BucketEncryption.S3_MANAGED,
    )
    claimant_oac = cloudfront.S3OriginAccessControl(
        stack, "ClaimantBucketOAC", description="OAC for claimant portal bucket",
    )
    claimant_distribution = cloudfront.Distribution(
        stack, "ClaimantDistribution",
        default_behavior=cloudfront.BehaviorOptions(
            origin=cloudfront_origins.S3BucketOrigin.with_origin_access_control(
                claimant_bucket, origin_access_control=claimant_oac,
            ),
            viewer_protocol_policy=cloudfront.ViewerProtocolPolicy.REDIRECT_TO_HTTPS,
            cache_policy=cloudfront.CachePolicy.CACHING_DISABLED,
            function_associations=[cloudfront.FunctionAssociation(
                function=cloudfront.Function(
                    stack, "ClaimantRewriteFunction",
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
        stack, "ClaimantBuild",
        source=codebuild.Source.git_hub(owner=gh_owner, repo=gh_repo, branch_or_ref=gh_branch, clone_depth=1),
        environment=codebuild.BuildEnvironment(
            build_image=codebuild.LinuxBuildImage.STANDARD_7_0,
            compute_type=codebuild.ComputeType.SMALL,
        ),
        # No `artifacts` — see admin_build for rationale. We sync with --delete
        # in post_build so stale Next chunks from prior builds don't linger.
        environment_variables={
            "API_URL": codebuild.BuildEnvironmentVariable(value=upload_api.url.rstrip("/")),
            "TARGET_BUCKET": codebuild.BuildEnvironmentVariable(value=claimant_bucket.bucket_name),
            "DISTRIBUTION_ID": codebuild.BuildEnvironmentVariable(value=claimant_distribution.distribution_id),
        },
        build_spec=codebuild.BuildSpec.from_object({
            "version": "0.2",
            "phases": {
                "install": {
                    "runtime-versions": {"nodejs": "22"},
                    "commands": [
                        "corepack enable || npm install -g yarn",
                        "cd claimant-portal",
                        "if [ -f yarn.lock ]; then yarn install --immutable; else npm ci; fi",
                    ],
                },
                "build": {
                    "commands": [
                        'printf \'window.__CLAIMANT_CONFIG__ = {"API_URL":"%s"};\\n\' "$API_URL" > public/config.js',
                        'if [ -f yarn.lock ]; then yarn build; else npm run build; fi',
                    ],
                },
                "post_build": {
                    "commands": [
                        'cd "$CODEBUILD_SRC_DIR"',
                        'aws s3 sync claimant-portal/out/ "s3://$TARGET_BUCKET/" --delete',
                        'aws cloudfront create-invalidation --distribution-id "$DISTRIBUTION_ID" --paths "/*"',
                    ],
                },
            },
        }),
        logging=codebuild.LoggingOptions(
            cloud_watch=codebuild.CloudWatchLoggingOptions(
                log_group=logs.LogGroup(
                    stack, "ClaimantBuildLogGroup",
                    removal_policy=RemovalPolicy.DESTROY,
                    retention=logs.RetentionDays.ONE_WEEK,
                ),
            ),
        ),
    )
    claimant_distribution.grant(claimant_build.role, "cloudfront:CreateInvalidation")
    # `aws s3 sync --delete` reads existing objects to compare ETags + lists
    # the bucket; grant_write alone misses ListBucket/GetObject.
    claimant_bucket.grant_read_write(claimant_build)

    claimant_trigger = cr.AwsCustomResource(
        stack, "TriggerClaimantBuild",
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
            iam.PolicyStatement(actions=["codebuild:StartBuild"], resources=[claimant_build.project_arn]),
        ]),
    )
    claimant_trigger.node.add_dependency(claimant_build)

    fn.add_environment("UPLOAD_PORTAL_URL", f"https://{claimant_distribution.distribution_domain_name}")
    upload_fn.add_environment("CLAIMANT_SECRET", "CHANGEME-set-in-ssm")
    upload_fn.add_environment(
        "ALLOWED_ORIGINS",
        f"{portal_origin},"
        f"https://{admin_distribution.distribution_domain_name},"
        f"https://{claimant_distribution.distribution_domain_name}",
    )

    CfnOutput(stack, "ClaimantPortalUrl", value=f"https://{claimant_distribution.distribution_domain_name}")
    CfnOutput(stack, "ClaimantBuildProjectName", value=claimant_build.project_name)
    return claimant_distribution


def _build_notifications(stack, cfg, proj, submissions_table, admin_config_table, user_pool, admin_distribution):
    """DynamoDB stream → notification Lambda → SES."""
    notif_cfg = cfg.get("notifications") or {}
    notif_sender = notif_cfg.get("sender", "")

    # `manage_ses_identity: false` lets a parallel stack reuse a sender that
    # was verified by another stack or out-of-band. CloudFormation can't import
    # an existing SES identity so re-creating it would fail with "already exists".
    if notif_sender and notif_cfg.get("manage_ses_identity", True):
        ses.EmailIdentity(
            stack, "NotificationSenderIdentity",
            identity=ses.Identity.email(notif_sender),
        )

    notif_fn = _lambda.Function(
        stack, "NotificationHandler",
        function_name=f"{proj}-notification-handler",
        runtime=_lambda.Runtime.PYTHON_3_12,
        handler="lambda_function.lambda_handler",
        code=_lambda.Code.from_asset("cdk/notification_handler"),
        timeout=Duration.seconds(30),
        memory_size=128,
        environment={
            "ADMIN_CONFIG_TABLE": admin_config_table.table_name,
            "USER_POOL_ID": user_pool.user_pool_id,
            "DASHBOARD_URL": f"https://{admin_distribution.distribution_domain_name}/dashboard",
            "SES_SENDER": notif_sender,
            "NOTIFICATIONS_MODE": notif_cfg.get("mode", "ses"),
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
    return notif_fn


def _build_security(stack, cfg, proj, upload_api, uploads_bucket, s3_key, chat_fn, upload_fn, notif_fn, fn):
    """GuardDuty Malware Protection, WAF, CloudWatch alarms."""
    # GuardDuty allows only one detector per account/region; a parallel stack
    # in the same account must NOT try to create a second one. Set
    # `security.manage_guardduty_detector: false` when reusing a detector
    # already created by another stack. The MalwareProtectionPlan below is
    # bucket-scoped and safe to create independently.
    sec_cfg = cfg.get("security") or {}
    if sec_cfg.get("manage_guardduty_detector", True):
        guardduty.CfnDetector(
            stack, "GuardDutyDetector",
            enable=True,
            features=[guardduty.CfnDetector.CFNFeatureConfigurationProperty(
                name="S3_DATA_EVENTS", status="ENABLED",
            )],
        )
    malware_role = iam.Role(
        stack, "MalwareProtectionRole",
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
        stack, "MalwareProtection",
        protected_resource=guardduty.CfnMalwareProtectionPlan.CFNProtectedResourceProperty(
            s3_bucket=guardduty.CfnMalwareProtectionPlan.S3BucketProperty(
                bucket_name=uploads_bucket.bucket_name,
                object_prefixes=["submissions/"],
            ),
        ),
        actions=guardduty.CfnMalwareProtectionPlan.CFNActionsProperty(
            tagging=guardduty.CfnMalwareProtectionPlan.CFNTaggingProperty(status="ENABLED"),
        ),
        role=malware_role.role_arn,
    )

    waf_acl = wafv2.CfnWebACL(
        stack, "ApiWaf",
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
                name="AWSManagedRulesCommonRuleSet", priority=10,
                statement=wafv2.CfnWebACL.StatementProperty(
                    managed_rule_group_statement=wafv2.CfnWebACL.ManagedRuleGroupStatementProperty(
                        vendor_name="AWS", name="AWSManagedRulesCommonRuleSet",
                    ),
                ),
                override_action=wafv2.CfnWebACL.OverrideActionProperty(none={}),
                visibility_config=wafv2.CfnWebACL.VisibilityConfigProperty(
                    cloud_watch_metrics_enabled=True, metric_name="CommonRuleSet", sampled_requests_enabled=True,
                ),
            ),
            wafv2.CfnWebACL.RuleProperty(
                name="AWSManagedRulesKnownBadInputsRuleSet", priority=20,
                statement=wafv2.CfnWebACL.StatementProperty(
                    managed_rule_group_statement=wafv2.CfnWebACL.ManagedRuleGroupStatementProperty(
                        vendor_name="AWS", name="AWSManagedRulesKnownBadInputsRuleSet",
                    ),
                ),
                override_action=wafv2.CfnWebACL.OverrideActionProperty(none={}),
                visibility_config=wafv2.CfnWebACL.VisibilityConfigProperty(
                    cloud_watch_metrics_enabled=True, metric_name="KnownBadInputs", sampled_requests_enabled=True,
                ),
            ),
            wafv2.CfnWebACL.RuleProperty(
                name="RateLimit", priority=30,
                statement=wafv2.CfnWebACL.StatementProperty(
                    rate_based_statement=wafv2.CfnWebACL.RateBasedStatementProperty(
                        limit=100, aggregate_key_type="IP", evaluation_window_sec=300,
                    ),
                ),
                action=wafv2.CfnWebACL.RuleActionProperty(block={}),
                visibility_config=wafv2.CfnWebACL.VisibilityConfigProperty(
                    cloud_watch_metrics_enabled=True, metric_name="RateLimit", sampled_requests_enabled=True,
                ),
            ),
        ],
    )
    waf_assoc = wafv2.CfnWebACLAssociation(
        stack, "ApiWafAssociation",
        resource_arn=f"arn:aws:apigateway:{stack.region}::/restapis/{upload_api.rest_api_id}/stages/prod",
        web_acl_arn=waf_acl.attr_arn,
    )
    # Explicit dependency: the WAF association references the `prod` stage by
    # ARN string, so CFN can't infer that the stage must exist first. On a
    # cold deploy the association tries to attach before the stage finishes
    # creating and fails with "resource doesn't exist".
    waf_assoc.node.add_dependency(upload_api.deployment_stage)

    alarm_topic_arn = (cfg.get("monitoring") or {}).get("alarm_sns_topic_arn", "")

    def _alarm(construct_id, metric, threshold, description, periods=1):
        alarm = cloudwatch.Alarm(
            stack, construct_id,
            alarm_name=f"{proj}-{construct_id}",
            alarm_description=description,
            metric=metric,
            threshold=threshold,
            evaluation_periods=periods,
            comparison_operator=cloudwatch.ComparisonOperator.GREATER_THAN_OR_EQUAL_TO_THRESHOLD,
            treat_missing_data=cloudwatch.TreatMissingData.NOT_BREACHING,
        )
        if alarm_topic_arn:
            import aws_cdk.aws_sns as sns
            import aws_cdk.aws_cloudwatch_actions as cw_actions
            topic = sns.Topic.from_topic_arn(stack, f"Topic-{construct_id}", alarm_topic_arn)
            alarm.add_alarm_action(cw_actions.SnsAction(topic))

    for fn_obj, name in [(fn, "runtime"), (chat_fn, "chat"), (upload_fn, "upload"), (notif_fn, "notif")]:
        _alarm(
            f"lambda-{name}-errors",
            fn_obj.metric_errors(period=Duration.minutes(5)),
            threshold=3,
            description=f"{name} Lambda error rate ≥ 3 in 5 min",
        )
    _alarm("api-4xx", upload_api.metric_client_error(period=Duration.minutes(5)), threshold=20,
           description="Upload API 4xx errors ≥ 20 in 5 min")
    _alarm("api-5xx", upload_api.metric_server_error(period=Duration.minutes(5)), threshold=5,
           description="Upload API 5xx errors ≥ 5 in 5 min")
    _alarm("failed-verifications",
           cloudwatch.Metric(namespace=f"{proj}/Chat", metric_name="VerificationFailure",
                             period=Duration.minutes(10), statistic="Sum"),
           threshold=10, description="≥ 10 identity verification failures in 10 min")
    _alarm("ingestion-failures", notif_fn.metric_errors(period=Duration.minutes(5)),
           threshold=1, description="Notification/ingestion Lambda any error")

    CfnOutput(stack, "WafAclArn", value=waf_acl.attr_arn)


# ── Optional: chat-tester admin tool ──────────────────────────────────────────
# A throwaway Next.js + shadcn page that talks to the production chat
# WebSocket. Gated by `chat_tester.enabled: true` in config.yaml so the whole
# thing can be removed by flipping one flag — no other constructs reference
# anything created here.

def _build_chat_tester(stack, cfg, proj, ws_api, ws_stage):
    """Self-contained admin chatbot tester (Next.js static export + CodeBuild)."""
    tester_cfg = cfg.get("chat_tester") or {}
    if not tester_cfg.get("enabled", False):
        return None

    dash_cfg = cfg.get("admin_dashboard") or {}
    gh_owner = dash_cfg.get("github_owner", "cal-poly-dxhub")
    gh_repo = dash_cfg.get("github_repo", "rivco-tax-connect")
    gh_branch = dash_cfg.get("github_branch", "main")

    bucket = s3.Bucket(
        stack, "ChatTesterBucket",
        bucket_name=f"{proj}-chat-tester-{stack.account}",
        removal_policy=RemovalPolicy.DESTROY,
        auto_delete_objects=True,
        block_public_access=s3.BlockPublicAccess.BLOCK_ALL,
        encryption=s3.BucketEncryption.S3_MANAGED,
    )
    oac = cloudfront.S3OriginAccessControl(
        stack, "ChatTesterBucketOAC", description="OAC for chat-tester bucket",
    )
    distribution = cloudfront.Distribution(
        stack, "ChatTesterDistribution",
        default_behavior=cloudfront.BehaviorOptions(
            origin=cloudfront_origins.S3BucketOrigin.with_origin_access_control(
                bucket, origin_access_control=oac,
            ),
            viewer_protocol_policy=cloudfront.ViewerProtocolPolicy.REDIRECT_TO_HTTPS,
            cache_policy=cloudfront.CachePolicy.CACHING_DISABLED,
            function_associations=[cloudfront.FunctionAssociation(
                function=cloudfront.Function(
                    stack, "ChatTesterRewriteFunction",
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

    ws_endpoint = f"wss://{ws_api.api_id}.execute-api.{stack.region}.amazonaws.com/{ws_stage.stage_name}"

    build = codebuild.Project(
        stack, "ChatTesterBuild",
        source=codebuild.Source.git_hub(owner=gh_owner, repo=gh_repo, branch_or_ref=gh_branch, clone_depth=1),
        environment=codebuild.BuildEnvironment(
            build_image=codebuild.LinuxBuildImage.STANDARD_7_0,
            compute_type=codebuild.ComputeType.SMALL,
        ),
        environment_variables={
            "WS_ENDPOINT": codebuild.BuildEnvironmentVariable(value=ws_endpoint),
            "TARGET_BUCKET": codebuild.BuildEnvironmentVariable(value=bucket.bucket_name),
            "DISTRIBUTION_ID": codebuild.BuildEnvironmentVariable(value=distribution.distribution_id),
        },
        build_spec=codebuild.BuildSpec.from_object({
            "version": "0.2",
            "phases": {
                "install": {
                    "runtime-versions": {"nodejs": "22"},
                    "commands": [
                        "cd chat-tester",
                        "npm ci",
                    ],
                },
                "build": {
                    "commands": [
                        'printf \'window.__CHAT_TESTER_CONFIG__ = {"WS_ENDPOINT":"%s"};\\n\' "$WS_ENDPOINT" > public/config.js',
                        "npm run build",
                    ],
                },
                "post_build": {
                    "commands": [
                        'cd "$CODEBUILD_SRC_DIR"',
                        'aws s3 sync chat-tester/out/ "s3://$TARGET_BUCKET/" --delete',
                        'aws cloudfront create-invalidation --distribution-id "$DISTRIBUTION_ID" --paths "/*"',
                    ],
                },
            },
        }),
        logging=codebuild.LoggingOptions(
            cloud_watch=codebuild.CloudWatchLoggingOptions(
                log_group=logs.LogGroup(
                    stack, "ChatTesterBuildLogGroup",
                    removal_policy=RemovalPolicy.DESTROY,
                    retention=logs.RetentionDays.ONE_WEEK,
                ),
            ),
        ),
    )
    distribution.grant(build.role, "cloudfront:CreateInvalidation")
    bucket.grant_read_write(build)

    trigger = cr.AwsCustomResource(
        stack, "TriggerChatTesterBuild",
        on_create=cr.AwsSdkCall(
            service="CodeBuild", action="startBuild",
            parameters={"projectName": build.project_name},
            physical_resource_id=cr.PhysicalResourceId.of(f"chat-tester-build-{int(_time.time())}"),
            output_paths=["build.id"],
        ),
        on_update=cr.AwsSdkCall(
            service="CodeBuild", action="startBuild",
            parameters={"projectName": build.project_name},
            physical_resource_id=cr.PhysicalResourceId.of(f"chat-tester-build-{int(_time.time())}"),
            output_paths=["build.id"],
        ),
        policy=cr.AwsCustomResourcePolicy.from_statements([
            iam.PolicyStatement(actions=["codebuild:StartBuild"], resources=[build.project_arn]),
        ]),
    )
    trigger.node.add_dependency(build)

    CfnOutput(stack, "ChatTesterUrl", value=f"https://{distribution.distribution_domain_name}")
    CfnOutput(stack, "ChatTesterBuildProjectName", value=build.project_name)
    return distribution


# ── Stack ─────────────────────────────────────────────────────────────────────

class RiversideTaxRefundStack(Stack):
    def __init__(self, scope: Construct, id: str, **kwargs) -> None:
        super().__init__(scope, id, **kwargs)

        cfg = load_config()
        proj = cfg['project']['name']
        portal_origin = f"http://{proj}-portal-{self.account}.s3-website-{self.region}.amazonaws.com"

        bucket, fn, s3_key = _build_storage(self, cfg, proj)

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
                    s3.Transition(storage_class=s3.StorageClass.GLACIER_INSTANT_RETRIEVAL,
                                  transition_after=Duration.days(cfg.get('retention', {}).get('hot_days', 90))),
                    s3.Transition(storage_class=s3.StorageClass.GLACIER,
                                  transition_after=Duration.days(cfg.get('retention', {}).get('warm_days', 365))),
                    s3.Transition(storage_class=s3.StorageClass.DEEP_ARCHIVE,
                                  transition_after=Duration.days(cfg.get('retention', {}).get('cold_days', 1825))),
                ],
                expiration=Duration.days(cfg.get('retention', {}).get('expire_days', 2555)),
            )],
            cors=[
                s3.CorsRule(allowed_methods=[s3.HttpMethods.PUT], allowed_origins=["*"],
                            allowed_headers=["*"], max_age=3600),
                s3.CorsRule(allowed_methods=[s3.HttpMethods.GET], allowed_origins=["*"],
                            allowed_headers=["*"], max_age=3600),
            ],
        )

        chat_fn, ws_stage, ws_api, chat_table = _build_chat(self, cfg, proj, fn)
        upload_fn, upload_api, submissions_table, admin_config_table, user_pool, user_pool_client, authorizer = \
            _build_upload_api(self, cfg, proj, uploads_bucket, chat_table, portal_origin)
        _build_portal(self, cfg, proj, upload_api, ws_api, ws_stage)
        admin_distribution, admin_bucket = _build_admin_dashboard(self, cfg, proj, upload_api, user_pool, user_pool_client)
        _build_claimant_portal(self, cfg, proj, upload_api, upload_fn, fn, admin_distribution, portal_origin)
        notif_fn = _build_notifications(self, cfg, proj, submissions_table, admin_config_table, user_pool, admin_distribution)
        _build_security(self, cfg, proj, upload_api, uploads_bucket, s3_key, chat_fn, upload_fn, notif_fn, fn)
        _build_chat_tester(self, cfg, proj, ws_api, ws_stage)
