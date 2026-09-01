"""回文检测工具。

对外契约：
    is_palindrome(text: str) -> bool

判定规则：忽略大小写，忽略所有非字母数字字符（标点、空格、下划线等），
剩余字符序列正读与反读相同即为回文。空串与仅含非字母数字字符的串视为回文。
"""

from __future__ import annotations

__all__ = ["is_palindrome", "normalize"]


def normalize(text: str) -> str:
    """返回仅保留字母与数字并统一为小写的规范化字符串。

    使用 str.isalnum() 而非 ASCII 白名单，因此中文、全角数字等
    Unicode 字母数字字符同样参与比较。
    """
    if not isinstance(text, str):
        raise TypeError(f"text must be str, got {type(text).__name__}")
    return "".join(ch.lower() for ch in text if ch.isalnum())


def is_palindrome(text: str) -> bool:
    """判断 text 在忽略大小写与非字母数字字符后是否为回文。

    Args:
        text: 待判定的字符串。

    Returns:
        True 表示是回文，False 表示不是。

    Raises:
        TypeError: text 不是 str。

    Examples:
        >>> is_palindrome("A man, plan, canal: Panama!")
        True
        >>> is_palindrome("Hello!")
        False
    """
    cleaned = normalize(text)
    left, right = 0, len(cleaned) - 1
    while left < right:
        if cleaned[left] != cleaned[right]:
            return False
        left += 1
        right -= 1
    return True


if __name__ == "__main__":
    for sample in ("A man, plan, canal: Panama!", "Hello!"):
        print(f"{sample!r} -> {is_palindrome(sample)}")
