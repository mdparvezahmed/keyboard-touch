"""A tiny TOML reader for the subset wkm.toml actually uses.

Python 3.11 added ``tomllib``, but macOS still ships 3.9 with the command line
tools. Requiring 3.11 would mean telling people to install Homebrew and a
second Python before they can use any of this, which is a lot to ask for one
config file of flat ``key = value`` lines. So: use ``tomllib`` when it exists,
fall back to this when it does not.

Deliberately not a TOML implementation. It handles flat key/value pairs with
strings, integers, floats and booleans, and rejects anything else rather than
guessing -- a config that silently half-parses is worse than one that refuses.
"""

from __future__ import annotations


class TOMLDecodeError(ValueError):
    pass


_ESCAPES = {
    '"': '"', "\\": "\\", "n": "\n", "t": "\t", "r": "\r",
    "b": "\b", "f": "\f", "0": "\0",
}


def _parse_basic_string(text: str, lineno: int) -> tuple[str, str]:
    """Read a "..." string from the front of text; return (value, rest)."""
    out = []
    i = 1
    while i < len(text):
        ch = text[i]
        if ch == "\\":
            i += 1
            if i >= len(text):
                break
            esc = text[i]
            if esc == "u" and i + 4 < len(text):
                try:
                    out.append(chr(int(text[i + 1 : i + 5], 16)))
                    i += 5
                    continue
                except ValueError:
                    raise TOMLDecodeError("bad \\u escape on line " + str(lineno))
            if esc not in _ESCAPES:
                raise TOMLDecodeError(
                    "unsupported escape \\" + esc + " on line " + str(lineno)
                )
            out.append(_ESCAPES[esc])
            i += 1
            continue
        if ch == '"':
            return ("".join(out), text[i + 1 :])
        out.append(ch)
        i += 1
    raise TOMLDecodeError("unterminated string on line " + str(lineno))


def _parse_value(text: str, lineno: int):
    text = text.strip()
    if not text:
        raise TOMLDecodeError("missing value on line " + str(lineno))

    if text[0] == '"':
        value, rest = _parse_basic_string(text, lineno)
    elif text[0] == "'":
        end = text.find("'", 1)
        if end == -1:
            raise TOMLDecodeError("unterminated string on line " + str(lineno))
        value, rest = text[1:end], text[end + 1 :]
    else:
        # Bare value: it ends at whitespace or a comment.
        cut = len(text)
        hash_at = text.find("#")
        if hash_at != -1:
            cut = hash_at
        token = text[:cut].strip()
        rest = text[cut:]
        if token in ("true", "false"):
            value = token == "true"
        else:
            try:
                value = int(token, 10)
            except ValueError:
                try:
                    value = float(token)
                except ValueError:
                    raise TOMLDecodeError(
                        "cannot read value " + repr(token) + " on line " + str(lineno)
                        + ". Strings need quotes around them."
                    )

    rest = rest.strip()
    if rest and not rest.startswith("#"):
        raise TOMLDecodeError("trailing text after value on line " + str(lineno))
    return value


def loads(text: str) -> dict:
    data: dict = {}
    for lineno, raw in enumerate(text.splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("["):
            raise TOMLDecodeError(
                "line " + str(lineno) + ": wkm.toml has no [sections]; "
                "every setting is a plain 'key = value' line"
            )
        key, sep, rest = line.partition("=")
        if not sep:
            raise TOMLDecodeError("line " + str(lineno) + ": expected 'key = value'")
        key = key.strip()
        if not key:
            raise TOMLDecodeError("line " + str(lineno) + ": missing key name")
        data[key] = _parse_value(rest, lineno)
    return data


def load(fp) -> dict:
    """Read from a binary file object, matching the tomllib signature."""
    raw = fp.read()
    if isinstance(raw, bytes):
        try:
            raw = raw.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise TOMLDecodeError("config file is not valid UTF-8") from exc
    return loads(raw)
