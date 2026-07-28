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
    subtitle_style: Literal["bold", "clean", "karaoke"] = "bold"
    burn_subtitles: bool = True
    # False stops after the draft so scenes can be reviewed and edited first.
    auto_render: bool = True

    def resolved_format(self) -> dict:
        return config.FORMATS.get(self.video_format, config.FORMATS["16:9"])


class ScenePatch(BaseModel):
    """Fields the user may rewrite on a single scene."""

    narration: str | None = Field(default=None, max_length=4000)
    image_prompt: str | None = Field(default=None, max_length=4000)
    motion: str | None = None
    # Cross-fade played when cutting *into* this scene. "" restores the default.
    transition: str | None = None
    on_screen_text: str | None = Field(default=None, max_length=60)
    hero_ids: list[str] | None = None


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
