#!/usr/bin/env python3
import concurrent.futures, hashlib, json, os, pathlib, sys, tempfile, unittest
from unittest import mock
ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import hermes_direct_pr_journal as journal
class DirectPrJournalTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); base = pathlib.Path(self.temp.name)
        self.root, self.mirror = base / "root", base / "mirror"
        self.journal = journal.Journal(self.root, self.mirror, require_separate_device=False)
        self.operation = journal.identity("pr-maintain", "owner/repo", 7, "a" * 40)
    def tearDown(self): self.temp.cleanup()
    def payload(self, **values): return {"identity": self.operation, **values}
    def bind(self, operation=None):
        operation = operation or self.operation; operation_id = operation["operation_id"]
        admission = self.journal.write("admission", operation_id, {"identity": operation})
        return admission, self.journal.write("binding", operation_id, {"identity": operation}, admission)
    def test_identity_is_canonical_case_insensitive_and_rejects_unsafe_input(self):
        raw = b'{"head":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa","kind":"pr-maintain","pr":7,"repo":"owner/repo"}'
        alias = journal.identity("pr-maintain", "OWNER/Repo", 7, "a" * 40)
        self.assertEqual(journal.canonical_identity("pr-maintain", "Owner/REPO", 7, "a" * 40), raw)
        self.assertEqual(alias, self.operation); self.assertEqual(self.operation["operation_id"], "pr-maintain-" + hashlib.sha256(raw).hexdigest())
        self.assertEqual(self.operation["lineage_id"], "lineage-" + hashlib.sha256(b"owner/repo#7").hexdigest())
        for args in [("bad", "o/r", 1, "a" * 40), ("pr-review", "../r", 1, "a" * 40),
                     ("pr-review", "o/r", 0, "a" * 40), ("pr-review", "o/r", 1, "A" * 40),
                     ("pr-review", f"o/{'r' * 101}", 1, "a" * 40)]:
            with self.subTest(args=args), self.assertRaises(journal.JournalError): journal.identity(*args)
    def test_fixed_record_paths_and_path_rejection(self):
        operation_id = self.operation["operation_id"]
        expected = {"admission": "admission.json", "binding": "binding.json", "intent": "effects/2-intent.json", "receipt": "effects/2-receipt.json", "quarantine": "quarantine.json",
                    "disposition": "dispositions/2.json", "closure": "closure.json"}
        for kind, suffix in expected.items():
            sequence = 2 if kind in journal.SEQUENCED else None
            self.assertEqual(self.journal.path(kind, operation_id, sequence).relative_to(self.root).as_posix(),
                             f"operations/{operation_id}/{suffix}")
        for args in [("unknown", operation_id, None), ("intent", operation_id, 0), ("admission", "../x", None)]:
            with self.assertRaises(journal.JournalError): self.journal.path(*args)
    def test_write_replay_conflict_roundtrip_and_digest_rules(self):
        operation_id = self.operation["operation_id"]; digest = self.journal.write("admission", operation_id, self.payload())
        value = self.journal.read("admission", operation_id); data = self.journal.path("admission", operation_id).read_bytes()
        self.assertEqual(set(value), {"schema_version", "record_type", "operation_id", "previous_digest", "payload"})
        self.assertEqual(data, (json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n").encode())
        self.assertEqual(digest, hashlib.sha256(data).hexdigest())
        self.assertEqual(self.journal.write("admission", operation_id, self.payload()), digest)
        with self.assertRaisesRegex(journal.JournalError, "differs"): self.journal.write("admission", operation_id, self.payload(choice=True))
        for kind, previous in [("admission", "f" * 64), ("binding", None), ("binding", "bad"), ("binding", "f" * 64)]:
            with self.subTest(kind=kind), self.assertRaises(journal.JournalError): self.journal.write(kind, operation_id, self.payload(), previous)
        orphan = journal.identity("pr-review", "owner/repo", 8, "b" * 40)
        with self.assertRaisesRegex(journal.JournalError, "predecessor"): self.journal.write("binding", orphan["operation_id"], {"identity": orphan}, digest)
    def test_linear_chain_recovery_closure_and_complete_scan_validation(self):
        operation_id = self.operation["operation_id"]; _, binding = self.bind()
        intent1 = self.journal.write("intent", operation_id, self.payload(sequence=1), binding, 1); receipt1 = self.journal.write("receipt", operation_id, self.payload(sequence=1), intent1, 1)
        quarantine = self.journal.write("quarantine", operation_id, self.payload(), receipt1)
        with self.assertRaisesRegex(journal.JournalError, "predecessor"): self.journal.write("closure", operation_id, self.payload(), receipt1)
        disposition1 = self.journal.write("disposition", operation_id, self.payload(sequence=1), quarantine, 1)
        with self.assertRaisesRegex(journal.JournalError, "predecessor"): self.journal.write("intent", operation_id, self.payload(sequence=2), binding, 2)
        intent2 = self.journal.write("intent", operation_id, self.payload(sequence=2), disposition1, 2); disposition2 = self.journal.write("disposition", operation_id, self.payload(sequence=2), intent2, 2); receipt2 = self.journal.write("receipt", operation_id, self.payload(sequence=2), disposition2, 2)
        closure = self.journal.write("closure", operation_id, self.payload(), receipt2)
        with self.assertRaisesRegex(journal.JournalError, "closure"): self.journal.write("intent", operation_id, self.payload(sequence=3), closure, 3)
        path = self.journal.path("disposition", operation_id, 2); value = json.loads(path.read_bytes())
        value["previous_digest"] = quarantine; changed = (json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n").encode()
        for mirror in (False, True): self.journal.path("disposition", operation_id, 2, mirror=mirror).write_bytes(changed)
        with self.assertRaisesRegex(journal.JournalError, "predecessor"): self.journal.read("closure", operation_id)
        other = journal.identity("pr-review", "owner/repo", 8, "b" * 40)
        with self.assertRaisesRegex(journal.JournalError, "predecessor"): self.journal.write("admission", other["operation_id"], {"identity": other})
        self.assertFalse(self.journal.path("admission", other["operation_id"]).exists())
    def test_identity_and_sequence_payload_validation(self):
        operation_id = self.operation["operation_id"]; other = journal.identity("pr-maintain", "owner/other", 7, "b" * 40)
        for payload in [{}, {"identity": other}, {"identity": {**self.operation, "repo": "OWNER/repo"}}]:
            with self.assertRaises(journal.JournalError): self.journal.write("binding", operation_id, payload, "f" * 64)
        for payload in [self.payload(), self.payload(sequence=1), self.payload(sequence=0), self.payload(sequence=True)]:
            with self.assertRaises(journal.JournalError): self.journal.write("intent", operation_id, payload, "f" * 64, 2)
        _, binding = self.bind(); intent = self.journal.write("intent", operation_id, self.payload(sequence=1), binding, 1)
        receipt = self.journal.write("receipt", operation_id, self.payload(sequence=1), intent, 1)
        self.journal.write("intent", operation_id, self.payload(sequence=2), receipt, 2)
    def test_partial_mirror_replay_and_read_fail_closed(self):
        operation_id = self.operation["operation_id"]; admission, _ = self.bind()
        mirror = self.journal.path("binding", operation_id, mirror=True); mirror.unlink()
        with self.assertRaises(journal.JournalError): self.journal.read("binding", operation_id)
        self.journal.write("binding", operation_id, self.payload(), admission)
        self.assertEqual(self.journal.read("binding", operation_id)["payload"], self.payload())
        mirror.write_bytes(b"{}\n")
        with self.assertRaises(journal.JournalError): self.journal.read("binding", operation_id)
    def test_unrelated_mirror_corruption_blocks_write(self):
        operation_id = self.operation["operation_id"]; self.journal.write("admission", operation_id, self.payload())
        self.journal.path("admission", operation_id, mirror=True).write_bytes(b"{}\n")
        other = journal.identity("pr-review", "owner/repo", 8, "b" * 40)
        with self.assertRaises(journal.JournalError): self.journal.write("admission", other["operation_id"], {"identity": other})
        self.assertFalse(self.journal.path("admission", other["operation_id"]).exists())
    def test_atomic_publish_failure_leaves_no_final_and_retries(self):
        operation_id = self.operation["operation_id"]
        with mock.patch.object(journal.os, "rename", side_effect=OSError("crash")):
            with self.assertRaises(OSError): self.journal.write("admission", operation_id, self.payload())
        self.assertFalse(self.journal.path("admission", operation_id).exists())
        self.assertFalse(self.journal.path("admission", operation_id, mirror=True).exists())
        self.journal.write("admission", operation_id, self.payload()); self.assertEqual(self.journal.read("admission", operation_id)["payload"], self.payload())
    def test_reversed_roots_serialize_conflict_without_divergence(self):
        reverse = journal.Journal(self.mirror, self.root, require_separate_device=False); operation_id = self.operation["operation_id"]
        def attempt(store, choice):
            try: return store.write("admission", operation_id, self.payload(choice=choice))
            except journal.JournalError: return None
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda args: attempt(*args), [(self.journal, 1), (reverse, 2)]))
        self.assertEqual(sum(result is not None for result in results), 1)
        self.assertEqual(self.journal.path("admission", operation_id).read_bytes(), self.journal.path("admission", operation_id, mirror=True).read_bytes())
    def test_unsafe_roots_files_and_root_substitution_are_rejected(self):
        base = pathlib.Path(self.temp.name); bad, other = base / "bad", base / "other"
        bad.mkdir(mode=0o755); other.mkdir(mode=0o700)
        with self.assertRaises(journal.JournalError): journal.Journal(bad, other, require_separate_device=False)
        bad.chmod(0o700)
        with self.assertRaises(journal.JournalError): journal.Journal(bad, other, expected_uid=os.geteuid() + 1, require_separate_device=False)
        link = base / "link"; link.symlink_to(bad, target_is_directory=True)
        with self.assertRaises(journal.JournalError): journal.Journal(link, other, require_separate_device=False)
        operation_id = self.operation["operation_id"]; _, binding = self.bind(); self.journal.write("closure", operation_id, self.payload(), binding)
        record = self.journal.path("closure", operation_id); record.chmod(0o644)
        with self.assertRaises(journal.JournalError): self.journal.read("closure", operation_id)
        record.chmod(0o600); alias = record.with_name("linked.json"); os.link(record, alias)
        with self.assertRaises(journal.JournalError): self.journal.read("closure", operation_id)
        alias.unlink(); moved = base / "moved"; self.root.rename(moved); self.root.mkdir(mode=0o700)
        with self.assertRaisesRegex(journal.JournalError, "identity changed"): self.journal.write("admission", operation_id, self.payload())
    def test_inventory_rejects_unknown_dirs_unsafe_temps_and_walk_errors(self):
        unknown = self.root / "unknown"; unknown.mkdir(mode=0o700)
        with self.assertRaisesRegex(journal.JournalError, "directory"): self.journal.read("admission", self.operation["operation_id"])
        unknown.rmdir(); temporary = self.root / ".tmp-1-0123456789abcdef"
        temporary.write_bytes(b"partial"); temporary.chmod(0o644)
        with self.assertRaisesRegex(journal.JournalError, "unsafe"): self.journal._cleanup_temps(self.root)
        with self.assertRaisesRegex(journal.JournalError, "unsafe"): self.journal.read("admission", self.operation["operation_id"])
        temporary.unlink(); temporary.touch(mode=0o600); self.journal.write("admission", self.operation["operation_id"], self.payload()); temporary.unlink()
        def broken_walk(*args, **kwargs): kwargs["onerror"](OSError("walk failed")); return ()
        with mock.patch.object(journal.os, "walk", side_effect=broken_walk):
            with self.assertRaisesRegex(journal.JournalError, "inventory failed"): self.journal.read("admission", self.operation["operation_id"])
    def test_device_pin_same_device_and_mkdir_fsync(self):
        base = pathlib.Path(self.temp.name)
        with self.assertRaisesRegex(journal.JournalError, "separate devices"): journal.Journal(base / "one", base / "two")
        original = self.journal._id
        with mock.patch.object(self.journal, "_id", side_effect=lambda path: (original(path)[0] + 1, original(path)[1])):
            with self.assertRaisesRegex(journal.JournalError, "identity changed"): self.journal.read("admission", self.operation["operation_id"])
        other = journal.identity("pr-review", "owner/repo", 9, "c" * 40)
        with mock.patch.object(self.journal, "_fsync_dir", wraps=self.journal._fsync_dir) as sync: self.journal.write("admission", other["operation_id"], {"identity": other})
        self.assertGreaterEqual(sync.call_count, 6)
if __name__ == "__main__": unittest.main()
