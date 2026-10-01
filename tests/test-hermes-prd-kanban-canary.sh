#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."

native=scripts/hermes-native.sh
fleet=scripts/fleet.sh
bash -n "$native" "$fleet" "$0"

python3 - <<'PY'
from pathlib import Path
native = Path("scripts/hermes-native.sh").read_text()
for text in (
    'PRD_KANBAN_ENQUEUE="$SUPPORT_ROOT/hermes-prd-kanban-enqueue.py"',
    'PRD_KANBAN_ADVANCE="$SUPPORT_ROOT/hermes-prd-kanban-advance.py"',
    'install -m 0555 -o root -g wheel "$ROOT/scripts/hermes-prd-kanban-enqueue.py" "$PRD_KANBAN_ENQUEUE"',
    'install -m 0555 -o root -g wheel "$ROOT/scripts/hermes-prd-kanban-advance.py" "$PRD_KANBAN_ADVANCE"',
    '"$ROOT/scripts/hermes-prd-kanban-enqueue.py:$PRD_KANBAN_ENQUEUE"',
    '"$ROOT/scripts/hermes-prd-kanban-advance.py:$PRD_KANBAN_ADVANCE"',
): assert text in native, text
commands = native[native.index("prd_canary_enqueue() {"):native.index("\nrequire_v2_services_unloaded()")]
for text in (
    'need_root', '"$#" == 1', '"$1" == /*', 'os.O_RDONLY | os.O_NOFOLLOW',
    'stat.S_ISREG(status.st_mode)', 'source.read(131073)', 'len(data) > 131072',
    'PRD_WORKFLOW_ENGINE:-fixed', '"--engine", engine', '"--document-kind", document_kind', '"--repository-cache-root", repository_cache',
    '["sudo", "-u", user, "env"', 'subprocess.run(command, input=data)',
    '"$INSTALL_DIR/venv/bin/python" "$PRD_KANBAN_ENQUEUE" "$LAUNCHER"',
    '"$1" =~ ^prd-[0-9a-f]{64}$', '"$PRD_KANBAN_ADVANCE"', '--operation-id "$1"',
): assert text in commands, text
for forbidden in ("preflight", "profiles/", "config.yaml", "cp ", "install "):
    assert forbidden not in commands, forbidden
install = native[native.index("install_native() {"):native.index("\npreflight() {")]
preflight = native[native.index("preflight() {"):native.index('\ncase "${1:-}"')]
up = native[native.index("  up)"):native.index("  down)")]
down = native[native.index("  down)"):native.index("  status)")]
for section in (install, preflight, up, down):
    assert "prd_canary_enqueue" not in section and "prd_canary_advance" not in section
fleet = Path("scripts/fleet.sh").read_text()
assert 'sudo env PRD_WORKFLOW_ENGINE="${PRD_WORKFLOW_ENGINE:-fixed}"' in fleet
assert '"$ROOT/scripts/hermes-native.sh" prd-canary-enqueue "$2"' in fleet
assert 'design-canary-enqueue|roadmap-canary-enqueue)' in fleet
assert 'sudo "$ROOT/scripts/hermes-native.sh" "$1" "$2"' in fleet
assert 'sudo "$ROOT/scripts/hermes-native.sh" prd-canary-advance "$2"' in fleet
for section in (fleet[fleet.index("  up)"):fleet.index("  down)")], fleet[fleet.index("  down)"):fleet.index("  review-kanban-up)")]):
    assert "prd-canary" not in section
env = Path(".env.example").read_text()
for text in (
    '"operation_id":', '"requester":', '"requirements":', '"title":',
    "scripts/fleet.sh prd-canary-enqueue /absolute/path/prd-intake.json",
    "scripts/fleet.sh prd-canary-advance prd-66c7963fc695491f540b081519813d41ea12a4d71af7ce3b33c995568394fd3d",
    "PRD_WORKFLOW_ENGINE=fixed # fixed|dynamic",
): assert text in env, text
PY

