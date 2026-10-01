#!/usr/bin/env python3
import argparse, hashlib, importlib.util, io, json, pathlib, tempfile, unittest
from unittest import mock

ROOT=pathlib.Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location("swe_enqueue",ROOT/"scripts/hermes-swe-kanban-enqueue.py");swe=importlib.util.module_from_spec(spec);spec.loader.exec_module(swe)


class FakeRun:
    def __init__(self, request, workspace): self.request=request; self.workspace=workspace; self.body=None; self.commands=[]
    def __call__(self, command, timeout=120, json_output=False, input_text=None):
        command=list(map(str,command)); self.commands.append(command)
        if "--check" in command: return {"repo":self.request["repository"],"granted":True}
        if "pin" in command and "hermes-repository-cache" in " ".join(command): return {"pin_ref":f"refs/hermes-pins/{self.request['operation_id']}"}
        if "hermes-swe-workspace" in " ".join(command): return {"worktree":str(self.workspace),"base_sha":self.request["base_sha"],"branch":next(item for item in command if item.startswith("hermes/"))}
        if "kanban" in command:
            index=command.index("kanban"); action=command[index+1] if command[index+1]!="--board" else command[index+3]
            if action=="boards": return []
            if action=="create" and command[index+1]=="boards": return ""
            if action=="create": self.body=command[command.index("--body")+1]; return {"id":"t_1234abcd"}
            if action=="show":
                return {"task":{"id":"t_1234abcd","body":self.body,"tenant":self.request["operation_id"],"workspace_path":str(self.workspace),"assignee":"swe-implement-v1","status":"blocked"},"runs":[],"events":[{"kind":"created"},{"kind":"blocked"}]}
            if action=="unblock": return ""
        raise AssertionError(command)


