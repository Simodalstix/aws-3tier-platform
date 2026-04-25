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

        # --- networking (synth-time lookups) ---
        vpc_id = ssm.StringParameter.value_from_lookup(
            self, "/ops-lab/networking/vpc-id"
        )
        vpc = ec2.Vpc.from_lookup(self, "Vpc", vpc_id=vpc_id)

        isolated_subnets = [
            ec2.Subnet.from_subnet_id(
                self,
                f"IsolatedSubnet{i}",
                ssm.StringParameter.value_from_lookup(
                    self, f"/ops-lab/networking/subnet/isolated-{i}"
                ),
            )
            for i in range(3)
        ]

        # --- security groups (ingress rules added by AppTierStack) ---
        self.rds_sg = ec2.SecurityGroup(
            self, "RdsSg",
            vpc=vpc,
            security_group_name="ops-lab-3tier-rds-sg",
            description="RDS PostgreSQL",
        )
        self.cache_sg = ec2.SecurityGroup(
            self, "CacheSg",
            vpc=vpc,
            security_group_name="ops-lab-3tier-cache-sg",
            description="ElastiCache Redis",
        )

        # --- RDS PostgreSQL ---
        # single-AZ, t3.micro — Multi-AZ can be enabled when demonstrating FIS failover
        db = rds.DatabaseInstance(
            self, "Rds",
            instance_identifier="ops-lab-3tier-rds",
            engine=rds.DatabaseInstanceEngine.postgres(
                version=rds.PostgresEngineVersion.VER_16_13,
            ),
            instance_type=ec2.InstanceType.of(
                ec2.InstanceClass.T3, ec2.InstanceSize.MICRO
            ),
            vpc=vpc,
            vpc_subnets=ec2.SubnetSelection(subnets=isolated_subnets),
            security_groups=[self.rds_sg],
            credentials=rds.Credentials.from_generated_secret("appuser"),
            database_name="appdb",
            allocated_storage=20,
            backup_retention=cdk.Duration.days(1),
            deletion_protection=False,
            removal_policy=cdk.RemovalPolicy.DESTROY,
            storage_encrypted=True,
            multi_az=False,
            publicly_accessible=False,
        )

        # --- ElastiCache Redis (single node, L1 constructs — no CDK L2 for ElastiCache) ---
        cache_subnet_group = elasticache.CfnSubnetGroup(
            self, "CacheSubnetGroup",
            cache_subnet_group_name="ops-lab-3tier-cache-subnet-group",
            description="Isolated subnets for Redis",
            subnet_ids=[s.subnet_id for s in isolated_subnets],
        )

        redis = elasticache.CfnCacheCluster(
            self, "Redis",
            cluster_name="ops-lab-3tier-redis",
            cache_node_type="cache.t3.micro",
            engine="redis",
            num_cache_nodes=1,
            cache_subnet_group_name=cache_subnet_group.ref,
            vpc_security_group_ids=[self.cache_sg.security_group_id],
        )
        redis.add_dependency(cache_subnet_group)

        # --- SSM outputs (consumed by AppTierStack and the app) ---
        ssm.StringParameter(
            self, "RdsEndpointParam",
            parameter_name="/ops-lab/3tier/rds-endpoint",
            string_value=db.db_instance_endpoint_address,
        )
        ssm.StringParameter(
            self, "RdsSecretArnParam",
            parameter_name="/ops-lab/3tier/rds-secret-arn",
            string_value=db.secret.secret_arn,  # type: ignore[union-attr]
        )
        ssm.StringParameter(
            self, "CacheEndpointParam",
            parameter_name="/ops-lab/3tier/elasticache-endpoint",
            string_value=redis.attr_redis_endpoint_address,
        )

        # Exposed for AppTierStack — grants read access to DB credentials
        self.db_secret: secretsmanager.ISecret = db.secret  # type: ignore[assignment]

