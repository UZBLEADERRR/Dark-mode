"""Request/response schemas and the internal scene model."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

from . import config


class HeroOut(BaseModel):
    id: str
    name: str
    description: str = ""
    filename: str
    url: str


class CaptionStyle(BaseModel):
    """How the burned-in subtitles look. Every field is optional — whatever is
    left unset falls back to the template, and the template falls back to the
    house style, so a partial patch from the editor is always a complete look."""

    template: str = "bold"
    colour: str | None = None
    highlight: str | None = None
    outline_colour: str | None = None
    outline: float | None = Field(default=None, ge=0, le=12)
    shadow: float | None = Field(default=None, ge=0, le=12)
    box: Literal["none", "outline", "shadow", "box"] | None = None
    box_colour: str | None = None
    box_opacity: float | None = Field(default=None, ge=0, le=1)
    bold: bool | None = None
    italic: bool | None = None
    size: float | None = Field(default=None, ge=0.45, le=2.2)
    position: Literal["bottom", "middle", "top"] | None = None
    margin: float | None = Field(default=None, ge=-0.35, le=0.35)
    uppercase: bool | None = None
    animation: Literal["none", "fade", "pop", "rise"] | None = None
    font: str | None = Field(default=None, max_length=80)

    def patch(self) -> dict[str, Any]:
        return self.model_dump(exclude_none=True)


class OverlayIn(BaseModel):
    """One layer sitting on top of a scene — a caption of your own, or a picture."""

    id: str | None = Field(default=None, max_length=40)
    type: Literal["text", "image"] = "text"
    text: str = Field(default="", max_length=180)
    asset_id: str | None = None
    x: float = Field(default=0.5, ge=-0.2, le=1.2)
    y: float = Field(default=0.22, ge=-0.2, le=1.2)
    # text: font height as a fraction of the frame. image: width as a fraction.
    size: float = Field(default=0.08, ge=0.02, le=1.0)
    start: float = Field(default=0.0, ge=0, le=3600)
    end: float = Field(default=0.0, ge=0, le=3600)  # 0 = until the scene ends
    anim: str = "fade"
    colour: str = "#FFFFFF"
    outline_colour: str = "#000000"
    box: bool = False
    box_colour: str = "#000000"
    box_opacity: float = Field(default=0.6, ge=0, le=1)
    bold: bool = True
    italic: bool = False
    rotate: float = Field(default=0.0, ge=-180, le=180)
    opacity: float = Field(default=1.0, ge=0.05, le=1)
    font: str = Field(default="", max_length=80)


class ModelSettings(BaseModel):
    """Which model each stage calls, and each provider's default voice.

    Both are stored rather than exported to the environment, so a new model can
    be adopted from the UI without a redeploy. An empty value means "fall back
    to whatever the environment says".
    """

    models: dict[str, str] = Field(default_factory=dict)
    voices: dict[str, str] = Field(default_factory=dict)


class BrandKit(BaseModel):
    """Settings applied to every new video, so the look is set once."""

    accent: str = "#FF3B30"
    logo_asset_id: str = ""
    logo_x: float = Field(default=0.9, ge=0, le=1)
    logo_y: float = Field(default=0.1, ge=0, le=1)
    logo_size: float = Field(default=0.11, ge=0.02, le=0.5)
    logo_opacity: float = Field(default=0.9, ge=0.1, le=1)
    art_style: str = Field(default="", max_length=600)
    tone: str = Field(default="", max_length=200)
    voice_id: str = Field(default="", max_length=120)
    tts_provider: str = Field(default="", max_length=40)
    music_id: str = ""
    caption_style: CaptionStyle | None = None


class SceneInsert(BaseModel):
    """Add a scene after `after`. -1 puts it at the very front."""

    after: int = Field(default=-1, ge=-1)
    narration: str = Field(min_length=2, max_length=4000)


class SceneOrder(BaseModel):
    order: list[int] = Field(min_length=1)


class TranslateRequest(BaseModel):
    """Make this video again in another language, keeping the pictures."""

    language: str
    voice_id: str | None = None
    tts_provider: str | None = None


class RepurposeRequest(BaseModel):
    video_format: str
    # Reused stills are centre-cropped into the new frame; regenerating draws
    # them for it instead, at the cost of another round of image generation.
    regenerate_images: bool = True


class JobPatch(BaseModel):
    """Settings that belong to the whole video rather than one scene."""

    caption_style: CaptionStyle | None = None
    burn_subtitles: bool | None = None
    music_id: str | None = None
    music_start: float | None = Field(default=None, ge=0, le=36000)
    # Changing either marks every scene for re-recording, because the narrator
    # belongs to the video and not to any one line of it.
    voice_id: str | None = None
    tts_provider: str | None = None


class MusicSwap(BaseModel):
    """Change the soundtrack of a video that has already been rendered."""

    # An empty id is a real choice, not a missing one: it means take the music
    # off. That is why this is a plain string rather than an optional field.
    music_id: str = ""
    music_start: float = Field(default=0.0, ge=0, le=36000)


class CreateJobRequest(BaseModel):
    topic: str = Field(min_length=2, max_length=500)
    # When set, this is used as the narration verbatim and the Director only
    # decides where the scene cuts fall.
    script: str | None = Field(default=None, max_length=40000)
    video_format: str = "16:9"
    target_seconds: int = Field(default=180, ge=20, le=1800)
    language: str = "en"
    tone: str = "cinematic documentary"
    art_style: str = "cinematic photorealistic, dramatic lighting, 35mm film"
    hero_ids: list[str] = Field(default_factory=list)
    tts_provider: str | None = None
    image_provider: str | None = None
    voice_id: str | None = None
    music_id: str | None = None
    music_start: float = Field(default=0.0, ge=0, le=36000)
    # Kept for older clients; `caption_style.template` supersedes it.
    subtitle_style: str = "bold"
    caption_style: CaptionStyle | None = None
    burn_subtitles: bool = True
    # Put the Director's hook line on the frame for the first three seconds.
    auto_hook: bool = False
    # Stamp the brand logo on every scene, when one is configured.
    brand_logo: bool = True
    # fast | balanced | quality — trades encode time against picture quality.
    render_speed: Literal["fast", "balanced", "quality"] = "balanced"
    # How often the picture changes. steady = one image per line, as before;
    # dynamic and fast split longer lines across two to four, which costs that
    # many more images to generate.
    shot_pace: Literal["steady", "dynamic", "fast"] = "steady"
    # What happens on screen, in the user's own words. The Director is told to
    # follow it; leaving it empty is the old behaviour, where the topic alone
    # decides everything.
    action: str | None = Field(default=None, max_length=4000)
    # Cartoon mode: characters are cut out of their backgrounds and walked
    # across them, and the agents decide who moves where in each scene.
    animate_actors: bool = False
    # False stops after the draft so scenes can be reviewed and edited first.
    auto_render: bool = True

    def resolved_format(self) -> dict:
        return config.FORMATS.get(self.video_format, config.FORMATS["16:9"])


class ShotIn(BaseModel):
    """One picture inside a scene. Order in the list is order on screen."""

    prompt: str = Field(default="", max_length=2000)
    motion: str = ""
    motion_strength: float = Field(default=1.0, ge=0.2, le=2.5)
    # How this shot arrives. "" is a straight cut, which is what fast cutting
    # usually wants; anything else cross-fades in.
    transition: str = ""
    # Relative share of the scene's time. Only the ratio between a scene's shots
    # matters, so the absolute number is never meaningful on its own.
    weight: float = Field(default=1.0, ge=0.25, le=4.0)
    # Sent back unchanged by the editor so an edit does not orphan the picture
    # that has already been drawn for this shot.
    sid: str = Field(default="", max_length=64)


class ScenePatch(BaseModel):
    """Fields the user may rewrite on a single scene."""

    narration: str | None = Field(default=None, max_length=4000)
    image_prompt: str | None = Field(default=None, max_length=4000)
    motion: str | None = None
    motion_strength: float | None = Field(default=None, ge=0.3, le=1.8)
    # Cross-fade played when cutting *into* this scene. "" restores the default.
    transition: str | None = None
    on_screen_text: str | None = Field(default=None, max_length=60)
    hero_ids: list[str] | None = None
    # Who says this line: a hero id, or "" for the narrator.
    speaker: str | None = Field(default=None, max_length=120)
    overlays: list[OverlayIn] | None = None
    # A one-shot sting cued inside this scene. "" removes it.
    sfx_id: str | None = None
    sfx_volume: float | None = Field(default=None, ge=0, le=4)
    sfx_offset: float | None = Field(default=None, ge=0, le=600)
    # An empty list collapses the scene back to a single picture.
    shots: list[ShotIn] | None = None


class RegenerateRequest(BaseModel):
    image: bool = True
    voice: bool = False
    # Re-recording is the moment you discover the voice was wrong, so the voice
    # can be changed here. Both apply to the whole video rather than this scene
    # alone: one narrator who changes halfway through is a defect, not a feature.
    voice_id: str | None = None
    tts_provider: str | None = None
    # Re-record every scene with the new voice, not just this one.
    all_scenes: bool = False


class HeroPatch(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    description: str | None = Field(default=None, max_length=600)
    # A character with its own voice. "" hands the line back to the narrator.
    voice_id: str | None = Field(default=None, max_length=120)
    tts_provider: str | None = Field(default=None, max_length=40)


class Scene(BaseModel):
    index: int
    narration: str
    image_prompt: str = ""
    negative_prompt: str = ""
    hero_ids: list[str] = Field(default_factory=list)
    motion: str = "zoom_in"
    motion_strength: float = 1.0
    overlays: list[dict[str, Any]] = Field(default_factory=list)
    on_screen_text: str = ""
    # filled in during the run
    image_path: str | None = None
    audio_path: str | None = None
    audio_duration: float = 0.0
    start: float = 0.0
    words: list[dict[str, Any]] = Field(default_factory=list)


class JobOut(BaseModel):
    id: str
    status: str
    step: str
    progress: int
    topic: str
    video_format: str
    language: str
    error: str | None = None
    video_url: str | None = None
    download_url: str | None = None
    subtitle_url: str | None = None
    duration: float | None = None
    metadata: dict[str, Any] | None = None
    scenes: list[dict[str, Any]] | None = None
    logs: list[str] = Field(default_factory=list)
    created_at: str | None = None
    updated_at: str | None = None
