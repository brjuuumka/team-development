import copy
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from core import Blockchain, Record

EXAMPLE = ROOT / "examples" / "valid-chain.json"
DOCUMENT = ROOT / "examples" / "issued-document.json"
TS = "2026-09-24T09:00:00.000Z"


def record(record_id="44444444-4444-4444-8444-444444444444", event_type="ISSUED"):
    return Record(record_id, "11111111-1111-4111-8111-111111111111", event_type,
                  "22222222-2222-4222-8222-222222222222", TS,
                  "55555555-5555-4555-8555-555555555555",
                  "c0854be50928aee0dc809ec4233002df0ae160a1e34d880e03f2eaeb15d5dd3c")


class CoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "chain.json"

    def load_example(self):
        chain = Blockchain()
        chain.import_json(str(EXAMPLE))
        return chain

    def test_T01_import_valid_chain(self):
        chain = self.load_example()
        self.assertEqual(len(chain.blocks), 2)
        self.assertEqual(len(chain.blocks[1].records), 1)
        self.assertTrue(chain.validate_chain()["valid"])

    def test_T02_export_import_roundtrip(self):
        chain = self.load_example()
        chain.export_json(str(self.path))
        restored = Blockchain()
        restored.import_json(str(self.path))
        self.assertEqual([b.hash for b in chain.blocks], [b.hash for b in restored.blocks])
        self.assertTrue(restored.validate_chain()["valid"])

    def test_T03_json_key_order_does_not_affect_hash(self):
        data = json.loads(EXAMPLE.read_text(encoding="utf-8"))
        self.path.write_text(json.dumps(data, ensure_ascii=False, sort_keys=True), encoding="utf-8")
        chain = Blockchain()
        chain.import_json(str(self.path))
        self.assertTrue(chain.validate_chain()["valid"])

    def test_T04_changed_record_is_localized(self):
        chain = self.load_example()
        changed = chain.blocks[1].records[0]
        changed.prescription_id = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
        result = chain.validate_chain()
        self.assertFalse(result["valid"])
        self.assertEqual(result["error_block_index"], 1)
        self.assertEqual(result["error_record_id"], changed.record_id)
        self.assertTrue(result["reason"].startswith("RECORD_HASH_MISMATCH"))

    def test_T05_broken_previous_hash_is_localized(self):
        chain = self.load_example()
        chain.blocks[1].previous_hash = "f" * 64
        chain.blocks[1].hash = chain.blocks[1].calculate_hash()
        result = chain.validate_chain()
        self.assertEqual(result["error_block_index"], 1)
        self.assertTrue(result["reason"].startswith("PREVIOUS_HASH_MISMATCH"))

    def test_T06_changed_genesis_is_rejected(self):
        chain = self.load_example()
        chain.blocks[0].timestamp = "2026-02-01T00:00:00.000Z"
        chain.blocks[0].hash = chain.blocks[0].calculate_hash()
        result = chain.validate_chain()
        self.assertEqual(result["error_block_index"], 0)
        self.assertTrue(result["reason"].startswith("INVALID_GENESIS"))

    def test_T07_duplicate_id_rejected_atomically(self):
        chain = Blockchain()
        chain.add_block([record()], TS)
        before = [block.hash for block in chain.blocks]
        with self.assertRaisesRegex(ValueError, "DUPLICATE_RECORD"):
            chain.add_block([record()], TS)
        self.assertEqual([block.hash for block in chain.blocks], before)

    def test_T08_empty_block_rejected_atomically(self):
        chain = Blockchain()
        before = len(chain.blocks)
        with self.assertRaisesRegex(ValueError, "EMPTY_BLOCK"):
            chain.add_block([], TS)
        self.assertEqual(len(chain.blocks), before)

    def test_T09_invalid_record_rejected_atomically(self):
        chain = Blockchain()
        before = len(chain.blocks)
        with self.assertRaises(ValueError):
            chain.add_block([record(), record("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa", "INVALID")], TS)
        self.assertEqual(len(chain.blocks), before)

    def test_T10_prescription_history(self):
        chain = self.load_example()
        prescription_id = chain.blocks[1].records[0].prescription_id
        self.assertEqual(len(chain.get_prescription_history(prescription_id)), 1)
        self.assertEqual(chain.get_prescription_history("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"), [])

    def test_T11_source_record_is_copied(self):
        chain = Blockchain()
        source = record()
        block = chain.add_block([source], TS)
        saved = copy.deepcopy(block.records[0].to_dict())
        source.event_type = "CANCELLED"
        self.assertEqual(block.records[0].to_dict(), saved)
        self.assertTrue(chain.validate_chain()["valid"])

    def test_T12_mutating_exported_data_does_not_change_chain(self):
        chain = self.load_example()
        before = [block.hash for block in chain.blocks]
        chain.export_json(str(self.path))
        exported = json.loads(self.path.read_text(encoding="utf-8"))
        exported["blocks"][1]["records"][0]["event_type"] = "CANCELLED"
        self.assertEqual([block.hash for block in chain.blocks], before)
        self.assertTrue(chain.validate_chain()["valid"])

    def test_T13_record_mutation_is_detected(self):
        chain = self.load_example()
        chain.blocks[1].records[0].event_type = "CANCELLED"
        result = chain.validate_chain()
        self.assertFalse(result["valid"])
        self.assertEqual(result["error_block_index"], 1)
        self.assertEqual(result["error_record_id"], "44444444-4444-4444-8444-444444444444")
        self.assertTrue(result["reason"].startswith("RECORD_HASH_MISMATCH"))

    def test_T14_record_mutation_detected_even_if_block_hash_is_recomputed(self):
        chain = self.load_example()
        chain.blocks[1].records[0].event_type = "CANCELLED"
        chain.blocks[1].hash = chain.blocks[1].calculate_hash()
        result = chain.validate_chain()
        self.assertTrue(result["reason"].startswith("RECORD_HASH_MISMATCH"))

    def test_T15_valid_external_json_file_matches(self):
        chain = self.load_example()
        self.assertTrue(chain.verify_external_document(DOCUMENT, "44444444-4444-4444-8444-444444444444"))

    def test_T16_changed_external_json_file_does_not_match(self):
        chain = self.load_example()
        document = json.loads(DOCUMENT.read_text(encoding="utf-8"))
        document["quantity"] = 2
        self.path.write_text(json.dumps(document, ensure_ascii=False), encoding="utf-8")
        self.assertFalse(chain.verify_external_document(self.path, "44444444-4444-4444-8444-444444444444"))

    def test_T17_missing_or_invalid_external_file_returns_false(self):
        chain = self.load_example()
        self.assertFalse(chain.verify_external_document(self.path, "44444444-4444-4444-8444-444444444444"))
        self.path.write_text("{broken", encoding="utf-8")
        self.assertFalse(chain.verify_external_document(self.path, "44444444-4444-4444-8444-444444444444"))

    def test_T18_invalid_import_does_not_replace_current_chain(self):
        chain = self.load_example()
        original_hashes = [block.hash for block in chain.blocks]
        data = json.loads(EXAMPLE.read_text(encoding="utf-8"))
        invalid_versions = copy.deepcopy(data)
        invalid_versions["schema_version"] = 1
        invalid_record_hash = copy.deepcopy(data)
        invalid_record_hash["blocks"][1]["records"][0]["record_hash"] = "f" * 64
        for invalid_data in (invalid_versions, invalid_record_hash):
            with self.subTest(invalid_data=invalid_data["schema_version"]):
                self.path.write_text(json.dumps(invalid_data), encoding="utf-8")
                with self.assertRaises(ValueError):
                    chain.import_json(str(self.path))
                self.assertEqual([block.hash for block in chain.blocks], original_hashes)


if __name__ == "__main__":
    unittest.main()
