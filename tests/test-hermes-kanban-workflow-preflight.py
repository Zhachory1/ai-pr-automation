#!/usr/bin/env python3
import importlib.util
import json
import os
import pathlib
import subprocess
import sys
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("kanban_preflight", ROOT / "scripts/hermes-kanban-workflow-preflight.py")
preflight = importlib.util.module_from_spec(spec); spec.loader.exec_module(preflight)
CONTRACT = ROOT / "agent-config/hermes/workflows/pr-risk-council-kanban.json"


class KanbanWorkflowPreflightTest(unittest.TestCase):
    def fixture(self, root):
        home, install = root / ".hermes", root / "install"
        contract = preflight.load_contract(CONTRACT)
        for value in contract["profiles"].values():
            profile = home / "profiles" / value["source"]; profile.mkdir(parents=True)
            (profile / "config.yaml").write_text("model:\n  provider: anthropic\n  default: claude-opus-5\n")
            (profile / "profile.yaml").write_text(f"description: Existing {value['role']} specialist\n")
        for relative in preflight.REQUIRED_MODULES:
            path = install / relative; path.parent.mkdir(parents=True, exist_ok=True); path.write_text("# fixture\n")
            init = path.parent / "__init__.py"; init.touch(exist_ok=True)
        (install / "hermes_cli/kanban_db_connect.py").write_text("class Conn:\n def close(self): pass\ndef connect(path): return Conn()\n")
        (install / "hermes_cli/kanban_db.py").write_text('''from types import SimpleNamespace
tasks={}; comments={}; seq=0
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
        venv = install / "venv/bin"; venv.mkdir(parents=True); (venv / "python").symlink_to(sys.executable)
        return home, install

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
        self.assertEqual(result["deferred_runtime_enforcement"],
                         ["deadline_seconds","max_active_workflows","profile_tool_policy","token_budget"])

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
            result = subprocess.run([sys.executable, str(ROOT / "scripts/hermes-kanban-workflow-preflight.py"),
                "--hermes-home", str(home), "--install-dir", str(install), "--contract", str(CONTRACT)],
                capture_output=True, text=True, check=True)
        self.assertTrue(json.loads(result.stdout)["feasible"])


if __name__ == "__main__": unittest.main()
