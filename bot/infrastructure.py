import json
import yaml
from aws_cdk import (
    Stack, Duration, RemovalPolicy, CfnOutput, Fn, BundlingOptions,
    aws_s3 as s3,
    aws_lambda as _lambda,
    aws_iam as iam,
    aws_lex as lex,
    aws_connect as connect,
    aws_wisdom as wisdom,
    aws_bedrockagentcore as agentcore,
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

        # Lambda environment from config
        env = {
            "S3_BUCKET": bucket.bucket_name,
            "DATA_FILE": cfg['s3']['data_file'],
            "PROMPT_WELCOME": cfg['prompts']['welcome'],
            "PROMPT_NOT_FOUND": cfg['prompts']['not_found'],
            "PROMPT_FOUND": cfg['prompts']['found'],
            "PROMPT_ERROR": cfg['prompts']['error'],
            **cfg['lambda']['environment']
        }

        # Lambda function for custom tool
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
        bot_role = iam.Role(self, "BotRole", assumed_by=iam.ServicePrincipal("lexv2.amazonaws.com"))

        # Wisdom (Q in Connect) Assistant
        assistant = wisdom.CfnAssistant(
            self, "Assistant",
            name=f"{proj}-assistant",
            type="AGENT",
            tags=[{"key": "AmazonConnectEnabled", "value": "True"}],
        )

        # Lex bot with AMAZON.QInConnectIntent for Q in Connect self-service
        bot = lex.CfnBot(
            self, "Bot",
            name=cfg['lex']['bot_name'],
            role_arn=bot_role.role_arn,
            data_privacy={"ChildDirected": False},
            idle_session_ttl_in_seconds=300,
            auto_build_bot_locales=True,
            bot_locales=[{
                "localeId": cfg['lex']['locale'],
                "nluConfidenceThreshold": cfg['lex']['nlu_threshold'],
                "intents": [
                    {
                        "name": "FallbackIntent",
                        "parentIntentSignature": "AMAZON.FallbackIntent",
                    },
                    {
                        "name": "QInConnectIntent",
                        "parentIntentSignature": "AMAZON.QInConnectIntent",
                        "dialogCodeHook": {"enabled": True},
                        "qInConnectIntentConfiguration": {
                            "qInConnectAssistantConfiguration": {
                                "assistantArn": assistant.attr_assistant_arn
                            }
                        },
                    }
                ]
            }],
        )
        bot.add_dependency(assistant)

        # Bot alias with Q in Connect enabled
        alias = lex.CfnBotAlias(
            self, "BotAlias",
            bot_alias_name=cfg['lex']['alias_name'],
            bot_id=bot.attr_id,
            bot_alias_locale_settings=[{
                "localeId": cfg['lex']['locale'],
                "botAliasLocaleSetting": {
                    "enabled": True,
                    "codeHookSpecification": {"lambdaCodeHook": {"lambdaArn": fn.function_arn, "codeHookInterfaceVersion": "1.0"}}
                }
            }],
        )

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

        # Associate Lex bot with Connect
        bot_assoc = connect.CfnIntegrationAssociation(
            self, "BotAssociation",
            instance_id=instance.attr_arn,
            integration_type="LEX_BOT",
            integration_arn=f"arn:aws:lex:{self.region}:{self.account}:bot-alias/{bot.attr_id}/{alias.attr_bot_alias_id}",
        )
        bot_assoc.add_dependency(alias)

        # Associate Wisdom assistant with Connect
        wisdom_assoc = connect.CfnIntegrationAssociation(
            self, "WisdomAssociation",
            instance_id=instance.attr_arn,
            integration_type="WISDOM_ASSISTANT",
            integration_arn=assistant.attr_assistant_arn,
        )

        # Contact flow — chat with AI agent via Lex + Wisdom
        flow_content_template = json.dumps({
            "Version": "2019-10-30",
            "StartAction": "wisdom-session",
            "Actions": [
                {
                    "Identifier": "wisdom-session",
                    "Type": "CreateWisdomSession",
                    "Parameters": {"WisdomAssistantArn": "${AssistantArn}"},
                    "Transitions": {
                        "NextAction": "get-input",
                        "Errors": [{"NextAction": "get-input", "ErrorType": "NoMatchingError"}],
                        "Conditions": []
                    }
                },
                {
                    "Identifier": "get-input",
                    "Type": "ConnectParticipantWithLexBot",
                    "Parameters": {
                        "Text": " ",
                        "LexV2Bot": {"AliasArn": "${BotAliasArn}"}
                    },
                    "Transitions": {
                        "NextAction": "get-input",
                        "Errors": [{"NextAction": "disconnect", "ErrorType": "NoMatchingError"}],
                        "Conditions": []
                    }
                },
                {
                    "Identifier": "disconnect",
                    "Type": "DisconnectParticipant",
                    "Parameters": {},
                    "Transitions": {}
                }
            ]
        })

        flow = connect.CfnContactFlow(
            self, "ContactFlow",
            instance_arn=instance.attr_arn,
            name=cfg['connect']['flow_name'],
            type="CONTACT_FLOW",
            content=Fn.sub(flow_content_template, {
                "AssistantArn": assistant.attr_assistant_arn,
                "BotAliasArn": f"arn:aws:lex:{self.region}:{self.account}:bot-alias/{bot.attr_id}/{alias.attr_bot_alias_id}",
            }),
        )
        flow.add_dependency(bot_assoc)

        CfnOutput(self, "BucketName", value=bucket.bucket_name)
        CfnOutput(self, "LambdaArn", value=fn.function_arn)
        CfnOutput(self, "BotId", value=bot.attr_id)
        CfnOutput(self, "BotAliasId", value=alias.attr_bot_alias_id)
        CfnOutput(self, "AssistantArn", value=assistant.attr_assistant_arn)
        CfnOutput(self, "ConnectInstanceId", value=instance.attr_id)
        CfnOutput(self, "ConnectInstanceArn", value=instance.attr_arn)
        CfnOutput(self, "ContactFlowArn", value=flow.attr_contact_flow_arn)

        # Gateway IAM role
        gateway_role = iam.Role(self, "GatewayRole",
            assumed_by=iam.ServicePrincipal("bedrock-agentcore.amazonaws.com"),
        )
        fn.grant_invoke(gateway_role)

        # Connect instance access URL for OIDC discovery
        connect_url = f"https://{proj}.my.connect.aws"

        # AgentCore Gateway (L1 construct) - uses Connect's OIDC for auth
        gateway = agentcore.CfnGateway(self, "TaxLookupGateway",
            name=f"{proj}-gateway",
            description="MCP Gateway for tax refund lookup tool",
            protocol_type="MCP",
            authorizer_type="CUSTOM_JWT",
            role_arn=gateway_role.role_arn,
            authorizer_configuration=agentcore.CfnGateway.AuthorizerConfigurationProperty(
                custom_jwt_authorizer=agentcore.CfnGateway.CustomJWTAuthorizerConfigurationProperty(
                    discovery_url=f"{connect_url}/.well-known/openid-configuration",
                    allowed_audience=["PLACEHOLDER"],
                ),
            ),
        )

        # Update gateway audience to its own ID (can't self-reference in CFN)
        cr.AwsCustomResource(self, "UpdateGatewayAudience",
            on_create=cr.AwsSdkCall(
                service="BedrockAgentCoreControl",
                action="updateGateway",
                parameters={
                    "gatewayIdentifier": gateway.ref,
                    "name": f"{proj}-gateway",
                    "description": "MCP Gateway for tax refund lookup tool",
                    "protocolType": "MCP",
                    "authorizerType": "CUSTOM_JWT",
                    "roleArn": gateway_role.role_arn,
                    "authorizerConfiguration": {
                        "customJWTAuthorizer": {
                            "discoveryUrl": f"{connect_url}/.well-known/openid-configuration",
                            "allowedAudience": [gateway.ref],
                        }
                    },
                },
                physical_resource_id=cr.PhysicalResourceId.of("gateway-audience-update"),
            ),
            policy=cr.AwsCustomResourcePolicy.from_statements([
                iam.PolicyStatement(
                    actions=["bedrock-agentcore:UpdateGateway"],
                    resources=[gateway.attr_gateway_arn],
                ),
            ]),
        )

        # Gateway Target with Lambda
        target = agentcore.CfnGatewayTarget(self, "TaxLookupTarget",
            name="tax-lookup",
            description="Look up tax refunds by customer name",
            gateway_identifier=gateway.ref,
            target_configuration=agentcore.CfnGatewayTarget.TargetConfigurationProperty(
                mcp=agentcore.CfnGatewayTarget.McpTargetConfigurationProperty(
                    lambda_=agentcore.CfnGatewayTarget.McpLambdaTargetConfigurationProperty(
                        lambda_arn=fn.function_arn,
                        tool_schema=agentcore.CfnGatewayTarget.ToolSchemaProperty(
                            inline_payload=[
                                agentcore.CfnGatewayTarget.ToolDefinitionProperty(
                                    name="tax_lookup",
                                    description="Look up tax refunds for a customer by their name",
                                    input_schema=agentcore.CfnGatewayTarget.SchemaDefinitionProperty(
                                        type="object",
                                        properties={
                                            "customer_name": agentcore.CfnGatewayTarget.SchemaDefinitionProperty(
                                                type="string",
                                                description="The customer's full name to search for refunds",
                                            ),
                                        },
                                        required=["customer_name"],
                                    ),
                                ),
                            ],
                        ),
                    ),
                ),
            ),
            credential_provider_configurations=[
                agentcore.CfnGatewayTarget.CredentialProviderConfigurationProperty(
                    credential_provider_type="GATEWAY_IAM_ROLE",
                ),
            ],
        )
        target.add_dependency(gateway)

        CfnOutput(self, "GatewayId", value=gateway.ref)
        CfnOutput(self, "GatewayUrl", value=gateway.attr_gateway_url)
