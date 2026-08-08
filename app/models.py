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
    # Which provider writes the script: anthropic, openai, gemini, or "auto" to
    # take whichever has a key. None leaves the current choice alone, so a page
    # that only edits model names does not have to know about this.
    text_provider: str | None = Field(default=None, max_length=20)
    # Which provider draws the scenes. Chosen here rather than only in the
    # environment because the stored choice is what actually wins at startup —
    # so the place it is stored has to be the place it can be seen and changed.
    image_provider: str | None = Field(default=None, max_length=20)


class BrandKit(BaseModel):
    """Settings applied to every new video, so the look is set once."""

    accent: str = "#FF3B30"
    logo_asset_id: str = ""
    logo_x: float = Field(default=0.9, ge=0, le=1)
    logo_y: float = Field(default=0.1, ge=0, le=1)
    logo_size: float = Field(default=0.11, ge=0.02, le=0.5)
    logo_opacity: float = Field(default=0.9, ge=0.1, le=1)
    # Which frame shape the placement was last checked against. Nothing reads it
    # but the placement pad, which reopens on the shape you left it on — a corner
    # that reads well on a wide frame can sit under a phone's own furniture on a
    # tall one, so which one you were looking at is worth remembering.
    logo_shape: Literal["16:9", "9:16"] = "16:9"
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
    # Which provider draws this project's scenes. A project remembers the one it
    # was started with, so switching the app over leaves every half-finished
    # video behind — this is how one of them is brought across. An empty string
    # means "follow the app", which is a different answer from not saying.
    image_provider: str | None = Field(default=None, max_length=20)


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
    # Stop as soon as the script is written, before a single picture is drawn or
    # a single line recorded — the one moment when changing your mind is free.
    #
    # On by default, because off by default meant it was only ever on for the one
    # caller that remembered to ask: the create form set it, and a video started
    # from the chat, or from a scheduled plan, silently went all the way through
    # to a render nobody had read. A default that only holds for the path it was
    # written on is not a default. The one place it is deliberately turned off is
    # a planned video, which has nobody watching to approve it.
    review_script: bool = True
    # False stops after the draft so scenes can be reviewed and edited first.
    auto_render: bool = True
    # Take the cheap, slow road: the provider's batch API instead of one request
    # per picture. Half the price, answers within hours rather than seconds — so
    # it is only ever set when something knows there is time, which in practice
    # means a planned video whose slot is hours away.
    batch: bool = False
    # How long the batch may take before the render stops waiting and pays full
    # price for the rest. Set from how far off the video is actually due.
    batch_patience_minutes: float = Field(default=0.0, ge=0, le=1440)

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
    # What is wrong with the picture that is there. Drawing the same prompt again
    # and hoping for a better roll is the expensive way to fix "his jacket is the
    # wrong colour" — saying so is the cheap one. Kept on the scene, so it still
    # applies the next time this scene is drawn.
    note: str = Field(default="", max_length=600)
    # Re-recording is the moment you discover the voice was wrong, so the voice
    # can be changed here. Both apply to the whole video rather than this scene
    # alone: one narrator who changes halfway through is a defect, not a feature.
    voice_id: str | None = None
    tts_provider: str | None = None
    # Re-record every scene with the new voice, not just this one.
    all_scenes: bool = False
    # Or only a stretch of them. Half a video recorded in the wrong voice is the
    # ordinary way this goes wrong, and re-recording all of it to fix the second
    # half means paying twice for the half that was already right.
    from_index: int | None = Field(default=None, ge=0)
    to_index: int | None = Field(default=None, ge=0)
    # Re-recording is also when you notice it is in the wrong language. Unlike
    # the range, this always covers the whole video: half a video in another
    # language is not something anybody wants. The subtitles follow on their own,
    # because they are written from the narration.
    language: str | None = None


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


class ProfilePatch(BaseModel):
    """Correct what was read off a channel screenshot, or name it yourself.

    Every field the reading produced is correctable, not only the prose: the
    specifics are what the assistant is actually held to, so a wrong `niche` is
    worth more to fix than a wrong sentence.
    """

    handle: str | None = Field(default=None, max_length=120)
    summary: str | None = Field(default=None, max_length=4000)
    niche: str | None = Field(default=None, max_length=400)
    audience: str | None = Field(default=None, max_length=400)
    language: str | None = Field(default=None, max_length=10)
    pillars: str | None = Field(default=None, max_length=800)
    style: str | None = Field(default=None, max_length=800)


class ChatTurn(BaseModel):
    message: str = Field(min_length=1, max_length=4000)


class LengthAsk(BaseModel):
    """A title, and what to say back about how long it should be."""

    topic: str = Field(min_length=2, max_length=500)
    language: str = Field(default="", max_length=8)


class PublishRequest(BaseModel):
    """What to publish a finished video as.

    Every field is optional because the publishing pack the app already wrote is
    the default — the point of the sheet is to change what you disagree with, not
    to retype it.
    """

    title: str | None = Field(default=None, max_length=100)
    description: str | None = Field(default=None, max_length=5000)
    tags: list[str] | None = None
    privacy: Literal["private", "unlisted", "public"] = "private"
    # RFC3339. YouTube only honours it on a private video, which is what makes
    # "prepare it now, publish it Tuesday at nine" work.
    publish_at: str | None = Field(default=None, max_length=40)
    with_thumbnail: bool = True


