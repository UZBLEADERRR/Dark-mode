"""Give harfbuzz's two FreeType finalizers the signature FreeType declares.

`FT_Generic_Finalizer` is `void (*)(void *)`, and harfbuzz 2.9.1 writes the two
functions as taking an `FT_Face` and casts the pointer at each of the three
places it is used. Clang grew `-Wcast-function-type-strict` after that release
and the NDK promotes it to an error, so the file no longer compiles.

Upstream's own fix, in 3.x, is this: take `void *` and cast inside. It removes
the casts rather than silencing the warning about them.
"""

import pathlib
import sys

EDITS = [
    ("static void\nhb_ft_face_finalize (FT_Face ft_face)\n{\n"
     "  hb_face_destroy ((hb_face_t *) ft_face->generic.data);\n}",
     "static void\nhb_ft_face_finalize (void *object)\n{\n"
     "  FT_Face ft_face = (FT_Face) object;\n"
     "  hb_face_destroy ((hb_face_t *) ft_face->generic.data);\n}"),

    ("static void\n_release_blob (FT_Face ft_face)\n{\n"
     "  hb_blob_destroy ((hb_blob_t *) ft_face->generic.data);\n}",
     "static void\n_release_blob (void *object)\n{\n"
     "  FT_Face ft_face = (FT_Face) object;\n"
     "  hb_blob_destroy ((hb_blob_t *) ft_face->generic.data);\n}"),

    ("ft_face->generic.finalizer != (FT_Generic_Finalizer) hb_ft_face_finalize",
     "ft_face->generic.finalizer != hb_ft_face_finalize"),

    ("ft_face->generic.finalizer = (FT_Generic_Finalizer) hb_ft_face_finalize;",
     "ft_face->generic.finalizer = hb_ft_face_finalize;"),

    ("ft_face->generic.finalizer = (FT_Generic_Finalizer) _release_blob;",
     "ft_face->generic.finalizer = _release_blob;"),
]

path = pathlib.Path(sys.argv[1])
source = path.read_text()
for old, new in EDITS:
    if new in source:
        continue  # already patched: the source tree survives between runs
    if old not in source:
        sys.exit(f"hb-ft.cc is not the file this patch was written for:\n{old}")
    source = source.replace(old, new, 1)
path.write_text(source)
print(f"patched {path.name}")
