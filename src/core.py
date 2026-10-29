import copy
import hashlib
import json
import re
from collections.abc import Mapping
from pathlib import Path

import rfc8785


SCHEMA_VERSION = 2
ZERO_HASH = "0" * 64
HASH_PATTERN = re.compile(r"^[0-9a-f]{64}$")
UUID_PATTERN = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")


def _hash_json(value):
    return hashlib.sha256(rfc8785.dumps(value)).hexdigest()


class Record:
    def __init__(self, record_id, prescription_id, event_type, actor_id, created_at,
                 document_ref, document_hash, record_hash=None):
        self.schema_version = SCHEMA_VERSION
        self.record_id = record_id
        self.prescription_id = prescription_id
        self.event_type = event_type
        self.actor_id = actor_id
        self.created_at = created_at
        self.document_ref = document_ref
        self.document_hash = document_hash
        self.record_hash = record_hash if record_hash is not None else self.calculate_hash()

    def content_dict(self):
        return {
            "schema_version": self.schema_version,
            "record_id": self.record_id,
            "prescription_id": self.prescription_id,
            "event_type": self.event_type,
            "actor_id": self.actor_id,
            "created_at": self.created_at,
            "document_ref": self.document_ref,
            "document_hash": self.document_hash,
        }

    def calculate_hash(self):
        return _hash_json(self.content_dict())

    def validate(self):
        if self.schema_version != SCHEMA_VERSION:
            raise ValueError("UNSUPPORTED_SCHEMA_VERSION: неподдерживаемая версия записи")
        for field_name in ["record_id", "prescription_id", "actor_id", "document_ref"]:
            val = getattr(self, field_name)
            if not isinstance(val, str) or not UUID_PATTERN.fullmatch(val):
                raise ValueError(f"INVALID_RECORD_FIELD: {field_name} должен быть UUID")
        if self.event_type not in ["ISSUED", "DISPENSED", "CANCELLED"]:
            raise ValueError("INVALID_RECORD_FIELD: недопустимый event_type")
        if not isinstance(self.created_at, str) or not self.created_at:
            raise ValueError("INVALID_RECORD_FIELD: created_at должен быть непустой строкой")
        if not isinstance(self.document_hash, str) or not HASH_PATTERN.fullmatch(self.document_hash):
            raise ValueError("INVALID_RECORD_FIELD: document_hash должен содержать 64 hex-символа")
        if not isinstance(self.record_hash, str) or not HASH_PATTERN.fullmatch(self.record_hash):
            raise ValueError("RECORD_HASH_MISMATCH: неверный формат хэша записи")
        if self.calculate_hash() != self.record_hash:
            raise ValueError("RECORD_HASH_MISMATCH: хэш содержимого записи не совпадает")
        return True

    def to_dict(self):
        return {**self.content_dict(), "record_hash": self.record_hash}


class Block:
    def __init__(self, index, timestamp, previous_hash, records, hash_val=None):
        self.schema_version = SCHEMA_VERSION
        self.index = index
        self.timestamp = timestamp
        self.previous_hash = previous_hash
        self.records = records
        self.hash = hash_val if hash_val is not None else self.calculate_hash()

    def calculate_hash(self):
        block_data = {
            "schema_version": self.schema_version,
            "index": self.index,
            "timestamp": self.timestamp,
            "previous_hash": self.previous_hash,
            "records": [rec.to_dict() for rec in self.records],
        }
        return _hash_json(block_data)

    def validate(self):
        if self.calculate_hash() != self.hash:
            raise ValueError(f"HASH_MISMATCH: хэш блока {self.index} не совпадает")
        return True


