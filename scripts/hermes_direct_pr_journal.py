#!/usr/bin/env python3
"""Mirrored immutable direct-PR records; typed layers bind later digests to predecessors."""
import contextlib, fcntl, hashlib, json, os, pathlib, re, stat
KINDS = {"pr-review", "pr-maintain"}; RECORDS = {"admission", "binding", "intent", "receipt", "quarantine", "disposition", "closure"}
SEQUENCED = {"intent", "receipt", "disposition"}
HEX40 = re.compile(r"[0-9a-f]{40}\Z"); HEX64 = re.compile(r"[0-9a-f]{64}\Z")
OPERATION = re.compile(r"(pr-review|pr-maintain)-[0-9a-f]{64}\Z"); PART = re.compile(r"[A-Za-z0-9._-]{1,100}\Z")
TEMP = re.compile(r"\.tmp-[0-9]+-[0-9a-f]{16}\Z")
MAX_RECORD = 1024 * 1024
class JournalError(ValueError): pass
def _canonical(value):
    try:
        return (json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False) + "\n").encode()
    except (TypeError, ValueError) as exc:
        raise JournalError("invalid record value") from exc
def _repo_pr(repo, pr):
    parts = repo.split("/") if isinstance(repo, str) else []
    if len(parts) != 2 or not all(PART.fullmatch(x) and x not in {".", ".."} for x in parts): raise JournalError("invalid repository")
    if type(pr) is not int or pr <= 0:
        raise JournalError("invalid PR number")
    return repo.lower()
def canonical_identity(kind, repo, pr, head):
    if kind not in KINDS: raise JournalError("invalid operation kind")
    repo = _repo_pr(repo, pr)
    if not isinstance(head, str) or not HEX40.fullmatch(head): raise JournalError("invalid head SHA")
    return _canonical({"head": head, "kind": kind, "pr": pr, "repo": repo})[:-1]
def identity(kind, repo, pr, head):
    raw = canonical_identity(kind, repo, pr, head); value = json.loads(raw)
    value["operation_id"] = f"{kind}-{hashlib.sha256(raw).hexdigest()}"
    value["lineage_id"] = "lineage-" + hashlib.sha256(f"{value['repo']}#{pr}".encode()).hexdigest()
    return value
def _valid_identity(value):
    try:
        return (isinstance(value, dict) and set(value) == {"kind", "repo", "pr", "head", "operation_id", "lineage_id"}
                and value == identity(value["kind"], value["repo"], value["pr"], value["head"]))
    except (JournalError, KeyError, TypeError): return False
