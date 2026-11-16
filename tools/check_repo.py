"""Repository gate: every published package matches its listed checksum and its unpacked copy,
and no file looks like a secret or carries a personal email. Exit 0 when clean, 2 when not."""
from __future__ import annotations

import hashlib
import pathlib
import re
import sys
import zipfile

ROOT = pathlib.Path(__file__).resolve().parent.parent
SECRET = re.compile(
    r"-----BEGIN [A-Z ]*PRIVATE KEY-----|\bgh[pousr]_[A-Za-z0-9]{30,}|\bgithub_pat_[A-Za-z0-9_]{30,}"
    r"|\bsk-[A-Za-z0-9_-]{20,}|\bAKIA[0-9A-Z]{16}\b|\bxox[baprs]-[A-Za-z0-9-]{10,}"
    r"|[A-Za-z0-9._%+-]+@(?:gmail|yahoo|outlook|hotmail|icloud)\.com", re.I)
BLOCKED_NAMES = re.compile(r"(^|/)(\.env(\..*)?|.*\.pem|.*\.key|.*\.pyc)$")


def problems() -> list[str]:
    found: list[str] = []
    files = [p for p in ROOT.rglob("*") if p.is_file() and ".git" not in p.relative_to(ROOT).parts]
    for path in files:
        rel = path.relative_to(ROOT).as_posix()
        if BLOCKED_NAMES.search(rel):
            found.append(f"{rel}: this kind of file must not be published")
        if path.suffix != ".zip":
            for number, line in enumerate(path.read_text("utf-8", errors="replace").splitlines(), 1):
                if SECRET.search(line) and rel != "tools/check_repo.py":
                    found.append(f"{rel}:{number}: looks like a secret or a personal email")
    for package in sorted(ROOT.glob("*/*.zip")):
        rel = package.relative_to(ROOT).as_posix()
        digest = hashlib.sha256(package.read_bytes()).hexdigest()
        sums = package.parent / "SHA256SUMS"
        listed = sums.read_text().split() if sums.exists() else []
        if digest not in listed:
            found.append(f"{rel}: checksum {digest} is not in {sums.relative_to(ROOT).as_posix()}")
        install = package.parent / "INSTALL.md"
        if not install.exists() or digest not in install.read_text() or package.name not in install.read_text():
            found.append(f"{rel}: INSTALL.md does not name this file with its checksum")
        with zipfile.ZipFile(package) as archive:
            for item in archive.infolist():
                if item.is_dir():
                    continue
                inner = pathlib.PurePosixPath(item.filename)
                if inner.is_absolute() or ".." in inner.parts:
                    found.append(f"{rel}: unsafe path inside the package: {item.filename}")
                    continue
                copy = package.parent / "skill" / pathlib.PurePosixPath(*inner.parts[1:])
                if not copy.is_file() or copy.read_bytes() != archive.read(item):
                    found.append(f"{rel}: {item.filename} differs from the readable copy in skill/")
            packed = {pathlib.PurePosixPath(*pathlib.PurePosixPath(i.filename).parts[1:]).as_posix()
                      for i in archive.infolist() if not i.is_dir()}
        loose = {p.relative_to(package.parent / "skill").as_posix() for p in (package.parent / "skill").rglob("*") if p.is_file()}
        for extra in sorted(loose - packed):
            found.append(f"{rel}: skill/{extra} is not in the package")
    return found


if __name__ == "__main__":
    issues = problems()
    for issue in issues:
        print("FAIL", issue)
    print("Repository check:", "clean." if not issues else f"{len(issues)} problem(s).")
    sys.exit(2 if issues else 0)
