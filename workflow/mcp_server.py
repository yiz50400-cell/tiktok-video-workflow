"""Local Codex bridge to the persisted workflow service."""
from pathlib import Path
from typing import Any, Literal
import os
from urllib.parse import urlparse

import httpx
from mcp.server.fastmcp import FastMCP
from mcp.server.fastmcp.exceptions import ToolError
from pydantic import Field
from typing import Annotated

from .media import MAX_UPLOAD_BYTES, VIDEO_EXTENSIONS
from .cli import local_previews

ROOT = Path(__file__).resolve().parent.parent
API_URL = os.environ.get('WORKFLOW_API_URL', 'http://127.0.0.1:8765').rstrip('/')
DATA = Path(os.environ.get('WORKFLOW_DATA_DIR', str(ROOT / '.runtime')))
if urlparse(API_URL).hostname not in {'localhost', '127.0.0.1'}:
    raise ValueError('This review build connects to the local API only.')
mcp = FastMCP('video_workflow_mcp', log_level='WARNING')
READ = {'readOnlyHint': True, 'destructiveHint': False, 'idempotentHint': True, 'openWorldHint': False}
WRITE = {'readOnlyHint': False, 'destructiveHint': False, 'idempotentHint': False, 'openWorldHint': False}


async def call_api(method: str, path: str, **kwargs) -> Any:
    try:
        token = (DATA / 'access.token').read_text(encoding='utf-8').strip()
    except OSError as exc:
        raise ToolError('请先运行项目 scripts/start.sh 启动本机工作流，再重试。') from exc
    try:
        async with httpx.AsyncClient(base_url=API_URL, timeout=1900 if path.endswith('/pipeline/run') else 60,
                                     headers={'Authorization': 'Bearer ' + token,
                                              'X-Workflow-Client': 'studio'}) as client:
            response = await client.request(method, path, **kwargs)
    except httpx.HTTPError as exc:
        raise ToolError('本机工作流连接失败。请确认服务已启动；创建请求超时后先查询任务，避免重复创建。') from exc
    if not response.is_success:
        body = response.json()
        detail = body.get('detail', '服务拒绝此操作。')
        if isinstance(detail, list):
            detail = '；'.join(f"{item['loc'][-1]}：{item['msg']}" for item in detail)
        raise ToolError(str(detail))
    return local_previews(response.json(), DATA.resolve())


@mcp.tool(annotations=READ)
async def workflow_capabilities() -> dict[str, Any]:
    """Read integrated, standalone and out-of-scope stages of the local workflow."""
    return await call_api('GET', '/api/capabilities')


@mcp.tool(annotations=WRITE)
async def workflow_pipeline_inputs(job_id: str, inputs: dict[str, Any]) -> dict[str, Any]:
    """Attach task-owned source IDs, observation/draft paths and approved production directories.

    Inputs are prepared by Codex. They do not approve Chinese copy or claim user review.
    """
    return await call_api('POST', '/api/jobs/' + job_id + '/pipeline/inputs', json=inputs)


@mcp.tool(annotations={**WRITE, 'idempotentHint': True})
async def workflow_run_pipeline(job_id: str) -> dict[str, Any]:
    """Resume deterministic collection/analysis/production/local delivery stages.

    Missing semantic input or approval returns the real pending step. No platform actions.
    """
    return await call_api('POST', '/api/jobs/' + job_id + '/pipeline/run')


@mcp.tool(annotations=WRITE)
async def workflow_pipeline_evidence(job_id: str, ordinal: int, kind: Literal['chinese_confirmation','localization'],
                                     records: dict[str, Any]) -> dict[str, Any]:
    """Persist exact Chinese user confirmation or independent internal localization evidence.

    Call Chinese confirmation only after the human explicitly approved the displayed exact plan;
    records must include real user_reply and source_turn_id. Internal review is not user approval.
    """
    return await call_api('POST','/api/jobs/'+job_id+'/pipeline/evidence',json={'ordinal':ordinal,'kind':kind,'records':records})


@mcp.tool(annotations=WRITE)
async def workflow_create_job(
    title: Annotated[str, Field(min_length=1, max_length=100, description='员工指定的任务名称')],
    product: Annotated[str, Field(min_length=1, max_length=100, description='真实商品名称或 SKU')],
    market: Literal['MX', 'BR', 'ID', 'PH'] = 'MX',
    voiceover_locale: str | None = None,
    style: str = '达人演示',
    count: Annotated[int, Field(ge=1, le=100)] = 1,
    budget: Annotated[float, Field(ge=0, le=100000)] = 0,
    owner: str = '本机制作员',
    sources: list[str] | None = None,
    notes: str = '',
) -> dict[str, Any]:
    """Create a persisted task from the user's request. URLs are recorded, not downloaded.

    Return the task ID for resuming, current status and next_action. No paid calls or publishing.
    """
    result = await call_api('POST', '/api/jobs', json={'title': title, 'product': product,
                            'market': market, 'count': count, 'budget': budget,
                            'voiceover_locale': voiceover_locale, 'style': style,
                            'owner': owner, 'sources': sources or [], 'notes': notes})
    return result


