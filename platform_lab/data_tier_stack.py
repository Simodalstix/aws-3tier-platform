import aws_cdk as cdk
from aws_cdk import (
    Stack,
    aws_ec2 as ec2,
    aws_rds as rds,
    aws_elasticache as elasticache,
    aws_secretsmanager as secretsmanager,
    aws_ssm as ssm,
)
from constructs import Construct


class DataTierStack(Stack):
    """RDS PostgreSQL + ElastiCache Redis in isolated subnets."""

    def __init__(self, scope: Construct, construct_id: str, **kwargs) -> None:
        super().__init__(scope, construct_id, **kwargs)

        # TODO: implement
        # Resources: RDS SG, ElastiCache SG, DB secret, RDS instance, Redis cluster
        # SSM reads:  /ops-lab/networking/vpc-id, isolated-subnet-0,1,2, sns-topic-arn
        # SSM writes: /ops-lab/3tier/rds-endpoint, rds-secret-arn, elasticache-endpoint

        # Expose SGs so AppTierStack can add ingress rules
        self.rds_sg: ec2.SecurityGroup
        self.cache_sg: ec2.SecurityGroup
