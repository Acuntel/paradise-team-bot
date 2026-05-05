"""
Warm Paradise email HTML. Single template used by all four bots, with
per-bot accent colour, header label, and CTA. Renders in two modes:

  - "preview": images inlined as base64 data: URIs so the .html file is
    self-contained and renders in any browser, no file:// path lookups.
  - "draft":   images attached as multipart/related with cid: refs (the
    sender.py builds the message from those tuples).
"""
import base64
import html as html_mod
import mimetypes
import os


def _esc(s):
    return html_mod.escape(str(s or ""))


def _data_uri(path):
    mime = mimetypes.guess_type(path)[0] or "image/png"
    with open(path, "rb") as f:
        b64 = base64.b64encode(f.read()).decode("ascii")
    return f"data:{mime};base64,{b64}"


def _paragraphs_html(body_text):
    paras = [p.strip() for p in body_text.split("\n\n") if p.strip()]
    out = []
    for p in paras:
        safe = _esc(p).replace("\n", "<br>")
        out.append(f"""
    <tr><td style="padding:0 48px;">
      <p style="font-family:'Helvetica Neue',Helvetica,Arial,sans-serif;
                font-size:16px;color:#2A2A2E;line-height:1.75;margin:0 0 18px 0;">
        {safe}
      </p>
    </td></tr>""")
    return "".join(out)


def _gallery_html(thumbs, mode):
    cells = []
    for i, (path, caption) in enumerate(thumbs):
        if mode == "preview":
            src = _data_uri(path)
        else:
            src = f"cid:thumb{i}"
        cells.append(f"""
      <td width="33%" valign="top" style="padding:5px;">
        <img src="{src}" alt="" width="170" height="130"
             style="display:block;width:100%;height:130px;object-fit:cover;
                    border:0;border-radius:3px;outline:none;">
        <p style="font-family:Helvetica,Arial,sans-serif;font-size:10.5px;
                  color:#8A8478;letter-spacing:0.04em;
                  margin:7px 0 0 0;text-align:center;line-height:1.4;">
          {_esc(caption)}
        </p>
      </td>""")
    return f"""
    <tr><td style="padding:6px 44px 0 44px;">
      <table width="100%" cellpadding="0" cellspacing="0" border="0"
             role="presentation"><tr>{''.join(cells)}</tr></table>
    </td></tr>"""


def wrap_email(*, subject, body_text, hero_path, thumbs,
               brand_label, brand_sub, accent, cta_label, cta_href,
               footer_line, mode="preview",
               tracking_open_url=None, unsubscribe_url=None):
    """thumbs: list of (path, caption). hero_path: absolute path.

    Optional tracking params (set when the dashboard is wired):
      tracking_open_url -- 1x1 pixel URL embedded at end of body
      unsubscribe_url   -- footer link + List-Unsubscribe header source
    Both are silently omitted when None."""
    if mode == "preview":
        hero_src = _data_uri(hero_path)
    else:
        hero_src = "cid:hero"

    unsub_block = (
        f'<a href="{unsubscribe_url}" style="color:#B6AFA1;text-decoration:underline;">unsubscribe</a> · '
        if unsubscribe_url else ""
    )
    open_pixel = (
        f'<img src="{tracking_open_url}" width="1" height="1" alt="" '
        f'style="display:block;border:0;width:1px;height:1px;" />'
        if tracking_open_url else ""
    )

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1.0">
<title>{_esc(subject)}</title>
</head>
<body style="margin:0;padding:0;background-color:#EEECE6;
             font-family:'Helvetica Neue',Helvetica,Arial,sans-serif;
             -webkit-font-smoothing:antialiased;">

<table cellpadding="0" cellspacing="0" border="0" width="640" align="center"
       style="max-width:640px;margin:0 auto;background-color:#FBFAF6;"
       role="presentation">

  <tr><td style="height:3px;background:linear-gradient(90deg,transparent,{accent} 25%,{accent} 75%,transparent);"></td></tr>

  <tr><td style="padding:42px 48px 0;text-align:center;">
    <p style="font-family:Georgia,'Times New Roman',serif;font-size:17px;
              letter-spacing:0.34em;text-transform:uppercase;color:#1F1F22;
              margin:0;line-height:1;font-weight:400;">
      {_esc(brand_label)}
    </p>
    <p style="font-family:Helvetica,Arial,sans-serif;font-size:9.5px;
              font-weight:300;letter-spacing:0.26em;text-transform:uppercase;
              color:#8A8478;margin:10px 0 0 0;">
      {_esc(brand_sub)}
    </p>
  </td></tr>

  <tr><td>
    <div style="width:42px;height:1px;background:#D1CCC2;margin:30px auto;"></div>
  </td></tr>

  <tr><td style="background:#1F1F22;">
    <img src="{hero_src}" alt="" width="640" height="360"
         style="display:block;width:100%;max-width:640px;height:auto;
                border:0;outline:none;text-decoration:none;">
  </td></tr>

  <tr><td style="height:30px;"></td></tr>

  <tr><td style="padding:0 48px 8px;">
    <p style="font-family:Georgia,'Times New Roman',serif;font-size:23px;
              color:#1F1F22;text-align:center;margin:0;line-height:1.4;
              letter-spacing:-0.005em;">
      {_esc(subject)}
    </p>
  </td></tr>

  <tr><td style="height:18px;"></td></tr>

  {_paragraphs_html(body_text)}

  <tr><td>
    <div style="width:42px;height:1px;background:#D1CCC2;margin:18px auto 22px;"></div>
  </td></tr>

  {_gallery_html(thumbs, mode)}

  <tr><td style="height:32px;"></td></tr>

  <tr><td style="text-align:center;padding:0 48px;">
    <a href="{cta_href}"
       style="display:inline-block;padding:15px 40px;
              border:1px solid {accent};color:{accent};
              font-family:Helvetica,Arial,sans-serif;font-size:11.5px;
              letter-spacing:0.22em;text-transform:uppercase;
              text-decoration:none;background:transparent;">
      {_esc(cta_label)}
    </a>
  </td></tr>

  <tr><td style="height:42px;"></td></tr>

  <tr><td style="padding:30px 48px 38px;background:#F2EFE8;text-align:center;">
    <p style="font-family:Helvetica,Arial,sans-serif;font-size:10.5px;
              font-weight:300;color:#9C9588;letter-spacing:0.04em;
              line-height:1.8;margin:0;">
      {_esc(footer_line)}
    </p>
    <p style="font-family:Helvetica,Arial,sans-serif;font-size:8.5px;
              font-weight:300;letter-spacing:0.16em;text-transform:uppercase;
              color:#B6AFA1;margin:10px 0 0 0;">
      {unsub_block}Reply STOP to opt out
    </p>
  </td></tr>

</table>
{open_pixel}
</body>
</html>"""
