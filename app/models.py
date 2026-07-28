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
    # False stops after the draft so scenes can be reviewed and edited first.
    auto_render: bool = True

    def resolved_format(self) -> dict:
        return config.FORMATS.get(self.video_format, config.FORMATS["16:9"])


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
    overlays: list[OverlayIn] | None = None
    # A one-shot sting cued inside this scene. "" removes it.
    sfx_id: str | None = None
    sfx_volume: float | None = Field(default=None, ge=0, le=4)
    sfx_offset: float | None = Field(default=None, ge=0, le=600)


class RegenerateRequest(BaseModel):
    image: bool = True
    voice: bool = False


class HeroPatch(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    description: str | None = Field(default=None, max_length=600)


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
