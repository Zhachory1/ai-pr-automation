#!/usr/bin/env python3
"""Prepare user-owned Hermes profiles and inert LaunchAgent definitions; never start services."""
import argparse
import os
import plistlib
import shutil
import stat
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PUBLIC = ROOT / "agent-config/hermes/profiles"
SKILLS = ("design-workflow", "prd-workflow", "roadmap-workflow")
PRIVATE = (
    "council-architect-v2", "council-orchestrator-v2", "council-reliability-v2",
    "council-reviewer-v2", "council-security-v2", "design-write-v1", "mvp",
    "occams-razor", "orchestrator", "prd-write-v1", "product-pm", "reviewer",
    "roadmap-write-v1", "security-engineer", "site-reliability-engineer",
    "software-architect", "technical-architect", "vp-eng",
)
SOURCE_ONLY = {"orchestrator", "reviewer", "security-engineer",
               "site-reliability-engineer", "technical-architect"}
OLD_SHIM = "/usr/local/libexec/ai-pr-automation/hermes-memory-recall-shim"
OLD_COUNCIL = "/usr/local/libexec/ai-pr-automation/hermes-council-tools"


def owned_dir(path):
    info = path.lstat()
    if (not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid() or path.is_symlink()
            or stat.S_IMODE(info.st_mode) & 0o022):
        raise ValueError(f"not a user-owned, non-writable-by-others directory: {path}")


def prepare_dir(path):
    if not path.exists() and not path.is_symlink():
        path.mkdir(mode=0o700)
    owned_dir(path)


def profile_files(profile, source=None):
    owned_dir(profile)
    if stat.S_IMODE(profile.lstat().st_mode) != 0o700:
        raise ValueError(f"profile directory must be owner-only: {profile}")
    required = ("SOUL.md", "profile.yaml") if profile.name in SOURCE_ONLY and source is None else ("SOUL.md", "config.yaml")
    for name in (*required, ".env", "mcp.json"):
        path = profile / name
        if name not in required and not path.exists() and not path.is_symlink():
            continue
        info = path.lstat()
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_nlink != 1
                or not info.st_mode & stat.S_IRUSR or stat.S_IMODE(info.st_mode) & 0o077 or path.is_symlink()):
            raise ValueError(f"profile file must be owner-only: {path}")
    if profile.name in SOURCE_ONLY and source is None:
        owned_dir(profile / "skills")
    for name in ("config.yaml", "mcp.json"):
        path = profile / name
        if path.is_symlink():
            raise ValueError(f"unsafe profile file: {path}")
        if path.is_file() and any(helper in path.read_text() for helper in (OLD_SHIM, OLD_COUNCIL)):
            raise ValueError(f"profile still points at retired system helper: {path}")
    if source:
        for item in source.rglob("*"):
            # Templates are not installed runtime state; the planner's private .env is supplied separately.
            if item.name == ".env.example":
                continue
            if item.is_symlink():
                raise ValueError(f"unsafe public profile source: {item}")
            if item.is_file():
                installed = profile / item.relative_to(source)
                info = installed.lstat()
                expected = item.read_bytes().replace(OLD_SHIM.encode(), str(ROOT / "bin/hermes-memory-recall-shim").encode())
                if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_nlink != 1
                        or installed.is_symlink() or installed.read_bytes() != expected):
                    raise ValueError(f"public profile differs; inspect before changing: {installed}")


def install_public(hermes_home, hermes_bin):
    profiles = hermes_home / "profiles"
    prepare_dir(profiles)
    tool_dir = hermes_home / "bin"
    prepare_dir(tool_dir)
    council_tool = tool_dir / "hermes-council-tools"
    source_tool = ROOT / "bin/hermes-council-tools"
    if not council_tool.exists() and not council_tool.is_symlink():
        fd = os.open(council_tool, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o500)
        with os.fdopen(fd, "wb") as output:
            output.write(source_tool.read_bytes())
    verify_council_tool(council_tool)
    for source in sorted(PUBLIC.iterdir()):
        if not source.is_dir():
            continue
        target = profiles / source.name
        if target.exists() or target.is_symlink():
            profile_files(target, source)
            continue
        subprocess.run([str(hermes_bin), "profile", "create", source.name, "--no-skills", "--no-alias"], check=True)
        owned_dir(target)
        target.chmod(0o700)
        for item in sorted(source.rglob("*")):
            if item.name == ".env.example":
                continue
            if item.is_symlink():
                raise ValueError(f"refusing profile source link: {item}")
            output = target / item.relative_to(source)
            if item.is_dir():
                prepare_dir(output)
            elif item.is_file():
                content = item.read_bytes().replace(OLD_SHIM.encode(), str(ROOT / "bin/hermes-memory-recall-shim").encode())
                if output.is_symlink():
                    raise ValueError(f"refusing profile destination link: {output}")
                output.write_bytes(content)
                output.chmod(0o600)
        profile_files(target, source)


def verify_council_tool(path):
    info = path.lstat()
    if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_nlink != 1
            or stat.S_IMODE(info.st_mode) != 0o500 or path.is_symlink()
            or path.read_bytes() != (ROOT / "bin/hermes-council-tools").read_bytes()):
        raise ValueError(f"user-owned Council helper differs; inspect before changing: {path}")


def check_skill(hermes_home, name):
    source = ROOT / "agent-config/skills" / name / "SKILL.md"
    dest = hermes_home / "skills" / name
    owned_dir(dest)
    path = dest / "SKILL.md"
    info = path.lstat()
    if (stat.S_IMODE(dest.lstat().st_mode) != 0o700 or not stat.S_ISREG(info.st_mode)
            or path.is_symlink() or info.st_uid != os.getuid() or info.st_nlink != 1
            or stat.S_IMODE(info.st_mode) & 0o077 or path.read_bytes() != source.read_bytes()):
        raise ValueError(f"public workflow skill differs; inspect before changing: {path}")


