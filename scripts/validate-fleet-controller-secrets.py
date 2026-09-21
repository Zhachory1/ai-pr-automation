#!/usr/bin/env python3
import argparse
import json
import os
import ssl
import stat
import subprocess
from pathlib import Path

PRIVATE = (
    "FLEET_CONTROLLER_PASSWORD_FILE",
    "FLEET_CONTROLLER_SESSION_SECRET_FILE",
    "FLEET_CONTROLLER_TLS_KEY_FILE",
    "UI_BASIC_AUTH_FILE",
)
CERT = "FLEET_CONTROLLER_TLS_CERT_FILE"
CA_CERT = "FLEET_CONTROLLER_TLS_CA_CERT_FILE"


def fail(message):
    raise SystemExit(f"Fleet Controller preflight: {message}")


def compose_values(repo, env_file):
    command = ["docker", "compose", "--env-file", str(env_file), "-f", str(repo / "docker-compose.yml"),
               "config", "--format", "json"]
    try:
        result = subprocess.run(command, cwd=repo, capture_output=True, text=True, timeout=30)
        config = json.loads(result.stdout) if result.returncode == 0 else None
    except (OSError, subprocess.TimeoutExpired, json.JSONDecodeError):
        config = None
    if not isinstance(config, dict):
        fail("Docker Compose configuration is invalid")
    names = {
        "FLEET_CONTROLLER_PASSWORD_FILE": "fleet_controller_password",
        "FLEET_CONTROLLER_SESSION_SECRET_FILE": "fleet_controller_session_secret",
        "FLEET_CONTROLLER_TLS_KEY_FILE": "fleet_controller_tls_key",
        "UI_BASIC_AUTH_FILE": "ui_basic_auth",
        CERT: "fleet_controller_tls_cert",
        CA_CERT: "fleet_controller_tls_ca_cert",
    }
    try:
        values = {key: config["secrets"][name]["file"] for key, name in names.items()}
        values["CODE_ROOT"] = next(
            mount["source"] for mount in config["services"]["coderag"]["volumes"]
            if mount["target"] == "/code")
    except (KeyError, StopIteration, TypeError):
        fail("Docker Compose configuration omitted Fleet Controller paths")
    return values


def inside(path, root):
    return path == root or root in path.parents


def git_parent(path):
    return any((parent / ".git").exists() for parent in (path.parent, *path.parents))


def checked_file(name, value, private, repo, code_root):
    if not value:
        fail(f"set {name}")
    path = Path(os.path.expandvars(os.path.expanduser(value)))
    try:
        info = path.lstat()
    except OSError:
        fail(f"{name} is missing or unreadable")
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
        fail(f"{name} must be a regular non-symlink file")
    resolved = path.resolve()
    if inside(resolved, repo) or inside(resolved, code_root) or git_parent(resolved):
        fail(f"{name} must be outside CODE_ROOT and Git repositories")
    if info.st_uid not in {0, os.getuid()}:
        fail(f"{name} has wrong owner")
    mode = stat.S_IMODE(info.st_mode)
    if private and mode != 0o600:
        fail(f"{name} must have mode 0600")
    if not private and mode & 0o022:
        fail(f"{name} must not be group/world writable")
    if private:
        parent = resolved.parent.stat()
        if parent.st_uid not in {0, os.getuid()} or stat.S_IMODE(parent.st_mode) & 0o077:
            fail(f"{name} parent must be owner-only")
    return resolved


def checked_secret(name, path, minimum):
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(descriptor, "rb") as source:
        raw = source.read(4097)
    if len(raw) > 4096:
        fail(f"{name} exceeds 4096 bytes")
    try:
        value = raw.decode().strip()
    except UnicodeDecodeError:
        fail(f"{name} must be UTF-8")
    if len(value) < minimum:
        fail(f"{name} is too short")


def checked_certificate(name, path):
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(descriptor, "rb") as source:
        raw = source.read(131073)
    if (len(raw) > 131072 or raw.count(b"-----BEGIN CERTIFICATE-----") != 1
            or raw.count(b"-----END CERTIFICATE-----") != 1):
        fail(f"{name} must contain exactly one certificate")


def openssl(*arguments):
    try:
        result = subprocess.run(["openssl", *map(str, arguments)], capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.TimeoutExpired):
        fail("openssl certificate validation failed")
    return result


