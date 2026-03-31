"""XML response parser for ad break analysis."""

import re
from xml.etree import ElementTree

from .models import AdBreakResult, AdvertResult


def normalize_timecode(timecode: str) -> str:
    """Normalize timecode to HH:MM:SS.mmm format.
    
    Args:
        timecode: Timecode string in various formats.
        
    Returns:
        Normalized timecode in HH:MM:SS.mmm format.
    """
    if not timecode:
        return timecode
    
    timecode = timecode.strip()
    
    # Pattern for HH:MM:SS.mmm (already correct)
    if re.match(r'^\d{2}:\d{2}:\d{2}\.\d{3}$', timecode):
        return timecode
    
    # Pattern for HH:MM:SS (add milliseconds)
    if re.match(r'^\d{2}:\d{2}:\d{2}$', timecode):
        return f"{timecode}.000"
    
    # Pattern for MM:SS.mmm (prepend 00:)
    if re.match(r'^\d{2}:\d{2}\.\d{3}$', timecode):
        return f"00:{timecode}"
    
    # Pattern for MM:SS (prepend 00: and add milliseconds)
    if re.match(r'^\d{2}:\d{2}$', timecode):
        return f"00:{timecode}.000"
    
    # If no pattern matches, return as-is (will be caught downstream)
    return timecode


def normalize_frame(frame: str) -> int:
    """Normalize frame number to integer.
    
    Args:
        frame: Frame number string.
        
    Returns:
        Frame number as integer.
        
    Raises:
        ValueError: If frame is not a valid non-negative integer.
    """
    if not frame:
        raise ValueError("Frame number cannot be empty")
    
    frame = frame.strip()
    
    # Ensure it's a valid integer
    try:
        frame_int = int(frame)
    except ValueError:
        raise ValueError(f"Frame number must be an integer, got: {frame}")
    
    # Ensure it's non-negative
    if frame_int < 0:
        raise ValueError(f"Frame number must be non-negative, got: {frame_int}")
    
    return frame_int


def parse_ad_break_response(
    response_text: str,
    expected_adverts: list | None = None,
    mode: str = "timecode"
) -> AdBreakResult:
    """Parse VLM XML response into AdBreakResult.
    
    Supports both old format (<ad_break> with ident_end and start_frame) 
    and new format (simple <advert> list with last_frame).
    
    Args:
        response_text: Raw response from VLM (may contain markdown).
        expected_adverts: Optional list of AdvertMetadata for matching unique_ids.
        mode: Parsing mode - "timecode" or "frame".
        
    Returns:
        Parsed AdBreakResult.
    """
    try:
        xml_content = _extract_xml(response_text)
        root = ElementTree.fromstring(xml_content)
        
        ident_el = root.find("ident_end")
        
        # Parse based on mode - frame mode now uses simple format without ident_end
        if mode == "frame":
            ident_frame = None
            ident_desc = None
            ident_timecode = None
        else:
            ident_timecode = _get_text(ident_el, "timecode") if ident_el is not None else None
            ident_timecode = normalize_timecode(ident_timecode) if ident_timecode else None
            ident_desc = _get_text(ident_el, "description") if ident_el is not None else None
            ident_frame = None
        
        adverts = []
        # Try old format: <adverts> container inside <ad_break>
        adverts_el = root.find("adverts")
        if adverts_el is not None:
            advert_elements = adverts_el.findall("advert")
        else:
            # New format: <advert> elements directly inside <ad_break>
            advert_elements = root.findall("advert")
        
        # Parse advert results in sequence order
        parsed_adverts = []
        for advert_el in advert_elements:
            # Read all values from XML elements (not attributes)
            advert_id = _get_text(advert_el, "unique_id") or ""
            brand = _get_text(advert_el, "brand") or ""
            description = _get_text(advert_el, "description") or ""
            duration_str = _get_text(advert_el, "duration_seconds") or ""
            
            # Parse based on mode - both modes now use 'last_' prefix
            if mode == "frame":
                # Frame mode uses last_frame
                frame_text = _get_text(advert_el, "last_frame") or ""
                frame = normalize_frame(frame_text) if frame_text else None
                timecode = None
            else:
                # Timecode mode uses last_timecode (simplified MM:SS format)
                timecode = _get_text(advert_el, "last_timecode") or ""
                # Don't normalize - keep as MM:SS
                frame = None
            
            # Parse duration
            duration_seconds = None
            if duration_str:
                try:
                    duration_seconds = int(duration_str)
                except ValueError:
                    # Handle UNKNOWN or non-numeric values
                    pass
            
            parsed_adverts.append(AdvertResult(
                timecode=timecode,
                frame=frame,
                advert_id=advert_id,
                brand=brand,
                description=description,
                confidence=0.0,
                duration_seconds=duration_seconds,
            ))
        
        # If we have expected adverts, match by sequence order
        if expected_adverts and len(parsed_adverts) == len(expected_adverts):
            for i, parsed in enumerate(parsed_adverts):
                # Replace the advert_id with the correct unique_id from input
                parsed.advert_id = expected_adverts[i].unique_id
                # Use duration from input if not provided in response
                if parsed.duration_seconds is None and expected_adverts[i].duration_seconds:
                    parsed.duration_seconds = expected_adverts[i].duration_seconds
                adverts.append(parsed)
        else:
            # No matching possible, use parsed IDs
            adverts = parsed_adverts
        
        return AdBreakResult(
            success=True,
            ident_end_timecode=ident_timecode,
            ident_end_frame=ident_frame,
            ident_description=ident_desc,
            adverts=adverts,
            total_found=len(adverts),
            total_expected=len(expected_adverts) if expected_adverts else len(adverts),
        )
    
    except Exception as e:
        return AdBreakResult(
            success=False,
            error=f"Failed to parse response: {e}",
        )


