"""Persist real batch concepts; pending slots are never finished videos."""
from copy import deepcopy
import hashlib
import json
import unicodedata

from pydantic import BaseModel, ConfigDict, Field

from .store import now


class ConceptInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra='forbid')
    ordinal: int = Field(ge=1, le=100, strict=True)
    angle: str = Field(min_length=1, max_length=300)
    hook: str = Field(min_length=1, max_length=600)
    buyer_concern: str = Field(min_length=1, max_length=600)
    proof_requirement: str = Field(min_length=1, max_length=1000)
    cta_direction: str = Field(min_length=1, max_length=600)
    source_asset_ids: list[str] = Field(default_factory=list, max_length=30)


class ConceptsInput(BaseModel):
    model_config = ConfigDict(extra='forbid')
    concepts: list[ConceptInput] = Field(min_length=1, max_length=100)


def new_batch(count):
    if isinstance(count, bool) or not isinstance(count, int) or not 1 <= count <= 100:
        raise ValueError('目标视频数量必须是 1–100 的整数。')
    return {'status': 'WAITING_CONCEPTS', 'target_count': count, 'planned_count': 0,
            'rendered_count': 0, 'representative_count': min(3, count),
            'sample_review_status': 'pending', 'material_capacity_status': 'not_assessed',
            'items': [{'ordinal': ordinal, 'representative': ordinal <= 3,
                       'status': 'WAITING_CONCEPT', 'concept': None, 'revisions': [],
                       'human_approval_status': 'pending_user'}
                      for ordinal in range(1, count + 1)]}


def semantic_key(concept):
    # Angle labels, music choices and asset order cannot disguise identical content.
    def normalize(value):
        value = unicodedata.normalize('NFKC', value).casefold()
        return ''.join(c for c in value if c.isalnum())
    return tuple(normalize(concept[key]) for key in
                 ('hook', 'buyer_concern', 'proof_requirement', 'cta_direction'))


def register_concepts(batch, concepts, allowed_assets):
    updated = deepcopy(batch)
    ordinals = [concept['ordinal'] for concept in concepts]
    if len(set(ordinals)) != len(ordinals):
        raise ValueError('同一次提交不能重复指定视频序号。')
    for concept in concepts:
        ordinal = concept['ordinal']
        if not 1 <= ordinal <= updated['target_count']:
            raise ValueError('视频序号超出该批次目标数量。')
        if set(concept['source_asset_ids']) - set(allowed_assets):
            raise ValueError('引用的素材必须已经导入本任务。')
        item = updated['items'][ordinal - 1]
        if item['concept'] == concept:
            continue
        item['concept'] = deepcopy(concept)
        fingerprint = hashlib.sha256(json.dumps(concept, ensure_ascii=False,
                                                sort_keys=True).encode()).hexdigest()
        item['revisions'].append({'version': len(item['revisions']) + 1,
                                  'created_at': now(), 'sha256': fingerprint,
                                  'concept': deepcopy(concept)})
        item['status'] = 'CONCEPT_REVIEW'
        item['human_approval_status'] = 'pending_user'
        updated['sample_review_status'] = 'pending'
    concepts = [item['concept'] for item in updated['items'] if item['concept'] is not None]
    keys = [semantic_key(concept) for concept in concepts]
    if len(keys) != len(set(keys)):
        raise ValueError('发现重复创意；仅更换角度标签、音乐或镜头顺序不能计为新内容。')
    updated['planned_count'] = len(concepts)
    updated['status'] = ('CONCEPT_REVIEW' if len(concepts) == updated['target_count'] else
                         'PARTIALLY_PLANNED' if concepts else 'WAITING_CONCEPTS')
    return updated
