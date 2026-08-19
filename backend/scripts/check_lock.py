"""Fail when requirements.lock no longer matches requirements.txt.

Introduced the moment the lockfile was: CI and the deploy image install from
the lock, so a change to requirements.txt on its own is tested by nothing at
all. Dependabot found this immediately -- it opened a pull request relaxing
`bcrypt<4.1`, a pin with a comment explaining that passlib reads an attribute
bcrypt removed in 4.1, and CI went green because the lock still held 4.0.1.
A green tick on a change nobody tested is worse than a red one.

So: every requirement declared in requirements.txt must be satisfied by the
version pinned in the lock. Change a range without regenerating, and this
says so, naming the package and both sides.

    python scripts/check_lock.py
"""

import re
import sys
from pathlib import Path

from packaging.requirements import Requirement
from packaging.version import InvalidVersion, Version

BACKEND = Path(__file__).resolve().parent.parent


def _declared(path: Path) -> list[Requirement]:
    out = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        try:
            out.append(Requirement(line))
        except Exception:
            print(f"could not read this line of {path.name}: {raw!r}")
            raise
    return out


def _locked(path: Path) -> dict[str, str]:
    pins = {}
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line or "==" not in line:
            continue
        name, version = line.split("==", 1)
        # Normalised the way pip does, so Package_Name and package-name match.
        pins[re.sub(r"[-_.]+", "-", name).strip().lower()] = version.strip()
    return pins


def main() -> int:
    declared = _declared(BACKEND / "requirements.txt")
    locked = _locked(BACKEND / "requirements.lock")

    problems = []
    for requirement in declared:
        key = re.sub(r"[-_.]+", "-", requirement.name).lower()
        pinned = locked.get(key)
        if pinned is None:
            problems.append(
                f"{requirement.name}: declared in requirements.txt, absent from the lock"
            )
            continue
        try:
            if not requirement.specifier.contains(Version(pinned), prereleases=True):
                problems.append(
                    f"{requirement.name}: lock has {pinned}, which does not satisfy '{requirement}'"
                )
        except InvalidVersion:
            problems.append(f"{requirement.name}: lock has an unreadable version {pinned!r}")

    if problems:
        print("requirements.lock does not match requirements.txt:\n")
        for problem in problems:
            print(f"  - {problem}")
        print(
            "\nThe lock is what CI and the deploy image install, so this change "
            "is currently tested by nothing.\nRegenerate it:\n"
            "    pip install -r requirements.txt\n"
            "    pip freeze > requirements.lock"
        )
        return 1

    print(f"requirements.lock satisfies all {len(declared)} declared requirements")
    return 0


if __name__ == "__main__":
    sys.exit(main())
