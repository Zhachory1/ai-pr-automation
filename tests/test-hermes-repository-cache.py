#!/usr/bin/env python3
import argparse, importlib.util, json, pathlib, subprocess, tempfile, time, unittest
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("repo_cache", ROOT / "scripts/hermes-repository-cache.py")
cache = importlib.util.module_from_spec(spec); spec.loader.exec_module(cache)


def git(*args, cwd=None):
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True)


class RepositoryCacheTest(unittest.TestCase):
    def fixture(self):
        temporary = tempfile.TemporaryDirectory(); self.addCleanup(temporary.cleanup)
        root = pathlib.Path(temporary.name); upstream = root / "upstream.git"; seed = root / "seed"
        git("init", "--bare", "--initial-branch=main", str(upstream))
        git("init", "--initial-branch=main", str(seed)); git("config", "user.email", "test@example.com", cwd=seed); git("config", "user.name", "Test", cwd=seed)
        (seed / "README.md").write_text("one\n"); git("add", "README.md", cwd=seed); git("commit", "-m", "one", cwd=seed)
        git("remote", "add", "origin", str(upstream), cwd=seed); git("push", "-u", "origin", "main", cwd=seed)
        git("checkout", "-b", "feature/local-only", cwd=seed); (seed / "feature.txt").write_text("local\n"); git("add", "feature.txt", cwd=seed); git("commit", "-m", "local feature", cwd=seed)
        git("branch", "wip/child", "main", cwd=seed)
        main_sha = subprocess.run(["git", "rev-parse", "main"], cwd=seed, check=True, capture_output=True, text=True).stdout.strip()
        git("--git-dir", str(upstream), "update-ref", "refs/heads/wip", main_sha)
        return root / "cache", upstream, seed

    @staticmethod
    def args(root, repository="ACME/widget", **values):
        return argparse.Namespace(root=root, repository=repository, remote=values.get("remote"), seed=values.get("seed"), max_age_seconds=values.get("max_age_seconds"))

    def test_seed_enroll_incremental_sync_and_status(self):
        root, upstream, seed = self.fixture(); args = self.args(root, remote=str(upstream), seed=seed)
        enrolled = cache.enroll(args)
        mirror = root / "ACME/widget.git"
        self.assertTrue(mirror.is_dir()); self.assertEqual(enrolled["head_sha"], cache.git(mirror, "rev-parse", "HEAD")); self.assertEqual(enrolled["default_branch"], "feature/local-only")
        self.assertEqual(enrolled["fetched_at"], 0); self.assertTrue(cache.status(self.args(root, max_age_seconds=60))["stale"])
        self.assertNotIn("secret", json.dumps(enrolled)); self.assertGreater(enrolled["size_bytes"], 0)

        git("checkout", "main", cwd=seed); (seed / "README.md").write_text("two\n"); git("add", "README.md", cwd=seed); git("commit", "-m", "two", cwd=seed); git("push", cwd=seed)
        updated = cache.sync(self.args(root)); self.assertNotEqual(updated["head_sha"], enrolled["head_sha"])
        self.assertEqual(updated["default_branch"], "main"); self.assertGreater(updated["fetched_at"], 0)
        self.assertEqual(cache.git(mirror, "show", f"{updated['head_sha']}:README.md"), "two")
        self.assertTrue(cache.git(mirror, "show-ref", "--verify", "refs/heads/wip")); self.assertNotIn("refs/heads/wip/child", cache.git(mirror, "show-ref")); self.assertNotIn("refs/remotes/", cache.git(mirror, "show-ref"))
        materialized = cache.materialize(self.args(root)); snapshot = pathlib.Path(materialized["snapshot"])
        self.assertEqual((snapshot / "README.md").read_text(), "two\n")
        self.assertEqual(snapshot.stat().st_mode & 0o777, 0o750)
        self.assertFalse((snapshot / "feature.txt").exists()); self.assertEqual(materialized["snapshot_sha"], updated["head_sha"])
        snapshot.chmod(0o700)
        self.assertEqual(cache.materialize(self.args(root))["snapshot"], str(snapshot)); self.assertEqual(snapshot.stat().st_mode & 0o777, 0o750)
        shown = cache.status(self.args(root, max_age_seconds=60)); self.assertFalse(shown["stale"]); self.assertTrue(shown["snapshot_ready"]); self.assertGreaterEqual(shown["age_seconds"], 0)
        manifest_path = root / "ACME/widget.json"; manifest = json.loads(manifest_path.read_text())
        self.assertEqual(manifest["head_sha"], updated["head_sha"]); self.assertEqual(manifest_path.stat().st_mode & 0o777, 0o640)

    def test_replay_and_remote_drift_fail_closed(self):
        root, upstream, seed = self.fixture(); args = self.args(root, remote=str(upstream), seed=seed)
        first = cache.enroll(args); replay = cache.enroll(args)
        self.assertEqual((replay["head_sha"], replay["fetched_at"]), (first["head_sha"], first["fetched_at"]))
        mirror = root / "ACME/widget.git"; cache.git(mirror, "remote", "set-url", "origin", str(root / "other.git"))
        with self.assertRaisesRegex(ValueError, "remote drift"):
            cache.sync(self.args(root))

    def test_rejects_bad_identity_symlink_root_and_credentials(self):
        with self.assertRaisesRegex(ValueError, "OWNER/REPO"): cache.identity("../repo")
        with self.assertRaisesRegex(ValueError, "credentials"): cache.validate_remote("https://user:secret@github.com/ACME/widget.git", "ACME/widget")
        with self.assertRaisesRegex(ValueError, "identity"): cache.validate_remote("git@github.com:OTHER/widget.git", "ACME/widget")
        temporary = tempfile.TemporaryDirectory(); self.addCleanup(temporary.cleanup); base = pathlib.Path(temporary.name)
        actual = base / "actual"; actual.mkdir(); link = base / "link"; link.symlink_to(actual, target_is_directory=True)
        with self.assertRaisesRegex(ValueError, "symlink"): cache.safe_root(link)

    def test_materialize_rejects_symlink_outside_snapshot(self):
        root, upstream, seed = self.fixture(); cache.enroll(self.args(root, remote=str(upstream), seed=seed))
        git("checkout", "main", cwd=seed); (seed / "escape").symlink_to("../../outside"); git("add", "escape", cwd=seed); git("commit", "-m", "unsafe link", cwd=seed); git("push", cwd=seed)
        cache.sync(self.args(root))
        with self.assertRaisesRegex(ValueError, "unsafe paths"): cache.materialize(self.args(root))

    def test_reader_group_requires_root_and_sets_effective_group(self):
        with mock.patch.object(cache.os, "geteuid", return_value=501):
            with self.assertRaisesRegex(ValueError, "requires root"): cache.configure_permissions("staff")
        group = argparse.Namespace(gr_gid=20)
        with mock.patch.object(cache.os, "geteuid", return_value=0), mock.patch.object(cache.grp, "getgrnam", return_value=group), mock.patch.object(cache.os, "setegid") as setegid, mock.patch.object(cache.os, "umask") as umask:
            cache.configure_permissions("staff")
        setegid.assert_called_once_with(20); umask.assert_called_once_with(0o027)

    def test_stale_status_and_unenrolled_failure(self):
        root, upstream, seed = self.fixture(); cache.enroll(self.args(root, remote=str(upstream), seed=seed))
        with self.assertRaisesRegex(ValueError, "synchronized"): cache.materialize(self.args(root))
        manifest = root / "ACME/widget.json"; value = json.loads(manifest.read_text()); value["fetched_at"] = int(time.time()) - 100; cache.atomic_json(manifest, value)
        self.assertTrue(cache.status(self.args(root, max_age_seconds=10))["stale"])
        with self.assertRaisesRegex(ValueError, "non-negative"): cache.status(self.args(root, max_age_seconds=-1))
        with self.assertRaisesRegex(ValueError, "not enrolled"): cache.status(self.args(root, repository="ACME/missing"))


if __name__ == "__main__": unittest.main()
