#!/usr/bin/env python3
"""Rotate PPSK credentials for every user in an ExtremeCloud IQ user group.

Each user is deleted and recreated with the same data, minus the
password: XIQ generates a new key per the group's password rules and,
by default, emails it to the user (the group's Delivery Settings must
have Email enabled).

Auth: set XIQ_API_TOKEN (XIQ: Administration > Integrations).

Usage:
  python3 ppsk_rotate.py --group-name "Corp-PPSK" [--no-email] [--out keys.csv]
"""

import argparse
import csv
import json
import logging
import os
import sys
import time

import requests

BASE_URL = os.environ.get("XIQ_BASE_URL", "https://api.extremecloudiq.com")
PAGE_SIZE = 100

# Writable fields carried over into the recreated user.
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

# Credential delivery destinations, copied unless --no-email is given.
DELIVERY_FIELDS = ("email_password_delivery", "sms_password_delivery")

log = logging.getLogger("ppsk-rotate")


def paged(session: requests.Session, path: str, **params) -> list[dict]:
    """Collect every item from a paginated XIQ list endpoint."""
    items: list[dict] = []
    page = 1
    while True:
        resp = session.get(
            f"{BASE_URL}{path}",
            params={**params, "page": page, "limit": PAGE_SIZE},
            timeout=30,
        )
        resp.raise_for_status()
        body = resp.json()
        items.extend(body.get("data", []))
        if page >= body.get("total_pages", 1):
            return items
        page += 1


def find_group(session: requests.Session, name: str) -> dict:
    for group in paged(session, "/usergroups"):
        if group.get("name") == name:
            return group
    sys.exit(f"User group {name!r} not found.")


def rotate_user(session: requests.Session, user: dict, deliver_email: bool) -> dict:
    """Delete the user and recreate it; return the new record (with new key)."""
    # Truthy filter: also drops vlan_override 0 ("no override"), which
    # the API rejects on create.
    payload = {"user_group_id": user["user_group_id"]}
    payload.update({f: user[f] for f in COPY_FIELDS if user.get(f)})
    if deliver_email:
        # These take the destination address itself, not a boolean flag.
        payload.update(
            {f: user[f] for f in DELIVERY_FIELDS if user.get(f)}
        )

    resp = session.delete(f"{BASE_URL}/endusers/{user['id']}", timeout=30)
    resp.raise_for_status()
    resp = session.post(f"{BASE_URL}/endusers", json=payload, timeout=30)
    resp.raise_for_status()
    return resp.json()


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

    token = os.environ.get("XIQ_API_TOKEN") or sys.exit("Set XIQ_API_TOKEN.")
    session = requests.Session()
    session.headers["Authorization"] = f"Bearer {token}"

    try:
        group = find_group(session, args.group_name)
        users = paged(session, "/endusers", user_group_ids=group["id"])
    except requests.HTTPError as exc:
        if exc.response.status_code in (401, 403):
            sys.exit("XIQ rejected the token (HTTP %d): check XIQ_API_TOKEN."
                     % exc.response.status_code)
        raise
    log.info("Rotating %d user(s) in group %r", len(users), args.group_name)

    if users:
        # Pre-rotation backup: everything needed to restore any user
        # whose recreate fails after the delete. Contains live keys.
        backup = time.strftime("users-%Y%m%d-%H%M%S.json")
        with open(backup, "w") as fh:
            json.dump(users, fh, indent=1)
        os.chmod(backup, 0o600)
        log.info("Backed up %d user record(s) to %s", len(users), backup)

    rotated: list[tuple[str, str]] = []
    failures = 0
    for user in users:
        label = user.get("user_name") or str(user["id"])
        try:
            new_user = rotate_user(session, user, not args.no_email)
        except requests.HTTPError as exc:
            failures += 1
            log.error("%s: %s (%s)", label, exc, exc.response.text)
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
