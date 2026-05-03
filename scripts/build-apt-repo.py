#!/usr/bin/env python3
"""Build a small static APT repository from GitHub release .deb assets."""

from __future__ import annotations

import argparse
import gzip
import hashlib
import html
import io
import json
import os
import shutil
import subprocess
import sys
import tarfile
import tempfile
import time
import urllib.request
from collections import OrderedDict
from dataclasses import dataclass
from email.utils import formatdate
from pathlib import Path
from typing import Iterable


HASHES = ("MD5sum", "SHA1", "SHA256", "SHA512")
CHECKSUM_FIELDS = {"Filename", "Size", *HASHES}


@dataclass(frozen=True)
class DebMetadata:
    fields: OrderedDict[str, str]
    filename: str
    size: int
    md5: str
    sha1: str
    sha256: str
    sha512: str


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file:
        for chunk in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def hash_file(path: Path, algorithm: str) -> str:
    digest = hashlib.new(algorithm)
    with path.open("rb") as file:
        for chunk in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def download(url: str, output: Path) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    request = urllib.request.Request(url, headers={"User-Agent": "daulet-packages/1.0"})
    with urllib.request.urlopen(request) as response, output.open("wb") as file:
        shutil.copyfileobj(response, file)


def resolve_asset_url(package: dict, asset: dict) -> str:
    if "url" in asset:
        return asset["url"]
    return (
        f"https://github.com/{package['repo']}/releases/download/"
        f"{package['tag']}/{asset['name']}"
    )


def read_ar_members(path: Path) -> dict[str, bytes]:
    data = path.read_bytes()
    if not data.startswith(b"!<arch>\n"):
        raise ValueError(f"{path} is not an ar archive")

    members: dict[str, bytes] = {}
    string_table = b""
    offset = 8
    while offset < len(data):
        header = data[offset : offset + 60]
        if len(header) != 60:
            raise ValueError(f"{path} has a truncated ar header")
        offset += 60

        raw_name = header[0:16].decode("utf-8").strip()
        size_text = header[48:58].decode("utf-8").strip()
        trailer = header[58:60]
        if trailer != b"`\n":
            raise ValueError(f"{path} has an invalid ar header trailer")
        size = int(size_text)
        body = data[offset : offset + size]
        offset += size + (size % 2)

        if raw_name == "//":
            string_table = body
            continue

        name = raw_name
        if raw_name.startswith("#1/"):
            name_len = int(raw_name[3:])
            name = body[:name_len].decode("utf-8")
            body = body[name_len:]
        elif raw_name.startswith("/") and raw_name[1:].isdigit() and string_table:
            table_offset = int(raw_name[1:])
            end = string_table.find(b"/\n", table_offset)
            if end == -1:
                end = len(string_table)
            name = string_table[table_offset:end].decode("utf-8")

        members[name.rstrip("/")] = body

    return members


def extract_control_text(path: Path) -> str:
    members = read_ar_members(path)
    control_name = next(
        (name for name in members if name.startswith("control.tar")),
        None,
    )
    if control_name is None:
        raise ValueError(f"{path} does not contain control.tar")

    control_data = members[control_name]
    tar_mode = "r:*"
    if control_name.endswith(".zst"):
        try:
            control_data = subprocess.run(
                ["zstd", "-dc"],
                input=control_data,
                check=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            ).stdout
        except FileNotFoundError as exc:
            raise ValueError(
                f"{path} uses zstd-compressed control data; install zstd"
            ) from exc
        tar_mode = "r:"

    with tarfile.open(fileobj=io.BytesIO(control_data), mode=tar_mode) as tar:
        control_member = next(
            (
                member
                for member in tar.getmembers()
                if member.name in {"control", "./control"}
            ),
            None,
        )
        if control_member is None:
            raise ValueError(f"{path} does not contain a Debian control file")
        extracted = tar.extractfile(control_member)
        if extracted is None:
            raise ValueError(f"{path} has an unreadable Debian control file")
        return extracted.read().decode("utf-8")


