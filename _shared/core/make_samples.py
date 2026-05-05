"""
Sample-email generator. Produces handwritten exemplar emails that follow
each bot's master prompt exactly, then renders them through the same
html_template the live bot uses. Output lands in <bot_dir>/previews/.

Used to populate Safari previews while the live Claude API key is being
refreshed. The bot code path (run.py preview N) is unchanged - this is
just an offline alternative that doesn't hit the Anthropic API.
"""
from pathlib import Path

from . import gallery, html_template


def _render(*, cfg, subject, body_text, hero_key, thumb_keys, fname,
            out_dir):
    thumbs = [(gallery.asset_path(k), gallery.caption(k))
              for k in thumb_keys[:3]]
    html = html_template.wrap_email(
        subject=subject,
        body_text=body_text,
        hero_path=gallery.asset_path(hero_key),
        thumbs=thumbs,
        brand_label=cfg.brand_label,
        brand_sub=cfg.brand_sub,
        accent=cfg.accent_color,
        cta_label=cfg.cta_label,
        cta_href=cfg.gallery_url,
        footer_line=cfg.footer_line,
        mode="preview",
    )
    out = Path(out_dir) / fname
    out.write_text(html)
    return out


def generate(cfg, samples):
    """samples: list of dicts with subject, body_text, hero_key, thumb_keys, fname."""
    out_dir = cfg.project_dir / "previews"
    out_dir.mkdir(exist_ok=True)
    for old in out_dir.glob("*.html"):
        old.unlink()
    paths = []
    for s in samples:
        paths.append(_render(
            cfg=cfg,
            subject=s["subject"],
            body_text=s["body_text"],
            hero_key=s["hero_key"],
            thumb_keys=s["thumb_keys"],
            fname=s["fname"],
            out_dir=out_dir,
        ))
    return paths
