"""Exercise the real config importer and subprocess boundary without network."""
import json
import os
from pathlib import Path
import sys
import pytest


@pytest.fixture
def config(tmp_path):
    path=tmp_path/'personal.config.toml'
    path.write_text('''model_provider = "custom"
model = "gpt-6-sol"
[model_providers.custom]
base_url = "https://relay.example/v1"
wire_api = "responses"
requires_openai_auth = false
experimental_bearer_token = "private-test-key"
[model_providers.unselected]
experimental_bearer_token = "wrong-provider-key"
''',encoding='utf-8')
    return path


def test_import_preserves_selected_provider_and_hides_credential(config):
    from workflow.codex.relay import load_relay
    relay=load_relay(config)
    assert relay.public()['model']=='gpt-6-sol'
    assert relay.public()['base_url']=='https://relay.example/v1'
    assert relay.key=='private-test-key'
    assert 'private-test-key' not in repr(relay)
    assert 'private-test-key' not in json.dumps(relay.public())


def test_environment_key_is_used_without_importing_other_providers(config,monkeypatch):
    from workflow.codex.relay import load_relay
    config.write_text(config.read_text().replace('experimental_bearer_token = "private-test-key"',
                                               'env_key = "EMPLOYEE_RELAY_KEY"'))
    monkeypatch.setenv('EMPLOYEE_RELAY_KEY','employee-key')
    assert load_relay(config).key=='employee-key'
    monkeypatch.delenv('EMPLOYEE_RELAY_KEY')
    with pytest.raises(ValueError):load_relay(config)


def test_probe_uses_isolated_home_and_env_only_credentials(config,tmp_path):
    from workflow.codex.relay import check_relay
    fake=tmp_path/'fake codex.py'
    fake.write_text('''import json,os,sys,tomllib
from pathlib import Path
home=Path(os.environ['CODEX_HOME'])
cfg=tomllib.loads((home/'config.toml').read_text(encoding='utf-8'))
provider=cfg['model_providers']['custom']
assert 'experimental_bearer_token' not in provider
assert provider['env_key']=='WORKFLOW_RELAY_KEY'
assert os.environ['WORKFLOW_RELAY_KEY']=='private-test-key'
assert 'private-test-key' not in ' '.join(sys.argv)
assert cfg['model']=='gpt-6-sol'
assert not (home/'auth.json').exists()
assert '--ephemeral' in sys.argv
assert sys.stdin.read()
print(json.dumps({'type':'item.completed','item':{'type':'agent_message','text':'RELAY_CONNECTION_OK'}}))
print(json.dumps({'type':'turn.completed','usage':{'input_tokens':10,'output_tokens':3}}))
''',encoding='utf-8')
    before=config.read_bytes()
    result=check_relay([sys.executable,str(fake)],config,timeout=10)
    assert result['status']=='passed'
    assert result['usage']['output_tokens']==3
    assert config.read_bytes()==before
    assert 'private-test-key' not in json.dumps(result)


def test_failed_probe_does_not_persist_raw_credentials(config,tmp_path):
    from workflow.codex.relay import check_relay
    fake=tmp_path/'fake.py'
    fake.write_text("import os,sys; print(os.environ['WORKFLOW_RELAY_KEY']); print('Bearer '+os.environ['WORKFLOW_RELAY_KEY'],file=sys.stderr); sys.exit(1)")
    result=check_relay([sys.executable,str(fake)],config,timeout=10)
    assert result['status']=='failed'
    assert 'private-test-key' not in json.dumps(result)
    assert 'Bearer' not in json.dumps(result)


def test_text_claim_does_not_count_as_a_local_tool_execution(config,tmp_path):
    from workflow.codex.relay import check_relay
    fake=tmp_path/'fake.py'
    fake.write_text("import json; print(json.dumps({'type':'item.completed','item':{'type':'agent_message','text':'CODEX_LOCAL_TOOL_OK'}})); print(json.dumps({'type':'turn.completed'}))")
    assert check_relay([sys.executable,str(fake)],config,timeout=10,mode='tool')['status']=='failed'


@pytest.mark.parametrize('replacement', [
    ('requires_openai_auth = false','requires_openai_auth = true'),
    ('wire_api = "responses"','wire_api = "chat"'),
    ('https://relay.example/v1','https://private-test-key@relay.example/v1'),
])
def test_unsupported_auth_or_transport_fails_without_exposing_values(config,replacement):
    from workflow.codex.relay import load_relay
    config.write_text(config.read_text().replace(*replacement))
    with pytest.raises(ValueError) as error:load_relay(config)
    assert 'private-test-key' not in str(error.value)
