#!/usr/bin/env python3
import argparse, importlib.util, json, pathlib, unittest
from unittest import mock
ROOT = pathlib.Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("prd_advance", ROOT / "scripts/hermes-prd-kanban-advance.py")
advance = importlib.util.module_from_spec(spec); spec.loader.exec_module(advance)

class FakeCli:
    def __init__(self, operation):
        self.operation, self.tasks, self.attachments, self.keys, self.commands = operation, {}, {}, {}, []
        self.seq = 0; self.crash_role = None; self.crashed = False
    def add(self, role, round_=0, status="done", result=None, parents=(), body=None, assignee=None):
        self.seq += 1; task_id = f"t_{self.seq:08x}"
        body = body or {"operation_id":self.operation, "round":round_, "role":role}
        events = [{"kind":"created"}, {"kind":"blocked", "payload":{"reason":"initial_status", "status":"blocked", "actor":"operator"}}]
        if parents: events.append({"kind":"linked", "payload":{"parents":list(parents)}})
        self.tasks[task_id] = {"id":task_id, "title":role, "body":advance.canonical(body), "status":status, "result":result, "assignee":assignee, "tenant":self.operation, "_parents":list(parents), "events":events, "runs":[]}
        self.attachments[task_id] = []; return task_id
    def file(self, task_id, name, size=8, content_type="text/markdown", attachment_id=2):
        self.attachments[task_id].append({"id":attachment_id, "filename":name, "content_type":content_type, "size":size})
    def public(self, task): return {key:value for key, value in task.items() if key not in {"_parents", "events", "runs"}}
    def __call__(self, command, env, json_output=False):
        self.commands.append(command); action = command[command.index("--board") + 2]
        def flag(name, default=None): return command[command.index(name) + 1] if name in command else default
        if action == "list": return [self.public(task) for task in self.tasks.values()]
        if action == "create":
            key = flag("--idempotency-key")
            if key in self.keys: return self.public(self.tasks[self.keys[key]])
            body = json.loads(flag("--body")); parents = [command[index + 1] for index, item in enumerate(command) if item == "--parent"]
            task_id = self.add(body["role"], body["round"], "blocked", parents=parents, body=body, assignee=flag("--assignee")); self.keys[key] = task_id
            if self.crash_role == body["role"] and not self.crashed: self.crashed = True; raise RuntimeError("crash after create")
            return self.public(self.tasks[task_id])
        task_id = command[command.index(action) + 1]; task = self.tasks[task_id]
        if action == "show": return {"task":self.public(task), "parents":task["_parents"], "events":task["events"], "runs":task["runs"]}
        if action == "attachments": return self.attachments[task_id]
        if action == "assign": task["assignee"] = None
        elif action == "unblock": task["status"] = "ready" if all(self.tasks[parent]["status"] == "done" for parent in task["_parents"]) else "todo"
        elif action == "request-review": task.update(status="review", assignee=None, summary=flag("--summary"))
        else: raise AssertionError(command)
        return "ok"

