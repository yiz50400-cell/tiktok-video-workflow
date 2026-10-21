from workflow.unattended.vision import KNOWN_CTA_TEXTS, ReviewOverlay, render_review_prompt, evaluate_review
from workflow.unattended.producer import CTA_EXAMPLE


def _prompt(captions, cta):
    shots = [{"path": f"/x/{i}.mp4"} for i in range(3)]
    return render_review_prompt({"product": "便携颈挂风扇"}, "U 形颈挂风扇", shots, captions, cta)


def test_review_prompt_lists_our_overlays_and_keeps_source_text_rule():
    p = _prompt(["Init agad? Hands-free cooling exists! 😩", "Add to cart na! Stay cool 🔥"], "ADD TO CART NA!")
    assert "{{" not in p
    assert "Init agad? Hands-free cooling exists! 😩" in p and "Add to cart na! Stay cool 🔥" in p
    assert "ADD TO CART NA!" in p and "颜色不固定" in p
    assert "粉色胶囊按钮" not in p  # old hard-coded colour caused false positives on yellow/red buttons
    assert "不要因此放松" in p and "清单之外" in p


def test_known_cta_texts_derived_from_cta_example_and_overlay_dedupes():
    assert set(KNOWN_CTA_TEXTS) == set(CTA_EXAMPLE.values())
    assert "ADD TO CART" not in KNOWN_CTA_TEXTS and "BUY NOW!" not in KNOWN_CTA_TEXTS
    out = ReviewOverlay.texts(["a", "a", ""], "ADD TO CART NA!")
    assert out == ["a", "ADD TO CART NA!"]


def test_other_market_cta_is_not_ignored_and_counts_as_source_text():
    import json
    for market, cta in CTA_EXAMPLE.items():
        ours = ReviewOverlay.texts(["cap"], cta)
        others = [c for m, c in CTA_EXAMPLE.items() if m != market]
        assert cta in ours and not any(o in ours for o in others)
        p = _prompt(["cap"], cta)
        line = next(l for l in p.splitlines() if "叠加文案清单" in l)
        assert json.dumps(ours, ensure_ascii=False) in line
        assert not any(o in line for o in others)
    # e.g. a BR CTA burned into source footage of a PH task is judged source text
    tiles = [{"i": 0, "product_visible": True, "product_prominent": True, "same_product": True,
              "source_burned_text": "heavy", "note": "原片里有 GARANTA A SUA NO CARRINHO!"}]
    assert any("原片烧录文字" in i for i in evaluate_review({"tiles": tiles}, 1))


def test_real_source_text_still_rejected():
    tiles = [{"i": 0, "product_visible": True, "product_prominent": True, "same_product": True,
              "source_burned_text": "heavy", "note": "原片价格字幕"}]
    assert any("原片烧录文字" in i for i in evaluate_review({"tiles": tiles}, 1))


def test_review_prompt_has_no_loose_cta_wording():
    p = _prompt(["cap"], "ADD TO CART NA!")
    assert "同类下单号召" not in p
    assert "只有文字与「ADD TO CART NA!」一致的按钮才是我们的" in p
    for loose in ("同类", "类似CTA", "类似 CTA", "之类的号召"):
        assert loose not in p
