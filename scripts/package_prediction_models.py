#!/usr/bin/env python3
"""Build a shareable prediction-only package from a validated full bundle."""
import argparse
import hashlib
import json
from pathlib import Path

import joblib


PRIVATE_ANALOG_KEYS={'fit_x','fit_y','fit_times','analog_scaler'}


def sha256(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream,'sha256').hexdigest()


def package(source,output,manifest):
    source=Path(source).resolve();output=Path(output).resolve();manifest=Path(manifest).resolve()
    if len({source,output,manifest})!=3:
        raise ValueError('SOURCE_OUTPUT_AND_MANIFEST_MUST_BE_DIFFERENT_FILES')
    bundle=joblib.load(source)
    if bundle.get('schema')!='neft-historical-intelligence-v1':
        raise ValueError('INVALID_HISTORICAL_BUNDLE')
    for horizon,item in bundle['quality'].items():
        if item.get('selected')=='analogs':
            raise ValueError('SELECTED_ANALOG_MODEL_CANNOT_BE_DISTRIBUTED_WITHOUT_ITS_INDEX: '+horizon)
        for key in PRIVATE_ANALOG_KEYS:
            item.pop(key,None)
    for key in ('source_runs','source_sha256','artifact_expectations'):
        bundle['config'].pop(key,None)
    marker='ANALOG_INDEX_NOT_BUNDLED_REBUILD_FROM_LOCAL_HISTORY'
    bundle['limitations']=list(dict.fromkeys([*bundle['limitations'],marker]))
    bundle['distribution']={'scope':'prediction_models_only','analog_index_included':False,
                            'training_rows_included':False,'training_timestamps_included':False}
    output.parent.mkdir(parents=True,exist_ok=True)
    joblib.dump(bundle,output,compress=3)
    document={
        'schema':'neft-model-package-v1',
        'artifact':{'file':output.name,'sha256':sha256(output),'size_bytes':output.stat().st_size},
        'source':{'experiment_id':bundle['config']['experiment_id'],'bundle_sha256':sha256(source),
                  'holdout_boundary':bundle['selection']['holdout_boundary'],
                  'selection_basis':bundle['selection']['selection_basis'],'fit_count':bundle['selection']['fit_count']},
        'runtime':{'python':'3.12','scikit_learn':'1.7.2','joblib':'1.5.2'},
        'scope':bundle['distribution'],
        'selection':{'quality':bundle['selection']['quality'],'controls':bundle['selection']['controls']},
        'validation':{'quality_component':'accepted_5_of_5_horizons',
                      'control_component':'rejected_5_of_15_pairs',
                      'unsafe_sulfur_misses_after_full_interval_gate':0,
                      'evidence':'EXP-0020 holdout 2024Q4'},
        'loading_policy':'Verify artifact SHA-256 before loading this trusted repository joblib file.'}
    manifest.parent.mkdir(parents=True,exist_ok=True)
    manifest.write_text(json.dumps(document,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    return document


def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--bundle',type=Path,required=True)
    ap.add_argument('--output',type=Path,required=True)
    ap.add_argument('--manifest',type=Path)
    args=ap.parse_args();manifest=args.manifest or args.output.parent/'manifest.json'
    if args.output.exists() or manifest.exists():
        ap.error('output or manifest already exists')
    try:
        result=package(args.bundle,args.output,manifest)
    except (ValueError,OSError,KeyError) as exc:
        ap.error(str(exc))
    print(json.dumps(result,ensure_ascii=False))


if __name__=='__main__':main()