def _extract_xml(response_text: str) -> str:
    """Extract XML from response (handles markdown code blocks).
    
    Also sanitizes XML content to handle unescaped special characters
    that the model may output (e.g., unescaped ampersands in descriptions).
    
    Supports both old format (<ad_break> wrapper) and new format (simple <advert> list).
    
    The model sometimes includes placeholder XML examples in its thinking/reasoning
    sections (e.g., with '...' as values). This function prioritizes XML that appears
    after [RESPONSE] markers, or uses the last occurrence if no marker is found.
    """
    # First, try to find XML after a [RESPONSE] marker
    # This avoids picking up placeholder XML examples in the thinking section
    response_marker_match = re.search(r'\[RESPONSE\].*', response_text, re.DOTALL)
    if response_marker_match:
        text_after_marker = response_marker_match.group(0)
        
        # Look for <ad_break> in the text after the marker
        match = re.search(r'<ad_break>(.*?)</ad_break>', text_after_marker, re.DOTALL)
        if match:
            xml_content = match.group(0)
            return _sanitize_xml(xml_content)
        
        # Try new format with simple <advert> elements
        advert_matches = re.findall(r'<advert[^>]*>.*?</advert>', text_after_marker, re.DOTALL)
        if advert_matches:
            xml_content = '<ad_break><adverts>' + ''.join(advert_matches) + '</adverts></ad_break>'
            return _sanitize_xml(xml_content)
    
    # Fallback: try to find the last <ad_break> in the entire response
    # (in case there's no [RESPONSE] marker, the last one is usually the actual output)
    all_matches = list(re.finditer(r'<ad_break>(.*?)</ad_break>', response_text, re.DOTALL))
    if all_matches:
        # Use the last match (avoids placeholder examples at the start)
        last_match = all_matches[-1]
        xml_content = last_match.group(0)
        return _sanitize_xml(xml_content)
    
    # Try new format with simple <advert> elements as last resort
    advert_matches = re.findall(r'<advert[^>]*>.*?</advert>', response_text, re.DOTALL)
    if advert_matches:
        xml_content = '<ad_break><adverts>' + ''.join(advert_matches) + '</adverts></ad_break>'
        return _sanitize_xml(xml_content)
    
    raise ValueError("No <ad_break> or <advert> XML found in response")