@mcp.tool(annotations={**WRITE, 'idempotentHint': True})
async def workflow_plan_batch(job_id: str, concepts: list[dict[str, Any]]) -> dict[str, Any]:
    """Register actual creative concepts for numbered batch items; preserve revisions.

    Each concept needs ordinal, angle, hook, buyer_concern, proof_requirement,
    cta_direction and optional source_asset_ids belonging to this job. Exact duplicates
    are rejected. Concepts await review; this tool does not render or approve content.
    """
    if not job_id.startswith('job_') or not job_id.removeprefix('job_').isalnum():
        raise ToolError('请输入 job_ 开头的真实任务 ID。')
    return await call_api('POST', '/api/jobs/' + job_id + '/batch/concepts', json={'concepts': concepts})


@mcp.tool(annotations={**WRITE, 'idempotentHint': True})
async def workflow_register_reference_package(job_id: str, package: dict[str, Any]) -> dict[str, Any]:
    """Register a collected same-product-first advertising research package.

    References remain pending review. Different-product references are structure-only;
    this tool does not approve copy, synthesize speech, render, or publish.
    """
    if not job_id.startswith('job_') or not job_id.removeprefix('job_').isalnum():
        raise ToolError('请输入 workflow_create_job 返回的 job_ 开头任务 ID。')
    return await call_api('POST', '/api/jobs/' + job_id + '/reference-package',
                          json={'package': package})


@mcp.tool(annotations={**WRITE, 'idempotentHint': True})
async def workflow_select_language(job_id: str, voiceover_locale: str) -> dict[str, Any]:
    """Resolve the task's local voiceover language, including a pending Philippines choice.

    Choose fil-PH, en-PH or taglish-PH for PH. Other countries use their configured locale.
    This selects a target language; it does not translate, synthesize or approve voiceover.
    """
    if not job_id.startswith('job_') or not job_id.removeprefix('job_').isalnum():
        raise ToolError('请输入 job_ 开头的真实任务 ID。')
    return await call_api('POST', '/api/jobs/' + job_id + '/language',
                          json={'voiceover_locale': voiceover_locale})


@mcp.tool(annotations=READ)
async def workflow_list_jobs() -> dict[str, Any]:
    """List up to 200 real local tasks, newest first. Examples are excluded."""
    jobs = await call_api('GET', '/api/jobs')
    return {'jobs': [{**{key: job[key] for key in ['id', 'title', 'product', 'market', 'count',
                                                'voiceover_locale', 'style', 'status', 'created_at', 'next_action']},
                      'batch_progress': {key: job['batch'][key] for key in
                                         ['target_count', 'planned_count', 'rendered_count']}}
                     for job in jobs]}


@mcp.tool(annotations=READ)
async def workflow_get_job(job_id: str) -> dict[str, Any]:
    """Read a saved task, original-video previews, metadata and history by task ID."""
    if not job_id.startswith('job_') or not job_id.removeprefix('job_').isalnum():
        raise ToolError('请输入 workflow_create_job 返回的 job_ 开头任务 ID。')
    result = await call_api('GET', '/api/jobs/' + job_id)
    return result


@mcp.tool(annotations={**WRITE, 'idempotentHint': True})
async def workflow_import_video(job_id: str, path: str) -> dict[str, Any]:
    """Upload an explicitly user-supplied absolute local video path; preserve the original.

    Maximum 250 MB. Bytes go to the media API, not model context. Reference-workflow videos
    should not be treated as product footage unless the user explicitly requests that.
    """
    if not job_id.startswith('job_') or not job_id.removeprefix('job_').isalnum():
        raise ToolError('请先创建任务，并传入 job_ 开头的任务 ID。')
    file = Path(path)
    if not file.is_absolute() or not file.is_file():
        raise ToolError('请提供确实存在的视频绝对路径；远程服务不能读取员工电脑盘符。')
    if file.suffix.lower() not in VIDEO_EXTENSIONS:
        raise ToolError('支持 MP4、MOV、M4V、WebM、MKV 视频。')
    if file.stat().st_size > MAX_UPLOAD_BYTES:
        raise ToolError('素材超过 250 MB，请先裁切或压缩。')
    with file.open('rb') as stream:
        result = await call_api('POST', f'/api/jobs/{job_id}/assets',
                                files={'file': (file.name, stream, 'application/octet-stream')})
    return {'job_id': job_id, **result, 'next_action': '素材已入库；分镜与内容分析尚待接通。'}


if __name__ == '__main__':
    mcp.run(transport='stdio')
