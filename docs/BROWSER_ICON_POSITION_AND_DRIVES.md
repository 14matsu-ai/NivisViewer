# Browser icon position and drive selection

The Browser settings expose `browser_badge_icon_left_margin` and
`browser_badge_icon_bottom_margin` for the lower-left file/folder type icon.
Each defaults to `-1` (Auto), preserving the visible position of the original
medium-size icon. Explicit values from 0 to 128 are logical-pixel distances
from the thumbnail frame edge to the visible icon, not its transparent image
boundary. An excessive distance is limited to keep the icon within the frame.
The two axes can independently use Auto. Browser reset restores both to Auto.

Changing either setting only repaints the delegate. It does not change the
thumbnail frame, central placeholder icon, image crop mode, thumbnail cache or
scan generation. The existing bounded association/size inset caches avoid
repeated alpha inspection on repaint. Tag layout reserves the relocated badge
rectangle. Nearly transparent shadow pixels are excluded from the anchor.

The first breadcrumb chevron opens a drive list using the existing location
list popup. It remains available when ancestors are collapsed into an ellipsis.
Drive roots are enumerated on opening, without volume-label, capacity or
readiness queries. Selection uses the existing `locationActivated` navigation
path, including its normal unavailable-path handling. The current drive is
highlighted; Escape closes the list. Location changes, breadcrumb rebuilds and
hiding the bar close the popup. UNC paths retain their normal breadcrumb and
can also use the drive selector.
