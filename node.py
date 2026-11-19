"""Independent blockchain node and minimal HTTP protocol for sprint 4.

The module deliberately uses only the Python standard library for networking.
It can therefore be run as a separate process without adding a web framework.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
import tempfile
import threading
import uuid
from datetime import datetime, timezone
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from core import Block, Blockchain, HASH_PATTERN, Record, SCHEMA_VERSION


BLOCK_FIELDS = {
    "schema_version", "index", "timestamp", "previous_hash", "records", "hash",
}
RECORD_FIELDS = {
    "schema_version", "record_id", "prescription_id", "event_type", "actor_id",
    "created_at", "document_ref", "document_hash", "record_hash",
}


class NodeError(ValueError):
    """An error that forms part of the sprint-4 node protocol."""


def record_to_dict(record: Record) -> dict:
    """Return a transport copy of a record."""
    return copy.deepcopy(record.to_dict())


def block_to_dict(block: Block) -> dict:
    """Return a transport copy of a block, without leaking in-memory objects."""
    return {
        "schema_version": block.schema_version,
        "index": block.index,
        "timestamp": block.timestamp,
        "previous_hash": block.previous_hash,
        "records": [record_to_dict(record) for record in block.records],
        "hash": block.hash,
    }


def _require_exact_fields(value: object, fields: set[str], entity: str) -> dict:
    if not isinstance(value, dict):
        raise NodeError(f"INVALID_{entity}: объект должен быть JSON-объектом")
    actual = set(value)
    if actual != fields:
        missing = sorted(fields - actual)
        unknown = sorted(actual - fields)
        detail = []
        if missing:
            detail.append("отсутствуют " + ", ".join(missing))
        if unknown:
            detail.append("неизвестны " + ", ".join(unknown))
        raise NodeError(f"INVALID_{entity}: " + "; ".join(detail))
    return value


def record_from_dict(value: object) -> Record:
    value = _require_exact_fields(value, RECORD_FIELDS, "RECORD")
    if value["schema_version"] != SCHEMA_VERSION:
        raise NodeError("UNSUPPORTED_SCHEMA_VERSION: неподдерживаемая версия записи")
    try:
        record = Record(
            record_id=value["record_id"],
            prescription_id=value["prescription_id"],
            event_type=value["event_type"],
            actor_id=value["actor_id"],
            created_at=value["created_at"],
            document_ref=value["document_ref"],
            document_hash=value["document_hash"],
            record_hash=value["record_hash"],
        )
        record.validate()
        return record
    except (KeyError, TypeError, ValueError) as exc:
        message = str(exc)
        if message.startswith(("UNSUPPORTED_SCHEMA_VERSION", "RECORD_HASH_MISMATCH", "INVALID_RECORD")):
            raise NodeError(message) from exc
        raise NodeError(f"INVALID_RECORD: {message}") from exc


def block_from_dict(value: object) -> Block:
    value = _require_exact_fields(value, BLOCK_FIELDS, "BLOCK")
    if value["schema_version"] != SCHEMA_VERSION:
        raise NodeError("UNSUPPORTED_SCHEMA_VERSION: неподдерживаемая версия блока")
    if not isinstance(value["records"], list):
        raise NodeError("INVALID_BLOCK: records должен быть списком")
    if isinstance(value["index"], bool) or not isinstance(value["index"], int) or value["index"] < 0:
        raise NodeError("INVALID_BLOCK: index должен быть неотрицательным целым")
    if not isinstance(value["timestamp"], str) or not value["timestamp"]:
        raise NodeError("INVALID_BLOCK: timestamp должен быть непустой строкой")
    for field in ("previous_hash", "hash"):
        if not isinstance(value[field], str) or not HASH_PATTERN.fullmatch(value[field]):
            raise NodeError(f"INVALID_BLOCK: {field} должен содержать 64 hex-символа")
    records = [record_from_dict(record) for record in value["records"]]
    try:
        block = Block(
            index=value["index"], timestamp=value["timestamp"],
            previous_hash=value["previous_hash"], records=records, hash_val=value["hash"],
        )
        return block
    except (TypeError, ValueError) as exc:
        message = str(exc)
        if message.startswith("HASH_MISMATCH"):
            raise NodeError(message) from exc
        raise NodeError(f"INVALID_BLOCK: {message}") from exc


class Node:
    """One independently persisted member of the educational blockchain network."""

    def __init__(self, node_id: str, storage_path: str | Path):
        if not isinstance(node_id, str) or not node_id.strip():
            raise ValueError("INVALID_NODE_ID: node_id должен быть непустой строкой")
        self.node_id = node_id
        self.storage_path = Path(storage_path)
        self._lock = threading.RLock()
        self.chain = Blockchain()
        if self.storage_path.exists():
            self.chain.import_json(str(self.storage_path))
        else:
            self._persist()

    def _persist(self) -> None:
        """Atomically store this node's own complete chain on local disk."""
        self.storage_path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"schema_version": SCHEMA_VERSION, "blocks": [block_to_dict(b) for b in self.chain.blocks]}
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=f".{self.storage_path.name}.", suffix=".tmp", dir=self.storage_path.parent,
        )
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as file:
                json.dump(payload, file, ensure_ascii=False, indent=2)
                file.flush()
                os.fsync(file.fileno())
            os.replace(temporary_name, self.storage_path)
        except Exception:
            try:
                os.unlink(temporary_name)
            except FileNotFoundError:
                pass
            raise

    def status(self) -> dict:
        with self._lock:
            result = self.chain.validate_chain()
            tip = self.chain.blocks[-1]
            return {
                "node_id": self.node_id,
                "schema_version": SCHEMA_VERSION,
                "height": tip.index,
                "last_hash": tip.hash,
                "valid": result["valid"],
            }

    def get_block(self, index: int) -> dict:
        with self._lock:
            if isinstance(index, bool) or not isinstance(index, int) or not 0 <= index < len(self.chain.blocks):
                raise NodeError("BLOCK_NOT_FOUND: блока с таким индексом нет")
            return block_to_dict(self.chain.blocks[index])

    def create(self, payload: dict | None = None) -> dict:
        """Create a synthetic record and append it locally for the demo/UI layer."""
        payload = payload or {}
        if not isinstance(payload, dict):
            raise NodeError("INVALID_REQUEST: payload должен быть объектом")
        now = datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")
        canonical_payload = json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
        record = Record(
            record_id=str(uuid.uuid4()),
            prescription_id=payload.get("prescription_id", str(uuid.uuid4())),
            event_type=payload.get("event_type", "ISSUED"),
            actor_id=payload.get("actor_id", str(uuid.uuid4())),
            created_at=now,
            document_ref=payload.get("document_ref", str(uuid.uuid4())),
            document_hash=payload.get("document_hash", hashlib.sha256(canonical_payload.encode()).hexdigest()),
        )
        with self._lock:
            block = self.chain.add_block([record], now)
            try:
                self._persist()
            except Exception:
                self.chain.blocks.pop()
                raise
            return block_to_dict(block)

    def accept(self, payload: object) -> dict:
        """Validate an incoming block fully, then append it atomically or reject it."""
        with self._lock:
            candidate = block_from_dict(payload)
            tip = self.chain.blocks[-1]
            if candidate.index <= tip.index:
                existing = self.chain.blocks[candidate.index] if candidate.index >= 0 else None
                if existing is not None and existing.hash == candidate.hash:
                    raise NodeError("DUPLICATE_BLOCK: блок уже есть в локальной цепочке")
                raise NodeError("CHAIN_CONFLICT: получен альтернативный блок на уже занятой высоте")
            if candidate.index != tip.index + 1:
                raise NodeError("INDEX_MISMATCH: получен блок с пропущенной высотой")
            if candidate.previous_hash != tip.hash:
                raise NodeError("PREVIOUS_HASH_MISMATCH: ссылка не совпадает с локальной вершиной")

            # Record validation happened while decoding.  Check the enclosing
            # hash only after the position/link checks so a bad link receives
            # its more useful PREVIOUS_HASH_MISMATCH error.
            try:
                candidate.validate()
            except ValueError as exc:
                raise NodeError(str(exc)) from exc

            known_ids = {record.record_id for block in self.chain.blocks for record in block.records}
            for record in candidate.records:
                if record.record_id in known_ids:
                    raise NodeError("DUPLICATE_RECORD: record_id уже есть в локальной цепочке")
                known_ids.add(record.record_id)
            if not candidate.records:
                raise NodeError("EMPTY_BLOCK: полученный блок не содержит записей")

            self.chain.blocks.append(copy.deepcopy(candidate))
            valid = self.chain.validate_chain()
            if not valid["valid"]:
                self.chain.blocks.pop()
                raise NodeError(valid["reason"])
            try:
                self._persist()
            except Exception:
                self.chain.blocks.pop()
                raise
            return block_to_dict(candidate)


