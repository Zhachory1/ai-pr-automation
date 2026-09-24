#!/usr/bin/env python3
import json
import os
import pathlib
import subprocess
import sys
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
SERVER = ROOT / "bin/hermes-council-tools"
TOOL_NAMES = [
    "snapshot_read", "snapshot_search", "kanban_show", "kanban_comment",
    "kanban_heartbeat", "kanban_complete", "kanban_block",
]
CONFIG_ENV = {
    "COUNCIL_TASK_ID":"${HERMES_KANBAN_TASK}",
    "COUNCIL_RUN_ID":"${HERMES_KANBAN_RUN_ID}",
    "COUNCIL_CLAIM_LOCK":"${HERMES_KANBAN_CLAIM_LOCK}",
    "COUNCIL_BOARD":"${HERMES_KANBAN_BOARD}",
    "COUNCIL_DB":"${HERMES_KANBAN_DB}",
    "COUNCIL_WORKSPACE":"${HERMES_KANBAN_WORKSPACE}",
    "COUNCIL_SNAPSHOT_ROOT":"${PR_SAFETY_SNAPSHOT_ROOT}",
    "COUNCIL_WORKFLOW_ROOT":"${PR_SAFETY_WORKFLOW_ROOT}",
    "COUNCIL_PROFILE":"${HERMES_PROFILE}",
    "COUNCIL_TOOLS_PYTHON":"${HERMES_COUNCIL_TOOLS_PYTHON}",
}


