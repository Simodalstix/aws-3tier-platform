import typing

import aws_cdk as cdk
from aws_cdk import (
    Stack,
    aws_cloudwatch as cw,
    aws_ssm as ssm,
)
from constructs import Construct


class AlarmsStack(Stack):
    """CloudWatch alarms for ALB, RDS, and ElastiCache — all publish to shared SNS topic."""

    def __init__(self, scope: Construct, construct_id: str, **kwargs) -> None:
        super().__init__(scope, construct_id, **kwargs)

        # value_for_string_parameter returns a CloudFormation SSM dynamic reference
        # ({{resolve:ssm:...}}) — avoids the ARN format validation that from_topic_arn
        # applies at synth time, which breaks on CDK's dummy lookup values.
        sns_arn = ssm.StringParameter.value_for_string_parameter(
            self, "/ops-lab/shared/sns-topic-arn"
        )

        # Deploy-time resolution — these parameters don't exist until PresentationStack
        # and AppTierStack are deployed, so value_from_lookup (synth-time) would fail.
        alb_full_name = ssm.StringParameter.value_for_string_parameter(
            self, "/ops-lab/3tier/alb-full-name"
        )
        tg_full_name = ssm.StringParameter.value_for_string_parameter(
            self, "/ops-lab/3tier/target-group-full-name"
        )

        def alert(alarm: cw.Alarm) -> cw.Alarm:
            """Attach SNS action via L1 to avoid IAlarmAction ARN validation."""
            typing.cast(cw.CfnAlarm, alarm.node.default_child).alarm_actions = [sns_arn]
            return alarm

        # --- ALB alarms ---
        alert(cw.Alarm(
            self, "Alb5xxAlarm",
            alarm_name="ops-lab-3tier-alb-5xx",
            alarm_description="ALB 5xx error rate elevated",
            metric=cw.Metric(
                namespace="AWS/ApplicationELB",
                metric_name="HTTPCode_ELB_5XX_Count",
                dimensions_map={"LoadBalancer": alb_full_name},
                statistic="Sum",
                period=cdk.Duration.minutes(5),
            ),
            threshold=10,
            evaluation_periods=2,
            comparison_operator=cw.ComparisonOperator.GREATER_THAN_THRESHOLD,
            treat_missing_data=cw.TreatMissingData.NOT_BREACHING,
        ))

        alert(cw.Alarm(
            self, "AlbResponseTimeAlarm",
            alarm_name="ops-lab-3tier-alb-response-time",
            alarm_description="ALB target response time p99 > 1s",
            metric=cw.Metric(
                namespace="AWS/ApplicationELB",
                metric_name="TargetResponseTime",
                dimensions_map={
                    "LoadBalancer": alb_full_name,
                    "TargetGroup": tg_full_name,
                },
                statistic="p99",
                period=cdk.Duration.minutes(5),
            ),
            threshold=1,
            evaluation_periods=3,
            comparison_operator=cw.ComparisonOperator.GREATER_THAN_THRESHOLD,
            treat_missing_data=cw.TreatMissingData.NOT_BREACHING,
        ))

        alert(cw.Alarm(
            self, "AlbUnhealthyHostAlarm",
            alarm_name="ops-lab-3tier-alb-unhealthy-hosts",
            alarm_description="ALB has unhealthy targets",
            metric=cw.Metric(
                namespace="AWS/ApplicationELB",
                metric_name="UnHealthyHostCount",
                dimensions_map={
                    "LoadBalancer": alb_full_name,
                    "TargetGroup": tg_full_name,
                },
                statistic="Maximum",
                period=cdk.Duration.minutes(1),
            ),
            threshold=0,
            evaluation_periods=2,
            comparison_operator=cw.ComparisonOperator.GREATER_THAN_THRESHOLD,
            treat_missing_data=cw.TreatMissingData.NOT_BREACHING,
        ))

        # --- RDS alarms ---
        alert(cw.Alarm(
            self, "RdsCpuAlarm",
            alarm_name="ops-lab-3tier-rds-cpu",
            alarm_description="RDS CPU > 80%",
            metric=cw.Metric(
                namespace="AWS/RDS",
                metric_name="CPUUtilization",
                dimensions_map={"DBInstanceIdentifier": "ops-lab-3tier-rds"},
                statistic="Average",
                period=cdk.Duration.minutes(5),
            ),
            threshold=80,
            evaluation_periods=3,
            comparison_operator=cw.ComparisonOperator.GREATER_THAN_THRESHOLD,
            treat_missing_data=cw.TreatMissingData.NOT_BREACHING,
        ))

        alert(cw.Alarm(
            self, "RdsConnectionsAlarm",
            alarm_name="ops-lab-3tier-rds-connections",
            alarm_description="RDS connection count > 80 - approaching db.t3.micro limit",
            metric=cw.Metric(
                namespace="AWS/RDS",
                metric_name="DatabaseConnections",
                dimensions_map={"DBInstanceIdentifier": "ops-lab-3tier-rds"},
                statistic="Maximum",
                period=cdk.Duration.minutes(5),
            ),
            threshold=80,
            evaluation_periods=2,
            comparison_operator=cw.ComparisonOperator.GREATER_THAN_THRESHOLD,
            treat_missing_data=cw.TreatMissingData.NOT_BREACHING,
        ))

        alert(cw.Alarm(
            self, "RdsFreeStorageAlarm",
            alarm_name="ops-lab-3tier-rds-free-storage",
            alarm_description="RDS free storage < 5 GB",
            metric=cw.Metric(
                namespace="AWS/RDS",
                metric_name="FreeStorageSpace",
                dimensions_map={"DBInstanceIdentifier": "ops-lab-3tier-rds"},
                statistic="Minimum",
                period=cdk.Duration.minutes(5),
            ),
            threshold=5 * 1024 ** 3,  # 5 GB in bytes
            evaluation_periods=2,
            comparison_operator=cw.ComparisonOperator.LESS_THAN_THRESHOLD,
            treat_missing_data=cw.TreatMissingData.NOT_BREACHING,
        ))

        # --- ElastiCache alarms ---
        # Redis is single-threaded — 50% EngineCPU is the practical ceiling before latency degrades
        alert(cw.Alarm(
            self, "RedisCpuAlarm",
            alarm_name="ops-lab-3tier-redis-cpu",
            alarm_description="Redis engine CPU > 50%",
            metric=cw.Metric(
                namespace="AWS/ElastiCache",
                metric_name="EngineCPUUtilization",
                dimensions_map={"CacheClusterId": "ops-lab-3tier-redis"},
                statistic="Average",
                period=cdk.Duration.minutes(5),
            ),
            threshold=50,
            evaluation_periods=3,
            comparison_operator=cw.ComparisonOperator.GREATER_THAN_THRESHOLD,
            treat_missing_data=cw.TreatMissingData.NOT_BREACHING,
        ))

        alert(cw.Alarm(
            self, "RedisEvictionsAlarm",
            alarm_name="ops-lab-3tier-redis-evictions",
            alarm_description="Redis evictions > 0 - memory pressure, cache undersized or TTLs needed",
            metric=cw.Metric(
                namespace="AWS/ElastiCache",
                metric_name="Evictions",
                dimensions_map={"CacheClusterId": "ops-lab-3tier-redis"},
                statistic="Sum",
                period=cdk.Duration.minutes(5),
            ),
            threshold=0,
            evaluation_periods=2,
            comparison_operator=cw.ComparisonOperator.GREATER_THAN_THRESHOLD,
            treat_missing_data=cw.TreatMissingData.NOT_BREACHING,
        ))

        alert(cw.Alarm(
            self, "RedisMemoryAlarm",
            alarm_name="ops-lab-3tier-redis-memory",
            alarm_description="Redis memory usage > 75%",
            metric=cw.Metric(
                namespace="AWS/ElastiCache",
                metric_name="DatabaseMemoryUsagePercentage",
                dimensions_map={"CacheClusterId": "ops-lab-3tier-redis"},
                statistic="Maximum",
                period=cdk.Duration.minutes(5),
            ),
            threshold=75,
            evaluation_periods=2,
            comparison_operator=cw.ComparisonOperator.GREATER_THAN_THRESHOLD,
            treat_missing_data=cw.TreatMissingData.NOT_BREACHING,
        ))
