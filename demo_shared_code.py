"""Shared CSV integration maintained by 前端开发工程师 许佳宁."""
from dataclasses import asdict
from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory
from src.contracts import InboundRequest, ParcelService
from src.sqlite_repository import SQLiteParcelRepository
from shared_modules.parcel_csv import write_parcels_csv


def demo_csv() -> str:
    with TemporaryDirectory() as directory:
        service = ParcelService(SQLiteParcelRepository(Path(directory) / "demo.db"))
        first = service.receive(InboundRequest("demo-clerk", "000001", "resident-a", "A-01", "req-1"))
        second = service.receive(InboundRequest("demo-clerk", "000002", "resident-b", "B-02", "req-2"))
        first = service.pickup(first.id, "resident-a")
        output = StringIO(newline="")
        write_parcels_csv((asdict(p) for p in (first, second)), output)
        return output.getvalue()


if __name__ == "__main__":
    print(demo_csv(), end="")
