"""
Trigger a manual RDS snapshot and wait for it to complete.
Reads the RDS identifier from SSM /ops-lab/3tier/rds-endpoint.
"""

import sys
import time
import datetime

import boto3

REGION        = "ap-southeast-2"
POLL_INTERVAL = 20    # seconds between status polls
TIMEOUT       = 1800  # 30 minutes

GREEN  = "\033[32m"
RED    = "\033[31m"
YELLOW = "\033[33m"
BOLD   = "\033[1m"
RESET  = "\033[0m"


def main():
    ssm = boto3.client("ssm", region_name=REGION)
    rds = boto3.client("rds", region_name=REGION)

    rds_endpoint = ssm.get_parameter(Name="/ops-lab/3tier/rds-endpoint")["Parameter"]["Value"]
    # RDS endpoint format: {identifier}.{hash}.{region}.rds.amazonaws.com
    db_id = rds_endpoint.split(".")[0]

    timestamp   = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%d-%H%M%S")
    snapshot_id = f"{db_id}-manual-{timestamp}"

    print(f"{BOLD}Creating RDS snapshot{RESET}")
    print(f"  DB instance:  {db_id}")
    print(f"  Snapshot ID:  {snapshot_id}\n")

    rds.create_db_snapshot(
        DBInstanceIdentifier=db_id,
        DBSnapshotIdentifier=snapshot_id,
    )
    print(f"  {GREEN}✓{RESET}  Snapshot initiated — polling for completion...\n")

    deadline = time.monotonic() + TIMEOUT
    while time.monotonic() < deadline:
        resp     = rds.describe_db_snapshots(DBSnapshotIdentifier=snapshot_id)
        snapshot = resp["DBSnapshots"][0]
        status   = snapshot["Status"]
        progress = snapshot.get("PercentProgress", 0)

        if status == "available":
            size_gb = snapshot.get("AllocatedStorage", "?")
            print(f"  {GREEN}✓{RESET}  {snapshot_id}  —  {size_gb} GB")
            print(f"\n{GREEN}{BOLD}Snapshot complete.{RESET}")
            return

        if status in ("failed", "deleted"):
            print(f"\n{RED}Snapshot {status}: {snapshot_id}{RESET}")
            sys.exit(1)

        print(f"  {YELLOW}…{RESET}  {status}  —  {progress}% complete")
        time.sleep(POLL_INTERVAL)

    print(f"\n{RED}TIMEOUT — snapshot did not complete within {TIMEOUT // 60} minutes{RESET}")
    sys.exit(1)


if __name__ == "__main__":
    main()
