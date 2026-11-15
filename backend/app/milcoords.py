"""Military coordinate notations on WGS 84: decimal degrees (DD), degrees and decimal minutes (DDM), degrees, minutes
and seconds (DMS), UTM, MGRS, USNG, GARS and GEOREF. Each can be written from a longitude and latitude and read
back, and `parse` recognises whichever notation a piece of text is in.

MGRS uses the current (AA) lettering on WGS 84, with the Norway and Svalbard zone exceptions; it covers latitudes
80° S to 84° N (the polar UPS areas are not handled). USNG is MGRS written with spaces."""
import math
import re
from functools import lru_cache

from pyproj import Transformer

BANDS = "CDEFGHJKLMNPQRSTUVWX"                 # latitude bands of 8° from 80° S (X is 12°, to 84° N)
COLS = ("ABCDEFGH", "JKLMNPQR", "STUVWXYZ")    # 100 km column letters, by zone % 3 (1, 2, 0)
ROWS = "ABCDEFGHJKLMNPQRSTUV"                  # 100 km row letters, repeating every 2,000 km
GARS_LAT = "ABCDEFGHJKLMNPQRSTUVWXYZ"          # 24 letters, no I or O
GEOREF_LON = "ABCDEFGHJKLMNPQRSTUVWXYZ"        # 15° columns from 180° W
GEOREF_LAT = "ABCDEFGHJKLM"                    # 15° rows from 90° S
GEOREF_DEG = "ABCDEFGHJKLMNPQ"                 # 1° within a 15° quadrangle
NOTATIONS = ("dd", "ddm", "dms", "utm", "mgrs", "usng", "gars", "georef")
NAMES = {"dd": "Decimal degrees", "ddm": "Degrees decimal minutes", "dms": "Degrees minutes seconds", "utm": "UTM",
         "mgrs": "MGRS", "usng": "USNG", "gars": "GARS", "georef": "GEOREF"}


class CoordError(ValueError):
    """Text that is not a coordinate, or a position the notation does not cover."""


# ---------- UTM ----------
@lru_cache(maxsize=128)
def _utm(zone: int, south: bool) -> tuple[Transformer, Transformer]:
    epsg = (32700 if south else 32600) + zone
    return (Transformer.from_crs(4326, epsg, always_xy=True), Transformer.from_crs(epsg, 4326, always_xy=True))


