#!/usr/bin/env bash
# review-loop.sh — Helper functions for the copilot-review-loop skill.
# Source this script or call individual functions.
#
# Usage:
#   source review-loop.sh
#   check_pr_status  RosenblattAI hermes-agent 42
#   request_review   RosenblattAI hermes-agent 42
#   poll_review      RosenblattAI hermes-agent 42 12345 600
#   get_comments     RosenblattAI hermes-agent 42 67890

set -euo pipefail

###############################################################################
# Preflight — verify required tools are available
###############################################################################
for cmd in gh jq md5sum; do
  if ! command -v "$cmd" &>/dev/null; then
    echo "ERROR: required command '$cmd' is not installed" >&2
    exit 1
  fi
done

###############################################################################
# check_pr_status — Verify PR is open and get branch name
# Args: owner repo pr_number
# Stdout: JSON {state, draft, branch, mergeable_state}
# Exit 1 if PR is not open, is a draft, or has merge conflicts
###############################################################################
check_pr_status() {
  local owner="$1" repo="$2" pr="$3"

  local pr_data
  pr_data=$(gh api "/repos/${owner}/${repo}/pulls/${pr}" \
    --jq '{state: .state, draft: .draft, branch: .head.ref, mergeable_state: .mergeable_state}')

  local state draft
  state=$(echo "$pr_data" | jq -r '.state')
  draft=$(echo "$pr_data" | jq -r '.draft')

  if [[ "$state" != "open" ]]; then
    echo "ERROR: PR #${pr} is not open (state: ${state})" >&2
    return 1
  fi

  if [[ "$draft" == "true" ]]; then
    echo "ERROR: PR #${pr} is a draft — mark as ready for review first" >&2
    return 1
  fi

  local mergeable_state
  mergeable_state=$(echo "$pr_data" | jq -r '.mergeable_state')
  if [[ "$mergeable_state" == "dirty" ]]; then
    echo "ERROR: PR #${pr} has merge conflicts. Resolve conflicts before continuing." >&2
    return 1
  fi

  echo "$pr_data"
}

###############################################################################
# get_baseline_review_id — Get the latest Copilot review ID
# Args: owner repo pr_number
# Stdout: review ID (integer), or 0 if no prior Copilot reviews
###############################################################################
get_baseline_review_id() {
  local owner="$1" repo="$2" pr="$3"

  gh api "/repos/${owner}/${repo}/pulls/${pr}/reviews" \
    --jq '[.[] | select(.user.login == "Copilot")] | sort_by(.submitted_at) | last | .id // 0'
}

###############################################################################
# request_review — Request Copilot as a reviewer
# Args: owner repo pr_number
# Stdout: API response JSON
# Exit 1 on failure
###############################################################################
request_review() {
  local owner="$1" repo="$2" pr="$3"

  local response
  response=$(gh api "/repos/${owner}/${repo}/pulls/${pr}/requested_reviewers" \
    -X POST -f 'reviewers[]=Copilot' 2>&1) || {
    # Sanitize: collapse to single line, strip control chars, truncate
    local sanitized
    sanitized=$(printf '%s' "$response" | tr '\n\r' '  ' | LC_ALL=C tr -d '[:cntrl:]' | cut -c1-200)
    echo "ERROR: Failed to request Copilot review: $sanitized" >&2
    return 1
  }

  echo "$response"
}

