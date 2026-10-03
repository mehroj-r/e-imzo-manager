#!/usr/bin/env python3
"""Render the three release placeholders using validated, immutable inputs."""
import argparse
import os
import pathlib
import re
import tempfile

PATTERNS = {
    "RELEASE_TAG": r"v[0-9]+\.[0-9]+\.[0-9]+-arch\.[1-9][0-9]*",
    "MANAGER_PACKAGE": r"e-imzo-manager-[0-9]+\.[0-9]+\.[0-9]+-[1-9][0-9]*-x86_64\.pkg\.tar\.zst",
    "MANAGER_SHA256": r"[0-9a-f]{64}",
}


def render(template, values):
    if set(values) != set(PATTERNS):
        raise ValueError("Exactly the three supported release values are required")
    placeholders = set(re.findall(r"@([A-Z][A-Z0-9_]*)@", template))
    if placeholders != set(PATTERNS):
        raise ValueError(f"Unexpected or missing placeholders: {sorted(placeholders)}")
    for name, pattern in PATTERNS.items():
        value = values[name]
        if re.fullmatch(pattern, value) is None:
            raise ValueError(f"Invalid {name}")
        template = template.replace(f"@{name}@", value)
    tag_version = re.fullmatch(r"v([0-9]+\.[0-9]+\.[0-9]+)-arch\.([0-9]+)", values["RELEASE_TAG"])
    package_version = re.fullmatch(r"e-imzo-manager-([0-9]+\.[0-9]+\.[0-9]+)-([0-9]+)-x86_64\.pkg\.tar\.zst", values["MANAGER_PACKAGE"])
    if tag_version.groups() != package_version.groups():
        raise ValueError("Release tag and package version/revision must match")
    return template


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--template", required=True, type=pathlib.Path)
    parser.add_argument("--output", required=True, type=pathlib.Path)
    parser.add_argument("--release-tag", required=True)
    parser.add_argument("--manager-package", required=True)
    parser.add_argument("--manager-sha256", required=True)
    args = parser.parse_args()
    values = {"RELEASE_TAG": args.release_tag, "MANAGER_PACKAGE": args.manager_package, "MANAGER_SHA256": args.manager_sha256}
    result = render(args.template.read_text(), values)
    with tempfile.NamedTemporaryFile(mode="w", dir=args.output.parent, delete=False) as handle:
        path = pathlib.Path(handle.name)
        handle.write(result)
    try:
        path.chmod(0o755)
        os.replace(path, args.output)
    finally:
        path.unlink(missing_ok=True)


if __name__ == "__main__":
    main()
