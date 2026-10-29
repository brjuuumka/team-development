"""Sprint 3 acceptance tests: CORE-11, CORE-12, CORE-13. Run: python -m pytest tests/test_sprint3.py -v"""
import copy
import hashlib
import json
import sys
from pathlib import Path

import pytest
import rfc8785

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from core import Blockchain, Record

CHAIN_FILE = ROOT / 'examples' / 'valid-chain.json'
DOCUMENT_FILE = ROOT / 'examples' / 'issued-document.json'
RECORD_ID = '44444444-4444-4444-8444-444444444444'


@pytest.fixture
def chain():
    result = Blockchain()
    result.import_json(str(CHAIN_FILE))
    return result


def check_failure(result, index, code, record_id=None):
    assert result['valid'] is False
    assert result['error_block_index'] == index
    assert result['error_record_id'] == record_id
    assert result['reason'].startswith(code + ':')


def test_S3_01_valid_chain_has_success_contract(chain):
    assert chain.validate_chain() == {'valid': True, 'error_block_index': None, 'error_record_id': None, 'reason': None}


def test_S3_02_record_hash_matches_independent_sha256(chain):
    rec = chain.blocks[1].records[0]
    expected = hashlib.sha256(rfc8785.dumps(rec.content_dict())).hexdigest()
    assert rec.record_hash == expected
    assert len(expected) == 64


def test_S3_03_record_hash_created_automatically():
    rec = Record(RECORD_ID, '11111111-1111-4111-8111-111111111111', 'ISSUED',
                 '22222222-2222-4222-8222-222222222222', '2026-09-24T09:00:00.000Z',
                 '55555555-5555-4555-8555-555555555555', 'a' * 64)
    assert rec.record_hash == rec.calculate_hash()
    assert rec.validate() is True


@pytest.mark.parametrize('field,value', [('event_type', 'CANCELLED'), ('document_hash', 'f' * 64), ('created_at', '2026-09-25T09:00:00.000Z')])
def test_S3_04_record_tampering_localized_even_if_block_rehashed(chain, field, value):
    rec = chain.blocks[1].records[0]
    setattr(rec, field, value)
    chain.blocks[1].hash = chain.blocks[1].calculate_hash()
    check_failure(chain.validate_chain(), 1, 'RECORD_HASH_MISMATCH', RECORD_ID)


def test_S3_05_stored_record_hash_corruption_detected(chain):
    chain.blocks[1].records[0].record_hash = 'f' * 64
    check_failure(chain.validate_chain(), 1, 'RECORD_HASH_MISMATCH', RECORD_ID)


def test_S3_06_block_timestamp_tampering_localized(chain):
    chain.blocks[1].timestamp = '2026-10-01T00:00:00.000Z'
    check_failure(chain.validate_chain(), 1, 'HASH_MISMATCH')


def test_S3_07_broken_previous_link_localized_even_if_rehashed(chain):
    chain.blocks[1].previous_hash = 'f' * 64
    chain.blocks[1].hash = chain.blocks[1].calculate_hash()
    check_failure(chain.validate_chain(), 1, 'PREVIOUS_HASH_MISMATCH')


def test_S3_08_wrong_block_index_localized(chain):
    chain.blocks[1].index = 12
    check_failure(chain.validate_chain(), 1, 'INDEX_MISMATCH')


def test_S3_09_invalid_genesis_localized(chain):
    chain.blocks[0].previous_hash = 'f' * 64
    check_failure(chain.validate_chain(), 0, 'INVALID_GENESIS')


def test_S3_10_empty_chain_returns_error(chain):
    chain.blocks.clear()
    check_failure(chain.validate_chain(), None, 'INVALID_GENESIS')


def test_S3_11_first_error_in_multi_block_chain(chain):
    first = copy.deepcopy(chain.blocks[1].records[0])
    first.record_id = 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa'
    first.record_hash = first.calculate_hash()
    chain.add_block([first], '2026-09-25T09:00:00.000Z')
    assert chain.validate_chain()['valid'] is True
    chain.blocks[1].timestamp = 'tampered'
    chain.blocks[2].timestamp = 'also tampered'
    check_failure(chain.validate_chain(), 1, 'HASH_MISMATCH')


def test_S3_12_import_preserves_record_hash_and_rejects_tampering(chain, tmp_path):
    data = json.loads(CHAIN_FILE.read_text(encoding='utf-8'))
    data['blocks'][1]['records'][0]['event_type'] = 'CANCELLED'
    path = tmp_path / 'tampered.json'
    path.write_text(json.dumps(data), encoding='utf-8')
    before = [b.hash for b in chain.blocks]
    with pytest.raises(ValueError, match='RECORD_HASH_MISMATCH'):
        chain.import_json(str(path))
    assert [b.hash for b in chain.blocks] == before


def test_S3_13_external_document_valid(chain):
    assert chain.verify_external_document(DOCUMENT_FILE, RECORD_ID) is True


@pytest.mark.parametrize('change', ['value', 'added', 'removed'])
def test_S3_14_external_document_tampering(chain, tmp_path, change):
    doc = json.loads(DOCUMENT_FILE.read_text(encoding='utf-8'))
    if change == 'value':
        doc['quantity'] = 999
    elif change == 'added':
        doc['extra'] = 'tampered'
    else:
        doc.pop('quantity')
    path = tmp_path / 'document.json'
    path.write_text(json.dumps(doc, ensure_ascii=False), encoding='utf-8')
    assert chain.verify_external_document(path, RECORD_ID) is False


def test_S3_15_external_json_key_order_irrelevant(chain, tmp_path):
    doc = json.loads(DOCUMENT_FILE.read_text(encoding='utf-8'))
    path = tmp_path / 'reordered.json'
    path.write_text(json.dumps(doc, sort_keys=True, ensure_ascii=False), encoding='utf-8')
    assert chain.verify_external_document(path, RECORD_ID) is True


@pytest.mark.parametrize('payload', ['{broken', '[]', 'null', '123'])
def test_S3_16_invalid_external_json_returns_false(chain, tmp_path, payload):
    path = tmp_path / 'bad.json'
    path.write_text(payload, encoding='utf-8')
    assert chain.verify_external_document(path, RECORD_ID) is False


def test_S3_17_missing_external_document_returns_false(chain, tmp_path):
    assert chain.verify_external_document(tmp_path / 'missing.json', RECORD_ID) is False


def test_S3_18_unknown_record_returns_false(chain):
    assert chain.verify_external_document(DOCUMENT_FILE, 'ffffffff-ffff-4fff-8fff-ffffffffffff') is False


def test_S3_19_old_schema_import_rejected_without_mutation(chain, tmp_path):
    data = json.loads(CHAIN_FILE.read_text(encoding='utf-8'))
    data['schema_version'] = 1
    path = tmp_path / 'old.json'
    path.write_text(json.dumps(data), encoding='utf-8')
    old = [b.hash for b in chain.blocks]
    with pytest.raises(ValueError, match='UNSUPPORTED_SCHEMA_VERSION'):
        chain.import_json(str(path))
    assert [b.hash for b in chain.blocks] == old
