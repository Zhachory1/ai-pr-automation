#!/usr/bin/env python3
import argparse
import copy
import hashlib
import importlib.util
import io
import json
import os
import pathlib
import subprocess
import sys
import tempfile
import unittest
from types import SimpleNamespace
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
spec = importlib.util.spec_from_file_location("enqueue", ROOT / "scripts/hermes-pr-safety-kanban-enqueue.py")
enqueue = importlib.util.module_from_spec(spec); spec.loader.exec_module(enqueue)


class FakeCouncil:
    V2_SPECIALISTS={role:f"council-{role}-v2" for role in enqueue.TITLES}
    V2_SYNTHESIS="council-orchestrator-v2"
    V2_MODELS={**{role:"claude-haiku-4-5-20251001" for role in enqueue.TITLES},"synthesis":"claude-sonnet-5"}
    def __init__(self, root): self.root = root / "home" / "workflow-runs"
    def v2_context(self, _home, request, _contract):
        workflow_id = "pr-risk-council-" + hashlib.sha256(request["operation_id"].encode()).hexdigest()[:32]
        return {"workflow_id":workflow_id, "workflow_root":self.root,
                "root":self.root / workflow_id, "request":request}
    def profile_check_v2(self, _home): pass
    def prepare_v2_inputs(self, ctx):
        (ctx["root"] / "input").mkdir(mode=0o700,parents=True,exist_ok=True)
        binding=ctx["root"] / ".council-tools.json"
        if not binding.exists(): binding.write_text("{}")
    def verify_v2_inputs(self, _ctx): pass
    def private_dir(self,path): path.mkdir(mode=0o700,parents=True,exist_ok=True)
    def immutable_file(self,path,data):
        if path.exists():
            if path.read_bytes()!=data: raise ValueError("changed")
            return
        path.write_bytes(data); path.chmod(0o440)
    def v2_body(self, ctx, role, parent_ids=()):
        return json.dumps({"workflow_id":ctx["workflow_id"],"role":role,"parents":list(parent_ids)},
                          sort_keys=True,separators=(",",":"))


class FakeCli:
    def __init__(self):
        self.boards = set(); self.tasks = {}; self.keys = {}; self.seq = 0; self.commands = []
    def __call__(self, command, _env, json_output=False):
        self.commands.append(command)
        if "boards" in command and "list" in command:
            return [{"slug":board} for board in sorted(self.boards)] if json_output else ""
        if "boards" in command and "create" in command:
            self.boards.add(command[command.index("create") + 1]); return "created"
        board = command[command.index("--board") + 1]
        assert board in self.boards, command
        action = command[command.index("--board") + 2]
        def value(flag, default=None): return command[command.index(flag)+1] if flag in command else default
        if action == "create":
            key=(board, value("--idempotency-key"))
            if key in self.keys: return self.tasks[self.keys[key]]["task"]
            self.seq += 1; task_id=f"t{self.seq}"; title=command[command.index("create")+1]
            parents=[]
            for index,item in enumerate(command):
                if item == "--parent": parents.append(command[index+1])
            task={"id":task_id,"title":title,"body":pathlib.Path(value("--body-file")).read_text(),
                  "assignee":value("--assignee"),"status":"blocked","model_override":value("--model"),
                  "provider_override":value("--provider"),"workspace_kind":"dir",
                  "workspace_path":value("--workspace").removeprefix("dir:"),"max_runtime_seconds":900,
                  "max_retries":1,"completion_contract":"local-only","created_by":"operator",
                  "tenant":value("--tenant")}
            self.tasks[task_id]={"board":board,"task":task,"parents":parents,"children":[],"runs":[],"events":[
                {"kind":"created"}, {"kind":"blocked","payload":{
                    "reason":"initial_status","status":"blocked","actor":"operator"}}]}
            self.keys[key]=task_id
            for parent in parents: self.tasks[parent]["children"].append(task_id)
            return task
        task_id=command[command.index(action)+1] if action in {"show","attachments","unblock"} else None
        if action == "list":
            return [shown["task"] for shown in self.tasks.values()
                    if shown["board"] == board
                    and ("--tenant" not in command or shown["task"]["tenant"] == value("--tenant"))
                    and ("--archived" in command or shown["task"]["status"] != "archived")]
        assert self.tasks[task_id]["board"] == board, command
        if action == "show": return self.tasks[task_id]
        if action == "attachments": return []
        if action == "unblock":
            self.tasks[task_id]["task"]["status"] = "todo" if self.tasks[task_id]["parents"] else "ready"
            return "unblocked"
        raise AssertionError(command)


