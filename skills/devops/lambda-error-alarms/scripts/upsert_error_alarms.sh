#!/bin/bash
# SD-16: CloudWatch error alarms for Lambda functions (Vanta remediation)
#
# Threshold: ≥3 errors in two consecutive 5-min windows.
# Rationale: CDK/Amplify infrastructure functions (TableManager, S3AutoDelete,
# BranchLinker, BucketDeployment) have transient errors during deploys that
# would cause false positives at threshold 1.
#
# Prerequisites:
#   1. AWS CLI configured with "admin" profile
#   2. SNS topic + subscription created (see setup_sns_topic.sh)

set -euo pipefail

SNS_TOPIC_ARN="arn:aws:sns:us-east-1:687613142139:LambdaErrorAlarms"
REGION="us-east-1"
PROFILE="admin"
THRESHOLD=3
EVAL_PERIODS=2
PERIOD=300

for function_name in $(aws lambda list-functions \
  --query "Functions[].FunctionName" \
  --output text \
  --profile "${PROFILE}" \
  --region "${REGION}"); do

  aws cloudwatch put-metric-alarm \
    --alarm-name "${function_name}-errors" \
    --alarm-description "Alarm for errors in ${function_name}" \
    --metric-name Errors \
    --namespace AWS/Lambda \
    --statistic Sum \
    --dimensions Name=FunctionName,Value="${function_name}" \
    --period "${PERIOD}" \
    --evaluation-periods "${EVAL_PERIODS}" \
    --threshold "${THRESHOLD}" \
    --comparison-operator GreaterThanOrEqualToThreshold \
    --alarm-actions "${SNS_TOPIC_ARN}" \
    --profile "${PROFILE}" \
    --region "${REGION}"

  echo "✓ ${function_name}"
done

echo ""
echo "Verify with:"
echo "  aws cloudwatch describe-alarms --alarm-name-prefix 'amplify-' --query 'MetricAlarms[].{Name:AlarmName,State:StateValue}' --output table --profile ${PROFILE} --region ${REGION}"
