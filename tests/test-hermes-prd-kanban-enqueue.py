#!/usr/bin/env python3
import argparse, hashlib, importlib.util, io, json, pathlib, tempfile, unittest
from unittest import mock
ROOT = pathlib.Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("prd_enqueue", ROOT / "scripts/hermes-prd-kanban-enqueue.py")
enqueue = importlib.util.module_from_spec(spec); spec.loader.exec_module(enqueue)

class FakeCli:
    def __init__(self): self.boards = {}; self.tasks = {}; self.keys = {}; self.attachments = {}; self.commands = []; self.seq = 0; self.lose_create = self.lose_attach = False
    def __call__(self, command, env, json_output=False):
        self.commands.append(command)
        if command[1:4] == ["kanban", "boards", "list"]: return [{"slug": slug, "name": name} for slug, name in self.boards.items()]
        if command[1:4] == ["kanban", "boards", "create"]: self.boards[command[4]] = command[6]; return "created"
        action = command[command.index("--board") + 2]
        def flag(name, default=None): return command[command.index(name) + 1] if name in command else default
        if action == "list": return [task for task in self.tasks.values() if task["tenant"] == flag("--tenant")]
        if action == "create":
            key = flag("--idempotency-key")
            if key in self.keys: return self.tasks[self.keys[key]]
            self.seq += 1; task_id = f"t_{self.seq:08x}"
            task = {"id": task_id, "body": flag("--body"), "assignee": flag("--assignee"), "status": "blocked", "tenant": flag("--tenant"), "parents": [command[index + 1] for index, item in enumerate(command) if item == "--parent"], "skills": [command[index + 1] for index, item in enumerate(command) if item == "--skill"]}
            self.tasks[task_id] = task; self.keys[key] = task_id; self.attachments[task_id] = []
            if self.lose_create: self.lose_create = False; raise ValueError("lost create response")
            return task
        task_id = command[command.index(action) + 1]; task = self.tasks[task_id]
        if action == "attachments": return self.attachments[task_id]
        if action == "attach":
            source = pathlib.Path(command[command.index(action) + 2]); self.attachments[task_id].append({"filename": flag("--name"), "content_type": flag("--content-type"), "size": source.stat().st_size})
            if self.lose_attach: self.lose_attach = False; raise ValueError("lost attach response")
            return "attached"
        if action == "unblock": task["status"] = "ready" if not task["parents"] else "todo"; return "unblocked"
        if action == "show":
            events = ([{"kind":"created"},{"kind":"blocked","payload":{"reason":"initial_status","status":"blocked","actor":"operator"}}]
                      if not task.get("failure") else [{"kind":"created"},{"kind":"blocked","payload":{"reason":"failed"}}])
            runs = [] if not task.get("failure") else [{"id":1,"ended_at":1}]
            return {"task": task, "events": events, "runs": runs}
        raise AssertionError(command)

