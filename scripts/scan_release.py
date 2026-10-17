"""Bounded heuristic release scan; never extracts archives or prints matched values."""
from __future__ import annotations

import argparse
import hashlib
import gzip
import bz2
import lzma
import io
import json
from pathlib import Path, PurePosixPath
import re
import tarfile
import zipfile


PATTERNS = {
    'private_key': rb'-----BEGIN (?:RSA |EC |DSA |OPENSSH |ENCRYPTED )?PRIVATE KEY-----',
    'provider_key': rb'\b(?:sk-(?:proj-|ant-[a-z0-9]+-)?[A-Za-z0-9_-]{20,}|AIza[A-Za-z0-9_-]{30,}|AKIA[A-Z0-9]{16})\b',
    'authorization_value': rb'(?i)(?:authorization[\s"\x27:=]+(?:bearer|basic)[\s]+[A-Za-z0-9+/=_-]{12,}|\bbearer\s+[A-Za-z0-9._~+/-]{24,})',
    'assigned_secret': rb'(?i)(?:api[_-]?key|secret[_-]?key|access[_-]?token|client[_-]?secret|password)["\x27 \t]*[:=]["\x27 \t]*[A-Za-z0-9_./+~-]{16,}',
    'local_account_path': rb'(?:/Users/[^/\s"\x27]{1,80}/|/home/[^/\s"\x27]{1,80}/)(?:\.ssh|\.aws|\.config|\.codex|\.claude|Library|prediction-market-research|Documents)(?:/|\b)',
}
COMPILED = {name: re.compile(pattern) for name, pattern in PATTERNS.items()}


def fingerprint(value):
    return hashlib.sha256(value).hexdigest()


class BoundedTarReader:
    """Bound decompressed headers and skipped payloads, not just yielded files."""
    def __init__(self, source, scanner, identity):
        self.source, self.scanner, self.identity = source, scanner, identity

    def read(self, size=-1):
        remaining = self.scanner.max_total - self.scanner.used
        if remaining <= 0:
            self.scanner.issue('tar_stream_byte_limit', self.identity)
            self.scanner.exhausted = True
            raise ValueError('bounded tar stream exhausted')
        amount = min(remaining, size) if size >= 0 else remaining
        data = self.source.read(amount)
        self.scanner.used += len(data)
        return data


class PrefixReader:
    """Replay a small sniffed prefix without seeking a decompressor."""
    def __init__(self, prefix, source):
        self.prefix, self.source = prefix, source

    def read(self, size):
        if size < 0:
            raise ValueError('Unbounded reads are not permitted')
        head, self.prefix = self.prefix[:size], self.prefix[size:]
        return head + self.source.read(size - len(head)) if len(head) < size else head


