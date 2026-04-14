from AppKit import (
    NSColor, NSFont,
    NSMutableAttributedString,
    NSFontAttributeName, NSForegroundColorAttributeName,
)

from .draw_icon import battery_attachment


def set_bar_text(nsstatusitem, title: str) -> None:
    nsstatusitem.button().setTitle_(title)


def set_bar_batteries(
    nsstatusitem,
    session_frac: float | None, session_reset: str, session_tooltip: str,
    weekly_frac:  float | None, weekly_reset:  str, weekly_tooltip:  str,
) -> None:
    label_attrs = {
        NSFontAttributeName:            NSFont.systemFontOfSize_(11),
        NSForegroundColorAttributeName: NSColor.systemOrangeColor(),
    }

    def _label(text):
        return NSMutableAttributedString.alloc().initWithString_attributes_(text, label_attrs)

    bar = NSMutableAttributedString.alloc().initWithString_attributes_("", {})
    bar.appendAttributedString_(_label("Session "))
    bar.appendAttributedString_(battery_attachment(session_frac, session_reset))
    bar.appendAttributedString_(_label("  Weekly "))
    bar.appendAttributedString_(battery_attachment(weekly_frac, weekly_reset))

    nsstatusitem.button().setAttributedTitle_(bar)
    nsstatusitem.button().setToolTip_(
        f"Session: {session_tooltip}\nWeekly: {weekly_tooltip}"
    )
