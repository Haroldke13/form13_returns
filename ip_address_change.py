#!/usr/bin/env python3
"""Replace old deployment IPs and render dynamic PBORA runtime env values."""

from __future__ import annotations

import argparse
import ipaddress
import os
import re
import shutil
import socket
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from urllib.parse import quote, urlparse


DEFAULT_OLD_IP = "192.0.2.10"
DEFAULT_NETWORK_INTERFACE = "eth0"
DEFAULT_HOST_POSTGRES_HOST = "auto"
DEFAULT_CONTAINER_POSTGRES_HOST = "auto"
BACKUP_DIR_NAME = ".ip_address_change_backups"
ENV_ASSIGNMENT_RE = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*)=(.*)$")
AUTO_VALUES = {"", "auto", "detect", "dynamic", "<server-ip>", "<server_ip>"}

SKIPPED_DIRS = {
    ".git",
    ".pytest_cache",
    BACKUP_DIR_NAME,
    "__pycache__",
    "backups",
}

BINARY_SUFFIXES = {
    ".bak",
    ".db",
    ".doc",
    ".docx",
    ".gif",
    ".jpeg",
    ".jpg",
    ".lock",
    ".mp3",
    ".mp4",
    ".pdf",
    ".png",
    ".pyc",
    ".sqlite",
    ".wav",
    ".xls",
    ".xlsm",
    ".xlsx",
    ".zip",
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Replace old static IPs or refresh PBORA Form 13 runtime "
            "environment values from the current server network."
        )
    )
    parser.add_argument(
        "new_ip",
        nargs="?",
        help="New IPv4 address. If omitted, the script prompts for it.",
    )
    parser.add_argument(
        "--old-ip",
        default=DEFAULT_OLD_IP,
        help=f"IPv4 address to replace. Default: {DEFAULT_OLD_IP}",
    )
    parser.add_argument(
        "--root",
        default=Path(__file__).resolve().parent,
        type=Path,
        help="Workspace root to scan. Default: this script's directory.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show files that would change without editing anything.",
    )
    parser.add_argument(
        "--include-backups",
        action="store_true",
        help="Also scan the repo's backups/ directory.",
    )
    parser.add_argument(
        "--no-file-backup",
        action="store_true",
        help="Do not copy changed files into .ip_address_change_backups first.",
    )
    parser.add_argument(
        "--render-runtime-env",
        action="store_true",
        help=(
            "Refresh .env.production with the current server IP, allowed hosts, "
            "and stable PostgreSQL host settings instead of replacing text globally."
        ),
    )
    parser.add_argument(
        "--env-file",
        default=None,
        type=Path,
        help="Environment file to render. Default: <root>/.env.production.",
    )
    parser.add_argument(
        "--app-host-ip",
        default=None,
        help="Override detected LAN IP for PUBLIC_BASE_URL and ALLOWED_HOSTS.",
    )
    parser.add_argument(
        "--network-interface",
        default=None,
        help=f"Network interface to prefer for app/Docker bind IP detection. Default: {DEFAULT_NETWORK_INTERFACE}.",
    )
    parser.add_argument(
        "--gateway-ip",
        default=None,
        help="Override detected default gateway for PBORA_GATEWAY.",
    )
    parser.add_argument(
        "--host-postgres-host",
        default=DEFAULT_HOST_POSTGRES_HOST,
        help=(
            "Host-side PostgreSQL address used by server scripts. "
            f"Default: {DEFAULT_HOST_POSTGRES_HOST}."
        ),
    )
    parser.add_argument(
        "--container-postgres-host",
        default=DEFAULT_CONTAINER_POSTGRES_HOST,
        help=(
            "Container-side PostgreSQL address used in SQLAlchemy URLs. "
            f"Default: {DEFAULT_CONTAINER_POSTGRES_HOST}."
        ),
    )
    return parser.parse_args()


def validate_ipv4(value: str, label: str) -> str:
    value = value.strip()
    try:
        address = ipaddress.ip_address(value)
    except ValueError as exc:
        raise ValueError(f"{label} must be a valid IPv4 address: {value!r}") from exc
    if address.version != 4:
        raise ValueError(f"{label} must be an IPv4 address, not IPv6: {value!r}")
    return value


def is_auto_value(value: str | None) -> bool:
    return str(value or "").strip().lower() in AUTO_VALUES


def split_env_assignment(line: str) -> tuple[str, str] | None:
    if line.lstrip().startswith("#"):
        return None
    match = ENV_ASSIGNMENT_RE.match(line)
    if not match:
        return None
    return match.group(1), match.group(2)


