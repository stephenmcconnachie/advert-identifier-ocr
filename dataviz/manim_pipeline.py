"""
Ad Break Identifier — Pipeline Explainer
A progressive build-up animation revealing the 5-stage advert detection pipeline.
"""

from manim import *

# ── Palette ──────────────────────────────────────────
BG = "#1C1C1C"
PRIMARY = "#58C4DD"
SECONDARY = "#83C167"
ACCENT = "#FFFF00"
GRAY = "#888888"
LIGHT = "#EAEAEA"

STAGE_COLORS = {
    "metadata": "#FFA500",  # amber
    "clip": "#FF6B6B",      # rose
    "detect": "#9B59B6",    # violet
    "refine": "#6366F1",    # indigo
    "extract": "#10B981",   # emerald
}

MONO = "Menlo"

# ── Helpers ──────────────────────────────────────────
def stage_box(stage_num, label, sublabel, color, width=2.2, height=0.9):
    """Create a rounded stage box with number, label, and sublabel."""
    box = RoundedRectangle(
        corner_radius=0.12,
        width=width,
        height=height,
        color=color,
        stroke_width=2.5,
        fill_color=color,
        fill_opacity=0.12,
    )
    num_text = Text(str(stage_num), font_size=16, font=MONO, color=color, weight=BOLD)
    num_text.next_to(box, UL, buff=0.08)
    label_text = Text(label, font_size=14, font=MONO, color=color, weight=BOLD)
    label_text.next_to(box, ORIGIN, buff=0)
    sub_text = Text(sublabel, font_size=9, font=MONO, color=color, opacity=0.7)
    sub_text.next_to(box, DOWN, buff=0.06)
    return VGroup(box, num_text, label_text, sub_text)

def arrow_between(left_obj, right_obj, color=GRAY):
    """Arrow from right edge of left_obj to left edge of right_obj."""
    return Arrow(
        left_obj.get_right() + RIGHT * 0.1,
        right_obj.get_left() + LEFT * 0.1,
        color=color,
        stroke_width=2,
        buff=0.05,
        tip_length=0.12,
    )

def file_label(label, color, width=1.5, height=0.45):
    """Small document-style box for file outputs."""
    box = RoundedRectangle(
        corner_radius=0.08,
        width=width,
        height=height,
        color=color,
        stroke_width=1.5,
        fill_color=color,
        fill_opacity=0.08,
    )
    text = Text(label, font_size=8, font=MONO, color=color)
    text.next_to(box, ORIGIN, buff=0)
    return VGroup(box, text)


