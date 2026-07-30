# NivisViewer branding sources

These source PNGs are the approved NivisViewer artwork. They are not redrawn
or synthesized during asset generation.

- `nivisviewer_logo_source.png`: high-resolution symbol source
- `nivisviewer_full_logo_source.png`: symbol and NivisViewer wordmark source

Generate the transparent runtime and Windows assets with:

```powershell
python scripts/generate_branding_assets.py
```

The generator uses fixed source coordinates and an edge-connected background
flood fill. It removes the smooth gray/blue background and original ambient
glow without changing foreground RGB pixels, geometry, or relative placement.
The thresholds and crop rectangles are declared at the top of the script.