def utm_zone(lng: float, lat: float) -> int:
    """The UTM zone, with the Norway (32V) and Svalbard (31X–37X) exceptions MGRS uses."""
    lng = ((lng + 180) % 360) - 180
    z = min(60, int((lng + 180) // 6) + 1)
    if 56 <= lat < 64 and 3 <= lng < 12:
        return 32
    if 72 <= lat <= 84 and lng >= 0:
        if lng < 9:
            return 31
        if lng < 21:
            return 33
        if lng < 33:
            return 35
        if lng < 42:
            return 37
    return z


def band_letter(lat: float) -> str:
    if not -80 <= lat <= 84:
        raise CoordError("MGRS and UTM bands cover 80° S to 84° N only.")
    return BANDS[min(19, int((lat + 80) // 8))]


def to_utm_xy(lng: float, lat: float, zone: int | None = None) -> tuple[int, bool, float, float]:
    zone = zone or utm_zone(lng, lat)
    south = lat < 0
    e, n = _utm(zone, south)[0].transform(lng, lat)
    return zone, south, e, n


def to_utm(lng: float, lat: float) -> str:
    """'32N 331529 1003546' (zone, hemisphere, easting, northing in metres)."""
    if not -80 <= lat <= 84:
        raise CoordError("UTM covers 80° S to 84° N only.")
    zone, south, e, n = to_utm_xy(lng, lat)
    return f"{zone}{'S' if south else 'N'} {int(round(e))} {int(round(n))}"


def from_utm(zone: int, south: bool, e: float, n: float) -> tuple[float, float]:
    if not 1 <= zone <= 60:
        raise CoordError("A UTM zone is 1 to 60.")
    if not 100_000 <= e <= 900_000 or not 0 <= n <= 10_000_000:
        raise CoordError("That easting or northing is outside a UTM zone.")
    lng, lat = _utm(zone, south)[1].transform(e, n)
    return lng, lat


# ---------- MGRS / USNG ----------
def to_mgrs(lng: float, lat: float, digits: int = 5, spaced: bool = False) -> str:
    """'32PLR3145203665' (digits per axis: 5 = 1 m, 4 = 10 m … 0 = the 100 km square); spaced: '32P LR 31452 03665'."""
    if not 0 <= digits <= 5:
        raise CoordError("MGRS precision is 0 to 5 digits.")
    band = band_letter(lat)
    zone, _, e, n = to_utm_xy(lng, lat)
    col = COLS[(zone - 1) % 3][int(e // 100_000) - 1]
    row = ROWS[(int(n // 100_000) + (5 if zone % 2 == 0 else 0)) % 20]
    div = 10 ** (5 - digits)
    ee = f"{int(e % 100_000) // div:0{digits}d}" if digits else ""
    nn = f"{int(n % 100_000) // div:0{digits}d}" if digits else ""
    if spaced:
        return " ".join(x for x in (f"{zone}{band}", col + row, ee, nn) if x)
    return f"{zone}{band}{col}{row}{ee}{nn}"


def to_usng(lng: float, lat: float, digits: int = 5) -> str:
    return to_mgrs(lng, lat, digits, spaced=True)


MGRS_RE = re.compile(r"^\s*(\d{1,2})\s*([C-HJ-NP-X])\s*([A-HJ-NP-Z])\s*([A-HJ-NP-V])\s*(\d*)\s*(\d*)\s*$", re.I)


def mgrs_cell(text: str) -> tuple[float, float, float, int, int, bool]:
    """The south-west corner (easting, northing in the zone), the cell size in metres, the zone, and the band."""
    m = MGRS_RE.match(text)
    if not m:
        raise CoordError("Not an MGRS reference (like 32P LR 31452 03665).")
    zone, band, col, row = int(m[1]), m[2].upper(), m[3].upper(), m[4].upper()
    digits = m[5] + m[6]
    if not 1 <= zone <= 60:
        raise CoordError("An MGRS zone is 1 to 60.")
    if len(digits) % 2 or len(digits) > 10:
        raise CoordError("An MGRS reference has as many easting digits as northing digits (up to 5 each).")
    k = len(digits) // 2
    cols = COLS[(zone - 1) % 3]
    if col not in cols:
        raise CoordError(f"{col} is not a 100 km column letter in zone {zone}.")
    e100 = (cols.index(col) + 1) * 100_000
    r = (ROWS.index(row) - (5 if zone % 2 == 0 else 0)) % 20
    size = 10 ** (5 - k)
    e = e100 + (int(digits[:k]) * size if k else 0)
    n_in = r * 100_000 + (int(digits[k:]) * size if k else 0)
    # the band says which 2,000 km cycle the row letters are in
    bi = BANDS.index(band)
    south = bi < 10
    bottom = -80 + bi * 8
    tr = _utm(zone, south)[0]
    cm = zone * 6 - 183
    lo = min(tr.transform(cm, bottom)[1], tr.transform(cm + 3, bottom)[1], tr.transform(cm - 3, bottom)[1])
    n = n_in
    while n < lo - 100_000:
        n += 2_000_000
    return e, n, size, zone, bi, south


def from_mgrs(text: str) -> tuple[float, float]:
    """The longitude and latitude of an MGRS (or USNG) reference: the centre of the square it names."""
    e, n, size, zone, bi, south = mgrs_cell(text)
    lng, lat = _utm(zone, south)[1].transform(e + size / 2, n + size / 2)
    top = -80 + bi * 8 + (12 if bi == 19 else 8)
    if not (-80 + bi * 8 - 0.5 <= lat <= top + 0.5):
        raise CoordError(f"{text.strip()} does not fall in latitude band {BANDS[bi]}.")
    return lng, lat


# ---------- degrees ----------
def _hemi(v: float, pos: str, neg: str) -> str:
    return pos if v >= 0 else neg


def to_dd(lng: float, lat: float, places: int = 6) -> str:
    """'9.076500° N 7.466400° E'."""
    return f"{abs(lat):.{places}f}° {_hemi(lat, 'N', 'S')} {abs(lng):.{places}f}° {_hemi(lng, 'E', 'W')}"


def _ddm(v: float, width: int, places: int) -> str:
    a = abs(v)
    d = int(a)
    m = round((a - d) * 60, places)
    if m >= 60:
        d, m = d + 1, 0.0
    return f"{d:0{width}d}° {m:0{places + 3}.{places}f}'"


def to_ddm(lng: float, lat: float, places: int = 3) -> str:
    """'09° 04.590' N 007° 27.984' E'."""
    return f"{_ddm(lat, 2, places)} {_hemi(lat, 'N', 'S')} {_ddm(lng, 3, places)} {_hemi(lng, 'E', 'W')}"


def _dms(v: float, width: int, places: int) -> str:
    a = abs(v)
    d = int(a)
    m = int((a - d) * 60)
    s = round(((a - d) * 60 - m) * 60, places)
    if s >= 60:
        s, m = 0.0, m + 1
    if m >= 60:
        m, d = 0, d + 1
    return f"{d:0{width}d}° {m:02d}' {s:0{places + 3 if places else 2}.{places}f}\""


def to_dms(lng: float, lat: float, places: int = 1) -> str:
    """'09° 04' 35.4" N 007° 27' 59.0" E'."""
    return f"{_dms(lat, 2, places)} {_hemi(lat, 'N', 'S')} {_dms(lng, 3, places)} {_hemi(lng, 'E', 'W')}"


# ---------- GARS ----------
def to_gars(lng: float, lat: float, level: int = 5) -> str:
    """'375LS28' — a 30' cell (number and letters), then a 15' quadrant (1–4) and a 5' keypad square (1–9)."""
    if not -90 <= lat <= 90:
        raise CoordError("Latitude is −90 to 90.")
    lng = ((lng + 180) % 360) - 180
    x = min(int((lng + 180) * 12), 4319)      # 5' columns from 180° W
    y = min(int((lat + 90) * 12), 2159)       # 5' rows from 90° S
    cx, cy = x // 6, y // 6
    out = f"{cx + 1:03d}{GARS_LAT[cy // 24]}{GARS_LAT[cy % 24]}"
    if level >= 30:
        return out
    qx, qy = (x % 6) // 3, (y % 6) // 3                       # 0 west / east, 0 south / north
    out += str({(0, 1): 1, (1, 1): 2, (0, 0): 3, (1, 0): 4}[(qx, qy)])
    if level >= 15:
        return out
    kx, ky = x % 3, 2 - (y % 3)                               # keypad: 1 2 3 on top
    return out + str(ky * 3 + kx + 1)


GARS_RE = re.compile(r"^\s*(\d{3})\s*([A-HJ-NP-Z])([A-HJ-NP-Z])\s*([1-4])?\s*([1-9])?\s*$", re.I)


def gars_cell(text: str) -> tuple[float, float, float]:
    """South-west corner (lng, lat) and size in degrees."""
    m = GARS_RE.match(text)
    if not m or not 1 <= int(m[1]) <= 720:
        raise CoordError("Not a GARS cell (like 375LS28).")
    cy = GARS_LAT.index(m[2].upper()) * 24 + GARS_LAT.index(m[3].upper())
    if cy >= 360:
        raise CoordError("Those GARS letters are past the North Pole.")
    w, s, size = -180 + (int(m[1]) - 1) * 0.5, -90 + cy * 0.5, 0.5
    if m[4]:
        q = int(m[4])
        w += 0.25 if q in (2, 4) else 0
        s += 0.25 if q in (1, 2) else 0
        size = 0.25
        if m[5]:
            k = int(m[5]) - 1
            w += (k % 3) * size / 3
            s += (2 - k // 3) * size / 3
            size /= 3
    return w, s, size


def from_gars(text: str) -> tuple[float, float]:
    w, s, size = gars_cell(text)
    return w + size / 2, s + size / 2


# ---------- GEOREF ----------
def to_georef(lng: float, lat: float, digits: int = 2) -> str:
    """'MKML5004': 15° quadrangle, 1° square, then minutes east and north (digits each: 2 = 1', 3 = 0.1', 4 = 0.01')."""
    if not -90 <= lat <= 90:
        raise CoordError("Latitude is −90 to 90.")
    lng = ((lng + 180) % 360) - 180
    x, y = min(lng + 180, 359.999999999), min(lat + 90, 179.999999999)
    out = GEOREF_LON[int(x // 15)] + GEOREF_LAT[int(y // 15)] + GEOREF_DEG[int(x % 15)] + GEOREF_DEG[int(y % 15)]
    if digits:
        f = 10 ** (digits - 2)
        mx = int((x % 1) * 60 * f)
        my = int((y % 1) * 60 * f)
        out += f"{mx:0{digits}d}{my:0{digits}d}"
    return out


GEOREF_RE = re.compile(r"^\s*([A-HJ-NP-Z])([A-HJ-M])([A-HJ-NP-Q])([A-HJ-NP-Q])\s*(\d*)\s*$", re.I)


def from_georef(text: str) -> tuple[float, float]:
    m = GEOREF_RE.match(text)
    if not m or len(m[5]) % 2 or len(m[5]) > 10:
        raise CoordError("Not a GEOREF reference (like MKML5004).")
    w = -180 + GEOREF_LON.index(m[1].upper()) * 15 + GEOREF_DEG.index(m[3].upper())
    s = -90 + GEOREF_LAT.index(m[2].upper()) * 15 + GEOREF_DEG.index(m[4].upper())
    d = m[5]
    if not d:
        return w + 0.5, s + 0.5
    k = len(d) // 2
    unit = 10 ** -(k - 2) / 60                                   # degrees per last digit
    mx, my = int(d[:k]) * unit, int(d[k:]) * unit
    if mx >= 1 or my >= 1:
        raise CoordError("GEOREF minutes go up to 59.")
    return w + mx + unit / 2, s + my + unit / 2


# ---------- reading any of them ----------
NUM = r"[-+]?\d+(?:[.,]\d+)?"
_PART = re.compile(r"""^\s*(?P<h1>[NSEW])?\s*(?P<d>[-+]?\d+(?:\.\d+)?)\s*(?:[°º:d]|\s|deg)\s*
                       (?:(?P<m>\d+(?:\.\d+)?)\s*(?:['′’:m]|\s)\s*)?(?:(?P<s>\d+(?:\.\d+)?)\s*(?:["″”]|''|s)?\s*)?(?P<h2>[NSEW])?\s*$""",
                   re.I | re.X)


def _angle(part: str) -> tuple[float, str | None]:
    """One latitude or longitude written in degrees (with minutes and seconds, a sign or a hemisphere letter)."""
    p = part.strip()
    m = re.fullmatch(r"([NSEW])?\s*([-+]?\d+(?:\.\d+)?)\s*°?\s*([NSEW])?", p, re.I)
    if m:
        v, h = float(m[2]), (m[1] or m[3] or "").upper() or None
    else:
        m = _PART.match(p)
        if not m:
            raise CoordError(f"Can't read {part.strip()!r} as degrees.")
        d, mi, s = float(m["d"]), float(m["m"] or 0), float(m["s"] or 0)
        if mi >= 60 or s >= 60:
            raise CoordError("Minutes and seconds go up to 59.")
        v = abs(d) + mi / 60 + s / 3600
        v = -v if str(m["d"]).startswith("-") else v
        h = (m["h1"] or m["h2"] or "").upper() or None
    if h in ("S", "W"):
        v = -abs(v)
    return v, h


def _split_degrees(text: str) -> list[str]:
    t = text.strip().replace("″", '"').replace("′", "'").replace("’", "'").replace("”", '"')
    for sep in (";", ","):
        parts = t.split(sep)
        if len(parts) == 2 and all(x.strip() for x in parts):
            return parts
    # split after a hemisphere letter that ends the first part ("9°04'35"N 7°27'59"E")
    m = re.match(r"^([NSEWnsew]\s*[^NSEWnsew]+?)\s*[,;]?\s*([NSEWnsew]\s*[^NSEWnsew]+)$", t)      # "N 9 4 35 E 7 27 59"
    if m:
        return [m[1], m[2]]
    m = re.match(r"^([^NSEWnsew]+?[NSns])\s*[,;]?\s*([^NSEWnsew]+[EWew])$", t) or re.match(r"^([^NSEWnsew]+?[EWew])\s*[,;]?\s*([^NSEWnsew]+[NSns])$", t)
    if m:
        return [m[1], m[2]]
    nums = t.replace(";", " ").split()
    if len(nums) == 2:
        return nums
    raise CoordError("Write a latitude and a longitude (e.g. 9.0765, 7.4664 or 9°04'35\"N 7°27'59\"E).")


def from_degrees(text: str, order: str = "latlon") -> tuple[float, float]:
    a, b = _split_degrees(text)
    (v1, h1), (v2, h2) = _angle(a), _angle(b)
    if h1 in ("E", "W") or h2 in ("N", "S"):
        lng, lat = v1, v2
    elif h1 in ("N", "S") or h2 in ("E", "W"):
        lat, lng = v1, v2
    elif order == "lonlat" or abs(v1) > 90 >= abs(v2):
        lng, lat = v1, v2
    else:
        lat, lng = v1, v2
    if not -90 <= lat <= 90 or not -180 <= lng <= 180:
        raise CoordError("Latitude is −90 to 90 and longitude −180 to 180.")
    return lng, lat


UTM_RE = re.compile(r"^\s*(\d{1,2})\s*([NS]|[C-HJ-NP-X])?\s*[, ]\s*(\d+(?:\.\d+)?)\s*m?\s*[EN]?\s*[, ]\s*(\d+(?:\.\d+)?)\s*m?\s*[EN]?\s*$", re.I)


def from_utm_text(text: str) -> tuple[float, float]:
    """'32N 331529 1003546', '32 331529 1003546' (north), or with a band letter '32P 331529 1003546'."""
    m = UTM_RE.match(text)
    if not m:
        raise CoordError("Not a UTM coordinate (like 32N 331529 1003546).")
    h = (m[2] or "N").upper()
    south = h == "S" if h in "NS" else BANDS.index(h) < 10        # N and S are the hemisphere; other letters a band
    return from_utm(int(m[1]), south, float(m[3]), float(m[4]))


def detect(text: str) -> str:
    """Which notation the text is in."""
    t = text.strip()
    if MGRS_RE.match(t) and re.match(r"^\s*\d{1,2}\s*[A-Z]\s*[A-Z]{2}", t, re.I):
        return "usng" if re.search(r"\s", t.strip()) else "mgrs"
    if UTM_RE.match(t) and float(UTM_RE.match(t)[3]) >= 100_000:
        return "utm"
    if GARS_RE.match(t):
        return "gars"
    if GEOREF_RE.match(t):
        return "georef"
    if re.search(r"['′’\"″]|\d\s*[°º]\s*\d", t):
        return "dms" if re.search(r"[\"″]|''", t) else "ddm"
    return "dd"


def parse(text: str, notation: str = "auto", order: str = "latlon") -> tuple[float, float, str]:
    """(longitude, latitude, the notation it was in)."""
    if not text or not text.strip():
        raise CoordError("No coordinate given.")
    n = detect(text) if notation in ("auto", "", None) else notation
    if n in ("mgrs", "usng"):
        lng, lat = from_mgrs(text)
    elif n == "utm":
        lng, lat = from_utm_text(text)
    elif n == "gars":
        lng, lat = from_gars(text)
    elif n == "georef":
        lng, lat = from_georef(text)
    elif n in ("dd", "ddm", "dms"):
        lng, lat = from_degrees(text, order)
    else:
        raise CoordError(f"Unknown notation {n}.")
    return lng, lat, n


def write(notation: str, lng: float, lat: float) -> str | None:
    """The position in a notation, or None where the notation does not reach (MGRS and UTM near the poles)."""
    try:
        return {"dd": to_dd, "ddm": to_ddm, "dms": to_dms, "utm": to_utm, "mgrs": to_mgrs, "usng": to_usng,
                "gars": to_gars, "georef": to_georef}[notation](lng, lat)
    except CoordError:
        return None


def all_notations(lng: float, lat: float) -> dict[str, str | None]:
    return {n: write(n, lng, lat) for n in NOTATIONS}


# ---------- grid zone designations ----------
def gzd_bounds(zone: int, bi: int) -> tuple[float, float, float, float] | None:
    """The longitude/latitude rectangle of a grid zone (e.g. 32V is widened, 32X does not exist)."""
    s = -80 + bi * 8
    n = s + (12 if bi == 19 else 8)
    w = -180 + (zone - 1) * 6
    e = w + 6
    band = BANDS[bi]
    if band == "V" and zone == 31:
        e = 3
    if band == "V" and zone == 32:
        w = 3
    if band == "X":
        if zone in (32, 34, 36):
            return None
        if zone == 31:
            e = 9
        elif zone == 33:
            w, e = 9, 21
        elif zone == 35:
            w, e = 21, 33
        elif zone == 37:
            w, e = 33, 42
    return w, s, e, n


def gzds_in(w: float, s: float, e: float, n: float):
    """The grid zones touching a rectangle, with their (clipped) rectangles."""
    for bi in range(20):
        bs, bn = -80 + bi * 8, -80 + bi * 8 + (12 if bi == 19 else 8)
        if bn <= s or bs >= n:
            continue
        for zone in range(1, 61):
            b = gzd_bounds(zone, bi)
            if not b or b[2] <= w or b[0] >= e:
                continue
            yield zone, bi, b, (max(b[0], w), max(b[1], s), min(b[2], e), min(b[3], n))


def math_ok(v: float) -> bool:
    return isinstance(v, (int, float)) and math.isfinite(v)