def definitions(home, hermes_home, hermes_bin, signal_bin):
    logs = hermes_home / "logs"
    shared = {"HOME": str(home), "HERMES_HOME": str(hermes_home),
              "PATH": f"{hermes_bin.parent}:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin"}
    jobs = {
        "ai.hermes.dashboard": [str(hermes_bin), "dashboard", "--host", "127.0.0.1",
                                 "--port", "9119", "--no-open", "--isolated"],
        "ai.hermes.signal": [str(signal_bin), "--scrub-log", "--config",
                             str(home / ".local/share/signal-cli"), "daemon", "--http",
                             "127.0.0.1:18080", "--no-receive-stdout"],
        "com.example.ai-pr-automation-kanban-ingress": [sys.executable, "-B",
            str(ROOT / "scripts/hermes-kanban-ingress.py"), "--review-key",
            str(hermes_home / "secrets/pr-review-ingress-key"), "--maintain-key",
            str(hermes_home / "secrets/pr-maintain-ingress-key"), "--authority",
            str(hermes_home / "authority.yaml"), "--work",
            str(hermes_home / "kanban-admission"), "--hermes-home", str(hermes_home),
            "--hermes-bin", str(hermes_bin)],
    }
    return {label: {"Label": label, "ProgramArguments": argv,
                    "WorkingDirectory": str(hermes_home), "EnvironmentVariables": shared,
                    "RunAtLoad": False, "KeepAlive": {"SuccessfulExit": False}, "Umask": 0o077,
                    "StandardOutPath": str(logs / (label + ".out.log")),
                    "StandardErrorPath": str(logs / (label + ".err.log"))}
            for label, argv in jobs.items()}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--prepare", action="store_true", help="install missing public profiles and inert user LaunchAgents")
    args = parser.parse_args()
    home = Path.home()
    hermes_home = home / ".hermes"
    hermes_bin = hermes_home / "runtime-v0.21.5/venv/bin/hermes"
    signal_bin = shutil.which("signal-cli")
    if not signal_bin or not hermes_bin.is_file() or not os.access(hermes_bin, os.X_OK):
        raise ValueError("install pinned personal Hermes and signal-cli before preparing the fleet")
    version = subprocess.run([signal_bin, "--version"], capture_output=True, text=True, timeout=10, check=True)
    if version.stdout.strip() != "signal-cli 0.14.8":
        raise ValueError("signal-cli 0.14.8 required for this fleet")
    owned_dir(hermes_home)
    if stat.S_IMODE(hermes_home.lstat().st_mode) != 0o700:
        raise ValueError("~/.hermes must be owner-only (0700)")
    if args.prepare:
        for path in (hermes_home / "profiles", hermes_home / "logs", hermes_home / "kanban-admission"):
            prepare_dir(path)
        install_public(hermes_home, hermes_bin)
        prepare_dir(hermes_home / "skills")
        for name in SKILLS:
            dest = hermes_home / "skills" / name
            if not dest.exists() and not dest.is_symlink():
                dest.mkdir(mode=0o700)
                source = ROOT / "agent-config/skills" / name / "SKILL.md"
                (dest / "SKILL.md").write_bytes(source.read_bytes())
                (dest / "SKILL.md").chmod(0o600)
            check_skill(hermes_home, name)
    expected = definitions(home, hermes_home, hermes_bin, Path(signal_bin))
    agents = home / "Library/LaunchAgents"
    if args.prepare:
        prepare_dir(home / "Library")
        prepare_dir(agents)
    issues = []
    try:
        verify_council_tool(hermes_home / "bin/hermes-council-tools")
    except (OSError, ValueError) as error:
        issues.append(str(error))
    for name, data in expected.items():
        path = agents / (name + ".plist")
        encoded = plistlib.dumps(data)
        if path.exists() or path.is_symlink():
            if path.is_symlink() or not path.is_file() or path.stat().st_uid != os.getuid() or path.read_bytes() != encoded:
                issues.append(f"existing LaunchAgent differs; inspect, do not overwrite: {path}")
        elif args.prepare:
            with path.open("xb") as output:
                output.write(encoded)
            path.chmod(0o600)
        else:
            issues.append(f"missing LaunchAgent: {path}")
    if not args.prepare:
        config = hermes_home / "config.yaml"
        try:
            info = config.lstat()
            if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid()
                    or stat.S_IMODE(info.st_mode) != 0o600 or config.is_symlink()):
                raise ValueError("private Hermes config must be owner-only")
        except (OSError, ValueError) as error:
            issues.append(f"private Hermes config missing or unsafe: {error}")
    for name in SKILLS:
        try:
            check_skill(hermes_home, name)
        except (OSError, ValueError) as error:
            issues.append(f"{name}: {error}")
    pending_private = []
    for name in sorted({path.name for path in PUBLIC.iterdir() if path.is_dir()} | set(PRIVATE)):
        path = hermes_home / "profiles" / name
        if args.prepare and name in PRIVATE and not path.exists() and not path.is_symlink():
            pending_private.append(name)
            continue
        try:
            source = PUBLIC / name
            profile_files(path, source if source.is_dir() else None)
        except (OSError, ValueError) as error:
            issues.append(f"{name}: {error}")
    if issues:
        print("\n".join(issues), file=sys.stderr)
        raise SystemExit(1)
    if pending_private:
        print("Prepared without starting services; restore owner-supplied private profiles: "
              + ", ".join(pending_private))
    else:
        print("Personal profile inventory and LaunchAgents ready; no service was started")


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, subprocess.CalledProcessError) as error:
        raise SystemExit(f"Personal Hermes bootstrap: {error}")
