import uuid
import re
import hashlib
import rfc8785
import json

class Record:
    def __init__(self, record_id, prescription_id, event_type, actor_id, created_at, document_ref, document_hash):
        self.schema_version = 1
        self.record_id = record_id
        self.prescription_id = prescription_id
        self.event_type = event_type
        self.actor_id = actor_id
        self.created_at = created_at
        self.document_ref = document_ref
        self.document_hash = document_hash

    def validate(self):
        uuid_pattern = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
        for field_name in ["record_id", "prescription_id", "actor_id", "document_ref"]:
            val = getattr(self, field_name)
            if not isinstance(val, str) or not uuid_pattern.match(val):
                raise ValueError(f"Ошибка в {field_name}: неверный формат UUID")

        if self.event_type not in ["ISSUED", "DISPENSED", "CANCELLED"]:
            raise ValueError("Ошибка в event_type: недопустимый тип события")

        hash_pattern = re.compile(r"^[0-9a-f]{64}$")
        if not isinstance(self.document_hash, str) or not hash_pattern.match(self.document_hash):
            raise ValueError("Ошибка в document_hash: должен быть 64 hex-символа в нижнем регистре")

        return True

    def to_dict(self):
        return {
            "schema_version": self.schema_version,
            "record_id": self.record_id,
            "prescription_id": self.prescription_id,
            "event_type": self.event_type,
            "actor_id": self.actor_id,
            "created_at": self.created_at,
            "document_ref": self.document_ref,
            "document_hash": self.document_hash
        }

class Block:
    def __init__(self, index, timestamp, previous_hash, records, hash_val=None):
        self.schema_version = 1
        self.index = index
        self.timestamp = timestamp
        self.previous_hash = previous_hash
        self.records = records
        self.hash = hash_val if hash_val else self.calculate_hash()

    def calculate_hash(self):
        block_data = {
            "schema_version": self.schema_version,
            "index": self.index,
            "timestamp": self.timestamp,
            "previous_hash": self.previous_hash,
            "records": [rec.to_dict() for rec in self.records]
        }
        canonical_json = rfc8785.dumps(block_data)
        return hashlib.sha256(canonical_json).hexdigest()

    def validate(self):
        if self.calculate_hash() != self.hash:
            raise ValueError(f"Ошибка в блоке {self.index}: HASH_MISMATCH (хэш не совпадает)")
        return True

