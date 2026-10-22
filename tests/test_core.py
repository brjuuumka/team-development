"""Sprint 2 QA: cases T-01–T-12 from docs/test-plan.md.
Run from repository root: python -m unittest discover -s tests -v
"""
import copy
import json
import re
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from core import Blockchain, Block, Record

EXAMPLE = ROOT / 'examples' / 'valid-chain.json'
TS = '2026-09-24T09:00:00.000Z'


def record(record_id='44444444-4444-4444-8444-444444444444', event_type='ISSUED'):
    return Record(record_id, '11111111-1111-4111-8111-111111111111', event_type,
                  '22222222-2222-4222-8222-222222222222', TS,
                  '55555555-5555-4555-8555-555555555555', 'a' * 64)


class Sprint2CoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / 'chain.json'

    def load_example(self):
        chain = Blockchain()
        chain.import_json(str(EXAMPLE))
        return chain

    def test_T01_import_valid_chain(self):
        chain = self.load_example()
        self.assertEqual(len(chain.blocks), 2)
        self.assertEqual(len(chain.blocks[1].records), 1)
        self.assertEqual(chain.blocks[1].records[0].event_type, 'ISSUED')
        self.assertTrue(chain.validate_chain())

    def test_T02_export_import_roundtrip(self):
        chain = self.load_example()
        chain.export_json(str(self.path))
        restored = Blockchain()
        restored.import_json(str(self.path))
        self.assertEqual([b.hash for b in chain.blocks], [b.hash for b in restored.blocks])
        self.assertEqual([b.calculate_hash() for b in chain.blocks], [b.calculate_hash() for b in restored.blocks])
        self.assertTrue(restored.validate_chain())

    def test_T03_json_key_order_does_not_affect_hash(self):
        data = json.loads(EXAMPLE.read_text(encoding='utf-8'))
        self.path.write_text(json.dumps(data, ensure_ascii=False, sort_keys=True), encoding='utf-8')
        chain = Blockchain()
        chain.import_json(str(self.path))
        self.assertTrue(chain.validate_chain())

    def test_T04_changed_record_detected(self):
        chain = self.load_example()
        chain.blocks[1].records[0].prescription_id = 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa'
        with self.assertRaisesRegex(ValueError, 'HASH_MISMATCH'):
            chain.validate_chain()

    def test_T05_broken_previous_hash_detected(self):
        chain = self.load_example()
        chain.blocks[1].previous_hash = 'f' * 64
        chain.blocks[1].hash = chain.blocks[1].calculate_hash()
        with self.assertRaisesRegex(ValueError, 'PREVIOUS_HASH_MISMATCH'):
            chain.validate_chain()

    def test_T06_changed_genesis_rejected(self):
        chain = self.load_example()
        chain.blocks[0].timestamp = '2026-02-01T00:00:00.000Z'
        chain.blocks[0].hash = chain.blocks[0].calculate_hash()
        with self.assertRaisesRegex(ValueError, 'INVALID_GENESIS'):
            chain.validate_chain()

    def test_T07_duplicate_id_rejected_atomically(self):
        chain = Blockchain()
        chain.add_block([record()], TS)
        before = len(chain.blocks)
        with self.assertRaisesRegex(ValueError, 'DUPLICATE_RECORD'):
            chain.add_block([record()], TS)
        self.assertEqual(len(chain.blocks), before)

    def test_T08_empty_block_rejected_atomically(self):
        chain = Blockchain()
        before = len(chain.blocks)
        with self.assertRaisesRegex(ValueError, 'EMPTY_BLOCK'):
            chain.add_block([], TS)
        self.assertEqual(len(chain.blocks), before)

    def test_T09_invalid_record_rejected_atomically(self):
        chain = Blockchain()
        before = len(chain.blocks)
        with self.assertRaises(ValueError):
            chain.add_block([record(), record('aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa', 'INVALID')], TS)
        self.assertEqual(len(chain.blocks), before)

    def test_T10_prescription_history(self):
        chain = self.load_example()
        known = chain.blocks[1].records[0].prescription_id
        self.assertEqual(len(chain.get_prescription_history(known)), 1)
        self.assertEqual(chain.get_prescription_history('aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa'), [])

    def test_T11_mutating_source_record_does_not_change_block(self):
        chain = Blockchain()
        source = record()
        block = chain.add_block([source], TS)
        saved = copy.deepcopy(block.records[0].to_dict())
        source.event_type = 'CANCELLED'
        self.assertEqual(block.records[0].to_dict(), saved)
        self.assertTrue(chain.validate_chain())

    def test_T12_mutating_exported_json_does_not_change_chain(self):
        chain = Blockchain()
        chain.add_block([record()], TS)
        old_hashes = [b.hash for b in chain.blocks]
        chain.export_json(str(self.path))
        exported = json.loads(self.path.read_text(encoding='utf-8'))
        exported['blocks'][1]['records'][0]['event_type'] = 'CANCELLED'
        self.assertEqual([b.hash for b in chain.blocks], old_hashes)
        self.assertEqual(chain.blocks[1].records[0].event_type, 'ISSUED')
        self.assertTrue(chain.validate_chain())


if __name__ == '__main__':
    unittest.main()
