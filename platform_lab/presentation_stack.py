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

        # --- networking (synth-time lookups) ---
        vpc_id = ssm.StringParameter.value_from_lookup(
            self, "/ops-lab/networking/vpc-id"
        )
        vpc = ec2.Vpc.from_lookup(self, "Vpc", vpc_id=vpc_id)

        # --- ALB security group ---
        alb_sg = ec2.SecurityGroup(
            self, "AlbSg",
            vpc=vpc,
            security_group_name="ops-lab-3tier-alb-sg",
            description="ALB - public HTTP ingress",
        )
        alb_sg.add_ingress_rule(
            peer=ec2.Peer.any_ipv4(),
            connection=ec2.Port.tcp(80),
            description="Public HTTP",
        )

        # Allow ALB to reach app instances on port 8080
        ec2.CfnSecurityGroupIngress(
            self, "AppFromAlb",
            group_id=app_tier.app_sg.security_group_id,
            ip_protocol="tcp",
            from_port=8080,
            to_port=8080,
            source_security_group_id=alb_sg.security_group_id,
            description="From ALB",
        )

        # --- ALB ---
        alb = elbv2.ApplicationLoadBalancer(
            self, "Alb",
            load_balancer_name="ops-lab-3tier-alb",
            vpc=vpc,
            internet_facing=True,
            security_group=alb_sg,
            vpc_subnets=ec2.SubnetSelection(subnet_type=ec2.SubnetType.PUBLIC),
        )

        # CfnListener (L1) — bypasses CDK's SG management entirely. The L2 add_listener
        # calls loadBalancerAttachedToTargetGroup even with open=False, which would create
        # an ingress rule on app_sg (AppTierStack) referencing alb_sg (this stack),
        # forming a cross-stack cycle. We handle SG connectivity manually with AppFromAlb.
        elbv2.CfnListener(
            self, "HttpListener",
            default_actions=[
                elbv2.CfnListener.ActionProperty(
                    type="forward",
                    target_group_arn=app_tier.target_group.target_group_arn,
                )
            ],
            load_balancer_arn=alb.load_balancer_arn,
            port=80,
            protocol="HTTP",
        )

        # --- SSM output ---
        ssm.StringParameter(
            self, "AlbDnsParam",
            parameter_name="/ops-lab/3tier/alb-dns-name",
            string_value=alb.load_balancer_dns_name,
        )