class EnqueueTest(unittest.TestCase):
    def fixture(self):
        temporary = tempfile.TemporaryDirectory(); self.addCleanup(temporary.cleanup); root = pathlib.Path(temporary.name)
        return argparse.Namespace(hermes_home=root / "home", hermes_bin=root / "hermes", engine="fixed"), FakeCli()
    def request(self, title="Write launch PRD", requirements="Ship a small canary"):
        core = {"title": title, "requester": "operator", "requirements": requirements}
        return {"operation_id": "prd-" + hashlib.sha256(enqueue.canonical(core)).hexdigest(), **core}
    def admit_raw(self, args, cli, raw):
        with mock.patch.object(enqueue, "run", side_effect=cli), mock.patch("sys.stdin", io.StringIO(raw)): return enqueue.enqueue(args)
    def admit(self, args, cli, request=None): return self.admit_raw(args, cli, enqueue.canonical(request or self.request()).decode())
    @staticmethod
    def actions(cli, action): return [command for command in cli.commands if "--board" in command and command[command.index("--board") + 2] == action]
    @staticmethod
    def role(cli, command): return json.loads(cli.tasks[command[command.index("unblock") + 1]]["body"])["role"]

    def test_exact_graph_profiles_release_replay_and_two_tenants(self):
        args, cli = self.fixture(); first = self.admit(args, cli); initial = list(cli.commands); replay = self.admit(args, cli); second = self.admit(args, cli, self.request("Billing PRD", "Bill safely"))
        self.assertEqual((first, len(cli.tasks), cli.boards), (replay, 12, {"prd-write": "PRD Write"}))
        ids = first["tasks"]; tasks = {role: cli.tasks[task_id] for role, task_id in ids.items()}; reviewers = enqueue.ROLES[2:5]
        self.assertEqual([tasks[role]["parents"] for role in enqueue.ROLES], [[], [], [ids["writer"]], [ids["writer"]], [ids["writer"]], [ids[role] for role in reviewers]])
        self.assertEqual([tasks[role]["assignee"] for role in enqueue.ROLES], [None, "prd-write-v1", "product-pm", "mvp", "occams-razor", "prd-write-v1"])
        self.assertEqual([self.role(cli, command) for command in initial if "--board" in command and command[command.index("--board") + 2] == "unblock"], ["synthesis", "product-pm", "mvp", "occams-razor", "writer"])
        creates = self.actions(cli, "create"); self.assertEqual(len(creates), 18); self.assertTrue(all(not {"--workspace", "--model", "--provider", "--body-file"} & set(command) and "--body" in command for command in creates))
        self.assertEqual(len({command[command.index("--idempotency-key") + 1] for command in creates}), 12)
        self.assertEqual(json.loads(tasks["root"]["body"])["intake"], self.request()); self.assertTrue(all("draft_digest" in json.loads(tasks[role]["body"]) for role in reviewers + ("synthesis",)))
        self.assertEqual((len(cli.attachments[ids["root"]]), len(cli.attachments[second["tasks"]["root"]])), (1, 1))

    def test_dynamic_intake_creates_only_one_writer_and_replays(self):
        args, cli = self.fixture(); args.engine = "dynamic"
        first = self.admit(args, cli); replay = self.admit(args, cli)
        self.assertEqual(first, replay)
        self.assertEqual(list(first["tasks"]), ["writer"])
        self.assertEqual(len(cli.tasks), 1)
        writer = cli.tasks[first["tasks"]["writer"]]
        body = json.loads(writer["body"])
        self.assertEqual((writer["assignee"], writer["parents"], writer["skills"], writer["status"]), ("prd-write-v1", [], ["prd-workflow"], "ready"))
        self.assertEqual((body["workflow"], body["stage"], body["round"], body["role"]), ("prd-write", "writer", 0, "writer"))
        self.assertEqual(body["reviewer_roles"], ["product-pm", "mvp", "occams-razor"])
        self.assertEqual(body["intake"], self.request())
        self.assertEqual(len(cli.attachments[first["tasks"]["writer"]]), 1)
        creates = self.actions(cli, "create")
        self.assertEqual(len(creates), 2)
        self.assertTrue(all("--skill" in command and command[command.index("--skill") + 1] == "prd-workflow" for command in creates))

    def test_engine_switch_cannot_mix_one_operation(self):
        args, cli = self.fixture(); fixed = self.admit(args, cli)
        args.engine = "dynamic"
        with self.assertRaisesRegex(ValueError, "operation already belongs"):
            self.admit(args, cli)
        self.assertEqual(len(cli.tasks), 6)
        self.assertNotIn("intake.json", [item["filename"] for item in cli.attachments[fixed["tasks"]["writer"]]])

        args, cli = self.fixture(); args.engine = "dynamic"; self.admit(args, cli)
        args.engine = "fixed"
        with self.assertRaisesRegex(ValueError, "operation already belongs"):
            self.admit(args, cli)
        self.assertEqual(len(cli.tasks), 1)

    def test_dynamic_lost_responses_are_adopted(self):
        args, cli = self.fixture(); args.engine = "dynamic"; cli.lose_create = True
        with self.assertRaisesRegex(ValueError, "lost create"):
            self.admit(args, cli)
        result = self.admit(args, cli)
        self.assertEqual(len(cli.tasks), 1)
        args, cli = self.fixture(); args.engine = "dynamic"; cli.lose_attach = True
        with self.assertRaisesRegex(ValueError, "lost attach"):
            self.admit(args, cli)
        result = self.admit(args, cli); writer = result["tasks"]["writer"]
        self.assertEqual((len(cli.tasks), len(cli.attachments[writer])), (1, 1))

    def test_lost_create_and_attachment_response_adoption(self):
        args, cli = self.fixture(); cli.lose_create = True
        with self.assertRaisesRegex(ValueError, "lost create"): self.admit(args, cli)
        result = self.admit(args, cli); self.assertEqual(len(cli.tasks), 6)
        args, cli = self.fixture(); cli.lose_attach = True
        with self.assertRaisesRegex(ValueError, "lost attach"): self.admit(args, cli)
        result = self.admit(args, cli); root = result["tasks"]["root"]
        self.assertEqual((len(cli.tasks), len(cli.attachments[root])), (6, 1))
        cli.tasks[result["tasks"]["writer"]].update(status="blocked", failure=True); before=len(self.actions(cli,"unblock")); self.admit(args,cli)
        self.assertEqual(len(self.actions(cli,"unblock")),before)
        cli.attachments[root][0]["size"] += 1
        with self.assertRaisesRegex(ValueError, "attachment mismatch"): self.admit(args, cli)

    def test_request_and_board_mismatch(self):
        request = self.request()
        for raw in (enqueue.canonical({**request, "operation_id": "prd-" + "0" * 64}).decode(), json.dumps(request)):
            args, cli = self.fixture()
            with self.assertRaises(ValueError): self.admit_raw(args, cli, raw)
            self.assertEqual(cli.commands, [])
        args, cli = self.fixture(); cli.boards[enqueue.BOARD] = "Other"
        with self.assertRaisesRegex(ValueError, "board name mismatch"): self.admit(args, cli)
        self.assertEqual(cli.tasks, {})

if __name__ == "__main__": unittest.main()
