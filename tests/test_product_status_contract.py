"""Acceptance accounting must never imply a product pass from local tests."""
from pathlib import Path
import json
import unittest

ROOT=Path(__file__).resolve().parents[1]

class ProductStatusContract(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.catalog=json.loads((ROOT/'product_acceptance/TARGET_CASES.json').read_text(encoding='utf-8'))['cases']

    def test_canonical_and_added_case_ids_are_complete_and_unique(self):
        ids=[x['id'] for x in self.catalog]
        self.assertEqual(len(ids),len(set(ids)))
        self.assertEqual(set(ids),{'T-'+f'{n:03d}' for n in range(1,183)})

    def test_no_unsourced_original_case_or_invented_pass(self):
        for case in self.catalog:
            if int(case['id'][2:])<=169:
                self.assertIn(case['baseline'],{'07_TESTSTRATEGIE_TRACEABILITY.md',
                        '16_TRACEABILITY_SECURITY_NFR.md','23_TEST_PLAN_V06.md'})
                self.assertEqual(case['verification'],'NOT_VERIFIED')
            else:
                self.assertEqual(case['verification'],'NOT_RUN')

    def test_explicitly_red_target_conformance_cases_are_separate_from_green_suite(self):
        content=(ROOT/'product_acceptance/test_target_product_e2e.py').read_text(encoding='utf-8')
        for key in ('082','102','163'):
            self.assertIn('def test_t'+key+'_',content)
        self.assertIn('FAIL_TARGET_CONTRACT',
                (ROOT/'scripts/run_product_status.py').read_text(encoding='utf-8'))

if __name__=='__main__':unittest.main()
