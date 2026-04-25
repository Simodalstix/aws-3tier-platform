# aws-3tier-platform

Production-style 3-tier application platform on AWS. Demonstrates the architecture a systems engineer owns and troubleshoots day-to-day: ALB, Auto Scaling Group, RDS PostgreSQL, and ElastiCache Redis. Extended with FIS fault injection and DR runbooks.

Built with CDK Python + Poetry. Part of a broader [ops lab](https://github.com/simoda) that includes shared networking and observability stacks.

---

## Architecture

```
Internet
   │
   ▼
[ALB]  ──  public subnets (3 AZs)
   │
   ▼
[ASG / EC2]  ──  public subnets, SSM access only, no key pairs
   │         └─  CloudWatch agent, FastAPI app on :8080
   ├──────────────────────────┐
   ▼                          ▼
[RDS PostgreSQL]        [ElastiCache Redis]
isolated subnets        isolated subnets
```

**Cross-project config bus:** SSM Parameter Store. No hardcoded ARNs anywhere.

---

## Stacks

| Stack | Resources |
|---|---|
| `DataTierStack` | RDS PostgreSQL 16, ElastiCache Redis, Secrets Manager secret, security groups |
| `AppTierStack` | EC2 ASG (launch template), instance role, CloudWatch log group, target group, CPU scaling |
| `PresentationStack` | ALB, HTTP listener, ALB security group |

Deploy order: `DataTierStack` → `AppTierStack` → `PresentationStack`

---

## Prerequisites

- AWS CLI configured for account `820242933814`, region `ap-southeast-2`
- [`aws-ops-networking`](https://github.com/simoda/aws-ops-networking) deployed — provides VPC, subnets, SSM SG
- [`aws-ops-observability`](https://github.com/simoda/aws-ops-observability) deployed — provides SNS topic, CloudWatch IAM policy
- Poetry, Node.js (for CDK CLI)

---

## Deploy

```bash
# One-time bootstrap (first deploy only)
poetry run cdk bootstrap aws://820242933814/ap-southeast-2

# Install dependencies
poetry install

# Deploy all stacks in order
poetry run cdk deploy --all

# Tear down (reverse order, no orphans — DESTROY removal policy set)
poetry run cdk destroy --all
```

---

## SSM Parameters

**Reads** (from prerequisite stacks):

```
/ops-lab/networking/vpc-id
/ops-lab/networking/subnet/public-{0,1,2}
/ops-lab/networking/subnet/isolated-{0,1,2}
/ops-lab/networking/ssm-sg-id
/ops-lab/shared/sns-topic-arn
/ops-lab/shared/cloudwatch-write-policy-arn
/ops-lab/shared/cw-agent-config-ssm-path
```

**Writes** (for downstream use):

```
/ops-lab/3tier/alb-dns-name
/ops-lab/3tier/rds-endpoint
/ops-lab/3tier/rds-secret-arn
/ops-lab/3tier/elasticache-endpoint
/ops-lab/3tier/asg-name
/ops-lab/3tier/target-group-arn
```

---

## Verify

```bash
# ALB health check
ALB=$(aws ssm get-parameter --name /ops-lab/3tier/alb-dns-name --query Parameter.Value --output text)
curl http://$ALB/health

# SSM Session Manager access
INSTANCE=$(aws autoscaling describe-auto-scaling-groups \
  --auto-scaling-group-names ops-lab-3tier-asg \
  --query 'AutoScalingGroups[0].Instances[0].InstanceId' --output text)
aws ssm start-session --target $INSTANCE
```

---

## Cost

~$85/month at minimum capacity (1x t3.small EC2, db.t3.micro RDS, cache.t3.micro Redis, ALB). **Tear down between sessions.**
