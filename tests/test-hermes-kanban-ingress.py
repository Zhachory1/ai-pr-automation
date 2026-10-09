#!/usr/bin/env python3
import hashlib
import hmac
import importlib.util
import json
import pathlib
import socket
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("kanban_ingress", ROOT / "scripts/hermes-kanban-ingress.py")
ingress = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ingress)


class IngressTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = pathlib.Path(self.temp.name)
        self.work = self.root / "work"
        self.work.mkdir(mode=0o700)
        self.key = self.root / "key"
        self.key.write_text("a" * 64 + "\n")
        self.key.chmod(0o600)
        self.authority = self.root / "authority.yaml"
        self.authority.write_text("repos:\n  - owner/repo\n")
        self.config = ingress.Config(self.work, self.authority, self.root, self.root / "hermes", {"pr-review": "a" * 64, "pr-maintain": "b" * 64})
        self.review = {"repo": "owner/repo", "number": 7, "url": "https://github.com/owner/repo/pull/7", "title": "Fix", "head_sha": "a" * 40}

    def prior_card(self, request, workspace, status="running", task_id="t_12345678"):
        terminal = status in {"done", "archived"}
        task = {"id": task_id, "title": f"{request['repo']}#{request['number']} @ {request['head_sha'][:8]}",
                "body": ingress.ENQUEUE.canonical(request).decode(), "assignee": "pr-maintain-v1",
                "status": status, "priority": 0, "tenant": request["operation_id"], "workspace_kind": "dir",
                "workspace_path": str(workspace), "branch_name": None, "project_id": None, "created_by": "operator",
                "created_at": 1, "started_at": 1 if status == "running" else None,
                "completed_at": 2 if terminal else None, "result": "ok" if terminal else None,
                "skills": [], "max_runtime_seconds": 2400, "max_retries": 1, "model_override": ingress.ENQUEUE.MODEL,
                "provider_override": "anthropic", "session_id": None, "workflow_template_id": None,
                "current_step_key": None, "completion_contract": "local-only", "last_failure_error": None}
        self.assertEqual(set(task), ingress.ENQUEUE.TASK_KEYS)
        return {"task": task, "latest_summary": None, "parents": [], "children": [], "comments": [], "events": [], "runs": []}

    def test_key_permissions_fail_closed(self):
        self.assertEqual(ingress.read_key(self.key), "a" * 64)
        self.key.write_text("a" * 64)
        self.assertEqual(ingress.read_key(self.key), "a" * 64)
        self.key.write_text("a" * 64 + " ")
        with self.assertRaises(ValueError):
            ingress.read_key(self.key)
        self.key.write_text("a" * 64 + "\n")
        self.key.chmod(0o644)
        with self.assertRaises(ValueError):
            ingress.read_key(self.key)

    def test_auth_scope_and_shape_fail_before_helper(self):
        with mock.patch.object(ingress, "invoke") as helper:
            for kind, key, payload in (
                ("pr-review", "wrong", self.review),
                ("pr-maintain", "a" * 64, {**self.review, "feedback_digest": "1" * 64}),
                ("pr-review", "a" * 64, {**self.review, "repo": "other/repo", "url": "https://github.com/other/repo/pull/7"}),
                ("pr-review", "a" * 64, {**self.review, "number": True}),
                ("pr-review", "a" * 64, {**self.review, "extra": "x"}),
            ):
                with self.assertRaises(ValueError):
                    ingress.admit(self.config, kind, key, payload)
            helper.assert_not_called()

    def test_two_active_council_heads_cap_new_admission_only(self):
        for suffix in ('1','2'):
            op='pr-review-'+suffix*64
            workspace=ingress.ENQUEUE.safe_dir(self.work/op,True)
            ingress.ENQUEUE.immutable(workspace/'request.json',
                ingress.ENQUEUE.canonical({'route':'council-v2','operation_id':op}))
        with self.assertRaisesRegex(ValueError,'capacity'):
            ingress.council_capacity(self.config,'pr-review-'+'3'*64)
        ingress.council_capacity(self.config,'pr-review-'+'1'*64)
        first=self.work/('pr-review-'+'1'*64)
        ingress.COUNCIL._immutable_file(first/'review-outcome.json',
            ingress.ENQUEUE.canonical({'operation_id':first.name,'review_id':7}))
        ingress.council_capacity(self.config,'pr-review-'+'3'*64)

    def test_uninstalled_council_profiles_block_enablement(self):
        config=self.config._replace(council_enabled=True,council_install=self.root/'runtime')
        with self.assertRaises(ValueError):ingress.validate_council_runtime(config)

    def test_source_pinned_profiles_and_owner_only_roots_pass_preflight(self):
        home=self.root/'hermes-home';install=self.root/'runtime'
        (home/'bin').mkdir(parents=True);(home/'profiles').mkdir()
        (install/'hermes_cli').mkdir(parents=True);(install/'hermes_cli/kanban_db.py').write_text('synthetic')
        (install/'venv/bin').mkdir(parents=True);python=install/'venv/bin/python';python.write_text('synthetic');python.chmod(0o500)
        tool=home/'bin/hermes-council-tools'
        tool.write_bytes((ROOT/'bin/hermes-council-tools').read_bytes());tool.chmod(0o500)
        env={'PR_REVIEW_COUNCIL_REPO_ROOT':str(home.parent/'code'),
             'PR_REVIEW_COUNCIL_WORKFLOW_ROOT':str(self.work),
             'HERMES_COUNCIL_TOOLS_BIN':str(tool),'HERMES_COUNCIL_TOOLS_PYTHON':str(python)}
        for role in ('generalist','reliability','mvp','security','synthesis'):
            name=f'pr-review-{role}-v2';target=home/'profiles'/name;target.mkdir(mode=0o700)
            source=ROOT/'agent-config/hermes/profiles'/name
            for filename in ('config.yaml','SOUL.md'):
                (target/filename).write_bytes((source/filename).read_bytes())
            dot=target/'.env';dot.write_text(''.join(f'{key}={json.dumps(value)}\n' for key,value in env.items()));dot.chmod(0o600)
        config=self.config._replace(home=home,council_enabled=True,council_install=install)
        ingress.validate_council_runtime(config)
        (home/'profiles/pr-review-mvp-v2/.env').chmod(0o644)
        with self.assertRaises(ValueError):ingress.validate_council_runtime(config)

    def test_council_ingress_claims_fast_without_github_or_kanban_calls(self):
        config=self.config._replace(council_enabled=True,council_install=self.root/'runtime')
        with mock.patch.object(ingress.GITHUB,'GitHub',side_effect=AssertionError('GitHub in ingress')),\
             mock.patch.object(ingress,'invoke_council',side_effect=AssertionError('worker in ingress')):
            first=ingress.admit(config,'pr-review','a'*64,self.review)
            self.assertEqual(first['status'],'deferred')
            self.assertIsNone(first['task_id'])
            self.assertEqual(first,ingress.admit(config,'pr-review','a'*64,self.review))
        owner=self.work/first['operation_id']/'request.json'
        self.assertEqual(json.loads(owner.read_text())['route'],'council-v2')

    def test_council_flag_routes_only_unbound_review_heads(self):
        config=self.config._replace(council_enabled=True,council_install=self.root/'runtime')
        expected={'kind':'pr-review','board':'pr-review','operation_id':'placeholder',
                  'task_id':'t_00000001','status':'ready'}
        def new_route(config,request):return {**expected,'operation_id':request['operation_id']}
        with mock.patch.object(ingress,'invoke_council',side_effect=AssertionError('worker in ingress')),\
             mock.patch.object(ingress,'invoke',side_effect=lambda cfg,kind,req:new_route(cfg,req)) as legacy:
            self.assertEqual(ingress.admit(config,'pr-review','a'*64,self.review)['status'],'deferred')
            legacy.assert_not_called()
            old={**self.review,'head_sha':'b'*40}
            op=ingress.validate(old,'pr-review')['operation_id']
            workspace=ingress.ENQUEUE.safe_dir(self.work/op,True)
            prior={'operation_id':op,**old}
            ingress.ENQUEUE.immutable(workspace/'request.json',ingress.ENQUEUE.canonical(prior))
            self.assertEqual(ingress.admit(config,'pr-review','a'*64,old)['status'],'ready')
            legacy.assert_called_once()

    def test_overlapping_kinds_wait_for_the_admission_lock(self):
        entered = threading.Event()
        release = threading.Event()
        results = []

        def helper(config, kind, payload):
            if kind == "pr-review":
                entered.set()
                self.assertTrue(release.wait(2))
            return {"kind": kind, "board": kind, "operation_id": payload["operation_id"],
                    "task_id": "t_12345678", "status": "ready"}

        with mock.patch.object(ingress, "invoke", side_effect=helper) as invoked:
            first = threading.Thread(target=lambda: results.append(ingress.admit(
                self.config, "pr-review", "a" * 64, self.review)), daemon=True)
            first.start()
            self.assertTrue(entered.wait(2))
            timer = threading.Timer(0.2, release.set)
            timer.start()
            try:
                second = ingress.admit(self.config, "pr-maintain", "b" * 64,
                                       {**self.review, "feedback_digest": "1" * 64})
            finally:
                release.set()
                first.join(2)
                timer.join(2)
            self.assertFalse(first.is_alive())
            self.assertEqual((second["kind"], len(results), invoked.call_count), ("pr-maintain", 1, 2))

    def test_unresolved_create_outcome_is_visible_without_replaying(self):
        operation = ingress.identity("pr-review", "owner/repo", 7, "a" * 40)["operation_id"]
        request = {"operation_id": operation, **self.review}
        failure = ingress.subprocess.CompletedProcess([], 1, b"", b"Hermes PR enqueue failed: unresolved create outcome\n")
        with mock.patch.object(ingress.subprocess, "run", return_value=failure) as cli:
            with self.assertRaisesRegex(ValueError, "unresolved create outcome"):
                ingress.invoke(self.config, "pr-review", request)
        cli.assert_called_once()

    def test_review_replay_is_exact_and_maintenance_round_is_capped(self):
        responses, task_ids, payload_by_id = [], {}, {}
        def helper(config, kind, payload):
            responses.append(payload)
            if payload["operation_id"] not in task_ids:
                task_id = f"t_{len(task_ids) + 1:08x}"
                task_ids[payload["operation_id"]] = task_id
                payload_by_id[task_id] = payload
            return {"kind": kind, "board": kind, "operation_id": payload["operation_id"],
                    "task_id": task_ids[payload["operation_id"]], "status": "ready"}
        def shown(_command, _env, _root, _board, task_id, timeout):
            prior = payload_by_id[task_id]
            return self.prior_card(prior, self.work / prior["operation_id"], "done", task_id)
        with mock.patch.object(ingress, "invoke", side_effect=helper), \
             mock.patch.object(ingress.ENQUEUE, "show", side_effect=shown):
            first = ingress.admit(self.config, "pr-review", "a" * 64, self.review)
            self.assertEqual(first, ingress.admit(self.config, "pr-review", "a" * 64, self.review))
            self.assertEqual(responses[0], responses[1])
            for index in range(1, 4):
                payload = {**self.review, "feedback_digest": f"{index}" * 64}
                admitted = ingress.admit(self.config, "pr-maintain", "b" * 64, payload)
                self.assertEqual(responses[-1]["round"], index)
                self.assertEqual(admitted["status"], "ready")
                entry = self.work / admitted["operation_id"]
                entry.mkdir(mode=0o700)
                record = entry / "request.json"
                record.write_bytes(ingress.ENQUEUE.canonical(responses[-1]))
                record.chmod(0o440)
                intent = entry / "create-intent.json"
                intent.write_bytes(ingress.ENQUEUE.canonical({"operation_id": admitted["operation_id"],
                    "request_digest": hashlib.sha256(record.read_bytes()).hexdigest()}))
                intent.chmod(0o440)
                binding = entry / "task-id.json"
                binding.write_bytes(ingress.ENQUEUE.canonical({"task_id": admitted["task_id"]}))
                binding.chmod(0o440)
            replay = ingress.admit(self.config, "pr-maintain", "b" * 64, {**self.review, "feedback_digest": "1" * 64, "head_sha": "b" * 40})
            self.assertEqual(replay["operation_id"], responses[2]["operation_id"])
            capped = ingress.admit(self.config, "pr-maintain", "b" * 64, {**self.review, "feedback_digest": "4" * 64})
            self.assertEqual(capped["status"], "capped")
            self.assertIsNone(capped["task_id"])
            self.assertEqual(len(responses), 6)

    def test_distinct_maintenance_round_waits_for_prior_card(self):
        first = {"operation_id": ingress.identity("pr-maintain", "owner/repo", 7, "a" * 40, "1" * 64)["operation_id"],
                 **self.review, "feedback_digest": "1" * 64, "round": 1}
        workspace = self.work / first["operation_id"]
        workspace.mkdir(mode=0o700)
        record = workspace / "request.json"
        record.write_bytes(ingress.ENQUEUE.canonical(first))
        record.chmod(0o440)
        payload = {**self.review, "feedback_digest": "2" * 64}
        shown = self.prior_card(first, workspace)
        task = shown["task"]
        with mock.patch.object(ingress, "invoke") as helper:
            with self.assertRaisesRegex(ValueError, "prior maintenance request incomplete for " + first["operation_id"]):
                ingress.admit(self.config, "pr-maintain", "b" * 64, payload)
            intent = workspace / "create-intent.json"
            intent.write_bytes(ingress.ENQUEUE.canonical({"operation_id": first["operation_id"],
                "request_digest": hashlib.sha256(record.read_bytes()).hexdigest()}))
            intent.chmod(0o440)
            with self.assertRaisesRegex(ValueError, first["operation_id"]):
                ingress.admit(self.config, "pr-maintain", "b" * 64, payload)
            binding = workspace / "task-id.json"
            binding.write_bytes(ingress.ENQUEUE.canonical({"task_id": "t_12345678"}))
            binding.chmod(0o440)
            with mock.patch.object(ingress.ENQUEUE, "show", return_value=shown) as show:
                self.assertEqual(ingress.admit(self.config, "pr-maintain", "b" * 64, payload)["status"], "deferred")
                self.assertLessEqual(show.call_args.kwargs["timeout"], 2)
                task.update(status="done", completed_at=2, result="ok")
                for field, altered in (("tenant", "other-operation"), ("body", "{}"),
                                       ("workspace_path", str(self.root)), ("id", "t_87654321")):
                    original = task[field]
                    task[field] = altered
                    with self.subTest(field=field), self.assertRaisesRegex(ValueError, "card drift|invalid prior maintenance"):
                        ingress.admit(self.config, "pr-maintain", "b" * 64, payload)
                    task[field] = original
                shown["runs"] = [{"id": 1, "profile": "pr-maintain-v1", "step_key": None,
                                  "status": "running", "outcome": None, "summary": None, "error": None,
                                  "metadata": None, "worker_pid": 123, "started_at": 1, "ended_at": None}]
                with self.assertRaisesRegex(ValueError, "open run"):
                    ingress.admit(self.config, "pr-maintain", "b" * 64, payload)
            helper.assert_not_called()
        for status in ("done", "archived"):
            shown = self.prior_card(first, workspace, status)
            with mock.patch.object(ingress.ENQUEUE, "show", return_value=shown), \
                 mock.patch.object(ingress, "invoke", return_value={"status": "ready"}) as helper:
                self.assertEqual(ingress.admit(self.config, "pr-maintain", "b" * 64, payload)["status"], "ready")
                self.assertEqual(helper.call_args.args[2]["round"], 2)
        with mock.patch.object(ingress.time, "monotonic", side_effect=[0, 0, 3]), \
             mock.patch.object(ingress.ENQUEUE, "show", return_value=shown), \
             mock.patch.object(ingress, "invoke") as helper:
            with self.assertRaisesRegex(TimeoutError, "prior maintenance status timed out"):
                ingress.admit(self.config, "pr-maintain", "b" * 64, payload)
            helper.assert_not_called()

    def test_later_uncertain_create_is_not_hidden_by_earlier_active_round(self):
        for round_number in (1, 2):
            digest = str(round_number) * 64
            prior = {"operation_id": ingress.identity("pr-maintain", "owner/repo", 7, "a" * 40, digest)["operation_id"],
                     **self.review, "feedback_digest": digest, "round": round_number}
            workspace = self.work / prior["operation_id"]
            workspace.mkdir(mode=0o700)
            record = workspace / "request.json"
            record.write_bytes(ingress.ENQUEUE.canonical(prior))
            record.chmod(0o440)
            if round_number == 1:
                first = prior
                first_workspace = workspace
                intent = workspace / "create-intent.json"
                intent.write_bytes(ingress.ENQUEUE.canonical({"operation_id": prior["operation_id"],
                    "request_digest": hashlib.sha256(record.read_bytes()).hexdigest()}))
                intent.chmod(0o440)
                binding = workspace / "task-id.json"
                binding.write_bytes(ingress.ENQUEUE.canonical({"task_id": "t_12345678"}))
                binding.chmod(0o440)
            else:
                second = prior
                second_workspace = workspace
                second_record = record
        payload = {**self.review, "feedback_digest": "3" * 64}
        cached = self.config._replace(history={("owner/repo", 7): {
            first["feedback_digest"]: (first, first_workspace),
            second["feedback_digest"]: (second, second_workspace)}})
        with mock.patch.object(ingress, "invoke") as helper:
            with mock.patch.object(ingress.ENQUEUE, "show", return_value=self.prior_card(first, first_workspace)):
                with self.assertRaisesRegex(ValueError, "prior maintenance request incomplete for " + second["operation_id"]):
                    ingress.admit(cached, "pr-maintain", "b" * 64, payload)
                intent = second_workspace / "create-intent.json"
                intent.write_bytes(ingress.ENQUEUE.canonical({"operation_id": second["operation_id"],
                    "request_digest": hashlib.sha256(second_record.read_bytes()).hexdigest()}))
                intent.chmod(0o440)
                with self.assertRaisesRegex(ValueError, "unresolved create outcome for " + second["operation_id"]):
                    ingress.admit(cached, "pr-maintain", "b" * 64, payload)
            helper.assert_not_called()

    def test_maintenance_history_above_1000_other_workspaces_does_not_stop_admission(self):
        for index in range(1001):
            repo = f"other/repo{index}"
            prior = {"repo": repo, "number": 1, "url": f"https://github.com/{repo}/pull/1",
                     "title": "Other PR", "head_sha": "b" * 40, "feedback_digest": "0" * 64, "round": 1}
            prior["operation_id"] = ingress.identity("pr-maintain", repo, 1, prior["head_sha"], prior["feedback_digest"])["operation_id"]
            entry = self.work / prior["operation_id"]
            entry.mkdir(mode=0o700)
            record = entry / "request.json"
            record.write_bytes(ingress.ENQUEUE.canonical(prior))
            record.chmod(0o440)
        payload = {**self.review, "feedback_digest": "1" * 64}
        cached = self.config._replace(history=ingress.load_history(self.work),
                                      history_dirs={entry.name for entry in self.work.iterdir()}, history_error=[])
        with mock.patch.object(ingress, "load_history", side_effect=AssertionError("rescan after startup")), \
             mock.patch.object(ingress, "invoke", return_value={"kind": "pr-maintain", "board": "pr-maintain",
                "operation_id": ingress.identity("pr-maintain", "owner/repo", 7, "a" * 40, "1" * 64)["operation_id"],
                "task_id": "t_12345678", "status": "ready"}) as helper:
            self.assertEqual(ingress.admit(cached, "pr-maintain", "b" * 64, payload)["status"], "ready")
            helper.assert_called_once()

    def test_external_maintenance_workspace_requires_reconciliation(self):
        cached = self.config._replace(history=ingress.load_history(self.work), history_dirs=set(), history_error=[])
        prior = {"operation_id": ingress.identity("pr-maintain", "owner/repo", 7, "a" * 40, "1" * 64)["operation_id"],
                 **self.review, "feedback_digest": "1" * 64, "round": 1}
        entry = self.work / prior["operation_id"]
        entry.mkdir(mode=0o700)
        record = entry / "request.json"
        record.write_bytes(ingress.ENQUEUE.canonical(prior))
        record.chmod(0o440)
        with mock.patch.object(ingress, "invoke") as helper:
            with self.assertRaisesRegex(ValueError, "maintenance history changed outside ingress"):
                ingress.admit(cached, "pr-maintain", "b" * 64, {**self.review, "feedback_digest": "2" * 64})
            helper.assert_not_called()

    def test_cached_replay_rejects_missing_binding_and_changed_history(self):
        prior = {"operation_id": ingress.identity("pr-maintain", "owner/repo", 7, "a" * 40, "1" * 64)["operation_id"],
                 **self.review, "feedback_digest": "1" * 64, "round": 1}
        entry = self.work / prior["operation_id"]
        entry.mkdir(mode=0o700)
        record = entry / "request.json"
        record.write_bytes(ingress.ENQUEUE.canonical(prior))
        record.chmod(0o440)
        cached = ingress.prepare_history(self.config)
        payload = {**self.review, "feedback_digest": "1" * 64}
        with mock.patch.object(ingress, "invoke") as helper:
            with self.assertRaisesRegex(ValueError, "prior maintenance request incomplete for " + prior["operation_id"]):
                ingress.admit(cached, "pr-maintain", "b" * 64, payload)
            intent = entry / "create-intent.json"
            intent.write_bytes(ingress.ENQUEUE.canonical({"operation_id": prior["operation_id"],
                "request_digest": hashlib.sha256(record.read_bytes()).hexdigest()}))
            intent.chmod(0o440)
            binding = entry / "task-id.json"
            binding.write_bytes(ingress.ENQUEUE.canonical({"task_id": "t_12345678"}))
            binding.chmod(0o440)
            record.chmod(0o600)
            record.write_bytes(ingress.ENQUEUE.canonical({**prior, "title": "Changed"}))
            record.chmod(0o440)
            with self.assertRaisesRegex(ValueError, "maintenance history changed outside ingress"):
                ingress.admit(cached, "pr-maintain", "b" * 64, payload)
            record.chmod(0o600)
            record.write_bytes(ingress.ENQUEUE.canonical(prior))
            record.chmod(0o440)
            cached.history_dirs.add("pr-maintain-" + "f" * 64)
            with self.assertRaisesRegex(ValueError, "maintenance history changed outside ingress"):
                ingress.admit(cached, "pr-maintain", "b" * 64, payload)
            helper.assert_not_called()

    def test_invalid_maintenance_history_does_not_stop_review(self):
        entry = self.work / ("pr-maintain-" + "c" * 64)
        entry.mkdir(mode=0o700)
        record = entry / "request.json"
        record.write_text("{}")
        record.chmod(0o440)
        with mock.patch.object(ingress.sys, "stderr"):
            prepared = ingress.prepare_history(self.config)
        with mock.patch.object(ingress, "invoke", return_value={"status": "ready"}) as helper:
            self.assertEqual(ingress.admit(prepared, "pr-review", "a" * 64, self.review)["status"], "ready")
            with self.assertRaisesRegex(ValueError, "maintenance history invalid"):
                ingress.admit(prepared, "pr-maintain", "b" * 64,
                              {**self.review, "feedback_digest": "2" * 64})
            helper.assert_called_once()

    def test_timeout_preserves_new_history_for_next_distinct_snapshot(self):
        cached = self.config._replace(history=ingress.load_history(self.work), history_dirs=set(), history_error=[])
        first = {**self.review, "feedback_digest": "1" * 64}
        operation = ingress.identity("pr-maintain", "owner/repo", 7, "a" * 40, "1" * 64)["operation_id"]
        def interrupted(config, kind, request):
            workspace = self.work / request["operation_id"]
            workspace.mkdir(mode=0o700)
            record = workspace / "request.json"
            record.write_bytes(ingress.ENQUEUE.canonical(request))
            record.chmod(0o440)
            intent = workspace / "create-intent.json"
            intent.write_bytes(ingress.ENQUEUE.canonical({"operation_id": request["operation_id"],
                "request_digest": hashlib.sha256(record.read_bytes()).hexdigest()}))
            intent.chmod(0o440)
            raise ingress.subprocess.TimeoutExpired("kanban create", 120)
        with mock.patch.object(ingress, "invoke", side_effect=interrupted):
            with self.assertRaises(ingress.subprocess.TimeoutExpired):
                ingress.admit(cached, "pr-maintain", "b" * 64, first)
        self.assertIn("1" * 64, cached.history[("owner/repo", 7)])
        self.assertIn(operation, cached.history_dirs)
        with mock.patch.object(ingress, "invoke") as helper:
            with self.assertRaisesRegex(ValueError, operation):
                ingress.admit(cached, "pr-maintain", "b" * 64, {**self.review, "feedback_digest": "2" * 64})
            helper.assert_not_called()

    def test_review_title_edit_reuses_stored_request(self):
        operation = ingress.identity("pr-review", "owner/repo", 7, "a" * 40)["operation_id"]
        previous = {"operation_id": operation, **self.review}
        workspace = self.work / operation
        workspace.mkdir(mode=0o700)
        request_path = workspace / "request.json"
        request_path.write_bytes(ingress.ENQUEUE.canonical(previous))
        request_path.chmod(0o440)
        with mock.patch.object(ingress, "invoke", return_value={"kind": "pr-review", "board": "pr-review",
                "operation_id": operation, "task_id": "t_12345678", "status": "done"}) as helper:
            result = ingress.admit(self.config, "pr-review", "a" * 64, {**self.review, "title": "Renamed"})
        self.assertEqual(result["operation_id"], operation)
        self.assertEqual(helper.call_args.args[2], previous)
        self.assertEqual(request_path.read_bytes(), ingress.ENQUEUE.canonical(previous))

    def test_slow_preauth_headers_release_ingress_capacity(self):
        with mock.patch.object(ingress.Handler, "_preauth_seconds", 0.15, create=True):
            server = ingress.BoundedHTTPServer(("127.0.0.1", 0), ingress.Handler)
            server.slots = threading.BoundedSemaphore(1)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                with socket.create_connection(("127.0.0.1", server.server_port), timeout=2) as client:
                    client.settimeout(2)
                    client.sendall(b"P")
                    time.sleep(0.35)
                    try:
                        self.assertEqual(client.recv(1), b"")
                    except ConnectionResetError:
                        pass
                for _ in range(20):
                    if server.slots.acquire(blocking=False):
                        server.slots.release()
                        break
                    time.sleep(0.01)
                else:
                    self.fail("slow unauthenticated connection retained the only slot")
            finally:
                server.shutdown()
                server.server_close()
                thread.join(2)

    def test_http_auth_and_origin_reject_before_admission(self):
        server = ingress.BoundedHTTPServer(("127.0.0.1", 0), ingress.Handler)
        prior = ingress.STATE
        ingress.STATE = self.config
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(lambda: setattr(ingress, "STATE", prior))
        self.addCleanup(thread.join)
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        url = f"http://127.0.0.1:{server.server_port}/v1/pr-tasks/pr-review"
        body = json.dumps(self.review).encode()
        def request(key, origin=None, data=body, signed_body=body, timestamp=None):
            stamp = str(int(time.time()) if timestamp is None else timestamp)
            message = b"POST\n/v1/pr-tasks/pr-review\n" + stamp.encode() + b"\n" + signed_body
            signature = hmac.new(bytes.fromhex(key), message, hashlib.sha256).hexdigest()
            headers = {"Host": "host.docker.internal:8767", "Content-Type": "application/json",
                       "X-Hermes-Timestamp": stamp, "X-Hermes-Signature": signature}
            if origin: headers["Origin"] = origin
            return urllib.request.Request(url, data=data, headers=headers, method="POST")
        with mock.patch.object(ingress, "invoke", return_value={"kind": "pr-review", "board": "pr-review",
                "operation_id": ingress.identity("pr-review", "owner/repo", 7, "a" * 40)["operation_id"],
                "task_id": "t_12345678", "status": "ready"}) as helper:
            altered = json.dumps({**self.review, "title": "Changed"}).encode()
            for req, expected in ((request("b" * 64), 401), (request("a" * 64, "https://example.com"), 403),
                                  (request("a" * 64, data=altered), 401),
                                  (request("a" * 64, timestamp=int(time.time()) - 1000), 401)):
                with self.assertRaises(urllib.error.HTTPError) as response:
                    urllib.request.urlopen(req, timeout=3)
                self.assertEqual(response.exception.code, expected)
                response.exception.close()
            with self.assertRaises(urllib.error.HTTPError) as probe:
                urllib.request.urlopen(request("a" * 64, data=b"{}", signed_body=b"{}"), timeout=3)
            self.assertEqual(probe.exception.code, 400)
            self.assertEqual(json.load(probe.exception), {"error": "invalid request fields"})
            probe.exception.close()
            helper.assert_not_called()
            with urllib.request.urlopen(request("a" * 64), timeout=3) as response:
                self.assertEqual(response.status, 200)
            with mock.patch.object(server, "slots", threading.BoundedSemaphore(0)):
                with self.assertRaises(OSError):
                    urllib.request.urlopen(request("a" * 64), timeout=3)
            helper.assert_called_once()


if __name__ == "__main__":
    unittest.main()
