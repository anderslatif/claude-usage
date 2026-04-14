from AppKit import (
    NSImage, NSColor, NSBezierPath, NSFont,
    NSMutableAttributedString, NSAttributedString,
    NSTextAttachment, NSFontAttributeName,
    NSForegroundColorAttributeName,
    NSGraphicsContext,
)
from Foundation import NSMakeRect, NSOperationQueue
from CoreText import (
    CTLineCreateWithAttributedString,
    CTLineDraw,
    CTLineGetTypographicBounds,
)
from Quartz.CoreGraphics import (
    CGContextSetTextMatrix,
    CGContextSetTextPosition,
    CGAffineTransformIdentity,
)

_BAT_W        = 62
_BAT_H        = 18
_BAT_BASELINE = -5  # vertical nudge to align battery with menu bar text baseline

def battery_image(fraction: float | None, label: str) -> NSImage:
    """
    fraction - 0.0-1.0 fill level (amount used); None = unknown (grey outline only)
    label    - time-remaining string drawn centred inside the battery body
    """
    image = NSImage.alloc().initWithSize_((_BAT_W, _BAT_H))
    image.lockFocus()

    pad       = 1
    bump_w    = 4
    bump_h    = int(_BAT_H * 0.4)
    body_w    = _BAT_W - bump_w - pad * 2
    body_h    = _BAT_H - pad * 2
    bx, by    = float(pad), float(pad)
    r         = 3.0
    inner_pad = 2

    NSColor.clearColor().set()
    NSBezierPath.fillRect_(NSMakeRect(0, 0, _BAT_W, _BAT_H))

    fill_frac = fraction if fraction is not None else 0.0
    fill_w = max(0.0, (body_w - inner_pad * 2) * min(fill_frac, 1.0))
    if fill_w > 0:
        fill_color = NSColor.systemRedColor() if fraction is not None and fraction >= 1.0 else NSColor.whiteColor().colorWithAlphaComponent_(0.6)
        fill_color.setFill()
        NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(
            NSMakeRect(bx + inner_pad, by + inner_pad, fill_w, body_h - inner_pad * 2),
            max(1.0, r - 1), max(1.0, r - 1),
        ).fill()

    outline_color = NSColor.systemOrangeColor()

    outline_path = NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(
        NSMakeRect(bx, by, float(body_w), float(body_h)), r, r
    )
    outline_path.setLineWidth_(1.5)
    outline_color.setStroke()
    outline_path.stroke()

    outline_color.setFill()
    NSBezierPath.fillRect_(NSMakeRect(
        bx + body_w, by + (body_h - bump_h) / 2, float(bump_w), float(bump_h),
    ))

    if label:
        font_size = 8.5
        font = NSFont.systemFontOfSize_(font_size)
        text_color = NSColor.systemOrangeColor()
        attrs = {NSFontAttributeName: font, NSForegroundColorAttributeName: text_color}
        attr_str = NSAttributedString.alloc().initWithString_attributes_(label, attrs)

        ct_line = CTLineCreateWithAttributedString(attr_str)
        adv_w = CTLineGetTypographicBounds(ct_line, None, None, None)
        if not isinstance(adv_w, (int, float)):
            adv_w = adv_w[0]

        cg_ctx = NSGraphicsContext.currentContext().CGContext()
        CGContextSetTextMatrix(cg_ctx, CGAffineTransformIdentity)

        ascender  = font.ascender()
        descender = font.descender()
        cap_h     = ascender - descender
        text_x = bx + inner_pad + (body_w - inner_pad * 2 - adv_w) / 2
        text_y = by + (body_h - cap_h) / 2 - descender
        CGContextSetTextPosition(cg_ctx, text_x, text_y)
        CTLineDraw(ct_line, cg_ctx)

    image.unlockFocus()
    image.setSize_((_BAT_W, _BAT_H))
    return image


def battery_attachment(fraction: float | None, label: str) -> NSAttributedString:
    attachment = NSTextAttachment.alloc().init()
    attachment.setImage_(battery_image(fraction, label))
    attachment.setBounds_(NSMakeRect(0, _BAT_BASELINE, _BAT_W, _BAT_H))
    return NSAttributedString.attributedStringWithAttachment_(attachment)