"""Shared per-resource concurrency helper.

Region-level parallelism already exists one layer up (the scanner's own
ThreadPoolExecutor across regions, in lensix-scanner-light's
aws/scanmodules/lib.py). This is the same pattern applied one level
deeper, for the N describe/list calls a single region's (or a single
global gather's) resources often need -- one per KMS key, log group,
IAM user, etc. Those were previously strictly serial Python for-loops,
each blocking on real network latency, and on an account with a
non-trivial resource count in its real regions they dominate gather
time far more than anything list/describe-paginated already covers.

A single boto3 client instance making concurrent calls from multiple
threads is the standard, AWS-documented way to parallelize request
volume against one client -- callers here keep constructing one client
and sharing it across the pool, exactly as they did in the serial loop.
"""

from concurrent.futures import ThreadPoolExecutor

DEFAULT_MAX_WORKERS = 10


def parallel_map(fn, items, max_workers: int = DEFAULT_MAX_WORKERS) -> list:
    """Applies fn to each item concurrently, returning results in the same
    order as `items` (not completion order) -- callers can zip() the
    result back against the original list exactly as a serial
    `[fn(i) for i in items]` would have. A single item's exception
    propagates to the caller rather than being swallowed here, matching
    plain `map()`'s own behavior; callers that need per-item fault
    isolation should have `fn` itself catch and return a safe default, as
    this codebase's own `_try` helper (account.py) and the per-field
    try/excepts throughout these gather modules already do."""
    if not items:
        return []
    with ThreadPoolExecutor(max_workers=min(max_workers, len(items))) as pool:
        return list(pool.map(fn, items))
