#!/usr/bin/env python3
"""Best-effort repair of the analyst's JSON result.

The analyst (an LLM) usually emits one clean JSON object on stdout, but on large
reviews it occasionally emits structurally-broken JSON around otherwise-correct
content: a duplicated key token (`{"operation_id":"operation_id":"..."`), prose
or code fences wrapping the object, or trailing junk after the closing brace.

This reads the raw result file, and:
  1. if it already parses as JSON, leaves it byte-for-byte unchanged (exit 0);
  2. otherwise applies a few conservative, order-preserving repairs and, ONLY if
     the result then parses, overwrites the file with the reparsed object (exit 0);
  3. if it still cannot be parsed, leaves the file unchanged (exit 1).

Repair can therefore only turn a would-be failure into a success; it never
alters a result that was already valid. It does not invent field values.
"""
import json
import re
import sys


def extract_outermost_object(text: str) -> str | None:
    """Return the substring from the first '{' to its matching '}', ignoring
    braces inside strings. Strips leading prose / code fences and trailing junk."""
    start = text.find("{")
    if start == -1:
        return None
    depth = 0
    in_str = False
    esc = False
    for i in range(start, len(text)):
        c = text[i]
        if in_str:
            if esc:
                esc = False
            elif c == "\\":
                esc = True
            elif c == '"':
                in_str = False
        else:
            if c == '"':
                in_str = True
            elif c == "{":
                depth += 1
            elif c == "}":
                depth -= 1
                if depth == 0:
                    return text[start : i + 1]
    return None


def repair(text: str) -> str:
    # Drop a duplicated key token: `"key":"key":` -> `"key":` (seen from luna).
    text = re.sub(r'("[A-Za-z0-9_]+"\s*:)\s*\1', r"\1", text)
    # Reduce to the outermost balanced object (strips code fences / prose / trailing junk).
    obj = extract_outermost_object(text)
    return obj if obj is not None else text


def main() -> int:
    path = sys.argv[1]
    with open(path, "r", encoding="utf-8", errors="replace") as fh:
        raw = fh.read()
    try:
        json.loads(raw)
        return 0  # already valid; leave untouched
    except json.JSONDecodeError:
        pass
    try:
        obj = json.loads(repair(raw))
    except (json.JSONDecodeError, ValueError):
        return 1  # unrepairable; leave original for the controller to reject
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(obj, fh, separators=(",", ":"))
        fh.write("\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
