#!/usr/bin/env python3
"""Mirrored immutable direct-PR records; typed layers bind later digests to predecessors."""
import contextlib, fcntl, hashlib, json, os, pathlib, re, stat
KINDS = {"pr-review", "pr-maintain"}; RECORDS = {"admission", "binding", "intent", "receipt", "quarantine", "disposition", "closure"}
SEQUENCED = {"intent", "receipt", "disposition"}
HEX40 = re.compile(r"[0-9a-f]{40}\Z"); HEX64 = re.compile(r"[0-9a-f]{64}\Z")
OPERATION = re.compile(r"(pr-review|pr-maintain)-[0-9a-f]{64}\Z"); LINEAGE = re.compile(r"lineage-[0-9a-f]{64}\Z"); PART = re.compile(r"[A-Za-z0-9._-]{1,100}\Z")
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
def _lineage_id(repo, pr):
    repo = _repo_pr(repo, pr)
    return "lineage-" + hashlib.sha256(f"{repo}#{pr}".encode()).hexdigest()
def canonical_identity(kind, repo, pr, head, feedback_digest=None):
    if kind not in KINDS: raise JournalError("invalid operation kind")
    repo = _repo_pr(repo, pr)
    if not isinstance(head, str) or not HEX40.fullmatch(head): raise JournalError("invalid head SHA")
    value = {"head": head, "kind": kind, "pr": pr, "repo": repo}
    if kind == "pr-maintain":
        if not isinstance(feedback_digest, str) or not HEX64.fullmatch(feedback_digest): raise JournalError("invalid feedback digest")
        value["feedback_digest"] = feedback_digest
    elif feedback_digest is not None: raise JournalError("review identity cannot include feedback digest")
    return _canonical(value)[:-1]
def identity(kind, repo, pr, head, feedback_digest=None):
    raw = canonical_identity(kind, repo, pr, head, feedback_digest=feedback_digest); value = json.loads(raw)
    value["operation_id"] = f"{kind}-{hashlib.sha256(raw).hexdigest()}"
    value["lineage_id"] = _lineage_id(value["repo"], pr)
    return value
