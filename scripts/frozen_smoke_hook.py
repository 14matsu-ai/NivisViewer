"""Record early frozen-smoke failures without a windowed traceback dialog."""

import json
import os
from pathlib import Path
import sys
import traceback


if "--smoke-test-output" in sys.argv:
    argument_index = sys.argv.index("--smoke-test-output") + 1
    if argument_index < len(sys.argv):
        destination = Path(sys.argv[argument_index])

        def record_smoke_failure(error_type, error, tb):
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_text(json.dumps({
                "success": False,
                "frozen": True,
                "stage": "startup",
                "errors": ["".join(traceback.format_exception(error_type, error, tb))],
            }, ensure_ascii=False, indent=2), encoding="utf-8")
            os._exit(1)

        sys.excepthook = record_smoke_failure
