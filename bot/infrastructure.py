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
    aws_logs as logs,
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

        # Lambda environment
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

        # Lex bot
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
                                "assistantArn": assistant.attr_assistant_arn
                            }
                        },
                    }
                ]
            }],
        )
        bot.add_dependency(assistant)

        # Bot version
        bot_version = lex.CfnBotVersion(
            self, "BotVersion",
            bot_id=bot.attr_id,
            bot_version_locale_specification=[{
                "localeId": cfg['lex']['locale'],
                "botVersionLocaleDetails": {"sourceBotVersion": "DRAFT"}
            }],
        )

        # CloudWatch log group for Lex conversation logs
        lex_log_group = logs.LogGroup(
            self, "LexLogGroup",
            log_group_name=f"/aws/lex/{cfg['lex']['bot_name']}",
            removal_policy=RemovalPolicy.DESTROY,
        )

        # Bot alias
        alias = lex.CfnBotAlias(
            self, "BotAlias",
            bot_alias_name=cfg['lex']['alias_name'],
            bot_id=bot.attr_id,
            bot_version=bot_version.attr_bot_version,
            bot_alias_locale_settings=[{
                "localeId": cfg['lex']['locale'],
                "botAliasLocaleSetting": {
                    "enabled": True,
                    "codeHookSpecification": {"lambdaCodeHook": {"lambdaArn": fn.function_arn, "codeHookInterfaceVersion": "1.0"}}
                }
            }],
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

        # Associate Wisdom assistant with Connect
        wisdom_assoc = connect.CfnIntegrationAssociation(
            self, "WisdomAssociation",
            instance_id=instance.attr_arn,
            integration_type="WISDOM_ASSISTANT",
            integration_arn=assistant.attr_assistant_arn,
        )

        # Contact flow — loaded from JSON with parameterized ARNs
        bot_alias_arn = f"arn:aws:lex:{self.region}:{self.account}:bot-alias/{bot.attr_id}/{alias.attr_bot_alias_id}"
        ai_agent_arn = cfg['connect']['ai_agent_version_arn']

        with open(cfg['connect']['flow_file']) as f:
            flow_template = f.read()

        flow = connect.CfnContactFlow(
            self, "ContactFlow",
            instance_arn=instance.attr_arn,
            name=cfg['connect']['flow_name'],
            type="CONTACT_FLOW",
            content=Fn.sub(flow_template, {
                "AssistantArn": assistant.attr_assistant_arn,
                "AIAgentArn": ai_agent_arn,
                "BotAliasArn": bot_alias_arn,
            }),
        )
        flow.add_dependency(wisdom_assoc)

        CfnOutput(self, "BucketName", value=bucket.bucket_name)
        CfnOutput(self, "LambdaArn", value=fn.function_arn)
        CfnOutput(self, "BotId", value=bot.attr_id)
        CfnOutput(self, "BotAliasId", value=alias.attr_bot_alias_id)
        CfnOutput(self, "AssistantArn", value=assistant.attr_assistant_arn)
        CfnOutput(self, "ConnectInstanceId", value=instance.attr_id)
        CfnOutput(self, "ConnectInstanceArn", value=instance.attr_arn)
        CfnOutput(self, "ContactFlowArn", value=flow.attr_contact_flow_arn)
