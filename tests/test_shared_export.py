import csv
import io
import unittest
from demo_shared_code import demo_csv
from shared_modules.parcel_csv import write_parcels_csv, FIELDS


class SharedExportTests(unittest.TestCase):
    def test_roundtrip_chinese_quotes_commas_and_newlines(self):
        record = dict(id=1, tracking_no="000001", location='中文货架,"一号"\n二层', status="stored", recipient="private")
        output = io.StringIO(newline="")
        self.assertEqual(write_parcels_csv([record], output), 1)
        rows = list(csv.DictReader(io.StringIO(output.getvalue())))
        self.assertEqual(rows[0]["location"], record["location"])
        self.assertEqual(rows[0]["tracking_no"], "000001")
        self.assertNotIn("recipient", rows[0])

    def test_empty_input_still_has_header(self):
        output = io.StringIO(newline="")
        self.assertEqual(write_parcels_csv([], output), 0)
        reader = csv.DictReader(io.StringIO(output.getvalue()))
        self.assertEqual(reader.fieldnames, list(FIELDS))
        self.assertEqual(list(reader), [])

    def test_generator_input_and_count(self):
        output = io.StringIO(newline="")
        records = (dict(id=i, tracking_no=str(i), location="A", status="stored") for i in range(3))
        self.assertEqual(write_parcels_csv(records, output), 3)
        self.assertEqual(len(list(csv.DictReader(io.StringIO(output.getvalue())))), 3)

    def test_core_receive_pickup_and_shared_export_integration(self):
        rows = list(csv.DictReader(io.StringIO(demo_csv())))
        self.assertEqual(len(rows), 2)
        self.assertEqual([r["status"] for r in rows], ["picked_up", "stored"])
        self.assertEqual([r["tracking_no"] for r in rows], ["000001", "000002"])
        self.assertEqual([r["location"] for r in rows], ["A-01", "B-02"])
