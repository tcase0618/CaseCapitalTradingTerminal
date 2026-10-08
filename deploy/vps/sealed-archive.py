#!/usr/bin/env python3
"""Off-host archival with AES-GCM and a Windows-user DPAPI-protected key.

Copies only; never deletes remote files. Requires a pinned SSH host key.
"""
import argparse
import base64
import ctypes
import getpass
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
from ctypes import wintypes

import paramiko
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes


def protect_key(data, decrypt=False):
    if os.name != 'nt':
        raise RuntimeError('DPAPI archive destination must be Windows')
    class Blob(ctypes.Structure):
        _fields_ = [('size', wintypes.DWORD), ('data', ctypes.POINTER(ctypes.c_ubyte))]
    buffer = (ctypes.c_ubyte * len(data)).from_buffer_copy(data)
    source, target = Blob(len(data), buffer), Blob()
    api = ctypes.windll.crypt32.CryptUnprotectData if decrypt else ctypes.windll.crypt32.CryptProtectData
    if not api(ctypes.byref(source), None, None, None, None, 1, ctypes.byref(target)):
        raise ctypes.WinError()
    try:
        return ctypes.string_at(target.data, target.size)
    finally:
        free = ctypes.windll.kernel32.LocalFree
        free.argtypes = [wintypes.HLOCAL]
        free.restype = wintypes.HLOCAL
        free(ctypes.cast(target.data, wintypes.HLOCAL))


def seal(sftp, remote, destination, key):
    allowed = ['/opt/case-capital/backups/postgres/', '/opt/case-capital/backups/resource-archives/']
    canonical = sftp.normalize(remote)
    if not any(canonical.startswith(root) for root in allowed):
        raise RuntimeError('Archive source is outside the inventoried backup directories')
    target = destination / (PurePosixPath(canonical).name + '.aesgcm')
    if target.exists():
        raise RuntimeError('Refusing to overwrite an archive')
    nonce = os.urandom(12)
    cipher = Cipher(algorithms.AES(key), modes.GCM(nonce)).encryptor()
    cipher.authenticate_additional_data(canonical.encode())
    digest, size = hashlib.sha256(), 0
    temporary = target.with_suffix(target.suffix + '.partial')
    with sftp.open(canonical, 'rb') as source, temporary.open('xb') as output:
        output.write(b'CCVPS1' + nonce)
        while chunk := source.read(1024 * 1024):
            digest.update(chunk)
            size += len(chunk)
            output.write(cipher.update(chunk))
        output.write(cipher.finalize())
        output.write(cipher.tag)
    # Verify the stored ciphertext and GCM tag without writing plaintext.
    verify_digest = hashlib.sha256()
    with temporary.open('rb') as source:
        header = source.read(18)
        source.seek(-16, 2)
        tag = source.read(16)
        decryptor = Cipher(algorithms.AES(key), modes.GCM(header[6:], tag)).decryptor()
        decryptor.authenticate_additional_data(canonical.encode())
        source.seek(18)
        remaining = temporary.stat().st_size - 34
        while remaining:
            chunk = source.read(min(1024 * 1024, remaining))
            if not chunk:
                raise RuntimeError('Truncated archive')
            remaining -= len(chunk)
            verify_digest.update(decryptor.update(chunk))
        verify_digest.update(decryptor.finalize())
    if digest.digest() != verify_digest.digest() or sftp.stat(canonical).st_size != size:
        raise RuntimeError('Archive verification failed or source changed')
    temporary.rename(target)
    with target.open('rb') as source:
        encrypted_digest = hashlib.file_digest(source, 'sha256').hexdigest()
    record = {'remote_file': canonical, 'plaintext_sha256': digest.hexdigest(), 'bytes': size,
              'ciphertext_sha256': encrypted_digest,
              'verified': True, 'key_protection': 'Windows current-user DPAPI'}
    target.with_suffix(target.suffix + '.json').write_text(json.dumps(record, indent=2))
    return record


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--host', required=True)
    parser.add_argument('--hostkey-sha256', required=True)
    parser.add_argument('--destination', type=Path, required=True)
    parser.add_argument('--remote-file', action='append', required=True)
    args = parser.parse_args()
    args.destination.mkdir(parents=True, exist_ok=True)
    key_file = args.destination / 'archive-key.dpapi'
    if key_file.exists():
        key = protect_key(key_file.read_bytes(), decrypt=True)
    else:
        key = os.urandom(32)
        key_file.write_bytes(protect_key(key))
    class PinnedPolicy(paramiko.MissingHostKeyPolicy):
        def missing_host_key(self, client, hostname, hostkey):
            fingerprint = base64.b64encode(hashlib.sha256(hostkey.asbytes()).digest()).decode().rstrip('=')
            if fingerprint != args.hostkey_sha256.removeprefix('SHA256:'):
                raise RuntimeError('SSH host-key fingerprint mismatch')
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(PinnedPolicy())
    client.connect(args.host, username='root', password=os.environ.get('CASE_VPS_PASSWORD') or getpass.getpass('SSH password: '), timeout=15)
    try:
        with client.open_sftp() as sftp:
            for remote in args.remote_file:
                print(json.dumps(seal(sftp, remote, args.destination, key)))
    finally:
        client.close()


if __name__ == '__main__':
    main()
