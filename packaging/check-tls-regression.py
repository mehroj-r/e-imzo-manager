#!/usr/bin/env python3
"""Require the production TLS regression to reject the original host:port bug."""
import argparse
import pathlib
import re
import shutil
import subprocess
import tempfile

FIXED = "tls.connect(url.host_str().unwrap(), tcp_stream)?"
ORIGINAL = "tls.connect(remote_addr.as_str(), tcp_stream)?"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=pathlib.Path)
    parser.add_argument("target", type=pathlib.Path)
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix="manager-tls-mutation-") as directory:
        source = pathlib.Path(directory) / "source"
        shutil.copytree(args.source, source, ignore=shutil.ignore_patterns("build", "target", ".git", "__pycache__", "dist", "cargo-vendor"))
        client = source / "vendor/e-imzo/src/client.rs"
        original = client.read_text()
        if original.count(FIXED) != 1:
            raise SystemExit("Expected exactly one production hostname-only TLS call")
        client.write_text(original.replace(FIXED, ORIGINAL))
        result = subprocess.run(
            ["cargo", "test", "--locked", "--release", "--target-dir", str(args.target.resolve()), "--test", "tls_server_name", "--", "--nocapture"],
            cwd=source, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, timeout=300,
        )
        # A compiler/network failure is not proof that the regression catches TLS.
        if result.returncode != 101 or not re.search(r"test result: FAILED\.", result.stdout) or "could not compile" in result.stdout:
            print(result.stdout)
            raise SystemExit("Restoring the original TLS argument did not produce the required test failure")
    print("TLS mutation gate passed: the production test rejects the original host:port argument")


if __name__ == "__main__":
    main()
