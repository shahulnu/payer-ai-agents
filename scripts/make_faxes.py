"""Generate the synthetic prior authorization faxes as image-only PDFs.

  uv run python scripts/make_faxes.py [--site ../ai-portfolio]

Each fax is drawn as page images with fax-like noise (skew, speckle, a transmission header, a
stamp), so the PDFs have no text layer and must be read from the pixels. Fonts come from Windows
(C:/Windows/Fonts); the generated files are committed, so this only reruns when the faxes change.
With --site, small PNG previews are also written to the site's public/fax-intake folder."""

from __future__ import annotations

import argparse
import json
import random
from datetime import datetime
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data" / "fax-intake"
FONTS = Path("C:/Windows/Fonts")
W, H, DPI = 1700, 2200, 200


def font(name: str, size: int):
    return ImageFont.truetype(str(FONTS / name), size)


SANS, BOLD, MONO, HAND = "arial.ttf", "arialbd.ttf", "cour.ttf", "segoesc.ttf"


def header(d: ImageDraw.ImageDraw, fax: dict, page: int, pages: int) -> None:
    t = datetime.fromisoformat(fax["receivedAt"].replace("Z", "+00:00"))
    text = f"{t:%m/%d/%Y %H:%M}   FROM: {fax['fromFax']}   {fax['sender'].upper()}   TO: UM INTAKE   P. {page}/{pages}"
    d.text((70, 28), text, font=font(MONO, 26), fill=40)


def field(d, x, y, label, value, w=700, value_font=None):
    d.text((x, y), label, font=font(SANS, 24), fill=70)
    d.line((x, y + 78, x + w, y + 78), fill=120, width=2)
    if value:
        d.text((x + 6, y + 34), value, font=value_font or font(SANS, 36), fill=15)


def page_one(fax: dict) -> Image.Image:
    f = fax["form"]
    img = Image.new("L", (W, H), 250)
    d = ImageDraw.Draw(img)
    header(d, fax, 1, 2)
    d.text((110, 110), "PRIOR AUTHORIZATION REQUEST", font=font(BOLD, 58), fill=10)
    d.text((110, 185), "Fax to Utilization Management: (555) 900-1200   ·   Sample Health Plan", font=font(SANS, 28), fill=60)
    d.rectangle((100, 250, W - 100, 254), fill=30)

    d.text((110, 290), "MEMBER", font=font(BOLD, 32), fill=20)
    field(d, 110, 340, "Member name", f["member_name"], 900)
    field(d, 1060, 340, "Member ID", f["member_id"], 520)
    field(d, 110, 460, "Date of birth", f["member_dob"], 520)

    d.text((110, 600), "REQUESTING PROVIDER", font=font(BOLD, 32), fill=20)
    field(d, 110, 650, "Provider name", f["provider_name"], 900)
    field(d, 1060, 650, "NPI", f["provider_npi"], 520)
    field(d, 110, 770, "Practice", fax["sender"], 900)

    d.text((110, 910), "SERVICE REQUESTED", font=font(BOLD, 32), fill=20)
    field(d, 110, 960, "CPT / HCPCS code", f["service_code"], 420)
    field(d, 600, 960, "Description", f["service_description"], 980)
    field(d, 110, 1080, "Diagnosis code(s), ICD-10", f["diagnosis_codes"], 900)
    field(d, 1060, 1080, "Date of service", f["date_of_service"], 520)

    d.text((110, 1220), "Urgency:", font=font(SANS, 30), fill=30)
    for i, label in enumerate(["Standard", "Expedited"]):
        x = 300 + i * 320
        d.rectangle((x, 1222, x + 34, 1256), outline=30, width=3)
        if f["urgency"] == label:
            d.line((x + 6, 1238, x + 15, 1250), fill=10, width=5)
            d.line((x + 15, 1250, x + 32, 1222), fill=10, width=5)
        d.text((x + 50, 1220), label, font=font(SANS, 30), fill=30)
    d.text((110, 1290), "Expedited means waiting the standard time could seriously jeopardize the member's life or health.", font=font(SANS, 22), fill=90)

    d.text((110, 1400), "ATTESTATION", font=font(BOLD, 32), fill=20)
    d.text((110, 1450), "I attest that the information in this request and the attached records is accurate and complete.", font=font(SANS, 26), fill=40)
    d.line((110, 1600, 900, 1600), fill=60, width=2)
    d.text((110, 1610), "Signature of requesting provider", font=font(SANS, 24), fill=70)
    if f["signed"]:
        d.text((150, 1520), f["provider_name"].split(",")[0], font=font(HAND, 64), fill=25)
        d.text((1060, 1545), datetime.fromisoformat(fax["receivedAt"].replace("Z", "+00:00")).strftime("%m/%d/%Y"), font=font(SANS, 36), fill=15)
    d.line((1060, 1600, 1580, 1600), fill=60, width=2)
    d.text((1060, 1610), "Date signed", font=font(SANS, 24), fill=70)
    d.text((110, 1700), "Attach clinical notes supporting medical necessity. Incomplete requests may be returned.", font=font(SANS, 24), fill=90)

    if fax["smudgeNpi"]:
        rnd = random.Random(fax["id"])
        for _ in range(26):
            cx, cy = 1180 + rnd.randint(0, 260), 712 + rnd.randint(-10, 14)
            r = rnd.randint(16, 34)
            d.ellipse((cx - r, cy - r * 0.7, cx + r, cy + r * 0.7), fill=rnd.randint(55, 95))
    return img


