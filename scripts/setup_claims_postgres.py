"""Generate separate local demo credentials without overwriting existing files."""

import argparse
import json
import os
from pathlib import Path
import secrets
import shutil
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from auth import hash_password


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, default=Path(__file__).resolve().parents[1] / ".claims-local")
    parser.add_argument("--docker-runtime", type=Path, help="Copy only Docker-mounted files to a new private directory")
    args = parser.parse_args()
    root = args.output_dir
    if args.docker_runtime:
        # Preflight before creating a destination. Never overwrite another demo.
        names = ("admin-password", "reader-password", "init-claims.sql", "database-url-container", "users-container.json")
        contents = {name: (root / name).read_bytes() for name in names}
        args.docker_runtime.mkdir(mode=0o700)
        for name, data in contents.items():
            fd = os.open(args.docker_runtime / name, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o444)
            with os.fdopen(fd, "wb") as handle:
                handle.write(data)
        print(f"Docker runtime files prepared in {args.docker_runtime}; no credentials printed.")
        print("Set CLAIMS_RUNTIME_DIR to this directory for every Compose command.")
        return
    # Exclusive directory creation avoids accidental password rotation for an
    # already initialized PostgreSQL volume or replacement of local accounts.
    root.mkdir(mode=0o700)
    admin = secrets.token_urlsafe(24)
    reader = secrets.token_urlsafe(24)
    users = {name: secrets.token_urlsafe(24) for name in ("gandalf", "galadriel")}
    files = {
        "admin-password": admin,
        "reader-password": reader,
        "database-url": f"postgresql://claims_reader:{reader}@127.0.0.1:55438/claims_demo",
        "users.json": json.dumps({name: hash_password(password) for name, password in users.items()}),
        "access.json": json.dumps(users),
        "database-url-container": f"postgresql://claims_reader:{reader}@postgres:5432/claims_demo",
    }
    files["users-container.json"] = files["users.json"]
    for name, password in users.items():
        files[f"mcp-{name}.json"] = json.dumps({"username": name, "password": password})
    for name, password in users.items():
        files[f"mcp-{name}.json"] = json.dumps({"username": name, "password": password})
    for name, contents in files.items():
        # Docker's postgres user must read these two bind-mounted secrets.
        # The host directory remains owner-only (0700).
        mode = 0o444 if name in ("admin-password", "reader-password", "database-url-container", "users-container.json") else 0o600
        fd = os.open(root / name, os.O_WRONLY | os.O_CREAT | os.O_EXCL, mode)
        with os.fdopen(fd, "w") as handle:
            handle.write(contents + "\n")
    shutil.copyfile(Path(__file__).with_name("init_claims_postgres.sql"), root / "init-claims.sql")
    (root / "init-claims.sql").chmod(0o444)
    print("Local demo credentials created in .claims-local; no passwords printed.")
    print("Keep this directory together with the PostgreSQL volume. Reuse it on later starts.")


if __name__ == "__main__":
    main()