class Journal:
    def __init__(self, root, mirror, expected_uid=None, require_separate_device=True):
        self.uid = os.geteuid() if expected_uid is None else expected_uid
        self.separate = require_separate_device
        self.root, self.mirror = self._root(root), self._root(mirror)
        if os.path.samefile(self.root, self.mirror): raise JournalError("journal roots must differ")
        self.pins = {path: self._id(path) for path in (self.root, self.mirror)}
        self._verify_roots()
    def _fsync_dir(self, path):
        descriptor = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try: os.fsync(descriptor)
        finally: os.close(descriptor)
    def _root(self, value):
        path = pathlib.Path(value)
        if not path.is_absolute():
            raise JournalError("journal root must be absolute")
        try:
            os.mkdir(path, 0o700); self._fsync_dir(path.parent)
        except FileExistsError: pass
        self._safe(path, True, 0o700)
        return path
    def _safe(self, path, directory, mode):
        try: info = path.lstat()
        except OSError as exc: raise JournalError(f"unsafe journal path: {path}") from exc
        wanted = stat.S_ISDIR if directory else stat.S_ISREG
        if (not wanted(info.st_mode) or stat.S_IMODE(info.st_mode) != mode or info.st_uid != self.uid
                or (not directory and info.st_nlink != 1)):
            raise JournalError(f"unsafe journal path: {path}")
        return info
    def _id(self, path):
        info = self._safe(path, True, 0o700)
        return info.st_dev, info.st_ino
    def _verify_roots(self):
        current = {path: self._id(path) for path in (self.root, self.mirror)}
        if current != self.pins: raise JournalError("journal root identity changed")
        if self.separate and current[self.root][0] == current[self.mirror][0]:
            raise JournalError("journal roots must use separate devices")
    def _dir(self, root, *parts):
        self._verify_roots(); current = root
        for part in parts:
            current = current / part
            try:
                os.mkdir(current, 0o700); self._fsync_dir(current.parent)
            except FileExistsError: pass
            self._safe(current, True, 0o700)
    @contextlib.contextmanager
    def _locked(self):
        self._verify_roots(); descriptors = []
        try:
            for root in (self.root, self.mirror):
                descriptor = os.open(root / ".journal.lock", os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
                info = os.fstat(descriptor); descriptors.append((info.st_dev, info.st_ino, descriptor))
                if (not stat.S_ISREG(info.st_mode) or stat.S_IMODE(info.st_mode) != 0o600
                        or info.st_uid != self.uid or info.st_nlink != 1):
                    raise JournalError("unsafe journal lock")
            for _, _, descriptor in sorted(descriptors): fcntl.flock(descriptor, fcntl.LOCK_EX)
            self._verify_roots(); yield
        finally:
            for _, _, descriptor in descriptors: os.close(descriptor)
    def _relative(self, record_type, operation_id, sequence=None):
        if (record_type not in RECORDS or not isinstance(operation_id, str)
                or not OPERATION.fullmatch(operation_id)):
            raise JournalError("invalid record path")
        base = pathlib.Path("operations") / operation_id
        if record_type in SEQUENCED:
            if type(sequence) is not int or sequence <= 0: raise JournalError("record sequence must be positive")
        elif sequence is not None: raise JournalError("unexpected record sequence")
        if record_type in {"intent", "receipt"}: return base / "effects" / f"{sequence}-{record_type}.json"
        if record_type == "disposition": return base / "dispositions" / f"{sequence}.json"
        return base / f"{record_type}.json"
    def _from_relative(self, relative):
        parts = relative.parts
        if len(parts) == 3 and parts[0] == "operations" and parts[2].endswith(".json"):
            record_type, operation_id, sequence = parts[2][:-5], parts[1], None
        elif len(parts) == 4 and parts[:1] == ("operations",) and parts[2] == "effects":
            match = re.fullmatch(r"([1-9][0-9]*)-(intent|receipt)\.json", parts[3])
            if not match: raise JournalError("invalid record path")
            sequence, record_type, operation_id = int(match.group(1)), match.group(2), parts[1]
        elif len(parts) == 4 and parts[:1] == ("operations",) and parts[2] == "dispositions":
            match = re.fullmatch(r"([1-9][0-9]*)\.json", parts[3])
            if not match: raise JournalError("invalid record path")
            sequence, record_type, operation_id = int(match.group(1)), "disposition", parts[1]
        else: raise JournalError("invalid record path")
        if self._relative(record_type, operation_id, sequence) != relative: raise JournalError("invalid record path")
        return record_type, operation_id, sequence
    def path(self, record_type, operation_id, sequence=None, mirror=False):
        return (self.mirror if mirror else self.root) / self._relative(record_type, operation_id, sequence)
    def _read_file(self, path):
        try: descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
        except OSError as exc: raise JournalError(f"missing or unsafe journal file: {path}") from exc
        with os.fdopen(descriptor, "rb") as source:
            info = os.fstat(descriptor)
            if (not stat.S_ISREG(info.st_mode) or stat.S_IMODE(info.st_mode) != 0o600
                    or info.st_uid != self.uid or info.st_nlink != 1):
                raise JournalError(f"unsafe journal file: {path}")
            data = source.read(MAX_RECORD + 1)
        if len(data) > MAX_RECORD: raise JournalError("journal record exceeds 1 MiB")
        return data
    def _envelope(self, record_type, operation_id, payload, previous_digest, sequence=None):
        valid_previous = previous_digest is None if record_type == "admission" else (
            isinstance(previous_digest, str) and bool(HEX64.fullmatch(previous_digest)))
        if (not isinstance(payload, dict) or not valid_previous or not _valid_identity(payload.get("identity"))
                or payload["identity"]["operation_id"] != operation_id
                or (record_type in SEQUENCED and (type(payload.get("sequence")) is not int
                                                   or payload["sequence"] != sequence))):
            raise JournalError("invalid record envelope")
        return {"operation_id": operation_id, "payload": payload, "previous_digest": previous_digest,
                "record_type": record_type, "schema_version": 1}
    def _decode(self, data, record_type, operation_id, sequence=None):
        try:
            value = json.loads(data)
            expected = self._envelope(record_type, operation_id, value.get("payload"),
                                      value.get("previous_digest"), sequence)
        except (AttributeError, UnicodeDecodeError, json.JSONDecodeError, JournalError) as exc:
            raise JournalError("invalid journal envelope") from exc
        if value != expected or data != _canonical(value): raise JournalError("invalid journal envelope")
        return value
    def _valid_directory(self, relative):
        parts = relative.parts
        valid = (not parts or parts == ("operations",)
                 or (len(parts) == 2 and parts[0] == "operations" and OPERATION.fullmatch(parts[1]))
                 or (len(parts) == 3 and parts[0] == "operations" and OPERATION.fullmatch(parts[1])
                     and parts[2] in {"effects", "dispositions"}))
        if not valid: raise JournalError("invalid journal directory")
    def _inventory(self, root):
        records = {}
        def walk_error(exc): raise JournalError("journal inventory failed") from exc
        for directory, names, files in os.walk(root, followlinks=False, onerror=walk_error):
            parent = pathlib.Path(directory); relative_parent = parent.relative_to(root)
            self._valid_directory(relative_parent); self._safe(parent, True, 0o700)
            for name in names:
                path = parent / name; self._valid_directory(path.relative_to(root)); self._safe(path, True, 0o700)
            for name in files:
                path = parent / name; relative = path.relative_to(root)
                if relative == pathlib.Path(".journal.lock"): self._safe(path, False, 0o600); continue
                if TEMP.fullmatch(name): self._safe(path, False, 0o600); continue
                record_type, operation_id, sequence = self._from_relative(relative)
                data = self._read_file(path); self._decode(data, record_type, operation_id, sequence)
                records[relative] = data
        return records
    def _validate_chains(self, records):
        operations = {}
        for relative, data in records.items():
            kind, operation, sequence = self._from_relative(relative); value = self._decode(data, kind, operation, sequence)
            operations.setdefault(operation, []).append((kind, sequence, hashlib.sha256(data).hexdigest(), value["previous_digest"]))
        for entries in operations.values():
            roots = [index for index, entry in enumerate(entries) if entry[0] == "admission"]
            if len(roots) != 1: raise JournalError("missing or ambiguous journal admission predecessor root")
            parents = [None] * len(entries)
            for index, entry in enumerate(entries):
                if entry[0] == "admission": continue
                matches = [candidate for candidate, value in enumerate(entries) if value[2] == entry[3]]
                if len(matches) != 1: raise JournalError("missing or ambiguous journal predecessor")
                parents[index] = matches[0]
            if len(set(parents)) != len(entries): raise JournalError("journal predecessor has multiple children")
            order = [roots[0]]
            while order[-1] in parents: order.append(parents.index(order[-1]))
            if len(order) != len(entries): raise JournalError("journal record is unreachable from admission")
            kinds = [entries[index][0] for index in order]
            if any(kinds.count(kind) > 1 for kind in {"binding", "quarantine", "closure"}): raise JournalError("duplicate singleton journal record")
            if "intent" in kinds and ("binding" not in kinds or kinds.index("binding") > kinds.index("intent")): raise JournalError("binding must precede first intent")
            if "disposition" in kinds and ("quarantine" not in kinds or kinds.index("quarantine") > kinds.index("disposition")): raise JournalError("disposition requires prior quarantine")
            if "closure" in kinds and kinds[-1] != "closure": raise JournalError("journal closure must be current tip")
            intents = [entries[index][1] for index in order if entries[index][0] == "intent"]
            dispositions = [entries[index][1] for index in order if entries[index][0] == "disposition"]
            if intents != list(range(1, len(intents) + 1)): raise JournalError("invalid intent sequence")
            if dispositions != list(range(1, len(dispositions) + 1)): raise JournalError("invalid disposition sequence")
            intent_positions = {entries[index][1]: position for position, index in enumerate(order) if entries[index][0] == "intent"}
            receipts = [(position, entries[index][1]) for position, index in enumerate(order) if entries[index][0] == "receipt"]
            if (len({sequence for _, sequence in receipts}) != len(receipts)
                    or any(sequence not in intent_positions or intent_positions[sequence] >= position for position, sequence in receipts)):
                raise JournalError("invalid receipt sequence")
    def _parity(self, allowed_missing=None):
        first, second = self._inventory(self.root), self._inventory(self.mirror)
        if (set(first) ^ set(second)) - ({allowed_missing} if allowed_missing else set()):
            raise JournalError("journal mirror inventory differs")
        if any(first[path] != second[path] for path in set(first) & set(second)):
            raise JournalError("journal mirrors differ")
        self._validate_chains(first); self._validate_chains(second)
        return first, second
    def _cleanup_temps(self, parent):
        for path in parent.iterdir():
            if not TEMP.fullmatch(path.name): continue
            self._safe(path, False, 0o600); path.unlink()
    def _publish(self, path, data):
        self._cleanup_temps(path.parent)
        temporary = path.with_name(f".tmp-{os.getpid()}-{os.urandom(8).hex()}")
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        try:
            with os.fdopen(descriptor, "wb") as output:
                os.fchmod(descriptor, 0o600); output.write(data); output.flush(); os.fsync(descriptor)
            try: path.lstat()
            except FileNotFoundError: pass
            else: raise JournalError(f"existing journal record differs: {path}")
            os.rename(temporary, path); self._fsync_dir(path.parent)
        except BaseException:
            try: self._safe(temporary, False, 0o600); temporary.unlink()
            except (JournalError, FileNotFoundError): pass
            raise
    def write(self, record_type, operation_id, payload, previous_digest=None, sequence=None):
        relative = self._relative(record_type, operation_id, sequence)
        data = _canonical(self._envelope(record_type, operation_id, payload, previous_digest, sequence))
        if len(data) > MAX_RECORD: raise JournalError("journal record exceeds 1 MiB")
        with self._locked():
            copies = self._parity(relative); existing = copies[0].get(relative) or copies[1].get(relative)
            if existing is not None and existing != data: raise JournalError("existing journal record differs")
            combined = {**copies[1], **copies[0], relative: data}
            self._validate_chains(combined)
            for index, root in enumerate((self.root, self.mirror)):
                self._dir(root, *relative.parts[:-1]); path = root / relative
                if relative not in copies[index]: self._publish(path, data)
            after = self._parity()
            if after[0].get(relative) != data: raise JournalError("journal commit failed")
        return hashlib.sha256(data).hexdigest()
    def read(self, record_type, operation_id, sequence=None):
        relative = self._relative(record_type, operation_id, sequence)
        with self._locked():
            copies = self._parity()
            try: data = copies[0][relative]
            except KeyError as exc: raise JournalError("missing journal record") from exc
            return self._decode(data, record_type, operation_id, sequence)
