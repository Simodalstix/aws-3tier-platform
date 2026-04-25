import aws_cdk as cdk
from aws_cdk import (
    Stack,
    aws_ec2 as ec2,
    aws_elasticloadbalancingv2 as elbv2,
    aws_ssm as ssm,
)
from constructs import Construct

from platform_lab.app_tier_stack import AppTierStack


class PresentationStack(Stack):
    """Application Load Balancer in public subnets."""

    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        app_tier: AppTierStack,
        **kwargs,
    ) -> None:
        super().__init__(scope, construct_id, **kwargs)

        # TODO: implement
        # Resources: ALB SG, ALB, HTTP listener → app_tier.target_group
        # SSM reads:  vpc-id, public-subnet-0,1,2
        # SSM writes: /ops-lab/3tier/alb-dns-name
