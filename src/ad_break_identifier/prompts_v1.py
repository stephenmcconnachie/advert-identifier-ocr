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

The duration_seconds is very important!

## TIMECODE OVERLAY

The video has a **timecode overlay displayed on the LEFT SIDE** of every frame.

**TIMECODE FORMAT**: HH:MM:SS.mmm (e.g., "13:52:05.000")
- This shows **time of day**, not elapsed time
- The overlay is white text in a monospace font

## CRITICAL: READ TIMECODES, DO NOT CALCULATE

**YOU MUST VISUALLY READ THE TIMECODE FROM EACH FRAME**

✅ **CORRECT**: Look at the left side of the frame and read the exact timecode displayed
   - Example: "I see '13:52:05.000' on the left side of this frame"

❌ **WRONG**: Do NOT calculate, estimate, or infer timecodes
   - Do NOT use frame numbers to calculate time
   - Do NOT estimate based on video duration
   - Do NOT guess timecodes from context
   - Do NOT output relative times like "01:42" or "1:42.000"

**ONLY REPORT TIMECODES YOU CAN VISUALLY CONFIRM**

If you cannot clearly see the timecode overlay on a frame, you cannot report that frame's timecode.

## YOUR TASK

Identify these frames by **reading the timecode overlay**:

 1. **Channel Ident End**: The LAST frame of the channel ident (before first advert)
    - Look for {prog_before_channel} logo/branding
    - Ident is typically a freeze frame (>=2 frames) or animation (>=2 frames)
    - **READ the timecode displayed on the left side of the final ident frame**
    
 2. **Each Advert's First Frame**: The FIRST frame of each advert
    - Find where each advert BEGINS (frame after previous content ends)
    - Use the brand names above to identify each advert
    - Brand logos typically appear in the FINAL frames of each advert
    - **READ the timecode displayed on the left side of the first frame**
    - Work backwards from brand logos using the duration information

## BACKWARDS SEARCH STRATEGY

Start from the LAST advert ({last_brand}) and work backwards:

1. Find {last_brand} logo in the final frames of the video
2. The frame immediately AFTER the logo sequence is the start of the next content
3. Duration is not available for the last advert in the sequence - for that one try: 20s -> 30s -> 10s -> 60s
4. For all other adverts, YOU MUST use the provided duration to go backwards and find the previous advert
5. Check +/-2 frames around expected position when searching

Example: If sequence is Renault(30s) -> Disneyland(20s) -> Tesco(unknown)
- Find Tesco logo at end -> mark advert start as frame before logo sequence
- Assume 20s for Tesco, go back ~20 frames -> look for Disneyland logo
- Use provided 20s duration for Disneyland, go back 20 frames -> find Renault
- Continue until all adverts identified - remember you must use the provided duration, do not guess
- Finally, find channel ident end (before first advert starts)

## OUTPUT FORMAT

Return EXACTLY this XML structure:

<ad_break>
    <ident_end>
        <timecode>HH:MM:SS.mmm</timecode>
        <description>What you see</description>
    </ident_end>
    <adverts>
        <advert id="ACTUAL_UNIQUE_ID_FROM_INPUT" brand="BrandName">
            <start_timecode>HH:MM:SS.mmm</start_timecode>
            <description>How you identified this advert</description>
        </advert>
        <!-- Repeat for each advert in order, using the exact unique_id from the input -->
    </adverts>
</ad_break>

## CRITICAL REQUIREMENTS

1. **TIMECODE FORMAT**: ALL timecodes MUST be in HH:MM:SS.mmm format (e.g., "13:52:05.000")
   - NEVER output MM:SS or MM:SS.mmm format
   - Always include leading zeros for hours (e.g., "00:01:42.000" not "1:42.000")
   - The video has time-of-day timecode (24-hour format)
   
2. **TIMECODE SOURCE**: Every timecode you report MUST be visually read from the overlay
   - Before outputting each timecode, verify you actually saw it on the frame
   - Your description should mention seeing the timecode (e.g., "Timecode '13:52:05.000' visible on left")
   - If you calculated or estimated a timecode, you made an error
   - MOST IMPORTANT! DO NOT output timecodes you have created, ONLY output timecodes you have seen on the video frame
   
3. **ADVERT ID**: Use the EXACT unique_id from the input metadata (e.g., "BBHTCPT536010")
   - DO NOT use generic IDs like "adv_001"
   - Copy the unique_id field exactly as provided in the advert sequence above

4. **DURATION**: Only the last advert in the sequence has no provided duration - use 20s / 30s / 10s / 60s checks for that one
   - For all other adverts, YOU MUST use the duration that is provided in the input metadata
   - DO NOT guess duration for any adverts where duration is provided. The provided duration is your source!

## NOTES

- Video is sampled at 1 FPS (each frame = 1 second apart)
- All advert durations are multiples of 10 seconds
- Brand logos appear in final >=2 frames of each advert
- Be precise with timecode reading
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

The duration_seconds is very important!

## FRAME COUNT

The video has a **frame number displayed on the LEFT SIDE** of every frame.

**FRAME FORMAT**: Integer starting from 0 (e.g., "0", "150", "1200")
- This shows the **frame index** (0-based), not the elapsed time
- This is extremely important - you must use this frame number in your output

