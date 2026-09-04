"""Character frequency analysis module."""


def char_freq(text: str) -> dict[str, int]:
    """Count frequency of each character in text.
    
    Args:
        text: Input string to analyze.
        
    Returns:
        Dictionary mapping each character to its frequency.
    """
    freq = {}
    for char in text:
        freq[char] = freq.get(char, 0) + 1
    return freq


def top_chars(text: str, n: int = 10) -> list[tuple[str, int]]:
    """Return top N most frequent characters.
    
    Args:
        text: Input string to analyze.
        n: Number of top characters to return.
        
    Returns:
        List of (character, frequency) tuples sorted by frequency descending.
    """
    freq = char_freq(text)
    return sorted(freq.items(), key=lambda x: x[1], reverse=True)[:n]


if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1:
        text = sys.argv[1]
    else:
        text = input("Enter text: ")
    freq = char_freq(text)
    print("Character frequencies:")
    for char, count in sorted(freq.items()):
        print(f"  {char!r}: {count}")