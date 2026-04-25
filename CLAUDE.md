# CLAUDE.md — aws-3tier-platform

## Behavioral Guidelines

These apply to every task in this repo. They bias toward caution over speed.
For trivial tasks, use judgment.

### 1. Think Before Coding

Don't assume. Don't hide confusion. Surface tradeoffs.

Before implementing:
- State assumptions explicitly. If uncertain, ask.
- If multiple interpretations exist, present them — don't pick silently.
- If a simpler approach exists, say so. Push back when warranted.
- If something is unclear, stop. Name what's confusing. Ask.

For infrastructure decisions specifically:
- Name the tradeoff (cost vs HA, simplicity vs flexibility, coupled vs modular)
- If a CDK construct choice has implications (L1 vs L2 vs L3), surface them
- Don't silently pick an AZ count, instance type, or retention policy — state it

### 2. Simplicity First

Minimum code that solves the problem. Nothing speculative.

- No features beyond what was asked
- No abstractions for single-use constructs
- No configurability that wasn't requested
- If you write 200 lines and it could be 50, rewrite it

Ask: "Would a senior engineer say this is overcomplicated?" If yes, simplify.

### 3. Surgical Changes

Touch only what you must. Clean up only your own mess.

When editing existing code:
- Don't improve adjacent code, comments, or formatting
- Don't refactor things that aren't broken
- Match existing style, even if you'd do it differently
- If you notice unrelated issues, mention them — don't fix them silently

When your changes create orphans:
- Remove imports/variables/constructs that YOUR changes made unused
- Don't remove pre-existing dead code unless asked

Every changed line should trace directly to the request.

### 4. Goal-Driven Execution

Define success criteria. Loop until verified.

For infrastructure tasks, replace "tests pass" with CLI or script verification:
- "Add RDS construct" → verify: endpoint appears in SSM Parameter Store
- "Fix SG rule" → verify: SSM Session Manager connects successfully
- "Add alarm" → verify: alarm visible in CloudWatch, SNS topic receives test

For multi-step tasks, state a brief plan first:
```
1. [Step] → verify: [CLI check]
2. [Step] → verify: [CLI check]
3. [Step] → verify: [CLI check]
```

---

## Platform Context

I am building a modular AWS ops platform as a series of independent but
interconnected GitHub projects. This repo is the primary application platform —
a production-style 3-tier architecture that demonstrates real CloudOps
operational patterns including auto scaling, caching, managed databases,
fault injection, and disaster recovery.

**Developer:** simoda
**Machine:** Beelink (Linux, Ubuntu)
**Region:** ap-southeast-2
**Account:** 820242933814
**Primary tool:** Claude Code (CLI), working directly inside this repo

---

## Existing Projects

- `aws-ops-networking` ✅ — deployed. Foundation VPC stack. Exports to
  `/ops-lab/networking/*` in SSM Parameter Store.
- `aws-ops-observability` ✅ — deployed. Shared SNS topic, CloudWatch IAM
  policy, agent config template. Exports to `/ops-lab/shared/*`.

## Planned Projects (not yet started)

- `aws-config-mgmt-lab` — AWS Config rules, SSM State Manager, Puppet,
  drift detection, auto-remediation.
- `aws-event-driven-pipeline` — SQS/Kinesis, Lambda, S3, Glue, Athena.

---

## Platform Rules (apply to every project)

- **IaC:** CDK Python with Poetry
- **No hardcoded ARNs or IDs anywhere** — all cross-project values go through
  SSM Parameter Store
- **SSM Parameter Store is the config bus** — whoever creates a resource writes
  its ID to Parameter Store; every other project reads from there at deploy time
- **NAT:** `NONE` by default — NAT Gateway only when explicitly demonstrating
  egress flows; never a NAT instance
- **EC2 access:** SSM only — no bastions, no key pairs
- **All projects include:**
  - CLI playbooks under `docs/cli-playbooks/`
  - Boto3 operational scripts under `scripts/`
  - This `CLAUDE.md` at repo root

---

## This Project: aws-3tier-platform

**Purpose:** Deploy a production-style 3-tier application platform — ALB, Auto
Scaling Group, RDS PostgreSQL, and ElastiCache Redis. Demonstrates the
architecture a systems engineer owns and troubleshoots day-to-day. Extended
with FIS fault injection and DR patterns.

### SSM Parameters This Project Reads

```
/ops-lab/networking/vpc-id
/ops-lab/networking/subnet/public-0,1,2      → ALB
/ops-lab/networking/subnet/isolated-0,1,2   → RDS, ElastiCache
/ops-lab/networking/ssm-sg-id               → attach to EC2 instances
/ops-lab/shared/sns-topic-arn               → alarm destination
/ops-lab/shared/cloudwatch-write-policy-arn → attach to instance role
/ops-lab/shared/cw-agent-config-ssm-path    → CloudWatch agent on instances
```

### SSM Parameters This Project Writes

```
/ops-lab/3tier/alb-dns-name
/ops-lab/3tier/rds-endpoint
/ops-lab/3tier/rds-secret-arn
/ops-lab/3tier/elasticache-endpoint
/ops-lab/3tier/asg-name
/ops-lab/3tier/target-group-arn
```

### What This Stack Deploys

**DataTierStack**
- RDS PostgreSQL — in isolated subnets
- ElastiCache Redis — in isolated subnets
- Secrets Manager secret for DB credentials

**AppTierStack**
- EC2 Auto Scaling Group — in public subnets, SSM access only
- Launch template — CloudWatch agent, SSM agent, app bootstrap
- Scaling policies — CPU-based

**PresentationStack**
- Application Load Balancer — public subnets
- Target group with health checks
- HTTP listener

**Operational extensions (added incrementally)**
- FIS experiment templates — AZ failure, instance termination, CPU stress
- SSM Automation runbooks — DR steps, recovery procedures
- CloudWatch alarms — ALB 5xx rate, RDS connections, Redis evictions, ASG activity

### Application

The CrossFit app (FastAPI + PostgreSQL) is the target application. Runs locally
on a Proxmox VM for development, deployed to this stack for AWS. Redis used for
session storage and query caching in front of RDS.

---

## Repo Structure

```
aws-3tier-platform/
├── CLAUDE.md
├── README.md
├── app.py
├── cdk.json
├── pyproject.toml
├── platform_lab/
│   ├── __init__.py
│   ├── data_tier_stack.py
│   ├── app_tier_stack.py
│   └── presentation_stack.py
├── scripts/
│   ├── verify_platform.py
│   ├── drain_asg.py
│   └── rds_snapshot.py
└── docs/
    └── cli-playbooks/
        ├── 01-data-tier.md
        ├── 02-app-tier.md
        ├── 03-presentation.md
        ├── 04-fis-experiments.md
        └── 05-dr-runbooks.md
```

---

## Key Conventions

- Stack names: `DataTierStack`, `AppTierStack`, `PresentationStack`
- Deploy order: `DataTierStack` → `AppTierStack` → `PresentationStack`
- All SSM parameter keys: `/ops-lab/3tier/{resource}`
- All resource names: `ops-lab-3tier-{resource}` e.g. `ops-lab-3tier-alb`
- Tag everything: `Project: ops-lab`, `Stack: 3tier`
- Comments explain *why*, not just *what*