def parse_control(text: str) -> OrderedDict[str, str]:
    fields: OrderedDict[str, str] = OrderedDict()
    current_key: str | None = None
    current_lines: list[str] = []

    def flush() -> None:
        nonlocal current_key, current_lines
        if current_key is not None:
            fields[current_key] = "\n".join(current_lines)
        current_key = None
        current_lines = []

    for line in text.splitlines():
        if not line:
            continue
        if line.startswith((" ", "\t")):
            if current_key is None:
                raise ValueError("control file continuation without a field")
            current_lines.append(line)
            continue
        flush()
        if ":" not in line:
            raise ValueError(f"invalid control field: {line}")
        key, value = line.split(":", 1)
        current_key = key
        current_lines = [value.lstrip()]
    flush()

    return fields


def read_deb_metadata(path: Path, repo_filename: str) -> DebMetadata:
    fields = parse_control(extract_control_text(path))
    for required in ("Package", "Version", "Architecture"):
        if required not in fields:
            raise ValueError(f"{path} is missing required control field {required}")

    for field in CHECKSUM_FIELDS:
        fields.pop(field, None)

    return DebMetadata(
        fields=fields,
        filename=repo_filename,
        size=path.stat().st_size,
        md5=hash_file(path, "md5"),
        sha1=hash_file(path, "sha1"),
        sha256=hash_file(path, "sha256"),
        sha512=hash_file(path, "sha512"),
    )


def format_package_stanza(metadata: DebMetadata) -> str:
    lines: list[str] = []
    fields = OrderedDict(metadata.fields)
    fields["Filename"] = metadata.filename
    fields["Size"] = str(metadata.size)
    fields["MD5sum"] = metadata.md5
    fields["SHA1"] = metadata.sha1
    fields["SHA256"] = metadata.sha256
    fields["SHA512"] = metadata.sha512

    for key, value in fields.items():
        value_lines = value.splitlines() or [""]
        lines.append(f"{key}: {value_lines[0]}")
        lines.extend(value_lines[1:])
    return "\n".join(lines) + "\n"


def write_gzip(input_path: Path, output_path: Path) -> None:
    with input_path.open("rb") as src, output_path.open("wb") as raw:
        with gzip.GzipFile(filename="", mode="wb", fileobj=raw, mtime=0) as gz:
            shutil.copyfileobj(src, gz)


def release_checksum_lines(root: Path, algorithm: str) -> list[str]:
    lines: list[str] = []
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        if path.name in {"Release", "InRelease", "Release.gpg"}:
            continue
        rel = path.relative_to(root).as_posix()
        digest = hash_file(path, algorithm)
        lines.append(f" {digest} {path.stat().st_size:16d} {rel}")
    return lines


def write_release(manifest: dict, output: Path) -> None:
    suite = manifest["suite"]
    release_root = output / "dists" / suite
    fields = OrderedDict(
        [
            ("Origin", manifest["origin"]),
            ("Label", manifest["label"]),
            ("Suite", suite),
            ("Codename", manifest["codename"]),
            ("Date", formatdate(time.time(), usegmt=True)),
            ("Architectures", " ".join(manifest["architectures"])),
            ("Components", manifest["component"]),
            ("Description", manifest["description"]),
        ]
    )

    lines = [f"{key}: {value}" for key, value in fields.items()]
    for field_name, algorithm in (
        ("MD5Sum", "md5"),
        ("SHA1", "sha1"),
        ("SHA256", "sha256"),
        ("SHA512", "sha512"),
    ):
        lines.append(f"{field_name}:")
        lines.extend(release_checksum_lines(release_root, algorithm))

    (release_root / "Release").write_text("\n".join(lines) + "\n", encoding="utf-8")


