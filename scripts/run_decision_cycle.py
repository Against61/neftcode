"""Run the Python decision API and write a strict JSON result and operator card."""
import argparse
import json
from pathlib import Path
import sys
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from neft.decision_cycle import run_cycle,example_request
from neft.cycle_report import render_decision


def unique_object(pairs):
    result={}
    for key,value in pairs:
        if key in result:raise ValueError('Duplicate JSON key: '+key)
        result[key]=value
    return result


def main():
    ap=argparse.ArgumentParser(description=__doc__)
    source=ap.add_mutually_exclusive_group(required=True)
    source.add_argument('--input',type=Path)
    source.add_argument('--preset',choices=['base','bridge','limited-stock','no-clean','sulfur-rise','cetane','missing'])
    ap.add_argument('--output',type=Path,required=True)
    args=ap.parse_args()
    if args.output.exists():ap.error('Output directory already exists; choose a new directory')
    if args.input:
        try:
            request=json.loads(args.input.read_text(),object_pairs_hook=unique_object,
                               parse_constant=lambda x:(_ for _ in ()).throw(ValueError('Nonfinite JSON: '+x)))
        except (ValueError,OSError) as exc:
            ap.error(str(exc))
    else:request=example_request(args.preset)
    decision=run_cycle(request)
    args.output.mkdir(parents=True)
    (args.output/'decision.json').write_text(json.dumps(decision,ensure_ascii=False,indent=2,allow_nan=False)+'\n')
    (args.output/'report.html').write_text(render_decision(decision))
    print(json.dumps({'status':decision['status'],'output':str(args.output.resolve()),'industrial_command':False},ensure_ascii=False))
    return 0


if __name__=='__main__':raise SystemExit(main())
