"""Preview one page of legacy editions, optionally queue their normal metadata enrichment."""

import argparse
import json
import os
from pathlib import Path
from uuid import UUID

from autplay.application.track_metadata import TrackMetadataService
from sqlalchemy import create_engine
from sqlalchemy.orm import Session


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--owner", type=UUID, required=True)
    parser.add_argument("--after", type=UUID)
    parser.add_argument("--limit", type=int, default=50)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    url, file = os.getenv("AUTPLAY_DATABASE_URL"), os.getenv("AUTPLAY_DATABASE_URL_FILE")
    if bool(url) == bool(file):
        parser.error("configure precisely one database URL source")
    engine = create_engine(url or Path(str(file)).read_text().strip())
    try:
        with Session(engine) as session, session.begin():
            result = TrackMetadataService.catalog_backfill_page(
                session, args.owner, after=args.after, limit=args.limit, apply=args.apply
            )
        print(json.dumps({"mode": "apply" if args.apply else "preview", **result}))
    finally:
        engine.dispose()


if __name__ == "__main__":
    main()
