"""Command-line entry point.

Usage:
    python -m lensix_inventory --provider aws --output inventory.ndjson.gz
    python -m lensix_inventory --provider aws --regions us-east-1,us-west-2 --output out.ndjson.gz

    LENSIX_REGIONS=us-east-1 python -m lensix_inventory --provider aws --output out.ndjson.gz

Credentials are never handled by this tool directly — it uses whatever
your local AWS CLI/SDK credential chain already resolves to (profile, env
vars, SSO, instance role, ...). Nothing is shared with Lensix except the
resulting output file, which you upload yourself.

Most people should run this via ./run.sh instead of calling this module
directly — see README.md.
"""

import argparse
import importlib
import os
import sys

from .common.output import is_permission_error

_MAX_ERRORS_SHOWN_PER_GROUP = 10


def _load_provider(name):
    # Imported lazily, and only for the one provider actually selected —
    # each provider's modules import that provider's SDK at module level
    # (e.g. azure/*.py imports azure.mgmt.*), and ./run.sh only installs
    # the one provider's requirements-<provider>.txt, so eagerly importing
    # all three here would fail for anyone who (correctly) only installed
    # what they needed.
    module = importlib.import_module(f'.{name}', package='lensix_inventory')
    return module.run


def _print_summary(provider, output_path, manifest, errors):
    bar = "=" * 60
    print(f"\n{bar}")
    print(f" Lensix Inventory — {provider} — done")
    print(bar)

    print(f"\nGathered {manifest['total_resources']} resource(s) across {len(manifest['regions'])} region(s):\n")
    for resource_type, count in sorted(manifest['resource_counts'].items(), key=lambda kv: -kv[1]):
        print(f"  {resource_type:<40} {count:>6}")

    if errors:
        permission_errors = [e for e in errors if is_permission_error(e['message'])]
        other_errors = [e for e in errors if e not in permission_errors]

        print(f"\n{len(errors)} issue(s) occurred while gathering — this is normal and does NOT")
        print("mean the upload will be rejected; it just means those specific resource")
        print("types couldn't be read with the credentials you ran this with.\n")

        if permission_errors:
            print(f"  Permission-related ({len(permission_errors)}) — you may be missing read")
            print("  access for these services (safe to ignore unless you specifically")
            print("  need findings for them; ask your cloud admin to grant read-only")
            print("  access and re-run if you do):")
            for e in permission_errors[:_MAX_ERRORS_SHOWN_PER_GROUP]:
                print(f"    - {e['source']} ({e['region']}): {e['message']}")
            if len(permission_errors) > _MAX_ERRORS_SHOWN_PER_GROUP:
                print(f"    ... and {len(permission_errors) - _MAX_ERRORS_SHOWN_PER_GROUP} more (full list in the output file's 'error' lines)")
            print()

        if other_errors:
            print(f"  Other ({len(other_errors)}):")
            for e in other_errors[:_MAX_ERRORS_SHOWN_PER_GROUP]:
                print(f"    - {e['source']} ({e['region']}): {e['message']}")
            if len(other_errors) > _MAX_ERRORS_SHOWN_PER_GROUP:
                print(f"    ... and {len(other_errors) - _MAX_ERRORS_SHOWN_PER_GROUP} more (full list in the output file's 'error' lines)")
            print()
    else:
        print("\nNo errors — every resource type was read successfully.")

    print(bar)
    print(f"Wrote: {output_path}")
    print("\nUpload this file to Lensix — you're done.")
    print(bar)


def main(argv=None):
    parser = argparse.ArgumentParser(prog='lensix-inventory', description=__doc__,
                                      formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--provider', required=True, choices=['aws', 'azure', 'gcp'])
    parser.add_argument('--output', required=True, help='Path to write the gzipped NDJSON inventory file to')
    parser.add_argument(
        '--regions',
        default=os.environ.get('LENSIX_REGIONS') or None,
        help='Comma-separated region list to limit gathering to (default: all enabled regions; '
             'falls back to the LENSIX_REGIONS env var if not given, useful for speeding up test '
             'runs without retyping --regions every time)',
    )
    args = parser.parse_args(argv)

    try:
        run_fn = _load_provider(args.provider)
    except ModuleNotFoundError as e:
        print(f"error: missing dependency ({e}).", file=sys.stderr)
        print(f"Run: pip install -r requirements-{args.provider}.txt", file=sys.stderr)
        return 1

    regions = args.regions.split(',') if args.regions else None

    try:
        writer = run_fn(regions=regions)
    except NotImplementedError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    except Exception as e:
        # Most failures at this point are credential/auth problems (no AWS
        # profile configured, AZURE_SUBSCRIPTION_ID unset, no GCP
        # Application Default Credentials, ...) — surface a clean one-line
        # message instead of a raw traceback, since this is meant to be run
        # directly by customers, not just developers.
        print(f"error: {type(e).__name__}: {e}", file=sys.stderr)
        return 1

    errors = writer.errors
    manifest = writer.write(args.output)
    _print_summary(args.provider, args.output, manifest, errors)
    return 0


if __name__ == '__main__':
    sys.exit(main())
