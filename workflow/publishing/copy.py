"""Select exact reviewed phrases instead of inventing publication claims."""
import unicodedata


def derive_copy(request, creative, localization):
    if any(key in request for key in ("title", "caption", "hashtags", "title_zh")):
        raise ValueError("发布文案仅支持已审核文字引用；新文案须走中文确认与内部本地化审核")
    segments = {item["line"]: item for item in localization["segments"]}
    if len(segments) != len(localization["segments"]):
        raise ValueError("本地化行号重复")

    def phrase(ref):
        line, field = ref.get("line"), ref.get("field")
        if (type(line) is not int or line < 1 or line > len(creative["lines"])
                or line not in segments or field not in ("visible_text", "text")):
            raise ValueError("发布文案引用行或字段无效")
        zh_key = "visible_zh" if field == "visible_text" else "zh"
        local, zh = segments[line].get(field), creative["lines"][line - 1].get(zh_key)
        if not isinstance(local, str) or not local.strip() or not isinstance(zh, str) or not zh.strip():
            raise ValueError("发布文案引用缺少准确中外文原句")
        if "\n" in local or "\r" in local:
            raise ValueError("发布文案原句含非法换行")
        return local, zh

    refs = request.get("title_refs")
    if not isinstance(refs, list) or not 1 <= len(refs) <= 3:
        raise ValueError("发布文案需要1至3个已审核整句引用")
    selected = [phrase(ref) for ref in refs]
    title = " | ".join(item[0] for item in selected)
    title_zh = "｜".join(item[1] for item in selected)
    hashtags, tag_evidence, tag_context_zh = [], [], []
    tag_refs = request.get("hashtag_refs", [])
    if not isinstance(tag_refs, list) or len(tag_refs) > 5:
        raise ValueError("发布文案最多5个标签引用")
    for ref in tag_refs:
        if ref.get("field") != "visible_text":
            raise ValueError("标签只支持已审核可见文字中的连续词组")
        text, zh = phrase(ref)
        words = text.split()
        indices = ref.get("tokens")
        if (not isinstance(indices, list) or not indices
                or any(type(i) is not int or i < 0 or i >= len(words) for i in indices)
                or indices != list(range(indices[0], indices[0] + len(indices)))):
            raise ValueError("发布文案标签词组必须连续且索引有效")
        chosen = " ".join(words[i] for i in indices)
        normalized = unicodedata.normalize("NFKD", chosen)
        clean = " ".join("".join(c for c in word if c.isalnum())
                         for word in normalized.split())
        tag = "#" + "".join(word[:1].upper() + word[1:] for word in clean.split())
        if tag == "#" or len(tag) > 100:
            raise ValueError("发布文案标签不能为空或过长")
        if tag not in hashtags:
            hashtags.append(tag)
            tag_evidence.append({**ref, "source_text": text, "selected_text": chosen, "hashtag": tag})
        if zh not in tag_context_zh:
            tag_context_zh.append(zh)
    caption = title + ("\n" + " ".join(hashtags) if hashtags else "")
    if len(caption) > 2200:
        raise ValueError("发布文案超过本地2200字符准备上限")
    return {"title": title, "title_zh": title_zh, "hashtags": hashtags, "caption": caption,
            "tag_context_zh": tag_context_zh, "title_refs": refs, "tag_evidence": tag_evidence,
            "review": {"method": "exact_reviewed_phrase_reuse_and_tag_normalization",
                       "new_translation_generated": False,
                       "localization_review": "inherited_bound_independent_internal_review",
                       "chinese_title_approval": "derived_within_approved_meaning_not_separately_user_reviewed",
                       "foreign_text_user_review": "waived_not_user_reviewed",
                       "new_claims": [], "platform_caption_delivery": "not_attempted"}}
