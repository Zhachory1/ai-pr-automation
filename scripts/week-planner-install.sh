#!/usr/bin/env bash
set -euo pipefail

mode="${1:---check}"
[[ "$mode" == --check || "$mode" == --install ]] || { echo 'usage: week-planner-install.sh [--check|--install]' >&2; exit 2; }
root="$(cd "$(dirname "$0")/.." && pwd)"
source="$root/agent-config/hermes/profiles/week-planner"
profile="$HOME/.hermes/profiles/week-planner"
hermes="${HERMES_BIN:-$HOME/.local/bin/hermes}"
python="${HERMES_PYTHON:-$HOME/.hermes/hermes-agent/venv/bin/python}"
script="$root/scripts/hermes-week-calendar.py"
[[ -x "$python" && -f "$script" && -x "$hermes" && -f "$source/config.yaml" && -f "$source/SOUL.md" ]] \
  || { echo 'Hermes or planner source unavailable' >&2; exit 1; }
if [[ "$mode" == --install ]]; then
  lock="$HOME/.hermes/.week-planner-install.lock"
  mkdir -m 0700 "$lock" || { echo 'another planner install is running, or a stale lock needs inspection' >&2; exit 1; }
  trap 'rmdir "$lock"' EXIT
fi

created=0
if [[ ! -d "$profile" ]]; then
  [[ "$mode" == --install ]] || { echo 'week-planner profile not installed' >&2; exit 1; }
  "$hermes" profile create week-planner --no-skills --no-alias
  created=1
fi
[[ ! -L "$profile" && "$(stat -f '%Su:%Lp' "$profile")" == "$(id -un):700" ]] \
  || { echo 'week-planner profile must be owner-only' >&2; exit 1; }

for file in config.yaml SOUL.md; do
  if (( created )); then
    install -m 0600 "$source/$file" "$profile/$file"
  else
    cmp -s "$source/$file" "$profile/$file" || { echo "profile $file differs; inspect before updating" >&2; exit 1; }
  fi
done
[[ ! -L "$profile/.env" && "$(stat -f '%Su:%Lp' "$profile/.env")" == "$(id -un):600" ]] \
  || { echo 'profile .env must be owner-only' >&2; exit 1; }
values=(
  "HERMES_WEEK_CALENDAR_PYTHON=$python"
  "HERMES_WEEK_CALENDAR_SCRIPT=$script"
  "HERMES_WEEK_PLANNER_HOME=$profile"
)
for value in "${values[@]}"; do
  if (( created )); then
    printf '%s\n' "$value" >> "$profile/.env"
  else
    key="${value%%=*}"
    [[ "$(grep -c "^${key}=" "$profile/.env" || true)" == 1 ]] && grep -Fxq "$value" "$profile/.env" \
      || { echo 'profile .env path differs or is duplicated; inspect before updating' >&2; exit 1; }
  fi
done

jobs="$("$hermes" -p week-planner cron list --all)"
if grep -Fq 'Name:      Plan next week' <<<"$jobs"; then
  grep -A1 -F 'Name:      Plan next week' <<<"$jobs" | grep -Eq 'Schedule:[[:space:]]+0 17 \* \* 0' \
    || { echo 'existing planner cron schedule differs; inspect before changing' >&2; exit 1; }
  echo 'Existing weekly Hermes cron preserved.'
elif [[ "$mode" == --install && "$jobs" == *'No scheduled jobs.'* ]]; then
  "$hermes" -p week-planner cron create '0 17 * * 0' \
    'Read week_context for next Monday. Use the private rules and all calendar events to prioritize the week. Use create_block only for missing private planner events with stable week-and-task keys. Never move an existing event in a cron run. Report anything that cannot fit or any tool error.' \
    --name 'Plan next week' --provider openai-codex --model gpt-5.6-terra \
    --paused --paused-reason 'Awaiting Google Calendar OAuth and target verification' --deliver local
elif [[ "$mode" == --check ]]; then
  echo 'No weekly Hermes cron installed.' >&2
  exit 1
else
  echo 'Other profile cron jobs exist; inspect before creating a planner job.' >&2
  exit 1
fi