tmp="$(mktemp -d)"; trap 'rm -rf "$tmp"' EXIT
mkdir -p "$tmp/repo/scripts" "$tmp/repo/agent-config/hermes" "$tmp/repo/policy" "$tmp/bin" "$tmp/home" "$tmp/support" "$tmp/install/venv/bin"
cp "$native" "$tmp/repo/scripts/hermes-native.sh"
cp agent-config/hermes/native.env "$tmp/repo/agent-config/hermes/native.env"
cp "$fleet" "$tmp/repo/scripts/fleet.sh"
cp policy/pr-safety-policy-v1.md "$tmp/repo/policy/"
python3 - "$tmp/repo/scripts/hermes-native.sh" <<'PY'
from pathlib import Path
import sys
path = Path(sys.argv[1]); source = path.read_text()
old = 'need_root() { [[ "$EUID" == 0 ]] || { echo "run as root" >&2; exit 2; }; }'
assert old in source
path.write_text(source.replace(old, 'need_root() { :; }'))
PY
cat > "$tmp/bin/sudo" <<'SH'
#!/usr/bin/env bash
set -euo pipefail
cat > "$MOCK_STDIN"
python3 - "$MOCK_ARGV" "$@" <<'PY'
import json, pathlib, sys
pathlib.Path(sys.argv[1]).write_text(json.dumps(sys.argv[2:]))
PY
printf '{"status":"mock"}\n'
SH
chmod +x "$tmp/bin/sudo"
export PATH="$tmp/bin:$PATH" MOCK_ARGV="$tmp/argv.json" MOCK_STDIN="$tmp/stdin"
export HERMES_SERVICE_USER="$(id -un)" HERMES_SERVICE_HOME="$tmp/home"
export HERMES_NATIVE_HOME="$tmp/home/.hermes" HERMES_NATIVE_INSTALL_DIR="$tmp/install"
export HERMES_NATIVE_SUPPORT_ROOT="$tmp/support" HERMES_REPOSITORY_CACHE_ROOT="$tmp/repositories"
operation=prd-66c7963fc695491f540b081519813d41ea12a4d71af7ce3b33c995568394fd3d
intake="$tmp/intake.json"
printf '%s' '{"operation_id":"prd-66c7963fc695491f540b081519813d41ea12a4d71af7ce3b33c995568394fd3d","requester":"operator@example.com","requirements":"Describe the approved canary scope.","title":"Canary PRD"}' > "$intake"

out="$(bash "$tmp/repo/scripts/hermes-native.sh" prd-canary-enqueue "$intake")"
[[ "$out" == '{"status":"mock"}' ]]
cmp -s "$intake" "$MOCK_STDIN"
python3 - "$MOCK_ARGV" "$tmp" <<'PY'
import json, pathlib, sys
args = json.loads(pathlib.Path(sys.argv[1]).read_text()); root = pathlib.Path(sys.argv[2])
assert args == ["-u", pathlib.Path.home().owner(), "env", f"HOME={root}/home", f"HERMES_HOME={root}/home/.hermes", f"{root}/install/venv/bin/python", "-B", f"{root}/support/hermes-prd-kanban-enqueue.py", "--hermes-home", f"{root}/home/.hermes", "--hermes-bin", f"{root}/home/.local/bin/hermes", "--engine", "fixed", "--document-kind", "prd", "--repository-cache-root", f"{root}/repositories"]
PY

export PRD_WORKFLOW_ENGINE=dynamic
bash "$tmp/repo/scripts/hermes-native.sh" prd-canary-enqueue "$intake" >/dev/null
python3 - "$MOCK_ARGV" <<'PY'
import json, pathlib, sys
args = json.loads(pathlib.Path(sys.argv[1]).read_text())
assert args[args.index("--engine"):args.index("--engine") + 2] == ["--engine", "dynamic"]
PY
unset PRD_WORKFLOW_ENGINE

bash "$tmp/repo/scripts/hermes-native.sh" design-canary-enqueue "$intake" >/dev/null
python3 - "$MOCK_ARGV" <<'PY'
import json, pathlib, sys
args = json.loads(pathlib.Path(sys.argv[1]).read_text())
assert args[args.index("--engine"):args.index("--engine") + 2] == ["--engine", "dynamic"]
assert args[args.index("--document-kind"):args.index("--document-kind") + 2] == ["--document-kind", "design"]
PY