class NodeClient:
    """Small client used by a node/UI to contact a known peer HTTP address."""

    def __init__(self, base_url: str, timeout: float = 5.0):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    def _request(self, method: str, path: str, body: object | None = None) -> dict:
        data = None if body is None else json.dumps(body, ensure_ascii=False).encode("utf-8")
        request = Request(
            self.base_url + path, data=data, method=method,
            headers={"Content-Type": "application/json"} if data is not None else {},
        )
        try:
            with urlopen(request, timeout=self.timeout) as response:
                return json.loads(response.read().decode("utf-8"))
        except HTTPError as exc:
            try:
                detail = json.loads(exc.read().decode("utf-8"))
                raise NodeError(detail.get("error", f"HTTP_{exc.code}")) from exc
            except json.JSONDecodeError:
                raise NodeError(f"PEER_HTTP_ERROR: HTTP {exc.code}") from exc
        except (URLError, TimeoutError) as exc:
            raise NodeError(f"PEER_UNAVAILABLE: {exc.reason if isinstance(exc, URLError) else exc}") from exc

    def status(self) -> dict:
        return self._request("GET", "/v1/node")

    def get_block(self, index: int) -> dict:
        return self._request("GET", f"/v1/blocks/{index}")["block"]

    def send_block(self, block: dict) -> dict:
        return self._request("POST", "/v1/blocks", block)

    def pull_block(self, target: Node, index: int) -> dict:
        """Request a peer block and validate/store it in ``target``."""
        return target.accept(self.get_block(index))


