"""Local secret-content scanner.

This is the ONE place in this tool where we deliberately compute a
finding-like result (a list of matched rule names) locally instead of
uploading raw data for Lensix to evaluate server-side. It exists only for
fields that are themselves plausible places to find hardcoded credentials —
EC2/Azure VM user-data, Lambda/Cloud Function/Container App environment
variables, GCP instance metadata. Everywhere else, this tool uploads raw
gathered data and lets Lensix determine findings.
"""

import re

SECRET_RULES = [
    ('AWS Secret Access Key',             re.compile(r'(?:aws_secret_access_key|aws_secret_key)\s*[=:]\s*\S+', re.I)),
    ('AWS Session Token',                 re.compile(r'(?:aws_session_token|x-amz-security-token)\s*[=:]\s*\S+', re.I)),
    ('Azure Storage Account Key',         re.compile(r'AccountKey=[A-Za-z0-9+/]{86}==')),
    ('Azure SAS Token',                   re.compile(r'\bsv=\d{4}-\d{2}-\d{2}&[^\s"\']*&sig=[A-Za-z0-9%]{20,}')),
    ('Azure Client Secret',               re.compile(r'(?:azure_client_secret|client_secret)\s*[=:]\s*\S+', re.I)),
    ('GCP API Key',                       re.compile(r'\bAIza[0-9A-Za-z\-_]{35}\b')),
    ('GCP Service Account Key',           re.compile(r'"private_key_id"\s*:\s*"[0-9a-f]{40}"')),
    ('GitHub Token',                      re.compile(r'\bgh[pousr]_[A-Za-z0-9]{36,255}\b')),
    ('GitHub Fine-Grained PAT',           re.compile(r'\bgithub_pat_[A-Za-z0-9_]{22,255}\b')),
    ('GitLab Personal Access Token',      re.compile(r'\bglpat-[A-Za-z0-9\-_]{20}\b')),
    ('Slack Token',                       re.compile(r'\bxox[baprs]-[A-Za-z0-9-]{10,48}\b')),
    ('Slack Webhook URL',                 re.compile(r'hooks\.slack\.com/services/T[A-Za-z0-9_]{8,}/B[A-Za-z0-9_]{8,}/[A-Za-z0-9_]{24}')),
    ('Stripe Live API Key',               re.compile(r'\bsk_live_[0-9a-zA-Z]{24,}\b')),
    ('Twilio API Key',                    re.compile(r'\bSK[0-9a-fA-F]{32}\b')),
    ('Private Key',                       re.compile(r'-----BEGIN\s?(?:RSA|EC|OPENSSH|PGP|DSA)?\s?PRIVATE KEY-----')),
    ('JSON Web Token',                    re.compile(r'\beyJ[A-Za-z0-9_-]{5,}\.eyJ[A-Za-z0-9_-]{5,}\.[A-Za-z0-9_-]{5,}\b')),
    ('Database Connection String',        re.compile(r'\b(?:postgres(?:ql)?|mysql|mongodb(?:\+srv)?|redis|amqp)://[^:\s/]+:[^@\s/]+@', re.I)),
    ('Basic Auth Credentials in URL',     re.compile(r'https?://[^:\s/@]+:[^@\s/]+@[^\s"\']+')),
    ('Generic API Key/Secret Assignment', re.compile(r'(?:api[_-]?key|apikey|secret|token|passwd|password)\s*[=:]\s*["\']([A-Za-z0-9\-_/+=]{16,})["\']', re.I)),
]

# CloudFormation WaitCondition presigned URLs - embedded by design in every
# AWS Elastic Beanstalk instance's UserData (via ebbootstrap.sh) to signal
# successful launch back to the stack - carry an X-Amz-Security-Token query
# param that matches the AWS Session Token rule above but isn't a meaningful
# secret. Strip these before scanning so they don't get flagged.
_CFN_WAITCONDITION_URL_RE = re.compile(r'https?://cloudformation-waitcondition-\S+', re.I)


def scan_text_for_secrets(text):
    """Scan free text for hardcoded credentials. Returns the distinct rule
    names that matched (empty list if none), never the matched value. This
    is the ONLY output that should ever leave this machine for these
    fields — never the raw text itself."""
    if not text:
        return []
    text = _CFN_WAITCONDITION_URL_RE.sub('', text)
    return [name for name, pattern in SECRET_RULES if pattern.search(text)]