class Blockchain:
    def __init__(self):
        self.blocks = [Block(
            index=0,
            timestamp="2026-01-01T00:00:00.000Z",
            previous_hash=ZERO_HASH,
            records=[],
        )]

    def add_block(self, records, timestamp):
        if not records:
            raise ValueError("EMPTY_BLOCK: Список записей пуст")
        if any(not isinstance(record, Record) for record in records):
            raise ValueError("INVALID_RECORD: Все элементы должны быть объектами Record")

        existing_ids = {r.record_id for b in self.blocks for r in b.records}
        for record in records:
            if record.record_id in existing_ids:
                raise ValueError(f"DUPLICATE_RECORD: Запись с ID {record.record_id} уже существует")
            existing_ids.add(record.record_id)
            record.validate()

        # Copy records so a caller cannot mutate the chain through a retained reference.
        saved_records = [copy.deepcopy(record) for record in records]
        last_block = self.blocks[-1]
        new_block = Block(
            index=last_block.index + 1,
            timestamp=timestamp,
            previous_hash=last_block.hash,
            records=saved_records,
        )
        self.blocks.append(new_block)
        return new_block

    def validate_chain(self):
        """Return the first integrity failure with block and optional record location."""
        if not self.blocks:
            return {"valid": False, "error_block_index": None, "error_record_id": None,
                    "reason": "INVALID_GENESIS: цепочка пуста"}

        genesis = self.blocks[0]
        if (genesis.index != 0 or genesis.previous_hash != ZERO_HASH or genesis.records
                or genesis.schema_version != SCHEMA_VERSION):
            return {"valid": False, "error_block_index": 0, "error_record_id": None,
                    "reason": "INVALID_GENESIS: нарушены обязательные поля genesis-блока"}

        seen_record_ids = set()
        for i, block in enumerate(self.blocks):
            if block.schema_version != SCHEMA_VERSION:
                return {"valid": False, "error_block_index": i, "error_record_id": None,
                        "reason": "UNSUPPORTED_SCHEMA_VERSION: неподдерживаемая версия блока"}
            if block.index != i:
                return {"valid": False, "error_block_index": i, "error_record_id": None,
                        "reason": "INDEX_MISMATCH: индекс блока не соответствует позиции в цепочке"}
            if not isinstance(block.records, list):
                return {"valid": False, "error_block_index": i, "error_record_id": None,
                        "reason": "INVALID_BLOCK: records должен быть списком"}

            for record in block.records:
                record_id = getattr(record, "record_id", None)
                if not isinstance(record, Record):
                    return {"valid": False, "error_block_index": i, "error_record_id": record_id,
                            "reason": "INVALID_RECORD: неизвестный тип записи"}
                if record_id in seen_record_ids:
                    return {"valid": False, "error_block_index": i, "error_record_id": record_id,
                            "reason": "DUPLICATE_RECORD: record_id уже встречался в цепочке"}
                seen_record_ids.add(record_id)
                try:
                    record.validate()
                except (ValueError, TypeError, AttributeError) as exc:
                    reason = str(exc)
                    code = reason.split(":", 1)[0]
                    return {"valid": False, "error_block_index": i, "error_record_id": record_id,
                            "reason": f"{code}: {reason.split(':', 1)[-1].strip()}"}

            if i > 0 and block.previous_hash != self.blocks[i - 1].hash:
                return {"valid": False, "error_block_index": i, "error_record_id": None,
                        "reason": "PREVIOUS_HASH_MISMATCH: ссылка не совпадает с хэшем предыдущего блока"}
            try:
                calculated_hash = block.calculate_hash()
            except (ValueError, TypeError, AttributeError) as exc:
                return {"valid": False, "error_block_index": i, "error_record_id": None,
                        "reason": f"INVALID_BLOCK: не удалось вычислить хэш ({exc})"}
            if calculated_hash != block.hash:
                return {"valid": False, "error_block_index": i, "error_record_id": None,
                        "reason": "HASH_MISMATCH: хэш блока не совпадает с пересчитанным"}

        return {"valid": True, "error_block_index": None, "error_record_id": None, "reason": None}

    def get_prescription_history(self, prescription_id):
        """Return events for a prescription in chain order."""
        return [
            record
            for block in self.blocks
            for record in block.records
            if record.prescription_id == prescription_id
        ]

    def verify_external_document(self, document_path, record_id):
        """Hash a JSON file's parsed content canonically and compare it to the record."""
        target_record = next((r for b in self.blocks for r in b.records if r.record_id == record_id), None)
        if target_record is None:
            return False
        try:
            with Path(document_path).open("r", encoding="utf-8") as file:
                document = json.load(file)
            if not isinstance(document, Mapping):
                return False
            return _hash_json(document) == target_record.document_hash
        except (OSError, json.JSONDecodeError, TypeError, ValueError):
            return False

    def export_json(self, filename="blockchain.json"):
        data = {"schema_version": SCHEMA_VERSION, "blocks": []}
        for block in self.blocks:
            data["blocks"].append({
                "schema_version": block.schema_version,
                "index": block.index,
                "timestamp": block.timestamp,
                "previous_hash": block.previous_hash,
                "records": [record.to_dict() for record in block.records],
                "hash": block.hash,
            })
        with open(filename, "w", encoding="utf-8") as file:
            json.dump(data, file, ensure_ascii=False, indent=2)

    def import_json(self, filename="blockchain.json"):
        with open(filename, "r", encoding="utf-8") as file:
            data = json.load(file)
        if not isinstance(data, dict) or data.get("schema_version") != SCHEMA_VERSION:
            raise ValueError("UNSUPPORTED_SCHEMA_VERSION: неподдерживаемая версия цепочки")
        if not isinstance(data.get("blocks"), list):
            raise ValueError("INVALID_CHAIN: blocks должен быть списком")

        new_blocks = []
        try:
            for block_data in data["blocks"]:
                if block_data.get("schema_version") != SCHEMA_VERSION:
                    raise ValueError("UNSUPPORTED_SCHEMA_VERSION: неподдерживаемая версия блока")
                records = []
                for record_data in block_data["records"]:
                    if record_data.get("schema_version") != SCHEMA_VERSION:
                        raise ValueError("UNSUPPORTED_SCHEMA_VERSION: неподдерживаемая версия записи")
                    records.append(Record(
                        record_id=record_data["record_id"],
                        prescription_id=record_data["prescription_id"],
                        event_type=record_data["event_type"],
                        actor_id=record_data["actor_id"],
                        created_at=record_data["created_at"],
                        document_ref=record_data["document_ref"],
                        document_hash=record_data["document_hash"],
                        record_hash=record_data["record_hash"],
                    ))
                new_blocks.append(Block(
                    index=block_data["index"],
                    timestamp=block_data["timestamp"],
                    previous_hash=block_data["previous_hash"],
                    records=records,
                    hash_val=block_data["hash"],
                ))
        except (KeyError, TypeError, ValueError, AttributeError) as exc:
            raise ValueError(f"INVALID_CHAIN: {exc}") from exc

        old_blocks = self.blocks
        self.blocks = new_blocks
        result = self.validate_chain()
        if not result["valid"]:
            self.blocks = old_blocks
            raise ValueError(f"INVALID_CHAIN: блок {result['error_block_index']}: {result['reason']}")
