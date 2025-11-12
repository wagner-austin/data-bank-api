from __future__ import annotations


def ensure_corpus_file(
    *,
    source: str,
    language: str,
    data_dir: str,
    max_sentences: int,
    transliterate: bool,
    confidence_threshold: float,
) -> None:
    """Ensure a local corpus file exists for the given spec.

    In production this would materialize a corpus file. The tests monkeypatch this
    function to a no-op, but the presence and signature must be stable and typed.
    """
    # Intentionally a no-op in this package; real implementation is external.
    return
