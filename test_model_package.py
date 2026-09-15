import json
from pathlib import Path
import tempfile
import unittest

import joblib

from scripts.agent_connection import DEFAULT_MODEL_MANIFEST, bundled_model
from scripts.package_prediction_models import PRIVATE_ANALOG_KEYS, package


class ModelPackageTests(unittest.TestCase):
    def test_repository_model_matches_manifest_and_github_limit(self):
        path,digest=bundled_model()
        manifest=json.loads(DEFAULT_MODEL_MANIFEST.read_text())
        self.assertEqual(digest,manifest['artifact']['sha256'])
        self.assertLess(path.stat().st_size,100*1024*1024)
        bundle=joblib.load(path)
        self.assertEqual(bundle['distribution']['scope'],'prediction_models_only')
        self.assertFalse(bundle['distribution']['training_rows_included'])
        self.assertEqual(bundle['selection']['quality'],{str(h):'hgb' for h in (15,30,60,120,180)})
        for item in bundle['quality'].values():
            self.assertTrue(PRIVATE_ANALOG_KEYS.isdisjoint(item))

    def test_packager_refuses_a_selected_analog_model(self):
        source,_=bundled_model();bundle=joblib.load(source)
        bundle['quality']['15']['selected']='analogs'
        with tempfile.TemporaryDirectory(prefix='neft-model-package-') as folder:
            root=Path(folder);bad=root/'bad.joblib';joblib.dump(bundle,bad)
            with self.assertRaisesRegex(ValueError,'SELECTED_ANALOG_MODEL'):
                package(bad,root/'out.joblib',root/'manifest.json')


if __name__=='__main__':unittest.main()
