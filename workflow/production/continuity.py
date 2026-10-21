"""Validate recorded source playback review, including cuts inside each source span."""
import math


def validate_continuity(config):
    review=config.get('source_continuity_review',{})
    if review.get('status')!='passed_codex_source_review' or not review.get('reviewer'):
        raise ValueError('缺少原片切点和连续动作复核记录')
    shots=review.get('shots',[])
    if len(shots)!=len(config['windows']):raise ValueError('连续动作复核未覆盖所有镜头')
    cuts=[]
    for index,(window,shot) in enumerate(zip(config['windows'],shots),1):
        sha=config['sources'][window.get('source_index',0)]['sha256']
        start,end=window['source_start'],window['source_end']
        if (shot.get('index')!=index or shot.get('source_sha256')!=sha
                or shot.get('source_start')!=start or shot.get('source_end')!=end
                or shot.get('action_complete') is not True):
            raise ValueError('连续动作复核与实际素材区间不匹配')
        inside=shot.get('internal_cuts')
        if (not isinstance(inside,list) or any(type(t) not in (int,float) or not math.isfinite(t)
                                             or not start<t<end for t in inside)
                or inside!=sorted(set(inside))):raise ValueError('无效的原片内部切点')
        boundaries=[start,*inside,end]
        short=[(a,b) for a,b in zip(boundaries,boundaries[1:]) if b-a<2-1e-6]
        checks=shot.get('short_shot_reviews',[])
        if any(not any(row.get('range')==[a,b] and isinstance(row.get('reason'),str) and row['reason'].strip()
                       for row in checks) for a,b in short):
            raise ValueError('短于2秒的实际镜头须逐项说明观看完整性')
        cuts.extend(window['timeline_start']+t-start for t in inside)
    return {'status':review['status'],'reviewer':review['reviewer'],'internal_cut_seconds':cuts,
            'scope':'recorded_source_playback_review_not_automatic_quality_acceptance'}
