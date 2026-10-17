import importlib.util
from pathlib import Path
import zipfile
import pytest

spec = importlib.util.spec_from_file_location('distribution_check', Path(__file__).parents[1]/'scripts/check_distribution.py')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def test_dashboard_without_hidden_references(tmp_path):
    p = tmp_path/'test.whl'
    with zipfile.ZipFile(p, 'w') as z:
        z.writestr('web_research_sim/static/index.html','fixture')
    assert module.check(p)['package_boundary']=='passed'


@pytest.mark.parametrize('name',['evaluation/answer.json','evidence/run.json','data/account.json','.env','../escape','/absolute'])
def test_refuses_private_or_unsafe_members(tmp_path,name):
    p = tmp_path/'test.whl'
    with zipfile.ZipFile(p,'w') as z:
        z.writestr('web_research_sim/static/index.html','fixture')
        z.writestr(name,'synthetic fixture')
    with pytest.raises(ValueError):
        module.check(p)
