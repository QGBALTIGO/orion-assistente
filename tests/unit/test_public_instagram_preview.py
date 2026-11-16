"""Profile embeds may load only from the reviewed Instagram host."""
from app.public_site_api import HEADERS
from pathlib import Path

def test_public_csp_allows_instagram_frame_but_no_remote_parent_scripts():
    csp=HEADERS['Content-Security-Policy']
    assert "frame-src https://www.google.com https://maps.google.com https://www.instagram.com;" in csp
    assert "script-src 'self'" in csp
    assert "connect-src 'self'" in csp
    assert "frame-ancestors 'none'" in csp
    assert "frame-src *" not in csp

def test_public_html_does_not_expose_remote_iframe_until_profile_is_validated():
    source=Path(__file__).resolve().parents[2]/'app'/'static'/'orion-public.html'
    html=source.read_text()
    assert '<iframe id="about-instagram-frame"' in html
    assert 'loading="lazy"' in html
    assert 'referrerpolicy="no-referrer"' in html
    assert 'src="https://www.instagram.com' not in html