class CouncilToolsTest(unittest.TestCase):
    def fixture(self, root):
        root = root.resolve()
        workspace = root / "workspace"; workspace.mkdir()
        snapshot_base = root / "snapshots"; snapshot = snapshot_base / "immutable"; snapshot.mkdir(parents=True)
        workflow_base = root / "workflows"; inputs = workflow_base / "run/input"; inputs.mkdir(parents=True)
        (snapshot / "src").mkdir(); (snapshot / "src/app.py").write_text("first\nneedle here\nlast\n")
        (snapshot / "README.md").write_text("needle docs\n")
        (inputs / "identity.json").write_text('{"head":"abc"}\n')
        binding = workspace / ".council-tools.json"
        binding.write_text(json.dumps({"schema_version":1,"snapshot_root":str(snapshot),"input_root":str(inputs)}))
        binding.chmod(0o440)
        db = root / "kanban.db"; db.touch()
        env = {**os.environ,"COUNCIL_TASK_ID":"task-own","COUNCIL_RUN_ID":"41",
               "COUNCIL_CLAIM_LOCK":"claim-own","COUNCIL_BOARD":"board-own","COUNCIL_DB":str(db),
               "COUNCIL_WORKSPACE":str(workspace),"COUNCIL_SNAPSHOT_ROOT":str(snapshot_base),
               "COUNCIL_WORKFLOW_ROOT":str(workflow_base),"COUNCIL_PROFILE":"council-reviewer-v2",
               "COUNCIL_TOOLS_PYTHON":sys.executable,"PYTHONDONTWRITEBYTECODE":"1"}
        return env, workspace, snapshot, inputs

    def fake_pinned(self, root):
        package = root / "fake/tools"; package.mkdir(parents=True)
        (package / "__init__.py").write_text("")
        (package / "kanban_tools.py").write_text('''import json,os

def called(name,args):
 keys=[key for key in os.environ if key.startswith("HERMES_KANBAN_") or key in {"HERMES_PROFILE","HERMES_SESSION_ID","HERMES_DELEGATED_CHILD_CONTEXT"}]
 with open(os.environ["HANDLER_LOG"],"a") as out: out.write(json.dumps([name,args,{key:os.environ[key] for key in sorted(keys)}],sort_keys=True)+"\\n")
 if name == "_handle_block" and args.get("reason") == "handler-error": return json.dumps({"error":"authoritative rejection"})
 return json.dumps({"ok":True,"handler":name},sort_keys=True)

def _handle_show(args): return called("_handle_show",args)
def _handle_comment(args): return called("_handle_comment",args)
def _handle_heartbeat(args): return called("_handle_heartbeat",args)
def _handle_complete(args): return called("_handle_complete",args)
def _handle_block(args): return called("_handle_block",args)
''')
        hermes = root / "fake/hermes_cli"; hermes.mkdir(); (hermes / "__init__.py").write_text("")
        (hermes / "kanban_db.py").write_text('''import os
from pathlib import Path
def _normalize_board_slug(value): return value if value and value == value.strip().lower() else None
def kanban_db_path(board=None): return Path(os.environ["HERMES_KANBAN_DB"])
''')
        log = root / "handlers.jsonl"; db = root / "kanban.db"; db.touch()
        workspace = root / "workspace"; workspace.mkdir()
        snapshots = root / "snapshots"; snapshots.mkdir()
        workflows = root / "workflows"; workflows.mkdir()
        env = {**os.environ,"PYTHONPATH":str(root / "fake"),"HANDLER_LOG":str(log),
               "COUNCIL_TASK_ID":"task-own","COUNCIL_RUN_ID":"41",
               "COUNCIL_CLAIM_LOCK":"claim-own","COUNCIL_BOARD":"board-own","COUNCIL_DB":str(db),
               "COUNCIL_WORKSPACE":str(workspace),"COUNCIL_SNAPSHOT_ROOT":str(snapshots),
               "COUNCIL_WORKFLOW_ROOT":str(workflows),"COUNCIL_PROFILE":"council-reviewer-v2",
               "COUNCIL_TOOLS_PYTHON":sys.executable,"PYTHONDONTWRITEBYTECODE":"1"}
        env.pop("HERMES_SESSION_ID", None)
        return env, log

    def request_raw(self, env, payload):
        result = subprocess.run([str(SERVER)], input=payload, capture_output=True, text=True,
                                env=env, check=True, timeout=10)
        return [json.loads(line) for line in result.stdout.splitlines()]

    def request(self, env, *messages):
        return self.request_raw(env, "".join(json.dumps(message) + "\n" for message in messages))

    def call(self, env, name, arguments):
        return self.request(env, {"jsonrpc":"2.0","id":1,"method":"tools/call",
                                  "params":{"name":name,"arguments":arguments}})[0]["result"]

    def safe_mcp_env(self, parent):
        install = pathlib.Path.home() / ".hermes/hermes-agent"
        if (install / "tools/mcp_tool_config.py").is_file():
            script = '''import json,sys
from tools.mcp_tool_config import _build_safe_env,_interpolate_env_vars
print(json.dumps(_build_safe_env(_interpolate_env_vars(json.loads(sys.argv[1]))),sort_keys=True))
'''
            completed = subprocess.run([sys.executable, "-c", script, json.dumps(CONFIG_ENV)],
                                       capture_output=True, text=True, check=True,
                                       env={**parent,"PYTHONPATH":str(install)}, timeout=10)
            return json.loads(completed.stdout)
        safe_keys = {"PATH", "HOME", "USER", "LANG", "LC_ALL", "TERM", "SHELL", "TMPDIR"}
        safe = {key:value for key, value in parent.items() if key in safe_keys or key.startswith("XDG_")}
        for key in ("HERMES_KANBAN_DB", "HERMES_KANBAN_BOARD"):
            if key in parent: safe[key] = parent[key]
        safe.update({alias:parent[source[2:-1]] for alias, source in CONFIG_ENV.items()})
        for key in ("HERMES_KANBAN_TASK", "HERMES_KANBAN_RUN_ID", "HERMES_KANBAN_CLAIM_LOCK"):
            safe.pop(key, None)
        safe["HERMES_DELEGATED_CHILD_CONTEXT"] = "1"
        return safe

    def test_pinned_safe_env_aliases_survive_without_session_transport(self):
        with tempfile.TemporaryDirectory() as td:
            root = pathlib.Path(td).resolve(); direct, log = self.fake_pinned(root)
            parent = {key:value for key, value in direct.items() if not key.startswith("COUNCIL_")}
            parent.update({
                "HERMES_KANBAN_TASK":"task-own","HERMES_KANBAN_RUN_ID":"41",
                "HERMES_KANBAN_CLAIM_LOCK":"claim-own","HERMES_KANBAN_BOARD":"board-own",
                "HERMES_KANBAN_DB":direct["COUNCIL_DB"],
                "HERMES_KANBAN_WORKSPACE":direct["COUNCIL_WORKSPACE"],
                "PR_SAFETY_SNAPSHOT_ROOT":direct["COUNCIL_SNAPSHOT_ROOT"],
                "PR_SAFETY_WORKFLOW_ROOT":direct["COUNCIL_WORKFLOW_ROOT"],
                "HERMES_PROFILE":"council-reviewer-v2",
                "HERMES_COUNCIL_TOOLS_PYTHON":sys.executable,
                "HERMES_KANBAN_WORKSPACES_ROOT":"/must-not-reach-handler",
            })
            safe = self.safe_mcp_env(parent)
            for key in ("HERMES_KANBAN_TASK", "HERMES_KANBAN_RUN_ID", "HERMES_KANBAN_CLAIM_LOCK",
                        "HERMES_KANBAN_WORKSPACE", "HERMES_PROFILE", "HERMES_SESSION_ID",
                        "HERMES_KANBAN_WORKSPACES_ROOT"):
                self.assertNotIn(key, safe)
            self.assertEqual({key:safe[key] for key in CONFIG_ENV}, {
                alias:parent[source[2:-1]] for alias, source in CONFIG_ENV.items()})
            self.assertEqual(safe["HERMES_DELEGATED_CHILD_CONTEXT"], "1")
            safe.update({"PYTHONPATH":direct["PYTHONPATH"],"HANDLER_LOG":str(log),
                         "PYTHONDONTWRITEBYTECODE":"1"})
            listed, called = self.request(safe,
                {"jsonrpc":"2.0","id":1,"method":"tools/list","params":{}},
                {"jsonrpc":"2.0","id":2,"method":"tools/call",
                 "params":{"name":"kanban_show","arguments":{}}})
            self.assertEqual([tool["name"] for tool in listed["result"]["tools"]], TOOL_NAMES)
            self.assertNotIn("isError", called["result"])
            handler_env = json.loads(log.read_text())[2]
        self.assertEqual(handler_env, {
            "HERMES_KANBAN_BOARD":"board-own","HERMES_KANBAN_CLAIM_LOCK":"claim-own",
            "HERMES_KANBAN_DB":parent["HERMES_KANBAN_DB"],"HERMES_KANBAN_RUN_ID":"41",
            "HERMES_KANBAN_TASK":"task-own","HERMES_PROFILE":"council-reviewer-v2"})

    def test_protocol_exposes_exact_bounded_schemas_and_snapshot_tools(self):
        with tempfile.TemporaryDirectory() as td:
            env, _, snapshot, inputs = self.fixture(pathlib.Path(td))
            before = {path:path.read_bytes() for tree in (snapshot, inputs) for path in tree.rglob("*") if path.is_file()}
            listed = self.request(env,
                {"jsonrpc":"2.0","id":1,"method":"initialize","params":{}},
                {"jsonrpc":"2.0","id":2,"method":"tools/list","params":{}})
            self.assertEqual(listed[0]["result"]["serverInfo"]["name"], "hermes-council-tools")
            tools = listed[1]["result"]["tools"]
            self.assertEqual([tool["name"] for tool in tools], TOOL_NAMES)
            self.assertTrue(all(tool["inputSchema"]["additionalProperties"] is False for tool in tools))
            self.assertEqual(tools[0]["inputSchema"]["properties"]["path"]["maxLength"], 4096)
            self.assertEqual(tools[1]["inputSchema"]["properties"]["query"]["maxLength"], 256)
            self.assertEqual(tools[1]["inputSchema"]["properties"]["max_results"]["maximum"], 100)
            read = self.call(env, "snapshot_read", {"path":"input/identity.json"})
            self.assertEqual(read["content"][0]["text"], '{"head":"abc"}\n')
            searched = self.call(env, "snapshot_search", {"path":"snapshot","query":"needle","max_results":2})
            body = json.loads(searched["content"][0]["text"])
            self.assertEqual(sorted((item["path"], item["line"]) for item in body["matches"]),
                             [("snapshot/README.md", 1), ("snapshot/src/app.py", 2)])
            self.assertFalse(body["truncated"])
            self.assertEqual({path:path.read_bytes() for path in before}, before)

    def test_kanban_wrappers_forward_only_own_task_and_exact_handlers(self):
        with tempfile.TemporaryDirectory() as td:
            env, log = self.fake_pinned(pathlib.Path(td))
            foreign_db = pathlib.Path(td) / "foreign.db"; foreign_db.touch()
            env.update({"HERMES_KANBAN_TASK":"foreign-task","HERMES_KANBAN_RUN_ID":"99",
                        "HERMES_KANBAN_CLAIM_LOCK":"foreign-lock","HERMES_KANBAN_BOARD":"foreign-board",
                        "HERMES_KANBAN_DB":str(foreign_db),"HERMES_KANBAN_WORKSPACES_ROOT":"/foreign",
                        "HERMES_PROFILE":"foreign-profile","HERMES_SESSION_ID":"ambient-untrusted",
                        "HERMES_DELEGATED_CHILD_CONTEXT":"1"})
            calls = (
                ("kanban_show", {}),
                ("kanban_comment", {"body":"finding"}),
                ("kanban_heartbeat", {"note":"working"}),
                ("kanban_complete", {"summary":"done","metadata":{"role":"review"}}),
                ("kanban_block", {"reason":"need input","kind":"needs_input"}),
            )
            for name, arguments in calls:
                self.assertNotIn("isError", self.call(env, name, arguments))
            observed = [json.loads(line) for line in log.read_text().splitlines()]
        self.assertEqual([item[0] for item in observed], [
            "_handle_show", "_handle_comment", "_handle_heartbeat", "_handle_complete", "_handle_block"])
        expected_env = {"HERMES_KANBAN_TASK":"task-own","HERMES_KANBAN_RUN_ID":"41",
                        "HERMES_KANBAN_CLAIM_LOCK":"claim-own","HERMES_KANBAN_BOARD":"board-own",
                        "HERMES_KANBAN_DB":observed[0][2]["HERMES_KANBAN_DB"],
                        "HERMES_PROFILE":"council-reviewer-v2"}
        for (_, forwarded, handler_env), (_, supplied) in zip(observed, calls):
            self.assertEqual(forwarded, {"task_id":"task-own","board":"board-own",**supplied})
            self.assertNotIn("run_id", forwarded); self.assertNotIn("claim_lock", forwarded)
            self.assertEqual(handler_env, expected_env)

    def test_kanban_wrappers_require_all_aliases_and_reject_model_routing(self):
        with tempfile.TemporaryDirectory() as td:
            env, log = self.fake_pinned(pathlib.Path(td))
            for missing in CONFIG_ENV:
                incomplete = dict(env); incomplete.pop(missing)
                completed = subprocess.run([str(SERVER)], input="", capture_output=True, text=True,
                                           env=incomplete, timeout=10)
                self.assertNotEqual(completed.returncode, 0); self.assertIn(missing, completed.stderr)
            for forbidden in ("task_id", "board", "run_id", "claim_lock"):
                result = self.call(env, "kanban_comment", {"body":"x", forbidden:"foreign"})
                self.assertTrue(result["isError"], forbidden)
            result = self.call(env, "kanban_complete", {
                "summary":"done","metadata":{"artifacts":["/tmp/forbidden"]}})
            self.assertTrue(result["isError"])
            self.assertFalse(log.exists())

    def test_pinned_handler_rejection_is_mcp_tool_error(self):
        with tempfile.TemporaryDirectory() as td:
            env, _ = self.fake_pinned(pathlib.Path(td))
            result = self.call(env, "kanban_block", {"reason":"handler-error","kind":"transient"})
        self.assertTrue(result["isError"])
        self.assertEqual(json.loads(result["content"][0]["text"]), {"error":"authoritative rejection"})

    def test_binding_and_path_confinement_fail_closed(self):
        with tempfile.TemporaryDirectory() as td:
            env, workspace, snapshot, _ = self.fixture(pathlib.Path(td))
            outside = pathlib.Path(td) / "outside.txt"; outside.write_text("secret\n")
            (snapshot / "link").symlink_to(outside)
            os.mkfifo(snapshot / "pipe")
            hardlink = snapshot / "hardlink"; os.link(snapshot / "README.md", hardlink)
            for path in ("/etc/passwd", "snapshot/../outside.txt", "snapshot/link", "snapshot/pipe",
                         "snapshot/hardlink", "snapshot/" + "x" * 4096):
                self.assertTrue(self.call(env, "snapshot_read", {"path":path})["isError"], path)
            binding = workspace / ".council-tools.json"; binding.chmod(0o640)
            self.assertTrue(self.call(env, "snapshot_read", {"path":"snapshot/README.md"})["isError"])

    def test_search_non_utf8_fails_closed_and_truncation_requires_extra_match(self):
        with tempfile.TemporaryDirectory() as td:
            env, _, snapshot, _ = self.fixture(pathlib.Path(td))
            (snapshot / "binary.dat").write_bytes(b"needle\xff")
            self.assertTrue(self.call(env, "snapshot_search", {"path":"snapshot","query":"needle"})["isError"])
            (snapshot / "binary.dat").unlink()
            matches = snapshot / "matches"; matches.mkdir()
            (matches / "two.txt").write_text("needle\nneedle\n")
            exact = json.loads(self.call(env, "snapshot_search", {
                "path":"snapshot/matches","query":"needle","max_results":2})["content"][0]["text"])
            self.assertEqual(len(exact["matches"]), 2); self.assertFalse(exact["truncated"])
            (matches / "three.txt").write_text("needle\n")
            extra = json.loads(self.call(env, "snapshot_search", {
                "path":"snapshot/matches","query":"needle","max_results":2})["content"][0]["text"])
            self.assertEqual(len(extra["matches"]), 2); self.assertTrue(extra["truncated"])

    def test_search_entry_and_depth_limits_are_enforced_iteratively(self):
        with tempfile.TemporaryDirectory() as td:
            env, _, snapshot, _ = self.fixture(pathlib.Path(td))
            wide = snapshot / "wide"; wide.mkdir()
            for number in range(4_097):
                (wide / f"d{number}").mkdir()
            self.assertTrue(self.call(env, "snapshot_search", {"path":"snapshot/wide","query":"none"})["isError"])
        with tempfile.TemporaryDirectory() as td:
            env, _, snapshot, _ = self.fixture(pathlib.Path(td))
            current = snapshot / "deep"; current.mkdir()
            for number in range(33):
                current /= f"d{number}"; current.mkdir()
            self.assertTrue(self.call(env, "snapshot_search", {"path":"snapshot/deep","query":"none"})["isError"])
            deepest = "snapshot/deep/" + "/".join(f"d{number}" for number in range(33))
            self.assertTrue(self.call(env, "snapshot_search", {"path":deepest,"query":"none"})["isError"])

    def test_configured_and_bound_symlink_roots_fail_closed(self):
        with tempfile.TemporaryDirectory() as td:
            root = pathlib.Path(td).resolve(); env, workspace, snapshot, inputs = self.fixture(root)
            alias = root / "snapshot-alias"; alias.symlink_to(snapshot.parent, target_is_directory=True)
            env["COUNCIL_SNAPSHOT_ROOT"] = str(alias)
            binding = workspace / ".council-tools.json"; binding.chmod(0o640)
            binding.write_text(json.dumps({"schema_version":1,"snapshot_root":str(alias / snapshot.name),
                                           "input_root":str(inputs)})); binding.chmod(0o440)
            completed = subprocess.run([str(SERVER)], input="", capture_output=True, text=True, env=env, timeout=10)
            self.assertNotEqual(completed.returncode, 0); self.assertIn("COUNCIL_SNAPSHOT_ROOT", completed.stderr)
        with tempfile.TemporaryDirectory() as td:
            root = pathlib.Path(td).resolve(); env, workspace, snapshot, inputs = self.fixture(root)
            linked = snapshot.parent / "linked"; linked.symlink_to(snapshot, target_is_directory=True)
            binding = workspace / ".council-tools.json"; binding.chmod(0o640)
            binding.write_text(json.dumps({"schema_version":1,"snapshot_root":str(linked),
                                           "input_root":str(inputs)})); binding.chmod(0o440)
            self.assertTrue(self.call(env, "snapshot_read", {"path":"snapshot/README.md"})["isError"])

    def test_request_line_and_json_rpc_errors_are_bounded(self):
        with tempfile.TemporaryDirectory() as td:
            env, _, _, _ = self.fixture(pathlib.Path(td))
            messages = self.request_raw(env, " " * 262_145 + "\n" + "{\n" + "[]\n" + json.dumps({
                "jsonrpc":"2.0","id":2,"method":"tools/call",
                "params":{"name":"snapshot_read","arguments":[]}}) + "\n" +
                json.dumps({"jsonrpc":"2.0","id":3,"method":"unknown","params":{}}) + "\n")
            self.assertEqual([message["error"]["code"] for message in messages],
                             [-32700, -32700, -32600, -32602, -32601])
            self.assertEqual(messages[0]["error"]["message"], "request line too long")

    def test_root_file_and_search_limits_are_enforced(self):
        with tempfile.TemporaryDirectory() as td:
            root = pathlib.Path(td); env, workspace, snapshot, inputs = self.fixture(root)
            (snapshot / "large.txt").write_bytes(b"x" * (1_048_576 + 1))
            self.assertTrue(self.call(env, "snapshot_read", {"path":"snapshot/large.txt"})["isError"])
            self.assertTrue(self.call(env, "snapshot_search", {"path":"snapshot","query":"x"})["isError"])
            self.assertTrue(self.call(env, "snapshot_search", {"path":"snapshot","query":"x" * 257})["isError"])
            binding = workspace / ".council-tools.json"; binding.chmod(0o640)
            binding.write_text(json.dumps({"schema_version":1,"snapshot_root":str(root),"input_root":str(inputs)}))
            binding.chmod(0o440)
            self.assertTrue(self.call(env, "snapshot_read", {"path":"snapshot/outside.txt"})["isError"])


if __name__ == "__main__": unittest.main()
