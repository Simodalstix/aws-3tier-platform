#!/usr/bin/env python3
"""
Drain an ASG before maintenance: suspend Launch, set desired=0,
wait for all instances to deregister from the target group, then report.
"""

import sys
import time

import boto3

REGION        = "ap-southeast-2"
POLL_INTERVAL = 15   # seconds between target-health polls
TIMEOUT       = 600  # 10 minutes

GREEN  = "\033[32m"
RED    = "\033[31m"
YELLOW = "\033[33m"
BOLD   = "\033[1m"
RESET  = "\033[0m"


def main():
    ssm        = boto3.client("ssm",         region_name=REGION)
    asg_client = boto3.client("autoscaling", region_name=REGION)
    elbv2      = boto3.client("elbv2",       region_name=REGION)

    asg_name = ssm.get_parameter(Name="/ops-lab/3tier/asg-name")["Parameter"]["Value"]
    tg_arn   = ssm.get_parameter(Name="/ops-lab/3tier/target-group-arn")["Parameter"]["Value"]

    print(f"{BOLD}Draining ASG: {asg_name}{RESET}\n")

    # Suspend Launch so no replacement instances start during drain
    asg_client.suspend_processes(
        AutoScalingGroupName=asg_name,
        ScalingProcesses=["Launch"],
    )
    print(f"  {GREEN}✓{RESET}  Suspended Launch process")

    asg_client.set_desired_capacity(
        AutoScalingGroupName=asg_name,
        DesiredCapacity=0,
        HonorCooldown=False,
    )
    print(f"  {GREEN}✓{RESET}  Set desired capacity → 0")
    print(f"  {YELLOW}…{RESET}  Waiting for targets to deregister (timeout {TIMEOUT}s)\n")

    deadline = time.monotonic() + TIMEOUT
    while time.monotonic() < deadline:
        resp    = elbv2.describe_target_health(TargetGroupArn=tg_arn)
        targets = resp["TargetHealthDescriptions"]

        active = [t for t in targets if t["TargetHealth"]["State"] not in ("unused",)]
        if not active:
            break

        summary = "  ".join(
            f"{t['Target']['Id']}={t['TargetHealth']['State']}" for t in active
        )
        print(f"  {YELLOW}…{RESET}  {len(active)} target(s) still draining:  {summary}")
        time.sleep(POLL_INTERVAL)
    else:
        print(f"\n{RED}TIMEOUT — targets still registered after {TIMEOUT}s{RESET}")
        sys.exit(1)

    resp = asg_client.describe_auto_scaling_groups(AutoScalingGroupNames=[asg_name])
    remaining_instances = len(resp["AutoScalingGroups"][0]["Instances"])

    print(f"\n  {GREEN}✓{RESET}  All targets deregistered")
    print(f"  {GREEN}✓{RESET}  ASG instances remaining: {remaining_instances}")
    print(f"\n{GREEN}{BOLD}ASG drained — safe to proceed with maintenance.{RESET}")

    print(f"\n{YELLOW}To restore when done:{RESET}")
    print(f"  aws autoscaling resume-processes"
          f" --auto-scaling-group-name {asg_name}"
          f" --scaling-processes Launch --region {REGION}")
    print(f"  aws autoscaling set-desired-capacity"
          f" --auto-scaling-group-name {asg_name}"
          f" --desired-capacity 1 --region {REGION}")


if __name__ == "__main__":
    main()
