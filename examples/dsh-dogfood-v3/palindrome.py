import re


def is_palindrome(text: str) -> bool:
    """Return True if text is a palindrome ignoring case, spaces, and punctuation."""
    # Keep only alphanumeric characters
    cleaned = re.sub(r'[^a-zA-Z0-9]', '', text)
    # Compare lowercase version
    return cleaned.lower() == cleaned.lower()[::-1]
