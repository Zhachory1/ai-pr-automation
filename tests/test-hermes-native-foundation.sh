#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."

tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT
mkdir -p "$tmp/install" "$tmp/service/.hermes/profiles/smoke-v1" "$tmp/service/.local/bin" "$tmp/source"
git -C "$tmp/install" init -q
git -C "$tmp/install" config user.email test@example.com
git -C "$tmp/install" config user.name test
printf 'pin\n' > "$tmp/install/pin"
git -C "$tmp/install" add pin
git -C "$tmp/install" commit -qm pin
commit="$(git -C "$tmp/install" rev-parse HEAD)"
for file in SOUL.md config.yaml distribution.yaml .no-bundled-skills; do
  printf '%s\n' "$file" > "$tmp/source/$file"
  cp "$tmp/source/$file" "$tmp/service/.hermes/profiles/smoke-v1/$file"
done
cat > "$tmp/service/.local/bin/hermes" <<EOF
#!/usr/bin/env bash
if [[ "\${1:-}" == --version ]]; then echo 'Hermes Agent v0.21.3'; exit 0; fi
[[ "\$*" == '-p smoke-v1 profile show smoke-v1' ]]
EOF
chmod +x "$tmp/service/.local/bin/hermes"
cat > "$tmp/contract.env" <<EOF
HERMES_NATIVE_VERSION=0.21.3
HERMES_NATIVE_COMMIT=$commit
HERMES_INSTALLER_SHA256=$(printf installer | shasum -a 256 | awk '{print $1}')
EOF
profile_digest="$(python3 - "$tmp/source" <<'PY'
import hashlib,pathlib,sys
root=pathlib.Path(sys.argv[1]); value=hashlib.sha256()
for name in ('.no-bundled-skills','SOUL.md','config.yaml','distribution.yaml'):
    value.update(name.encode()+b'\0'+(root/name).read_bytes()+b'\0')
print(value.hexdigest())
PY
)"
python3 - "$tmp/manifest.json" "$commit" "$tmp/service/.local/bin/hermes" "$profile_digest" <<'PY'
import hashlib,json,pathlib,sys
out,commit,launcher,profile=sys.argv[1:]
pathlib.Path(out).write_text(json.dumps({
  'version':'0.21.3','commit':commit,
  'installer_sha256':hashlib.sha256(b'installer').hexdigest(),
  'launcher_sha256':hashlib.sha256(pathlib.Path(launcher).read_bytes()).hexdigest(),
  'profile_digest':profile}))
PY
scripts/hermes-native-preflight.py --contract "$tmp/contract.env" --manifest "$tmp/manifest.json" \
  --install-dir "$tmp/install" --hermes-home "$tmp/service/.hermes" --profile-source "$tmp/source" \
  | jq -e '.status == "ready" and .profile == "smoke-v1"' >/dev/null
if scripts/hermes-native-preflight.py --contract "$tmp/contract.env" --manifest "$tmp/manifest.json" \
  --install-dir "$tmp/install" --hermes-home "$tmp/service/.hermes" --profile-source "$tmp/source" \
  --service-user nobody >/dev/null 2>&1; then
  echo 'FAIL: non-root service-user preflight accepted' >&2; exit 1
fi
printf 'changed\n' >> "$tmp/service/.hermes/profiles/smoke-v1/SOUL.md"
if scripts/hermes-native-preflight.py --contract "$tmp/contract.env" --manifest "$tmp/manifest.json" \
  --install-dir "$tmp/install" --hermes-home "$tmp/service/.hermes" --profile-source "$tmp/source" >/dev/null 2>&1; then
  echo 'FAIL: changed installed profile passed preflight' >&2; exit 1
fi

plutil -lint launchd/com.example.ai-pr-automation-hermes.plist.template >/dev/null
grep -Fq 'mktemp /private/tmp/hermes-install.XXXXXX' scripts/hermes-native.sh
# shellcheck disable=SC2016
grep -Fq 'chmod 0444 "$installer"' scripts/hermes-native.sh
# Existing pinned installs can sync profiles/binaries/plists without downloading the mutable
# installer URL. Full install still keeps the digest gate.
grep -Fq 'sync-support) HERMES_SUPPORT_ONLY=true install_native' scripts/hermes-native.sh
grep -Fq "if [[ \"\${HERMES_SUPPORT_ONLY:-false}\" != true ]]" scripts/hermes-native.sh
grep -Fq "hermes-doc-write-runner\" \"\$SUPPORT_ROOT/hermes-doc-write-runner\"" scripts/hermes-native.sh
grep -Fq "doc-writer-publication\" \"\$SUPPORT_ROOT/doc-writer-publication\"" scripts/hermes-native.sh
grep -Fq 'provider: anthropic' agent-config/hermes/profiles/doc-write-v1/config.yaml
grep -Fq 'cli: []' agent-config/hermes/profiles/doc-write-v1/config.yaml
grep -Fq 'DOC_WRITER_STAGE_DIR: /work' docker-compose.yml
grep -Fq 'DOC_WRITER_STAGE_HOST' docker-compose.yml
grep -Fq 'DOC_WRITER_STAGE_HOST=' .env.example
# LaunchDaemon log files must exist before bootstrap; hermes-agent cannot create files in root-owned
# LOG_ROOT and launchd otherwise exits EX_CONFIG before running the program.
grep -Fq "install -m 0600 -o \"\$SERVICE_USER\" -g staff /dev/null \"\$LOG_ROOT/\$logfile.log\"" scripts/hermes-native.sh
mkdir -p "$tmp/runtime"
cat > "$tmp/fake-hermes" <<'SH'
#!/usr/bin/env bash
echo started > "$TEST_STARTED"
SH
chmod +x "$tmp/fake-hermes"
TEST_STARTED="$tmp/started" HERMES_HOME="$tmp/runtime" HERMES_BIN="$tmp/fake-hermes" \
  HERMES_MAINTENANCE_FILE="$tmp/maintenance" bin/hermes-native-gateway
[[ -e "$tmp/started" ]]
rm "$tmp/started"; touch "$tmp/maintenance"
TEST_STARTED="$tmp/started" HERMES_HOME="$tmp/runtime" HERMES_BIN="$tmp/fake-hermes" \
  HERMES_MAINTENANCE_FILE="$tmp/maintenance" bin/hermes-native-gateway
[[ ! -e "$tmp/started" ]]

echo 'PASS: native Hermes pin, profile, plist, and maintenance gate'
