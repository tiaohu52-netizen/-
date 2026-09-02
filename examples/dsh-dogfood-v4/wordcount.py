def count_words(text: str) -> int:
    """Count words by splitting on whitespace."""
    if not text:
        return 0
    words = text.split()
    return len(words)
