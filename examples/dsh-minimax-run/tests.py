"""Unit tests for palindrome module."""

import unittest
from palindrome import is_palindrome


class TestIsPalindrome(unittest.TestCase):
    """Test cases for is_palindrome function."""
    
    def test_normal_palindrome(self):
        """Test a simple palindrome without punctuation or spaces."""
        self.assertTrue(is_palindrome("racecar"))
        self.assertTrue(is_palindrome("level"))
        self.assertTrue(is_palindrome("radar"))
    
    def test_palindrome_with_punctuation(self):
        """Test palindromes that contain punctuation marks."""
        self.assertTrue(is_palindrome("A man, a plan, a canal: Panama"))
        self.assertTrue(is_palindrome("Was it a car or a cat I saw?"))
        self.assertFalse(is_palindrome("No, it is averted, I: Verizon?"))
    
    def test_non_palindrome(self):
        """Test strings that are not palindromes."""
        self.assertFalse(is_palindrome("hello"))
        self.assertFalse(is_palindrome("world"))
        self.assertFalse(is_palindrome("python"))
    
    def test_empty_string(self):
        """Test empty string (trivially a palindrome)."""
        self.assertTrue(is_palindrome(""))
    
    def test_pure_punctuation(self):
        """Test string with only punctuation marks."""
        self.assertTrue(is_palindrome("!!!"))
        self.assertTrue(is_palindrome(".,;:"))
        self.assertTrue(is_palindrome("---"))


if __name__ == "__main__":
    unittest.main()
