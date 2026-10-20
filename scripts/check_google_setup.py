"""Read-only local configuration check: no OAuth, network, credentials or writes."""
import argparse
import json

from app.google_oauth import configuration_checklist


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--worker", action="store_true", help="Validate worker requirements instead of the API callback")
    args = parser.parse_args(argv)
    report = configuration_checklist(worker=args.worker)
    print(json.dumps(report, ensure_ascii=False))
    return 0 if report["configured"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
