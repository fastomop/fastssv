"""Note-Related Rules.

Rules specific to note and note_nlp tables.
"""

from .note_nlp_snippet_misuse import NoteNlpSnippetMisuseRule

__all__ = [
    "NoteNlpSnippetMisuseRule",
]
