import aws_cdk as cdk
from aws_cdk import (
    Stack,
    aws_ec2 as ec2,
    aws_autoscaling as autoscaling,
    aws_iam as iam,
    aws_elasticloadbalancingv2 as elbv2,
    aws_ssm as ssm,
)
from constructs import Construct

from platform_lab.data_tier_stack import DataTierStack


class AppTierStack(Stack):
    """EC2 Auto Scaling Group in public subnets with SSM access."""

    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        data_tier: DataTierStack,
        **kwargs,
    ) -> None:
        super().__init__(scope, construct_id, **kwargs)

        # TODO: implement
        # Resources: instance role, launch template, ASG, target group, CPU scaling policy
        # SSM reads:  vpc-id, public-subnet-0,1,2, ssm-sg-id, cloudwatch-write-policy-arn,
        #             cw-agent-config-ssm-path
        # SSM writes: /ops-lab/3tier/asg-name, target-group-arn
        # SG rules:   allow port 5432 → data_tier.rds_sg, port 6379 → data_tier.cache_sg

        # Expose target group so PresentationStack can attach it to the ALB listener
        self.target_group: elbv2.ApplicationTargetGroup
