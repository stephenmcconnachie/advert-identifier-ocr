"""Prompt templates and builders for ad break analysis."""

from .models import AdBreakMetadata

AD_BREAK_PROMPT = '''
You are analyzing a TV broadcast video containing an advertisement break.

## VIDEO CONTENT

The video contains (in order):
1. End of programme: "{prog_before_title}" on {prog_before_channel}
2. POSSIBLY: sponsor bumper for {prog_before_title}
3. Channel ident for {prog_before_channel}
3. Advertisement break with {num_adverts} adverts
4. POSSIBLY: Start of programme: "{prog_after_title}" on {prog_after_channel}

## ADVERT SEQUENCE

You are provided with metadata for each advert:

{adverts_list}

## YOUR TASK

For each advert in the adverts_list, identify the LAST FRAME where the brand appears:
    - Review all frames and look for brand logos/names in the FINAL frames of each advert
    - The brand typically appears prominently in the last 2-3 seconds of the advert
    - Provide the timecode in MM:SS format for the last frame

## OUTPUT FORMAT

Return EXACTLY this XML structure with ALL values as elements (not attributes):

<ad_break>
    <advert>
        <unique_id>ACTUAL_UNIQUE_ID_FROM_INPUT</unique_id>
        <brand>ACTUAL BRAND FROM INPUT</brand>
        <duration_seconds>ACTUAL DURATION FROM INPUT</duration_seconds>
        <last_timecode>ACTUAL LAST TIMECODE YOU HAVE IDENTIFIED</last_timecode>
        <description>YOUR REASON FOR THE DECISION, REFERENCING THE BRAND LOGO OR NAME</description>
    </advert>
    <!-- Repeat for each advert in order -->
</ad_break>

## CRITICAL REQUIREMENTS

1. **TIMECODE FORMAT**: Use MM:SS format (e.g., "09:30", "12:45")
   - Minutes and seconds only, no hours or milliseconds
   - Simple elapsed time format
   
2. **ADVERT ID**: Use the EXACT unique_id from the input metadata (e.g., "BBHTCPT536010")
   - DO NOT use generic IDs like "adv_001"
   - Copy the unique_id field exactly as provided in the adverts_list input

3. **DURATION**: Include the duration_seconds element from the input metadata
   - If duration is unknown (null), use UNKNOWN

4. **UNCERTAINTY**: If there is uncertainty over identification of a specific advert:
   - Use the timecode you have identified for the NEXT ad in the sequence, and the duration_seconds of that next ad, to
   - Calculate the FIRST timecode of that next ad (last timecode - duration seconds) and therefore
   - Calculate the LAST timecode of this advert
   - Let that calculated timecode override your uncertainty about the ad content   

## NOTES

- Video is sampled at 1 FPS (each frame = 1 second apart)
- All advert durations are multiples of 10 seconds
- Brand logos and names will always appear in final 2 to 3 frames of each advert
'''


AD_BREAK_PROMPT_FRAME = '''
You are analyzing a TV broadcast video containing an advertisement break.

## VIDEO CONTENT

The video contains (in order):
1. End of programme: "{prog_before_title}" on {prog_before_channel}
2. POSSIBLY: sponsor bumper for {prog_before_title}
3. Channel ident for {prog_before_channel}
3. Advertisement break with {num_adverts} adverts
4. POSSIBLY: Start of programme: "{prog_after_title}" on {prog_after_channel}

## ADVERT SEQUENCE

You are provided with metadata for each advert:

{adverts_list}

## YOUR TASK

For each advert in the adverts_list, identify the LAST FRAME where the brand appears:
    - Review all frames and look for brand logos/names in the FINAL frames of each advert
    - The brand typically appears prominently in the last 2-3 seconds of the advert
    - Provide the frame number for the last frame

## OUTPUT FORMAT

Return EXACTLY this XML structure with ALL values as elements (not attributes):

<ad_break>
    <advert>
        <unique_id>ACTUAL_UNIQUE_ID_FROM_INPUT</unique_id>
        <brand>ACTUAL BRAND FROM INPUT</brand>
        <duration_seconds>ACTUAL DURATION FROM INPUT</duration_seconds>
        <last_frame>ACTUAL LAST FRAME YOU HAVE IDENTIFIED</last_frame>
        <description>YOUR REASON FOR THE DECISION, REFERENCING THE BRAND LOGO OR NAME</description>
    </advert>
    <!-- Repeat for each advert in order -->
</ad_break>

## CRITICAL REQUIREMENTS

1. **FRAME FORMAT**: ALL frames MUST be integers (e.g., "150", "1200")
   - NEVER output timecodes like "HH:MM:SS.mmm"
   - NEVER output MM:SS or relative times
   - Use 0-based frame numbers
   
2. **ADVERT ID**: Use the EXACT unique_id from the input metadata (e.g., "BBHTCPT536010")
   - DO NOT use generic IDs like "adv_001"
   - Copy the unique_id field exactly as provided in the adverts_list input

3. **DURATION**: Include the duration_seconds element from the input metadata
   - If duration is unknown (null), use UNKNOWN

4. **UNCERTAINTY**: If there is uncertainty over identification of a specific advert:
   - Use the frame you have identified for the NEXT ad in the sequence, and the duration_seconds of that next ad, to
   - Calculate the FIRST frame of that next ad (last frame - duration seconds) and therefore
   - Calculate the LAST frame of this advert
   - Let that calculated frame override your uncertainty about the ad content

## NOTES

- Video is sampled at 1 FPS (each frame = 1 second apart)
- All advert durations are multiples of 10 seconds
- Brand logos and names will always appear in final 2 to 3 frames of each advert
'''


