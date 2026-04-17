---
name: lambda-error-alarms
description: Create and manage CloudWatch error rate alarms for all Lambda functions in an AWS account. Use when Vanta flags missing Lambda error monitoring, or when new functions need alarms.
version: 1.0.0
metadata:
  hermes:
    tags: [aws, cloudwatch, lambda, vanta, compliance, monitoring]
---

# Lambda Error Alarms

Create CloudWatch error rate alarms for all Lambda functions in a given AWS region. Designed to satisfy Vanta's Lambda error monitoring control while avoiding false positives from CDK/Amplify infrastructure functions.

## When to Use

- Vanta flags Lambda functions without error alarms
- New Lambda functions have been deployed and need monitoring
- Alarm thresholds need adjustment after tuning

## Prerequisites

- AWS CLI installed and configured with a profile that has CloudWatch + Lambda + SNS permissions
- An SNS topic for alarm notifications (the setup script creates one)

## Setup (One-Time)

Run the SNS topic setup script to create the notification target:

```bash
chmod +x skills/devops/lambda-error-alarms/scripts/setup_sns_topic.sh
./skills/devops/lambda-error-alarms/scripts/setup_sns_topic.sh
```

Then **confirm the subscription email** before proceeding — check the inbox for the address configured in the script.

## Create/Update Alarms

```bash
chmod +x skills/devops/lambda-error-alarms/scripts/upsert_error_alarms.sh
./skills/devops/lambda-error-alarms/scripts/upsert_error_alarms.sh
```

This enumerates **all** Lambda functions in the configured region and creates/updates an error alarm for each. It is idempotent — safe to re-run at any time (e.g., after deploying new functions).

## Configuration

Edit the variables at the top of `upsert_error_alarms.sh`:

| Variable | Default | Description |
|----------|---------|-------------|
| `REGION` | `us-east-1` | AWS region to enumerate functions and create alarms |
| `PROFILE` | `admin` | AWS CLI profile |
| `THRESHOLD` | `3` | Error count that triggers the alarm |
| `EVAL_PERIODS` | `2` | Consecutive periods that must breach threshold |
| `PERIOD` | `300` | Evaluation window in seconds (5 min) |

### Why threshold 3 / eval-periods 2?

CDK/Amplify infrastructure functions (TableManager, S3AutoDelete, BranchLinker, BucketDeployment) produce transient errors during deploys. Threshold 1 per 5 min generates false positives. `≥3 errors in two consecutive 5-min windows` filters deploy noise while catching real issues.

## Verification

After running the script, verify alarms were created:

```bash
aws cloudwatch describe-alarms \
  --alarm-name-prefix "amplify-" \
  --query "MetricAlarms[].{Name:AlarmName,State:StateValue}" \
  --output table \
  --profile admin \
  --region us-east-1
```

## References

- [SD-16](https://rosenblatt-ai.atlassian.net/browse/SD-16) — Vanta remediation ticket
- SNS topic: `arn:aws:sns:us-east-1:687613142139:LambdaErrorAlarms`
