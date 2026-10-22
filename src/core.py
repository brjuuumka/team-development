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
        for i in range(len(self.blocks)):
            block = self.blocks[i]
            if block.calculate_hash() != block.hash:
                raise ValueError(f"HASH_MISMATCH: Блок {i} поврежден")
            if i > 0:
                if block.previous_hash != self.blocks[i - 1].hash:
                    raise ValueError(f"PREVIOUS_HASH_MISMATCH: Блок {i} не связан с предыдущим")
        return True

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
    print("Тест 1: Создание правильной записи")
    try:
        good_record = Record(
            record_id="44444444-4444-4444-8444-444444444444",
            prescription_id="11111111-1111-4111-8111-111111111111",
            event_type="ISSUED",
            actor_id="22222222-2222-4222-8222-222222222222",
            created_at="2026-09-24T09:00:00.000Z",
            document_ref="55555555-5555-4555-8555-555555555555",
            document_hash="c0854be50928aee0dc809ec4233002df0ae160a1e34d880e03f2eaeb15d5dd3c"
        )
        good_record.validate()
        print("Успех: запись прошла валидацию!")
    except Exception as e:
        print(f"Ошибка: {e}")

    print("\nТест 2: Создание записи с ошибкой (плохой UUID)")
    try:
        bad_record = Record(
            record_id="НЕ-UUID",
            prescription_id="11111111-1111-4111-8111-111111111111",
            event_type="ISSUED",
            actor_id="22222222-2222-4222-8222-222222222222",
            created_at="2026-09-24T09:00:00.000Z",
            document_ref="55555555-5555-4555-8555-555555555555",
            document_hash="c0854be50928aee0dc809ec4233002df0ae160a1e34d880e03f2eaeb15d5dd3c"
        )
        bad_record.validate()
        print("Ошибка: плохой UUID прошел валидацию (так не должно быть!)")
    except ValueError as e:
        print(f"Успех: система поймала ошибку! Текст: {e}")

    print("Тест 3: Создание Genesis-блока (CORE-03)")
    genesis = Block(
        index=0,
        timestamp="2026-01-01T00:00:00.000Z",
        previous_hash="0" * 64,
        records=[]
    )
    print(f"Хэш Genesis-блока: {genesis.hash}")
    expected_genesis_hash = "15b9cf17589776e821fa5cfafd32bb3ba503803d4cd59cb68c1b8413974b6571"
    if genesis.hash == expected_genesis_hash:
        print("Успех! Хэш совпал с valid-chain.json!")
    else:
        print("Ошибка: Хэш не совпал!")

    print("\nТест 4: Создание второго блока с записью (CORE-04)")
    good_record = Record(
        record_id="44444444-4444-4444-8444-444444444444",
        prescription_id="11111111-1111-4111-8111-111111111111",
        event_type="ISSUED",
        actor_id="22222222-2222-4222-8222-222222222222",
        created_at="2026-09-24T09:00:00.000Z",
        document_ref="55555555-5555-4555-8555-555555555555",
        document_hash="c0854be50928aee0dc809ec4233002df0ae160a1e34d880e03f2eaeb15d5dd3c"
    )
    block1 = Block(
        index=1,
        timestamp="2026-09-24T09:00:00.000Z",
        previous_hash=genesis.hash,
        records=[good_record]
    )
    print(f"Хэш второго блока: {block1.hash}")
    expected_block1_hash = "3b71b8b4be9fb2c73b3757a50abb38f9a61991207216890ad0c55743d92c9e36"
    if block1.hash == expected_block1_hash:
        print("Успех! Хэш второго блока совпал с valid-chain.json!")
    else:
        print("Ошибка: Хэш второго блока не совпал!")

    print("Тест 5: Создание цепочки и добавление блоков (CORE-04, CORE-06)")
    chain = Blockchain()
    try:
        chain.add_block([], "2026-09-24T10:00:00.000Z")
        print("Ошибка: пустой блок добавился")
    except ValueError as e:
        print(f"Успех: пустой блок отклонен. {e}")

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

    try:
        chain.add_block([good_record], "2026-09-24T11:00:00.000Z")
        print("Ошибка: дубликат добавился")
    except ValueError as e:
        print(f"Успех: дубликат отклонен. {e}")

    print("\nТест 6: Проверка целостности цепочки (CORE-05)")
    try:
        chain.validate_chain()
        print("Успех: цепочка валидна!")
    except ValueError as e:
        print(f"Ошибка: {e}")

    print("\nТест 7: Порча данных (Хакерская атака)")
    chain.blocks[1].records[0].event_type = "CANCELLED"
    try:
        chain.validate_chain()
        print("Ошибка: система не заметила взлом!")
    except ValueError as e:
        print(f"Успех: система поймала взлом! {e}")

    print("\nТест 8: Сохранение и загрузка (CORE-08)")
    chain.blocks[1].records[0].event_type = "ISSUED"
    chain.export_json("my_chain.json")
    new_chain = Blockchain()
    new_chain.import_json("my_chain.json")
    try:
        new_chain.validate_chain()
        print("Успех: загруженная цепочка валидна и хэши совпадают!")
    except ValueError as e:
        print(f"Ошибка: {e}")
