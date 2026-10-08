import hashlib
import importlib.util
import io
from pathlib import Path
from types import SimpleNamespace

import pytest


def test_sealed_archive_recovers_and_rejects_tampering(tmp_path):
    pytest.importorskip('paramiko')
    pytest.importorskip('cryptography')
    path = Path(__file__).resolve().parents[2] / 'deploy/vps/sealed-archive.py'
    spec = importlib.util.spec_from_file_location('sealed_archive', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    content = b'private archive evidence' * 1000
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
    module.unseal(archive, output, key)
    assert output.read_bytes() == content
    modified = bytearray(archive.read_bytes())
    modified[100] ^= 1
    archive.write_bytes(modified)
    with pytest.raises(Exception):
        module.unseal(archive, tmp_path / 'tampered.dump', key)
    assert not (tmp_path / 'tampered.dump').exists()
    assert not (tmp_path / 'tampered.dump.partial').exists()