## CRITICAL: READ FRAME NUMBERS from the image, DO NOT CALCULATE from the sequence you have

**YOU MUST VISUALLY READ THE FRAME NUMBER FROM EACH FRAME**

✅ **CORRECT**: Look at the left side of the frame and read the exact frame number displayed
   - Example: "I see frame '150' on the left side of this frame"

❌ **WRONG**: Do NOT calculate, estimate, or infer frame numbers
   - Do NOT calculate frames from timecodes
   - Do NOT estimate based on video duration
   - Do NOT guess frame numbers from context
   - Do NOT output timecodes like "13:52:05.000" or "02:25"

**ONLY REPORT FRAME NUMBERS YOU CAN VISUALLY CONFIRM**

If you cannot clearly see the frame number overlay on a frame, you cannot report that frame's number.

## YOUR TASK

Identify these frames by **reading the frame number overlay**:

 1. **Channel Ident End**: The LAST frame of the channel ident (before first advert)
    - Look for {prog_before_channel} logo/branding
    - Ident is typically a freeze frame (>=2 frames) or animation (>=2 frames)
    - Ident sometimes comes after a sponsorship section with 'sponsored by' or 'sponsors' information
    - **READ the frame number displayed on the left side of the final ident frame**
    
 2. **Each Advert's First Frame**: The FIRST frame of each advert
    - Use the brand names in the adverts list above to identify each advert
    - Brand logos / names usually appear in the FINAL frames of each advert
    - **READ the frame number displayed on the left side of the last frame**
    - Work backwards from that last frame using the duration_seconds in the adverts_list
    - Find where each advert BEGINS (frame after previous content ends)
    - **READ the frame number displayed on the left side of the first frame**

## BACKWARDS SEARCH STRATEGY

Start from the LAST advert ({last_brand}) and work backwards:

1. Find {last_brand} logo in the final frames of the video
2. Duration is not available for the LAST advert in the sequence - for that one try stepping back 20s, or 30s, or 10s, or 60s
4. For all other adverts, YOU MUST use the provided duration_seconds to go backwards and find the previous advert
5. Check +/-2 frames around expected position when searching

Example: If the sequence is Renault(30s) -> Disneyland(20s) -> Tesco(unknown)
- Find Tesco logo at end -> mark advert end frame by **READING frame number from image**
- Assume 20s for Tesco, go back ~20 frames -> look for Disneyland logo
- If not found, repeat for 30, 10, 60 frames until you find Disneyland logo
- Use provided 20s duration for Disneyland, go back 20 frames -> find Renault
- Continue until all adverts identified - remember you **must use the provided duration, do not guess**
- Finally, find channel ident end (before first advert starts)
- ALWAYS READ THE FRAME NUMBER FROM THE IMAGE, ON THE LEFT SIDE

## OUTPUT FORMAT

Return EXACTLY this XML structure:

<ad_break>
    <ident_end>
        <frame>INTEGER</frame>
        <description>What you see</description>
    </ident_end>
    <adverts>
        <advert id="ACTUAL_UNIQUE_ID_FROM_INPUT" brand="BrandName">
            <start_frame>INTEGER</start_frame>
            <description>How you identified this advert - remember you must identify the brand in the final frame then use duration_seconds to move backwards to the end of previous advert</description>
        </advert>
        <!-- Repeat for each advert in order, using the exact unique_id from the input -->
    </adverts>
</ad_break>

## CRITICAL REQUIREMENTS

1. **FRAME FORMAT**: ALL frames MUST be integers (e.g., "150", "1200")
   - NEVER output timecodes like "HH:MM:SS.mmm"
   - NEVER output MM:SS or relative times
   - Use 0-based frame numbers as displayed on the video
   
2. **FRAME SOURCE**: Every frame number you report MUST be visually read from the image, NOT INFERRED FROM SEQUENCE
   - Before outputting each frame, verify that you actually saw it on the frame
   - Your description should mention seeing the frame number (e.g., "Frame '150' visible on left")
   - If you calculated or estimated a frame number, you made an error
   - MOST IMPORTANT! DO NOT output frame numbers you have created, ONLY output frame numbers you have seen on the video frame
   
3. **ADVERT ID**: Use the EXACT unique_id from the input metadata (e.g., "BBHTCPT536010")
   - DO NOT use generic IDs like "adv_001"
   - Copy the unique_id field exactly as provided in the advert sequence above

4. **DURATION**: Only the last advert in the sequence has no provided duration - use 20s / 30s / 10s / 60s checks for that one
   - For all other adverts, YOU MUST use the duration that is provided in the input metadata, to find the exact frames
   - DO NOT guess duration for any adverts where duration is provided. The provided duration is your source!

## NOTES

- Video is sampled at 1 FPS (each frame = 1 second apart)
- All advert durations are multiples of 10 seconds
- Brand logos appear in final >=2 frames of each advert
- Be precise with frame number reading
- Before writing the XML output, check one last time to make sure the <start_frame> comes from the visual display on the input frame
'''


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
            f"{i}. {advert.brand} ({advert.category}) - "
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
