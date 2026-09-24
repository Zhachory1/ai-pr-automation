#!/usr/bin/env python3
"""Report bridge state/Kanban mismatches without mutating either store."""
import importlib.machinery
import importlib.util
import json
import os
from pathlib import Path


def main():
    path = Path(os.environ.get("HERMES_KANBAN_BRIDGE_BIN",
        "/usr/local/libexec/ai-pr-automation/hermes-kanban-safety-bridge"))
    loader = importlib.machinery.SourceFileLoader("hermes_kanban_safety_bridge", str(path))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    module = importlib.util.module_from_spec(spec); loader.exec_module(module)
    args = module.arguments()
    report = module.build_bridge(args, read_only=True).reconcile_report()
    print(json.dumps(report, sort_keys=True, separators=(",", ":")))


if __name__ == "__main__": main()