class Scanner:
    def __init__(self, *, max_total_bytes=256*1024**2, max_file_bytes=16*1024**2,
                 max_entries=10000, max_depth=5, max_findings=1000):
        limits = [max_total_bytes, max_file_bytes, max_entries, max_depth, max_findings]
        if any(type(n) is not int or n < 1 for n in limits):
            raise ValueError('All scan limits must be positive integers')
        self.max_total, self.max_file, self.max_entries, self.max_depth, self.max_findings = limits
        self.used, self.entries = 0, 0
        self.findings, self.issues = [], []
        self.exhausted = False

    def issue(self, kind, identity):
        # Identity is only an opaque hash: member names can themselves be secrets.
        item = {'reason': kind, 'object_id': fingerprint(identity.encode('utf-8', 'surrogatepass'))}
        if item not in self.issues and len(self.issues) < self.max_entries + 1:
            self.issues.append(item)

    def inspect(self, data, identity, region='content'):
        for category, pattern in COMPILED.items():
            for match in pattern.finditer(data):
                if len(self.findings) >= self.max_findings:
                    self.issue('finding_limit', identity)
                    return
                self.findings.append({'category': category, 'object_id': fingerprint(identity.encode('utf-8', 'surrogatepass')),
                                      'region': region, 'byte_offset': match.start()})

    def take_entry(self, identity):
        if self.entries >= self.max_entries:
            self.issue('entry_limit', identity)
            self.exhausted = True
            return False
        self.entries += 1
        self.inspect(identity.rsplit('!', 1)[-1].encode('utf-8', 'surrogatepass'), identity, 'name')
        return True

    def read(self, stream, size, identity):
        remaining = self.max_total - self.used
        if remaining <= 0:
            self.issue('total_byte_limit', identity)
            self.exhausted = True
            return None
        limit = min(self.max_file, remaining)
        data = stream.read(limit + 1)
        self.used += len(data)
        truncated = size > limit or len(data) > limit
        if truncated:
            self.issue('file_or_total_byte_limit', identity)
            self.inspect(data[:limit], identity)
            if self.used >= self.max_total:
                self.exhausted = True
            return None
        return data

    def content(self, raw, identity, depth):
        self.inspect(raw, identity)
        lower = identity.lower()
        is_zip = raw.startswith(b'PK\x03\x04') or lower.endswith(('.zip', '.whl'))
        is_tar = (len(raw) > 262 and raw[257:262] == b'ustar') or lower.endswith(('.tar', '.tar.gz', '.tgz', '.tar.bz2', '.tar.xz'))
        compressed = raw.startswith((b'\x1f\x8b', b'BZh', b'\xfd7zXZ'))
        if not (is_zip or is_tar or compressed):
            return
        if depth >= self.max_depth:
            self.issue('archive_depth_limit', identity)
            return
        try:
            if is_zip:
                with zipfile.ZipFile(io.BytesIO(raw)) as archive:
                    for member in archive.infolist():
                        child = identity + '!' + member.filename
                        if self.exhausted or not self.take_entry(child):
                            break
                        self.path_check(member.filename, child)
                        if member.is_dir():
                            continue
                        if (member.external_attr >> 16) & 0o170000 == 0o120000:
                            self.issue('archive_link_unfollowed', child)
                        if member.flag_bits & 1:
                            self.issue('encrypted_entry_unscanned', child)
                            continue
                        try:
                            with archive.open(member) as stream:
                                data = self.read(stream, member.file_size, child)
                            if data is not None:
                                self.content(data, child, depth + 1)
                        except Exception:
                            self.issue('archive_entry_unreadable', child)
            else:
                source = io.BytesIO(raw)
                if raw.startswith(b'\x1f\x8b'):
                    source = gzip.GzipFile(fileobj=source)
                elif raw.startswith(b'BZh'):
                    source = bz2.BZ2File(source)
                elif raw.startswith(b'\xfd7zXZ'):
                    source = lzma.LZMAFile(source)
                if compressed:
                    # Read at most one header, charged to the global budget.
                    # Replaying it is conservatively counted again below.
                    prefix = BoundedTarReader(source, self, identity).read(min(512, self.max_file))
                    header_is_tar = False
                    if len(prefix) == 512:
                        try:
                            tarfile.TarInfo.frombuf(prefix, 'utf-8', 'surrogateescape')
                            header_is_tar = True
                        except tarfile.HeaderError:
                            pass
                    source = PrefixReader(prefix, source)
                    if not is_tar and not header_is_tar:
                        child = identity + '!decompressed-content'
                        if self.take_entry(child):
                            data = self.read(source, 0, child)
                            if data is not None:
                                self.content(data, child, depth + 1)
                        return
                bounded = BoundedTarReader(source, self, identity)
                with tarfile.open(fileobj=bounded, mode='r|') as archive:
                    for member in archive:
                        child = identity + '!' + member.name
                        if self.exhausted or not self.take_entry(child):
                            break
                        self.path_check(member.name, child)
                        if member.isdir():
                            continue
                        if not member.isfile():
                            self.issue('archive_link_or_special_unscanned', child)
                            self.inspect(member.linkname.encode('utf-8', 'surrogatepass'), child, 'link_target')
                            continue
                        stream = archive.extractfile(member)
                        if stream is None:
                            self.issue('archive_entry_unreadable', child)
                            continue
                        with stream:
                            data = self.read(stream, member.size, child)
                        if data is not None:
                            self.content(data, child, depth + 1)
                        else:
                            self.issue('tar_remainder_unscanned_after_member_limit', identity)
                            break
        except Exception:
            self.issue('archive_unreadable_or_unsupported', identity)

    def path_check(self, name, identity):
        path = PurePosixPath(name)
        if path.is_absolute() or '..' in path.parts or '\\' in name or '\x00' in name:
            self.issue('unsafe_archive_member_path', identity)

    def filesystem(self, path, identity):
        if self.exhausted or not self.take_entry(identity):
            return
        try:
            if path.is_symlink():
                self.issue('filesystem_symlink_unfollowed', identity)
            elif path.is_dir():
                # No blanket exclusions: an explicitly selected release tree may
                # contain a private file that must not be silently skipped.
                for child in path.iterdir():
                    if self.exhausted:
                        break
                    self.filesystem(child, identity + '!' + child.name)
            elif path.is_file():
                with path.open('rb') as stream:
                    data = self.read(stream, path.stat().st_size, identity)
                if data is not None:
                    self.content(data, identity, 0)
            else:
                self.issue('missing_or_special_path', identity)
        except Exception:
            self.issue('filesystem_unreadable', identity)

    def scan(self, paths):
        if not paths:
            raise ValueError('Choose explicit paths')
        for index, supplied in enumerate(paths):
            path = Path(supplied)
            identity = f'input-{index}!' + path.name
            if self.exhausted:
                self.issue('input_unscanned_after_limit', identity)
            else:
                self.filesystem(path, identity)
        complete = not self.issues
        return {'schema': 'release-secret-scan-v1', 'status': 'findings' if self.findings else ('no_findings_within_scope' if complete else 'partial'),
                'complete': complete, 'inputs': len(paths), 'entries_seen': self.entries, 'bytes_read': self.used,
                'findings': self.findings, 'unscanned_or_limits': self.issues,
                'limitations': ['Heuristic byte patterns: no guarantee of finding all credentials, encodings or personal data.',
                                'No archives are extracted or executed; every readable Docker-save layer is scanned, including deleted files.',
                                'Opaque object IDs prevent leaking secret filenames; original explicit inputs are not printed.',
                                'No finding is a security certification. Any unreadable, linked, encrypted or bounded-out content prevents a complete result.']}


def main():
    parser = argparse.ArgumentParser(description='Bounded release scan with redacted output')
    parser.add_argument('paths', nargs='+')
    parser.add_argument('--max-total-bytes', type=int, default=256*1024**2)
    parser.add_argument('--max-file-bytes', type=int, default=16*1024**2)
    parser.add_argument('--max-entries', type=int, default=10000)
    parser.add_argument('--max-depth', type=int, default=5)
    parser.add_argument('--max-findings', type=int, default=1000)
    args = parser.parse_args()
    paths = args.paths
    del args.paths
    try:
        result = Scanner(**vars(args)).scan(paths)
    except (ValueError, OSError):
        print(json.dumps({'status': 'partial', 'complete': False, 'error': 'invalid_limits_or_input'}))
        return 2
    print(json.dumps(result, indent=2))
    return 1 if result['findings'] else (0 if result['complete'] else 2)


if __name__ == '__main__':
    raise SystemExit(main())
