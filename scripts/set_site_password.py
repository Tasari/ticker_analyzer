from __future__ import annotations

import argparse
import getpass
import secrets
from pathlib import Path

from ticker_analyzer.access_control import ACCESS_CONFIG_PATH, MINIMUM_PASSWORD_LENGTH, write_access_config


def main() -> None:
    parser = argparse.ArgumentParser(description="Replace the password hash used by the Streamlit access gate.")
    parser.add_argument("--generate", action="store_true", help="Generate and print a strong random password")
    parser.add_argument("--output", type=Path, default=ACCESS_CONFIG_PATH)
    args = parser.parse_args()

    if args.generate:
        password = secrets.token_urlsafe(18)
    else:
        password = getpass.getpass(f"New password ({MINIMUM_PASSWORD_LENGTH}+ characters): ")
        confirmation = getpass.getpass("Repeat password: ")
        if password != confirmation:
            parser.error("passwords do not match")

    try:
        write_access_config(password, args.output)
    except ValueError as exc:
        parser.error(str(exc))
    print(f"Password hash written to: {args.output.resolve()}")
    if args.generate:
        print(f"Generated password: {password}")


if __name__ == "__main__":
    main()
