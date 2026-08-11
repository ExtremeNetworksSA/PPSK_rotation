# PPSK Rotation

Rotates PPSK credentials for every user in an ExtremeCloud IQ user
group: each user is deleted and recreated with the same data, minus the
password. XIQ generates a new key per the group's password rules and
emails/SMSes it to the user (requires the group's Delivery Settings to
enable Email/SMS, and users to have a delivery address set).

Run it on a schedule; the rotation interval is the schedule. Pair it
with a matching Account Expiration on the user group (e.g. Valid For
Time Period = 7 days) as a backstop for missed runs.

## Setup

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install requests
export XIQ_API_TOKEN="<key from XIQ: Administration > Integrations>"
```

To use Extreme Platform ONE endpoint instead of XIQ, also set:

```bash
export XIQ_BASE_URL="https://cloudapi.extremecloudiq.com/xiq/v1"
```

## Usage

```bash
python3 ppsk_rotate.py --group-name "Corp-PPSK"
```

- `--no-email` — skip email/SMS delivery of new keys
- `--out keys.csv` — also write `user_name,new_password` pairs

Each run first writes a pre-rotation backup (`users-<timestamp>.json`,
chmod 600) of every fetched user record; if a recreate fails after its
delete, restore the user by POSTing their record from the backup
(drop the server-set fields). The backups contain live keys — prune
old ones periodically.

Exits non-zero if any user failed to rotate; details are in the log
output.

## Cron

```cron
# Weekly, Monday 06:00 (group expiration: 7 days)
0 6 * * 1 cd /path/to/PPSK-rotation && ./.venv/bin/python ppsk_rotate.py --group-name "Corp-PPSK" >> rotate.log 2>&1

# Monthly, 1st at 06:00 (group expiration: 31 days)
0 6 1 * * cd /path/to/PPSK-rotation && ./.venv/bin/python ppsk_rotate.py --group-name "Corp-PPSK" >> rotate.log 2>&1
```

`XIQ_API_TOKEN` (and `XIQ_BASE_URL` if needed) must be in the cron
environment.
