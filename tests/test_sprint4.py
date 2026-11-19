import copy
import json
import sys
import threading
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from node import Node, NodeClient, NodeError, make_http_server


@pytest.fixture
def nodes(tmp_path):
    return Node('node-a', tmp_path / 'a.json'), Node('node-b', tmp_path / 'b.json')


@pytest.fixture
def server(nodes):
    server = make_http_server(nodes[0], port=0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f'http://127.0.0.1:{server.server_address[1]}'
    finally:
        server.shutdown()
        thread.join(timeout=3)
        server.server_close()


def request_json(url, method='GET', payload=None):
    data = None if payload is None else json.dumps(payload).encode()
    req = Request(url, data=data, method=method,
                  headers={'Content-Type': 'application/json'})
    try:
        with urlopen(req, timeout=3) as response:
            return response.status, json.load(response)
    except HTTPError as error:
        return error.code, json.loads(error.read())


def test_01_independent_storage(nodes):
    a, b = nodes
    a.create({})
    assert a.status()['height'] == 1
    assert b.status()['height'] == 0


def test_02_node_ids(nodes):
    a, b = nodes
    assert a.status()['node_id'] == 'node-a'
    assert b.status()['node_id'] == 'node-b'


def test_03_same_genesis(nodes):
    a, b = nodes
    assert a.status()['last_hash'] == b.status()['last_hash']


def test_04_create_block(nodes):
    a, _ = nodes
    assert a.create({})['index'] == 1
    assert a.status()['valid'] is True


def test_05_transfer_block(nodes):
    a, b = nodes
    b.accept(a.create({}))
    assert b.status()['last_hash'] == a.status()['last_hash']


def test_06_multiple_blocks(nodes):
    a, b = nodes
    for _ in range(3):
        b.accept(a.create({}))
    assert b.status()['height'] == 3
    assert b.status()['valid'] is True


def test_07_invalid_hash(nodes):
    a, b = nodes
    block = a.create({})
    block['hash'] = 'f' * 64
    with pytest.raises(NodeError, match='HASH_MISMATCH'):
        b.accept(block)


def test_08_wrong_previous_hash(nodes):
    a, b = nodes
    block = a.create({})
    block['previous_hash'] = 'f' * 64
    with pytest.raises(NodeError, match='PREVIOUS_HASH_MISMATCH'):
        b.accept(block)


def test_09_tampered_record(nodes):
    a, b = nodes
    block = a.create({})
    block['records'][0]['event_type'] = 'CANCELLED'
    with pytest.raises(NodeError, match='RECORD_HASH_MISMATCH'):
        b.accept(block)


def test_10_duplicate_block(nodes):
    a, b = nodes
    block = a.create({})
    b.accept(block)
    with pytest.raises(NodeError, match='DUPLICATE_BLOCK'):
        b.accept(block)


def test_11_missing_block(nodes):
    a, b = nodes
    a.create({})
    with pytest.raises(NodeError, match='INDEX_MISMATCH'):
        b.accept(a.create({}))


def test_12_wrong_schema(nodes):
    a, b = nodes
    block = a.create({})
    block['schema_version'] = 999
    with pytest.raises(NodeError, match='UNSUPPORTED_SCHEMA_VERSION'):
        b.accept(block)


def test_13_chain_conflict(nodes):
    a, b = nodes
    a.create({})
    with pytest.raises(NodeError, match='CHAIN_CONFLICT'):
        a.accept(b.create({}))


def test_14_storage_reload(tmp_path):
    path = tmp_path / 'node.json'
    a = Node('a', path)
    a.create({})
    assert Node('a', path).status()['last_hash'] == a.status()['last_hash']


def test_15_rejected_block_unchanged(nodes):
    a, b = nodes
    block = copy.deepcopy(a.create({}))
    block['hash'] = 'f' * 64
    with pytest.raises(NodeError):
        b.accept(block)
    assert b.status()['height'] == 0


def test_16_http_node_status(server):
    code, data = request_json(server + '/v1/node')
    assert code == 200
    assert data['node_id'] == 'node-a'


def test_17_http_chain_status(server):
    code, data = request_json(server + '/v1/chain/status')
    assert code == 200
    assert data['height'] == 0


def test_18_http_create_block(server):
    code, data = request_json(server + '/v1/blocks/create', 'POST', {})
    assert code == 201
    assert data['block']['index'] == 1


def test_19_http_get_block(server):
    request_json(server + '/v1/blocks/create', 'POST', {})
    code, data = request_json(server + '/v1/blocks/1')
    assert code == 200
    assert data['block']['index'] == 1


def test_20_http_reject_invalid_block(server):
    code, data = request_json(server + '/v1/blocks', 'POST', {'index': 1})
    assert code == 422
    assert data['accepted'] is False
    assert 'INVALID_BLOCK' in data['error']


def test_21_http_missing_block(server):
    code, data = request_json(server + '/v1/blocks/999')
    assert code == 404
    assert 'BLOCK_NOT_FOUND' in data['error']


def test_22_http_unknown_route(server):
    code, data = request_json(server + '/v1/unknown')
    assert code == 404
    assert 'ROUTE_NOT_FOUND' in data['error']


def test_23_http_peer_client(server):
    client = NodeClient(server)
    assert client.status()['node_id'] == 'node-a'


def test_24_http_peer_pull(server, nodes):
    request_json(server + '/v1/blocks/create', 'POST', {})
    NodeClient(server).pull_block(nodes[1], 1)
    assert nodes[1].status()['height'] == 1


def test_25_http_duplicate_rejected(server):
    _, created = request_json(server + '/v1/blocks/create', 'POST', {})
    code, data = request_json(server + '/v1/blocks', 'POST', created['block'])
    assert code == 422
    assert 'DUPLICATE_BLOCK' in data['error']
