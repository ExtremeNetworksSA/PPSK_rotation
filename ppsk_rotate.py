#!/usr/bin/env python3
"""Rotate PPSK credentials for every user in an ExtremeCloud IQ user group.

Each user is deleted and recreated with the same data, minus the
password: XIQ generates a new key per the group's password rules and,
by default, emails/SMSes it to the user (the group's Delivery Settings
must enable the method, and the user must have a delivery address set).

Auth: set XIQ_TOKEN (XIQ: Administration > Integrations), or use a
.env file. Set XIQ_BASE_URL to target Extreme Platform ONE.

Usage:
  python3 ppsk_rotate.py --group-name "Corp-PPSK" [--no-email] [--out keys.csv]
"""

import argparse
import csv
import json
import logging
import os
import sys

from xiq_client import (
    XIQ,
    XIQ_BASE_URL,
    AuthenticationError,
    CredentialsError,
    XIQError,
)

# Writable fields carried over into the recreated user. password is
# omitted so XIQ generates a new key; server-set fields are excluded.
COPY_FIELDS = (
    "user_name",
    "name",
    "description",
    "email_address",
    "phone_number",
    "organization",
    "visit_purpose",
    "vlan_override",
)

# Credential delivery destinations (address strings, not booleans),
# copied unless --no-email is given.
DELIVERY_FIELDS = ("email_password_delivery", "sms_password_delivery")

log = logging.getLogger("ppsk-rotate")


def recreate_payload(user: dict, deliver: bool) -> dict:
    fields = COPY_FIELDS + DELIVERY_FIELDS if deliver else COPY_FIELDS
    # Truthy filter: drops empty strings and vlan_override 0
    # ("no override"), which the API rejects on create.
    payload = {f: user[f] for f in fields if user.get(f)}
    payload["user_group_id"] = user["user_group_id"]
    return payload


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Rotate PPSK credentials for all users in an XIQ user group."
    )
    parser.add_argument("--group-name", required=True)
    parser.add_argument("--no-email", action="store_true",
                        help="skip email/SMS delivery of new keys")
    parser.add_argument("--out", metavar="CSV",
                        help="also write user_name,new_password pairs here")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s")

    try:
        xiq = XIQ(base_url=os.environ.get("XIQ_BASE_URL", XIQ_BASE_URL))
        group = next(
            (g for g in xiq.usergroups() if g.get("name") == args.group_name),
            None,
        ) or sys.exit(f"User group {args.group_name!r} not found.")
        users = list(xiq.endusers(user_group_ids=group["id"]))
    except (CredentialsError, AuthenticationError) as exc:
        sys.exit(str(exc))

    log.info("Rotating %d user(s) in group %r", len(users), args.group_name)

    rotated: list[tuple[str, str]] = []
    failures = 0
    for user in users:
        label = user.get("user_name") or str(user["id"])
        try:
            xiq.delete_enduser(user["id"])
            new_user = xiq.create_enduser(
                recreate_payload(user, not args.no_email)
            )
        except XIQError as exc:
            failures += 1
            log.error("%s: %s", label, exc)
            # The delete may have succeeded before the recreate failed;
            # keep the original record so the user can be restored.
            log.error("%s original record: %s", label, json.dumps(user))
            continue
        rotated.append((label, new_user.get("password", "")))
        log.info("Rotated %s", label)

    if args.out and rotated:
        with open(args.out, "w", newline="") as fh:
            writer = csv.writer(fh)
            writer.writerow(["user_name", "new_password"])
            writer.writerows(rotated)
        os.chmod(args.out, 0o600)
        log.info("Wrote %s", args.out)

    log.info("Done: %d rotated, %d failed", len(rotated), failures)
    sys.exit(1 if failures else 0)


if __name__ == "__main__":
    main()
