import builtins
import hashlib
import importlib.util
import io
import json
from pathlib import Path
from types import SimpleNamespace

import pytest


@pytest.fixture(autouse=True)
def no_ssh_transport(monkeypatch):
    original_import = builtins.__import__

    def without_ssh(name, *args, **kwargs):
        if name == 'paramiko' or name.startswith('paramiko.'):
            raise ImportError('SSH transport is deliberately unavailable in recovery tests')
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, '__import__', without_ssh)


@pytest.mark.parametrize('damage', ['ciphertext', 'tag', 'key', 'checksum', 'size', 'aad', 'truncated', 'header'])
def test_invalid_archive_never_opens_plaintext_output(tmp_path, monkeypatch, damage):
    from cryptography.exceptions import InvalidTag
    path = Path(__file__).resolve().parents[2] / 'deploy/vps/sealed-archive.py'
    spec = importlib.util.spec_from_file_location('sealed_archive', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    key = b'a' * 32
    content = b'private recovery evidence' * 100
    aad = '/opt/case-capital/backups/postgres/test.dump'
    nonce = b'n' * 12
    encoder = module.Cipher(module.algorithms.AES(key), module.modes.GCM(nonce)).encryptor()
    encoder.authenticate_additional_data(aad.encode())
    encrypted = bytearray(b'CCVPS1' + nonce + encoder.update(content) + encoder.finalize() + encoder.tag)
    record = {'remote_file': aad, 'plaintext_sha256': hashlib.sha256(content).hexdigest(), 'bytes': len(content)}
    if damage == 'ciphertext':
        encrypted[100] ^= 1
    elif damage == 'tag':
        encrypted[-1] ^= 1
    elif damage == 'key':
        key = b'b' * 32
    elif damage == 'checksum':
        record['plaintext_sha256'] = '0' * 64
    elif damage == 'size':
        record['bytes'] += 1
    elif damage == 'aad':
        record['remote_file'] += '.other'
    elif damage == 'truncated':
        encrypted = encrypted[:20]
    elif damage == 'header':
        encrypted[0] ^= 1
    archive = tmp_path / 'test.dump.aesgcm'
    archive.write_bytes(encrypted)
    archive.with_suffix(archive.suffix + '.json').write_text(json.dumps(record))
    output = tmp_path / 'recovered.dump'
    original_open = Path.open
    plaintext_opens = []

    def track_open(path, mode='r', *args, **kwargs):
        if path in (output, output.with_suffix(output.suffix + '.partial')):
            plaintext_opens.append(mode)
        return original_open(path, mode, *args, **kwargs)

    monkeypatch.setattr(Path, 'open', track_open)
    expected_error = RuntimeError if damage in ('checksum', 'size', 'truncated', 'header') else InvalidTag
    with pytest.raises(expected_error):
        module.unseal(archive, output, key)
    assert plaintext_opens == [], 'Unauthenticated archive must not open a plaintext destination'
    assert not output.exists()
    assert not output.with_suffix(output.suffix + '.partial').exists()


@pytest.mark.parametrize('repetitions', [0, 1000, 100000])
@pytest.mark.parametrize('source_changes', [False, True])
def test_sealed_archive_recovers_and_rejects_tampering(tmp_path, monkeypatch, repetitions, source_changes):
    path = Path(__file__).resolve().parents[2] / 'deploy/vps/sealed-archive.py'
    spec = importlib.util.spec_from_file_location('sealed_archive', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    content = b'private archive evidence' * repetitions
    class Reader(io.BytesIO):
        def stat(self):
            return SimpleNamespace(st_size=len(content))
        def readv(self, chunks, **kwargs):
            for offset, size in chunks:
                yield content[offset:offset + size]
    class SFTP:
        def normalize(self, path):
            return path
        def open(self, *args):
            return Reader(content)
        def stat(self, *args):
            return SimpleNamespace(st_size=len(content))
    key = b'a' * 32
    record = module.seal(SFTP(), '/opt/case-capital/backups/postgres/test.dump', tmp_path, key)
    assert record['plaintext_sha256'] == hashlib.sha256(content).hexdigest()
    archive = tmp_path / 'test.dump.aesgcm'
    output = tmp_path / 'recovered.dump'
    original_archive = archive.read_bytes()
    original_open = Path.open

    def change_source_before_recovery(path, mode='r', *args, **kwargs):
        if path == output.with_suffix(output.suffix + '.partial'):
            archive.write_bytes(b'source replaced after authentication')
        return original_open(path, mode, *args, **kwargs)

    with monkeypatch.context() as patch:
        if source_changes:
            patch.setattr(Path, 'open', change_source_before_recovery)
        module.unseal(archive, output, key)
    assert output.read_bytes() == content
    if source_changes:
        assert archive.read_bytes() != original_archive
        archive.write_bytes(original_archive)
    with pytest.raises(RuntimeError, match='Refusing to overwrite'):
        module.unseal(archive, output, key)
    assert output.read_bytes() == content
    existing_partial = tmp_path / 'existing.dump.partial'
    existing_partial.write_bytes(b'previous recovery evidence')
    with pytest.raises(FileExistsError):
        module.unseal(archive, tmp_path / 'existing.dump', key)
    assert existing_partial.read_bytes() == b'previous recovery evidence'
    modified = bytearray(archive.read_bytes())
    modified[-1] ^= 1
    archive.write_bytes(modified)
    with pytest.raises(Exception):
        module.unseal(archive, tmp_path / 'tampered.dump', key)
    assert not (tmp_path / 'tampered.dump').exists()
    assert not (tmp_path / 'tampered.dump.partial').exists()