bash "$tmp/repo/scripts/hermes-native.sh" roadmap-canary-enqueue "$intake" >/dev/null
python3 - "$MOCK_ARGV" <<'PY'
import json, pathlib, sys
args = json.loads(pathlib.Path(sys.argv[1]).read_text())
assert args[args.index("--engine"):args.index("--engine") + 2] == ["--engine", "dynamic"]
assert args[args.index("--document-kind"):args.index("--document-kind") + 2] == ["--document-kind", "roadmap"]
PY

out="$(bash "$tmp/repo/scripts/hermes-native.sh" prd-canary-advance "$operation")"
[[ "$out" == '{"status":"mock"}' ]]
python3 - "$MOCK_ARGV" "$tmp" "$operation" <<'PY'
import json, pathlib, sys
args = json.loads(pathlib.Path(sys.argv[1]).read_text()); root = pathlib.Path(sys.argv[2])
assert args[-6:] == ["--hermes-home", f"{root}/home/.hermes", "--hermes-bin", f"{root}/home/.local/bin/hermes", "--operation-id", sys.argv[3]]
assert f"{root}/support/hermes-prd-kanban-advance.py" in args
PY

reject() {
  rm -f "$MOCK_ARGV"
  if bash "$tmp/repo/scripts/hermes-native.sh" "$@" >/dev/null 2>&1; then echo "accepted invalid native arguments: $*" >&2; exit 1; fi
  [[ ! -e "$MOCK_ARGV" ]]
}
reject prd-canary-enqueue
PRD_WORKFLOW_ENGINE=other reject prd-canary-enqueue "$intake"
(cd "$tmp" && reject prd-canary-enqueue intake.json)
ln -s "$intake" "$tmp/intake-link.json"; reject prd-canary-enqueue "$tmp/intake-link.json"
python3 - "$tmp/large.json" <<'PY'
from pathlib import Path
import sys
Path(sys.argv[1]).write_bytes(b"x" * 131073)
PY
reject prd-canary-enqueue "$tmp/large.json"
reject prd-canary-advance prd-ABC
reject prd-canary-advance "${operation}0"

rm -f "$MOCK_ARGV"
out="$(bash "$tmp/repo/scripts/fleet.sh" prd-canary-enqueue "$intake")"
[[ "$out" == '{"status":"mock"}' ]]
python3 - "$MOCK_ARGV" "$tmp" "$intake" <<'PY'
import json, pathlib, sys
args = json.loads(pathlib.Path(sys.argv[1]).read_text()); root = pathlib.Path(sys.argv[2])
assert args == ["env", "PRD_WORKFLOW_ENGINE=fixed", f"{root}/repo/scripts/hermes-native.sh", "prd-canary-enqueue", sys.argv[3]]
PY
rm -f "$MOCK_ARGV"
PRD_WORKFLOW_ENGINE=dynamic bash "$tmp/repo/scripts/fleet.sh" prd-canary-enqueue "$intake" >/dev/null
python3 - "$MOCK_ARGV" <<'PY'
import json, pathlib, sys
assert json.loads(pathlib.Path(sys.argv[1]).read_text())[0:2] == ["env", "PRD_WORKFLOW_ENGINE=dynamic"]
PY
rm -f "$MOCK_ARGV"
bash "$tmp/repo/scripts/fleet.sh" prd-canary-advance "$operation" >/dev/null
python3 - "$MOCK_ARGV" "$tmp" "$operation" <<'PY'
import json, pathlib, sys
args = json.loads(pathlib.Path(sys.argv[1]).read_text()); root = pathlib.Path(sys.argv[2])
assert args == [f"{root}/repo/scripts/hermes-native.sh", "prd-canary-advance", sys.argv[3]]
PY
rm -f "$MOCK_ARGV"
if bash "$tmp/repo/scripts/fleet.sh" prd-canary-enqueue >/dev/null 2>&1; then exit 1; fi
[[ ! -e "$MOCK_ARGV" ]]

echo 'PASS: PRD canary operator wiring is manual, bounded, and exact-byte'
