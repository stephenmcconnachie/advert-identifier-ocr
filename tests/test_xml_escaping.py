"""Tests for XML escaping in output formatting."""

import html
import sys
from pathlib import Path
from dataclasses import dataclass


def _escape_xml(text: str) -> str:
    """Escape XML special characters in text."""
    return html.escape(text, quote=True)


@dataclass
class AdvertResult:
    timecode: str | None = None
    frame: int | None = None
    advert_id: str = ""
    brand: str = ""
    description: str = ""
    confidence: float = 0.0
    duration_seconds: int | None = None


@dataclass
class AdBreakResult:
    success: bool = False
    error: str | None = None
    ident_end_timecode: str | None = None
    ident_end_frame: int | None = None
    ident_description: str | None = None
    adverts: list = None

    def __post_init__(self):
        if self.adverts is None:
            self.adverts = []


def format_xml(result: AdBreakResult, mode: str = "timecode") -> str:
    """Format result as XML with just the adverts array."""
    lines = []
    lines.append("<ad_break>")

    for advert in result.adverts:
        lines.append("    <advert>")
        lines.append(f"        <unique_id>{_escape_xml(advert.advert_id)}</unique_id>")
        lines.append(f"        <brand>{_escape_xml(advert.brand)}</brand>")
        if advert.duration_seconds:
            lines.append(f"        <duration_seconds>{advert.duration_seconds}</duration_seconds>")
        if mode == "frame":
            lines.append(f"        <last_frame>{advert.frame}</last_frame>")
        else:
            lines.append(f"        <last_timecode>{_escape_xml(advert.timecode or '')}</last_timecode>")
        if advert.description:
            lines.append(f"        <description>{_escape_xml(advert.description)}</description>")
        lines.append("    </advert>")

    lines.append("</ad_break>")
    return "\n".join(lines)


def test_escape_xml_basic():
    """Test basic XML escaping."""
    assert _escape_xml("&") == "&amp;"
    assert _escape_xml("<") == "&lt;"
    assert _escape_xml(">") == "&gt;"
    assert _escape_xml('"') == "&quot;"
    assert _escape_xml("'") == "&#x27;"  # HTML escape uses &#x27; for apostrophe
    print("test_escape_xml_basic: PASSED")


def test_escape_xml_combined():
    """Test combined special characters."""
    assert _escape_xml("Brand & Name <Test>") == "Brand &amp; Name &lt;Test&gt;"
    assert _escape_xml("A & B <C> D 'E' F \"G\"") == "A &amp; B &lt;C&gt; D &#x27;E&#x27; F &quot;G&quot;"
    print("test_escape_xml_combined: PASSED")


def test_escape_xml_no_escaping_needed():
    """Test text without special characters passes through."""
    assert _escape_xml("Normal text 123") == "Normal text 123"
    assert _escape_xml("Tesco") == "Tesco"
    print("test_escape_xml_no_escaping_needed: PASSED")


def test_format_xml_escapes_special_chars():
    """Test that format_xml properly escapes special characters in advert data."""
    result = AdBreakResult(success=True, adverts=[
        AdvertResult(
            advert_id="ID&123",
            brand="Brand <Test> & 'Quote'",
            timecode="02:30",
            description="Description & <test>",
            duration_seconds=20,
        )
    ])
    xml = format_xml(result)

    assert "&amp;" in xml, f"Expected &amp; in XML: {xml}"
    assert "&lt;" in xml, f"Expected &lt; in XML: {xml}"
    assert "&gt;" in xml, f"Expected &gt; in XML: {xml}"
    assert "&#x27;" in xml or "&apos;" in xml  # ' escaped
    print("test_format_xml_escapes_special_chars: PASSED")


def test_format_xml_preserves_structure():
    """Test that format_xml output has correct XML structure."""
    result = AdBreakResult(success=True, adverts=[
        AdvertResult(
            advert_id="TEST001",
            brand="TestBrand",
            timecode="02:30",
            description="Test description",
            duration_seconds=10,
        )
    ])
    xml = format_xml(result)

    assert "<ad_break>" in xml
    assert "</ad_break>" in xml
    assert "<advert>" in xml
    assert "</advert>" in xml
    assert "<unique_id>TEST001</unique_id>" in xml
    assert "<brand>TestBrand</brand>" in xml
    assert "<duration_seconds>10</duration_seconds>" in xml
    assert "<last_timecode>02:30</last_timecode>" in xml
    print("test_format_xml_preserves_structure: PASSED")


def test_format_xml_frame_mode():
    """Test format_xml with frame mode."""
    result = AdBreakResult(success=True, adverts=[
        AdvertResult(
            advert_id="TEST001",
            brand="Brand & Test",
            frame=150,
            description="Desc <test>",
            duration_seconds=20,
        )
    ])
    xml = format_xml(result, mode="frame")

    assert "<last_frame>150</last_frame>" in xml
    assert "&amp;" in xml
    assert "&lt;" in xml
    print("test_format_xml_frame_mode: PASSED")


def test_real_world_example():
    """Test with real-world brand names containing &."""
    result = AdBreakResult(success=True, adverts=[
        AdvertResult(
            advert_id="HOGULCO016020",
            brand="Comfort fabric softener by Unilever uk home & p",
            timecode="02:37",
            description="The Comfort fabric softener bottle with the Unilever logo is displayed as the final image in the ad.",
            duration_seconds=20,
        )
    ])
    xml = format_xml(result)

    assert "&amp;" in xml, f"Expected &amp; for & in brand: {xml}"
    assert "<brand>Comfort fabric softener by Unilever uk home &amp; p</brand>" in xml
    print("test_real_world_example: PASSED")


if __name__ == "__main__":
    test_escape_xml_basic()
    test_escape_xml_combined()
    test_escape_xml_no_escaping_needed()
    test_format_xml_escapes_special_chars()
    test_format_xml_preserves_structure()
    test_format_xml_frame_mode()
    test_real_world_example()
    print("\nAll tests passed!")