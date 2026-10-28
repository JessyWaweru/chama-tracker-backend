import re

_KENYAN_MOBILE = re.compile(r"^254[17]\d{8}$")


def normalise_phone(raw: str) -> str:
    """Normalise a Kenyan mobile number to 2547XXXXXXXX / 2541XXXXXXXX.

    Accepts 07.., 01.., 7.., 1.., +254.., 254.. with spaces or dashes.
    Raises ValueError for anything else.
    """
    digits = re.sub(r"[\s\-()]", "", str(raw or ""))
    if digits.startswith("+"):
        digits = digits[1:]
    if digits.startswith("0") and len(digits) == 10:
        digits = "254" + digits[1:]
    elif len(digits) == 9 and digits[0] in "17":
        digits = "254" + digits
    if not _KENYAN_MOBILE.match(digits):
        raise ValueError(f"{raw!r} is not a valid Kenyan mobile number")
    return digits
