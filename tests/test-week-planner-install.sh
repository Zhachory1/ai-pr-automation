#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
tmp="$(mktemp -d)"; trap 'rm -rf "$tmp"' EXIT
cat > "$tmp/hermes" <<'SH'
#!/usr/bin/env bash
set -euo pipefail
case "$*" in
  'profile create week-planner --no-skills --no-alias')
    root="$HOME/.hermes/profiles/week-planner"
    install -d -m 0700 "$root"
    printf 'generated\n' > "$root/config.yaml"
    printf 'generated\n' > "$root/SOUL.md"
    printf '# generated\n' > "$root/.env"
    chmod 0600 "$root/config.yaml" "$root/SOUL.md" "$root/.env"
    ;;
  '-p week-planner cron list --all')
    if [[ -e "$HOME/cron-created" ]]; then
      printf '    Name:      Plan next week\n    Schedule:  0 17 * * 0\n'
    else
      echo 'No scheduled jobs.'
    fi
    ;;
  '-p week-planner cron create '* )
    [[ "$*" == *' --paused '* ]] || exit 1
    : > "$HOME/cron-created"
    echo 'Created paused planner cron'
    ;;
  *) echo "unexpected Hermes command" >&2; exit 1 ;;
esac
SH
chmod +x "$tmp/hermes"
python_bin="$(command -v python3)"
export HOME="$tmp" HERMES_BIN="$tmp/hermes" HERMES_PYTHON="$python_bin"
install -d -m 0700 "$HOME/.hermes"
if bash scripts/week-planner-install.sh --check >/dev/null 2>&1; then
  echo 'check accepted an absent profile' >&2; exit 1
fi
bash scripts/week-planner-install.sh --install >/dev/null
root="$HOME/.hermes/profiles/week-planner"
cmp agent-config/hermes/profiles/week-planner/config.yaml "$root/config.yaml"
cmp agent-config/hermes/profiles/week-planner/SOUL.md "$root/SOUL.md"
[[ -e "$HOME/cron-created" ]]
[[ "$(stat -f '%Lp' "$root/.env")" == 600 ]]
mkdir -m 700 "$HOME/.hermes/.week-planner-install.lock"
if bash scripts/week-planner-install.sh --install >/dev/null 2>&1; then
  echo 'concurrent install bypassed lock' >&2; exit 1
fi
rmdir "$HOME/.hermes/.week-planner-install.lock"
bash scripts/week-planner-install.sh --install | grep -q 'Existing weekly Hermes cron preserved'
bash scripts/week-planner-install.sh --check | grep -q 'Existing weekly Hermes cron preserved'
printf 'HERMES_WEEK_PLANNER_HOME=/another/profile\n' >> "$root/.env"
if bash scripts/week-planner-install.sh --check >/dev/null 2>&1; then
  echo 'check accepted a duplicated profile path' >&2; exit 1
fi
python3 - "$root/.env" <<'PY'
from pathlib import Path
import sys
p=Path(sys.argv[1]); p.write_text(''.join(line for line in p.read_text().splitlines(keepends=True) if line != 'HERMES_WEEK_PLANNER_HOME=/another/profile\n'))
PY
rm "$HOME/cron-created"
if bash scripts/week-planner-install.sh --check >/dev/null 2>&1; then
  echo 'check accepted a missing planner cron' >&2; exit 1
fi
bash scripts/week-planner-install.sh --install >/dev/null
printf 'drift\n' >> "$root/SOUL.md"
if bash scripts/week-planner-install.sh --check >/dev/null 2>&1; then
  echo 'check accepted profile drift' >&2; exit 1
fi
echo 'PASS: planner setup creates one paused cron, remains idempotent, and refuses drift'
