# Unadopted TODO19 JPEG experiment

These are historical investigation artifacts, not active code or instructions
to apply the patch. The user redirected TODO19 to persisted warm-cache startup.
`TODO19_JPEG_UNADOPTED.patch` records the withdrawn production prototype;
`test_browser_jpeg_thumbnail.py.txt` and
`benchmark_browser_jpeg_decode.py.txt` preserve its tests and microbenchmark.
The latter refers to a helper that intentionally does not exist in production.
The patch must not be applied without a new design decision: its cache identity
revision would cause lazy regeneration and is not justified for the current
warm-cache issue. See `../BROWSER_WARM_STARTUP_INVESTIGATION.md`.
