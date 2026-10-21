"""Market choices shared by task intake and localized creative planning."""
from copy import deepcopy


MARKETS = {
    'MX': {'name': '墨西哥', 'default_locale': 'es-MX', 'locale_options': ['es-MX'], 'currency': 'MXN'},
    'BR': {'name': '巴西', 'default_locale': 'pt-BR', 'locale_options': ['pt-BR'], 'currency': 'BRL'},
    'ID': {'name': '印尼', 'default_locale': 'id-ID', 'locale_options': ['id-ID'], 'currency': 'IDR'},
    'PH': {'name': '菲律宾', 'default_locale': None,
           'locale_options': ['fil-PH', 'en-PH', 'taglish-PH'], 'currency': 'PHP'},
}


def market_profile(market, voiceover_locale=None):
    if market not in MARKETS:
        raise ValueError('市场必须为 MX、BR、ID 或 PH。')
    profile = deepcopy(MARKETS[market])
    locale = voiceover_locale or profile.pop('default_locale')
    profile.pop('default_locale', None)
    if locale is not None and locale not in profile['locale_options']:
        raise ValueError(f"{market} 口播语言可选：{', '.join(profile['locale_options'])}")
    return {'code': market, **profile, 'voiceover_locale': locale,
            'language_status': 'selected' if locale else 'pending_selection'}


def plan_voice_probes(catalog, texts):
    """Validate real catalog entries before submitting language-specific probes."""
    rows = []
    seen = set()
    for market, candidates in catalog.items():
        for candidate in candidates:
            locale, voice = candidate['Locale'], candidate['ShortName']
            market_profile(market, locale)
            if not voice.startswith(locale + '-') or (market, voice) in seen:
                raise ValueError('voice catalog locale mismatch or duplicate')
            text = texts.get(locale)
            if not isinstance(text, str) or not text.strip():
                raise ValueError('each probe needs its own target-language text')
            seen.add((market, voice))
            rows.append({'market': market, 'locale': locale, 'voice': voice,
                         'text': text, 'rate': '+0%', 'volume': '+0%', 'pitch': '+0Hz'})
    if not rows:
        raise ValueError('empty voice probe catalog')
    return rows
