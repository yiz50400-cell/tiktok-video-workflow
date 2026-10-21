"""Codex-native local execution: no browser, frontend build or running server."""
import argparse
import asyncio
import json
import os
from pathlib import Path
import sys

import httpx

from .api import create_app, ROOT
from .media import MAX_UPLOAD_BYTES, VIDEO_EXTENSIONS
from .analysis_attachment import load_analysis_attachment


def parser():
    result = argparse.ArgumentParser(description='Codex 视频工作流：直接执行并返回结构化结果')
    sub = result.add_subparsers(dest='command', required=True)
    sub.add_parser('capabilities', help='查看已接通能力')
    sub.add_parser('list', help='查询已保存任务')
    create = sub.add_parser('create', help='创建商品生产任务')
    create.add_argument('--title', required=True)
    create.add_argument('--product', required=True)
    create.add_argument('--market', choices=['MX', 'BR', 'ID', 'PH'], default='MX')
    create.add_argument('--voiceover-locale', default=None)
    create.add_argument('--style', default='达人演示')
    create.add_argument('--count', type=int, default=1)
    create.add_argument('--budget', type=float, default=0)
    create.add_argument('--owner', default='本机制作员')
    create.add_argument('--source', action='append', default=[])
    create.add_argument('--notes', default='')
    for command in ['show', 'cancel']:
        sub.add_parser(command).add_argument('job_id')
    importer = sub.add_parser('import', help='导入员工明确提供的视频，原文件保持不变')
    importer.add_argument('job_id')
    importer.add_argument('paths', nargs='+')
    analysis = sub.add_parser('attach-analysis', help='接入已有分析索引，不重复分析或代签批准')
    analysis.add_argument('job_id')
    analysis.add_argument('index_path', type=Path)
    batch = sub.add_parser('plan-batch', help='登记具体批量创意，保留修订；不配音或渲染')
    batch.add_argument('job_id')
    batch.add_argument('concepts_path', type=Path)
    language = sub.add_parser('set-language', help='选择任务的当地口播语言')
    language.add_argument('job_id')
    language.add_argument('voiceover_locale')
    inputs = sub.add_parser('pipeline-inputs', help='登记Codex整理的素材、稿件和制作证据')
    inputs.add_argument('job_id')
    inputs.add_argument('input_path', type=Path)
    pipeline = sub.add_parser('run', help='执行并恢复采集到本机交付链路，缺审阅资料时返回待办')
    pipeline.add_argument('job_id')
    evidence = sub.add_parser('pipeline-evidence', help='记录准确中文确认或内部本地化审核；来源须真实')
    evidence.add_argument('job_id')
    evidence.add_argument('evidence_path', type=Path)
    return result


def local_previews(value, data):
    """Resolve only actual saved asset files, for Codex native media display."""
    if isinstance(value, list):
        return [local_previews(item, data) for item in value]
    if not isinstance(value, dict):
        return value
    value = {key: local_previews(item, data) for key, item in value.items()}
    if value.get('media_url') and value.get('id', '').startswith('asset_'):
        media = data / 'media'
        stem = value['id']
        for suffix in sorted(VIDEO_EXTENSIONS):
            file = media / (stem + suffix)
            if file.is_file():
                value['local_media_path'] = str(file)
                break
        thumbnail = media / (stem + '.jpg')
        if thumbnail.is_file():
            value['local_thumbnail_path'] = str(thumbnail)
    return value


async def execute(args):
    data = Path(os.environ.get('WORKFLOW_DATA_DIR', ROOT / '.runtime')).resolve()
    app = create_app(data)
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                     base_url='http://127.0.0.1',
                                     headers={'Authorization': 'Bearer ' + (data / 'access.token').read_text(encoding='utf-8').strip(),
                                              'X-Workflow-Client': 'studio'}, timeout=60) as client:
            async def call(method, route, **kwargs):
                response = await client.request(method, route, **kwargs)
                if not response.is_success:
                    detail = response.json().get('detail', '操作失败')
                    if isinstance(detail, list):
                        detail = '；'.join(f"{item['loc'][-1]}：{item['msg']}" for item in detail)
                    raise ValueError(detail)
                return local_previews(response.json(), data)

            if args.command == 'capabilities':
                return await call('GET', '/api/capabilities')
            if args.command == 'list':
                return await call('GET', '/api/jobs')
            if args.command == 'create':
                return await call('POST', '/api/jobs', json={
                    'title': args.title, 'product': args.product, 'market': args.market,
                    'voiceover_locale': args.voiceover_locale, 'style': args.style,
                    'count': args.count, 'budget': args.budget, 'owner': args.owner,
                    'sources': args.source, 'notes': args.notes})
            job_id = args.job_id
            if not job_id.startswith('job_') or not job_id.removeprefix('job_').isalnum():
                raise ValueError('请提供 job_ 开头的真实任务 ID。')
            route = '/api/jobs/' + job_id
            if args.command == 'show':
                return await call('GET', route)
            if args.command == 'run':
                return await call('POST', route + '/pipeline/run')
            if args.command == 'pipeline-inputs':
                return await call('POST', route + '/pipeline/inputs',
                                  json=json.loads(args.input_path.read_text(encoding='utf-8')))
            if args.command == 'pipeline-evidence':
                return await call('POST', route + '/pipeline/evidence',
                                  json=json.loads(args.evidence_path.read_text(encoding='utf-8')))
            if args.command == 'cancel':
                return await call('POST', route + '/cancel')
            if args.command == 'set-language':
                return await call('POST', route + '/language', json={'voiceover_locale': args.voiceover_locale})
            if args.command == 'attach-analysis':
                return await call('POST', route + '/analyses',
                                  json=load_analysis_attachment(args.index_path))
            if args.command == 'plan-batch':
                try:
                    concepts = json.loads(args.concepts_path.read_text(encoding='utf-8'))
                except (json.JSONDecodeError, UnicodeError) as exc:
                    raise ValueError('批量创意文件必须是有效的 UTF-8 JSON。') from exc
                return await call('POST', route + '/batch/concepts', json=concepts)
            # Check every explicitly supplied path before committing any import.
            await call('GET', route)
            files = [Path(path) for path in args.paths]
            for file in files:
                if not file.is_absolute() or not file.is_file():
                    raise ValueError(f'素材路径不存在或不是绝对路径：{file}')
                if file.suffix.lower() not in VIDEO_EXTENSIONS:
                    raise ValueError(f'不是支持的视频文件：{file.name}')
                if file.stat().st_size > MAX_UPLOAD_BYTES:
                    raise ValueError(f'{file.name} 超过 250 MB，请先压缩或裁切。')
            imports = []
            for file in files:
                with file.open('rb') as stream:
                    imports.append(await call('POST', route + '/assets',
                                               files={'file': (file.name, stream, 'application/octet-stream')}))
            return {'job_id': job_id, 'imports': imports, 'job': await call('GET', route)}


def main():
    args = parser().parse_args()
    try:
        result = asyncio.run(execute(args))
    except (ValueError, OSError, httpx.HTTPError) as exc:
        print(json.dumps({'ok': False, 'error': str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0
