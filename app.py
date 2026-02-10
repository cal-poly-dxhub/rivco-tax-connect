#!/usr/bin/env python3
import os
import yaml
import aws_cdk as cdk
from bot.infrastructure import NovaSonicConnectStack

with open('config.yaml') as f:
    cfg = yaml.safe_load(f)

app = cdk.App()

NovaSonicConnectStack(
    app, cfg['project']['name'],
    env=cdk.Environment(
        account=os.environ.get("CDK_DEFAULT_ACCOUNT"),
        region=cfg['aws']['region']
    ),
)

app.synth()
