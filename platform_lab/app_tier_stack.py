import base64
import typing

import aws_cdk as cdk
from aws_cdk import (
    Stack,
    aws_autoscaling as autoscaling,
    aws_ec2 as ec2,
    aws_elasticloadbalancingv2 as elbv2,
    aws_iam as iam,
    aws_logs as logs,
    aws_ssm as ssm,
)
from constructs import Construct

from platform_lab.data_tier_stack import DataTierStack


_APP_CODE = '''\
import json
import os
import random
import string
import time
from contextlib import asynccontextmanager

import boto3
import psycopg
import redis as redis_lib
from fastapi import FastAPI, HTTPException
from fastapi.responses import RedirectResponse
from pydantic import BaseModel

REGION = os.environ["AWS_REGION"]
DB_SECRET_ARN = os.environ["DB_SECRET_ARN"]
RDS_HOST = os.environ["RDS_HOST"]
CACHE_HOST = os.environ["CACHE_HOST"]

_sm = boto3.client("secretsmanager", region_name=REGION)
_cw = boto3.client("cloudwatch", region_name=REGION)


def _creds():
    secret = json.loads(_sm.get_secret_value(SecretId=DB_SECRET_ARN)["SecretString"])
    return secret["username"], secret["password"]


def _db():
    u, p = _creds()
    return psycopg.connect(
        host=RDS_HOST, port=5432, dbname="appdb",
        user=u, password=p, connect_timeout=5,
    )


def _cache():
    return redis_lib.Redis(
        host=CACHE_HOST, port=6379, socket_timeout=2, decode_responses=True,
    )


def _emit(name, value, unit="Count"):
    try:
        _cw.put_metric_data(
            Namespace="OpsLab/3tier",
            MetricData=[{"MetricName": name, "Value": value, "Unit": unit}],
        )
    except Exception:
        pass


def _code():
    return "".join(random.choices(string.ascii_letters + string.digits, k=7))


@asynccontextmanager
async def lifespan(app: FastAPI):
    with _db() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS links (
                code        VARCHAR(10) PRIMARY KEY,
                url         TEXT        NOT NULL,
                hits        INTEGER     DEFAULT 0,
                created_at  TIMESTAMPTZ DEFAULT NOW()
            )
            """
        )
    yield


app = FastAPI(lifespan=lifespan)


class ShortenRequest(BaseModel):
    url: str


@app.get("/health")
def health():
    result = {"status": "ok", "db": "ok", "cache": "ok"}
    try:
        with _db() as conn:
            conn.execute("SELECT 1")
    except Exception as exc:
        raise HTTPException(status_code=503, detail={"db": str(exc)})
    try:
        _cache().ping()
    except Exception as exc:
        result["cache"] = f"degraded: {exc}"
    return result


@app.post("/links", status_code=201)
def shorten(req: ShortenRequest):
    code = _code()
    with _db() as conn:
        conn.execute("INSERT INTO links (code, url) VALUES (%s, %s)", (code, req.url))
    try:
        _cache().setex(f"link:{code}", 3600, req.url)
    except Exception:
        pass
    return {"code": code, "url": req.url}


@app.get("/links/{code}/stats")
def stats(code: str):
    with _db() as conn:
        row = conn.execute(
            "SELECT url, hits, created_at FROM links WHERE code = %s", (code,)
        ).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="not found")
    return {"code": code, "url": row[0], "hits": row[1], "created_at": str(row[2])}


@app.get("/{code}")
def redirect(code: str):
    t0 = time.monotonic()
    url = None

    try:
        url = _cache().get(f"link:{code}")
        _emit("CacheHit" if url else "CacheMiss", 1)
    except Exception:
        _emit("CacheError", 1)

    if not url:
        with _db() as conn:
            row = conn.execute(
                "SELECT url FROM links WHERE code = %s", (code,)
            ).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="not found")
        url = row[0]
        try:
            _cache().setex(f"link:{code}", 3600, url)
        except Exception:
            pass

    _emit("RedirectLatencyMs", (time.monotonic() - t0) * 1000, "Milliseconds")

    try:
        with _db() as conn:
            conn.execute("UPDATE links SET hits = hits + 1 WHERE code = %s", (code,))
    except Exception:
        pass

    return RedirectResponse(url=url, status_code=302)
'''