def make_http_server(node: Node, host: str = "127.0.0.1", port: int = 8000) -> ThreadingHTTPServer:
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, format: str, *args: object) -> None:  # noqa: A003
            return

        def _send(self, status: HTTPStatus, payload: dict) -> None:
            encoded = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(encoded)))
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(encoded)

        def _send_html(self, html: bytes) -> None:
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(html)))
            self.end_headers()
            self.wfile.write(html)

        def _read_json(self) -> object:
            raw_length = self.headers.get("Content-Length")
            try:
                length = int(raw_length or "0")
            except ValueError as exc:
                raise NodeError("INVALID_REQUEST: неверный Content-Length") from exc
            if length <= 0 or length > 1_000_000:
                raise NodeError("INVALID_REQUEST: тело запроса пустое или слишком большое")
            try:
                return json.loads(self.rfile.read(length).decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise NodeError("INVALID_REQUEST: требуется корректный JSON") from exc

        def do_GET(self) -> None:  # noqa: N802
            try:
                if self.path == "/":
                    self._send_html((Path(__file__).with_name("ui") / "index.html").read_bytes())
                    return
                if self.path in ("/v1/node", "/v1/chain/status"):
                    self._send(HTTPStatus.OK, node.status())
                    return
                if self.path.startswith("/v1/blocks/"):
                    index = int(self.path.removeprefix("/v1/blocks/"))
                    self._send(HTTPStatus.OK, {"block": node.get_block(index)})
                    return
                self._send(HTTPStatus.NOT_FOUND, {"error": "ROUTE_NOT_FOUND: маршрут не существует"})
            except (ValueError, NodeError) as exc:
                self._send(HTTPStatus.NOT_FOUND, {"error": str(exc)})

        def do_OPTIONS(self) -> None:  # noqa: N802
            self.send_response(HTTPStatus.NO_CONTENT)
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
            self.send_header("Access-Control-Allow-Headers", "Content-Type")
            self.end_headers()

        def do_POST(self) -> None:  # noqa: N802
            try:
                body = self._read_json()
                if self.path == "/v1/blocks":
                    self._send(HTTPStatus.CREATED, {"accepted": True, "block": node.accept(body)})
                    return
                if self.path == "/v1/blocks/create":
                    self._send(HTTPStatus.CREATED, {"block": node.create(body)})
                    return
                self._send(HTTPStatus.NOT_FOUND, {"error": "ROUTE_NOT_FOUND: маршрут не существует"})
            except (NodeError, ValueError) as exc:
                self._send(HTTPStatus.UNPROCESSABLE_ENTITY, {"accepted": False, "error": str(exc)})

    return ThreadingHTTPServer((host, port), Handler)


def main() -> None:
    parser = argparse.ArgumentParser(description="Sprint-4 blockchain node")
    parser.add_argument("--node-id", required=True)
    parser.add_argument("--storage", required=True, help="Path to this node's JSON chain")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, required=True)
    args = parser.parse_args()
    server = make_http_server(Node(args.node_id, args.storage), args.host, args.port)
    print(f"Node {args.node_id} listening on http://{args.host}:{args.port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
