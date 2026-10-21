#!/usr/bin/env bash
#
# Sets every GitHub Actions secret the shorts factory needs, in one go.
#
#   ./scripts/setup_secrets.sh
#
# It only asks for the values you actually have. Anything you skip stays
# unset and the factory simply runs without that service.
#
set -euo pipefail

REPO="${1:-$(cd "$(dirname "$0")/.." && pwd)}"
cd "$REPO"

if ! command -v gh >/dev/null 2>&1; then
  echo "gh (GitHub CLI) is not installed. See https://cli.github.com" >&2
  exit 1
fi

echo "Setting secrets for $(gh repo view --json nameWithOwner -q .nameWithOwner)"
echo

ask() {
  # ask <secret-name> <prompt> [secret]
  local name="$1" prompt="$2" secret="${3:-}"
  printf '%s' "$prompt"
  if [ -n "$secret" ]; then
    read -r -s value
    echo
  else
    read -r value
  fi
  if [ -z "$value" ]; then
    echo "  -> skipped $name"
    echo
    return
  fi
  gh secret set "$name" --body "$value"
  echo "  -> set $name"
  echo
}

ask GEMINI_API_KEY \
  "Gemini API key (https://aistudio.google.com/apikey) : "

ask PIXABAY_API_KEY \
  "Pixabay API key (https://pixabay.com/api/docs/) - leave empty to skip: " secret

ask PEXELS_API_KEY \
  "Pexels API key (https://www.pexels.com/api/) - leave empty to skip: " secret

if [ -f service-account.json ]; then
  echo "Found ./service-account.json, using it as-is."
  gh secret set GOOGLE_SERVICE_ACCOUNT --body "$(cat service-account.json)"
  echo "  -> set GOOGLE_SERVICE_ACCOUNT"
  echo
  SERVICE_ACCOUNT_EMAIL="$(python3 -c 'import json,sys; print(json.load(open("service-account.json"))["client_email"])' 2>/dev/null || true)"
  if [ -n "$SERVICE_ACCOUNT_EMAIL" ]; then
    echo "  Share your target Drive folder with: $SERVICE_ACCOUNT_EMAIL"
  fi
  echo
else
  ask GOOGLE_SERVICE_ACCOUNT \
    "Google service account JSON - paste the file content: " secret
fi

ask TELEGRAM_BOT_TOKEN \
  "Telegram bot token (from @BotFather) - leave empty to skip: " secret

ask TELEGRAM_CHAT_ID \
  "Telegram chat id - leave empty to skip: "

ask N8N_WEBHOOK_URL \
  "n8n webhook URL (https://host/webhook/shorts-factory) - leave empty to skip: "

echo "Current secrets:"
gh secret list
