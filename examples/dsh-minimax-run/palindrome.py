"""Palindrome detection module."""

import string


def is_palindrome(text: str) -> bool:
    """
    Check if the given text is a palindrome.
    
    Ignores case, spaces, and punctuation.
    
    Args:
        text: Input string to check.
        
    Returns:
        True if the text is a palindrome, False otherwise.
    """
    # Remove punctuation and whitespace, then convert to lowercase
    cleaned = ''.join(
        char.lower() 
        for char in text 
        if char not in string.punctuation and not char.isspace()
    )
    
    # Check if the cleaned string equals its reverse
    return cleaned == cleaned[::-1]
