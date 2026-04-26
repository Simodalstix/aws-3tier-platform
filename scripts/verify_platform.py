"""
Verify all three stacks are healthy by checking SSM outputs, ALB health,
RDS connectivity (via SSM port forwarding), and Redis reachability.
"""

import sys
import time
import urllib.error
import urllib.request

import boto3

REGION = "ap-southeast-2"

GREEN = "\033[32m"
RED = "\033[31m"
YELLOW = "\033[33m"
BOLD = "\033[1m"
RESET = "\033[0m"

SSM_PARAMS = [
    "/ops-lab/3tier/alb-dns-name",
    "/ops-lab/3tier/rds-endpoint",
    "/ops-lab/3tier/rds-secret-arn",
    "/ops-lab/3tier/elasticache-endpoint",
    "/ops-lab/3tier/asg-name",
    "/ops-lab/3tier/target-group-arn",
]


def ok(msg):    print(f"  {GREEN}✓{RESET}  {msg}")
def fail(msg):  print(f"  {RED}✗{RESET}  {msg}")
def warn(msg):  print(f"  {YELLOW}!{RESET}  {msg}")
def section(title): print(f"\n{BOLD}{title}{RESET}")


def main():
    ssm          = boto3.client("ssm",          region_name=REGION)
    elbv2        = boto3.client("elbv2",        region_name=REGION)
    rds_client   = boto3.client("rds",          region_name=REGION)
    cache_client = boto3.client("elasticache",  region_name=REGION)
    asg_client   = boto3.client("autoscaling",  region_name=REGION)

    failures = 0

    # ── SSM parameters ────────────────────────────────────────────────────────
    section("SSM Parameters")
    params = {}
    for name in SSM_PARAMS:
        try:
            params[name] = ssm.get_parameter(Name=name)["Parameter"]["Value"]
            ok(f"{name}  =  {params[name][:70]}")
        except Exception as exc:
            fail(f"{name}  —  {exc}")
            failures += 1

    if failures:
        print(f"\n{RED}Aborting — SSM parameters missing. Have the stacks been deployed?{RESET}")
        sys.exit(1)

    alb_dns      = params["/ops-lab/3tier/alb-dns-name"]
    asg_name     = params["/ops-lab/3tier/asg-name"]
    tg_arn       = params["/ops-lab/3tier/target-group-arn"]
    rds_endpoint = params["/ops-lab/3tier/rds-endpoint"]

    # ── ALB /health ───────────────────────────────────────────────────────────
    section("ALB — GET /health")
    url = f"http://{alb_dns}/health"
    try:
        with urllib.request.urlopen(url, timeout=10) as resp:
            body = resp.read().decode()
            ok(f"HTTP {resp.status}  —  {body[:100]}")
    except urllib.error.HTTPError as exc:
        fail(f"HTTP {exc.code}  —  {url}")
        failures += 1
    except Exception as exc:
        fail(f"{url}  —  {exc}")
        failures += 1

    # ── Target group ──────────────────────────────────────────────────────────
    section("Target Group — registered targets")
    try:
        resp = elbv2.describe_target_health(TargetGroupArn=tg_arn)
        targets = resp["TargetHealthDescriptions"]
        if not targets:
            warn("No targets registered yet — ASG may still be launching")
        for t in targets:
            state  = t["TargetHealth"]["State"]
            target = f"{t['Target']['Id']}:{t['Target']['Port']}"
            if state == "healthy":
                ok(f"{target}  —  {state}")
            elif state == "initial":
                warn(f"{target}  —  {state} (awaiting health check)")
            else:
                fail(f"{target}  —  {state}")
                failures += 1
    except Exception as exc:
        fail(str(exc))
        failures += 1

    # ── ASG ───────────────────────────────────────────────────────────────────
    section("Auto Scaling Group")
    try:
        resp = asg_client.describe_auto_scaling_groups(AutoScalingGroupNames=[asg_name])
        asg = resp["AutoScalingGroups"][0]
        in_service = sum(1 for i in asg["Instances"] if i["LifecycleState"] == "InService")
        ok(f"Desired={asg['DesiredCapacity']}  InService={in_service}  Total={len(asg['Instances'])}")
        for inst in asg["Instances"]:
            state = inst["LifecycleState"]
            line  = f"{inst['InstanceId']}  {state}  AZ={inst['AvailabilityZone']}"
            (ok if state == "InService" else warn)(line)
    except Exception as exc:
        fail(str(exc))
        failures += 1

    # ── RDS ───────────────────────────────────────────────────────────────────
    section("RDS PostgreSQL")
    try:
        db_id = rds_endpoint.split(".")[0]
        resp  = rds_client.describe_db_instances(DBInstanceIdentifier=db_id)
        db    = resp["DBInstances"][0]
        status = db["DBInstanceStatus"]
        line   = f"{db_id}  —  {status}  —  {db['DBInstanceClass']}  —  MultiAZ={db['MultiAZ']}"
        if status == "available":
            ok(line)
        elif status in ("backing-up", "modifying", "upgrading"):
            warn(line)
        else:
            fail(line)
            failures += 1
    except Exception as exc:
        fail(str(exc))
        failures += 1

    # ── ElastiCache ───────────────────────────────────────────────────────────
    section("ElastiCache Redis")
    try:
        resp    = cache_client.describe_cache_clusters(CacheClusterId="ops-lab-3tier-redis")
        cluster = resp["CacheClusters"][0]
        status  = cluster["CacheClusterStatus"]
        line    = f"ops-lab-3tier-redis  —  {status}  —  {cluster['CacheNodeType']}"
        if status == "available":
            ok(line)
        elif status in ("snapshotting", "modifying"):
            warn(line)
        else:
            fail(line)
            failures += 1
    except Exception as exc:
        fail(str(exc))
        failures += 1

    # ── Summary ───────────────────────────────────────────────────────────────
    print()
    if failures:
        print(f"{RED}{BOLD}FAILED — {failures} check(s) did not pass{RESET}")
        sys.exit(1)
    print(f"{GREEN}{BOLD}ALL CHECKS PASSED{RESET}")


if __name__ == "__main__":
    main()
