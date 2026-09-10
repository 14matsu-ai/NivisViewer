# Qt 6.11.2 open-source license supplement

The installed PySide6 / PySide6_Essentials / PySide6_Addons / shiboken6
distributions declare `LGPL-3.0-only OR GPL-2.0-only OR GPL-3.0-only` but ship
only a commercial reference text in their wheel license folders. The three
unmodified open-source texts here are from the official PySide source tag
`v6.11.2`; see SOURCES.json for URLs and SHA-256. This is not a grant of a
commercial license and not a claim that all Qt modules are LGPL.

The intended open-source route is LGPLv3 for the PySide/shiboken libraries and
eligible Qt modules; the inspected bundle's Qt Virtual Keyboard requires its
GPLv3 terms. Qt PDF has an LGPLv3 alternative and its own third-party/PDFium
tree, distinct from pypdfium2's PDFium build. The other GPL texts preserve the
published alternatives; no GPLv2-only combination with AGPL is being selected.

Examples of retained module attribution (not an exhaustive copyright list):

- Qt Core `src/corelib/global/qglobal.h`: Copyright (C) 2020 The Qt Company Ltd.;
  Copyright (C) 2019 Intel Corporation.
- Qt Virtual Keyboard `src/virtualkeyboard/qvirtualkeyboardinputcontext.cpp`:
  Copyright (C) 2016 The Qt Company Ltd.
- PySide runtime `sources/pyside6/libpyside/pyside.cpp`:
  Copyright (C) 2016 The Qt Company Ltd.

Official fixed sources:

- https://github.com/qt/qtbase/blob/v6.11.2/src/corelib/global/qglobal.h
- https://github.com/qt/qtvirtualkeyboard/blob/v6.11.2/src/virtualkeyboard/qvirtualkeyboardinputcontext.cpp
- https://github.com/pyside/pyside-setup/tree/v6.11.2/LICENSES
- https://github.com/pyside/pyside-setup/blob/v6.11.2/sources/pyside6/libpyside/pyside.cpp

The full component-specific attributions, exact corresponding source/build
configuration, and LGPL replacement/relinking verification remain release
blockers. This folder is deliberately not labelled a complete Qt notice set.
See docs/RELEASE_LICENSE_AUDIT.md in the NivisViewer source tree.