def write_index(manifest: dict, output: Path, packages_by_arch: dict[str, list[DebMetadata]]) -> None:
    package_names = sorted({pkg["name"] for pkg in manifest["packages"]})
    rows = "\n".join(
        f"<li><code>{html.escape(name)}</code></li>" for name in package_names
    )
    arch_text = ", ".join(manifest["architectures"])
    index = f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{html.escape(manifest["label"])}</title>
  <style>
    body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; line-height: 1.5; margin: 2rem auto; max-width: 760px; padding: 0 1rem; }}
    code, pre {{ background: #f4f4f5; border-radius: 4px; }}
    code {{ padding: 0.1rem 0.25rem; }}
    pre {{ overflow-x: auto; padding: 1rem; }}
  </style>
</head>
<body>
  <h1>{html.escape(manifest["label"])}</h1>
  <p>{html.escape(manifest["description"])}. Architectures: {html.escape(arch_text)}.</p>
  <h2>Install</h2>
  <pre><code>sudo install -d -m 0755 /etc/apt/keyrings
curl -fsSL https://daulet.github.io/packages/daulet-archive-keyring.gpg | sudo tee /etc/apt/keyrings/daulet-archive-keyring.gpg &gt;/dev/null
echo "deb [signed-by=/etc/apt/keyrings/daulet-archive-keyring.gpg] https://daulet.github.io/packages stable main" | sudo tee /etc/apt/sources.list.d/daulet.list &gt;/dev/null
sudo apt update
sudo apt install mot</code></pre>
  <h2>Packages</h2>
  <ul>
    {rows}
  </ul>
</body>
</html>
"""
    output.joinpath("index.html").write_text(index, encoding="utf-8")


def build_repo(manifest: dict, output: Path, cache_dir: Path) -> None:
    if output.exists():
        shutil.rmtree(output)
    output.mkdir(parents=True)
    cache_dir.mkdir(parents=True, exist_ok=True)

    component = manifest["component"]
    suite = manifest["suite"]
    architectures = set(manifest["architectures"])
    packages_by_arch: dict[str, list[DebMetadata]] = {arch: [] for arch in architectures}

    for package in manifest["packages"]:
        package_name = package["name"]
        assets = package["assets"]
        pool_dir = output / "pool" / component / package_name[0] / package_name
        pool_dir.mkdir(parents=True, exist_ok=True)

        for arch, asset in assets.items():
            if arch not in architectures:
                raise ValueError(f"{package_name} has unsupported architecture {arch}")

            asset_name = asset["name"]
            url = resolve_asset_url(package, asset)
            cached = cache_dir / asset_name
            expected_sha = asset.get("sha256")
            if not cached.exists() or (expected_sha and sha256_file(cached) != expected_sha):
                print(f"downloading {url}", file=sys.stderr)
                download(url, cached)

            actual_sha = sha256_file(cached)
            if expected_sha and actual_sha != expected_sha:
                raise ValueError(
                    f"checksum mismatch for {asset_name}: expected {expected_sha}, got {actual_sha}"
                )

            repo_path = pool_dir / asset_name
            shutil.copy2(cached, repo_path)
            repo_filename = repo_path.relative_to(output).as_posix()
            metadata = read_deb_metadata(repo_path, repo_filename)
            actual_arch = metadata.fields["Architecture"]
            if actual_arch != arch:
                raise ValueError(
                    f"{asset_name} declared as {arch} but package control says {actual_arch}"
                )
            packages_by_arch[arch].append(metadata)

    for arch in sorted(architectures):
        binary_dir = output / "dists" / suite / component / f"binary-{arch}"
        binary_dir.mkdir(parents=True, exist_ok=True)
        packages_path = binary_dir / "Packages"
        stanzas = sorted(
            packages_by_arch[arch],
            key=lambda item: (item.fields["Package"], item.fields["Version"]),
        )
        packages_path.write_text(
            "\n".join(format_package_stanza(stanza) for stanza in stanzas),
            encoding="utf-8",
        )
        write_gzip(packages_path, binary_dir / "Packages.gz")

    write_release(manifest, output)
    write_index(manifest, output, packages_by_arch)


def load_manifest(path: Path) -> dict:
    with path.open("r", encoding="utf-8") as file:
        manifest = json.load(file)
    for field in (
        "origin",
        "label",
        "suite",
        "codename",
        "description",
        "architectures",
        "component",
        "packages",
    ):
        if field not in manifest:
            raise ValueError(f"manifest is missing {field}")
    return manifest


def parse_args(argv: Iterable[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", default="packages.json", type=Path)
    parser.add_argument("--output", default="site", type=Path)
    parser.add_argument("--cache-dir", default=".cache/debs", type=Path)
    return parser.parse_args(argv)


def main(argv: Iterable[str] = sys.argv[1:]) -> int:
    args = parse_args(argv)
    manifest = load_manifest(args.manifest)
    build_repo(manifest, args.output, args.cache_dir)
    print(f"built APT repository in {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
