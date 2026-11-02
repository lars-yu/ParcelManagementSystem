"""CSV module supplied by virtual BE; all roles are enacted by one operator."""
import csv
from typing import Iterable, Mapping, TextIO

FIELDS = ("id", "tracking_no", "location", "status")


def write_parcels_csv(parcels: Iterable[Mapping[str, object]], output: TextIO) -> int:
    """Write supplied parcel records, excluding recipient identity; return count."""
    writer = csv.DictWriter(output, fieldnames=FIELDS, lineterminator="\n")
    writer.writeheader()
    count = 0
    for parcel in parcels:
        writer.writerow({field: parcel[field] for field in FIELDS})
        count += 1
    return count