AD_REFINE_PROMPT = '''
You are analyzing a 3-second video clip (72 frames at 24fps) showing the
FINAL FRAMES OF AN ADVERTISEMENT.

## ADVERT INFORMATION
- Brand: {brand}
- Advertiser: {advertiser}
- Category: {category}
- Duration: {duration} seconds

## YOUR TASK

Identify the EXACT LAST FRAME where the brand/product appears in this clip.
- The clip is centered on the expected end of the advert
- Look carefully at all 72 frames for brand logos and visual branding
- Use the brand and advertiser information above to help identify the correct frames

## OUTPUT FORMAT

Return EXACTLY this XML structure:

<advert>
    <last_frame>FRAME_NUMBER</last_frame>
    <confidence>HIGH/MEDIUM/LOW</confidence>
    <description>Brief reason for decision</description>
</advert>

## NOTES

- Frame 0 is the first frame of the clip (1.5 seconds BEFORE the expected advert end)
- Frame 71 is the last frame of the clip (1.5 seconds AFTER the expected advert end)
- The expected advert end timecode is at frame 36 (center of clip)
- Return only the frame number (0-71), not a full timecode
'''


def build_refine_prompt(brand: str, advertiser: str, category: str, duration: int | None) -> str:
    """Build refinement prompt with advert-specific context.

    Args:
        brand: Brand name.
        advertiser: Advertiser name.
        category: Category name.
        duration: Duration in seconds or None.

    Returns:
        Formatted prompt string.
    """
    duration_str = f"{duration} seconds" if duration else "unknown"
    return AD_REFINE_PROMPT.format(
        brand=brand,
        advertiser=advertiser,
        category=category,
        duration=duration_str,
    )


def build_ad_break_prompt(metadata: AdBreakMetadata, mode: str = "timecode") -> str:
    """Build VLM prompt from ad break metadata.
    
    Args:
        metadata: Complete ad break metadata.
        mode: Analysis mode - "timecode" or "frame".
        
    Returns:
        Formatted prompt string.
    """
    adverts_lines = []
    for i, advert in enumerate(metadata.adverts, 1):
        duration_str = f"{advert.duration_seconds} seconds" if advert.duration_seconds else "unknown duration"
        adverts_lines.append(
            f"{i}. {advert.brand} by {advert.advertiser} ({advert.category}) - "
            f"{duration_str} - ID: {advert.unique_id}"
        )
    
    last_brand = metadata.adverts[-1].brand
    
    if mode == "frame":
        prompt_template = AD_BREAK_PROMPT_FRAME
    else:
        prompt_template = AD_BREAK_PROMPT
    
    return prompt_template.format(
        prog_before_title=metadata.programme_before.title,
        prog_before_channel=metadata.programme_before.channel,
        prog_after_title=metadata.programme_after.title,
        prog_after_channel=metadata.programme_after.channel,
        num_adverts=len(metadata.adverts),
        adverts_list="\n".join(adverts_lines),
        last_brand=last_brand,
    )
