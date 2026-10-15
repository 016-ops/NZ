"""Verify a generated catalogue without connecting to MySQL."""
import argparse
import json
from pathlib import Path
from tools.client_catalog_export import verify_output


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    result = verify_output(args.output.resolve())
    print(json.dumps(result, ensure_ascii=False, indent=2))
    raise SystemExit(0 if result['passed'] else 1)


if __name__ == '__main__':
    main()