class PipelineExplainer(Scene):
    def construct(self):
        self.camera.background_color = BG

        # ═══════════════════════════════════════════
        # Phase 1: Title (~5s)
        # ═══════════════════════════════════════════
        title = Text("Ad Break Identifier", font_size=44, font=MONO, color=PRIMARY, weight=BOLD)
        subtitle = Text(
            "Automated TV Advert Detection Pipeline",
            font_size=18,
            font=MONO,
            color=GRAY,
        )
        subtitle.next_to(title, DOWN, buff=0.3)
        title_group = VGroup(title, subtitle).center()

        self.add_subcaption("Ad Break Identifier — Pipeline Overview", duration=3)
        self.play(Write(title), run_time=1.0)
        self.play(FadeIn(subtitle, shift=UP * 0.1), run_time=0.8)
        self.wait(2.0)
        self.play(FadeOut(title_group), run_time=0.5)
        self.wait(0.3)

        # ═══════════════════════════════════════════
        # Phase 2: Inputs (~4s)
        # ═══════════════════════════════════════════
        input_y = 1.8
        video_box = RoundedRectangle(
            corner_radius=0.12, width=2.0, height=0.6,
            color="#3B82F6", stroke_width=2, fill_color="#3B82F6", fill_opacity=0.1,
        )
        video_label = Text("Video .mp4", font_size=12, font=MONO, color="#60A5FA")
        video_label.move_to(video_box.get_center())
        video_input = VGroup(video_box, video_label)
        video_input.shift(LEFT * 5.5 + UP * input_y)

        csv_box = RoundedRectangle(
            corner_radius=0.12, width=2.0, height=0.6,
            color="#F59E0B", stroke_width=2, fill_color="#F59E0B", fill_opacity=0.1,
        )
        csv_label = Text("CSV Schedule", font_size=12, font=MONO, color="#FBBF24")
        csv_label.move_to(csv_box.get_center())
        csv_input = VGroup(csv_box, csv_label)
        csv_input.shift(LEFT * 5.5 + DOWN * input_y * 0.5)

        input_title = Text("Inputs", font_size=12, font=MONO, color=GRAY)
        input_title.next_to(video_input, UP, buff=0.25)

        self.add_subcaption("Inputs: broadcast video and scheduling data", duration=3)
        self.play(
            Write(input_title),
            FadeIn(video_input, shift=RIGHT * 0.3),
            FadeIn(csv_input, shift=RIGHT * 0.3),
            run_time=0.8,
        )
        self.wait(1.5)

        # ═══════════════════════════════════════════
        # Phase 3: Stage 1 — Metadata (~7s)
        # ═══════════════════════════════════════════
        s1_x = -2.5
        s1_y = 0.8
        s1 = stage_box(1, "Metadata", "Extraction", STAGE_COLORS["metadata"])
        s1.shift(RIGHT * s1_x + UP * s1_y)

        arrow_in = Arrow(
            video_input.get_right() + RIGHT * 0.15,
            s1.get_left() + LEFT * 0.2,
            color=GRAY, stroke_width=2, buff=0.05, tip_length=0.12,
        )

        # Output files
        json_out = file_label("_metadata.json", STAGE_COLORS["metadata"], width=1.8, height=0.4)
        json_out.next_to(s1, DOWN, buff=0.5).shift(LEFT * 0.3)
        state_out = file_label("_pipeline_state.json", "#3B82F6", width=2.0, height=0.4)
        state_out.next_to(json_out, DOWN, buff=0.2)

        output_title = Text("Outputs", font_size=10, font=MONO, color=GRAY)
        output_title.next_to(json_out, LEFT, buff=0.8)

        arrow_out_json = Arrow(
            s1.get_bottom() + DOWN * 0.1,
            json_out.get_top(),
            color=GRAY, stroke_width=1.5, buff=0.05, tip_length=0.1,
        )
        arrow_out_state = Arrow(
            s1.get_bottom() + DOWN * 0.1 + RIGHT * 0.4,
            state_out.get_top(),
            color=GRAY, stroke_width=1.5, buff=0.05, tip_length=0.1,
        )

        self.add_subcaption("Stage 1: Parse CSV scheduling data into structured metadata and pipeline state", duration=5)
        self.play(
            Create(arrow_in),
            FadeIn(s1, shift=UP * 0.3),
            run_time=0.8,
        )
        self.wait(0.3)
        self.play(
            Create(arrow_out_json),
            Create(arrow_out_state),
            FadeIn(json_out, shift=DOWN * 0.2),
            FadeIn(state_out, shift=DOWN * 0.2),
            Write(output_title),
            run_time=0.8,
        )
        self.wait(0.5)

        # ═══════════════════════════════════════════
        # Phase 4: Stage 2 — Clip Extraction (~6s)
        # ═══════════════════════════════════════════
        s2 = stage_box(2, "Clip", "Extraction", STAGE_COLORS["clip"])
        s2.shift(RIGHT * 0.5 + UP * s1_y)

        arrow_12 = arrow_between(s1, s2)

        clip_out = file_label("_CLIP.mp4", STAGE_COLORS["clip"], width=1.5, height=0.4)
        clip_out.next_to(s2, DOWN, buff=0.5)

        arrow_12_out = Arrow(
            s2.get_bottom(),
            clip_out.get_top(),
            color=GRAY, stroke_width=1.5, buff=0.05, tip_length=0.1,
        )

        self.add_subcaption("Stage 2: Extract ad break clips using FFmpeg", duration=4)
        self.play(
            Create(arrow_12),
            FadeIn(s2, shift=UP * 0.3),
            run_time=0.7,
        )
        self.wait(0.2)
        self.play(
            Create(arrow_12_out),
            FadeIn(clip_out, shift=DOWN * 0.2),
            run_time=0.6,
        )
        self.wait(0.5)

        # ═══════════════════════════════════════════
        # Phase 5: Stage 3 — AI Detection 1 FPS (~9s)
        # ═══════════════════════════════════════════
        s3 = stage_box(3, "AI Detection", "1 FPS", STAGE_COLORS["detect"])
        s3.shift(RIGHT * 3.5 + UP * s1_y)

        arrow_23 = arrow_between(s2, s3)

        # Ensemble: 5 parallel arrows showing multiple API calls
        ensemble_label = Text("5x Ensemble", font_size=9, font=MONO, color=STAGE_COLORS["detect"], opacity=0.8)
        ensemble_label.next_to(s3, UP, buff=0.25)

        xml_out = file_label("_CLIP.xml", STAGE_COLORS["detect"], width=1.5, height=0.4)
        xml_out.next_to(s3, DOWN, buff=0.5)

        arrow_3_out = Arrow(
            s3.get_bottom(),
            xml_out.get_top(),
            color=GRAY, stroke_width=1.5, buff=0.05, tip_length=0.1,
        )

        self.add_subcaption("Stage 3: Vision-language model identifies adverts at 1 FPS with ensemble voting", duration=6)
        self.play(
            Create(arrow_23),
            FadeIn(s3, shift=UP * 0.3),
            run_time=0.7,
        )
        self.wait(0.2)
        self.play(Write(ensemble_label), run_time=0.4)
        self.wait(0.3)
        self.play(
            Create(arrow_3_out),
            FadeIn(xml_out, shift=DOWN * 0.2),
            run_time=0.6,
        )
        self.wait(0.5)

        # ═══════════════════════════════════════════
        # Phase 6: Stage 4 — Frame Refinement 25 FPS (~8s)
        # ═══════════════════════════════════════════
        s4 = stage_box(4, "Refinement", "25 FPS", STAGE_COLORS["refine"])
        s4.shift(RIGHT * 6.5 + UP * s1_y)

        arrow_34 = arrow_between(s3, s4)

        refine_label = Text("3x Ensemble + MAD", font_size=9, font=MONO, color=STAGE_COLORS["refine"], opacity=0.8)
        refine_label.next_to(s4, UP, buff=0.25)

        refined_out = file_label("_refined.xml", STAGE_COLORS["refine"], width=1.8, height=0.4)
        refined_out.next_to(s4, DOWN, buff=0.5)

        arrow_4_out = Arrow(
            s4.get_bottom(),
            refined_out.get_top(),
            color=GRAY, stroke_width=1.5, buff=0.05, tip_length=0.1,
        )

        # Precision callout
        precision = Text("±40ms accuracy", font_size=10, font=MONO, color=STAGE_COLORS["refine"], opacity=0.8)
        precision.next_to(refined_out, DOWN, buff=0.2)

        self.add_subcaption("Stage 4: 25 FPS refinement achieves millisecond-precision advert boundaries", duration=5)
        self.play(
            Create(arrow_34),
            FadeIn(s4, shift=UP * 0.3),
            run_time=0.7,
        )
        self.wait(0.2)
        self.play(Write(refine_label), run_time=0.3)
        self.play(
            Create(arrow_4_out),
            FadeIn(refined_out, shift=DOWN * 0.2),
            run_time=0.5,
        )
        self.play(Write(precision), run_time=0.3)
        self.wait(0.5)

        # ═══════════════════════════════════════════
        # Phase 7: Stage 5 — Advert Clip Extraction (~7s)
        # ═══════════════════════════════════════════
        s5 = stage_box(5, "Advert Clips", "Extraction", STAGE_COLORS["extract"])
        s5.shift(RIGHT * 9.5 + UP * s1_y)

        arrow_45 = arrow_between(s4, s5)

        # Multiple output clips
        clip1 = file_label("Tesco.mp4", STAGE_COLORS["extract"], width=1.5, height=0.35)
        clip1.next_to(s5, DOWN, buff=0.5)
        clip2 = file_label("SkyMobile.mp4", STAGE_COLORS["extract"], width=1.5, height=0.35)
        clip2.next_to(clip1, DOWN, buff=0.15)
        clip3 = file_label("Cadbury.mp4", STAGE_COLORS["extract"], width=1.5, height=0.35)
        clip3.next_to(clip2, DOWN, buff=0.15)

        clip_group = VGroup(clip1, clip2, clip3)
        clips_brace = Brace(clip_group, RIGHT, color=GRAY, stroke_width=1)
        clips_label = Text("lossless H.264", font_size=8, font=MONO, color=GRAY)
        clips_label.next_to(clips_brace, RIGHT, buff=0.1)

        arrow_5_out = Arrow(
            s5.get_bottom(),
            clip1.get_top(),
            color=GRAY, stroke_width=1.5, buff=0.05, tip_length=0.1,
        )

        self.add_subcaption("Stage 5: Extract each advert as a lossless video clip", duration=4)
        self.play(
            Create(arrow_45),
            FadeIn(s5, shift=UP * 0.3),
            run_time=0.7,
        )
        self.wait(0.2)
        self.play(
            Create(arrow_5_out),
            FadeIn(clip1, shift=DOWN * 0.2),
            FadeIn(clip2, shift=DOWN * 0.2),
            FadeIn(clip3, shift=DOWN * 0.2),
            run_time=0.7,
        )
        self.play(
            Create(clips_brace),
            Write(clips_label),
            run_time=0.4,
        )
        self.wait(0.5)

        # ═══════════════════════════════════════════
        # Phase 8: Final reveal + tech credits (~5s)
        # ═══════════════════════════════════════════
        # Highlight the full pipeline title at the top
        pipeline_title = Text(
            "Ad Break Identifier Pipeline",
            font_size=22, font=MONO, color=PRIMARY, weight=BOLD,
        )
        pipeline_title.to_edge(UP, buff=0.5)

        flow_summary = Text(
            "CSV → Clips → 1 FPS → 25 FPS → Individual Adverts",
            font_size=13, font=MONO, color=GRAY,
        )
        flow_summary.next_to(pipeline_title, DOWN, buff=0.2)

        credits = Text(
            "Qwen3.5 · FFmpeg · vLLM · BFI National Archive",
            font_size=10, font=MONO, color=GRAY, opacity=0.6,
        )
        credits.to_edge(DOWN, buff=0.4)

        self.add_subcaption("The complete pipeline — from CSV scheduling data to frame-accurate advert clips", duration=4)
        self.play(
            Write(pipeline_title),
            Write(flow_summary),
            run_time=0.6,
        )
        self.play(Write(credits), run_time=0.4)
        self.wait(3.0)

        # Fade out
        all_objects = Group(*self.mobjects)
        self.play(FadeOut(all_objects), run_time=0.8)
        self.wait(0.3)