class PlanIn(BaseModel):
    """A video asked for in advance, or an idea put on the shelf for one.

    Two things with one shape, separated by whether `publish_at` is set. With a
    slot it builds itself and publishes at the minute chosen. Without one it
    just sits there under its channel's name until you press the button — which
    is what a content plan actually is: a list you work through, not a timer.
    """

    topic: str = Field(min_length=2, max_length=500)
    # Which of your channels this is for. Free text rather than a link to a
    # stored profile: a plan is often written for a channel before there is a
    # screenshot of it in the app, and refusing it then would be absurd.
    channel: str = Field(default="", max_length=80)
    # When it should be live. RFC3339; the browser sends an absolute instant.
    # Empty means the shelf: nothing starts until it is asked for.
    publish_at: str = Field(default="", max_length=40)
    title: str = Field(default="", max_length=200)
    # Who draws the pictures for this one. `manual` is the answer to "voice and
    # subtitles now, pictures later" — the draft stops at the images and hands
    # over the prompts. Empty means whatever the app is set to.
    image_provider: str = Field(default="", max_length=20)
    video_format: str = "9:16"
    target_seconds: int = Field(default=45, ge=20, le=1800)
    language: str = "uz"
    tone: str = ""
    art_style: str = ""
    action: str = ""
    hero_ids: list[str] = Field(default_factory=list)
    animate_actors: bool = False
    music_id: str | None = None
    privacy: Literal["private", "unlisted", "public"] = "public"
    # How long before the slot to start building. A fifty-scene video is not a
    # five-minute job, and the point of planning ahead is not to be waiting.
    lead_minutes: int = Field(default=240, ge=10, le=10080)
    # Waits for you to look at it. On by default: a video published unread cannot
    # be un-seen by whoever already saw it.
    approve: bool = True
    # The cheap slow road for the pictures. `auto` uses it when the slot is far
    # enough off to afford the wait; `on` and `off` say so outright.
    batch: Literal["auto", "on", "off"] = "auto"


class PlanPatch(BaseModel):
    title: str | None = Field(default=None, max_length=200)
    channel: str | None = Field(default=None, max_length=80)
    publish_at: str | None = Field(default=None, max_length=40)
    lead_minutes: int | None = Field(default=None, ge=10, le=10080)
    privacy: Literal["private", "unlisted", "public"] | None = None
    approve: bool | None = None
    batch: Literal["auto", "on", "off"] | None = None
    status: Literal["planned", "cancelled", "idea"] | None = None


# ── API keys ──────────────────────────────────────────────────────────────────

KeyProvider = Literal["gemini", "anthropic", "openai", "elevenlabs", "fal"]


class ShortCut(BaseModel):
    """One Short to cut out of a finished video, by scene range.

    A range, not a pair of timestamps: the app knows exactly how long every
    scene runs because it recorded the voice-over, so a range is a real duration
    and cuts land on a sentence rather than mid-word.
    """

    from_index: int = Field(ge=0)
    to_index: int = Field(ge=0)
    title: str = Field(default="", max_length=200)
    video_format: Literal["9:16", "1:1", "4:5", "16:9"] = "9:16"
    # The stills were framed for the long video. Reused, they are centre-cropped
    # into the taller frame, which is right until the subject sits near an edge.
    regenerate_images: bool = False
    render: bool = True


class ScriptNote(BaseModel):
    """What to fix in a script, in your own words."""

    note: str = Field(min_length=2, max_length=2000)


class ShortsAll(BaseModel):
    """Cut every Short this video holds, in one go.

    `limit` is a ceiling on the asking, not a target: the model is told to stop
    when the video stops holding stretches that stand alone. Ten is high enough
    that a long video is not cut off early and low enough that one press cannot
    queue an afternoon of rendering by accident.
    """

    limit: int = Field(default=10, ge=1, le=10)
    video_format: Literal["9:16", "1:1", "4:5", "16:9"] = "9:16"
    # On by default here, unlike a single hand-picked cut: cutting everything is
    # the unattended path, and nobody is watching to notice a subject that fell
    # outside the taller frame.
    regenerate_images: bool = True
    render: bool = True


class ApiKeyIn(BaseModel):
    """One key to store. Several per provider is the point, so nothing is unique.

    There is no minimum length and no expected shape. Google alone issues both
    `AIza…` and `AQ.…` keys, of different lengths, and every other provider does
    something else again — so any rule this app invented about what a key looks
    like could only ever start rejecting real keys. The provider is the authority
    on whether a key is valid, and it says so when the key is used or tested.

    Whitespace is stripped throughout rather than just at the ends: a key copied
    from a dashboard on a phone arrives wrapped, with newlines inside it.
    """

    provider: KeyProvider
    secret: str = Field(min_length=1, max_length=2000)
    label: str = Field(default="", max_length=80)


class ApiKeyPatch(BaseModel):
    label: str | None = Field(default=None, max_length=80)
    enabled: bool | None = None
    # Replacing the secret keeps the row's history, which is what you want when a
    # key is rotated rather than swapped for a different account's.
    secret: str | None = Field(default=None, min_length=1, max_length=2000)
    # Forget a cooldown on request — a limit the provider has since lifted should
    # not keep a key benched because we wrote down a pessimistic guess.
    clear_cooldown: bool = False


class FlowFail(BaseModel):
    """A picture that did not come out, reported by whatever was making it."""

    reason: str = Field(default="", max_length=300)
    # True puts the prompt back in the queue instead of failing the scene. The
    # difference matters: a Flow tab that was closed mid-generation should be
    # tried again, a prompt the model refuses should not be tried forever.
    retry: bool = False


class FlowPrompt(BaseModel):
    """A rewritten prompt for a picture nobody has drawn yet."""

    prompt: str = Field(min_length=2, max_length=4000)


class FlowMode(BaseModel):
    """Whether the scenes are drawn in your own browser or bought from an API."""

    on: bool
