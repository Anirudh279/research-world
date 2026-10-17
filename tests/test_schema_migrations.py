import sqlite3
from concurrent.futures import ThreadPoolExecutor
import pytest
from web_research_sim.core import Store,SimError
from web_research_sim import schema


def test_v1_upgrade_retains_frozen_bytes_and_spent_reservations(tmp_path):
    store=Store(tmp_path);wid=store.create_world('Legacy world')['id']
    doc=store.add_capture(wid,url='https://fixture.example',body=b'original',text='text',captured_at='2020-01-01T00:00:00Z')
    snapshot=store.freeze(wid)['id'];before=store.objects.get(snapshot)
    store.reserve(wid,'capture_calls','uncertain',{'url':'https://fixture.example'})
    with store.db(write=True) as db:
        db.execute("UPDATE metadata SET value='1' WHERE key='schema_version'")
        db.execute('DROP TABLE schema_migrations')
    with ThreadPoolExecutor(max_workers=2) as pool:list(pool.map(lambda _:Store(tmp_path),range(2)))
    upgraded=Store(tmp_path)
    assert upgraded.objects.get(snapshot)==before
    assert upgraded.replay(snapshot).open(doc['id'])['text']=='text'
    assert upgraded.usage(wid)['capture_calls']['reserved']==1
    with upgraded.db() as db:
        assert db.execute("SELECT value FROM metadata WHERE key='schema_version'").fetchone()[0]=='2'
        assert db.execute('SELECT from_version,to_version FROM schema_migrations').fetchall()[0][:]==('1','2')
        assert db.execute('SELECT count(*) FROM schema_migrations').fetchone()[0]==1


def test_unknown_schema_is_rejected_without_ddl_or_file_changes(tmp_path):
    path=tmp_path/'state.sqlite3'
    with sqlite3.connect(path) as db:
        db.execute('CREATE TABLE metadata(key TEXT PRIMARY KEY,value TEXT NOT NULL)')
        db.execute("INSERT INTO metadata VALUES('schema_version','999')")
    before=path.read_bytes()
    with pytest.raises(SimError):Store(tmp_path)
    assert path.read_bytes()==before
    with sqlite3.connect(path) as db:
        assert [r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")]==['metadata']


def test_ddl_failure_rolls_back_upgrade(tmp_path,monkeypatch):
    path=tmp_path/'state.sqlite3'
    with sqlite3.connect(path) as db:
        db.execute('CREATE TABLE metadata(key TEXT PRIMARY KEY,value TEXT NOT NULL)')
        db.execute("INSERT INTO metadata VALUES('schema_version','1')")
    monkeypatch.setattr(schema,'SCHEMA',schema.SCHEMA+'; THIS IS INVALID SQL;')
    with pytest.raises(sqlite3.Error):Store(tmp_path)
    with sqlite3.connect(path) as db:
        assert db.execute("SELECT value FROM metadata WHERE key='schema_version'").fetchone()[0]=='1'
        assert not db.execute("SELECT name FROM sqlite_master WHERE name='worlds'").fetchall()
