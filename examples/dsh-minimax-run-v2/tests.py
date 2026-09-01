"""palindrome 模块的 unittest 测试，逐条覆盖合同验收 check。"""

from __future__ import annotations

import inspect
import os
import unittest

import palindrome
from palindrome import is_palindrome

WORKSPACE = os.path.dirname(os.path.abspath(__file__))


class TestDeliverables(unittest.TestCase):
    """check 1 / check 2：交付物文件存在。"""

    def test_palindrome_module_file_exists(self) -> None:
        self.assertTrue(os.path.isfile(os.path.join(WORKSPACE, "palindrome.py")))

    def test_tests_module_file_exists(self) -> None:
        self.assertTrue(os.path.isfile(os.path.join(WORKSPACE, "tests.py")))


class TestSignature(unittest.TestCase):
    """check 3：签名为 is_palindrome(text: str) -> bool。"""

    def test_callable_exported(self) -> None:
        self.assertTrue(callable(getattr(palindrome, "is_palindrome", None)))

    def test_parameter_name_and_annotation(self) -> None:
        sig = inspect.signature(is_palindrome)
        params = list(sig.parameters.values())
        self.assertEqual(len(params), 1)
        self.assertEqual(params[0].name, "text")
        self.assertEqual(params[0].annotation, "str")

    def test_return_annotation(self) -> None:
        self.assertEqual(inspect.signature(is_palindrome).return_annotation, "bool")

    def test_returns_actual_bool(self) -> None:
        self.assertIsInstance(is_palindrome("abba"), bool)


class TestContractExamples(unittest.TestCase):
    """check 4 / check 5：合同给出的两个具体样例。"""

    def test_panama_sentence_is_palindrome(self) -> None:
        self.assertIs(is_palindrome("A man, plan, canal: Panama!"), True)

    def test_hello_is_not_palindrome(self) -> None:
        self.assertIs(is_palindrome("Hello!"), False)


class TestBehaviour(unittest.TestCase):
    """补充行为：大小写、标点、空串、单字符、Unicode、数字。"""

    def test_positive_cases(self) -> None:
        for text in (
            "",
            "   ",
            "!!!",
            "a",
            "aa",
            "aba",
            "AbBa",
            "No 'x' in Nixon",
            "Was it a car or a cat I saw?",
            "12321",
            "上海自来水来自海上",
        ):
            with self.subTest(text=text):
                self.assertTrue(is_palindrome(text))

    def test_negative_cases(self) -> None:
        for text in ("ab", "abca", "palindrome", "12345", "Hello, World!", "中文测试"):
            with self.subTest(text=text):
                self.assertFalse(is_palindrome(text))

    def test_case_insensitive(self) -> None:
        self.assertEqual(is_palindrome("Racecar"), is_palindrome("racecar"))

    def test_rejects_non_string(self) -> None:
        for value in (None, 121, ["a"], object()):
            with self.subTest(value=value):
                with self.assertRaises(TypeError):
                    is_palindrome(value)  # type: ignore[arg-type]

    def test_does_not_mutate_input(self) -> None:
        text = "A man, plan, canal: Panama!"
        is_palindrome(text)
        self.assertEqual(text, "A man, plan, canal: Panama!")


class TestNormalize(unittest.TestCase):
    """规范化辅助函数的直接验证。"""

    def test_strips_non_alnum_and_lowercases(self) -> None:
        self.assertEqual(
            palindrome.normalize("A man, plan, canal: Panama!"),
            "amanplancanalpanama",
        )

    def test_empty_result(self) -> None:
        self.assertEqual(palindrome.normalize(" ,.;!? "), "")


if __name__ == "__main__":
    unittest.main(verbosity=2)
