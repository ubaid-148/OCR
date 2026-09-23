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

    def test_invoice_candidate_uses_label_geometry_not_confidence(self):
        from label_value_pairing import apply_invoice_number_candidates
        near=dict(box('9480',190,100),confidence=70)
        far=box('692',400,100)
        result={'data':{},'quality':{}}
        apply_invoice_number_candidates(result,[{'page':1,'words':[box('Inv. No.',100,100),far,near]}])
        self.assertEqual(result['data']['invoice']['invoice_number'],'9480')
        self.assertTrue(any('alternate candidate found, not selected: 692' in s for s in result['quality']['review_reasons']))

    def test_candidate_does_not_cross_pages_or_select_date(self):
        from label_value_pairing import invoice_number_candidates
        self.assertEqual(invoice_number_candidates([
            {'page':1,'words':[box('Inv. No.',100,100),box('11/03/2026',190,100)]},
            {'page':2,'words':[box('123',190,100)]}]),[])

    def test_live_bilingual_labels_keep_both_candidates(self):
        import json
        from pathlib import Path
        from label_value_pairing import apply_invoice_number_candidates
        payload=json.loads(Path('tests/fixtures/9480_live_ocr.json').read_text())
        result={'data':{},'quality':{}}
        apply_invoice_number_candidates(result,payload['pages'])
        candidates=result['quality']['invoice_number_candidates']
        self.assertEqual({c['value'] for c in candidates},{'9480','692'})
        self.assertEqual(result['data']['invoice']['invoice_number'],candidates[0]['value'])
        self.assertLess(candidates[0]['distance'],candidates[1]['distance'])
        self.assertTrue(result['quality']['needs_review'])