###############################################################################
# poll_review — Wait for a new Copilot review to appear
# Args: owner repo pr_number baseline_review_id [timeout_seconds]
# Stdout: JSON of the new review object
# Exit 1 on timeout
###############################################################################
poll_review() {
  local owner="$1" repo="$2" pr="$3" baseline_id="$4"
  local timeout="${5:-600}"  # default 10 minutes
  local interval=30
  local elapsed=0

  # Initial wait — give Copilot time to start
  echo "Waiting ${interval}s before first poll..." >&2
  sleep "$interval"
  elapsed=$((elapsed + interval))

  while [[ $elapsed -lt $timeout ]]; do
    local new_review
    new_review=$(gh api "/repos/${owner}/${repo}/pulls/${pr}/reviews" \
      --jq "[.[] | select(.user.login == \"Copilot\" and (.id > ${baseline_id}))] | sort_by(.submitted_at) | last // empty")

    if [[ -n "$new_review" && "$new_review" != "null" ]]; then
      echo "$new_review"
      return 0
    fi

    echo "Poll ${elapsed}s/${timeout}s — no new review yet..." >&2
    sleep "$interval"
    elapsed=$((elapsed + interval))
  done

  echo "ERROR: Copilot review timed out after ${timeout}s" >&2
  return 1
}

###############################################################################
# get_comments — Fetch and format review comments
# Args: owner repo pr_number review_id
# Stdout: formatted comment blocks for Copilot remote prompt
# Also prints COMMENT_COUNT to stderr
###############################################################################
get_comments() {
  local owner="$1" repo="$2" pr="$3" review_id="$4"

  local raw_comments
  raw_comments=$(gh api "/repos/${owner}/${repo}/pulls/${pr}/reviews/${review_id}/comments")

  local count
  count=$(echo "$raw_comments" | jq 'length')
  echo "COMMENT_COUNT=${count}" >&2

  if [[ "$count" -eq 0 ]]; then
    echo ""
    return 0
  fi

  # Format each comment as a structured block.
  # - Use @json on body/diff_hunk to safely escape all special chars
  #   including triple backticks and newlines.
  # - Properly escape the "N/A" fallback for original_line.
  echo "$raw_comments" | jq -r --argjson total "$count" '
    to_entries[] |
    "## Review Comment \(.key + 1)/\($total)\n" +
    "**File:** `\(.value.path)`\n" +
    "**Line:** \(.value.original_line // "N\/A")\n" +
    "**Copilot says:** \(.value.body | gsub("```"; "` ` `"))\n" +
    "**Diff context:**\n````diff\n\(.value.diff_hunk | gsub("```"; "` ` `"))\n````\n"
  '
}

###############################################################################
# check_duplicate_comments — Compare current round comments to previous round
# Args: previous_hashes_file current_comments_json
# Stdout: "DUPLICATE" if >50% overlap, "OK" otherwise
###############################################################################
check_duplicate_comments() {
  local prev_file="$1" current_json="$2"

  # Use @base64 to encode each comment into a single line for hashing,
  # avoiding newlines in body text splitting one comment into multiple lines.
  if [[ ! -f "$prev_file" ]]; then
    echo "$current_json" | jq -r '.[] | "\(.path):\(.original_line):\(.body)" | @base64' | \
      while IFS= read -r line; do printf '%s' "$line" | md5sum | cut -d' ' -f1; done > "$prev_file"
    echo "OK"
    return 0
  fi

  local total current_hashes_file
  total=$(echo "$current_json" | jq 'length')
  current_hashes_file=$(mktemp)
  trap "rm -f '$current_hashes_file'" EXIT

  echo "$current_json" | jq -r '.[] | "\(.path):\(.original_line):\(.body)" | @base64' | \
    while IFS= read -r line; do printf '%s' "$line" | md5sum | cut -d' ' -f1; done > "$current_hashes_file"

  local duplicates=0
  while IFS= read -r hash; do
    if grep -Fxq "$hash" "$prev_file" 2>/dev/null; then
      duplicates=$((duplicates + 1))
    fi
  done < "$current_hashes_file"

  # Update previous hashes for next round
  cp "$current_hashes_file" "$prev_file"
  rm -f "$current_hashes_file"

  if [[ $total -gt 0 ]] && [[ $((duplicates * 100 / total)) -gt 50 ]]; then
    echo "DUPLICATE"
  else
    echo "OK"
  fi
}

# If sourced, functions are available. If run directly, execute the given function.
if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then
  "$@"
fi
