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

        # value_from_lookup resolves to the actual path: /ops-lab/shared/cw-agent-config
        cw_config_path = ssm.StringParameter.value_from_lookup(
            self, "/ops-lab/shared/cw-agent-config-ssm-path"
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
        user_data = ec2.UserData.for_linux()
        user_data.add_commands(
            "set -e",
            # Fetch CW config from SSM, substitute log group placeholder, start agent
            f"aws ssm get-parameter --region {self.region} --name {cw_config_path}"
            " --query Parameter.Value --output text"
            " | sed 's|__LOG_GROUP__|/ops-lab/3tier/app|g'"
            " > /opt/aws/amazon-cloudwatch-agent/etc/amazon-cloudwatch-agent.json",
            "/opt/aws/amazon-cloudwatch-agent/bin/amazon-cloudwatch-agent-ctl"
            " -a start -m ec2 -s"
            " -c file:/opt/aws/amazon-cloudwatch-agent/etc/amazon-cloudwatch-agent.json",
            # Minimal FastAPI app with /health endpoint
            "dnf install -y python3-pip",
            "pip3 install fastapi uvicorn",
            "mkdir -p /opt/app",
            "cat > /opt/app/main.py << 'PYEOF'",
            "from fastapi import FastAPI",
            "app = FastAPI()",
            "",
            "@app.get('/health')",
            "def health():",
            "    return {'status': 'ok'}",
            "PYEOF",
            "cd /opt/app && nohup uvicorn main:app --host 0.0.0.0 --port 8080"
            " >> /var/log/app.log 2>&1 &",
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
