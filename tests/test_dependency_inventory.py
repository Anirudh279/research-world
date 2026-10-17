import importlib.util
from pathlib import Path
import pytest

spec = importlib.util.spec_from_file_location('dependency_inventory', Path(__file__).parents[1] / 'scripts/dependency_inventory.py')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def fixture_root(tmp_path):
    (tmp_path / 'uv.lock').write_text('''version = 1
[[package]]
name = "fake-dep"
version = "1.0"
source = { registry = "https://example.invalid/simple" }
wheels = [{url="https://example.invalid/a.whl",hash="sha256:example"}]
[[package]]
name = "conditional-dep"
version = "2.0"
source = { registry = "https://example.invalid/simple" }
''')
    info = tmp_path / '.venv/lib/python3.11/site-packages/fake_dep-1.0.dist-info'
    info.mkdir(parents=True)
    (info / 'METADATA').write_text('Metadata-Version: 2.4\nName: fake-dep\nVersion: 1.0\nLicense-Expression: MIT\nLicense-File: LICENSE\n\n')
    (info / 'licenses').mkdir()
    (info / 'licenses/LICENSE').write_text('Actual declared license text.\n')
    return info


def test_reproducible_declared_metadata_and_conditional_unknown(tmp_path):
    fixture_root(tmp_path)
    a, notices = module.inventory(tmp_path)
    assert module.inventory(tmp_path) == (a, notices)
    absent, present = a['packages']
    assert absent['license_status'] == 'unknown_uninstalled_or_version_mismatch'
    assert present['installed_match']
    assert present['license_metadata'][0]['expression'] == 'MIT'
    assert present['wheels'][0]['hash'] == 'sha256:example'
    assert 'Actual declared license text.' in notices
    assert a['lock_sha256']


def test_license_symlink_and_size_bounds(tmp_path):
    venv = tmp_path / '.venv'
    venv.mkdir()
    outside = tmp_path / 'private'
    outside.write_text('DO NOT COPY')
    link = venv / 'LICENSE'
    link.symlink_to(outside)
    result, text = module.read_license(link, venv)
    assert result['status'] == 'outside_venv_rejected' and text is None
    link.unlink()
    link.write_bytes(b'x' * (module.MAX_FILE + 1))
    assert module.read_license(link, venv)[0]['status'] == 'size_limit'


def test_metadata_symlink_rejected_before_read(tmp_path):
    info = fixture_root(tmp_path)
    (info / 'METADATA').unlink()
    outside = tmp_path / 'outside'
    outside.write_text('not metadata')
    (info / 'METADATA').symlink_to(outside)
    with pytest.raises(ValueError, match='Unsafe'):
        module.inventory(tmp_path)
