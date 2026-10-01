#!/usr/bin/env python3
import argparse, importlib.util, pathlib, subprocess, tempfile, unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("swe_workspace", ROOT / "scripts/hermes-swe-workspace.py")
workspace = importlib.util.module_from_spec(spec); spec.loader.exec_module(workspace)


def git(*args, cwd=None): return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True).stdout.strip()


class SweWorkspaceTest(unittest.TestCase):
    def fixture(self):
        temporary = tempfile.TemporaryDirectory(); self.addCleanup(temporary.cleanup); root = pathlib.Path(temporary.name)
        seed, mirror, work = root / "seed", root / "mirror.git", root / "work"
        git("init", "--initial-branch=main", str(seed)); git("config", "user.email", "test@example.com", cwd=seed); git("config", "user.name", "Test", cwd=seed)
        (seed / "README.md").write_text("source\n"); git("add", "README.md", cwd=seed); git("commit", "-m", "source", cwd=seed)
        sha = git("rev-parse", "HEAD", cwd=seed); git("clone", "--mirror", str(seed), str(mirror))
        operation = "swe-implement-" + "a" * 64; pin_ref = f"refs/hermes-pins/{operation}"; git("--git-dir", str(mirror), "update-ref", pin_ref, sha)
        work.mkdir(mode=0o700)
        args = argparse.Namespace(command="prepare", repository="ACME/widget", operation=operation, base_sha=sha, branch="hermes/aaaaaaaaaaaa-change", mirror=mirror, pin_ref=pin_ref, remote="https://github.com/ACME/widget.git", work_root=work)
        return args, seed, mirror, work

    def test_prepare_replay_and_status(self):
        args, _, mirror, _ = self.fixture(); first = workspace.prepare(args); replay = workspace.prepare(args)
        self.assertEqual(first, replay); self.assertEqual(workspace.status(args), first)
        repo = pathlib.Path(first["worktree"]); self.assertEqual((repo / "README.md").read_text(), "source\n")
        self.assertEqual(git("branch", "--show-current", cwd=repo), args.branch)
        self.assertEqual(pathlib.Path(pathlib.Path(repo / ".git/objects/info/alternates").read_text().strip()).resolve(), (mirror / "objects").resolve())
        self.assertEqual((pathlib.Path(first["operation_root"]) / "workspace.json").stat().st_mode & 0o777, 0o600)

    def test_dirty_drift_and_collision_fail_closed(self):
        args, _, _, _ = self.fixture(); value = workspace.prepare(args); repo = pathlib.Path(value["worktree"])
        (repo / "README.md").write_text("dirty\n")
        with self.assertRaisesRegex(ValueError, "dirty"): workspace.status(args)
        git("checkout", "--", "README.md", cwd=repo)
        changed = argparse.Namespace(**{**vars(args), "base_sha": "b" * 40})
        with self.assertRaises(ValueError): workspace.prepare(changed)

    def test_rejects_wrong_remote_branch_pin_and_root(self):
        args, _, mirror, work = self.fixture()
        with self.assertRaisesRegex(ValueError, "remote differs"): workspace.prepare(argparse.Namespace(**{**vars(args), "remote":"https://github.com/OTHER/widget.git"}))
        with self.assertRaisesRegex(ValueError, "branch differs"): workspace.prepare(argparse.Namespace(**{**vars(args), "branch":"hermes/bbbbbbbbbbbb-change"}))
        with self.assertRaisesRegex(ValueError, "pin differs"): workspace.prepare(argparse.Namespace(**{**vars(args), "base_sha":"b" * 40}))
        link = work.parent / "linked"; link.symlink_to(work, target_is_directory=True)
        with self.assertRaisesRegex(ValueError, "symlink"): workspace.safe_root(link)


if __name__ == "__main__": unittest.main()