def extension(path, name):
    result = openssl("x509", "-in", path, "-noout", "-ext", name)
    lines = [line.strip() for line in result.stdout.splitlines() if line.strip()]
    if result.returncode or len(lines) != 2:
        fail(f"TLS certificate has invalid {name}")
    return lines[0], lines[1]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--env-file", type=Path, required=True)
    parser.add_argument("--repo", type=Path, required=True)
    args = parser.parse_args()
    repo = args.repo.resolve()
    values = compose_values(repo, args.env_file.resolve())
    code_root_raw = values.get("CODE_ROOT", "")
    if not code_root_raw:
        fail("set CODE_ROOT")
    code_root = Path(os.path.expandvars(os.path.expanduser(code_root_raw))).resolve()
    files = {name: checked_file(name, values.get(name, ""), True, repo, code_root) for name in PRIVATE}
    files[CERT] = checked_file(CERT, values.get(CERT, ""), False, repo, code_root)
    files[CA_CERT] = checked_file(CA_CERT, values.get(CA_CERT, ""), False, repo, code_root)
    checked_secret("FLEET_CONTROLLER_PASSWORD_FILE", files["FLEET_CONTROLLER_PASSWORD_FILE"], 16)
    checked_secret("FLEET_CONTROLLER_SESSION_SECRET_FILE", files["FLEET_CONTROLLER_SESSION_SECRET_FILE"], 32)
    descriptor = os.open(files["UI_BASIC_AUTH_FILE"], os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(descriptor) as source:
        auth_lines = [line.rstrip("\n") for line in source]
    if (len(auth_lines) != 1 or ":" not in auth_lines[0]
            or len(auth_lines[0].split(":", 1)[1]) < 20):
        fail("UI_BASIC_AUTH_FILE must contain one username:password-hash entry")
    checked_certificate(CERT, files[CERT])
    checked_certificate(CA_CERT, files[CA_CERT])

    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    try:
        context.load_cert_chain(files[CERT], files["FLEET_CONTROLLER_TLS_KEY_FILE"])
    except (OSError, ssl.SSLError):
        fail("TLS certificate and key do not match")
    if openssl("x509", "-in", files[CERT], "-noout", "-checkend", "86400").returncode:
        fail("TLS certificate expires within 24 hours")
    verify_flags = ("-trusted", files[CA_CERT], "-no-CAfile", "-no-CApath", "-no-CAstore", "-check_ss_sig")
    if openssl("verify", *verify_flags, files[CA_CERT]).returncode:
        fail("TLS CA certificate is not correctly self-signed")
    if openssl("verify", *verify_flags, "-purpose", "sslserver", files[CERT]).returncode:
        fail("TLS certificate is not issued by configured CA")

    leaf_basic = extension(files[CERT], "basicConstraints")
    leaf_usage = extension(files[CERT], "keyUsage")
    leaf_eku = extension(files[CERT], "extendedKeyUsage")
    leaf_san = extension(files[CERT], "subjectAltName")
    ca_basic = extension(files[CA_CERT], "basicConstraints")
    ca_usage = extension(files[CA_CERT], "keyUsage")
    if (not leaf_basic[0].endswith(": critical") or leaf_basic[1] != "CA:FALSE"
            or not leaf_usage[0].endswith(": critical")
            or leaf_usage[1] != "Digital Signature, Key Encipherment"
            or leaf_eku[1] != "TLS Web Server Authentication"
            or set(map(str.strip, leaf_san[1].split(","))) != {"DNS:localhost", "IP Address:127.0.0.1",
                "DNS:fleet.localhost", "DNS:hermes.localhost", "DNS:memory.localhost", "DNS:code.localhost"}
            or not ca_basic[0].endswith(": critical") or ca_basic[1] != "CA:TRUE, pathlen:0"
            or not ca_usage[0].endswith(": critical") or ca_usage[1] != "Certificate Sign, CRL Sign"):
        fail("TLS CA/leaf extensions do not match Fleet Controller policy")
    ca_identity = openssl("x509", "-in", files[CA_CERT], "-noout", "-subject", "-issuer")
    identities = [line.split("=", 1)[1].strip() for line in ca_identity.stdout.splitlines() if "=" in line]
    ca_fingerprint = openssl("x509", "-in", files[CA_CERT], "-noout", "-fingerprint", "-sha256").stdout
    leaf_fingerprint = openssl("x509", "-in", files[CERT], "-noout", "-fingerprint", "-sha256").stdout
    if ca_identity.returncode or len(identities) != 2 or identities[0] != identities[1]:
        fail("TLS CA certificate must be self-signed")
    if not ca_fingerprint or ca_fingerprint == leaf_fingerprint:
        fail("TLS CA and leaf certificates must differ")
    print("Fleet Controller secret preflight passed")


if __name__ == "__main__":
    main()
