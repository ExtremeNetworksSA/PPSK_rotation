#!/usr/bin/env python3
"""Rotate PPSK credentials for every user in an ExtremeCloud IQ user group.

Each user is deleted and recreated with the same data, minus the
password: XIQ generates a new key per the group's password rules and,
by default, emails it to the user (the group's Delivery Settings must
have Email enabled).

Auth: configure XIQ_API_TOKEN, or XIQ_USERNAME and XIQ_PASSWORD.

Usage:
  python3 ppsk_rotate.py --group "Corp-PPSK" [--no-email] [--no-backup]
                         [--out keys.csv] [--notify-email admin@example.com]

Settings are read from the environment, then .env, then the globals below.
"""

from __future__ import annotations

import argparse
import csv
import json
import logging
import os
import smtplib
import sys
import time
from email.message import EmailMessage

import requests

# Optional in-file defaults. Environment variables and .env override these.
XIQ_API_TOKEN = ""
XIQ_USERNAME = ""
XIQ_PASSWORD = ""
XIQ_BASE_URL = ""
SMTP_HOST = ""
SMTP_PORT = ""
SMTP_FROM = ""
SMTP_USERNAME = ""
SMTP_PASSWORD = ""


def load_env_file() -> dict[str, str]:
    """Read simple KEY=VALUE or export KEY=VALUE entries from .env."""
    values = {}
    try:
        with open(os.path.join(os.path.dirname(__file__), ".env")) as fh:
            for line in fh:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                if line.startswith("export "):
                    line = line[7:].strip()
                key, separator, value = line.partition("=")
                if separator:
                    values[key.strip()] = value.strip().strip("'\"")
    except FileNotFoundError:
        pass
    return values


DOTENV = load_env_file()


def setting(name: str, default: str = "") -> str:
    """Resolve a setting from the environment, .env, then globals."""
    return str(os.environ.get(name) or DOTENV.get(name)
               or globals().get(name) or default)


BASE_URL = setting("XIQ_BASE_URL", "https://api.extremecloudiq.com").rstrip("/")
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


class RotationError(Exception):
    def __init__(self, action: str, cause: requests.RequestException,
                 needs_recovery: bool = False):
        self.action = action
        self.cause = cause
        self.needs_recovery = needs_recovery
        super().__init__(str(cause))


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


def get_access_token(session: requests.Session, username: str,
                     password: str) -> str:
    """Log in to XIQ and return its temporary access token."""
    response = session.post(
        f"{BASE_URL}/login",
        json={"username": username, "password": password},
        timeout=30,
    )
    response.raise_for_status()
    token = response.json().get("access_token")
    if not token:
        raise ValueError("XIQ login response did not contain an access token.")
    return token


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

    try:
        resp = session.delete(f"{BASE_URL}/endusers/{user['id']}", timeout=30)
        resp.raise_for_status()
    except requests.RequestException as exc:
        raise RotationError("deletion", exc) from exc
    try:
        resp = session.post(f"{BASE_URL}/endusers", json=payload, timeout=30)
        resp.raise_for_status()
    except requests.RequestException as exc:
        raise RotationError(
            "recreation after successful deletion", exc, needs_recovery=True
        ) from exc
    return resp.json()


def write_private_json(path: str, records: list[dict]) -> None:
    with open(path, "w") as fh:
        json.dump(records, fh, indent=1)
    os.chmod(path, 0o600)


