import importlib.util
import io
import json
from pathlib import Path
import subprocess
import sys
import tarfile
import zipfile

import pytest

MODULE = Path(__file__).resolve().parents[1] / 'scripts' / 'scan_release.py'
spec = importlib.util.spec_from_file_location('release_scan_under_test', MODULE)
scanner = importlib.util.module_from_spec(spec)
spec.loader.exec_module(scanner)


def tar_bytes(entries):
    target = io.BytesIO()
    with tarfile.open(fileobj=target, mode='w') as archive:
        for name, body in entries:
            member = tarfile.TarInfo(name)
            member.size = len(body)
            archive.addfile(member, io.BytesIO(body))
    return target.getvalue()


def zip_bytes(entries):
    target = io.BytesIO()
    with zipfile.ZipFile(target, 'w', compression=zipfile.ZIP_DEFLATED) as archive:
        for name, body in entries:
            archive.writestr(name, body)
    return target.getvalue()


@pytest.mark.parametrize('fake,category', [
    (b'sk-' + b'X' * 30, 'provider_key'),
    (b'sk-ant-api03-' + b'Y' * 30, 'provider_key'),
    (b'-----BEGIN ' + b'PRIVATE KEY-----', 'private_key'),
    (b'Authorization: Bearer ' + b'F' * 30, 'authorization_value'),
    (b'api_key="' + b'Q' * 30 + b'"', 'assigned_secret'),
    (b'/Users/' + b'fake-person/.codex/auth.json', 'local_account_path'),
])
def test_fake_secret_detected_without_echoing_value_or_name(tmp_path, fake, category):
    path = tmp_path / ('private-filename-' + 'Z' * 20)
    path.write_bytes(fake)
    result = scanner.Scanner().scan([path])
    assert category in {f['category'] for f in result['findings']}
    output = json.dumps(result)
    assert fake.decode() not in output
    assert path.name not in output
    assert str(tmp_path) not in output


def test_zip_and_nested_docker_save_scan_deleted_lower_layer(tmp_path):
    fake = b'sk-proj-' + b'Z' * 32
    first = tar_bytes([('root/credential', fake)])
    second = tar_bytes([('root/.wh.credential', b'')])
    saved = tar_bytes([('manifest.json', b'[{"Layers":["first/layer.tar","second/layer.tar"]}]'),
                       ('first/layer.tar', first), ('second/layer.tar', second)])
    path = tmp_path / 'image.zip'
    path.write_bytes(zip_bytes([('docker-save.tar', saved)]))
    result = scanner.Scanner().scan([path])
    assert result['complete']
    assert any(f['category'] == 'provider_key' for f in result['findings'])
    assert fake.decode() not in json.dumps(result)


def test_traversal_member_is_scanned_without_extraction_and_marks_incomplete(tmp_path):
    outside = tmp_path / 'escaped'
    path = tmp_path / 'unsafe.zip'
    path.write_bytes(zip_bytes([('../escaped', b'sk-' + b'A' * 30)]))
    result = scanner.Scanner().scan([path])
    assert not outside.exists()
    assert result['findings']
    assert not result['complete']
    assert 'unsafe_archive_member_path' in {i['reason'] for i in result['unscanned_or_limits']}


def test_zip_expansion_and_file_limit_never_claim_clean(tmp_path):
    path = tmp_path / 'compressed.zip'
    path.write_bytes(zip_bytes([('huge.txt', b'X' * 100000 + b'sk-' + b'A' * 30)]))
    result = scanner.Scanner(max_file_bytes=1024, max_total_bytes=4096).scan([path])
    assert result['status'] == 'partial'
    assert not result['complete']
    assert result['bytes_read'] <= 4097


def test_depth_and_entry_limits_report_partial(tmp_path):
    nested = zip_bytes([('deep.zip', zip_bytes([('inner.txt', b'ordinary')]))])
    path = tmp_path / 'outer.zip'
    path.write_bytes(nested)
    depth = scanner.Scanner(max_depth=1).scan([path])
    assert not depth['complete']
    assert any(i['reason'] == 'archive_depth_limit' for i in depth['unscanned_or_limits'])
    entries = scanner.Scanner(max_entries=1).scan([path])
    assert not entries['complete']
    assert any(i['reason'] == 'entry_limit' for i in entries['unscanned_or_limits'])


def test_unreadable_archive_missing_input_and_symlink_are_not_clean(tmp_path):
    broken = tmp_path / 'broken.tar'
    broken.write_bytes(b'not a tar archive')
    target = tmp_path / 'target'
    target.write_bytes(b'ordinary')
    link = tmp_path / 'link'
    link.symlink_to(target)
    result = scanner.Scanner().scan([broken, tmp_path / 'absent', link])
    assert not result['complete']
    reasons = {i['reason'] for i in result['unscanned_or_limits']}
    assert {'archive_unreadable_or_unsupported', 'missing_or_special_path', 'filesystem_symlink_unfollowed'} <= reasons


def test_archive_symlink_is_not_followed(tmp_path):
    target = io.BytesIO()
    with tarfile.open(fileobj=target, mode='w') as archive:
        member = tarfile.TarInfo('link')
        member.type = tarfile.SYMTYPE
        member.linkname = '/outside/private'
        archive.addfile(member)
    path = tmp_path / 'links.tar'
    path.write_bytes(target.getvalue())
    result = scanner.Scanner().scan([path])
    assert not result['complete']
    assert result['unscanned_or_limits'][0]['reason'] == 'archive_link_or_special_unscanned'