def _valid_identity(value):
    try:
        fields = {"kind", "repo", "pr", "head", "operation_id", "lineage_id"}
        if value.get("kind") == "pr-maintain": fields.add("feedback_digest")
        return (isinstance(value, dict) and set(value) == fields
                and value == identity(value["kind"], value["repo"], value["pr"], value["head"],
                                      feedback_digest=value.get("feedback_digest")))
    except (AttributeError, JournalError, KeyError, TypeError): return False
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
    def _lineage_relative(self, lineage_id, round_number=None, operation_id=None):
        if not isinstance(lineage_id, str) or not LINEAGE.fullmatch(lineage_id): raise JournalError("invalid lineage path")
        base = pathlib.Path("lineages") / lineage_id
        if round_number is None and operation_id is None: return base / "floor.json"
        if (type(round_number) is not int or round_number not in range(1, 4)
                or not isinstance(operation_id, str) or not OPERATION.fullmatch(operation_id)
                or not operation_id.startswith("pr-maintain-")): raise JournalError("invalid lineage path")
        return base / "rounds" / f"{round_number}-{operation_id}.json"
    def _from_lineage_relative(self, relative):
        parts = relative.parts
        if len(parts) == 3 and parts[0] == "lineages" and parts[2] == "floor.json":
            result = ("floor", parts[1], None, None)
        elif len(parts) == 4 and parts[0] == "lineages" and parts[2] == "rounds":
            match = re.fullmatch(r"([1-3])-(pr-maintain-[0-9a-f]{64})\.json", parts[3])
            if not match: raise JournalError("invalid lineage path")
            result = ("round", parts[1], int(match.group(1)), match.group(2))
        else: raise JournalError("invalid lineage path")
        if self._lineage_relative(result[1], result[2], result[3]) != relative: raise JournalError("invalid lineage path")
        return result
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
        if (record_type == "admission" and payload["identity"]["kind"] == "pr-maintain"
                and (type(payload.get("round")) is not int or payload["round"] not in range(1, 4))):
            raise JournalError("maintenance admission requires round 1..3")
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
    def _decode_lineage(self, data, kind, lineage_id, round_number=None, operation_id=None):
        try:
            value = json.loads(data); payload = value.get("payload")
            if kind == "floor":
                repo = _repo_pr(payload.get("repo"), payload.get("pr")); feedback = payload.get("feedback_digests")
                if (set(payload) != {"repo", "pr", "lineage_id", "count", "feedback_digests", "provenance_digest"}
                        or repo != payload["repo"] or _lineage_id(repo, payload["pr"]) != lineage_id
                        or payload["lineage_id"] != lineage_id or type(payload["count"]) is not int
                        or not isinstance(feedback, list) or payload["count"] != len(feedback) or len(feedback) not in range(4)
                        or len(set(feedback)) != len(feedback) or any(not isinstance(item, str) or not HEX64.fullmatch(item) for item in feedback)
                        or not isinstance(payload["provenance_digest"], str)
                        or not HEX64.fullmatch(payload["provenance_digest"])): raise JournalError("invalid lineage floor")
                expected = {"lineage_id": lineage_id, "payload": payload, "previous_digest": None,
                            "schema_version": 1, "type": "floor"}
            else:
                operation = payload.get("identity")
                if (set(payload) != {"identity", "lineage_id", "round"} or not _valid_identity(operation)
                        or operation["kind"] != "pr-maintain" or operation["operation_id"] != operation_id
                        or operation["lineage_id"] != lineage_id or payload["lineage_id"] != lineage_id
                        or payload["round"] != round_number or not isinstance(value.get("previous_digest"), str)
                        or not HEX64.fullmatch(value["previous_digest"])): raise JournalError("invalid lineage round")
                expected = {"operation_id": operation_id, "payload": payload,
                            "previous_digest": value["previous_digest"], "schema_version": 1, "type": "round"}
        except (AttributeError, KeyError, TypeError, UnicodeDecodeError, json.JSONDecodeError, JournalError) as exc:
            raise JournalError("invalid lineage envelope") from exc
        if value != expected or data != _canonical(value): raise JournalError("invalid lineage envelope")
        return value
    def _valid_directory(self, relative):
        parts = relative.parts
        valid = (not parts or parts in {("operations",), ("lineages",)}
                 or (len(parts) == 2 and ((parts[0] == "operations" and OPERATION.fullmatch(parts[1]))
                                          or (parts[0] == "lineages" and LINEAGE.fullmatch(parts[1]))))
                 or (len(parts) == 3 and ((parts[0] == "operations" and OPERATION.fullmatch(parts[1])
                                           and parts[2] in {"effects", "dispositions"})
                                          or (parts[0] == "lineages" and LINEAGE.fullmatch(parts[1])
                                              and parts[2] == "rounds"))))
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
                data = self._read_file(path)
                if relative.parts[0] == "operations":
                    record_type, operation_id, sequence = self._from_relative(relative)
                    self._decode(data, record_type, operation_id, sequence)
                else:
                    kind, lineage_id, round_number, operation_id = self._from_lineage_relative(relative)
                    self._decode_lineage(data, kind, lineage_id, round_number, operation_id)
                records[relative] = data
        return records
    def _validate_chains(self, records):
        operations, admissions, reservations = {}, [], {}
        for relative, data in records.items():
            if relative.parts[0] != "operations": continue
            kind, operation, sequence = self._from_relative(relative); value = self._decode(data, kind, operation, sequence)
            operations.setdefault(operation, []).append((kind, sequence, hashlib.sha256(data).hexdigest(), value["previous_digest"]))
            identity = value["payload"]["identity"]
            if kind == "admission" and identity["kind"] == "pr-maintain":
                admissions.append((operation, _canonical(identity), identity["lineage_id"], value["payload"]["round"]))
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
        lineages = {}
        for relative, data in records.items():
            if relative.parts[0] != "lineages": continue
            kind, lineage_id, round_number, operation_id = self._from_lineage_relative(relative)
            value = self._decode_lineage(data, kind, lineage_id, round_number, operation_id)
            lineages.setdefault(lineage_id, []).append((kind, round_number, operation_id, data, value))
            if kind == "round":
                reservation = (operation_id, _canonical(value["payload"]["identity"]), lineage_id, round_number)
                reservations[reservation] = reservations.get(reservation, 0) + 1
        for entries in lineages.values():
            floors = [entry for entry in entries if entry[0] == "floor"]
            if len(floors) != 1: raise JournalError("missing or ambiguous lineage floor")
            floor = floors[0]; rounds = sorted((entry for entry in entries if entry[0] == "round"), key=lambda entry: entry[1])
            count = floor[4]["payload"]["count"]
            if ([entry[1] for entry in rounds] != list(range(count + 1, count + len(rounds) + 1))
                    or count + len(rounds) > 3 or len({entry[2] for entry in rounds}) != len(rounds)):
                raise JournalError("invalid lineage round sequence")
            previous = hashlib.sha256(floor[3]).hexdigest()
            for entry in rounds:
                if entry[4]["previous_digest"] != previous: raise JournalError("invalid lineage predecessor")
                previous = hashlib.sha256(entry[3]).hexdigest()
        if any(reservations.get(admission) != 1 for admission in admissions):
            raise JournalError("maintenance admission requires exactly one matching reservation")
    def _parity(self, allowed_missing=None, allowed_round=None):
        first, second = self._inventory(self.root), self._inventory(self.mirror); difference = set(first) ^ set(second)
        allowed = {allowed_missing} if allowed_missing else set()
        if allowed_round:
            for relative in difference:
                try: kind, lineage_id, _, operation_id = self._from_lineage_relative(relative)
                except JournalError: continue
                if (kind == "round" and operation_id == allowed_round["operation_id"]
                        and lineage_id == allowed_round["lineage_id"]): allowed.add(relative)
        if difference - allowed or len(difference & allowed) > 1: raise JournalError("journal mirror inventory differs")
        if any(first[path] != second[path] for path in set(first) & set(second)):
            raise JournalError("journal mirrors differ")
        if difference: self._validate_chains({**second, **first})
        else: self._validate_chains(first); self._validate_chains(second)
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
    def _commit(self, relative, data, copies):
        combined = {**copies[1], **copies[0], relative: data}; self._validate_chains(combined)
        for index, root in enumerate((self.root, self.mirror)):
            self._dir(root, *relative.parts[:-1]); path = root / relative
            if relative not in copies[index]: self._publish(path, data)
        after = self._parity()
        if after[0].get(relative) != data: raise JournalError("journal commit failed")
    def write(self, record_type, operation_id, payload, previous_digest=None, sequence=None):
        relative = self._relative(record_type, operation_id, sequence)
        data = _canonical(self._envelope(record_type, operation_id, payload, previous_digest, sequence))
        if len(data) > MAX_RECORD: raise JournalError("journal record exceeds 1 MiB")
        with self._locked():
            copies = self._parity(relative)
            existing = copies[0].get(relative) or copies[1].get(relative)
            if existing is not None and existing != data: raise JournalError("existing journal record differs")
            self._commit(relative, data, copies)
        return hashlib.sha256(data).hexdigest()
    def set_lineage_floor(self, repo, pr, feedback_digests, provenance_digest):
        repo = _repo_pr(repo, pr); lineage_id = _lineage_id(repo, pr)
        if not isinstance(feedback_digests, (list, tuple)): raise JournalError("invalid historical feedback digests")
        feedback_digests = list(feedback_digests)
        payload = {"repo": repo, "pr": pr, "lineage_id": lineage_id, "count": len(feedback_digests),
                   "feedback_digests": feedback_digests, "provenance_digest": provenance_digest}
        value = {"lineage_id": lineage_id, "payload": payload, "previous_digest": None,
                 "schema_version": 1, "type": "floor"}
        relative = self._lineage_relative(lineage_id); data = _canonical(value)
        self._decode_lineage(data, "floor", lineage_id)
        with self._locked():
            copies = self._parity(relative); existing = copies[0].get(relative) or copies[1].get(relative)
            if existing is not None and existing != data: raise JournalError("existing lineage floor differs")
            self._commit(relative, data, copies)
        return hashlib.sha256(data).hexdigest()
    def reserve_round(self, operation):
        if not _valid_identity(operation) or operation["kind"] != "pr-maintain": raise JournalError("invalid maintenance operation")
        lineage_id, operation_id = operation["lineage_id"], operation["operation_id"]
        with self._locked():
            copies = self._parity(allowed_round=operation); records = {**copies[1], **copies[0]}
            existing = []
            for relative, data in records.items():
                if relative.parts[0] != "lineages": continue
                kind, current_lineage, round_number, current_operation = self._from_lineage_relative(relative)
                if kind == "round" and current_operation == operation_id:
                    existing.append((relative, data, current_lineage, round_number))
            if existing:
                relative, data, current_lineage, round_number = existing[0]
                if current_lineage != lineage_id: raise JournalError("maintenance operation lineage differs")
                self._commit(relative, data, copies); return round_number
            floor_relative = self._lineage_relative(lineage_id)
            try: floor_data = records[floor_relative]
            except KeyError as exc: raise JournalError("missing lineage floor") from exc
            floor = self._decode_lineage(floor_data, "floor", lineage_id); rounds = []
            if operation["feedback_digest"] in floor["payload"]["feedback_digests"]:
                raise JournalError("feedback digest already reserved")
            for relative, data in records.items():
                if relative.parts[:2] != ("lineages", lineage_id): continue
                kind, _, round_number, current_operation = self._from_lineage_relative(relative)
                if kind != "round": continue
                reserved = self._decode_lineage(data, kind, lineage_id, round_number, current_operation)
                if reserved["payload"]["identity"]["feedback_digest"] == operation["feedback_digest"]:
                    raise JournalError("feedback digest already reserved")
                rounds.append((round_number, data))
            round_number = floor["payload"]["count"] + len(rounds) + 1
            if round_number > 3: raise JournalError("maintenance round limit exceeded")
            previous = hashlib.sha256(max(rounds)[1] if rounds else floor_data).hexdigest()
            relative = self._lineage_relative(lineage_id, round_number, operation_id)
            value = {"operation_id": operation_id,
                     "payload": {"identity": operation, "lineage_id": lineage_id, "round": round_number},
                     "previous_digest": previous, "schema_version": 1, "type": "round"}
            data = _canonical(value); self._decode_lineage(data, "round", lineage_id, round_number, operation_id)
            self._commit(relative, data, copies)
        return round_number
    def read(self, record_type, operation_id, sequence=None):
        relative = self._relative(record_type, operation_id, sequence)
        with self._locked():
            copies = self._parity()
            try: data = copies[0][relative]
            except KeyError as exc: raise JournalError("missing journal record") from exc
            return self._decode(data, record_type, operation_id, sequence)
