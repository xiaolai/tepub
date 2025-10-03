from pathlib import Path
from webbuilder.dom import clean_html


def test_clean_html_removes_font_and_styles():
    html = "<html><body><font color='red'><span style='color:blue'>文本</span></font></body></html>"
    cleaned = clean_html(html)
    assert "font" not in cleaned
    assert "style" not in cleaned
    assert "文本" in cleaned



def test_clean_html_adds_image_attrs():
    html_doc = "<html><body><img src='a.jpg' class='cover' /><img src='b.jpg' /></body></html>"
    cleaned = clean_html(html_doc)
    assert 'loading="lazy"' in cleaned
    assert 'decoding="async"' in cleaned
    assert cleaned.count('class="tepub-img"') == 2
    assert 'class="tepub-img"' in cleaned



def test_clean_html_rewrites_media_urls(tmp_path):
    html_doc = """
    <html><body>
      <img src="images/a.jpg" srcset="images/a@2x.jpg 2x" />
      <audio src="audio/sample.mp3"></audio>
      <svg xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink"><image xlink:href="images/cover.jpg" href="images/cover.jpg" /></svg>
      <p><a href="../chapter2.xhtml#section">Next chapter</a></p>
    </body></html>
    """
    rel = Path("chapters/ch1.xhtml")
    cleaned = clean_html(html_doc, relative_path=rel)
    assert 'src="content/chapters/images/a.jpg"' in cleaned
    assert 'srcset="content/chapters/images/a@2x.jpg 2x"' in cleaned
    assert 'src="content/chapters/audio/sample.mp3"' in cleaned
    assert ('xlink:href="content/chapters/images/cover.jpg"' in cleaned) or (
        'ns0:href="content/chapters/images/cover.jpg"' in cleaned
    )
    assert 'href="content/chapters/images/cover.jpg"' in cleaned
    assert 'href="content/chapter2.xhtml#section"' in cleaned
