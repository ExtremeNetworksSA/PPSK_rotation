# PPSK Rotation

Rotates PPSK credentials for every user in an ExtremeCloud IQ user
group: each user is deleted and recreated with the same data, minus the
password. XIQ generates a new key per the group's password rules and
emails/SMSes it to the user (requires the group's Delivery Settings to
enable Email/SMS, and users to have a delivery address set).

Run it on a schedule; the rotation interval is the schedule. Pair it
with a matching Account Expiration on the user group (e.g. Valid For
Time Period = 7 days) as a backstop for missed runs.

## Requirements

- Python 3.9 or later
- An XIQ API token, or local XIQ login credentials
- Permission to read, delete, and create users in the target group
- Optional: an SMTP account for failure alerts



## Installation

```bash
cd /path/to/PPSK_rotation
python3 -m venv .venv && source .venv/bin/activate
pip install requests
```



## Configuration

Settings are resolved in this order:

1. Operating-system environment variables
2. `.env` in the same directory as `ppsk_rotate.py` (Recommended)
3. Non-empty defaults at the top of `ppsk_rotate.py`



### API tokens

- Recommended: create a Platform ONE key (`extr_sk_...`) under
**Administration & Settings > Integrations**. It works with either
supported endpoint.
- Alternatively, generate an XIQ token through `/login` (valid for 24
hours) and `/auth/apitoken` (configurable expiration) with
`usergroup:r` and `enduser` permissions. It works only with the
default `api.extremecloudiq.com` endpoint.
- Or set `XIQ_USERNAME` and `XIQ_PASSWORD`; the script obtains a new
24-hour `/login` token each run.

Set the selected key as `XIQ_API_TOKEN`. A configured key takes
priority over login credentials. The `SMTP_*` settings are only needed
when using `--notify-email`.

## Command options

```bash
./.venv/bin/python ppsk_rotate.py --group "Corp-PPSK"
```

- `--group "Corp-PPSK"` — XIQ user group to rotate (required)
- `--no-email` — skip email/SMS delivery of new keys
- `--out keys.csv` — also write `user_name,new_password` pairs
- `--no-backup` — skip the full pre-rotation backup
- `--notify-email admin@example.com` — send an SMTP alert if a user
deletion or recreation fails

Each run first writes a pre-rotation backup (`users-<timestamp>.json`)
of every fetched user record; if a recreate fails after its delete,
restore the user by POSTing their record from the backup (drop the
server-set fields). `--no-backup` skips this full backup.

Regardless of `--no-backup`, a recreation failure writes the deleted
user immediately to `users-recovery-<timestamp>.json`. This recovery
file contains only users that were deleted but could not be recreated.

Exits non-zero if any user failed to rotate; details are in the log
output.

## Failure notifications

Failure alerts use STARTTLS (normally port 587). The alert lists each
failed user, identifies whether deletion or recreation failed, and
includes the recovery file path when needed.

## Cron

Use absolute paths in cron. The script loads its `.env` file
automatically. These examples also save output to `rotate.log`:

```cron
# Weekly, Monday 06:00 (group expiration: 7 days)
0 6 * * 1 cd /path/to/PPSK_rotation && ./.venv/bin/python ppsk_rotate.py --group "Corp-PPSK" --notify-email admin@example.com >> rotate.log 2>&1

# Monthly, 1st at 06:00 (group expiration: 31 days)
0 6 1 * * cd /path/to/PPSK_rotation && ./.venv/bin/python ppsk_rotate.py --group "Corp-PPSK" --notify-email admin@example.com >> rotate.log 2>&1
```

Run `crontab -e` as the same operating-system user that owns `.env` and
the virtual environment.

## Step-by-step setup



### 1. Open the project

```bash
cd /path/to/PPSK_rotation
```



### 2. Install

```bash
python3 -m venv .venv
./.venv/bin/pip install requests
```



### 3. Configure

```bash
cp .env.example .env
chmod 600 .env
nano .env
```

Set either `XIQ_API_TOKEN`, or both `XIQ_USERNAME` and `XIQ_PASSWORD`.
Set the `SMTP_*` values only if failure notifications are required.

### 4. Run the first rotation

This command rotates every user in the named group:

```bash
./.venv/bin/python ppsk_rotate.py \
  --group "Corp-PPSK" \
  --notify-email admin@example.com
```

Remove `--notify-email` if SMTP is not configured.

### 5. Schedule

```bash
crontab -e
```

Add one schedule:

```cron
# Weekly, Monday 06:00
0 6 * * 1 cd /path/to/PPSK_rotation && ./.venv/bin/python ppsk_rotate.py --group "Corp-PPSK" --notify-email admin@example.com >> rotate.log 2>&1
```



### 6. Verify

```bash
crontab -l
tail -n 100 /path/to/PPSK_rotation/rotate.log
```

