import unittest
from label_value_pairing import pair_labels


def box(text, x, y, page=1):
    return dict(text=text, left=x, top=y, width=80, height=20,
                confidence=99, page=page)


class LabelValueMappingTests(unittest.TestCase):
    def test_label_never_consumes_value_from_another_page(self):
        result = pair_labels([box('Invoice number', 100, 100),
                              box('WRONG-123', 190, 100, page=2)])
        self.assertNotIn('invoice_number', result)

    def test_another_field_label_is_not_a_value(self):
        result = pair_labels([box('Invoice number', 100, 100),
                              box('Invoice date', 190, 100),
                              box('INV-123', 100, 140)])
        self.assertEqual(result['invoice_number']['text'], 'INV-123')

    def test_label_words_do_not_match_unrelated_words(self):
        self.assertEqual(pair_labels([box('streetwear', 100, 100),
                                      box('999', 200, 100)]), {})
