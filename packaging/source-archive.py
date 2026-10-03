#!/usr/bin/env python3
"""Export reviewed source, never ignored build trees or backend payloads."""
import argparse
import io
import pathlib
import subprocess
import tarfile

PREFIX = "e-imzo-manager-1.3.0"
SOURCE_DIRS = {"src", "data", "po", "build-aux", "vendor", "tests", "packaging", ".github", "packages", "shells", ".forgejo", ".zed"}
SOURCE_FILES = {"Cargo.toml", "Cargo.lock", "LICENSE-AGPL", "LICENSE-CCBY", "README.md", "Flatpak.md", "meson.build", "meson_options.txt", "rust-toolchain.toml", "flake.nix", "flake.lock", "package.nix", "justfile", ".gitignore"}
FORBIDDEN_PARTS = {".git", ".ssh", ".gnupg", "target", "build", "_build", "builddir", "dist", "__pycache__", ".flatpak-builder", "node_modules", "cargo-home"}


def checked_path(name):
    path = pathlib.PurePosixPath(name)
    if path.is_absolute() or ".." in path.parts or not path.parts:
        raise ValueError(f"Unsafe source path: {name}")
    if any(part in FORBIDDEN_PARTS for part in path.parts):
        raise ValueError(f"Build/private data is not source: {name}")
    if path.parts[0] not in SOURCE_DIRS and name not in SOURCE_FILES:
        return False
    if path.parts[0] == "vendor" and (len(path.parts) < 2 or path.parts[1] != "e-imzo"):
        raise ValueError(f"Only the open-source SDK belongs in vendor: {name}")
    if path.suffix.lower() in {".jar", ".jks", ".p12", ".pfx", ".pem", ".key", ".pkg", ".log", ".tar", ".gz", ".xz", ".zst", ".zip", ".7z", ".bz2", ".deb", ".rpm", ".so", ".a", ".o"} or path.name.startswith(".env"):
        raise ValueError(f"Backend/private payload is not source: {name}")
    return True


def git(root, *args):
    return subprocess.check_output(["git", "-C", str(root), *args])


def export(root, output, working_tree=False):
    epoch = int(git(root, "show", "-s", "--format=%ct", "HEAD").strip())
    if working_tree:
        names = git(root, "ls-files", "--cached", "--others", "--exclude-standard", "-z").decode().split("\0")
        files = []
        for name in sorted(set(filter(None, names))):
            if not checked_path(name):
                continue
            path = root / name
            if path.is_symlink() or not path.is_file():
                raise ValueError(f"Source must be a regular file: {name}")
            files.append((name, path.read_bytes(), bool(path.stat().st_mode & 0o111)))
    else:
        if git(root, "status", "--porcelain", "--untracked-files=normal").strip():
            raise ValueError("Release sources require clean HEAD; use --working-tree only for local verification")
        files = []
        with tarfile.open(fileobj=io.BytesIO(git(root, "archive", "--format=tar", "HEAD"))) as archive:
            for member in archive:
                if member.isdir():
                    continue
                if not checked_path(member.name):
                    continue
                if not member.isfile():
                    raise ValueError(f"Source must be a regular file: {member.name}")
                files.append((member.name, archive.extractfile(member).read(), bool(member.mode & 0o111)))
    with subprocess.Popen(["zstd", "-q", "-19", "-T0", "-o", str(output)], stdin=subprocess.PIPE) as compressor:
        with tarfile.open(fileobj=compressor.stdin, mode="w|", format=tarfile.PAX_FORMAT) as archive:
            for name, content, executable in files:
                member = tarfile.TarInfo(f"{PREFIX}/{name}")
                member.size = len(content)
                member.mode = 0o755 if executable else 0o644
                member.mtime = epoch
                member.uid = member.gid = 0
                member.uname = member.gname = "root"
                archive.addfile(member, io.BytesIO(content))
        compressor.stdin.close()
        if compressor.wait():
            raise RuntimeError("Source compression failed")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=pathlib.Path)
    parser.add_argument("--working-tree", action="store_true", help="include reviewed, non-ignored local source edits; not for publication")
    args = parser.parse_args()
    root = pathlib.Path(__file__).resolve().parents[1]
    export(root, args.output, args.working_tree)


if __name__ == "__main__":
    main()
