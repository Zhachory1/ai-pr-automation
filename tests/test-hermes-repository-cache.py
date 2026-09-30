#!/usr/bin/env python3
import argparse, importlib.util, json, pathlib, subprocess, tempfile, time, unittest

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
        return root / "cache", upstream, seed

    @staticmethod
    def args(root, repository="ACME/widget", **values):
        return argparse.Namespace(root=root, repository=repository, remote=values.get("remote"), seed=values.get("seed"), max_age_seconds=values.get("max_age_seconds"))

    def test_seed_enroll_incremental_sync_and_status(self):
        root, upstream, seed = self.fixture(); args = self.args(root, remote=str(upstream), seed=seed)
        enrolled = cache.enroll(args)
        mirror = root / "ACME/widget.git"
        self.assertTrue(mirror.is_dir()); self.assertEqual(enrolled["head_sha"], cache.git(mirror, "rev-parse", "HEAD")); self.assertEqual(enrolled["default_branch"], "main")
        self.assertNotIn("secret", json.dumps(enrolled)); self.assertGreater(enrolled["size_bytes"], 0)

        (seed / "README.md").write_text("two\n"); git("add", "README.md", cwd=seed); git("commit", "-m", "two", cwd=seed); git("push", cwd=seed)
        updated = cache.sync(self.args(root)); self.assertNotEqual(updated["head_sha"], enrolled["head_sha"])
        self.assertEqual(cache.git(mirror, "show", f"{updated['head_sha']}:README.md"), "two")
        shown = cache.status(self.args(root, max_age_seconds=60)); self.assertFalse(shown["stale"]); self.assertGreaterEqual(shown["age_seconds"], 0)
        manifest = json.loads((root / "ACME/widget.json").read_text()); self.assertEqual(manifest["head_sha"], updated["head_sha"])

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

    def test_stale_status_and_unenrolled_failure(self):
        root, upstream, seed = self.fixture(); cache.enroll(self.args(root, remote=str(upstream), seed=seed))
        manifest = root / "ACME/widget.json"; value = json.loads(manifest.read_text()); value["fetched_at"] = int(time.time()) - 100; cache.atomic_json(manifest, value)
        self.assertTrue(cache.status(self.args(root, max_age_seconds=10))["stale"])
        with self.assertRaisesRegex(ValueError, "non-negative"): cache.status(self.args(root, max_age_seconds=-1))
        with self.assertRaisesRegex(ValueError, "not enrolled"): cache.status(self.args(root, repository="ACME/missing"))


if __name__ == "__main__": unittest.main()
