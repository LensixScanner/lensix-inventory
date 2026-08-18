"""Small helpers shared by every GCP gather module, kept in one place
instead of copy-pasted into each one.
"""


def extract_network_name(value):
    """Normalize a VPC network reference (bare name or full selfLink URL) to
    its short name."""
    if not value or not isinstance(value, str):
        return None
    return value.rsplit('/', 1)[-1]
