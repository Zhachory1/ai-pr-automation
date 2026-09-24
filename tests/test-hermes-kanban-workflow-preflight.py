#!/usr/bin/env python3
import importlib.util
import json
import os
import pathlib
import shutil
import subprocess
import runpy
import sys
import tempfile
import unittest

import yaml

ROOT = pathlib.Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("kanban_preflight", ROOT / "scripts/hermes-kanban-workflow-preflight.py")
preflight = importlib.util.module_from_spec(spec); spec.loader.exec_module(preflight)
CONTRACT = ROOT / "agent-config/hermes/workflows/pr-risk-council-kanban.json"
V2_CONTRACT = ROOT / "agent-config/hermes/workflows/pr-risk-council-kanban-v2.json"
SERVER = ROOT / "bin/hermes-council-tools"
SERVER_TOOLS = runpy.run_path(SERVER)["TOOLS"]
SERVER_DEFINITIONS = [{"type":"function","function":{
    "name":f"mcp__council_tools__{tool['name']}","description":tool["description"],
    "parameters":preflight.effective_input_schema(tool["inputSchema"])}} for tool in SERVER_TOOLS]


class KanbanWorkflowPreflightTest(unittest.TestCase):
    def fixture(self, root, contract_path=CONTRACT):
        home, install = root / ".hermes", root / "install"
        contract = preflight.load_contract(contract_path)
        for target, value in contract["profiles"].items():
            profile = home / "profiles" / (value["source"] if contract["schema_version"] == 1 else target)
            profile.mkdir(parents=True)
            if contract["schema_version"] == 1:
                (profile / "config.yaml").write_text("model:\n  provider: anthropic\n  default: claude-opus-5\n")
                (profile / "profile.yaml").write_text(f"description: Existing {value['role']} specialist\n")
            else:
                (profile / "config.yaml").write_text(yaml.safe_dump(preflight.v2_config(value["model"]), sort_keys=False))
                (profile / "profile.yaml").write_text(yaml.safe_dump({
                    "description":f"Existing {value['role']} specialist",
                    "workflow":{"name":"pr-risk-council","source_profile":value["source"]}}, sort_keys=False))
        for relative in preflight.REQUIRED_MODULES:
            path = install / relative; path.parent.mkdir(parents=True, exist_ok=True); path.write_text("# fixture\n")
            init = path.parent / "__init__.py"; init.touch(exist_ok=True)
        (install / "tools/kanban_tools.py").write_text(
            "def _handle_show(args): pass\n"
            "def _handle_comment(args): pass\n"
            "def _handle_heartbeat(args): pass\n"
            "def _handle_complete(args): pass\n"
            "def _handle_block(args): pass\n")
        (install / "hermes_cli/kanban_db_connect.py").write_text(
            "from contextlib import contextmanager\n"
            "class Conn:\n def close(self): pass\n"
            "def connect(path): return Conn()\n"
            "@contextmanager\n"
            "def _dispatch_tick_lock(path): yield True\n")
        (install / "hermes_cli/kanban_db.py").write_text('''import os
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
tasks={}; comments={}; seq=0
def _normalize_board_slug(value): return value if value and value == value.strip().lower() else None
def kanban_db_path(board=None): return Path(os.environ["HERMES_KANBAN_DB"])
def _terminate_reclaimed_worker(pid,claim_lock,signal_fn=None): return {"terminated":True}
@contextmanager
def write_txn(conn): yield
def create_task(conn,*,title,assignee=None,parents=None,**kw):
 global seq; seq+=1; key=f"t_{seq}"; tasks[key]={"status":"todo" if parents else "ready","assignee":assignee,"parents":parents or []}; return key
def get_task(conn,key): return SimpleNamespace(**tasks[key])
def add_comment(conn,key,author,body): comments.setdefault(key,[]).append((author,body)); return len(comments[key])
def list_comments(conn,key): return comments.get(key,[])
def complete_task(conn,key,**kw):
 tasks[key]["status"]="done"
 for item in tasks.values():
  if item["status"]=="todo" and all(tasks[parent]["status"]=="done" for parent in item["parents"]): item["status"]="ready"
 return True
def claim_task(conn,key,**kw): tasks[key]["status"]="running"; return SimpleNamespace(current_run_id=1)
def request_review(conn,key,reviewer,**kw): tasks[key].update(status="review",assignee=reviewer); return True
def claim_review_task(conn,key,**kw): return SimpleNamespace(current_run_id=2)
''')
        catalog = install / "hermes_cli/models_catalog_static.py"
        catalog.write_text("\"\"\"Catalog with mixed 'single' and \\\"double\\\" quotes.\"\"\"\n"
                           "MODELS=['claude-sonnet-5','claude-haiku-4-5-20251001']\n")
        (install / "hermes_cli/kanban_parser.py").write_text("FLAGS=['--model','--provider']\n")
        (install / "tools/kanban_tools_schemas.py").write_text("TOOLS=" + repr(preflight.REQUIRED_TOOL_NAMES) + "\n")
        defaults = {"dispatch_in_gateway":True,"review_dispatch":True,"dispatch_interval_seconds":60,
                    "failure_limit":2,"max_in_progress":None,"max_in_progress_per_profile":None,
                    "auto_decompose":True,"dispatch_stale_timeout_seconds":14400,"reconcile_orphans":True}
        (install / "hermes_cli/config_defaults.py").write_text("DEFAULT_CONFIG={'kanban':" + repr(defaults) + "}\n")
        (install / "hermes_cli/config.py").write_text(
            "import os,yaml\nfrom pathlib import Path\nDEFAULT_CONFIG={'kanban':" + repr(defaults) + "}\n"
            "def read_raw_config(): return yaml.safe_load((Path(os.environ['HERMES_HOME'])/'config.yaml').read_text())\n"
            "def load_config(): return read_raw_config()\n")
        (install / "hermes_cli/tools_config.py").write_text(
            "def _get_platform_tools(config,platform,include_default_mcp_servers=True):\n"
            " return set(config['platform_toolsets'][platform])\n")
        (install / "tools/mcp_tool_discovery.py").write_text('''import json,os,subprocess
from hermes_cli.config import load_config

def discover_mcp_tools(allowed_mcp_names=None):
 assert allowed_mcp_names == ["council-tools"]
 config=load_config()["mcp_servers"]["council-tools"]
 aliases={key:os.environ[value[2:-1]] for key,value in config["env"].items()}
 safe={key:value for key,value in os.environ.items() if key in {"PATH","HOME","USER","LANG","LC_ALL","TERM","SHELL","TMPDIR"} or key.startswith("XDG_")}
 for key in ("HERMES_KANBAN_DB","HERMES_KANBAN_BOARD"):
  if key in os.environ: safe[key]=os.environ[key]
 safe.update(aliases)
 for key in ("HERMES_KANBAN_TASK","HERMES_KANBAN_RUN_ID","HERMES_KANBAN_CLAIM_LOCK"):
  safe.pop(key,None)
 safe["HERMES_DELEGATED_CHILD_CONTEXT"]="1"
 safe["PYTHONPATH"]=os.environ["PYTHONPATH"]
 command=os.environ.get("COUNCIL_TOOLS_PROBE_COMMAND",config["command"])
 requests="\\n".join((json.dumps({"jsonrpc":"2.0","id":1,"method":"initialize","params":{}}),json.dumps({"jsonrpc":"2.0","id":2,"method":"tools/list","params":{}})))+"\\n"
 completed=subprocess.run([command],input=requests,capture_output=True,text=True,env=safe,check=True,timeout=5)
 messages=[json.loads(line) for line in completed.stdout.splitlines()]
 tools=messages[1]["result"]["tools"]
 return ["mcp__council_tools__"+tool["name"] for tool in tools]
''')
        (install / "model_tools.py").write_text(
            "DEFINITIONS=" + repr(SERVER_DEFINITIONS) + "\n"
            "def get_tool_definitions(**kwargs): return DEFINITIONS\n")
        venv = install / "venv/bin"; venv.mkdir(parents=True); (venv / "python").symlink_to(sys.executable)
        return home, install

    def installed_council_tools(self, root):
        support = root / "support"; support.mkdir(mode=0o700)
        server = support / "hermes-council-tools"; shutil.copyfile(SERVER, server); server.chmod(0o555)
        return server, support

    def test_ready_report_is_inert_and_cost_bounded(self):
        with tempfile.TemporaryDirectory() as td:
            home, install = self.fixture(pathlib.Path(td)); result = preflight.preflight(home, install, CONTRACT)
        self.assertTrue(result["feasible"]); self.assertEqual(result["engine"], "kanban")
        self.assertEqual((result["service_state_writes"], result["model_calls"]), (0, 0))
        self.assertTrue(result["isolated_temporary_database"])
        self.assertEqual(result["isolated_probe"], {"graph":{"parents":4,"verifier_status":"ready"},
                                                     "review_status":"done","comment_count":1})
        self.assertEqual(result["profiles"]["council-orchestrator"]["model"], "claude-sonnet-5")
        self.assertEqual({value["model"] for key, value in result["profiles"].items() if key != "council-orchestrator"},
                         {"claude-haiku-4-5-20251001"})
        self.assertEqual(result["kanban_defaults"]["failure_limit"], 2)
        self.assertEqual(result["stop_safety_helpers"],
                         ["_dispatch_tick_lock","_terminate_reclaimed_worker","write_txn"])
        self.assertEqual(result["deferred_runtime_enforcement"],
                         ["deadline_seconds","max_active_workflows","profile_tool_policy","token_budget"])

    def test_effective_schema_matches_pinned_mcp_object_normalization(self):
        definitions = {item["function"]["name"]:item["function"]["parameters"]
                       for item in SERVER_DEFINITIONS}
        self.assertEqual(definitions["mcp__council_tools__kanban_show"]["required"], [])
        self.assertEqual(definitions["mcp__council_tools__kanban_heartbeat"]["required"], [])
        metadata = definitions["mcp__council_tools__kanban_complete"]["properties"]["metadata"]
        self.assertEqual(metadata["properties"], {})
        self.assertEqual(metadata["required"], [])

    def test_v2_proves_exact_profiles_graph_and_council_tools(self):
        with tempfile.TemporaryDirectory() as td:
            root = pathlib.Path(td).resolve(); home, install = self.fixture(root, V2_CONTRACT)
            server, trust = self.installed_council_tools(root)
            before = {path.relative_to(home):(path.is_dir(), path.read_bytes() if path.is_file() else None)
                      for path in home.rglob("*")}
            result = preflight.preflight(home, install, V2_CONTRACT, server, os.getuid(), trust)
            after = {path.relative_to(home):(path.is_dir(), path.read_bytes() if path.is_file() else None)
                     for path in home.rglob("*")}
        self.assertEqual(after, before)
        self.assertEqual(result["schema_version"], 2)
        self.assertEqual(set(result["profiles"]), set(preflight.V2_PROFILES))
        self.assertEqual(result["council_tools"]["tools"], list(preflight.COUNCIL_TOOLS))
        self.assertEqual(result["council_tools"]["command"], str(preflight.COUNCIL_TOOLS_COMMAND))
        self.assertEqual(result["council_tools"]["definitions"], SERVER_DEFINITIONS)
        self.assertEqual({tool["name"]:tool["inputSchema"] for tool in SERVER_TOOLS},
                         preflight.EXPECTED_INPUT_SCHEMAS)
        self.assertEqual(set(result["effective_worker_tools"]), set(preflight.V2_PROFILES))
        self.assertEqual(result["required_tools"], list(preflight.COUNCIL_TOOLS))
        for evidence in result["effective_worker_tools"].values():
            self.assertEqual(evidence["tools"], list(preflight.COUNCIL_TOOLS))
            self.assertEqual(evidence["toolsets"], ["council-tools"])
            self.assertEqual(evidence["fallback_providers"], [])
        self.assertNotIn("profile_tool_policy", result["deferred_runtime_enforcement"])
        self.assertEqual((result["service_state_writes"], result["model_calls"]), (0, 0))

    def test_installed_layout_without_repository_bin_uses_trusted_installed_binary(self):
        with tempfile.TemporaryDirectory() as td:
            root = pathlib.Path(td).resolve(); server, support = self.installed_council_tools(root)
            original = preflight.__file__
            try:
                preflight.__file__ = str(support / "hermes-kanban-workflow-preflight.py")
                preflight.validate_installed_council_tools(server, os.getuid(), support)
            finally:
                preflight.__file__ = original

    def test_v2_profile_tool_and_contract_drift_fail(self):
        with tempfile.TemporaryDirectory() as td:
            root = pathlib.Path(td).resolve(); home, install = self.fixture(root, V2_CONTRACT)
            server, trust = self.installed_council_tools(root)
            path = home / "profiles/council-reviewer-v2/config.yaml"
            config = yaml.safe_load(path.read_text()); config["platform_toolsets"]["cli"].append("terminal")
            path.write_text(yaml.safe_dump(config))
            with self.assertRaisesRegex(ValueError, "dangerous runtime profile tools"):
                preflight.preflight(home, install, V2_CONTRACT, server, os.getuid(), trust)
        with tempfile.TemporaryDirectory() as td:
            root = pathlib.Path(td).resolve(); home, install = self.fixture(root, V2_CONTRACT)
            server, trust = self.installed_council_tools(root)
            path = home / "profiles/council-security-v2/config.yaml"
            data = yaml.safe_load(path.read_text()); data["mcp_servers"]["write"] = {"command":"sh"}
            path.write_text(yaml.safe_dump(data))
            with self.assertRaisesRegex(ValueError, "dangerous runtime profile tools"):
                preflight.preflight(home, install, V2_CONTRACT, server, os.getuid(), trust)
        with tempfile.TemporaryDirectory() as td:
            root = pathlib.Path(td).resolve(); support = root / "support"; support.mkdir(mode=0o700)
            fake = support / "server"; fake.write_text(SERVER.read_text().replace('"snapshot_search"', '"snapshot_find"', 1))
            fake.chmod(0o555)
            with self.assertRaisesRegex(ValueError, "differs from repository source"):
                preflight.council_tools_report(fake, os.getuid(), support)
        data = json.loads(V2_CONTRACT.read_text()); data["task_graph"]["edges"].pop()
        with tempfile.TemporaryDirectory() as td:
            path = pathlib.Path(td) / "contract.json"; path.write_text(json.dumps(data))
            with self.assertRaisesRegex(ValueError, "v2 task graph"): preflight.load_contract(path)
        data = json.loads(V2_CONTRACT.read_text()); data["tools"].append("kanban_create")
        with tempfile.TemporaryDirectory() as td:
            path = pathlib.Path(td) / "contract.json"; path.write_text(json.dumps(data))
            with self.assertRaisesRegex(ValueError, "invalid workflow contract"): preflight.load_contract(path)

    def test_v2_effective_probe_rejects_builtin_worker_tool(self):
        with tempfile.TemporaryDirectory() as td:
            root = pathlib.Path(td).resolve(); home, install = self.fixture(root, V2_CONTRACT)
            server, trust = self.installed_council_tools(root)
            path = install / "model_tools.py"
            extra = "[{'type':'function','function':{'name':'kanban_create','description':'bad','parameters':{}}}]+"
            path.write_text(path.read_text().replace("DEFINITIONS=", "DEFINITIONS=" + extra))
            with self.assertRaisesRegex(ValueError, "built-in worker tools exposed"):
                preflight.preflight(home, install, V2_CONTRACT, server, os.getuid(), trust)

    def test_council_tools_install_validator_rejects_mode_link_owner_and_parent_drift(self):
        for mutate, expected_uid, message in (
            (lambda server, support: server.chmod(0o755), os.getuid(), "missing or unsafe"),
            (lambda server, support: os.link(server, support / "hardlink"), os.getuid(), "missing or unsafe"),
            (lambda server, support: None, os.getuid() + 1, "unsafe parent"),
            (lambda server, support: support.chmod(0o777), os.getuid(), "unsafe parent"),
        ):
            with tempfile.TemporaryDirectory() as td:
                root = pathlib.Path(td).resolve(); server, support = self.installed_council_tools(root)
                mutate(server, support)
                with self.assertRaisesRegex(ValueError, message):
                    preflight.validate_installed_council_tools(server, expected_uid, support)

    def test_missing_profile_tool_model_and_default_drift_fail(self):
        with tempfile.TemporaryDirectory() as td:
            home, install = self.fixture(pathlib.Path(td));
            (home / "profiles/verifier").rename(home / "verifier-away")
            with self.assertRaisesRegex(ValueError, "profile missing"): preflight.preflight(home, install, CONTRACT)
        with tempfile.TemporaryDirectory() as td:
            home, install = self.fixture(pathlib.Path(td));
            path = install / "tools/kanban_tools_schemas.py"; path.write_text("TOOLS=[]\n")
            with self.assertRaisesRegex(ValueError, "tool missing"): preflight.preflight(home, install, CONTRACT)
        with tempfile.TemporaryDirectory() as td:
            home, install = self.fixture(pathlib.Path(td));
            path = install / "hermes_cli/models_catalog_static.py"; path.write_text("MODELS=[]\n")
            with self.assertRaisesRegex(ValueError, "model missing"): preflight.preflight(home, install, CONTRACT)
        with tempfile.TemporaryDirectory() as td:
            home, install = self.fixture(pathlib.Path(td));
            path = install / "hermes_cli/config_defaults.py"
            path.write_text(path.read_text().replace("'failure_limit': 2", "'failure_limit': 3"))
            with self.assertRaisesRegex(ValueError, "defaults changed"): preflight.preflight(home, install, CONTRACT)

    def test_runtime_rejects_missing_dispatch_lock_helper(self):
        with tempfile.TemporaryDirectory() as td:
            home, install = self.fixture(pathlib.Path(td))
            path = install / "hermes_cli/kanban_db_connect.py"
            path.write_text(path.read_text().replace("@contextmanager\ndef _dispatch_tick_lock(path): yield True",
                                                     "_dispatch_tick_lock = None"))
            with self.assertRaisesRegex(ValueError, "pinned Kanban stop helpers changed"):
                preflight.preflight(home, install, CONTRACT)

    def test_runtime_rejects_missing_termination_or_write_transaction_helper(self):
        for helper in ("_terminate_reclaimed_worker", "write_txn"):
            with self.subTest(helper=helper), tempfile.TemporaryDirectory() as td:
                home, install = self.fixture(pathlib.Path(td))
                path = install / "hermes_cli/kanban_db.py"
                source = path.read_text()
                if helper == "_terminate_reclaimed_worker":
                    source = source.replace(
                        "def _terminate_reclaimed_worker(pid,claim_lock,signal_fn=None): return {\"terminated\":True}",
                        "_terminate_reclaimed_worker = None")
                else:
                    source = source.replace("@contextmanager\ndef write_txn(conn): yield", "write_txn = None")
                path.write_text(source)
                with self.assertRaisesRegex(ValueError, "pinned Kanban stop helpers changed"):
                    preflight.preflight(home, install, CONTRACT)

    def test_contract_rejects_opus_and_budget_drift(self):
        data = json.loads(CONTRACT.read_text()); data["profiles"]["council-reviewer"]["model"] = "claude-opus-5"
        with tempfile.TemporaryDirectory() as td:
            path = pathlib.Path(td) / "contract.json"; path.write_text(json.dumps(data))
            with self.assertRaisesRegex(ValueError, "source, role, or model"): preflight.load_contract(path)
        data = json.loads(CONTRACT.read_text()); data["token_budget"] = 150001
        with tempfile.TemporaryDirectory() as td:
            path = pathlib.Path(td) / "contract.json"; path.write_text(json.dumps(data))
            with self.assertRaisesRegex(ValueError, "budget changed"): preflight.load_contract(path)
        data = json.loads(CONTRACT.read_text()); data["task_graph"]["edges"].pop()
        with tempfile.TemporaryDirectory() as td:
            path = pathlib.Path(td) / "contract.json"; path.write_text(json.dumps(data))
            with self.assertRaisesRegex(ValueError, "roles or edges changed"): preflight.load_contract(path)

    def test_cli_output_is_machine_readable(self):
        with tempfile.TemporaryDirectory() as td:
            home, install = self.fixture(pathlib.Path(td))
            command = [sys.executable, str(ROOT / "scripts/hermes-kanban-workflow-preflight.py"),
                       "--hermes-home", str(home), "--install-dir", str(install), "--contract", str(CONTRACT)]
            result = subprocess.run(command, capture_output=True, text=True, check=True)
            rejected = subprocess.run([*command, "--council-tools", str(SERVER)], capture_output=True, text=True)
        self.assertTrue(json.loads(result.stdout)["feasible"])
        self.assertNotEqual(rejected.returncode, 0)
        self.assertIn("unrecognized arguments", rejected.stderr)


if __name__ == "__main__": unittest.main()
