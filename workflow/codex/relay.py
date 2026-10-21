"""Read the employee's selected relay and probe Codex without storing its key."""
from dataclasses import dataclass, field
import json
import os
from pathlib import Path
import subprocess
import tempfile
import time
import tomllib
from urllib.parse import urlsplit

from workflow.orchestration.process import run


@dataclass(frozen=True)
class Relay:
    provider: str
    model: str
    base_url: str
    credential_source: str
    key: str = field(repr=False)

    def public(self):
        values={'provider':self.provider,'model':self.model,'base_url':self.base_url,
                'wire_api':'responses','credential_source':self.credential_source,
                'requires_openai_auth':False}
        return {k:v.replace(self.key,'[REDACTED]') if isinstance(v,str) else v for k,v in values.items()}


def load_relay(config_path):
    try:
        config=tomllib.loads(Path(config_path).read_text(encoding='utf-8'))
    except (OSError,ValueError):
        raise ValueError('无法读取中转配置，请检查文件格式及权限') from None
    provider=config.get('model_provider')
    model=config.get('model')
    selected=config.get('model_providers',{}).get(provider,{})
    if not provider or not model or not isinstance(selected,dict):
        raise ValueError('配置须指定模型及自定义中转 provider')
    if selected.get('wire_api','responses')!='responses' or selected.get('requires_openai_auth',False):
        raise ValueError('本入口要求 Responses 中转及独立 Key 登录')
    if any(selected.get(k) for k in ('http_headers','env_http_headers','experimental_bearer_token_command')):
        raise ValueError('当前接入不支持额外请求头或命令凭据，请先补充适配')
    base=selected.get('base_url','')
    try:
        parsed=urlsplit(base)
        valid=(parsed.scheme in ('http','https') and parsed.hostname and not parsed.username
               and not parsed.password and not parsed.query and not parsed.fragment)
    except (TypeError,ValueError):valid=False
    if not valid:
        raise ValueError('中转地址须为不含凭据的 HTTP(S) 地址')
    key=selected.get('experimental_bearer_token')
    source='provider_token'
    if not key:
        env_key=selected.get('env_key')
        key=os.environ.get(env_key,'') if isinstance(env_key,str) else ''
        source='environment'
    if not isinstance(key,str) or not key.strip():
        raise ValueError('当前中转 Key 未配置或所需环境变量未设置')
    return Relay(provider,model,base,source,key)


def _isolated_config(relay):
    quote=lambda value:json.dumps(value,ensure_ascii=False)
    return ('model = '+quote(relay.model)+'\nmodel_provider = '+quote(relay.provider)+'\n'
            'model_reasoning_effort = "low"\nweb_search = "disabled"\n'
            '[model_providers.'+quote(relay.provider)+']\nname = "Employee relay"\n'
            'base_url = '+quote(relay.base_url)+'\nwire_api = "responses"\n'
            'requires_openai_auth = false\nenv_key = "WORKFLOW_RELAY_KEY"\n'
            'request_max_retries = 0\nstream_max_retries = 0\nstream_idle_timeout_ms = 30000\n')


def check_relay(codex,config_path,*,timeout=90,mode='connection'):
    if mode not in ('connection','tool'):raise ValueError('未知联调模式')
    relay=load_relay(config_path)
    report={'mode':mode,'configuration':relay.public(),'status':'failed'}
    started=time.perf_counter()
    with tempfile.TemporaryDirectory(prefix='workflow-relay-') as temporary:
        home=Path(temporary)/'codex-home';home.mkdir()
        workspace=Path(temporary)/'work';workspace.mkdir()
        (home/'config.toml').write_text(_isolated_config(relay),encoding='utf-8')
        token='RELAY_CONNECTION_OK' if mode=='connection' else 'CODEX_LOCAL_TOOL_OK'
        prompt='Reply with exactly RELAY_CONNECTION_OK. Do not use any tools.'
        if mode=='tool':
            (workspace/'probe.txt').write_text(token,encoding='utf-8')
            prompt=('Use the local shell tool to read only probe.txt in the current directory. '
                    'Do not inspect environment variables or other files. Then reply with its exact contents.')
        executable=[str(codex)] if isinstance(codex,(str,Path)) else [str(part) for part in codex]
        command=[*executable,'exec','--ephemeral','--ignore-rules','--skip-git-repo-check',
                 '--sandbox','read-only','--json','--color','never','-C',str(workspace),'-']
        try:
            result=run(command,cwd=workspace,timeout=timeout,input=prompt,
                       env={'CODEX_HOME':str(home),'WORKFLOW_RELAY_KEY':relay.key})
        except subprocess.TimeoutExpired:
            report['reason']='timeout'
        except OSError:
            report['reason']='codex_launch_failed'
        else:
            completed=False;answer=False;tools=0;event_errors=False
            for line in result.stdout.splitlines():
                try:event=json.loads(line)
                except ValueError:continue
                if not isinstance(event,dict):continue
                if event.get('type') in ('error','turn.failed'):event_errors=True
                if event.get('type')=='turn.completed':
                    completed=True
                    usage=event.get('usage') or {}
                    if isinstance(usage,dict):
                        report['usage']={k:usage[k] for k in ('input_tokens','cached_input_tokens','output_tokens')
                                         if type(usage.get(k)) is int and usage[k]>=0}
                item=event.get('item') or {}
                if not isinstance(item,dict):continue
                if event.get('type')=='item.completed' and item.get('type')=='agent_message':
                    answer=item.get('text','').strip()==token
                if (event.get('type')=='item.completed' and item.get('type')=='command_execution'
                        and item.get('exit_code')==0 and token in item.get('aggregated_output','')):
                    tools+=1
            report['local_tool_successes']=tools
            if result.returncode==0 and completed and answer and not event_errors and (mode!='tool' or tools>0):
                report['status']='passed'
            else:
                # Raw output is deliberately never persisted: even failure messages
                # from a relay can contain credentials or confidential headers.
                report['reason']='codex_failed' if result.returncode or event_errors else 'probe_evidence_missing'
            report['exit_code']=result.returncode
    report['elapsed_seconds']=round(time.perf_counter()-started,3)
    return report