class AdvanceTest(unittest.TestCase):
    digest = "d" * 64
    def round_zero_bodies(self, operation):
        intake = {"operation_id":operation, "title":"Title", "requester":"owner", "requirements":"Need PRD"}; full = {"operation_id":operation, "round":0, "intake":intake}
        return {
            "root":{**full, "role":"root", "output":"remain blocked and unassigned; intake.json attachment is source of record"},
            "writer":{**full, "role":"writer", "output":"attach draft PRD bytes and raw-byte SHA-256 digest"},
            **{role:{"operation_id":operation, "round":0, "role":role, "draft_digest":"writer result attachment raw-byte SHA-256", "output":"attach review bound to draft digest"} for role in ("product-pm", "mvp", "occams-razor")},
            "synthesis":{"operation_id":operation, "round":0, "role":"synthesis", "draft_digest":"writer result attachment raw-byte SHA-256", "review_roles":["product-pm", "mvp", "occams-razor"], "output":"attach verdict bound to digest and reviews; do not edit PRD bytes"},
        }
    def fixture(self, verdict="approve", blockers=None, round_=0):
        operation = "prd-" + "a" * 64; cli = FakeCli(operation); bodies = self.round_zero_bodies(operation)
        root = cli.add("root", status="blocked", body=bodies["root"]); writer = cli.add("writer", body=bodies["writer"]); cli.file(writer, "generated-prd.txt", content_type="text/plain")
        reviewers = {role:cli.add(role, parents=[writer], body=bodies[role]) for role in ("product-pm", "mvp", "occams-razor")}
        result = advance.canonical({"verdict":verdict if round_ == 0 else "revise", "reviewed_digest":self.digest, "blockers":blockers or []})
        synthesis = cli.add("synthesis", result=result, parents=list(reviewers.values()), body=bodies["synthesis"]); prior = synthesis
        for current in range(1, round_ + 1):
            ledger = blockers or []; roles = advance.selected(ledger)
            common = {"operation_id":operation, "round":current, "prior_digest":self.digest, "blockers":ledger}
            writer = cli.add("writer", current, parents=[prior], body={**common, "role":"writer"}); cli.file(writer, "revision.md")
            reviewer_ids = {role:cli.add(role, current, parents=[writer], body={**common, "role":role}) for role in roles}
            result = advance.canonical({"verdict":verdict if current == round_ else "revise", "reviewed_digest":self.digest, "blockers":ledger})
            synthesis = cli.add("synthesis", current, result=result, parents=list(reviewer_ids.values()), body={**common, "role":"synthesis", "review_roles":roles}); prior = synthesis
        args = argparse.Namespace(hermes_home=pathlib.Path("/tmp/hermes/home"), hermes_bin=pathlib.Path("/tmp/hermes/bin"), operation_id=operation)
        return args, cli, root, writer, synthesis
    def call(self, args, cli):
        with mock.patch.object(advance, "run", side_effect=cli): return advance.advance(args)
    def native_metadata(self, operation, **changes):
        value = {"operation_id":operation, "round":0, "role":"synthesis", "verdict":"needs_revision",
                 "draft_digest":"PENDING", "draft_attachment_id":2, "draft_attachment_filename":"generated-prd.txt",
                 "draft_attachment_size_bytes":8, "must_fix_count":1, "blocking_issues":["scope: reduce scope"],
                 "reviewer_verdicts":{"product-pm":"needs_revision", "mvp":"block", "occams-razor":"conditional_pass"}}
        value.update(changes); return value

    def test_approve_reviews_root_with_writer_attachment_reference(self):
        args, cli, root, writer, synthesis = self.fixture(); result = self.call(args, cli); summary = json.loads(cli.tasks[root]["summary"])
        self.assertEqual((result["status"], cli.tasks[root]["status"], cli.attachments[root]), ("review", "review", []))
        self.assertEqual({key:summary[key] for key in ("writer_task_id", "attachment_filename", "attachment_size", "reviewed_digest")}, {"writer_task_id":writer, "attachment_filename":"generated-prd.txt", "attachment_size":8, "reviewed_digest":self.digest})

    def test_approve_with_open_blocker_routes_needs_human_without_selection(self):
        blockers = [{"id":"pm-1", "owner":"product-pm", "status":"open", "evidence":"metric missing"}]
        args, cli, root, writer, synthesis = self.fixture("approve", blockers); result = self.call(args, cli); summary = json.loads(cli.tasks[root]["summary"])
        self.assertEqual((result["status"], summary["verdict"]), ("review", "needs-human")); self.assertNotIn("writer_task_id", summary)

    def test_deny_and_needs_human_route_review(self):
        for verdict in ("deny", "needs-human"):
            with self.subTest(verdict=verdict):
                args, cli, root, writer, synthesis = self.fixture(verdict); self.assertEqual(self.call(args, cli)["status"], "review")
                self.assertEqual(json.loads(cli.tasks[root]["summary"])["verdict"], verdict)

    def test_native_synthesis_metadata_creates_targeted_revision(self):
        args, cli, root, writer, synthesis = self.fixture(); metadata = self.native_metadata(args.operation_id)
        cli.tasks[synthesis]["result"] = None
        cli.tasks[synthesis]["runs"] = [
            {"id":2, "outcome":"completed", "ended_at":2, "metadata":metadata},
            {"id":3, "outcome":"failed", "ended_at":3, "metadata":None},
            {"id":1, "outcome":"completed", "ended_at":1, "metadata":{}},
        ]
        result = self.call(args, cli)
        round_one = {json.loads(task["body"])["role"]:task for task in cli.tasks.values() if json.loads(task["body"])["round"] == 1}
        self.assertEqual((result["status"], result["digest"], set(round_one)),
                         ("revision", "attachment:2", {"writer", "mvp", "occams-razor", "product-pm", "synthesis"}))
        blockers = json.loads(round_one["writer"]["body"])["blockers"]
        self.assertEqual(blockers, [
            {"id":"scope", "owner":"product-pm", "status":"open", "evidence":"scope: reduce scope"},
            {"id":"mvp-1", "owner":"mvp", "status":"open", "evidence":"block"},
        ])
        count = len(cli.tasks); self.assertEqual(self.call(args, cli)["status"], "waiting"); self.assertEqual(len(cli.tasks), count)

    def test_authoritative_document_metadata_approves_revision(self):
        args, cli, root, writer, synthesis = self.fixture(); metadata = self.native_metadata(args.operation_id)
        for key in ("draft_attachment_id", "draft_attachment_filename", "draft_attachment_size_bytes", "draft_digest", "must_fix_count"):
            metadata.pop(key)
        metadata.update(verdict="PASS", advance_condition_met=True, blocking_issues=[],
                        reviewer_verdicts={"product-pm":"PASS", "mvp":"APPROVED", "occams-razor":"PASS"},
                        authoritative_document={"attachment_id":2, "filename":"generated-prd.txt", "task_id":writer})
        cli.tasks[synthesis]["result"] = None
        cli.tasks[synthesis]["runs"] = [{"id":1, "outcome":"completed", "ended_at":1, "metadata":metadata}]
        result = self.call(args, cli)
        self.assertEqual((result["status"], result["digest"], cli.tasks[root]["status"]), ("review", "attachment:2", "review"))

    def test_legacy_synthesis_attachment_aliases_remain_supported(self):
        args, cli, root, writer, synthesis = self.fixture(); metadata = self.native_metadata(args.operation_id)
        metadata["draft_filename"] = metadata.pop("draft_attachment_filename")
        metadata["draft_size"] = metadata.pop("draft_attachment_size_bytes")
        cli.tasks[synthesis]["result"] = None
        cli.tasks[synthesis]["runs"] = [{"id":1, "outcome":"completed", "ended_at":1, "metadata":metadata}]
        self.assertEqual(self.call(args, cli)["status"], "revision")

    def test_native_synthesis_metadata_drift_routes_review(self):
        for fault in ("mismatch", "missing", "malformed"):
            with self.subTest(fault=fault):
                args, cli, root, writer, synthesis = self.fixture(); metadata = self.native_metadata(args.operation_id)
                cli.tasks[synthesis]["result"] = None
                if fault == "mismatch": metadata["draft_attachment_size_bytes"] = 9
                if fault == "malformed": metadata["blocking_issues"] = [""]
                cli.tasks[synthesis]["runs"] = [] if fault == "missing" else [{"id":1, "outcome":"completed", "ended_at":1, "metadata":metadata}]
                self.assertEqual(self.call(args, cli)["status"], "review")
                self.assertEqual(cli.tasks[root]["status"], "review")

    def test_revision_graph_and_replay(self):
        blockers = [{"id":"pm-1", "owner":"product-pm", "status":"open", "evidence":"metric missing"}]
        args, cli, root, writer, synthesis = self.fixture("revise", blockers); result = self.call(args, cli)
        round_one = {json.loads(task["body"])["role"]:task for task in cli.tasks.values() if json.loads(task["body"])["round"] == 1}
        self.assertEqual((result["status"], set(round_one)), ("revision", {"writer", "mvp", "occams-razor", "product-pm", "synthesis"}))
        self.assertEqual(round_one["writer"]["_parents"], [synthesis]); self.assertEqual(round_one["synthesis"]["_parents"], [round_one[role]["id"] for role in ("mvp", "occams-razor", "product-pm")])
        unblocked = [json.loads(cli.tasks[command[command.index("unblock") + 1]]["body"])["role"] for command in cli.commands if "unblock" in command]
        self.assertEqual(unblocked, ["synthesis", "product-pm", "occams-razor", "mvp", "writer"])
        count = len(cli.tasks); self.assertEqual(self.call(args, cli)["status"], "waiting"); self.assertEqual(len(cli.tasks), count)

    def test_partial_revision_crash_after_each_role_heals_without_duplicates(self):
        blockers = [{"id":"pm-1", "owner":"product-pm", "status":"open", "evidence":"metric missing"}]
        expected = {"writer", "mvp", "occams-razor", "product-pm", "synthesis"}
        for crash_role in ("writer", "mvp", "occams-razor", "product-pm", "synthesis"):
            with self.subTest(role=crash_role):
                args, cli, root, writer, synthesis = self.fixture("revise", blockers); cli.crash_role = crash_role
                with self.assertRaisesRegex(RuntimeError, "crash after create"): self.call(args, cli)
                self.assertEqual(self.call(args, cli)["status"], "revision")
                round_one = [json.loads(task["body"])["role"] for task in cli.tasks.values() if json.loads(task["body"])["round"] == 1]
                self.assertEqual(set(round_one), expected); self.assertEqual(len(round_one), len(expected))
                count = len(cli.tasks); self.call(args, cli); self.assertEqual(len(cli.tasks), count)

    def test_graph_edge_and_body_drift_route_review(self):
        for fault in ("edge", "body"):
            with self.subTest(fault=fault):
                args, cli, root, writer, synthesis = self.fixture()
                reviewer = next(task for task in cli.tasks.values() if json.loads(task["body"])["role"] == "mvp")
                if fault == "edge": reviewer["_parents"] = []
                else:
                    body = json.loads(reviewer["body"]); body["extra"] = True; reviewer["body"] = advance.canonical(body)
                self.assertEqual(self.call(args, cli)["status"], "review"); self.assertEqual(cli.tasks[root]["status"], "review")

    def test_attachment_and_digest_drift_route_review(self):
        for fault in ("attachment", "digest"):
            with self.subTest(fault=fault):
                args, cli, root, writer, synthesis = self.fixture()
                if fault == "attachment": cli.attachments[writer][0]["size"] = 0
                else: cli.tasks[synthesis]["result"] = advance.canonical({"verdict":"approve", "reviewed_digest":"short", "blockers":[]})
                self.assertEqual(self.call(args, cli)["status"], "review")

    def test_round_two_revise_routes_review_without_round_three(self):
        args, cli, root, writer, synthesis = self.fixture("revise", round_=2); result = self.call(args, cli)
        self.assertEqual((result["status"], cli.tasks[root]["status"], max(json.loads(task["body"])["round"] for task in cli.tasks.values())), ("review", "review", 2))
        self.assertEqual(json.loads(cli.tasks[root]["summary"])["verdict"], "revise")

if __name__ == "__main__": unittest.main()
