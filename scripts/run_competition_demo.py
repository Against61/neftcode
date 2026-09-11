#!/usr/bin/env python3
"""Run the offline, auditable competition walkthrough over the real MCP boundary."""
import argparse
import asyncio
from pathlib import Path

from check_agent_protocol import main as run_protocol


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=Path('output/competition-demo'))
    asyncio.run(run_protocol(parser.parse_args()))
