#!/usr/bin/env python3
"""Update packages.json with release .deb assets for one package."""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Iterable
from pathlib import Path


def parse_asset(value: str) -> tuple[str, dict[str, str]]:
    parts = value.split(":", 2)
    if len(parts) != 3:
        raise argparse.ArgumentTypeError(
            "asset must have the form ARCH:ASSET_NAME:SHA256"
        )
    arch, name, sha256 = parts
    if not arch or not name or not sha256:
        raise argparse.ArgumentTypeError(
            "asset must have non-empty ARCH, ASSET_NAME, and SHA256"
        )
    return arch, {"name": name, "sha256": sha256}


def parse_args(argv: Iterable[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", default="packages.json", type=Path)
    parser.add_argument("--name", required=True)
    parser.add_argument("--repo", required=True)
    parser.add_argument("--tag", required=True)
    parser.add_argument("--version", required=True)
    parser.add_argument("--asset", action="append", type=parse_asset, required=True)
    return parser.parse_args(argv)


def main(argv: Iterable[str] = sys.argv[1:]) -> int:
    args = parse_args(argv)

    with args.manifest.open("r", encoding="utf-8") as file:
        manifest = json.load(file)

    supported_architectures = set(manifest["architectures"])
    assets = dict(args.asset)
    unknown_architectures = sorted(set(assets) - supported_architectures)
    if unknown_architectures:
        raise SystemExit(
            f"unsupported architecture(s): {', '.join(unknown_architectures)}"
        )

    missing_architectures = sorted(supported_architectures - set(assets))
    if missing_architectures:
        raise SystemExit(
            f"missing asset(s) for architecture(s): {', '.join(missing_architectures)}"
        )

    packages = manifest.setdefault("packages", [])
    next_entry = {
        "name": args.name,
        "repo": args.repo,
        "tag": args.tag,
        "version": args.version,
        "assets": {arch: assets[arch] for arch in manifest["architectures"]},
    }

    for index, package in enumerate(packages):
        if package["name"] == args.name:
            packages[index] = next_entry
            break
    else:
        packages.append(next_entry)
        packages.sort(key=lambda package: package["name"])

    args.manifest.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    print(f"updated {args.name} to {args.tag} in {args.manifest}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