def page_two(fax: dict) -> Image.Image:
    img = Image.new("L", (W, H), 250)
    d = ImageDraw.Draw(img)
    header(d, fax, 2, 2)
    d.text((110, 110), fax["sender"], font=font(BOLD, 44), fill=15)
    d.text((110, 170), "CLINICAL NOTE", font=font(BOLD, 34), fill=30)
    f = fax["form"]
    d.text((110, 240), f"Patient: {f['member_name']}    DOB: {f['member_dob']}    Member ID: {f['member_id']}", font=font(SANS, 30), fill=25)
    d.text((110, 290), f"Provider: {f['provider_name']}", font=font(SANS, 30), fill=25)
    d.rectangle((100, 350, W - 100, 353), fill=40)
    y = 400
    for line in fax["note"]:
        d.text((110, y), line, font=font(SANS, 32), fill=20)
        y += 70
    d.text((110, y + 40), f"Plan: {f['service_description']} ({f['service_code']}).", font=font(SANS, 32), fill=20)
    if fax["footer"]:
        d.text((110, H - 190), fax["footer"], font=font(SANS, 22), fill=60)
    return img


def stamp(img: Image.Image, fax: dict) -> None:
    t = datetime.fromisoformat(fax["receivedAt"].replace("Z", "+00:00"))
    s = Image.new("L", (520, 150), 0)
    d = ImageDraw.Draw(s)
    d.rectangle((4, 4, 515, 145), outline=255, width=6)
    d.text((40, 22), "RECEIVED", font=font(BOLD, 52), fill=255)
    d.text((40, 88), f"{t:%b %d %Y}".upper() + "  UM INTAKE", font=font(BOLD, 32), fill=255)
    s = s.rotate(8, expand=True, fillcolor=0)
    img.paste(110, (W - 640, 90), s.point(lambda v: int(v * 0.55)))


def scan(img: Image.Image, seed: str) -> Image.Image:
    """Make a clean page look faxed: slight skew, blur, speckle and uneven contrast."""
    rnd = random.Random(seed)
    img = img.rotate(rnd.uniform(-0.8, 0.8), resample=Image.BICUBIC, expand=False, fillcolor=250)
    img = img.filter(ImageFilter.GaussianBlur(0.7))
    px = img.load()
    for _ in range(9000):
        x, y = rnd.randrange(W), rnd.randrange(H)
        px[x, y] = rnd.choice([40, 90, 130])
    return img.point(lambda v: 20 if v < 110 else (248 if v > 200 else v))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--site", default=None, help="Also write PNG previews into this site folder.")
    args = ap.parse_args()
    faxes = json.loads((DATA / "faxes.json").read_text(encoding="utf-8"))
    out = DATA / "faxes"
    out.mkdir(parents=True, exist_ok=True)
    for fax in faxes:
        p1, p2 = page_one(fax), page_two(fax)
        stamp(p1, fax)
        pages = [scan(p1, fax["id"] + "a"), scan(p2, fax["id"] + "b")]
        pages[0].save(out / f"{fax['id']}.pdf", save_all=True, append_images=pages[1:], resolution=DPI)
        print(f"Wrote {out / (fax['id'] + '.pdf')}")
        if args.site:
            prev = Path(args.site) / "public" / "fax-intake"
            prev.mkdir(parents=True, exist_ok=True)
            for i, p in enumerate(pages, 1):
                p.resize((850, 1100), Image.LANCZOS).save(prev / f"{fax['id']}-p{i}.png", optimize=True)
            print(f"Wrote previews to {prev}")


if __name__ == "__main__":
    main()
