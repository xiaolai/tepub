from state.models import Segment, SegmentMetadata, ExtractMode
from translation.prefilter import should_auto_copy

def _segment(text: str) -> Segment:
    return Segment(
        segment_id="seg",
        file_path="chapter.xhtml",
        xpath="/",
        extract_mode=ExtractMode.TEXT,
        source_content=text,
        metadata=SegmentMetadata(element_type="p", spine_index=0, order_in_file=0),
    )

def test_auto_copy_for_ellipsis():
    assert should_auto_copy(_segment("…"))

def test_auto_copy_for_numbers():
    assert should_auto_copy(_segment("123-125"))

def test_auto_copy_for_page_marker():
    assert should_auto_copy(_segment("p. 42"))

def test_auto_copy_rejects_short_word():
    assert not should_auto_copy(_segment("Fig."))

def test_auto_copy_rejects_normal_sentence():
    assert not should_auto_copy(_segment("Hello world"))