class Blockchain:
    def __init__(self):
        self.blocks = [Block(
            index=0,
            timestamp="2026-01-01T00:00:00.000Z",
            previous_hash="0" * 64,
            records=[]
        )]

    def add_block(self, records, timestamp):
        if not records:
            raise ValueError("EMPTY_BLOCK: Список записей пуст")

        existing_ids = set()
        for b in self.blocks:
            for r in b.records:
                existing_ids.add(r.record_id)

        for r in records:
            if r.record_id in existing_ids:
                raise ValueError(f"DUPLICATE_RECORD: Запись с ID {r.record_id} уже существует")
            existing_ids.add(r.record_id)

        last_block = self.blocks[-1]
        new_block = Block(
            index=last_block.index + 1,
            timestamp=timestamp,
            previous_hash=last_block.hash,
            records=records
        )
        self.blocks.append(new_block)
        return new_block

    def validate_chain(self):
        """
        CORE-11, CORE-12: Глубокая проверка целостности с поиском точки разрыва.
        """
        for i in range(len(self.blocks)):
            block = self.blocks[i]
            
            # Проверка собственного хэша блока
            if block.calculate_hash() != block.hash:
                return {"valid": False, "error_block_index": i, "reason": "HASH_MISMATCH: Хэш блока не совпадает с пересчитанным"}
            
            # Проверка связи с предыдущим блоком
            if i > 0:
                if block.previous_hash != self.blocks[i - 1].hash:
                    return {"valid": False, "error_block_index": i, "reason": "PREVIOUS_HASH_MISMATCH: Разорвана связь с предыдущим блоком"}
            
            # Детальная валидация каждой записи внутри блока
            for record in block.records:
                try:
                    record.validate()
                except ValueError as e:
                    return {"valid": False, "error_block_index": i, "reason": f"INVALID_RECORD: Повреждена запись {record.record_id} - {e}"}
                    
        return {"valid": True, "error_block_index": None, "reason": None}

    def verify_external_document(self, document_dict, record_id):
        """
        CORE-13: Проверка внешнего документа (сверка пересчитанного хэша файла с сохраненным).
        """
        target_record = None
        for b in self.blocks:
            for r in b.records:
                if r.record_id == record_id:
                    target_record = r
                    break
            if target_record:
                break
                
        if not target_record:
            return False
            
        try:
            canonical_json = rfc8785.dumps(document_dict)
            doc_hash = hashlib.sha256(canonical_json).hexdigest()
            return doc_hash == target_record.document_hash
        except Exception:
            return False

    def export_json(self, filename="blockchain.json"):
        data = {"schema_version": 1, "blocks": []}
        for b in self.blocks:
            data["blocks"].append({
                "schema_version": b.schema_version,
                "index": b.index,
                "timestamp": b.timestamp,
                "previous_hash": b.previous_hash,
                "records": [r.to_dict() for r in b.records],
                "hash": b.hash
            })
        with open(filename, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        print(f"Цепочка сохранена в файл {filename}")

    def import_json(self, filename="blockchain.json"):
        try:
            with open(filename, "r", encoding="utf-8") as f:
                data = json.load(f)
        except FileNotFoundError:
            raise ValueError("Файл не найден")

        new_blocks = []
        for b_data in data["blocks"]:
            records = []
            for r_data in b_data["records"]:
                records.append(Record(
                    record_id=r_data["record_id"],
                    prescription_id=r_data["prescription_id"],
                    event_type=r_data["event_type"],
                    actor_id=r_data["actor_id"],
                    created_at=r_data["created_at"],
                    document_ref=r_data["document_ref"],
                    document_hash=r_data["document_hash"]
                ))
            new_block = Block(
                index=b_data["index"],
                timestamp=b_data["timestamp"],
                previous_hash=b_data["previous_hash"],
                records=records,
                hash_val=b_data["hash"]
            )
            new_blocks.append(new_block)

        self.blocks = new_blocks
        print(f"Цепочка загружена из файла {filename}")

if __name__ == "__main__":
    print("Тест 5: Создание цепочки и добавление блоков")
    chain = Blockchain()
    
    good_record = Record(
        record_id="44444444-4444-4444-8444-444444444444",
        prescription_id="11111111-1111-4111-8111-111111111111",
        event_type="ISSUED",
        actor_id="22222222-2222-4222-8222-222222222222",
        created_at="2026-09-24T09:00:00.000Z",
        document_ref="55555555-5555-4555-8555-555555555555",
        document_hash="c0854be50928aee0dc809ec4233002df0ae160a1e34d880e03f2eaeb15d5dd3c"
    )
    chain.add_block([good_record], "2026-09-24T09:00:00.000Z")
    print("Успех: блок с записью добавлен")

    print("\nТест 6: Проверка целостности цепочки (CORE-11)")
    val_result = chain.validate_chain()
    if val_result["valid"]:
        print("Успех: цепочка валидна!")
    else:
        print(f"Ошибка валидации: {val_result}")

    print("\nТест 7: Поиск точки разрыва (CORE-12)")
    # Имитируем хакерскую атаку (меняем тип события в обход хэша)
    chain.blocks[1].records[0].event_type = "CANCELLED"
    val_result = chain.validate_chain()
    if not val_result["valid"]:
        print(f"Успех: система поймала взлом! Блок {val_result['error_block_index']}, причина: {val_result['reason']}")
    else:
        print("Ошибка: система не заметила взлом!")

    # Возвращаем как было для следующих тестов
    chain.blocks[1].records[0].event_type = "ISSUED"

    print("\nТест 8: Проверка внешнего документа (CORE-13)")
    # Создаем фейковый документ, который соответствует хэшу записи
    valid_document = {
        "schema_version": 1,
        "prescription_id": "11111111-1111-4111-8111-111111111111",
        "event_type": "ISSUED",
        "patient_id": "33333333-3333-4333-8333-333333333333",
        "doctor_id": "22222222-2222-4222-8222-222222222222",
        "medication_code": "TEST-001",
        "dosage_text": "Учебный пример, не для применения",
        "quantity": 1,
        "unit": "упаковка",
        "issued_at": "2026-09-24T09:00:00.000Z",
        "expires_at": "2026-10-24T09:00:00.000Z",
        "nonce": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
    }
    
    is_valid = chain.verify_external_document(valid_document, "44444444-4444-4444-8444-444444444444")
    if is_valid:
        print("Успех: Внешний документ подтвержден (хэши совпали).")
    else:
        print("Ошибка: Документ не прошел проверку.")
        
    invalid_document = valid_document.copy()
    invalid_document["quantity"] = 2 # Злоумышленник изменил количество
    is_valid_fake = chain.verify_external_document(invalid_document, "44444444-4444-4444-8444-444444444444")
    if not is_valid_fake:
        print("Успех: Поддельный внешний документ отклонен.")
    else:
        print("Ошибка: Система пропустила подделку!")
