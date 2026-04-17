#!/bin/bash
# Create the SNS topic and email subscription for Lambda error alarms.
# Run once, then confirm the subscription email before creating alarms.

set -euo pipefail

REGION="us-east-1"
PROFILE="admin"
TOPIC_NAME="LambdaErrorAlarms"
EMAIL="owen.white@rosenblatt.ai"

echo "Creating SNS topic: ${TOPIC_NAME}"
TOPIC_ARN=$(aws sns create-topic \
  --name "${TOPIC_NAME}" \
  --region "${REGION}" \
  --profile "${PROFILE}" \
  --query "TopicArn" \
  --output text)
echo "✓ Topic ARN: ${TOPIC_ARN}"

echo ""
echo "Subscribing ${EMAIL}..."
aws sns subscribe \
  --topic-arn "${TOPIC_ARN}" \
  --protocol email \
  --notification-endpoint "${EMAIL}" \
  --region "${REGION}" \
  --profile "${PROFILE}"
echo "✓ Subscription requested"

echo ""
echo "⚠  Check ${EMAIL} for a confirmation email and click the link before running upsert_error_alarms.sh"
