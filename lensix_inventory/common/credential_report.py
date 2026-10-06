"""Shared IAM credential report fetch.

A stateful generate-then-poll workflow (generate_credential_report, then
poll get_credential_report until ready) rather than a simple list/
describe call -- but still just one CSV covering every IAM user plus the
root account in a single report. aws/account.py (root row) and
aws/user.py (per-user rows) both need it; this is the one place that
actually calls the live API, so a caller gathering both in the same
process (see lensix-scanner-light's account_checks.py scan_global(),
which runs gather_account_global() and gather_users() back to back
against the same account) fetches it once here and passes the parsed
content to both, instead of each module independently generating and
polling for its own copy of the identical report.
"""

import csv
import io
import time

import boto3


def fetch_credential_report_content() -> str:
    """Raw CSV content, as a string. A report generation task already in
    progress (for this account, or a concurrent gather of it) surfaces as
    LimitExceededException -- expected, not a failure -- so that falls
    through to polling for the report already being generated instead of
    failing outright. Raises TimeoutError if the report never becomes
    ready within 15 retries (~30s)."""
    iam = boto3.client('iam')
    try:
        iam.generate_credential_report()
    except iam.exceptions.LimitExceededException:
        pass
    for _ in range(15):
        try:
            resp = iam.get_credential_report()
            return resp['Content'].decode('utf-8')
        except iam.exceptions.CredentialReportNotReadyException:
            time.sleep(2)
    raise TimeoutError('Credential report not ready after 15 retries')


def parse_credential_report_rows(content: str) -> list:
    reader = csv.DictReader(io.StringIO(content))
    return list(reader)