def notify_failure(recipient: str, group_name: str, failures: list[str],
                   backup: str | None, recovery: str | None) -> None:
    """Send one SMTP summary without exposing the backup's live credentials."""
    host = setting("SMTP_HOST")
    sender = setting("SMTP_FROM")
    if not host or not sender:
        raise ValueError("Set SMTP_HOST and SMTP_FROM.")
    msg = EmailMessage()
    msg["Subject"] = f"PPSK rotation failed for {group_name}"
    msg["From"] = sender
    msg["To"] = recipient
    msg.set_content(
        "One or more PPSK users failed to rotate:\n\n"
        + "\n".join(failures)
        + f"\n\nRecovery file: {recovery or 'not needed'}"
        + f"\nFull backup: {backup or 'not created (--no-backup)'}\n"
        + "Review the cronjob log and restore deleted users from the recovery file."
    )
    with smtplib.SMTP(host, int(setting("SMTP_PORT", "587"))) as server:
        server.starttls()
        username = setting("SMTP_USERNAME")
        if username:
            server.login(username, setting("SMTP_PASSWORD"))
        server.send_message(msg)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Rotate PPSK credentials for all users in an XIQ user group."
    )
    parser.add_argument("--group", required=True,
                        help="XIQ user group to rotate")
    parser.add_argument("--no-email", action="store_true",
                        help="skip email/SMS delivery of new keys")
    parser.add_argument("--out", metavar="CSV",
                        help="also write user_name,new_password pairs here")
    parser.add_argument("--no-backup", action="store_true",
                        help="skip the pre-rotation backup file")
    parser.add_argument("--notify-email", metavar="ADDRESS",
                        help="send an SMTP alert if any user fails")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s")

    session = requests.Session()
    token = setting("XIQ_API_TOKEN")
    username = setting("XIQ_USERNAME")
    password = setting("XIQ_PASSWORD")
    using_configured_token = bool(token)
    if not token:
        if not username or not password:
            sys.exit("Set XIQ_API_TOKEN or both XIQ_USERNAME and XIQ_PASSWORD.")
        try:
            token = get_access_token(session, username, password)
        except (requests.RequestException, ValueError) as exc:
            sys.exit(f"XIQ login failed: {exc}")
    session.headers["Authorization"] = f"Bearer {token}"

    try:
        group = find_group(session, args.group)
        users = paged(session, "/endusers", user_group_ids=group["id"])
    except requests.HTTPError as exc:
        if (using_configured_token and username and password
                and exc.response.status_code in (401, 403)):
            log.warning("XIQ rejected the API token; retrying with login credentials")
            try:
                session.headers.pop("Authorization", None)
                token = get_access_token(session, username, password)
                session.headers["Authorization"] = f"Bearer {token}"
                group = find_group(session, args.group)
                users = paged(session, "/endusers", user_group_ids=group["id"])
            except (requests.RequestException, ValueError) as login_exc:
                sys.exit(f"XIQ login fallback failed: {login_exc}")
        elif exc.response.status_code in (401, 403):
            sys.exit("XIQ authentication was rejected (HTTP %d)."
                     % exc.response.status_code)
        else:
            raise
    log.info("Rotating %d user(s) in group %r", len(users), args.group)

    backup = None
    if users and not args.no_backup:
        # Pre-rotation backup: everything needed to restore any user
        # whose recreate fails after the delete. Contains live keys.
        backup = os.path.abspath(time.strftime("users-%Y%m%d-%H%M%S.json"))
        write_private_json(backup, users)
        log.info("Backed up %d user record(s) to %s", len(users), backup)

    rotated: list[tuple[str, str]] = []
    failures: list[str] = []
    recovery_users: list[dict] = []
    recovery = None
    for user in users:
        label = user.get("user_name") or str(user["id"])
        try:
            new_user = rotate_user(session, user, not args.no_email)
        except RotationError as exc:
            detail = f"{label}: {exc.action} failed: {exc.cause}"
            failures.append(detail)
            response = exc.cause.response
            log.error(
                "%s (%s)",
                detail,
                response.text if response is not None else "no response",
            )
            if exc.needs_recovery:
                recovery_users.append(user)
                if not recovery:
                    recovery = os.path.abspath(
                        time.strftime("users-recovery-%Y%m%d-%H%M%S.json")
                    )
                write_private_json(recovery, recovery_users)
                log.error("%s: saved original record to %s", label, recovery)
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

    if failures and args.notify_email:
        try:
            notify_failure(
                args.notify_email, args.group, failures, backup, recovery
            )
            log.info("Sent failure notification to %s", args.notify_email)
        except Exception:
            log.exception("Failed to send SMTP notification")

    log.info("Done: %d rotated, %d failed", len(rotated), len(failures))
    sys.exit(1 if failures else 0)


if __name__ == "__main__":
    main()