def _sanitize_xml(xml_content: str) -> str:
    """Sanitize XML content by escaping unescaped special characters.
    
    The VLM may output unescaped ampersands (&), quotes, and other characters in text
    content. This function escapes them properly for XML parsing.
    
    Args:
        xml_content: Raw XML content that may contain unescaped characters.
        
    Returns:
        Sanitized XML content with properly escaped special characters.
    """
    import html
    
    # First, temporarily protect already-escaped entities
    # Replace common entities with placeholders
    protected = xml_content
    entity_map = {
        '&amp;': '\x00AMP\x00',
        '&lt;': '\x00LT\x00',
        '&gt;': '\x00GT\x00',
        '&quot;': '\x00QUOT\x00',
        '&apos;': '\x00APOS\x00',
    }
    
    for entity, placeholder in entity_map.items():
        protected = protected.replace(entity, placeholder)
    
    # Escape any remaining & characters that are not part of valid XML entities
    # Valid entity patterns: alphanumeric only (no spaces, <, >, etc.)
    # This regex matches & that is NOT followed by a valid entity pattern
    def escape_ampersand(match):
        entity_content = match.group(1)
        # Check if it looks like a valid named entity (alphanumeric only) or numeric entity
        if re.match(r'^[a-zA-Z][a-zA-Z0-9]*$', entity_content):
            # Named entity like &amp; &lt; &gt; - but these are already protected
            # If we get here, it's an unescaped named entity that we should escape
            return '&amp;' + entity_content
        elif re.match(r'^#[0-9]+$', entity_content):
            # Decimal numeric entity: &#123;
            return '&' + entity_content
        elif re.match(r'^#x[0-9a-fA-F]+$', entity_content):
            # Hex numeric entity: &#xABC;
            return '&' + entity_content
        else:
            # Contains invalid characters (spaces, <, >, etc.), escape the &
            return '&amp;' + entity_content
    
    # Match & followed by anything up to ; or end of string, but be careful
    # We match: & followed by (non-semicolon chars)
    protected = re.sub(r'&([^;<>&\s][^;<>]*)', escape_ampersand, protected)
    
    # Also handle bare & at end of text or followed by whitespace/end
    # Match & that is NOT followed by any of the protected placeholders
    protected = re.sub(r'&(?![\x00])', '&amp;', protected)
    
    # Escape unescaped double quotes that appear in text content (not in tags)
    # Split content by tags, preserving the tags
    def escape_quotes_in_text(text):
        """Escape quotes in XML text content (not in tags)."""
        # Check if this looks like a tag (starts with < and ends with >)
        if text.startswith('<') and text.endswith('>'):
            # This is a tag, don't escape quotes inside
            return text
        else:
            # This is text content between tags, escape unescaped quotes
            result = text.replace('"', '\x00ESCQUOT\x00')
            return result
    
    # Split content by tags, preserving the tags
    parts = re.split(r'(<[^>]+>)', protected)
    
    # Process each part
    processed_parts = []
    for part in parts:
        if part:  # Skip empty strings
            processed_parts.append(escape_quotes_in_text(part))
    
    protected = ''.join(processed_parts)
    
    # Convert our temporary placeholder for escaped quotes to &quot;
    protected = protected.replace('\x00ESCQUOT\x00', '&quot;')
    
    # Restore original entities
    for entity, placeholder in entity_map.items():
        protected = protected.replace(placeholder, entity)
    
    return protected


def _get_text(parent: ElementTree.Element, child_tag: str) -> str | None:
    """Safely get text from child element."""
    child = parent.find(child_tag)
    return child.text.strip() if child is not None and child.text else None