class EnqueueTest(unittest.TestCase):
    def fixture(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        root = pathlib.Path(directory.name).resolve()
        contract = root / "contract.json"; contract.write_text("{}")
        args = argparse.Namespace(hermes_home=root/"home", hermes_bin=root/"hermes",
                                  risk_council=root/"risk.py", contract=contract)
        return args, FakeCouncil(root), FakeCli()

    def admit(self, args, council, cli, request=None):
        request = request or {"operation_id":"op", "repo":"example/fixture", "pr":1}
        with mock.patch.object(enqueue, "load_module", return_value=council), \
             mock.patch.object(enqueue, "run", side_effect=cli), \
             mock.patch("sys.stdin", io.StringIO(json.dumps(request))):
            return enqueue.enqueue(args)

    def test_creates_verifies_and_releases_exact_graph(self):
        with tempfile.TemporaryDirectory() as td:
            root=pathlib.Path(td); contract=root/"contract.json"; contract.write_text("{}")
            fake=FakeCli(); council=FakeCouncil(root)
            args=argparse.Namespace(hermes_home=root/"home",hermes_bin=root/"hermes",
                                    risk_council=root/"risk.py",contract=contract)
            request={"operation_id":"op", "repo":"example/fixture", "pr":1}
            with mock.patch.object(enqueue,"load_module",return_value=council), \
                 mock.patch.object(enqueue,"run",side_effect=fake), \
                 mock.patch("sys.stdin",io.StringIO(json.dumps(request))):
                result=enqueue.enqueue(args)
            self.assertEqual(result["status"],"enqueued")
            self.assertEqual(set(result["task_ids"]),{"review","security","reliability","architecture","synthesis"})
            self.assertEqual(len(fake.tasks),5)
            synthesis=fake.tasks[result["task_ids"]["synthesis"]]
            self.assertEqual(set(synthesis["parents"]),{result["task_ids"][role] for role in enqueue.TITLES})
            self.assertEqual(synthesis["task"]["status"],"todo")
            self.assertTrue(all(fake.tasks[result["task_ids"][role]]["task"]["status"]=="ready"
                                for role in enqueue.TITLES))
            self.assertTrue(all(isinstance(command,list) for command in fake.commands))

            with mock.patch.object(enqueue,"load_module",return_value=council), \
                 mock.patch.object(enqueue,"run",side_effect=fake), \
                 mock.patch("sys.stdin",io.StringIO(json.dumps(request))):
                replay=enqueue.enqueue(args)
            self.assertEqual(replay["task_ids"],result["task_ids"])
            self.assertEqual(len(fake.tasks),5)

    def test_two_prs_share_board_with_disjoint_graphs_and_replay_without_extra_cards(self):
        args, council, fake = self.fixture()
        requests = [{"operation_id":"op", "repo":"example/fixture", "pr":1},
                    {"operation_id":"op-2", "repo":"example/fixture", "pr":2}]
        results = [self.admit(args, council, fake, request) for request in requests]
        self.assertEqual(fake.boards, {"pr-safety-council"})
        self.assertEqual(len(fake.tasks), 10)
        self.assertNotEqual(results[0]["workflow_id"], results[1]["workflow_id"])
        self.assertTrue(set(results[0]["task_ids"].values()).isdisjoint(results[1]["task_ids"].values()))
        self.assertEqual(sum("boards" in command and "create" in command for command in fake.commands), 1)
        for request, result in zip(requests, results):
            self.assertEqual(result["status"], "enqueued")
            self.assertEqual(result["board"], "pr-safety-council")
            self.assertEqual(set(result["task_ids"]), {*enqueue.TITLES, "synthesis"})
            workspace = args.hermes_home / "workflow-runs" / result["workflow_id"]
            marker = workspace / "kanban-board.txt"
            self.assertEqual(marker.read_bytes(), b"pr-safety-council\n")
            self.assertEqual(marker.stat().st_mode & 0o777, 0o440)
            for role, title in {**enqueue.TITLES, "synthesis":enqueue.SYNTHESIS_TITLE}.items():
                shown = fake.tasks[result["task_ids"][role]]
                self.assertEqual(shown["board"], result["board"])
                self.assertEqual(shown["task"]["tenant"], result["workflow_id"])
                self.assertEqual(shown["task"]["title"], f"{request['repo']}#{request['pr']}: {title}")
                self.assertEqual(shown["task"]["workspace_path"], str(workspace))
                self.assertEqual(json.loads(shown["task"]["body"])["workflow_id"], result["workflow_id"])
                self.assertEqual(shown["task"]["status"], "todo" if role == "synthesis" else "ready")
                self.assertCountEqual(shown["parents"], [result["task_ids"][parent] for parent in enqueue.TITLES]
                                      if role == "synthesis" else [])
                self.assertEqual(shown["children"], [] if role == "synthesis" else [result["task_ids"]["synthesis"]])
        for command in fake.commands:
            if "--board" in command and any(action in command for action in ("create", "list")):
                self.assertIn("--tenant", command)
                self.assertIn(command[command.index("--tenant") + 1], {result["workflow_id"] for result in results})

        admitted = copy.deepcopy(fake.tasks)
        fake.commands.clear()
        for request, result in zip(requests, results):
            self.assertEqual(self.admit(args, council, fake, request), result)
        self.assertEqual(fake.tasks, admitted)
        self.assertEqual(len(fake.tasks), 10)
        self.assertFalse(any(action in command for command in fake.commands for action in ("create", "unblock")))

    def test_foreign_parent_edge_fails_before_any_release(self):
        args, council, fake = self.fixture()
        first = self.admit(args, council, fake)
        second = self.admit(args, council, fake, {"operation_id":"op-2", "repo":"example/fixture", "pr":2})
        foreign_parent = second["task_ids"]["review"]
        synthesis = first["task_ids"]["synthesis"]
        fake.tasks[synthesis]["parents"].append(foreign_parent)
        fake.tasks[foreign_parent]["children"].append(synthesis)
        for shown in fake.tasks.values():
            shown["task"]["status"] = "blocked"
        staged = copy.deepcopy(fake.tasks)
        fake.commands.clear()

        with self.assertRaisesRegex(ValueError, "differs from fixed safety graph"):
            self.admit(args, council, fake)
        self.assertEqual(fake.tasks, staged)
        self.assertFalse(any(action in command for command in fake.commands for action in ("create", "unblock")))

    def test_legacy_existing_or_archived_board_is_skipped_without_writes(self):
        for state in ("board", "task-bodies", "input-only"):
            with self.subTest(state=state):
                args, council, fake = self.fixture()
                request = {"operation_id":"op", "repo":"example/fixture", "pr":1}
                ctx = council.v2_context(args.hermes_home, request, {})
                if state == "board":
                    fake.boards.add(ctx["workflow_id"])
                elif state == "task-bodies":
                    bodies = ctx["root"] / "task-bodies"
                    bodies.mkdir(parents=True)
                    (bodies / "review.json").write_text("legacy body")
                else:
                    (ctx["root"] / "input").mkdir(parents=True)
                    (ctx["root"] / ".council-tools.json").write_text("{}")
                fake.tasks["legacy"] = {"board":ctx["workflow_id"], "task":{
                    "id":"legacy", "title":enqueue.TITLES["review"], "status":"blocked", "tenant":None}}
                before = copy.deepcopy((fake.boards, fake.tasks, fake.keys, fake.seq))
                files = {path:path.read_bytes() if path.is_file() else None
                         for path in args.contract.parent.rglob("*")}
                expected = {"status":"legacy", "board":ctx["workflow_id"],
                            "workflow_id":ctx["workflow_id"], "task_ids":{}}

                self.assertEqual(self.admit(args, council, fake, request), expected)
                self.assertEqual(self.admit(args, council, fake, request), expected)
                self.assertEqual((fake.boards, fake.tasks, fake.keys, fake.seq), before)
                self.assertEqual({path:path.read_bytes() if path.is_file() else None
                                  for path in args.contract.parent.rglob("*")}, files)
                self.assertFalse((ctx["root"] / "kanban-board.txt").exists())
                self.assertEqual(fake.commands, [[str(args.hermes_bin), "kanban", "boards", "list", "--json"]] * 2)

    def test_changed_board_marker_is_rejected_without_cli_writes(self):
        args, council, fake = self.fixture()
        request = {"operation_id":"op", "repo":"example/fixture", "pr":1}
        ctx = council.v2_context(args.hermes_home, request, {})
        council.prepare_v2_inputs(ctx)
        marker = ctx["root"] / "kanban-board.txt"
        council.immutable_file(marker, b"other-board\n")

        with self.assertRaisesRegex(ValueError, "changed"):
            self.admit(args, council, fake, request)
        self.assertEqual(marker.read_bytes(), b"other-board\n")
        self.assertEqual(fake.boards, set())
        self.assertEqual(fake.tasks, {})
        self.assertEqual(fake.commands, [[str(args.hermes_bin), "kanban", "boards", "list", "--json"]])

    def test_replay_preserves_operational_block(self):
        with tempfile.TemporaryDirectory() as td:
            root=pathlib.Path(td); contract=root/"contract.json"; contract.write_text("{}")
            fake=FakeCli(); council=FakeCouncil(root)
            args=argparse.Namespace(hermes_home=root/"home",hermes_bin=root/"hermes",
                                    risk_council=root/"risk.py",contract=contract)
            first=self.admit(args, council, fake)
            review=fake.tasks[first["task_ids"]["review"]]
            review["task"]["status"]="blocked"
            review["runs"]=[{"id":1,"outcome":"failed"}]
            review["events"].append({"kind":"blocked"})
            self.admit(args, council, fake)
            self.assertEqual(review["task"]["status"],"blocked")

    def test_interrupted_third_create_retries_only_missing_tasks_before_release(self):
        args, council, fake = self.fixture()

        def interrupted(command, env, json_output=False):
            if "--idempotency-key" in command and fake.seq == 2:
                fake.commands.append(command)
                raise ValueError("interrupted third create")
            return fake(command, env, json_output)

        with self.assertRaisesRegex(ValueError, "interrupted third create"):
            self.admit(args, council, interrupted)
        existing = {value["task"]["title"]: task_id for task_id, value in fake.tasks.items()}
        prefix = "example/fixture#1: "
        self.assertEqual(set(existing), {prefix + enqueue.TITLES["review"], prefix + enqueue.TITLES["security"]})
        self.assertTrue(all(value["task"]["status"] == "blocked" for value in fake.tasks.values()))
        self.assertFalse(any("unblock" in command for command in fake.commands))

        fake.commands.clear()
        result = self.admit(args, council, fake)
        self.assertEqual(result["status"], "enqueued")
        self.assertEqual(len(fake.tasks), 5)
        for role in ("review", "security"):
            self.assertEqual(result["task_ids"][role], existing[prefix + enqueue.TITLES[role]])
        self.assertEqual([command[command.index("create") + 1] for command in fake.commands
                          if "create" in command],
                         [prefix + title for title in
                          (enqueue.TITLES["reliability"], enqueue.TITLES["architecture"], enqueue.SYNTHESIS_TITLE)])
        releases = [command[command.index("unblock") + 1] for command in fake.commands if "unblock" in command]
        self.assertCountEqual(releases, result["task_ids"].values())
        first_release = next(index for index, command in enumerate(fake.commands) if "unblock" in command)
        for action in ("show", "attachments"):
            self.assertEqual({command[command.index(action) + 1] for command in fake.commands[:first_release]
                              if action in command}, set(result["task_ids"].values()))
        for role, task_id in result["task_ids"].items():
            self.assertEqual(fake.tasks[task_id]["task"]["status"], "todo" if role == "synthesis" else "ready")

    def test_archived_five_card_replay_never_creates_or_unblocks(self):
        args, council, fake = self.fixture()
        first = self.admit(args, council, fake)
        for value in fake.tasks.values():
            value["task"]["status"] = "archived"
        archived = copy.deepcopy(fake.tasks)
        fake.commands.clear()

        self.assertEqual(self.admit(args, council, fake), first)
        self.assertEqual(fake.tasks, archived)
        self.assertFalse(any(action in command for command in fake.commands for action in ("create", "unblock")))

    def test_human_block_without_runs_is_not_released(self):
        for kind in ("status", "blocked"):
            with self.subTest(event_kind=kind):
                args, council, fake = self.fixture()
                first = self.admit(args, council, fake)
                review = fake.tasks[first["task_ids"]["review"]]
                review["task"]["status"] = "blocked"
                review["events"] = [{"kind":"created"}, {"kind":kind}]
                self.assertEqual(review["runs"], [])
                fake.commands.clear()

                self.assertEqual(self.admit(args, council, fake), first)
                self.assertEqual(review["task"]["status"], "blocked")
                self.assertFalse(any("unblock" in command for command in fake.commands))

    def test_duplicate_or_altered_staged_task_fails_before_any_release(self):
        for alteration in ("duplicate", "body"):
            with self.subTest(alteration=alteration):
                args, council, fake = self.fixture()
                first = self.admit(args, council, fake)
                for value in fake.tasks.values():
                    value["task"]["status"] = "blocked"
                synthesis = fake.tasks[first["task_ids"]["synthesis"]]
                if alteration == "duplicate":
                    duplicate = copy.deepcopy(synthesis)
                    duplicate["task"]["id"] = "duplicate"
                    fake.tasks["duplicate"] = duplicate
                    error = "unexpected or duplicate tasks"
                else:
                    synthesis["task"]["body"] = "altered synthesis body"
                    error = "differs from fixed safety graph"
                staged = copy.deepcopy(fake.tasks)
                fake.commands.clear()

                with self.assertRaisesRegex(ValueError, error):
                    self.admit(args, council, fake)
                self.assertEqual(fake.tasks, staged)
                self.assertFalse(any("unblock" in command for command in fake.commands))


@unittest.skipUnless(os.environ.get("HERMES_TEST_BIN"), "set HERMES_TEST_BIN to the real Hermes v0.21.5 CLI")
class RealCliEnqueueTest(unittest.TestCase):
    def test_real_cli_shared_board_admission_replay_and_archived_task_replay(self):
        council = enqueue.load_module(ROOT / "scripts/hermes-kanban-risk-council.py")
        hermes = pathlib.Path(os.environ["HERMES_TEST_BIN"]).expanduser().resolve(strict=True)
        with tempfile.TemporaryDirectory() as td:
            root = pathlib.Path(td).resolve()
            home = root / "home"; home.mkdir(mode=0o700)
            (home / "config.yaml").write_text("{}\n")
            env = {"HOME":str(root), "HERMES_HOME":str(home), "PATH":os.environ.get("PATH", ""),
                   "PYTHONUTF8":"1", "PYTHONDONTWRITEBYTECODE":"1", "HERMES_SAFE_MODE":"1",
                   "GIT_CONFIG_NOSYSTEM":"1", "GIT_CONFIG_GLOBAL":os.devnull}

            def execute(command, *, input=None, timeout=30):
                completed = subprocess.run([str(part) for part in command], input=input, env=env, cwd=root,
                                           capture_output=True, text=True, timeout=timeout)
                self.assertEqual(completed.returncode, 0, completed.stderr or completed.stdout)
                return completed.stdout

            self.assertRegex(execute([hermes, "--version"]), r"\bv?0\.21\.5\b")
            snapshot = root / "snapshots/op"; snapshot.mkdir(parents=True)
            git = ["git", "-C", snapshot, "-c", f"core.hooksPath={os.devnull}", "-c", "commit.gpgsign=false"]
            execute([*git, "init", "-q", "--template="])
            execute([*git, "config", "user.email", "test@example.com"])
            execute([*git, "config", "user.name", "Enqueue Test"])
            (snapshot / "app.txt").write_text("old\n")
            execute([*git, "add", "app.txt"])
            execute([*git, "commit", "-qm", "base"])
            base = execute([*git, "rev-parse", "HEAD"]).strip()
            (snapshot / "app.txt").write_text("new\n")
            execute([*git, "commit", "-qam", "head"])
            head = execute([*git, "rev-parse", "HEAD"]).strip()
            diff = execute([*git, "diff", "--no-ext-diff", base, head]).encode()
            policy = root / "policy.md"
            policy.write_text("Review only this synthetic fixture. No external effects.\n")
            request = {"operation_id":"cli-test", "repo":"example/fixture", "pr":1,
                       "head_sha":head, "base_sha":base, "diff_hash":hashlib.sha256(diff).hexdigest(),
                       "policy_version":"v1", "policy_digest":hashlib.sha256(policy.read_bytes()).hexdigest(),
                       "snapshot_path":str(snapshot), "policy_path":str(policy)}
            env.update(PR_SAFETY_SNAPSHOT_ROOT=str(snapshot.parent), PR_SAFETY_POLICY_PATH=str(policy),
                       PR_SAFETY_POLICY_VERSION=request["policy_version"], PR_SAFETY_POLICY_DIGEST=request["policy_digest"])
            profiles = {**council.V2_SPECIALISTS, "synthesis":council.V2_SYNTHESIS}
            for role, name in profiles.items():
                profile = home / "profiles" / name; profile.mkdir(parents=True)
                (profile / "config.yaml").write_text(json.dumps(council.v2_profile_config(council.V2_MODELS[role])))
            command = [sys.executable, ROOT / "scripts/hermes-pr-safety-kanban-enqueue.py",
                       "--hermes-home", home, "--hermes-bin", hermes,
                       "--risk-council", ROOT / "scripts/hermes-kanban-risk-council.py",
                       "--contract", ROOT / "agent-config/hermes/workflows/pr-risk-council-kanban-v2.json"]
            first = json.loads(execute(command, input=json.dumps(request), timeout=180))
            self.assertEqual(first["status"], "enqueued")
            self.assertEqual(set(first["task_ids"]), set(profiles))
            task_ids = first["task_ids"]
            self.assertEqual(len(set(task_ids.values())), 5)
            second_request = {**request, "operation_id":"cli-test-2", "pr":2}
            second = json.loads(execute(command, input=json.dumps(second_request), timeout=180))
            self.assertEqual(second["status"], "enqueued")
            self.assertEqual(first["board"], "pr-safety-council")
            self.assertEqual(second["board"], first["board"])
            self.assertNotEqual(first["workflow_id"], second["workflow_id"])
            self.assertTrue(set(task_ids.values()).isdisjoint(second["task_ids"].values()))
            all_ids = [*task_ids.values(), *second["task_ids"].values()]
            self.assertEqual(len(set(all_ids)), 10)
            kanban = [hermes, "kanban", "--board", first["board"]]
            listed = json.loads(execute([*kanban, "list", "--archived", "--json"]))
            self.assertCountEqual([task["id"] for task in listed], all_ids)
            for admission, result in ((request, first), (second_request, second)):
                self.assertEqual(set(result["task_ids"]), set(profiles))
                listed = json.loads(execute([*kanban, "list", "--tenant", result["workflow_id"], "--archived", "--json"]))
                self.assertCountEqual([task["id"] for task in listed], result["task_ids"].values())
                self.assertEqual({task["tenant"] for task in listed}, {result["workflow_id"]})
                workspace = home / "workflow-runs" / result["workflow_id"]
                self.assertEqual((workspace / "kanban-board.txt").read_bytes(), b"pr-safety-council\n")
                for role, task_id in result["task_ids"].items():
                    shown = json.loads(execute([*kanban, "show", task_id, "--json"]))
                    self.assertEqual(shown["task"]["id"], task_id)
                    title = enqueue.SYNTHESIS_TITLE if role == "synthesis" else enqueue.TITLES[role]
                    self.assertEqual(shown["task"]["title"], f"{admission['repo']}#{admission['pr']}: {title}")
                    self.assertEqual(shown["task"]["tenant"], result["workflow_id"])
                    self.assertEqual(shown["task"]["assignee"], profiles[role])
                    self.assertEqual(shown["task"]["model_override"],
                                     "claude-sonnet-5" if role == "synthesis" else "claude-haiku-4-5-20251001")
                    self.assertEqual(shown["task"]["provider_override"], "anthropic")
                    self.assertEqual(shown["task"]["status"], "todo" if role == "synthesis" else "ready")
                    self.assertEqual(pathlib.Path(shown["task"]["workspace_path"]).resolve(), workspace)
                    self.assertCountEqual(shown["parents"], [result["task_ids"][parent] for parent in council.V2_SPECIALISTS]
                                          if role == "synthesis" else [])
                    self.assertEqual(shown["children"], [] if role == "synthesis" else [result["task_ids"]["synthesis"]])
                    self.assertEqual(shown["runs"], [])

            for admission, result in ((request, first), (second_request, second)):
                replay = json.loads(execute(command, input=json.dumps(admission), timeout=180))
                self.assertEqual(replay, result)
            listed = json.loads(execute([*kanban, "list", "--archived", "--json"]))
            self.assertCountEqual([task["id"] for task in listed], all_ids)
            execute([*kanban, "archive", task_ids["review"]])
            archived = json.loads(execute([*kanban, "show", task_ids["review"], "--json"]))
            self.assertEqual(archived["task"]["status"], "archived")
            for admission, result in ((request, first), (second_request, second)):
                replay = json.loads(execute(command, input=json.dumps(admission), timeout=180))
                self.assertEqual(replay, result)
                listed = json.loads(execute([*kanban, "list", "--tenant", result["workflow_id"], "--archived", "--json"]))
                self.assertCountEqual([task["id"] for task in listed], result["task_ids"].values())
            listed = json.loads(execute([*kanban, "list", "--archived", "--json"]))
            self.assertCountEqual([task["id"] for task in listed], all_ids)
            archived = json.loads(execute([*kanban, "show", task_ids["review"], "--json"]))
            self.assertEqual(archived["task"]["status"], "archived")
            self.assertEqual(archived["runs"], [])


if __name__ == "__main__": unittest.main()
