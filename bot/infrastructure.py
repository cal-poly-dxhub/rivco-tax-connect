import yaml
from aws_cdk import (
    Stack, Duration, RemovalPolicy, CfnOutput,
    aws_s3 as s3,
    aws_lambda as _lambda,
    aws_iam as iam,
    aws_lex as lex,
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
            removal_policy=RemovalPolicy.RETAIN,
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

        # Lambda function
        fn = _lambda.Function(
            self, "Function",
            function_name=cfg['lambda']['function_name'],
            runtime=_lambda.Runtime.PYTHON_3_12,
            handler="lambda_function.lambda_handler",
            code=_lambda.Code.from_asset("bot/runtime"),
            role=role,
            timeout=Duration.seconds(cfg['lambda']['timeout_seconds']),
            memory_size=cfg['lambda']['memory_mb'],
            environment=env,
        )
        fn.add_permission("LexInvoke", principal=iam.ServicePrincipal("lexv2.amazonaws.com"), source_account=self.account)

        # Lex bot role
        bot_role = iam.Role(self, "BotRole", assumed_by=iam.ServicePrincipal("lexv2.amazonaws.com"))

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
                "voiceSettings": {"voiceId": cfg['lex']['voice_id'], "engine": "neural"},
                "intents": [
                    {
                        "name": "FallbackIntent",
                        "parentIntentSignature": "AMAZON.FallbackIntent",
                        "dialogCodeHook": {"enabled": True},
                        "initialResponseSetting": {
                            "nextStep": {"dialogAction": {"type": "InvokeDialogCodeHook"}},
                            "codeHook": {"enableCodeHookInvocation": True, "isActive": True,
                                "postCodeHookSpecification": {
                                    "successNextStep": {"dialogAction": {"type": "EndConversation"}},
                                    "failureNextStep": {"dialogAction": {"type": "EndConversation"}},
                                    "timeoutNextStep": {"dialogAction": {"type": "EndConversation"}},
                                }}
                        }
                    },
                    {
                        "name": "GreetingIntent",
                        "sampleUtterances": [{"utterance": u} for u in cfg['prompts']['fallback_utterances']],
                        "initialResponseSetting": {
                            "nextStep": {"dialogAction": {"type": "InvokeDialogCodeHook"}},
                            "codeHook": {"enableCodeHookInvocation": True, "isActive": True,
                                "postCodeHookSpecification": {
                                    "successNextStep": {"dialogAction": {"type": "EndConversation"}},
                                    "failureNextStep": {"dialogAction": {"type": "EndConversation"}},
                                    "timeoutNextStep": {"dialogAction": {"type": "EndConversation"}},
                                }}
                        }
                    }
                ]
            }],
        )

        # Bot alias
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

        CfnOutput(self, "BucketName", value=bucket.bucket_name)
        CfnOutput(self, "LambdaArn", value=fn.function_arn)
        CfnOutput(self, "BotId", value=bot.attr_id)
        CfnOutput(self, "BotAliasId", value=alias.attr_bot_alias_id)