def test_clean_scope_and_cli_redacted_exit_codes(tmp_path):
    clean = tmp_path / 'clean.txt'
    clean.write_text('Only ordinary example content.')
    good = subprocess.run([sys.executable, str(MODULE), str(clean)], capture_output=True, text=True)
    assert good.returncode == 0
    assert json.loads(good.stdout)['status'] == 'no_findings_within_scope'
    fake = b'sk-' + b'W' * 30
    clean.write_bytes(fake)
    flagged = subprocess.run([sys.executable, str(MODULE), str(clean)], capture_output=True, text=True)
    assert flagged.returncode == 1
    assert fake.decode() not in flagged.stdout + flagged.stderr
    bad = subprocess.run([sys.executable, str(MODULE), str(clean), '--max-file-bytes', '0'], capture_output=True, text=True)
    assert bad.returncode == 2
    assert not json.loads(bad.stdout)['complete']


def test_exhausted_budget_labels_later_explicit_inputs_unscanned(tmp_path):
    a, b = tmp_path / 'a', tmp_path / 'b'
    a.write_bytes(b'x' * 50)
    b.write_bytes(b'ordinary')
    result = scanner.Scanner(max_total_bytes=10).scan([a, b])
    assert not result['complete']
    assert any(i['reason'] == 'input_unscanned_after_limit' for i in result['unscanned_or_limits'])


def test_compressed_tar_skip_and_pax_headers_are_bounded(tmp_path):
    import gzip
    target = io.BytesIO()
    with tarfile.open(fileobj=target, mode='w', format=tarfile.PAX_FORMAT) as archive:
        member = tarfile.TarInfo('ordinary')
        member.pax_headers = {'comment': 'x' * 1000000}
        member.size = 1000000
        archive.addfile(member, io.BytesIO(b'z' * 1000000))
    path = tmp_path / 'pax.tar.gz'
    path.write_bytes(gzip.compress(target.getvalue()))
    result = scanner.Scanner(max_total_bytes=8192, max_file_bytes=4096).scan([path])
    assert not result['complete']
    assert result['bytes_read'] <= 8193
    assert result['unscanned_or_limits']


def test_empty_adjacent_environment_values_do_not_cross_lines(tmp_path):
    path = tmp_path / '.env.example'
    path.write_bytes(b'OPENAI_API_KEY=\nANTHROPIC_API_KEY=\r\nSCRAPPA_API_KEY= \t\n')
    report = scanner.Scanner().scan([path])
    assert report['complete']
    assert not report['findings']


def test_real_secret_after_empty_environment_line_is_detected_on_own_line(tmp_path):
    path = tmp_path / '.env.example'
    fake = b'Q' * 30
    content = b'OPENAI_API_KEY=\nANTHROPIC_API_KEY=\t"' + fake + b'"\n'
    path.write_bytes(content)
    report = scanner.Scanner().scan([path])
    findings = [f for f in report['findings'] if f['category'] == 'assigned_secret']
    assert len(findings) == 1
    assert findings[0]['byte_offset'] == content.index(b'API_KEY', content.index(b'ANTHROPIC'))
    assert fake.decode() not in json.dumps(report)


@pytest.mark.parametrize('codec', ['gzip', 'bz2', 'lzma'])
def test_compressed_plaintext_is_scanned_and_redacted(tmp_path, codec):
    import importlib
    fake = b'sk-' + b'J' * 30
    path = tmp_path / 'log.compressed'
    path.write_bytes(importlib.import_module(codec).compress(b'log start\n' + fake + b'\n'))
    report = scanner.Scanner().scan([path])
    assert report['complete']
    assert any(f['category'] == 'provider_key' for f in report['findings'])
    assert fake.decode() not in json.dumps(report)


@pytest.mark.parametrize('codec', ['gzip', 'bz2', 'lzma'])
def test_compressed_plaintext_bomb_stops_at_budget(tmp_path, codec):
    import importlib
    path = tmp_path / 'bomb'
    path.write_bytes(importlib.import_module(codec).compress(b'z' * 2000000))
    report = scanner.Scanner(max_file_bytes=4096, max_total_bytes=8192).scan([path])
    assert not report['complete']
    assert report['bytes_read'] <= 8193
    assert any(i['reason'] == 'file_or_total_byte_limit' for i in report['unscanned_or_limits'])


@pytest.mark.parametrize('codec', ['gzip', 'bz2', 'lzma'])
def test_compressed_plaintext_truncation_never_claims_complete(tmp_path, codec):
    import importlib
    path = tmp_path / 'truncated'
    path.write_bytes(importlib.import_module(codec).compress(b'ordinary log' * 100)[:-8])
    report = scanner.Scanner().scan([path])
    assert not report['complete']
    assert report['unscanned_or_limits']


@pytest.mark.parametrize('codec', ['gzip', 'bz2', 'lzma'])
def test_compressed_tar_detected_by_header_without_extension(tmp_path, codec):
    import importlib
    fake = b'api_key=' + b'L' * 30
    path = tmp_path / 'archive'
    path.write_bytes(importlib.import_module(codec).compress(tar_bytes([('file.txt', fake)])))
    report = scanner.Scanner().scan([path])
    assert report['complete']
    member_id = scanner.fingerprint(b'input-0!archive!file.txt')
    assert any(f['object_id'] == member_id and f['category'] == 'assigned_secret' for f in report['findings'])
