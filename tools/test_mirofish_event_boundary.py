"""VALEDIA-EXTRACTOR-WORD-BOUNDARIES-1 focused offline regressions; no models/stores."""
import ast,copy,hashlib,importlib.util,json,re,sys,unittest
from pathlib import Path
from unittest.mock import patch
TOOLS=Path(__file__).parent
spec=importlib.util.spec_from_file_location('event_revision_stage',TOOLS/'mirofish_native_stage.py');stage=importlib.util.module_from_spec(spec);spec.loader.exec_module(stage)
OFFLINE_WORK=stage.CREW/'valedia-native-20261108-extractor-war-boundary-offline'
REPORT_ROOT=stage.CREW/'valedia-native-20261108-report64-coverage/owned-source/producer/backend/uploads/reports/report_ce440d084536'
def extractor(path,name):
    module=__import__('types').ModuleType(name);module.__file__=str(path);exec(compile(path.read_bytes(),str(path),'exec'),module.__dict__);return module
class EventWordBoundary(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.parent=extractor(stage.EXTRACTOR_PARENT_SNAPSHOT,'event_parent_snapshot');cls.current=extractor(stage.EXTRACTOR_CANONICAL,'event_canonical_revision')
    def test_false_embedded_war_and_preserved_macro_warsh(self):
        for text in ['toward NVDA','reward','forward','Warsh']:
            events,_=self.current.extract_events(text);self.assertFalse(any(e['type']=='geopolitics' for e in events),text)
        old,_=self.parent.extract_events('Warsh');new,_=self.current.extract_events('Warsh');self.assertEqual([e for e in old if e['matched']=='warsh'],[e for e in new if e['matched']=='warsh']);self.assertTrue(any(e['matched']=='warsh' for e in new))
    def test_true_whole_word_war_plural_hyphen_and_case(self):
        for text in ['war','wars','WAR','WaRs','anti-war','war-related','wars,']:self.assertTrue(any(e['type']=='geopolitics' and e['matched']=='war' and e['direction']=='RISK_OFF' for e in self.current.extract_events(text)[0]),text)
        for text in ['Warsaw','warlike','rewards','towards']:self.assertFalse(any(e['matched']=='war' for e in self.current.extract_events(text)[0]),text)
    def test_other_intentional_stems_and_asset_semantics_unchanged(self):
        for text in ['geopolitical tensions','regulatory approval','hawkish Fed','corporate acquisition']:
            self.assertEqual(self.parent.extract_events(text),self.current.extract_events(text),text)
        self.assertEqual(self.parent.extract_signals_from_report('NVDA HOLD neutral'),self.current.extract_signals_from_report('NVDA HOLD neutral'))
    def test_exact_closed_revision_guard_rejects_unknowns(self):
        witness=stage.canonical_revision_witness(stage.EXTRACTOR_CANONICAL,stage.EXTRACTOR_PARENT_SHA);self.assertEqual(witness['reviewed_new_sha256'],stage.EXTRACTOR_NEW_SHA)
        for path,expected in [(stage.EXTRACTOR_CANONICAL.with_name('other.py'),stage.EXTRACTOR_PARENT_SHA),(stage.EXTRACTOR_CANONICAL,'f'*64)]:
            with self.assertRaises(RuntimeError):stage.canonical_revision_witness(path,expected)
        real_sha=stage.sha
        with patch.object(stage,'sha',side_effect=lambda path:'f'*64 if Path(path)==stage.EXTRACTOR_CANONICAL else real_sha(path)):
            with self.assertRaisesRegex(RuntimeError,'unreviewed canonical'):stage.canonical_revision_witness(stage.EXTRACTOR_CANONICAL,stage.EXTRACTOR_PARENT_SHA)
        with patch.object(stage,'sha',side_effect=lambda path:'f'*64 if Path(path)==stage.EXTRACTOR_PARENT_SNAPSHOT else real_sha(path)):
            with self.assertRaisesRegex(RuntimeError,'parent W16'):stage.canonical_revision_witness(stage.EXTRACTOR_CANONICAL,stage.EXTRACTOR_PARENT_SHA)
    def test_archived_real_report_offline_replay_keeps_hold_and_negative_buy_predicate(self):
        meta=json.loads((REPORT_ROOT/'meta.json').read_text());markdown=meta['markdown_content'];summary=(meta.get('outline') or {}).get('summary','');before=self.parent.derive_report_features(markdown,summary);after=self.current.derive_report_features(markdown,summary);self.assertTrue(any(e['matched']=='war' for e in before['events']));self.assertFalse(any(e['matched']=='war' for e in after['events']));self.assertEqual(before['signals'],after['signals']);signal=after['signals']['NVDA'];self.assertEqual((signal['direction'],signal['score'],signal['confidence']),('HOLD',45.0,0.24));self.assertFalse(signal['direction']=='BUY' and signal['score']>55)
    def test_offline_staged_revision_exact_copy_and_distinct_lineage(self):
        if not (OFFLINE_WORK/'builder/candidate-manifest.json').exists():self.skipTest('offline stage follows unit tests')
        self.assertEqual((OFFLINE_WORK/stage.EXTRACTOR_REL).read_bytes(),stage.EXTRACTOR_CANONICAL.read_bytes());self.assertEqual(stage.sha(stage.EXTRACTOR_PARENT_SNAPSHOT),stage.EXTRACTOR_PARENT_SHA);manifest=json.loads((OFFLINE_WORK/'builder/candidate-manifest.json').read_text());self.assertEqual(manifest['intentional_canonical_source_revision']['reviewed_new_sha256'],stage.EXTRACTOR_NEW_SHA);self.assertFalse(manifest['launch_authorized']);self.assertFalse((OFFLINE_WORK/'builder/one-attempt.claim').exists());lineage=json.loads((OFFLINE_WORK/'builder/staging-ancestry.json').read_text());self.assertEqual(lineage['exact_owned_source_files'],67);self.assertEqual(sum(r['kind']=='ONE_CLOSED_CANONICAL_EXTRACTOR_REVISION' for r in lineage['source_copies']),1)
if __name__=='__main__':unittest.main()
