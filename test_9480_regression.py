"""Real OCR evidence: faint invoice with one item and a large logo/footer."""
import json
from pathlib import Path
import unittest

from main import build_document
from invoice_result import extract_result
from targeted_ocr import merge_retries, plan_regions


class FaintSourceRegressionTests(unittest.TestCase):
    def payload(self):
        return json.loads(Path(__file__).with_name('tests').joinpath(
            'fixtures/9480_ocr.json').read_text(encoding='utf-8'))

    def test_canonical_path_preserves_real_fields_and_excludes_footer(self):
        payload = self.payload()
        for source in (payload, payload['pages'][0]['words']):
            with self.subTest(paged=isinstance(source, dict)):
                result = build_document(source)
                self.assertEqual(result['seller']['tax_number'], '300056327900003')
                self.assertEqual(result['invoice_date'], '11/03/2026')
                self.assertEqual(len(result['items']), 1)
                self.assertEqual(result['items'][0]['unit_price'], 90)
                self.assertEqual(result['items'][0]['taxable_amount'], 180)
                self.assertEqual(result['totals']['total_excluding_vat'], 180)
                self.assertEqual(result['totals']['total_vat'], 27)
                self.assertEqual(result['totals']['total_amount_including_vat'], 207)
                self.assertFalse(result['validation']['passed'])
                self.assertIsNone(result['invoice_number'])
                self.assertIsNone(result['items'][0]['quantity'])

    def test_missing_quantity_gets_priority_enhanced_numeric_crop(self):
        regions = plan_regions(self.payload()['pages'][0])
        region = next(r for r in regions if r['kind'] == 'numeric_cell'
                      and r['original']['text'] == '')
        self.assertEqual(region['language'], 'en')
        self.assertTrue(region['enhance'])
        self.assertEqual(region['dpi'], 400)
        self.assertLess(region['bbox'][0], 1130)
        self.assertGreater(region['bbox'][2], 1130)

    def test_invoice_retry_cannot_promote_customer_tax_number(self):
        page = {'words': []}
        word = dict(text='300402905100003', confidence=99,
                    left=200, top=200, width=250, height=30)
        merge_retries(page, [dict(kind='invoice_identifier', original=word, words=[word])])
        self.assertEqual(page['words'], [])

    def test_printed_identifier_with_punctuation_beats_large_handwriting(self):
        label = dict(text='Inv. No.', confidence=99, left=100, top=100, width=80, height=20)
        page = {'words': [label]}
        handwritten = dict(text='9480', confidence=99, left=200, top=80, width=120, height=85)
        printed = dict(text='692:', confidence=89, left=400, top=100, width=50, height=20)
        merge_retries(page, [dict(kind='invoice_identifier', original=label,
                                  words=[handwritten, printed])])
        self.assertEqual(page['words'][-1]['text'], '692')
        self.assertEqual(page['words'][-1]['raw_text'], '692:')

    def test_competing_header_identifiers_are_visible_for_review(self):
        payload = self.payload()
        payload['pages'][0]['targeted_ocr'] = {'alternatives': [
            {'kind': 'invoice_identifier', 'words': [
                {'text': '692:', 'confidence': 89}, {'text': '9480', 'confidence': 99},
                {'text': '300402905100003', 'confidence': 99}]}]}
        result = extract_result(payload, 'unrelated-name.pdf')
        note = next(note for note in result['review_notes'] if 'Multiple invoice identifiers' in note)
        self.assertIn('692', note)
        self.assertIn('9480', note)
        self.assertNotIn('300402905100003', note)
        self.assertEqual(result['status'], 'needs_review')

    def test_compact_and_canonical_totals_agree(self):
        payload = self.payload()
        compact, canonical = extract_result(payload, 'unrelated-name.pdf'), build_document(payload)
        self.assertEqual(compact['totals']['net_amount'], canonical['totals']['total_amount_including_vat'])
        self.assertEqual(len(compact['items']), len(canonical['items']))

    def test_fresh_english_retry_evidence_recovers_printed_fields(self):
        payload = json.loads(Path(__file__).with_name('tests').joinpath(
            'fixtures/9480_recovered_ocr.json').read_text(encoding='utf-8'))
        result = extract_result(payload, 'arbitrary-upload-name.pdf')
        self.assertEqual(result['invoice_number'], '692')
        self.assertEqual(result['customer']['vat_number'], '300402905100003')
        self.assertEqual(len(result['items']), 1)
        self.assertEqual(result['items'][0]['quantity'], 2)
        self.assertEqual(result['items'][0]['unit_price'], 90)
        self.assertEqual(result['items'][0]['amount'], 180)
        self.assertEqual(result['totals']['vat_amount'], 27)
        self.assertEqual(result['totals']['net_amount'], 207)
        self.assertEqual(result['status'], 'needs_review')
        canonical = build_document(payload)
        self.assertEqual(canonical['invoice_number'], '692')
        self.assertEqual(canonical['items'][0]['quantity'], 2)
        self.assertEqual(canonical['customer']['tax_number'], '300402905100003')

    def test_faint_printed_zero_discount_needs_matching_arithmetic(self):
        payload = json.loads(Path(__file__).with_name('tests').joinpath(
            'fixtures/9480_recovered_ocr.json').read_text(encoding='utf-8'))
        result = extract_result(payload, '9480.pdf')
        self.assertEqual(result['totals']['discount'], 0)
        self.assertTrue(any('totals.discount' in note for note in result['review_notes']))
        total_word = next(w for w in payload['pages'][0]['words'] if w['text'] == '207.00')
        total_word['text'] = '208.00'
        from layout_invoice import parse_layout
        self.assertIsNone(parse_layout(payload['pages'], '9480.pdf', 'eng+ara')['totals']['discount'])

    def test_faint_name_description_and_discount_get_focused_crops(self):
        page = json.loads(Path(__file__).with_name('tests').joinpath(
            'fixtures/9480_recovered_ocr.json').read_text(encoding='utf-8'))['pages'][0]
        regions = plan_regions(page)
        by_kind = {region['kind']: region for region in regions}
        self.assertGreater(by_kind['customer_name_ar']['bbox'][0], 800)
        self.assertLess(by_kind['customer_name_ar']['bbox'][2], 1500)
        self.assertEqual(by_kind['description_ar']['language'], 'ar')
        self.assertLess(by_kind['description_ar']['bbox'][0], 743)
        self.assertGreater(by_kind['description_ar']['bbox'][2], 1044)
        self.assertEqual(by_kind['footer_discount']['original']['text'], '(.00')
        self.assertNotIn('vat_identifier', by_kind)

    def test_confident_focused_arabic_replaces_faint_text_but_keeps_review(self):
        payload = json.loads(Path(__file__).with_name('tests').joinpath(
            'fixtures/9480_recovered_ocr.json').read_text(encoding='utf-8'))
        page = payload['pages'][0]
        original = next(w for w in page['words'] if w['text'] == 'ا لون اري تركيايه')
        customer_label = next(w for w in page['words'] if w['text'] == 'اسم العميل')
        name = 'مؤسسة اختبار للتجارة'
        description = 'جالون دهان تركي'
        merge_retries(page, [
            dict(kind='customer_name_ar', original=customer_label, words=[
                dict(text=name, confidence=91, left=1030, top=401, width=380, height=47,
                     source='targeted_ocr', retry_kind='customer_name_ar')]),
            dict(kind='description_ar', original=original, words=[
                dict(text=description, confidence=92, left=710, top=595, width=335, height=42,
                     source='targeted_ocr', retry_kind='description_ar')]),
        ])
        result = extract_result(payload, '9480.pdf')
        self.assertEqual(result['customer']['name'], name)
        self.assertEqual(result['items'][0]['description'], description)
        self.assertEqual(result['status'], 'needs_review')
        self.assertTrue(any('Check names and item descriptions' in note for note in result['review_notes']))


if __name__ == '__main__':
    unittest.main()