def unquote_env_value(value: str) -> str:
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
        return value[1:-1]
    return value


def env_map_from_text(env_text: str) -> dict[str, str]:
    env_values: dict[str, str] = {}
    for line in env_text.splitlines():
        parsed = split_env_assignment(line)
        if parsed is None:
            continue
        key, value = parsed
        env_values[key] = unquote_env_value(value)
    return env_values


def set_env_line(lines: list[str], key: str, value: str) -> None:
    replacement = f"{key}={value}"
    for index, line in enumerate(lines):
        parsed = split_env_assignment(line)
        if parsed is not None and parsed[0] == key:
            lines[index] = replacement
            return
    lines.append(replacement)


def csv_items(value: str | None) -> list[str]:
    return [item.strip() for item in str(value or "").split(",") if item.strip()]


def unique_items(items: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for item in items:
        if item in seen:
            continue
        seen.add(item)
        result.append(item)
    return result


def is_ip_literal(value: str) -> bool:
    try:
        ipaddress.ip_address(value)
    except ValueError:
        return False
    return True


def allowed_hosts_for_runtime(existing_value: str | None, app_host_ip: str) -> str:
    preserved_hosts: list[str] = []
    for host in csv_items(existing_value):
        if is_auto_value(host):
            continue
        if host in {"localhost", "127.0.0.1", app_host_ip}:
            continue
        if is_ip_literal(host):
            continue
        preserved_hosts.append(host)

    runtime_hosts = [host for host in runtime_ipv4_hosts() if host not in {"localhost", "127.0.0.1"}]
    return ",".join(unique_items([app_host_ip, *runtime_hosts, "localhost", "127.0.0.1", *preserved_hosts]))


def existing_public_scheme(env_values: dict[str, str]) -> str:
    public_base_url = env_values.get("PUBLIC_BASE_URL", "")
    if public_base_url and not is_auto_value(public_base_url):
        parsed = urlparse(public_base_url)
        if parsed.scheme in {"http", "https"}:
            return parsed.scheme
    preferred = env_values.get("PREFERRED_URL_SCHEME", "").strip().lower()
    if preferred in {"http", "https"}:
        return preferred
    return "http"


def explicit_public_base_url(env_values: dict[str, str]) -> str | None:
    public_base_url = (env_values.get("PUBLIC_BASE_URL") or "").strip().rstrip("/")
    if not public_base_url or is_auto_value(public_base_url):
        return None

    parsed = urlparse(public_base_url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return None

    hostname = parsed.hostname or ""
    if hostname in {"localhost", "127.0.0.1", "0.0.0.0"} or is_ip_literal(hostname):
        return None

    return public_base_url


def postgres_url_scheme(env_values: dict[str, str]) -> str:
    for key in ("INTERNAL_DATABASE_URL", "DATABASE_URL", "PBORA_DATABASE_URL"):
        parsed = urlparse(env_values.get(key, ""))
        if parsed.scheme.startswith("postgresql"):
            return parsed.scheme
    return "postgresql+psycopg2"


def build_postgres_url(env_values: dict[str, str], host: str) -> str:
    scheme = postgres_url_scheme(env_values)
    user = env_values.get("DB_USER") or env_values.get("POSTGRES_USER") or "pbora"
    password = env_values.get("DB_PASSWORD") or env_values.get("POSTGRES_PASSWORD") or "pbora"
    port = env_values.get("DB_PORT") or env_values.get("POSTGRES_PORT") or "5432"
    db_name = env_values.get("DB_NAME") or env_values.get("POSTGRES_DB") or "pbora"
    auth = quote(user, safe="")
    if password:
        auth = f"{auth}:{quote(password, safe='')}"
    return f"{scheme}://{auth}@{host}:{port}/{quote(db_name, safe='')}"


def public_base_url_for_runtime(env_values: dict[str, str], app_host_ip: str, host_port: str) -> str:
    explicit_url = explicit_public_base_url(env_values)
    if explicit_url:
        return explicit_url

    scheme = existing_public_scheme(env_values)
    public_port = (
        env_values.get("PUBLIC_HOST_PORT")
        or env_values.get("PUBLIC_PORT")
        or env_values.get("PUBLIC_BASE_URL_PORT")
        or host_port
    )
    normalized_port = str(public_port or "").strip().lower()
    if normalized_port in {"", "default", "none", "off"}:
        return f"{scheme}://{app_host_ip}"
    if (scheme == "http" and normalized_port == "80") or (scheme == "https" and normalized_port == "443"):
        return f"{scheme}://{app_host_ip}"
    return f"{scheme}://{app_host_ip}:{normalized_port}"


def render_runtime_env_text(
    env_text: str,
    *,
    app_host_ip: str,
    network_interface: str | None = DEFAULT_NETWORK_INTERFACE,
    gateway_ip: str | None = None,
    host_postgres_host: str = DEFAULT_HOST_POSTGRES_HOST,
    container_postgres_host: str = DEFAULT_CONTAINER_POSTGRES_HOST,
) -> str:
    app_host_ip = validate_ipv4(app_host_ip, "App host IP")
    if gateway_ip and not is_auto_value(gateway_ip):
        gateway_ip = validate_ipv4(gateway_ip, "Gateway IP")

    detected_host_ip = detect_primary_ipv4()
    if host_postgres_host == "auto" or is_auto_value(host_postgres_host) or host_postgres_host == "host.docker.internal":
        host_postgres_host = detected_host_ip or DEFAULT_HOST_POSTGRES_HOST
    if container_postgres_host == "auto" or is_auto_value(container_postgres_host) or container_postgres_host == "host.docker.internal":
        container_postgres_host = detected_host_ip or host_postgres_host

    env_values = env_map_from_text(env_text)
    host_port = env_values.get("HOST_PORT") or env_values.get("PORT") or "8000"
    db_port = env_values.get("DB_PORT") or env_values.get("POSTGRES_PORT") or "5432"
    public_scheme = existing_public_scheme(env_values)
    container_database_url = build_postgres_url(env_values, container_postgres_host)
    existing_gateway = env_values.get("PBORA_GATEWAY")
    selected_gateway = gateway_ip or (None if is_auto_value(existing_gateway) else existing_gateway)

    lines = env_text.splitlines()
    updates = {
        "APP_HOST": "0.0.0.0",
        "FLASK_RUN_HOST": "0.0.0.0",
        "PUBLIC_BASE_URL": public_base_url_for_runtime(env_values, app_host_ip, host_port),
        "ALLOWED_HOSTS": allowed_hosts_for_runtime(env_values.get("ALLOWED_HOSTS"), app_host_ip),
        "PBORA_NETWORK_INTERFACE": network_interface or DEFAULT_NETWORK_INTERFACE,
        "PBORA_APP_HOST_IP": app_host_ip,
        "DOCKER_BIND_IP": app_host_ip,
        "PBORA_POSTGRES_HOST": host_postgres_host,
        "PBORA_POSTGRES_PORT": db_port,
        "PBORA_DATABASE_URL": container_database_url,
        "DATABASE_DIALECT": env_values.get("DATABASE_DIALECT") or "postgresql",
        "INTERNAL_DATABASE_URL": container_database_url,
        "DATABASE_URL": container_database_url,
        "DB_HOST": host_postgres_host,
        "DB_PORT": db_port,
        "POSTGRES_HOST": host_postgres_host,
        "POSTGRES_PORT": db_port,
    }
    if selected_gateway:
        updates["PBORA_GATEWAY"] = selected_gateway
    if "ANNUAL_RETURNS_DATABASE_URL" in env_values:
        updates["ANNUAL_RETURNS_DATABASE_URL"] = container_database_url

    for key, value in updates.items():
        set_env_line(lines, key, value)

    return "\n".join(lines).rstrip("\n") + "\n"


def command_output(args: list[str]) -> str:
    try:
        completed = subprocess.run(args, check=False, text=True, capture_output=True)
    except OSError:
        return ""
    if completed.returncode != 0:
        return ""
    return completed.stdout.strip()


def interface_ipv4_hosts(interface_name: str | None = DEFAULT_NETWORK_INTERFACE) -> list[str]:
    if not interface_name or is_auto_value(interface_name):
        interface_name = DEFAULT_NETWORK_INTERFACE

    output = command_output(["ip", "-o", "-4", "addr", "show", "dev", interface_name])
    return re.findall(r"\binet\s+(\d+\.\d+\.\d+\.\d+)/", output)


def candidate_runtime_ips(network_interface: str | None = DEFAULT_NETWORK_INTERFACE) -> list[str]:
    candidates: list[str] = []
    candidates.extend(interface_ipv4_hosts(network_interface))

    route_output = command_output(["ip", "-4", "route", "get", "1.1.1.1"])
    route_match = re.search(r"\bsrc\s+(\d+\.\d+\.\d+\.\d+)", route_output)
    if route_match:
        candidates.append(route_match.group(1))

    hostname_ips = command_output(["hostname", "-I"])
    candidates.extend(hostname_ips.split())

    try:
        candidates.extend(socket.gethostbyname_ex(socket.gethostname())[2])
    except OSError:
        pass

    return unique_items(candidates)


def runtime_ipv4_hosts(network_interface: str | None = DEFAULT_NETWORK_INTERFACE) -> list[str]:
    hosts: list[str] = []
    for candidate in candidate_runtime_ips(network_interface):
        try:
            address = ipaddress.ip_address(candidate)
        except ValueError:
            continue
        if address.version == 4 and not address.is_loopback:
            hosts.append(candidate)
    return unique_items(hosts)


def detect_primary_ipv4(network_interface: str | None = DEFAULT_NETWORK_INTERFACE) -> str | None:
    fallback: str | None = None
    for candidate in candidate_runtime_ips(network_interface):
        try:
            address = ipaddress.ip_address(candidate)
        except ValueError:
            continue
        if address.version != 4 or address.is_loopback:
            continue
        if fallback is None:
            fallback = candidate
        if address.is_private:
            return candidate
    return fallback


def detect_default_gateway() -> str | None:
    route_output = command_output(["ip", "-4", "route", "show", "default"])
    route_match = re.search(r"\bdefault\s+via\s+(\d+\.\d+\.\d+\.\d+)", route_output)
    if route_match:
        return route_match.group(1)
    return None


def backup_single_file(root: Path, path: Path, original_data: bytes) -> Path:
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    backup_root = root / BACKUP_DIR_NAME / timestamp
    try:
        relative_path = path.relative_to(root)
    except ValueError:
        relative_path = Path(path.name)
    destination = backup_root / relative_path
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(original_data)
    (backup_root / "MANIFEST.txt").write_text(f"{relative_path}\n", encoding="utf-8")
    return backup_root


def render_runtime_env_file(
    env_file: Path,
    *,
    root: Path,
    app_host_ip: str | None,
    network_interface: str | None,
    gateway_ip: str | None,
    host_postgres_host: str,
    container_postgres_host: str,
    write_backup: bool,
) -> tuple[str, Path | None]:
    if not env_file.exists():
        raise FileNotFoundError(f"Environment file not found: {env_file}")

    original_data = env_file.read_bytes()
    env_text = original_data.decode("utf-8")
    env_values = env_map_from_text(env_text)
    selected_interface = network_interface or env_values.get("PBORA_NETWORK_INTERFACE") or DEFAULT_NETWORK_INTERFACE

    selected_ip = app_host_ip if app_host_ip and not is_auto_value(app_host_ip) else detect_primary_ipv4(selected_interface)
    if not selected_ip:
        raise RuntimeError("Could not detect the server IPv4 address. Pass --app-host-ip explicitly.")

    selected_gateway = gateway_ip if gateway_ip and not is_auto_value(gateway_ip) else detect_default_gateway()
    rendered = render_runtime_env_text(
        env_text,
        app_host_ip=selected_ip,
        network_interface=selected_interface,
        gateway_ip=selected_gateway,
        host_postgres_host=host_postgres_host,
        container_postgres_host=container_postgres_host,
    )
    backup_root = backup_single_file(root, env_file, original_data) if write_backup else None
    env_file.write_text(rendered, encoding="utf-8")
    return selected_ip, backup_root


def prompt_for_new_ip(old_ip: str) -> str:
    entered = input(f"Enter the new IP address to replace {old_ip}: ").strip()
    if not entered:
        raise ValueError("No new IP address entered.")
    return entered


def should_skip_file(path: Path, script_path: Path) -> bool:
    if path.resolve() == script_path:
        return True
    if path.suffix.lower() in BINARY_SUFFIXES:
        return True
    return False


def iter_candidate_files(root: Path, include_backups: bool, script_path: Path):
    skipped_dirs = set(SKIPPED_DIRS)
    if include_backups:
        skipped_dirs.discard("backups")

    for current_root, dir_names, file_names in os.walk(root):
        dir_names[:] = [
            name
            for name in dir_names
            if name not in skipped_dirs and not name.endswith(".egg-info")
        ]

        current_path = Path(current_root)
        for file_name in file_names:
            path = current_path / file_name
            if should_skip_file(path, script_path):
                continue
            yield path


def is_probably_binary(data: bytes) -> bool:
    if b"\0" in data:
        return True
    if not data:
        return False

    sample = data[:4096]
    textish = set(range(32, 127)) | {8, 9, 10, 12, 13, 27}
    non_text = sum(byte not in textish for byte in sample)
    return non_text / len(sample) > 0.30


def find_replacements(root: Path, old_ip: str, include_backups: bool, script_path: Path):
    old_bytes = old_ip.encode("ascii")
    replacements = []
    skipped_binary_hits = []

    for path in iter_candidate_files(root, include_backups, script_path):
        try:
            data = path.read_bytes()
        except OSError as exc:
            print(f"Skipping unreadable file: {path} ({exc})", file=sys.stderr)
            continue

        if old_bytes not in data:
            continue

        if is_probably_binary(data):
            skipped_binary_hits.append(path)
            continue

        replacements.append((path, data.count(old_bytes), data))

    return replacements, skipped_binary_hits


def write_backups(root: Path, replacements: list[tuple[Path, int, bytes]]) -> Path:
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    backup_root = root / BACKUP_DIR_NAME / timestamp
    for path, _, _ in replacements:
        relative_path = path.relative_to(root)
        destination = backup_root / relative_path
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, destination)

    manifest_path = backup_root / "MANIFEST.txt"
    manifest_path.write_text(
        "\n".join(str(path.relative_to(root)) for path, _, _ in replacements) + "\n",
        encoding="utf-8",
    )
    return backup_root


def apply_replacements(replacements: list[tuple[Path, int, bytes]], old_ip: str, new_ip: str) -> None:
    old_bytes = old_ip.encode("ascii")
    new_bytes = new_ip.encode("ascii")
    for path, _, original_data in replacements:
        path.write_bytes(original_data.replace(old_bytes, new_bytes))


def print_summary(root: Path, replacements: list[tuple[Path, int, bytes]], old_ip: str, new_ip: str) -> None:
    total_occurrences = sum(count for _, count, _ in replacements)
    print(f"Replacing {old_ip} -> {new_ip}")
    print(f"Files matched: {len(replacements)}")
    print(f"Occurrences matched: {total_occurrences}")
    for path, count, _ in replacements:
        print(f"  {path.relative_to(root)} ({count})")


def main() -> int:
    args = parse_args()
    root = args.root.resolve()
    script_path = Path(__file__).resolve()

    if args.render_runtime_env:
        env_file = args.env_file or (root / ".env.production")
        if not env_file.is_absolute():
            env_file = root / env_file
        try:
            selected_ip, backup_root = render_runtime_env_file(
                env_file=env_file.resolve(),
                root=root,
                app_host_ip=args.app_host_ip,
                network_interface=args.network_interface,
                gateway_ip=args.gateway_ip,
                host_postgres_host=args.host_postgres_host,
                container_postgres_host=args.container_postgres_host,
                write_backup=not args.no_file_backup,
            )
        except (FileNotFoundError, RuntimeError, ValueError, UnicodeDecodeError) as exc:
            print(f"Error: {exc}", file=sys.stderr)
            return 2

        print(f"Runtime environment rendered for server IP: {selected_ip}")
        print(f"Environment file updated: {env_file}")
        if backup_root:
            print(f"Backup written to: {backup_root.relative_to(root)}")
        return 0

    try:
        old_ip = validate_ipv4(args.old_ip, "Old IP address")
        new_ip = validate_ipv4(args.new_ip or prompt_for_new_ip(old_ip), "New IP address")
    except ValueError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 2

    if old_ip == new_ip:
        print("Nothing to change: old IP and new IP are the same.")
        return 0

    if not root.is_dir():
        print(f"Error: root directory does not exist: {root}", file=sys.stderr)
        return 2

    replacements, skipped_binary_hits = find_replacements(
        root=root,
        old_ip=old_ip,
        include_backups=args.include_backups,
        script_path=script_path,
    )

    if not replacements:
        print(f"No text-file occurrences of {old_ip} were found under {root}.")
        return 0

    print_summary(root, replacements, old_ip, new_ip)

    if skipped_binary_hits:
        print("Skipped binary-looking files that contain the old IP bytes:")
        for path in skipped_binary_hits:
            print(f"  {path.relative_to(root)}")

    if args.dry_run:
        print("Dry run only; no files were changed.")
        return 0

    backup_root = None
    if not args.no_file_backup:
        backup_root = write_backups(root, replacements)
        print(f"Backups written to: {backup_root.relative_to(root)}")

    apply_replacements(replacements, old_ip, new_ip)

    print("IP address replacement complete.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
