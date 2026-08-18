# Lensix Inventory

Gather an inventory of your AWS, Azure, or GCP account and upload it to
Lensix — without ever giving Lensix live access to your cloud account.

You run this tool yourself, on your own machine, using the same
credentials you already use with the AWS CLI / Azure CLI / gcloud. It
reads your account (read-only — it never creates, changes, or deletes
anything) and writes everything it finds to a single file. You look it
over, then upload that file to Lensix yourself. Lensix never connects to
your cloud account directly in this mode.

## Before you start

You need:

- **Python 3.9 or newer** — check with `python3 --version`. If you don't
  have it, get it from [python.org](https://www.python.org/downloads/).
- **You're already logged in to the cloud provider you want to scan**, the
  same way you'd normally use its command-line tool:

  | Provider | How to log in |
  |---|---|
  | AWS | `aws configure` (or already have a profile / SSO session set up) |
  | Azure | `az login`, then set `export AZURE_SUBSCRIPTION_ID=<your subscription id>` |
  | GCP | `gcloud auth application-default login` |

  If you're not sure, just try running the tool (below) — it will tell you
  clearly if something's missing.

## Run it

```bash
git clone https://github.com/LensixScanner/lensix-inventory
cd lensix-inventory
./run.sh
```

`run.sh` will ask which provider to scan (or you can skip the prompt:
`./run.sh aws`, `./run.sh azure`, or `./run.sh gcp`). The first time you
run it for a given provider, it sets up a small private Python environment
and installs what it needs — that only happens once. Every run after that
starts scanning right away.

When it's done, you'll see a summary like this:

```
============================================================
 Lensix Inventory — aws — done
============================================================

Gathered 1,532 resource(s) across 14 region(s):

  ec2_instance                                    42
  s3_bucket                                       11
  security_group                                  27
  ...

2 issue(s) occurred while gathering — this is normal and does NOT
mean the upload will be rejected...

  Permission-related (2) — you may be missing read access for these
  services (safe to ignore unless you specifically need findings for
  them; ask your cloud admin to grant read-only access and re-run if
  you do):
    - workspaces (us-east-1): AccessDenied: ...

============================================================
Wrote: lensix-inventory-aws-20260722-101500.ndjson.gz

Upload this file to Lensix — you're done.
============================================================
```

Permission errors are common and expected — most read-only roles don't
cover every single AWS/Azure/GCP service, and that's fine. You don't need
to fix them before uploading; Lensix will just have less data for whatever
services it couldn't read. If you want a more complete inventory, ask
whoever manages your cloud account to grant broader read-only access and
run the tool again.

## What happens to your data

- Nothing is sent anywhere by this tool itself — it only writes a file to
  your local disk. You choose when (and whether) to upload it.
- The output file contains resource configuration data (instance types,
  security group rules, bucket settings, and so on) — the same kind of
  information any read-only audit would collect.
- A few fields that sometimes contain hardcoded secrets (like EC2/VM
  startup scripts and function environment variables) are scanned for
  credentials **on your machine** before anything is written — if a
  pattern matches, only the name of what was found is recorded (e.g.
  "AWS Secret Access Key"), never the actual secret value.

## Restricting to specific regions (AWS only)

```bash
./run.sh aws --regions us-east-1,us-west-2
```

Or set `LENSIX_REGIONS` instead of typing `--regions` every time — handy
while testing, since a full gather across every enabled region is much
slower than one:

```bash
export LENSIX_REGIONS=us-east-1
./run.sh aws
```

An explicit `--regions` flag always wins if you pass both. Azure and GCP
inventories aren't region-scoped, so this option only applies to AWS.

## Automated (Docker)

Paid Lensix customers can run this tool unattended, on their own schedule
(cron, a Kubernetes CronJob, ...), instead of running `./run.sh` by hand.
Lensix never runs this container for you — you run it, on your own
infrastructure, with your own cloud credentials, exactly like the manual
flow above.

1. In the Lensix dashboard, click **+ Automated upload**, pick a provider,
   and name the account. You'll get a one-time token and a ready-to-copy
   `docker run` command — copy the token now, it won't be shown again.
2. Build the image from this repo: `docker build -t lensix-inventory .`
3. Run it on whatever schedule you like:

   ```bash
   docker run --rm \
     -e LENSIX_PROVIDER=aws \
     -e LENSIX_UPLOAD_TOKEN=lsx_... \
     -e AWS_ACCESS_KEY_ID=... -e AWS_SECRET_ACCESS_KEY=... \
     lensix-inventory:latest
   ```

Environment variables:

| Variable | Required? | Meaning |
|---|---|---|
| `LENSIX_PROVIDER` | Yes | `aws`, `azure`, or `gcp` — must match the provider chosen when the token was created |
| `LENSIX_UPLOAD_TOKEN` | Yes | The one-time token from the dashboard — scoped to exactly one account |
| `LENSIX_API_URL` | No — defaults to `https://lensix.com` | Only needed if you're pointed at a different Lensix instance |
| `LENSIX_REGIONS` | No | Comma-separated AWS region list, same as `--regions` above |

Cloud credentials are **never** passed to this tool explicitly — the same
as running it by hand (see "Before you start"), it just uses whatever the
AWS/Azure/GCP SDK's normal credential resolution already finds: env vars,
`aws configure`'s profile file, `az login`, GCP Application Default
Credentials, or an IAM role/managed identity/workload identity attached to
wherever the container is running. Before gathering anything, it makes one
cheap real call (AWS: `sts:GetCallerIdentity`; Azure: a subscription
lookup; GCP: a project lookup) to confirm whatever credential it found
actually works — so a missing or bad credential fails immediately with a
clear message instead of quietly turning into dozens of per-service errors
partway through a long gather.

The container gathers the inventory, uploads it, and exits — `0` on
success, non-zero if either step failed (check the container logs for
which). Each run refreshes the same account's data; it does not create a
new account every time.

**Rate limit**: uploads for a given account are limited to once every 5
minutes — well within any real schedule (hourly, daily, whatever you pick),
but if you're re-running the container back-to-back while testing, you'll
get a `429` with a "try again in N minute(s)" message. That's expected,
not a bug — just wait it out, or regenerate the account's token from the
dashboard, which also resets the cooldown.

## Something not working?

- **"command not found: python3"** — install Python 3.9+ and try again.
- **A credential/authentication error** — make sure you're logged in with
  the provider's CLI, or the right env vars/role are in place (see
  "Before you start" and, for the Docker flow, the credentials note
  above).
- Any other error printed by the tool is meant to be read as-is; it tells
  you what went wrong and, where possible, what to do about it.

## License

[MIT](LICENSE)