_SYSTEMD_UNIT = '''\
[Unit]
Description=URL Shortener
After=network.target

[Service]
Environment=AWS_REGION=__REGION__
Environment=DB_SECRET_ARN=__DB_SECRET_ARN__
Environment=RDS_HOST=__RDS_HOST__
Environment=CACHE_HOST=__CACHE_HOST__
WorkingDirectory=/opt/app
ExecStart=/usr/local/bin/uvicorn main:app --host 0.0.0.0 --port 8080
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
'''


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

        # --- networking (synth-time lookups) ---
        vpc_id = ssm.StringParameter.value_from_lookup(
            self, "/ops-lab/networking/vpc-id"
        )
        vpc = ec2.Vpc.from_lookup(self, "Vpc", vpc_id=vpc_id)

        # Individual subnet IDs available via SSM /ops-lab/networking/subnet/public-{0,1,2}
        # but CDK requires SubnetType.PUBLIC for associate_public_ip_address — use VPC type
        # selection here; the VPC lookup has already resolved the correct subnets.
        public_subnet_selection = ec2.SubnetSelection(
            subnet_type=ec2.SubnetType.PUBLIC
        )

        ssm_sg_id = ssm.StringParameter.value_from_lookup(
            self, "/ops-lab/networking/ssm-sg-id"
        )
        ssm_sg = ec2.SecurityGroup.from_security_group_id(
            self, "SsmSg", ssm_sg_id, allow_all_outbound=False
        )

        cw_policy_arn = ssm.StringParameter.value_from_lookup(
            self, "/ops-lab/shared/cloudwatch-write-policy-arn"
        )

        # --- log group for app and cloud-init logs ---
        logs.LogGroup(
            self, "AppLogGroup",
            log_group_name="/ops-lab/3tier/app",
            retention=logs.RetentionDays.ONE_MONTH,
            removal_policy=cdk.RemovalPolicy.DESTROY,
        )

        # --- instance role ---
        role = iam.Role(
            self, "InstanceRole",
            role_name="ops-lab-3tier-instance-role",
            assumed_by=iam.ServicePrincipal("ec2.amazonaws.com"),
            managed_policies=[
                iam.ManagedPolicy.from_aws_managed_policy_name(
                    "AmazonSSMManagedInstanceCore"
                ),
                iam.ManagedPolicy.from_managed_policy_arn(
                    self, "CwPolicy", cw_policy_arn
                ),
            ],
        )
        # Allow the app to fetch DB credentials at runtime
        data_tier.db_secret.grant_read(role)

        # --- app security group (ALB ingress added by PresentationStack) ---
        self.app_sg = ec2.SecurityGroup(
            self, "AppSg",
            vpc=vpc,
            security_group_name="ops-lab-3tier-app-sg",
            description="App tier EC2 instances",
        )

        # Ingress rules live here (not in DataTierStack) to keep the dependency
        # one-directional: AppTierStack → DataTierStack. Using add_ingress_rule on
        # data_tier SGs would create a CDK cross-stack export back to AppTierStack,
        # which combined with the secret reference would form a cycle.
        ec2.CfnSecurityGroupIngress(
            self, "RdsFromApp",
            group_id=data_tier.rds_sg.security_group_id,
            ip_protocol="tcp",
            from_port=5432,
            to_port=5432,
            source_security_group_id=self.app_sg.security_group_id,
            description="From app tier",
        )
        ec2.CfnSecurityGroupIngress(
            self, "CacheFromApp",
            group_id=data_tier.cache_sg.security_group_id,
            ip_protocol="tcp",
            from_port=6379,
            to_port=6379,
            source_security_group_id=self.app_sg.security_group_id,
            description="From app tier",
        )

        # --- user data ---
        _app_b64 = base64.b64encode(_APP_CODE.encode()).decode()
        _unit_b64 = base64.b64encode(_SYSTEMD_UNIT.encode()).decode()

        user_data = ec2.UserData.for_linux()
        user_data.add_commands(
            "set -euo pipefail",
            "dnf install -y amazon-cloudwatch-agent python3-pip",
            'pip3 install fastapi uvicorn "psycopg[binary]" redis boto3',
            # IMDSv2 token then region — two clean steps
            'TOKEN=$(curl -sf -X PUT "http://169.254.169.254/latest/api/token"'
            ' -H "X-aws-ec2-metadata-token-ttl-seconds: 21600")',
            'REGION=$(curl -sf -H "X-aws-ec2-metadata-token: $TOKEN"'
            ' http://169.254.169.254/latest/meta-data/placement/region)',
            # CloudWatch agent: fetch config path, fetch config, substitute log group, start
            'CW_CONFIG_PATH=$(aws ssm get-parameter --region "$REGION"'
            ' --name /ops-lab/shared/cw-agent-config-ssm-path --query Parameter.Value --output text)',
            'aws ssm get-parameter --region "$REGION" --name "$CW_CONFIG_PATH"'
            ' --query Parameter.Value --output text'
            " | sed 's|__LOG_GROUP__|/ops-lab/3tier/app|g'"
            ' > /opt/aws/amazon-cloudwatch-agent/etc/amazon-cloudwatch-agent.json',
            '/opt/aws/amazon-cloudwatch-agent/bin/amazon-cloudwatch-agent-ctl'
            ' -a start -m ec2 -s'
            ' -c file:/opt/aws/amazon-cloudwatch-agent/etc/amazon-cloudwatch-agent.json',
            # App connection details — fetched at runtime so new instances always get current values
            'DB_SECRET_ARN=$(aws ssm get-parameter --region "$REGION"'
            ' --name /ops-lab/3tier/rds-secret-arn --query Parameter.Value --output text)',
            'RDS_HOST=$(aws ssm get-parameter --region "$REGION"'
            ' --name /ops-lab/3tier/rds-endpoint --query Parameter.Value --output text)',
            'CACHE_HOST=$(aws ssm get-parameter --region "$REGION"'
            ' --name /ops-lab/3tier/elasticache-endpoint --query Parameter.Value --output text)',
            # Decode app and service unit — no heredocs
            "mkdir -p /opt/app",
            f'echo "{_app_b64}" | base64 -d > /opt/app/main.py',
            f'echo "{_unit_b64}" | base64 -d > /etc/systemd/system/app.service',
            # Stamp runtime values into the service unit
            'sed -i "s|__REGION__|$REGION|" /etc/systemd/system/app.service',
            'sed -i "s|__DB_SECRET_ARN__|$DB_SECRET_ARN|" /etc/systemd/system/app.service',
            'sed -i "s|__RDS_HOST__|$RDS_HOST|" /etc/systemd/system/app.service',
            'sed -i "s|__CACHE_HOST__|$CACHE_HOST|" /etc/systemd/system/app.service',
            "systemctl daemon-reload",
            "systemctl enable --now app",
        )

        # --- launch template (required — AWS no longer allows LaunchConfigurations) ---
        lt = ec2.LaunchTemplate(
            self, "LaunchTemplate",
            launch_template_name="ops-lab-3tier-lt",
            instance_type=ec2.InstanceType.of(
                ec2.InstanceClass.T3, ec2.InstanceSize.SMALL
            ),
            machine_image=ec2.MachineImage.latest_amazon_linux2023(),
            role=role,
            security_group=self.app_sg,
            user_data=user_data,
            associate_public_ip_address=True,
        )
        lt.add_security_group(ssm_sg)

        # --- ASG ---
        asg = autoscaling.AutoScalingGroup(
            self, "Asg",
            auto_scaling_group_name="ops-lab-3tier-asg",
            vpc=vpc,
            vpc_subnets=public_subnet_selection,
            launch_template=lt,
            min_capacity=1,
            max_capacity=3,
            desired_capacity=1,
        )

        # Target tracking — CDK manages both scale-out and scale-in rules
        asg.scale_on_cpu_utilization(
            "CpuScaling",
            target_utilization_percent=70,
        )

        # --- target group ---
        self.target_group = elbv2.ApplicationTargetGroup(
            self, "TargetGroup",
            target_group_name="ops-lab-3tier-tg",
            vpc=vpc,
            port=8080,
            protocol=elbv2.ApplicationProtocol.HTTP,
            target_type=elbv2.TargetType.INSTANCE,
            health_check=elbv2.HealthCheck(
                path="/health",
                healthy_http_codes="200",
                interval=cdk.Duration.seconds(30),
                timeout=cdk.Duration.seconds(5),
                healthy_threshold_count=2,
                unhealthy_threshold_count=3,
            ),
        )
        # L1 override — avoids CDK calling loadBalancerAttachedToTargetGroup, which would
        # create a SecurityGroupIngress on app_sg referencing the ALB SG (PresentationStack),
        # forming a cross-stack cycle.
        cfn_asg = typing.cast(autoscaling.CfnAutoScalingGroup, asg.node.default_child)
        cfn_asg.target_group_arns = [self.target_group.target_group_arn]

        # --- SSM outputs ---
        ssm.StringParameter(
            self, "AsgNameParam",
            parameter_name="/ops-lab/3tier/asg-name",
            string_value=asg.auto_scaling_group_name,
        )
        ssm.StringParameter(
            self, "TargetGroupArnParam",
            parameter_name="/ops-lab/3tier/target-group-arn",
            string_value=self.target_group.target_group_arn,
        )
        # Full name used as CloudWatch TargetGroup dimension (e.g. targetgroup/ops-lab-3tier-tg/abc123)
        ssm.StringParameter(
            self, "TargetGroupFullNameParam",
            parameter_name="/ops-lab/3tier/target-group-full-name",
            string_value=self.target_group.target_group_full_name,
        )
