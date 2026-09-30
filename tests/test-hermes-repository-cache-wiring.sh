#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."

bash -n scripts/hermes-native.sh scripts/fleet.sh bin/hermes-git-read-askpass "$0"
python3 - <<'PY'
from pathlib import Path
native = Path("scripts/hermes-native.sh").read_text()
for text in (
    'REPOSITORY_CACHE_BIN="$SUPPORT_ROOT/hermes-repository-cache"',
    'GIT_READ_ASKPASS="$SUPPORT_ROOT/hermes-git-read-askpass"',
    'install_repository_cache() {',
    'install -m 0555 -o root -g wheel "$ROOT/scripts/hermes-repository-cache.py" "$REPOSITORY_CACHE_BIN"',
    'GIT_ASKPASS_REQUIRE=force',
    'GITHUB_READ_TOKEN_FILE="$GITHUB_READ_TOKEN_FILE"',
    'repo-cache-install) install_repository_cache',
    'repo-cache-enroll) shift; repository_cache enroll',
    'repo-cache-sync) shift; repository_cache sync',
    'repo-cache-status) shift; repository_cache status',
): assert text in native, text
fleet = Path("scripts/fleet.sh").read_text()
for text in ('repo-cache-install)', 'repo-cache-enroll)', 'repo-cache-sync|repo-cache-status)', 'sudo "$ROOT/scripts/hermes-native.sh" repo-cache-install'):
    assert text in fleet, text
PY

tmp="$(mktemp -d)"; trap 'rm -rf "$tmp"' EXIT
mkdir -p "$tmp/repo/scripts" "$tmp/repo/bin" "$tmp/repo/agent-config/hermes" "$tmp/support" "$tmp/cache" "$tmp/seed/.git" "$tmp/home"
cp scripts/hermes-native.sh "$tmp/repo/scripts/"
cp agent-config/hermes/native.env "$tmp/repo/agent-config/hermes/"
python3 - "$tmp/repo/scripts/hermes-native.sh" <<'PY'
from pathlib import Path
import sys
path=Path(sys.argv[1]); text=path.read_text(); old='need_root() { [[ "$EUID" == 0 ]] || { echo "run as root" >&2; exit 2; }; }'
assert old in text; path.write_text(text.replace(old,'need_root() { :; }'))
PY
cat >"$tmp/support/hermes-repository-cache" <<'SH'
#!/usr/bin/env bash
printf '%s\n' "$@" >"$CAPTURE_ARGS"
printf '%s\n' "${GIT_ASKPASS:-}" "${GIT_ASKPASS_REQUIRE:-}" "${GITHUB_READ_TOKEN_FILE:-}" >"$CAPTURE_ENV"
SH
chmod +x "$tmp/support/hermes-repository-cache"
touch "$tmp/support/hermes-git-read-askpass" "$tmp/token"
export HERMES_SERVICE_USER="$(id -un)" HERMES_SERVICE_HOME="$tmp/home" HERMES_NATIVE_HOME="$tmp/home/.hermes"
export HERMES_NATIVE_SUPPORT_ROOT="$tmp/support" HERMES_REPOSITORY_CACHE_ROOT="$tmp/cache" GITHUB_READ_TOKEN_FILE="$tmp/token"
export CAPTURE_ARGS="$tmp/args" CAPTURE_ENV="$tmp/env"

bash "$tmp/repo/scripts/hermes-native.sh" repo-cache-enroll ROKT/cpi "$tmp/seed"
python3 - "$tmp" <<'PY'
from pathlib import Path
import sys
root=Path(sys.argv[1]); args=(root/'args').read_text().splitlines(); env=(root/'env').read_text().splitlines()
assert args[-4:] == ['enroll','ROKT/cpi','--seed',str(root/'seed')], args
assert '--reader-group' in args and args[args.index('--root')+1] == str(root/'cache')
assert env == [str(root/'support/hermes-git-read-askpass'),'force',str(root/'token')]
PY
bash "$tmp/repo/scripts/hermes-native.sh" repo-cache-sync ROKT/cpi
[[ "$(tail -n2 "$tmp/args" | tr '\n' ' ')" == 'sync ROKT/cpi ' ]]
bash "$tmp/repo/scripts/hermes-native.sh" repo-cache-status ROKT/cpi
[[ "$(tail -n2 "$tmp/args" | tr '\n' ' ')" == 'status ROKT/cpi ' ]]
if bash "$tmp/repo/scripts/hermes-native.sh" repo-cache-enroll bad/repo relative >/dev/null 2>&1; then exit 1; fi

printf 'token-value' >"$tmp/token"
[[ "$(GITHUB_READ_TOKEN_FILE="$tmp/token" bin/hermes-git-read-askpass 'Username for github')" == x-access-token ]]
[[ "$(GITHUB_READ_TOKEN_FILE="$tmp/token" bin/hermes-git-read-askpass 'Password for github')" == token-value ]]
if GITHUB_READ_TOKEN_FILE="$tmp/token" bin/hermes-git-read-askpass other >/dev/null 2>&1; then exit 1; fi

echo 'PASS: repository cache host wiring is scoped and token-isolated'