class SweEnqueueTest(unittest.TestCase):
    def request(self, **changes):
        core={"repository":"ACME/widget","base_branch":"main","base_sha":"a"*40,"title":"Fix widget behavior","problem":"Widget returns wrong result","acceptance_criteria":["Regression test passes","Correct value is returned"],"non_goals":["Refactor unrelated code"],"validation_expectations":["Run focused unit tests"],"source_type":"approved-design","source_refs":[{"ref":str(self.design),"sha256":hashlib.sha256(self.design.read_bytes()).hexdigest()}],"human_approval":{"approved_by":"Zhach","approved_at":"2026-10-01T00:00:00Z","scope":"implementation-and-draft-pr"},"remote_effects":{"push_branch":True,"draft_pr":True}}
        core.update(changes); operation="swe-implement-"+hashlib.sha256(swe.canonical(core)).hexdigest(); return {"operation_id":operation,**core}
    def args(self, root):
        return argparse.Namespace(authority_file=root/"authority.yaml",authority_bin=root/"authority",cache_root=root/"cache",cache_bin=root/"hermes-repository-cache",workspace_bin=root/"hermes-swe-workspace",work_root=root/"work",reader_group="staff",service_user="hermes-agent",service_home=pathlib.Path("/Users/hermes-agent"),hermes_home=pathlib.Path("/Users/hermes-agent/.hermes"),hermes_bin=pathlib.Path("/Users/hermes-agent/.local/bin/hermes"),max_cache_age_seconds=3600)
    def admit(self, request, callback):
        raw=swe.canonical(request).decode()
        with mock.patch("sys.stdin",io.StringIO(raw)),mock.patch.object(swe,"run",side_effect=callback),mock.patch.object(swe.os,"geteuid",return_value=0): return swe.enqueue(self.args(self.root))
    def setUp(self):
        temporary=tempfile.TemporaryDirectory();self.addCleanup(temporary.cleanup);self.root=pathlib.Path(temporary.name);self.design=self.root/"design.md";self.design.write_text("approved design\n");(self.root/"work").mkdir();self.workspace=self.root/"work/repo";self.workspace.mkdir()
        snapshot=self.root/"snapshot";snapshot.mkdir();self.manifest={"repository":"ACME/widget","default_branch":"main","head_sha":"a"*40,"snapshot_sha":"a"*40,"snapshot":str(snapshot),"fetched_at":swe.time.time(),"mirror":str(self.root/"mirror.git"),"remote":"https://github.com/ACME/widget.git"};p=self.root/"cache/ACME/widget.json";p.parent.mkdir(parents=True);p.write_text(json.dumps(self.manifest))

    def test_admits_exact_approved_push_task(self):
        request=self.request(); fake=FakeRun(request,self.workspace); result=self.admit(request,fake)
        self.assertEqual((result["status"],result["task_id"],result["repository"]),("ready","t_1234abcd","ACME/widget"))
        body=json.loads(fake.body); self.assertEqual(body["request"],request); self.assertEqual(body["contract"]["posture"],"Caveman reasoning and Ponytail coding")
        create=next(c for c in fake.commands if "--idempotency-key" in c)
        self.assertIn("--goal",create); self.assertIn("dir:"+str(self.workspace),create); self.assertEqual(create[create.index("--assignee")+1],"swe-implement-v1")

    def test_rejects_missing_approval_effect_and_stale_cache(self):
        for changes,message in (({"human_approval":{"approved_by":"","approved_at":"x","scope":"implementation-and-draft-pr"}},"approval"),({"remote_effects":{"push_branch":False,"draft_pr":True}},"remote effects")):
            request=self.request(**changes)
            with self.assertRaisesRegex(ValueError,message): self.admit(request,FakeRun(request,self.workspace))
        request=self.request(source_refs=[{"ref":str(self.design),"sha256":"b"*64}])
        with self.assertRaisesRegex(ValueError,"digest differs"): self.admit(request,FakeRun(request,self.workspace))
        self.manifest["fetched_at"]=1;(self.root/"cache/ACME/widget.json").write_text(json.dumps(self.manifest));request=self.request()
        with self.assertRaisesRegex(ValueError,"stale"): self.admit(request,FakeRun(request,self.workspace))

    def test_rejects_non_root_before_any_side_effect(self):
        request=self.request(); fake=FakeRun(request,self.workspace)
        raw=swe.canonical(request).decode()
        with mock.patch("sys.stdin",io.StringIO(raw)),mock.patch.object(swe,"run",side_effect=fake),mock.patch.object(swe.os,"geteuid",return_value=501):
            with self.assertRaisesRegex(ValueError,"root"): swe.enqueue(self.args(self.root))
        self.assertEqual(fake.commands,[])

    def test_rejects_create_show_identity_drift(self):
        for field,override in (("body","{\"tampered\":true}"),("tenant","foreign-op"),("workspace_path","/tmp/other"),("assignee","wrong-profile")):
            with self.subTest(field=field):
                request=self.request(); fake=FakeRun(request,self.workspace)
                original_call=fake.__call__
                def drifted(command,timeout=120,json_output=False,input_text=None,_f=field,_o=override,_orig=original_call):
                    result=_orig(command,timeout=timeout,json_output=json_output,input_text=input_text)
                    if isinstance(result,dict) and "task" in result and "show" in list(map(str,command)):
                        task=dict(result["task"]); task[_f]=_o; return {"task":task,"runs":result["runs"],"events":result["events"]}
                    return result
                with self.assertRaisesRegex(ValueError,"identity drift"): self.admit(request,drifted)

    def test_rejects_noncanonical_operation_and_repo(self):
        request=self.request(); request["operation_id"]="swe-implement-"+"0"*64
        with self.assertRaisesRegex(ValueError,"operation ID"): self.admit(request,FakeRun(request,self.workspace))
        request=self.request(repository="../repo")
        with self.assertRaisesRegex(ValueError,"repository"): self.admit(request,FakeRun(request,self.workspace))


if __name__=="__main__":unittest.main()
