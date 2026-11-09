#!/usr/bin/env bash
# Deploy one backend image tag on the server and roll back if it does not become healthy.
# Usage: /opt/oryn/deploy.sh <image-tag>
# State lives in /opt/oryn/.env: IMAGE_TAG (written here) and ORYN_DOMAIN (set once by hand).
set -euo pipefail

NEW_TAG="${1:?usage: deploy.sh <image-tag>}"
[[ "$NEW_TAG" =~ ^[A-Za-z0-9_.-]+$ ]] || { echo "invalid image tag: $NEW_TAG" >&2; exit 2; }

APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ENV_FILE="$APP_DIR/.env"
HEALTH_TIMEOUT_S="${HEALTH_TIMEOUT_S:-120}"
cd "$APP_DIR"

compose() { docker compose -f docker-compose.prod.yml "$@"; }

read_env() { grep -E "^$1=" "$ENV_FILE" 2>/dev/null | tail -n1 | cut -d= -f2- || true; }

set_tag() {
  touch "$ENV_FILE"
  { grep -vE '^IMAGE_TAG=' "$ENV_FILE" || true; echo "IMAGE_TAG=$1"; } > "$ENV_FILE.tmp"
  mv "$ENV_FILE.tmp" "$ENV_FILE"
}

wait_healthy() {
  local cid status deadline=$((SECONDS + HEALTH_TIMEOUT_S))
  while (( SECONDS < deadline )); do
    cid="$(compose ps -q backend)"
    status="$([[ -n "$cid" ]] && docker inspect -f '{{.State.Health.Status}}' "$cid" 2>/dev/null || echo none)"
    case "$status" in
      healthy) return 0 ;;
      unhealthy) return 1 ;;
    esac
    sleep 3
  done
  return 1
}

# $1 = tag, $2 = "pull" to fetch the image first (skipped for rollback: it is already local)
rollout() {
  set_tag "$1"
  # Explicit returns: set -e is suspended inside functions called from `if`.
  if [[ "${2:-}" == "pull" ]]; then compose pull backend || return 1; fi
  compose up -d --remove-orphans || return 1
  wait_healthy
}

[[ -n "$(read_env ORYN_DOMAIN)" ]] || { echo "ORYN_DOMAIN missing in $ENV_FILE" >&2; exit 2; }
[[ -f /opt/oryn/backend.env ]] || { echo "/opt/oryn/backend.env missing" >&2; exit 2; }

PREVIOUS_TAG="$(read_env IMAGE_TAG)"
echo "deploying $NEW_TAG (previous: ${PREVIOUS_TAG:-none})"

if rollout "$NEW_TAG" pull; then
  echo "deploy ok: $NEW_TAG"
  docker image prune -af --filter "until=168h" >/dev/null
  exit 0
fi

echo "deploy of $NEW_TAG FAILED; recent backend logs:" >&2
compose logs --tail 40 backend >&2 || true

if [[ -n "$PREVIOUS_TAG" && "$PREVIOUS_TAG" != "$NEW_TAG" ]]; then
  echo "rolling back to $PREVIOUS_TAG" >&2
  if rollout "$PREVIOUS_TAG"; then
    echo "rollback ok: $PREVIOUS_TAG is serving" >&2
  else
    echo "ROLLBACK ALSO FAILED; manual intervention needed" >&2
  fi
else
  echo "no previous tag to roll back to" >&2
fi
exit 1
