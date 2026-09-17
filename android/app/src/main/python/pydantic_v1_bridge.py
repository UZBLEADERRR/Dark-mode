"""Let the app's pydantic v2 models run on pydantic v1.

Nothing here is wanted on a server. It exists because pydantic v2 keeps its
validator in `pydantic-core`, a Rust extension that publishes no Android wheel,
so a phone build has to fall back to the last pure-Python pydantic — 1.10 — and
the handful of places where the two spellings differ have to be bridged rather
than written twice in `app/`.

Two differences matter, and only two, because the models use very little of
pydantic beyond field types and ranges:

**`model_dump` was `dict`.** Ten call sites ask for it. Same arguments, same
result; the name changed.

**A list's length is `min_length` in v2 and `min_items` in v1.** v1 does not
merely ignore the name it does not know, it refuses the model outright — the
constraint would silently not be enforced, and pydantic would rather say so.
Translating keeps the limit enforced instead of dropping it, which is the whole
reason to translate rather than strip.

Import this before `app.main`, and let it do nothing at all under v2.
"""

from __future__ import annotations

import typing

import pydantic

_SIZED_ORIGINS = (list, set, frozenset, tuple)


def _is_sequence(annotation: object) -> bool:
    """Does this annotation hold a number of things, rather than be one thing?

    `list[str] | None` has to answer yes as surely as `list[str]`: an optional
    list is still a list when it is present, and that is when a length limit
    applies.
    """
    origin = typing.get_origin(annotation)
    if origin is typing.Union or str(origin) == "types.UnionType":
        return any(_is_sequence(arg) for arg in typing.get_args(annotation))
    return (origin or annotation) in _SIZED_ORIGINS


def install() -> None:
    if not pydantic.VERSION.startswith("1."):
        return

    if not hasattr(pydantic.BaseModel, "model_dump"):
        pydantic.BaseModel.model_dump = pydantic.BaseModel.dict
        pydantic.BaseModel.model_dump_json = pydantic.BaseModel.json

    # Bound under another name: `import pydantic.schema` would rebind `pydantic`
    # itself as a local and shadow the module-level import above it.
    from pydantic import schema as pydantic_schema

    original = pydantic_schema.get_annotation_from_field_info
    if getattr(original, "_sarideo_bridged", False):
        return

    def bridged(annotation, field_info, field_name, validate_assignment=False):
        if _is_sequence(annotation):
            for v2_name, v1_name in (("min_length", "min_items"),
                                     ("max_length", "max_items")):
                limit = getattr(field_info, v2_name, None)
                if limit is not None and getattr(field_info, v1_name, None) is None:
                    setattr(field_info, v1_name, limit)
                    setattr(field_info, v2_name, None)
        return original(annotation, field_info, field_name, validate_assignment)

    bridged._sarideo_bridged = True
    # `ModelField.infer` imports this by name from the module at call time, so
    # replacing the module attribute reaches every model defined from here on —
    # which is all of them, since `app.main` has not been imported yet.
    pydantic_schema.get_annotation_from_field_info = bridged
