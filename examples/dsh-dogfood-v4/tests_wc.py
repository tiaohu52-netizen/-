import wordcount

assert wordcount.count_words('hello world') == 2, "'hello world' should be 2"
assert wordcount.count_words('  a  b  ') == 2, "'  a  b  ' should be 2"
assert wordcount.count_words('') == 0, "'' should be 0"

print('All tests passed!')
