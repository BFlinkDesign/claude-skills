#!/usr/bin/env python3
"""Translate with Context: fact checker and renderer for English to Kirundi messages.

How the work is split. Understanding text is the model's job; proving it is
this script's job.

  1. The model lists the FACTS in the English message: each date, weekday,
     clock time, and each thing that must be copied exactly (numbers, money,
     phone numbers, emails, links, codes). Each fact quotes the message.
  2. `scan` proves the list against the message: every digit, every day or
     month word, and every email address and link is inside a quoted fact,
     and each stated date and time agrees with the text it quotes.
  3. The model writes the Kirundi with numbered blanks, {1}, {2}, ... in place
     of the facts.
  4. `check` fills the blanks itself ("20 Munyonyo 2026", "15:15", the phone
     number), refuses anything typed around them that looks like a detail,
     and prints the reply to show the writer.

Commands read one JSON object on standard input and print one on standard
output.

  scan   {"source": "...", "facts": [...]}
  check  {"source": "...", "facts": [...], "template": "...", "meaning": "..."}

A fact is {"text": "<exact words from the message>", "is": "<kind>", ...}.
Each fact quotes one detail and nothing else:

  date     "November 20, 2026" -> "value": "2026-11-20"; "Nov. 20" or a bare
           "20" after it -> "11-20". A year only when the quote has one.
  month    "December" -> "value": "12"; "June 2027" -> "2027-06"
  weekday  "Friday" -> "value": "friday"
  time     "3:15 p.m." -> "value": "15:15" (24-hour). The checker works the
           24-hour time out itself and says what the value must be. With no
           a.m./p.m. in or next to the quote, the digits as written: "3:15".
  copy     a name, number, code, email address or link, kept exactly as
           written; "as" turns a number in words or with an ending into
           digits ("two" as "2", "1st" as "1")
  word     one ordinary word that only looks like a day or month ("May I ...")

"confirmed": true records that the writer answered a question about that
fact; it lets the value differ from the quote she corrected.

Exit codes: 0 = ran and nothing blocks; 2 = problems found, `repairs` says
what to change; 3 = input unusable or the script failed. Anything other than
0 means the translation has NOT passed.

Python standard library only. Reads no files, writes no files, no network.
"""

from __future__ import annotations

import bisect
import html
import os
import datetime as _dt
import difflib
import json
import re
import sys
import unicodedata
from dataclasses import dataclass, field
from typing import Callable, Optional, TextIO

VERSION = "3.6.1"

# Exit code contract: 0 = passed, 2 = anything else. Unusable input and crashes also exit 2,
# and their output has an "error" field instead of "repairs", so they can be told apart.
EXIT_OK = 0
EXIT_PROBLEMS = 2
EXIT_USAGE = 2

STATUS_PASS = (
    "Dates, times, numbers and contact details were filled in from your message by the checker. "
    "No Kirundi speaker has checked the wording."
)
STATUS_FAIL_ONE = "Do not send yet. The automatic check found 1 problem."
STATUS_FAIL_MANY = "Do not send yet. The automatic check found {count} problems."

# ---------------------------------------------------------------------------
# Reference tables
#
# Kirundi month and weekday names, from three sources read on 2026-11-14:
# the Unicode CLDR "rn" locale, the BCU Kirundi dictionary (matana.de), and
# Zorc & Nibagwire, "Kinyarwanda and Kirundi Comparative Grammar" (2007),
# table 6. The name written for each month is one that at least two of the
# three sources give. Other names the sources list are still recognised when
# they are typed by hand, so they can be refused as details outside a blank.
# ---------------------------------------------------------------------------

ENGLISH_MONTHS = [
    "January", "February", "March", "April", "May", "June", "July",
    "August", "September", "October", "November", "December",
]
ENGLISH_MONTH_ABBREVIATIONS = {
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "jun": 6, "jul": 7, "aug": 8,
    "sep": 9, "sept": 9, "oct": 10, "nov": 11, "dec": 12,
}
KIRUNDI_MONTHS = {
    1: "Nzero", 2: "Ruhuhuma", 3: "Ntwarante", 4: "Ndamukiza", 5: "Rusama",
    6: "Ruheshi", 7: "Mukakaro", 8: "Myandagaro", 9: "Nyakanga",
    10: "Gitugutu", 11: "Munyonyo", 12: "Kigarama",
}
KIRUNDI_MONTH_VARIANTS = ("nyamagoma", "kabozambuba", "kaboza", "kiyumpu", "rwirabura", "nyandagaro")

ENGLISH_WEEKDAYS = [
    "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday",
]
ENGLISH_WEEKDAY_ABBREVIATIONS = {
    "mon": 1, "tue": 2, "tues": 2, "wed": 3, "weds": 3, "thu": 4, "thur": 4, "thurs": 4,
    "fri": 5, "sat": 6, "sun": 7,
}
KIRUNDI_WEEKDAYS = {
    1: "ku wa mbere", 2: "ku wa kabiri", 3: "ku wa gatatu", 4: "ku wa kane",
    5: "ku wa gatanu", 6: "ku wa gatandatu", 7: "ku w'indwi",
}

_UNIT_WORDS = {
    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7,
    "eight": 8, "nine": 9, "ten": 10, "eleven": 11, "twelve": 12, "thirteen": 13,
    "fourteen": 14, "fifteen": 15, "sixteen": 16, "seventeen": 17, "eighteen": 18,
    "nineteen": 19,
}
_TENS_WORDS = {
    "twenty": 20, "thirty": 30, "forty": 40, "fifty": 50, "sixty": 60,
    "seventy": 70, "eighty": 80, "ninety": 90,
}
_ORDINAL_UNITS = {
    "first": 1, "second": 2, "third": 3, "fourth": 4, "fifth": 5, "sixth": 6,
    "seventh": 7, "eighth": 8, "ninth": 9, "tenth": 10, "eleventh": 11,
    "twelfth": 12, "thirteenth": 13, "fourteenth": 14, "fifteenth": 15,
    "sixteenth": 16, "seventeenth": 17, "eighteenth": 18, "nineteenth": 19,
}
_ORDINAL_TENS = {"twentieth": 20, "thirtieth": 30}

SHORTEST_WEEKDAYS = {"m": 1, "tu": 2, "w": 3, "th": 4, "f": 5, "sa": 6, "su": 7}

# Month names that are also common first names. One of these on its own may be
# kept as a copy fact ("Ask April.").
NAME_MONTHS = ("april", "may", "june", "august")

# A full stop after one of these does not end a sentence.
SENTENCE_ABBREVIATIONS = (
    "mr", "mrs", "ms", "dr", "st", "vs", "etc", "no", "ext", "approx", "jr",
    "sr", "a.m", "p.m", "e.g", "i.e",
)

# Common English words that are not Kirundi words. Kirundi words end in a
# vowel, so every consonant-final word here is safe; the vowel-final ones were
# chosen because Kirundi has no such word.
ENGLISH_MARKERS = frozenset(
    """and your will with this that has for our their from they would could
    should about when what which thank school child not was been his her is it
    of in on at an as if or by but can must all any need needs today bring
    students student parents parent teacher class call send form who how new
    out get also just than then them after next last week month year morning
    afternoon evening night children kids during until each every into does
    did keep tomorrow the you please have are were there because before these
    those here more home""".split()
)

# Kinyarwanda is a close neighbour of Kirundi and models drift into it. The
# comparative grammar's table 1 gives spelling differences that are automatic:
# Kinyarwanda cy, jy, shy, by are Kirundi c, j, sh, vy; and "to have" is -fite
# in Kinyarwanda, -fise in Kirundi.
KINYARWANDA_SPELLING = re.compile(r"(?:cy|jy|shy|by)[aeiou']")
KINYARWANDA_HAVE = re.compile(r"[a-z']*[a-z]fite")
# The future marker: Kirundi -zo- ("izoba", "tuzobonana"), Kinyarwanda -za- ("izaba",
# "tuzabonana"). Matched only before common verb stems, because "-za-" also occurs in ordinary
# Kirundi words (kuza, to come).
KINYARWANDA_FUTURE = re.compile(
    r"(?:nti|nta|ntu|si)?(?:[iaubktrmnz]|bi|ki|ri|zi|ba|tu|mu|ku|ha|bu|ru|ka)?"
    r"za(?:ba|bona|bonana|genda|za|kora|tanga|ganira|haba|fata|subira|tangura|vuga|shobora|ja|garuka|rangiza)\w*")

# Words that turn an English sentence negative. Curly apostrophes count ("don’t").
ENGLISH_NEGATION = re.compile(
    r"\b(?:not|no|never|none|nobody|nothing|neither|nor|without|cannot)\b|n['’]t\b", re.I)
# Negative English phrases that Kirundi often says positively ("bitarenze" for "no later than",
# "mwibuke" for "don't forget"). They allow a negative in the Kirundi but do not require one.
ENGLISH_SOFT_NEGATION = re.compile(
    r"\b(?:no|not) (?:later|more|less|earlier) than\b|\bnot only\b|\bno\.?\s*\d"
    r"|\b(?:don['’]t|do not|never) (?:forget|hesitate|worry)\b|\bno problem\b", re.I)
# Kirundi negative forms: words starting nti-/nta-/ntu- ("ntibashobora", "nta shure"),
# "si"/"sin-" and "oya", and the -ta- / -da- infix only before a few verb stems ("atameze",
# "badafise"). The infix alone is too common in ordinary words (itariki, itangire, ndabasavye) to
# count, and is checked against every saved translation in the test suite. A net, not a proof: compiled from grammar sources, not checked by a Kirundi speaker.
KIRUNDI_NEGATION = re.compile(
    r"^(?:nt[aiu]\w+|nta|si|sin[a-z]+|oya)$"
    r"|^(?:a|ba|u|tu|mu|bi|ki|ri|zi|i|ka|ru|bu|ku|ha)(?:ta|da)(?:meze|fise|shobora|kora|bona|zi|ziye|gira)\w*$")
# Words that look negative but are neutral in school messages: "bitarenze" is the usual way to
# say "by" or "no later than" a date.
KIRUNDI_NOT_NEGATION = frozenset("bitarenze kitarenze bitararenga".split())

# Kirundi words for parts of the day (BCU dictionary, read 2026-11-14).
KIRUNDI_MORNING = ("igitondo", "gitondo", "agatondo", "gatondo")
KIRUNDI_LATER = ("umugoroba", "mugoroba", "akagoroba", "ijoro", "umuhingamo", "muhingamo")

META_LABELS = ("translation", "kirundi", "ikirundi", "english", "note", "notes")
FACT_KINDS = ("date", "month", "weekday", "time", "copy", "word")

# Details typed by hand in other forms. Month and day names of the languages
# a model is most likely to drift into (Kinyarwanda, Swahili, French), Kirundi
# number words, and ways of writing a weekday that the blank does not use.
OTHER_LANGUAGE_DATE_WORDS = (
    "mutarama gashyantare werurwe gicurasi kamena nzeri ugushyingo ukuboza "
    "januari februari machi aprili julai agosti septemba oktoba novemba desemba "
    "jumatatu jumanne jumatano alhamisi ijumaa jumamosi jumapili "
    "janvier fevrier avril juillet aout septembre octobre novembre decembre "
    "lundi mardi mercredi jeudi vendredi samedi dimanche"
).split()
KIRUNDI_NUMBER_WORDS = (
    "rimwe imwe kabiri ibiri zibiri abiri gatatu itatu zitatu atatu kane ine zine ane "
    "gatanu itanu zitanu atanu gatandatu itandatu zitandatu atandatu indwi zindwi "
    "umunani munani icenda cenda icumi cumi mirongo mbere"
).split()
ENGLISH_SHORT_DATE_WORDS = r"(?:Mon|Tues?|Wed|Thu(?:rs?)?|Fri|Sat|Sun|Jan|Feb|Mar|Apr|Jun|Jul|Aug|Sept?|Oct|Nov|Dec)"
SLOT = re.compile(r"\{(\d+)\}")
URL_PATTERN = r"[\w.+-]+@[\w.-]*\w|https?://\S+|www\.\S+|\b[a-z0-9-]+(?:\.[a-z0-9-]+)+/\S+"
RANGE_CONNECTOR = r"(?:-|–|—|to|until|through|thru|till|and|&|or|,)"


class UsageError(Exception):
    """The input cannot be used. Reported with exit code 2 and an "error" field."""


# ---------------------------------------------------------------------------
# Text helpers
# ---------------------------------------------------------------------------

def fold(text: str) -> str:
    """Lower case, accents removed, curly apostrophes straightened.

    Every character maps to exactly one character, so positions in the folded
    text are positions in the original.
    """
    out = []
    for ch in text:
        if ch in "’‘ʼ`":
            out.append("'")
            continue
        base = unicodedata.normalize("NFD", ch)
        stripped = "".join(c for c in base if not unicodedata.combining(c))
        low = stripped.lower() if len(stripped) == 1 else ch.lower()
        out.append(low if len(low) == 1 else ch)
    return "".join(out)


def quote(text: str) -> str:
    return '"' + " ".join(text.split()) + '"'


def snippet(text: str, start: int, end: int, width: int = 18) -> str:
    left = text[max(0, start - width):start]
    right = text[end:end + width]
    return quote(("..." if start > width else "") + left + text[start:end] + right + ("..." if end + width < len(text) else ""))


def _close_name(word: str, names: list[str]) -> Optional[int]:
    """Index (from 1) of the name a possibly misspelled word stands for."""
    match = difflib.get_close_matches(word, [n.lower() for n in names], n=1, cutoff=0.8)
    return [n.lower() for n in names].index(match[0]) + 1 if match else None


def months_named(text: str) -> set[int]:
    """Months a piece of English text names, by full name, abbreviation or near-miss spelling."""
    found = set()
    for word in re.findall(r"[a-z]+", fold(text)):
        if word in ENGLISH_MONTH_ABBREVIATIONS:
            found.add(ENGLISH_MONTH_ABBREVIATIONS[word])
        elif len(word) >= 5 or word in ("may", "june", "july"):
            index = _close_name(word, ENGLISH_MONTHS)
            if index:
                found.add(index)
    return found


def weekdays_named(text: str) -> set[int]:
    found = set()
    for word in re.findall(r"[a-z]+", fold(text)):
        word = word[:-1] if word.endswith("days") else word
        if word in ENGLISH_WEEKDAY_ABBREVIATIONS:
            found.add(ENGLISH_WEEKDAY_ABBREVIATIONS[word])
        elif len(word) >= 6:
            index = _close_name(word, ENGLISH_WEEKDAYS)
            if index:
                found.add(index)
    return found


# ---------------------------------------------------------------------------
# Facts
# ---------------------------------------------------------------------------

@dataclass
class Fact:
    number: int                  # 1-based position in the list; the blank is {number}
    text: str
    kind: str
    value: str = ""
    as_text: str = ""
    confirmed: bool = False
    start: int = -1
    end: int = -1
    # Worked out during validation:
    month: int = 0
    day: int = 0
    year: Optional[int] = None
    weekday: int = 0
    hour: int = 0
    minute: int = 0
    exact_time: bool = False     # False: the message gives no a.m./p.m.
    reading: Optional[tuple] = None    # the quoted time as read by _parse_time_text
    expected: Optional[tuple] = None   # (hour, minute) the quoted time stands for
    render: str = ""

    @property
    def has_blank(self) -> bool:
        return self.kind != "word"


@dataclass
class Review:
    """What validation found. `repairs` are for the model, `questions` for the writer."""
    facts: list[Fact] = field(default_factory=list)
    repairs: list[str] = field(default_factory=list)
    questions: list[str] = field(default_factory=list)
    slash_order: str = ""        # "md" or "dm" once the message shows how it writes all-digit dates


def _require_text(payload: object, name: str, allow_blank: bool = False) -> str:
    if not isinstance(payload, dict):
        raise UsageError("The input must be a JSON object.")
    if name not in payload:
        raise UsageError(f'The input is missing "{name}".')
    value = payload[name]
    if not isinstance(value, str):
        raise UsageError(f'"{name}" must be text.')
    try:
        value.encode("utf-8")
    except UnicodeEncodeError as exc:
        raise UsageError(f'"{name}" contains a broken character.') from exc
    if not allow_blank and not value.strip():
        raise UsageError(f'"{name}" is empty.')
    return value


def _read_facts(payload: dict) -> list[Fact]:
    raw = payload.get("facts")
    if not isinstance(raw, list):
        raise UsageError('"facts" must be a list. Use an empty list when the message has no details.')
    facts = []
    for number, item in enumerate(raw, start=1):
        if not isinstance(item, dict):
            raise UsageError(f"Fact {number} must be an object.")
        text, kind = item.get("text"), item.get("is")
        if not isinstance(text, str) or not text.strip():
            raise UsageError(f'Fact {number} needs "text": the exact words from the message.')
        if kind not in FACT_KINDS:
            raise UsageError(f'Fact {number} needs "is" set to one of: {", ".join(FACT_KINDS)}.')
        value, as_text = item.get("value", ""), item.get("as", "")
        if not isinstance(value, str) or not isinstance(as_text, str):
            raise UsageError(f'Fact {number}: "value" and "as" must be text.')
        facts.append(Fact(number, text, kind, value.strip(), as_text.strip(), item.get("confirmed") is True))
    return facts


def _locate(source: str, facts: list[Fact], review: Review) -> None:
    """Finds where each fact's quoted text sits in the message.

    Longer quotes are placed first, and a quote is placed where it stands as
    a whole token when it can be, so that "5" does not land inside "15".
    """
    taken: list[tuple[int, int]] = []

    def free(index: int, end: int) -> bool:
        return all(end <= s or index >= e for s, e in taken)

    def whole(index: int, end: int) -> bool:
        left = source[index - 1:index] if index else ""
        right = source[end:end + 1]
        return not (left.isalnum() and source[index].isalnum()) and not (right.isalnum() and source[end - 1].isalnum())

    for fact in sorted(facts, key=lambda f: -len(f.text)):
        places = [m.start() for m in re.finditer(re.escape(fact.text), source)]
        places = [i for i in places if free(i, i + len(fact.text))]
        best = next((i for i in places if whole(i, i + len(fact.text))), places[0] if places else -1)
        if best >= 0:
            fact.start, fact.end = best, best + len(fact.text)
            taken.append((fact.start, fact.end))
    for fact in facts:
        if fact.start == -1:
            if fact.text in source:
                review.repairs.append(
                    f"Fact {fact.number} quotes {quote(fact.text)}, but that part of the message is already "
                    "covered by another fact. Make the quotes not overlap."
                )
            else:
                review.repairs.append(
                    f"Fact {fact.number} quotes {quote(fact.text)}, which is not in the message. "
                    "Copy the words exactly, including punctuation, spaces and capitals: "
                    + repr(fact.text) + " was not found."
                )


def detail_tokens(source: str) -> list[tuple[int, int, str]]:
    """Everything in the message that must be inside a quoted fact.

    Digits, weekday names, month names written with a capital, capitalised
    day and month abbreviations, "noon" and "midnight", clock times written
    in words, and email addresses and links. No judgement is made here about
    what they mean; the model's facts say that, and validation checks the
    facts.
    """
    tokens: list[tuple[int, int, str]] = []
    for match in re.finditer(r"\d+", source):
        tokens.append((match.start(), match.end(), match[0]))
    for match in re.finditer(r"[A-Za-z]+", source):
        word, low = match[0], match[0].lower()
        capital = word[0].isupper()
        is_weekday = low.rstrip("s") in (w.lower() for w in ENGLISH_WEEKDAYS) or low in (w.lower() for w in ENGLISH_WEEKDAYS)
        is_month = capital and low in (m.lower() for m in ENGLISH_MONTHS)
        is_short = capital and (low in ENGLISH_WEEKDAY_ABBREVIATIONS or low in ENGLISH_MONTH_ABBREVIATIONS)
        if is_weekday or is_month or is_short or low in ("noon", "midnight"):
            tokens.append((match.start(), match.end(), word))
    for match in re.finditer(URL_PATTERN, source, flags=re.I):
        text = match[0].rstrip(".,;:!?)>\"'")
        tokens.append((match.start(), match.start() + len(text), text))
    folded = fold(source)
    hours = "|".join(w for w, n in _UNIT_WORDS.items() if n <= 12)
    spoken = (
        r"\b(?:" + hours + r")\s+(?:o'clock|[ap]\.?m\b\.?)"
        r"|\b(?:half|quarter)\s+(?:past|to|after|before)\s+(?:" + hours + r"|noon|midnight)\b"
    )
    for match in re.finditer(spoken, folded):
        tokens.append((match.start(), match.end(), source[match.start():match.end()]))
    return sorted(set(tokens))


def sentence_starts(text: str, protected: list[tuple[int, int]]) -> list[int]:
    """Where each sentence of the message begins. Full stops inside facts and after titles do not count."""
    starts = [0]
    # A line break alone is not a sentence end (letters are often hard-wrapped);
    # a blank line or the start of a list item is.
    for match in re.finditer(r"[.!?]+[\"')\]]*\s+|\n\s*\n\s*|\n\s*(?=[-*\u2022]\s|\d+[.)]\s)", text):
        following = text[match.end():match.end() + 1]
        if match[0][0] == ".":
            # A full stop in the middle of a fact ("Nov. 20") never ends a sentence. One that
            # closes a fact ("3 p.m.") or follows "a.m."/"p.m." does when a capital comes next.
            if any(s <= match.start() < e - 1 for s, e in protected):
                continue
            closes_fact = any(match.start() == e - 1 for s, e in protected)
            before = re.search(r"[A-Za-z.]+$", text[:match.start()])
            short = before[0].lower() if before else ""
            if short in SENTENCE_ABBREVIATIONS and short not in ("a.m", "p.m"):
                continue
            if (closes_fact or short in ("a.m", "p.m")) and not following.isupper():
                continue
            if following.islower():
                continue
        elif any(s < match.end() and match.start() < e for s, e in protected):
            continue
        starts.append(match.end())
    return starts


def _numbers_in(text: str) -> list[int]:
    return [int(n) for n in re.findall(r"\d+", text)]


def _valid_md(month: int, day: int) -> bool:
    return 1 <= month <= 12 and 1 <= day <= 31


def number_from_words(words: list[str]) -> Optional[int]:
    """The number one or two English words say: "five", "twentieth", "thirty first"."""
    if words in (["hundred"], ["one", "hundred"], ["a", "hundred"]):
        return 100
    if len(words) == 1:
        for table in (_UNIT_WORDS, _TENS_WORDS, _ORDINAL_UNITS, _ORDINAL_TENS):
            if words[0] in table:
                return table[words[0]]
        return None
    if len(words) == 2 and words[0] in _TENS_WORDS:
        for table in (_UNIT_WORDS, _ORDINAL_UNITS):
            if table.get(words[1], 10) < 10:
                return _TENS_WORDS[words[0]] + table[words[1]]
    return None


def _is_number_word(word: str) -> bool:
    return any(word in table for table in (_UNIT_WORDS, _TENS_WORDS, _ORDINAL_UNITS, _ORDINAL_TENS))


def _month_of(word: str) -> Optional[int]:
    found = months_named(word)
    return next(iter(found)) if len(found) == 1 else None


def _parse_named_date(text: str) -> Optional[tuple[Optional[int], int, Optional[int]]]:
    """(month or None, day, year or None) when the text is one date and nothing else.

    Accepts "November 20, 2026", "Nov. 20", "20th of November", "the 20th",
    "20", "November twentieth". Anything with other words in it is refused.
    """
    if not re.fullmatch(r"[\w\s,.'’-]+", text):
        return None
    month: Optional[int] = None
    day: Optional[int] = None
    year: Optional[int] = None
    spoken: list[str] = []
    after_digits = False
    for word in re.findall(r"[a-z]+|\d+", fold(text)):
        if word.isdigit():
            if day is None and not spoken and len(word) <= 2:
                day = int(word)
            elif year is None and len(word) == 4 and (day is not None or spoken):
                year = int(word)
            else:
                return None
            after_digits = True
            continue
        if after_digits and word in ("st", "nd", "rd", "th"):
            after_digits = False
            continue
        after_digits = False
        if word in ("the", "of"):
            continue
        if _is_number_word(word):
            if day is not None:
                return None
            spoken.append(word)
            continue
        found = _month_of(word)
        if found and month is None:
            month = found
            continue
        return None
    if spoken:
        day = number_from_words(spoken)
    if day is None:
        return None
    return month, day, year


def _parse_month_text(text: str) -> Optional[tuple[int, Optional[int]]]:
    """(month, year or None) when the text is a month name, with or without a year, and nothing else."""
    match = re.fullmatch(r"\s*([A-Za-z]+)\.?,?(?:\s+(\d{4}))?\s*", text)
    if not match:
        return None
    month = _month_of(match[1])
    return (month, int(match[2]) if match[2] else None) if month else None


def _month_evidence(fact: Fact, source: str, facts: list[Fact]) -> Optional[int]:
    """The month a bare day number ("20" in "November 16-20") belongs to.

    It is the month written nearest before it on the same line, or, when
    there is none, nearest after it.
    """
    found: list[tuple[int, int, int]] = []
    for start, end, token in detail_tokens(source):
        month = _month_of(token) if token[:1].isalpha() and token[:1].isupper() else None
        if month:
            found.append((start, end, month))
    for other in facts:
        if other is fact or other.kind != "date" or other.start < 0 or not other.month:
            continue
        if re.fullmatch(r"[\d\s/.-]+", other.text):
            found.append((other.start, other.end, other.month))
    line_start = source.rfind("\n", 0, fact.start) + 1
    line_end = source.find("\n", fact.end)
    line_end = len(source) if line_end == -1 else line_end
    before = [f for f in found if line_start <= f[0] and f[1] <= fact.start and fact.start - f[1] <= 60]
    if before:
        return max(before)[2]
    after = [f for f in found if f[0] >= fact.end and f[1] <= line_end and f[0] - fact.end <= 30]
    return min(after)[2] if after else None


def _slash_order(facts: list[Fact]) -> str:
    """How the message writes all-digit dates: "md", "dm", or "" when the message contradicts itself.

    "11/19" can only be month first, so "11/5" beside it is too. An answer
    from the writer ("confirmed") settles it the same way.
    """
    seen = set()
    for fact in facts:
        match = re.fullmatch(r"\s*(\d{1,2})\s*[/.-]\s*(\d{1,2})(?:\s*[/.-]\s*\d{2,4})?\s*", fact.text) if fact.kind == "date" else None
        if not match:
            continue
        a, b = int(match[1]), int(match[2])
        if a <= 12 < b <= 31:
            seen.add("md")
        elif b <= 12 < a <= 31:
            seen.add("dm")
        elif fact.confirmed and a != b and (fact.month, fact.day) in ((a, b), (b, a)):
            seen.add("md" if (fact.month, fact.day) == (a, b) else "dm")
    if not seen:
        return "md"  # the messages are American English: month first unless the message shows otherwise
    return seen.pop() if len(seen) == 1 else ""


def _check_date(fact: Fact, source: str, facts: list[Fact], review: Review) -> None:
    n = fact.number
    match = re.fullmatch(r"(?:(\d{4})-)?(\d{1,2})-(\d{1,2})", fact.value)
    if not match:
        review.repairs.append(f'Fact {n}: write the date value as "2026-11-20", or "11-20" when the quoted text gives no year.')
        return
    year = int(match[1]) if match[1] else None
    month, day = int(match[2]), int(match[3])
    try:
        _dt.date(year or 2028, month, day)  # 2028 is a leap year, so 29 February is allowed without a year
    except ValueError:
        review.repairs.append(f"Fact {n}: {fact.value} is not a real date.")
        return
    fact.month, fact.day, fact.year = month, day, year
    said = f"{ENGLISH_MONTHS[month - 1]} {day}"

    def year_must_be(text_year: Optional[int]) -> None:
        if text_year == year:
            return
        if text_year is None:
            review.repairs.append(
                f"Fact {n}: {quote(fact.text)} gives no year, so the value must not have one. "
                f'Write "{month:02d}-{day:02d}".'
            )
        else:
            review.repairs.append(
                f"Fact {n}: {quote(fact.text)} gives the year {text_year}, so the value must start with {text_year}-."
            )

    digits_only = re.fullmatch(r"\s*(\d{1,4})\s*([/.-])\s*(\d{1,2})(?:\s*\2\s*(\d{2,4}))?\s*", fact.text)
    if digits_only:
        a, b, y = int(digits_only[1]), int(digits_only[3]), digits_only[4]
        if a > 31 and y:  # 2026-11-20
            readings = [(b, int(y))] if _valid_md(b, int(y)) else []
            text_year: Optional[int] = a
        else:
            readings = [r for r in ((a, b), (b, a)) if _valid_md(*r)]
            text_year = (int(y) + (2000 if len(y) <= 2 else 0)) if y else None
        readings = list(dict.fromkeys(readings))
        year_must_be(text_year)
        if (month, day) not in readings:
            review.repairs.append(f"Fact {n}: {quote(fact.text)} cannot be read as {said}.")
        elif len(readings) == 2 and not fact.confirmed:
            settled = _settled_by_weekday(fact, facts, readings, text_year, source)
            if settled is None and review.slash_order:
                settled = (a, b) if review.slash_order == "md" else (b, a)
            if settled is None:
                if any(q.startswith("Your message has the date") for q in review.questions):
                    return  # one question settles how the message writes its dates

                (m1, d1), (m2, d2) = readings
                review.questions.append(
                    f"Your message has the date {quote(fact.text)}. Is that "
                    f"{ENGLISH_MONTHS[m1 - 1]} {d1} or {ENGLISH_MONTHS[m2 - 1]} {d2}?"
                )
            elif settled != (month, day):
                review.repairs.append(
                    f"Fact {n}: going by the weekday next to it or the other dates in the message, {quote(fact.text)} is "
                    f"{ENGLISH_MONTHS[settled[0] - 1]} {settled[1]}, not {said}."
                )
        return

    parsed = _parse_named_date(fact.text)
    if parsed is None:
        review.repairs.append(
            f"Fact {n}: a date fact quotes one date and nothing else, for example \"November 20, 2026\", "
            f"\"Nov. 20\" or \"the 20th\". {quote(fact.text)} has something else in it. Give the weekday, "
            "the time and each end of a range their own facts. For a month with no day, use a month fact."
        )
        return
    text_month, text_day, text_year = parsed
    year_must_be(text_year)
    if fact.confirmed:
        return  # the writer corrected the date herself
    if text_month is None:
        nearest = _month_evidence(fact, source, facts)
        if nearest is None:
            review.repairs.append(
                f"Fact {n}: {quote(fact.text)} does not name a month and no month is written near it. "
                "If the message gives only a day number, make it a copy fact."
            )
        elif nearest != month:
            review.repairs.append(
                f"Fact {n}: the month written nearest to {quote(fact.text)} is {ENGLISH_MONTHS[nearest - 1]}, "
                f"but the value says {ENGLISH_MONTHS[month - 1]}."
            )
    elif text_month != month:
        review.repairs.append(
            f"Fact {n}: {quote(fact.text)} names {ENGLISH_MONTHS[text_month - 1]}, "
            f"but the value says {ENGLISH_MONTHS[month - 1]}."
        )
    if text_day != day:
        review.repairs.append(f"Fact {n}: {quote(fact.text)} says the day {text_day}, but the value says {day}.")


def _check_month(fact: Fact, review: Review) -> None:
    n = fact.number
    match = re.fullmatch(r"(?:(\d{4})-)?(\d{1,2})", fact.value)
    if not match or not 1 <= int(match[2]) <= 12:
        review.repairs.append(f'Fact {n}: write the month value as "12", or "2027-06" when the quoted text gives a year.')
        return
    fact.month, fact.year = int(match[2]), int(match[1]) if match[1] else None
    parsed = _parse_month_text(fact.text)
    if parsed is None:
        review.repairs.append(
            f"Fact {n}: a month fact quotes a month name, with a year if one is written, and nothing else, "
            f"for example \"December\" or \"June 2027\". {quote(fact.text)} is not that."
        )
        return
    if parsed != (fact.month, fact.year):
        right = (f"{parsed[1]}-" if parsed[1] else "") + f"{parsed[0]:02d}"
        review.repairs.append(f'Fact {n}: {quote(fact.text)} is the month value "{right}", but the value says "{fact.value}".')


def _in_weekday_list(weekday: Fact, facts: list[Fact], source: str) -> bool:
    """True for "Monday-Friday" or "Thursday and Friday": such a day is not the day of the date after it."""
    for other in facts:
        if other is weekday or other.kind != "weekday" or other.start < 0:
            continue
        gap = source[other.end:weekday.start] if other.end <= weekday.start else source[weekday.end:other.start]
        if len(gap) <= 12 and re.fullmatch(r"\s*" + RANGE_CONNECTOR + r"\s*", fold(gap)):
            return True
    return False


def _neighbour_weekday(fact: Fact, facts: list[Fact], source: str) -> Optional[Fact]:
    """The weekday written directly with a date: "Friday, November 20" or "November 20 (Friday)"."""
    for other in facts:
        if other.kind != "weekday" or other.start < 0 or not other.weekday:
            continue
        if other.end <= fact.start:
            joined = re.fullmatch(r"[\s,]*(?:the\s+)?", source[other.end:fact.start])
        elif other.start >= fact.end:
            joined = re.fullmatch(r"\s*[,(]\s*", source[fact.end:other.start])
        else:
            joined = None
        if joined is not None and not _in_weekday_list(other, facts, source):
            return other
    return None


def _settled_by_weekday(fact: Fact, facts: list[Fact], readings: list, year: Optional[int], source: str) -> Optional[tuple]:
    neighbour = _neighbour_weekday(fact, facts, source)
    if neighbour is None or year is None:
        return None
    fits = []
    for month, day in readings:
        try:
            if _dt.date(year, month, day).isoweekday() == neighbour.weekday:
                fits.append((month, day))
        except ValueError:
            pass
    return fits[0] if len(fits) == 1 else None


def _check_weekday(fact: Fact, review: Review) -> None:
    names = [w.lower() for w in ENGLISH_WEEKDAYS]
    if fact.value.lower() not in names:
        review.repairs.append(f'Fact {fact.number}: write the weekday value as one of {", ".join(names)}.')
        return
    fact.weekday = names.index(fact.value.lower()) + 1
    named = weekdays_named(fact.text)
    letters = fact.text.strip().rstrip(".").lower()
    if letters in SHORTEST_WEEKDAYS:  # "M-F", "Tu/Th": only as the quote of a weekday fact
        named = {SHORTEST_WEEKDAYS[letters]}
    if not re.fullmatch(r"\s*[A-Za-z]+\.?\s*", fact.text) or len(named) != 1:
        review.repairs.append(
            f"Fact {fact.number}: a weekday fact quotes one day name and nothing else, for example "
            f"\"Friday\" or \"Tues.\". {quote(fact.text)} is not that. Give each day its own fact."
        )
    elif fact.weekday not in named and not fact.confirmed:
        review.repairs.append(
            f"Fact {fact.number}: {quote(fact.text)} is {ENGLISH_WEEKDAYS[next(iter(named)) - 1]}, "
            f"but the value says {ENGLISH_WEEKDAYS[fact.weekday - 1]}."
        )


_MARK = r"(?:[ap]\.?\s?m\.?|[ap]\b|in the (?:morning|afternoon|evening)|at night|tonight)"


def _mark(text: Optional[str]) -> Optional[str]:
    if not text:
        return None
    return "am" if text.replace(".", "").replace(" ", "") in ("am", "a") or "morning" in text else "pm"


def _parse_time_text(text: str) -> Optional[tuple[int, int, Optional[str], bool]]:
    """(hour, minute, "am"/"pm"/None, fixed) when the text is one clock time and nothing else.

    `fixed` means the hour is already on the 24-hour clock ("15:15", "noon",
    "half past noon"). Otherwise the hour is 1 to 12 as written.
    """
    low = " ".join(fold(text).split()).strip(" ,;")
    match = re.fullmatch(r"(?:12(?::00)? ?)?(noon|midnight)", low)
    if match:
        return (12 if match[1] == "noon" else 0, 0, None, True)
    match = re.fullmatch(
        r"(a quarter|quarter|half|\d{1,2}|[a-z]+(?:[ -][a-z]+)?)(?: minutes?)? (past|after|to|till|before|of) "
        r"(\d{1,2}|[a-z]+)(?: o'clock)?(?: (" + _MARK + r"))?", low)
    if match:
        offset = {"a quarter": 15, "quarter": 15, "half": 30}.get(match[1])
        if offset is None:
            offset = int(match[1]) if match[1].isdigit() else number_from_words(re.findall(r"[a-z]+", match[1]))
        if not offset or not 1 <= offset <= 59:
            return None
        forward = match[2] in ("past", "after")
        if match[3] in ("noon", "midnight"):
            if match[4]:
                return None
            total = ((720 if match[3] == "noon" else 0) + (offset if forward else -offset)) % 1440
            return (total // 60, total % 60, None, True)
        hour = int(match[3]) if match[3].isdigit() else _UNIT_WORDS.get(match[3])
        if hour is None or not 1 <= hour <= 12:
            return None
        marker = _mark(match[4])
        if forward:
            return (hour, offset, marker, False)
        if hour == 12 and marker:  # "quarter to 12 p.m." is 11:45 in the morning
            marker = "am" if marker == "pm" else "pm"
        return (12 if hour == 1 else hour - 1, 60 - offset, marker, False)
    match = re.fullmatch(r"(\d{1,2})(?:[:.](\d{2}))?(?: o'clock)?(?: ?(" + _MARK + r"))?", low)
    if match:
        hour, minute, marker = int(match[1]), int(match[2] or 0), _mark(match[3])
        if minute > 59:
            return None
        if marker:
            return (hour, minute, marker, False) if 1 <= hour <= 12 else None
        if hour > 23:
            return None
        fixed = hour >= 13 or hour == 0 or (match[1].startswith("0") and match[2] is not None)
        return (hour, minute, None, fixed)
    match = re.fullmatch(r"([a-z]+)(?: o'clock)?(?: (" + _MARK + r"))?", low)
    if match and _UNIT_WORDS.get(match[1], 99) <= 12 and ("o'clock" in low or match[2]):
        return (_UNIT_WORDS[match[1]], 0, _mark(match[2]), False)
    match = re.fullmatch(r"([01]\d|2[0-3])([0-5]\d)(?: ?(?:hours|hrs|h))?", low)
    if match:
        return (int(match[1]), int(match[2]), None, True)
    return None


def _range_partners(fact: Fact, facts: list[Fact], source: str) -> list[tuple[str, Fact]]:
    """Time facts joined to this one in a range or list ("7-8 p.m.", "1, 2 and 3 p.m."), the one after it first."""
    after, before = [], []
    for other in facts:
        if other is fact or other.kind != "time" or other.start < 0:
            continue
        if fact.end <= other.start:
            gap, side = source[fact.end:other.start], after
        elif other.end <= fact.start:
            gap, side = source[other.end:fact.start], before
        else:
            continue
        if len(gap) <= 12 and re.fullmatch(r"\s*" + RANGE_CONNECTOR + r"?\s*", fold(gap)):
            side.append(other)
    return [("after", f) for f in after] + [("before", f) for f in before]


def _resolve_times(times: list[Fact], facts: list[Fact], source: str) -> None:
    """Works out the 24-hour time each time fact stands for.

    A time with its own a.m. or p.m. is settled. A time without one borrows
    from the time it is joined to, so that a range runs forwards: "7-8 p.m."
    starts at 19:00, "11-1 p.m." at 11:00, and "9 a.m.-3" ends at 15:00. A
    time with nothing to borrow from stays exactly as written.
    """
    waiting = []
    for fact in times:
        fact.reading = _parse_time_text(fact.text)
        if fact.reading is None:
            continue
        hour, minute, marker, fixed = fact.reading
        if fixed:
            fact.expected, fact.exact_time = (hour, minute), True
        elif marker:
            fact.expected, fact.exact_time = (hour % 12 + (12 if marker == "pm" else 0), minute), True
        else:
            waiting.append(fact)
    progress = True
    while waiting and progress:
        progress = False
        for fact in list(waiting):
            settled = [(side, o) for side, o in _range_partners(fact, facts, source) if o.exact_time and o.expected]
            if not settled:
                continue
            side, partner = settled[0]
            hour, minute = fact.reading[0], fact.reading[1]
            partner_minutes = partner.expected[0] * 60 + partner.expected[1]
            same = hour % 12 + (12 if partner.expected[0] >= 12 else 0)
            mine = same * 60 + minute
            forwards = mine <= partner_minutes if side == "after" else mine >= partner_minutes
            fact.expected, fact.exact_time = ((same if forwards else (same + 12) % 24), minute), True
            waiting.remove(fact)
            progress = True
    for fact in waiting:
        fact.expected = (fact.reading[0], fact.reading[1])


def _check_time(fact: Fact, facts: list[Fact], source: str, review: Review) -> None:
    n = fact.number
    match = re.fullmatch(r"(\d{1,2}):(\d{2})", fact.value)
    if not match or int(match[1]) > 23 or int(match[2]) > 59:
        if fact.expected is not None:
            review.repairs.append(
                f'Fact {n}: write the time value as hours and minutes. For {quote(fact.text)} set "value" to '
                f'"{fact.expected[0]}:{fact.expected[1]:02d}".'
            )
        else:
            review.repairs.append(f'Fact {n}: write the time value as hours and minutes, for example "15:15".')
        return
    hour, minute = int(match[1]), int(match[2])
    fact.hour, fact.minute = hour, minute
    if fact.reading is None or fact.expected is None:
        review.repairs.append(
            f"Fact {n}: a time fact quotes one clock time and nothing else, for example \"3:15 p.m.\", \"7\", "
            f"\"noon\", \"15:15\" or \"quarter to 4\". {quote(fact.text)} is not that. Give each end of a "
            "range its own fact. To keep the words exactly as written, use a copy fact."
        )
        return
    right = f"{fact.expected[0]}:{fact.expected[1]:02d}"
    if (hour, minute) == fact.expected:
        return
    if fact.exact_time:
        review.repairs.append(f'Fact {n}: {quote(fact.text)} is {right} on the 24-hour clock. Set "value" to "{right}".')
    elif fact.confirmed and minute == fact.expected[1] and hour % 12 == fact.expected[0] % 12:
        fact.exact_time = True  # the writer said which half of the day
    else:
        review.repairs.append(
            f"Fact {n}: {quote(fact.text)} does not say a.m. or p.m., and no time joined to it does, so the "
            f'value must stay "{right}". Do not guess.'
        )


def _calendar_words(text: str) -> list[str]:
    return [token for _, _, token in detail_tokens(text) if token.isalpha() and token.lower() not in ("noon", "midnight")]


def _check_copy(fact: Fact, review: Review) -> None:
    n, text = fact.number, fact.text.strip()
    low = text.lower().rstrip(".")
    weekday_names = [w.lower() for w in ENGLISH_WEEKDAYS]
    if low in weekday_names or low.rstrip("s") in weekday_names:
        review.repairs.append(
            f"Fact {n}: {quote(text)} is a day of the week, so make it a weekday fact. If it is part of a "
            'name such as "Taco Tuesday", quote the whole name.'
        )
        return
    if low in (m.lower() for m in ENGLISH_MONTHS) and low not in NAME_MONTHS:
        review.repairs.append(f"Fact {n}: {quote(text)} is a month, so make it a month fact. If it is part of a name "
            '(a street, a school, a person), make a copy fact that quotes the whole name without any number.')
        return
    if low in ("noon", "midnight"):
        review.repairs.append(f"Fact {n}: {quote(text)} is a time, so make it a time fact.")
        return
    one_piece = not re.search(r"\s", text)
    if not one_piece:
        words = re.findall(r"[a-z']+", fold(text))
        # Common English words written without a capital: a title such as
        # "Lincoln School for the Arts" has few, a copied sentence has many.
        ordinary = [w for w in re.findall(r"\b[a-z][a-z']*\b", text) if w in ENGLISH_MARKERS]
        if len(words) > 8 or len(ordinary) >= 3:
            review.repairs.append(
                f"Fact {n}: {quote(text)} is too much to copy. A copy fact is for one name, number, code or "
                "contact detail that must stay exactly as written. Translate the rest."
            )
            return
    if _calendar_words(text) and re.search(r"\d", text) and not re.fullmatch(URL_PATTERN, text, flags=re.I):
        review.repairs.append(
            f"Fact {n}: {quote(text)} has a day or month name together with a number. Give the date, the "
            "day and the number their own facts. If the day or month word is part of a name (\"March Lane\"), "
            "quote the name in one copy fact and the number in another."
        )
        return
    if re.search(r"\d\s*[ap]\.?\s?m\b|\b\d{1,2}:\d{2}\b", fold(text)) and not fact.confirmed:
        if not re.fullmatch(URL_PATTERN, text, flags=re.I):
            review.repairs.append(
                f"Fact {n}: {quote(text)} looks like a clock time, so make it a time fact. If it is clearly "
                'not a time (a score or a ratio), add "confirmed": true.'
            )
            return
    if re.search(r"\d", text) and not re.fullmatch(URL_PATTERN, text, flags=re.I):
        loose = [w for w in re.findall(r"(?<![A-Za-z0-9'])[a-z]{3,}(?![A-Za-z0-9])", text) if w not in ("ext",)]
        if loose:
            review.repairs.append(
                f"Fact {n}: {quote(text)} has a number together with ordinary words ({', '.join(loose)}). "
                "Quote only the number, and translate the words."
            )
            return
    if fact.as_text:
        ordinal = re.fullmatch(r"(\d+)(?:st|nd|rd|th)", text, flags=re.I)
        spoken = number_from_words(re.findall(r"[a-z]+", fold(text))) if re.fullmatch(r"[A-Za-z\s-]+", text) else None
        right = ordinal[1] if ordinal else (str(spoken) if spoken is not None else None)
        if right is None:
            review.repairs.append(
                f'Fact {n}: "as" is only for a number written in words ("two" as "2") or with an ending '
                f'("1st" as "1"). Remove "as"; {quote(text)} is copied exactly as written.'
            )
        elif fact.as_text != right:
            review.repairs.append(f'Fact {n}: {quote(text)} is the number {right}, so "as" must be "{right}".')
    # "11/12" or "11-20" on its own can be a date. Copying it would leave a
    # date in digits, which a Kirundi reader takes day-first.
    looks_dated = re.fullmatch(r"(\d{1,2})\s*[/-]\s*(\d{1,2})", text)
    if looks_dated and not fact.confirmed and _valid_md(int(looks_dated[1]), int(looks_dated[2])):
        review.repairs.append(
            f"Fact {n}: {quote(text)} could be a date. If it is, make it a date fact. If it is clearly "
            'a fraction, a score or a range of numbers, add "confirmed": true. If you cannot tell, ask the '
            "writer what it means."
        )


def _check_word(fact: Fact, review: Review) -> None:
    n, text = fact.number, fact.text.strip()
    low = text.lower().rstrip(".")
    weekday_names = [w.lower() for w in ENGLISH_WEEKDAYS]
    if not re.fullmatch(r"[A-Za-z]+\.?", text):
        review.repairs.append(
            f"Fact {n}: a word fact quotes one ordinary word, such as the \"May\" in \"May I call you?\". "
            f"{quote(text)} is not one word."
        )
    elif low in weekday_names or low.rstrip("s") in weekday_names:
        review.repairs.append(
            f"Fact {n}: {quote(text)} is a day of the week, so make it a weekday fact. If it is part of a "
            "name, make a copy fact that quotes the whole name."
        )
    elif low in (m.lower() for m in ENGLISH_MONTHS) and low not in ("may", "march"):
        review.repairs.append(
            f"Fact {n}: {quote(text)} is a month, so make it a month fact. If it is a person's name, make a "
            "copy fact that quotes the name."
        )


def review_facts(source: str, facts: list[Fact]) -> Review:
    """Proves the model's list of facts against the message."""
    review = Review(facts=facts)
    _locate(source, facts, review)
    located = [f for f in facts if f.start >= 0]

    for fact in located:
        if fact.kind == "weekday":
            _check_weekday(fact, review)
    for fact in facts:
        # Read every date value first, so a bare day number can lean on the date before it.
        parsed = re.fullmatch(r"(?:(\d{4})-)?(\d{1,2})-(\d{1,2})", fact.value) if fact.kind == "date" else None
        if parsed and _valid_md(int(parsed[2]), int(parsed[3])):
            fact.year = int(parsed[1]) if parsed[1] else None
            fact.month, fact.day = int(parsed[2]), int(parsed[3])
    review.slash_order = _slash_order(located)
    _resolve_times([f for f in located if f.kind == "time"], located, source)
    for fact in located:
        if fact.kind == "date":
            _check_date(fact, source, facts, review)
        elif fact.kind == "month":
            _check_month(fact, review)
        elif fact.kind == "time":
            _check_time(fact, facts, source, review)
        elif fact.kind == "copy":
            _check_copy(fact, review)
            before = source[max(0, fact.start - 2):fact.start]
            if before[-1:] in ("-", "\u2212") and not before[:-1].strip().isalnum() and re.match(r"[$\d]", fact.text):
                review.repairs.append(
                    f"Fact {fact.number}: {quote(fact.text)} has a minus sign in front of it in the message. "
                    "Start the quote at the minus sign so it is kept."
                )
        elif fact.kind == "word":
            _check_word(fact, review)

    # Coverage: nothing that looks like a detail may be left outside the facts.
    loose = [
        f"{quote(token)} in {snippet(source, start, end)}"
        for start, end, token in detail_tokens(source)
        if not any(f.start <= start and end <= f.end for f in located)
    ]
    if loose:
        review.repairs.append(
            "These parts of the message are not inside any fact: " + "; ".join(loose) + ". Add a fact that "
            "quotes each one: date, month, weekday, time, copy (to keep it exactly as written, such as a "
            "number, a name, an email address or a link), or word (an ordinary word that is not a day or month)."
        )

    # A weekday written with a full date must agree with the calendar.
    for fact in located:
        if fact.kind != "date" or not (fact.year and fact.month) or fact.confirmed:
            continue
        neighbour = _neighbour_weekday(fact, facts, source)
        if neighbour is None or neighbour.confirmed:
            continue
        try:
            actual = _dt.date(fact.year, fact.month, fact.day).isoweekday()
        except ValueError:
            continue
        if actual != neighbour.weekday:
            review.questions.append(
                f"Your message says {ENGLISH_WEEKDAYS[neighbour.weekday - 1]}, but "
                f"{ENGLISH_MONTHS[fact.month - 1]} {fact.day}, {fact.year} is a "
                f"{ENGLISH_WEEKDAYS[actual - 1]}. Which is right, the day or the date?"
            )

    for fact in facts:
        fact.render = _render(fact)
    return review


def _render(fact: Fact) -> str:
    """The exact Kirundi text written into a blank."""
    if fact.kind == "date" and fact.month:
        return f"{fact.day} {KIRUNDI_MONTHS[fact.month]}" + (f" {fact.year}" if fact.year else "")
    if fact.kind == "month" and fact.month:
        return KIRUNDI_MONTHS[fact.month] + (f" {fact.year}" if fact.year else "")
    if fact.kind == "weekday" and fact.weekday:
        return KIRUNDI_WEEKDAYS[fact.weekday]
    if fact.kind == "time" and fact.value:
        return f"{fact.hour}:{fact.minute:02d}"
    if fact.kind == "copy":
        return fact.as_text or fact.text
    return ""


# ---------------------------------------------------------------------------
# Template: the Kirundi with numbered blanks
# ---------------------------------------------------------------------------

def fill(template: str, facts: list[Fact]) -> str:
    by_number = {f.number: f for f in facts}

    def put(match: re.Match) -> str:
        fact = by_number.get(int(match[1]))
        if fact is None or not fact.has_blank:
            return match[0]
        text = fact.render
        before = template[:match.start()].rstrip()
        if fact.kind == "weekday" and (not before or before[-1] in ".!?"):
            text = text[0].upper() + text[1:]
        if text.endswith(".") and template[match.end():match.end() + 1] == ".":
            text = text[:-1]  # "Grand Ave." at the end of a sentence: one full stop, not two
        return text

    return SLOT.sub(put, template)


@dataclass
class GateResult:
    id: str
    passed: bool
    problems: list[str] = field(default_factory=list)
    repairs: list[str] = field(default_factory=list)


def gate_not_empty(template: str, **_) -> GateResult:
    if template.strip():
        return GateResult("not_empty", True)
    return GateResult("not_empty", False, ["The translation is empty."], ["Write the translation."])


def gate_blanks(template: str, facts: list[Fact], **_) -> GateResult:
    """Every fact is written exactly once, and no blank is made up."""
    problems, repairs = [], []
    used = [int(n) for n in SLOT.findall(template)]
    by_number = {f.number: f for f in facts}
    for number in sorted(set(used)):
        fact = by_number.get(number)
        if fact is None:
            problems.append("The translation refers to a detail that is not in your message.")
            repairs.append(f"Remove {{{number}}}: there is no fact {number}.")
        elif not fact.has_blank:
            problems.append("The translation refers to a detail that is not in your message.")
            repairs.append(f"Remove {{{number}}}: fact {number} is an ordinary word and has no blank.")
    for fact in facts:
        if not fact.has_blank:
            continue
        count = used.count(fact.number)
        if count == 0:
            problems.append(f"{_plain(fact)} from your message is missing from the translation.")
            repairs.append(f"Put {{{fact.number}}} where {quote(fact.text)} belongs. It will be written as {quote(fact.render)}.")
        elif count > 1:
            problems.append(f"{_plain(fact)} appears {count} times in the translation, but once in your message.")
            repairs.append(f"Use {{{fact.number}}} once only.")
    return GateResult("blanks", not problems, problems, repairs)


def _plain(fact: Fact) -> str:
    """A detail described for the writer, in terms of her own message."""
    if fact.kind == "date":
        return f"The date {quote(fact.text)}"
    if fact.kind == "month":
        return f"The month {quote(fact.text)}"
    if fact.kind == "weekday":
        return f"The day {quote(fact.text)}"
    if fact.kind == "time":
        return f"The time {quote(fact.text)}"
    return quote(fact.text)


def gate_no_typed_details(template: str, **_) -> GateResult:
    """Details may only come from blanks. Anything typed by hand is refused.

    Every detail of the message reaches the translation through a blank, so
    a detail typed by hand is either a repeat or an invention. This gate
    looks for the forms such a detail is likely to take. It is a list, not a
    proof: a detail spelled in a way the list does not know is not caught.
    """
    problems, repairs = [], []
    template = "".join(ch for ch in template if unicodedata.category(ch) != "Cf")
    # A blank becomes a run of NUL characters: positions stay the same, and
    # no pattern can read across a blank.
    marked = SLOT.sub(lambda m: "\x00" * len(m[0]), template)
    bare = marked.replace("\x00", " ")
    folded = fold(marked)
    seen: set[tuple[int, int]] = set()

    def report(start: int, end: int, what: str, advice: str) -> None:
        if any(s <= start and end <= e for s, e in seen):
            return
        seen.add((start, end))
        shown = snippet(bare, start, end, 14)
        problems.append(f"The translation has {what} that did not come from your message: {shown}")
        repairs.append(f"{advice} Found in {shown}.")

    no_digits = "Do not type digits. Use the blank for that fact, or add a fact for it."
    for match in re.finditer(r"\d[\d.,:/-]*\d|\d", marked):
        report(match.start(), match.end(), "a number", no_digits)
    for index, ch in enumerate(marked):
        if not ch.isascii() and unicodedata.category(ch).startswith("N"):
            report(index, index + 1, "a number", no_digits)

    month_names = [m.lower() for m in KIRUNDI_MONTHS.values()] + list(KIRUNDI_MONTH_VARIANTS)
    no_months = "Do not type month names. Use the blank of the date or month fact."
    for match in re.finditer(r"\b(?:" + "|".join(month_names) + r")\b", folded):
        report(match.start(), match.end(), "a month name", no_months)
    for match in re.finditer(r"\b([a-z]+)(?=(\s+)([a-z]+)\b)", folded):
        if match[1] + match[3] in month_names:
            report(match.start(), match.end() + len(match[2]) + len(match[3]), "a month name", no_months)
    # "ukwezi kwa mbere" is also "the first month", "isaha imwe" also "the same time".
    counted = "|".join(w for w in KIRUNDI_NUMBER_WORDS if w not in ("mbere", "imwe"))
    for match in re.finditer(r"\b[a-z]*kwezi\s+kwa\s+(?:" + counted + r")\b", folded):
        report(match.start(), match.end(), "a month written in words", no_months)

    no_weekdays = "Do not type weekdays. Use the blank of the weekday fact, or rephrase if this is not a weekday."
    weekday_forms = (
        r"\bku\s*wa[\s-]+(?:mbere|kabiri|gatatu|kane|gatanu|gatandatu)\b"
        r"|\bku\s*w'\s*(?:indwi|imana)\b|\bku\s+cumweru\b|\bku\s+wa\s+mungu\b"
    )
    for match in re.finditer(weekday_forms, folded):
        report(match.start(), match.end(), "a day of the week", no_weekdays)

    keep_name = "Use the blank for that fact. To keep an English name such as a program name, make it a copy fact."
    english = [w.lower() for w in ENGLISH_WEEKDAYS + ENGLISH_MONTHS if w not in ("May", "March")]
    for match in re.finditer(r"\b(?:" + "|".join(english) + r")s?\b", folded):
        report(match.start(), match.end(), "an English day or month", keep_name)
    for match in re.finditer(r"\b" + ENGLISH_SHORT_DATE_WORDS + r"\b\.?", marked):
        report(match.start(), match.end(), "an English day or month", keep_name)
    for match in re.finditer(r"\b(?:" + "|".join(OTHER_LANGUAGE_DATE_WORDS) + r")\b", folded):
        report(match.start(), match.end(), "a day or month in another language",
               "Use the blank of the date, month or weekday fact.")

    numbers = counted
    for match in re.finditer(r"\b(?:isaha|saa|amasaha|masaha)\s+(?:(?:ya|za|zi)\s+)?(?:" + numbers + r")\b", folded):
        report(match.start(), match.end(), "a clock time or a number of hours written in words",
               "Do not write times in words. Use the blank of the time fact. For a length of time, make the "
               'number a copy fact (a number in words takes "as": "two" as "2") and use its blank.')
    for match in re.finditer(r"\b(?:itariki|igenekerezo)\s+(?:(?:ya|zi)\s+)?(?:" + numbers + r")\b", folded):
        report(match.start(), match.end(), "a date written in words", "Use the blank of the date fact.")
    return GateResult("no_typed_details", not problems, problems, repairs)


def gate_time_of_day(template: str, facts: list[Fact], **_) -> GateResult:
    """Words such as "mu gitondo" must not contradict the clock time next to them."""
    problems, repairs = [], []
    by_number = {f.number: f for f in facts}
    for match in SLOT.finditer(template):
        fact = by_number.get(int(match[1]))
        if fact is None or fact.kind != "time" or not fact.exact_time:
            continue
        # Only the words between this blank and the next blank or clause break belong to it.
        left = re.split(r"\{\d+\}|[,;.:!?\n]", template[max(0, match.start() - 28):match.start()])[-1]
        right = re.split(r"\{\d+\}|[,;.:!?\n]", template[match.end():match.end() + 28])[0]
        nearby = fold(left + " | " + right)
        wrong = KIRUNDI_MORNING if fact.hour >= 12 else KIRUNDI_LATER
        hit = next((w for w in wrong if re.search(r"\b" + w + r"\b", nearby)), None)
        if hit:
            part = "afternoon or evening" if fact.hour >= 12 else "morning"
            problems.append(f"The time {quote(fact.text)} is in the {part}, but the translation's wording next to it says otherwise.")
            repairs.append(f"Remove or correct {quote(hit)} next to {{{fact.number}}}: {fact.render} is in the {part}.")
    return GateResult("time_of_day", not problems, problems, repairs)


def gate_same_order(source: str, template: str, facts: list[Fact], **_) -> GateResult:
    """Details stay where the message puts them.

    "Grade 3 trip is November 5. Grade 4 trip is November 12." must not come
    out with the two dates exchanged. Three rules: details of one kind keep
    the order of the message; a detail does not move into an earlier or later
    sentence; and a blank does not sit where another kind of detail belongs.
    """
    problems, repairs = [], []
    by_number = {f.number: f for f in facts}
    position: dict[int, int] = {}
    for index, number in enumerate(int(n) for n in SLOT.findall(template)):
        position.setdefault(number, index)
    in_message = [f for f in sorted(facts, key=lambda f: f.start) if f.has_blank and f.number in position and f.start >= 0]
    groups = (
        (("date", "month"), "dates"), (("time",), "times"), (("weekday",), "days of the week"),
        (("copy",), "names, numbers and contact details"),
    )
    for kinds, noun in groups:
        ordered = [f for f in in_message if f.kind in kinds]
        placed = sorted(ordered, key=lambda f: position[f.number])
        if [f.number for f in ordered] != [f.number for f in placed]:
            problems.append(f"The {noun} are in a different order in the translation than in your message, so one may be attached to the wrong thing.")
            repairs.append(
                f"Keep the {noun} in the order of the message: "
                + ", ".join(f"{{{f.number}}} ({f.text})" for f in ordered) + "."
            )

    spans = [(f.start, f.end) for f in facts if f.start >= 0]
    starts = sentence_starts(source, spans)
    sentence_of = {f.number: bisect.bisect_right(starts, f.start) - 1 for f in in_message}
    in_translation = sorted(in_message, key=lambda f: position[f.number])
    for earlier, later in zip(in_translation, in_translation[1:]):
        if sentence_of[later.number] < sentence_of[earlier.number]:
            problems.append(
                "A detail from a later sentence of your message comes before a detail from an earlier "
                "sentence in the translation, so one may be attached to the wrong thing."
            )
            repairs.append(
                f"Keep every blank in the sentence it belongs to, and the sentences in the order of the "
                f"message: {{{later.number}}} ({later.text}) must come before {{{earlier.number}}} ({earlier.text})."
            )
            break

    masked = "".join(" " if any(s <= i < e for s, e in spans) else ch for i, ch in enumerate(source))
    message_has_bare_line = any(not re.search(r"[^\W\d_]", part) for part in _sentences(masked))
    if not message_has_bare_line:
        for part in _sentences(SLOT.sub("\x00", template)):
            if part.count("\x00") >= 2 and not re.search(r"[^\W\d_]", part):
                problems.append("The translation has a bare list of details with no words around them.")
                repairs.append("Put each blank inside the sentence it belongs to; do not list blanks on their own.")
                break

    for match in SLOT.finditer(template):
        fact = by_number.get(int(match[1]))
        word = re.search(r"([^\W\d_]+)\W*$", fold(template[:match.start()]))
        if fact is None or not word:
            continue
        if word[1] in ("isaha", "saa") and fact.kind in ("date", "month"):
            wanted = "clock time"
        elif word[1] in ("itariki", "igenekerezo") and fact.kind == "time":
            wanted = "date"
        else:
            continue
        problems.append(f"{_plain(fact)} is written where a {wanted} belongs in the translation.")
        repairs.append(f"{{{fact.number}}} ({fact.text}) follows {quote(word[1])}, which introduces a {wanted}. Put the right blank there.")
    return GateResult("same_order", not problems, problems, repairs)


def _sentences(text: str) -> list[str]:
    return [s.strip() for s in re.split(r"(?<=[.!?])\s+|\n+", text) if s.strip()]


def gate_translated(source: str, template: str, facts: list[Fact], **_) -> GateResult:
    without_links = lambda text: re.sub(URL_PATTERN, " ", text, flags=re.I)
    body = SLOT.sub(" ", template)
    if not body.strip():
        return GateResult("translated", True)
    source_words = set(re.findall(r"[a-z']+", fold(without_links(source))))
    problems, repairs = [], []
    for sentence in _sentences(body):
        tokens = re.findall(r"[^\W\d_]+(?:['’][^\W\d_]+)*", without_links(sentence))
        letters = "".join(tokens)
        shouting = len(letters) >= 8 and sum(c.isupper() for c in letters) * 10 >= len(letters) * 7
        # A word with a capital inside a sentence is a name or a kept term,
        # unless the whole sentence is in capitals.
        words = [fold(t) for i, t in enumerate(tokens) if shouting or i == 0 or not t[:1].isupper()]
        capitalised = [fold(t) for t in tokens[1:] if t[:1].isupper()] if not shouting else []
        marker_hits = sum(1 for word in words if word in ENGLISH_MARKERS)
        copied = [word for word in words if word in source_words]
        copied_long = [word for word in copied if len(word) >= 3]
        # A run of capitalised English words copied from the message
        # ("Please Keep Your Child Home") is English too, when it has common words in it.
        title_hits = sum(1 for word in capitalised if word in ENGLISH_MARKERS)
        english = (
            (words and marker_hits >= 2 and marker_hits * 5 >= len(words))
            or (len(copied_long) >= 2 and len(copied) * 2 >= len(words))
            or title_hits >= 3
        )
        if english:
            shown = sentence if len(sentence) <= 70 else sentence[:67].rstrip() + "..."
            problems.append(f"Part of the translation is still in English: {quote(shown)}")
            repairs.append(
                f"Translate this into Kirundi, or remove it if the message does not say it: {quote(shown)} "
                "To keep an English name or term on purpose, make it a copy fact."
            )
    return GateResult("translated", not problems, problems, repairs)


def gate_kirundi_spelling(source: str, template: str, **_) -> GateResult:
    strip = lambda text: re.sub(URL_PATTERN, " ", text, flags=re.I)
    kept_from_message = set()
    for word in re.findall(r"[a-z']+", fold(strip(source))):
        kept_from_message.update({word, re.sub(r"'s$|'$", "", word)})
    flagged: list[str] = []
    for match in re.finditer(r"[^\W\d_]+(?:['’][^\W\d_]+)*", strip(SLOT.sub(" ", template))):
        word = fold(match[0])
        if word in kept_from_message or word in flagged:
            continue  # a name or term copied from the message
        if KINYARWANDA_SPELLING.search(word) or KINYARWANDA_HAVE.fullmatch(word) or KINYARWANDA_FUTURE.fullmatch(word):
            flagged.append(word)
    problems = [
        f"The word {quote(word)} is spelled the Kinyarwanda way. Kinyarwanda is a neighbouring language, not Kirundi."
        for word in flagged[:5]
    ]
    repairs = [
        f"Rewrite {quote(word)} in Kirundi: Kirundi writes c, j, sh, vy where Kinyarwanda writes "
        "cy, jy, shy, by, -fise where Kinyarwanda writes -fite, and the future with -zo- "
        "(izoba) where Kinyarwanda uses -za- (izaba). Then re-read the whole translation for "
        "other Kinyarwanda words."
        for word in flagged[:5]
    ]
    return GateResult("kirundi_spelling", not flagged, problems, repairs)


def gate_questions_kept(source: str, template: str, facts: list[Fact], **_) -> GateResult:
    inside_facts = sum(f.text.count("?") for f in facts if f.start >= 0)
    asked, kept = source.count("?") - inside_facts, SLOT.sub(" ", template).count("?")
    if kept >= asked:
        return GateResult("questions_kept", True)
    lost = asked - kept
    noun = "question" if lost == 1 else "questions"
    return GateResult(
        "questions_kept", False,
        [f"Your message asks {asked} {'question' if asked == 1 else 'questions'}, but the translation asks {kept}."],
        [f"Restore the {lost} missing {noun} as {noun}, ending with a question mark."],
    )


def gate_length_sane(source: str, translation: str, **_) -> GateResult:
    source_length, translation_length = len(source.strip()), len(translation.strip())
    if source_length < 40 or translation_length == 0:
        return GateResult("length_sane", True)
    ratio = translation_length / source_length
    if ratio < 0.4:
        return GateResult(
            "length_sane", False,
            ["The translation is much shorter than your message, so part of it may be missing."],
            ["Translate every sentence of the message; do not summarise or drop any."],
        )
    if ratio > 3.5:
        return GateResult(
            "length_sane", False,
            ["The translation is much longer than your message, so something may have been added."],
            ["Translate only what the message says; remove explanations and additions."],
        )
    return GateResult("length_sane", True)


def gate_clean_text(source: str, translation: str, **_) -> GateResult:
    problems, repairs = [], []
    source_labels = {m.lower() for m in re.findall(r"(?mi)^\s*([a-z]+)\s*:", source)}
    for match in re.finditer(r"(?mi)^\s*([a-z]+)\s*:", translation):
        label = match[1].lower()
        if label in META_LABELS and label not in source_labels:
            problems.append(f"The translation contains a label ({quote(match[0].strip())}) that is not part of the message.")
            repairs.append(f"Remove the label {quote(match[0].strip())} and anything that is a comment, not message text.")
    markup = re.compile(r"\*\*|__|`|^\s*#{1,6}\s", re.M)
    bullets = re.compile(r"^\s*(?:[*•-]|\d+[.)])\s+\S", re.M)
    if (markup.search(translation) and not markup.search(source)) or \
            (bullets.search(translation) and not bullets.search(source)):
        problems.append("The translation contains formatting symbols or list marks that are not in your message.")
        repairs.append(
            "Remove Markdown symbols and list marks (asterisks, underscores, backticks, heading "
            "marks, bullets) that the English message does not have."
        )
    if any(match[0] not in source for match in re.finditer(r"\{[^{}]*\}", translation)):
        problems.append("The translation still contains an unfilled blank.")
        repairs.append("Every blank must be a fact number in braces, such as {1}. Remove any other braces.")
    for opening, closing in (("(", ")"), ("[", "]")):
        if translation.count(opening) != translation.count(closing) and source.count(opening) == source.count(closing):
            problems.append(f"The translation has a {opening} without its {closing}, or the other way round.")
            repairs.append(f"Make every {opening} in the translation have a matching {closing}.")
    if translation.count('"') % 2 and not source.count('"') % 2:
        problems.append("The translation has a quotation mark without its partner.")
        repairs.append("Make every opening quotation mark in the translation have a closing one.")
    hidden = {ch for ch in translation if unicodedata.category(ch) == "Cf"} - set(source)
    if hidden:
        problems.append("The translation contains invisible characters that are not in your message.")
        repairs.append("Remove zero-width and other invisible characters from the translation.")
    return GateResult("clean_text", not problems, problems, repairs)


def gate_read_back(meaning: str, facts: list[Fact], **_) -> GateResult:
    """The read-back is the only part the writer can read, so it must not contradict the facts.

    It must be there, and any weekday, month or a.m./p.m. clock time it
    states must be one the facts hold.
    """
    if not meaning.strip():
        return GateResult("read_back", False, ["The plain-English read-back is missing."],
                          ['Write "meaning": one or two English sentences saying what the Kirundi says.'])
    problems, repairs = [], []
    days = {f.weekday for f in facts if f.kind == "weekday"}
    months = {f.month for f in facts if f.kind in ("date", "month")}
    kept = " ".join(f.text for f in facts if f.kind in ("copy", "word")).lower()
    for index, name in enumerate(ENGLISH_WEEKDAYS, start=1):
        if re.search(r"\b" + name + r"s?\b", meaning, flags=re.I) and index not in days and name.lower() not in kept:
            problems.append(f"The read-back says {name}, but your message does not.")
            repairs.append(f'The read-back names {name}, which is in no fact. Re-read the Kirundi and correct it or "meaning".')
    for index, name in enumerate(ENGLISH_MONTHS, start=1):
        if name == "May":
            continue
        if re.search(r"\b" + name + r"\b", meaning) and index not in months and name.lower() not in kept:
            problems.append(f"The read-back says {name}, but your message does not.")
            repairs.append(f'The read-back names {name}, which is in no fact. Re-read the Kirundi and correct it or "meaning".')
    exact = {f.expected for f in facts if f.kind == "time" and f.exact_time and f.expected}
    if exact:
        for match in re.finditer(r"\b\d{1,2}(?:[:.]\d{2})?\s*(?:[ap]\.?\s?m\b\.?|in the (?:morning|afternoon|evening)|at night)", meaning, flags=re.I):
            reading = _parse_time_text(match[0])
            if reading and reading[2]:
                stated = (reading[0] % 12 + (12 if reading[2] == "pm" else 0), reading[1])
                if stated not in exact:
                    problems.append(f"The read-back says {quote(match[0])}, which is not a time in your message.")
                    repairs.append(f'The read-back states {quote(match[0])}, which matches no time fact. Correct the Kirundi or "meaning".')
    return GateResult("read_back", not problems, problems, repairs)


def _sentences_of(text: str) -> list[str]:
    return [part for part in re.split(r"(?<=[.!?])\s+|\n+", text) if part.strip()]


def _english_negations(source: str, facts: list[Fact]) -> tuple[int, int, str]:
    """(sentences that must stay negative, sentences that may be negative, first negative word)."""
    spans = [(f.start, f.end) for f in facts if f.start >= 0]
    spans += [m.span() for m in ENGLISH_SOFT_NEGATION.finditer(source)]  # matched before facts are blanked
    masked = "".join(" " if any(s <= i < e for s, e in spans) else ch for i, ch in enumerate(source))
    strict = soft = 0
    first = ""
    for sentence in _sentences_of(masked):
        hard = ENGLISH_NEGATION.search(sentence)
        if hard:
            strict += 1
            first = first or hard[0]
    soft = sum(1 for sentence in _sentences_of(source) if ENGLISH_NEGATION.search(sentence))
    return strict, soft, first


def _is_kirundi_negative(word: str) -> bool:
    return bool(KIRUNDI_NEGATION.match(word)) and word not in KIRUNDI_NOT_NEGATION


def gate_negation_kept(source: str, template: str, facts: list[Fact], meaning: str = "", **_) -> GateResult:
    """A "not" in the message must survive. A dropped negative reverses the meaning.

    Counted per sentence: the Kirundi needs at least as many negative sentences as the English.
    Phrases that Kirundi often says positively ("no later than", "don't forget") are not counted.
    """
    strict, _allowed, first = _english_negations(source, facts)
    kirundi = sum(
        1 for sentence in _sentences_of(SLOT.sub(" ", template))
        if any(_is_kirundi_negative(fold(w)) for w in re.findall(r"[^\W\d_]+", sentence))
    )
    problems, repairs = [], []
    if kirundi < strict:
        problems.append(
            f"Your message has {strict} negative sentence(s), such as {quote(first)}, but the Kirundi has only "
            f"{kirundi}. A dropped \"not\" reverses the meaning."
        )
        repairs.append(
            f"Check every sentence of the Kirundi against the English. Each sentence that says {quote(first)} or "
            "another negative must be negative in the Kirundi too (nti-, nta-, -ta-, -da-). Do not add a "
            "negative anywhere the English has none."
        )
    # An added negative is not refused: Kirundi often says a positive English word negatively
    # ("absent" as "ntiyaje", did not come; "sick" as "ntameze neza", not well).
    if strict and meaning.strip() and not ENGLISH_NEGATION.search(meaning):
        problems.append(f"Your message says {quote(first)}, but the English read-back of the Kirundi has no negative.")
        repairs.append(
            "Re-read the Kirundi and write the read-back from it. If the Kirundi is negative, the read-back "
            "says so; if it is not, the Kirundi dropped the negative and must be fixed."
        )
    return GateResult("negation_kept", not problems, problems, repairs)


GATE_ORDER = (
    "not_empty", "blanks", "no_typed_details", "same_order", "time_of_day",
    "translated", "kirundi_spelling", "questions_kept", "negation_kept", "length_sane", "clean_text", "read_back",
)
GATES: dict[str, Callable[..., GateResult]] = {
    "not_empty": gate_not_empty,
    "blanks": gate_blanks,
    "no_typed_details": gate_no_typed_details,
    "same_order": gate_same_order,
    "time_of_day": gate_time_of_day,
    "translated": gate_translated,
    "kirundi_spelling": gate_kirundi_spelling,
    "questions_kept": gate_questions_kept,
    "length_sane": gate_length_sane,
    "clean_text": gate_clean_text,
    "read_back": gate_read_back,
    "negation_kept": gate_negation_kept,
}


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------

def _blanks_listing(facts: list[Fact]) -> list[dict]:
    return [
        {"blank": f"{{{f.number}}}", "quotes": f.text, "is": f.kind, "will_be_written_as": f.render}
        for f in facts if f.has_blank
    ]


def run_scan(payload: object) -> dict:
    source = _require_text(payload, "source")
    review = review_facts(source, _read_facts(payload))
    return {
        "version": VERSION,
        "passed": not review.repairs and not review.questions,
        "repairs": review.repairs,
        "questions": review.questions if not review.repairs else [],
        "blanks": _blanks_listing(review.facts) if not review.repairs else [],
    }


def run_check(payload: object) -> dict:
    source = _require_text(payload, "source")
    template = _require_text(payload, "template", allow_blank=True)
    meaning = payload.get("meaning", "") if isinstance(payload, dict) else ""
    if not isinstance(meaning, str):
        raise UsageError('"meaning" must be text.')
    review = review_facts(source, _read_facts(payload))

    if review.repairs or review.questions:
        # The facts are not settled, so nothing built on them can pass.
        repairs = list(review.repairs)
        if not repairs:
            repairs = ["Ask the writer this before translating, and then record the answer in the facts: " + q
                       for q in review.questions]
        problems = ["The details in your message could not be confirmed, so the translation was not checked."]
        if review.questions and not review.repairs:
            problems = list(review.questions)
        return _result(False, problems, repairs, [], "", meaning, facts_ok=False)

    translation = fill(template, review.facts)
    files_dir = payload.get("files_dir") if isinstance(payload, dict) else None
    if files_dir is not None and not isinstance(files_dir, str):
        raise UsageError('"files_dir" must be the path of a folder.')
    context = dict(source=source, template=template, translation=translation, facts=review.facts, meaning=meaning)
    results = [GATES[gate_id](**context) for gate_id in GATE_ORDER]
    problems = [p for r in results for p in r.problems]
    repairs = [p for r in results for p in r.repairs]
    passed = all(r.passed for r in results)
    result = _result(passed, problems, repairs, results, translation, meaning, facts_ok=True)
    if files_dir:
        _remove_old_files(files_dir)  # a failed check must never leave an earlier message to download
    if _may_write_files(passed, files_dir):
        try:
            result["files"] = write_files(files_dir, translation, meaning)
        except OSError as exc:
            result["files_error"] = f"The files could not be written ({type(exc).__name__}). Send the reply alone."
    return result


def _result(passed: bool, problems: list[str], repairs: list[str], results: list[GateResult],
            translation: str, meaning: str, facts_ok: bool) -> dict:
    if passed:
        status = STATUS_PASS
    elif len(problems) == 1:
        status = STATUS_FAIL_ONE
    else:
        status = STATUS_FAIL_MANY.format(count=len(problems))
    return {
        "version": VERSION,
        "passed": passed,
        "facts_accepted": facts_ok,
        "status_line": status,
        "problems": problems,
        "repairs": repairs,
        "gates": [{"id": r.id, "passed": r.passed} for r in results],
        "translation": translation,
        "reply": build_reply(translation, meaning, status, problems, passed),
        "files": [],
    }


def build_reply(translation: str, meaning: str, status: str, problems: list[str], passed: bool) -> str:
    """The exact text to show the writer."""
    # The read-back is the model's own words. Nothing in it may look like part of this reply's frame.
    plain = re.sub(r"(?i)\bstatus\s*:", "status -", " ".join(re.sub(r"[*`_#]", "", meaning).split()))
    meaning_line = f"**What I meant it to say:** {plain}" if plain else ""
    if passed:
        lines = ["**Kirundi message**", "", "```text", translation.strip(), "```", ""]
        if meaning_line:
            lines += [meaning_line, ""]
        lines.append(f"**Status:** {status}")
        return "\n".join(lines)
    # Not ready: the warning comes first, and the box is labelled a draft.
    lines = [f"**Status:** {status}", "", "**Please check:**"]
    lines += [f"- {problem}" for problem in problems]
    lines += ["", "Ask me to try again, or ask a Kirundi speaker to read it before you send it."]
    if translation.strip():
        lines += ["", "**Draft, not ready to send**", "", "```text", translation.strip(), "```"]
        if meaning_line:
            lines += ["", meaning_line]
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Files to copy, download, save as a picture or print. Written only for a
# translation that passed, so an unchecked draft can never be downloaded.
# ---------------------------------------------------------------------------

CARD_NAME = "Kirundi-message.html"


def _may_write_files(passed: bool, files_dir: Optional[str]) -> bool:
    return bool(passed and files_dir)


TEXT_NAME = "Kirundi-message.txt"


def _read_back(meaning: str) -> str:
    return re.sub(r"(?i)\bstatus\s*:", "status -", " ".join(re.sub(r"[*`_#]", "", meaning).split()))


def _remove_old_files(folder: str) -> None:
    for name in (CARD_NAME, TEXT_NAME):
        try:
            os.remove(os.path.join(folder, name))
        except OSError:
            pass


def write_files(folder: str, translation: str, meaning: str) -> list[str]:
    """Writes the text file and the card, and returns their paths."""
    os.makedirs(folder, exist_ok=True)
    text_path = os.path.join(folder, TEXT_NAME)
    card_path = os.path.join(folder, CARD_NAME)
    with open(text_path, "w", encoding="utf-8", newline="\n") as handle:
        handle.write(translation.strip() + "\n")
    with open(card_path, "w", encoding="utf-8", newline="\n") as handle:
        handle.write(build_card(translation, meaning))
    return [card_path, text_path]


def _json_for_html(value: object) -> str:
    """JSON that can sit inside a script element: no <, > or & can appear in it."""
    return (json.dumps(value, ensure_ascii=True)
            .replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026"))


def build_card(translation: str, meaning: str) -> str:
    """One self-contained page: the message, buttons to copy, download, save and print, and a three-step guide.

    It loads nothing from the internet. The message is placed in the page as
    escaped text and handed to the script as JSON that cannot close the
    script element, so nothing in a message can run as code.
    """
    kirundi = translation.strip()
    english = _read_back(meaning)
    data = _json_for_html({"kirundi": kirundi, "english": english})
    english_block = (
        '<section class="en"><h2>What it says in English</h2>'
        f'<p id="english">{html.escape(english)}</p>'
        '<button type="button" class="ghost" data-action="copy-english">Copy the English</button></section>'
        if english else ""
    )
    return CARD_TEMPLATE.format(
        kirundi=html.escape(kirundi), english_block=english_block, status=html.escape(STATUS_PASS),
        data=data, version=VERSION,
    )


CARD_TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Message for the family</title>
<style>
  :root {{ --ink:#1d2433; --muted:#5b6475; --line:#d9dee7; --card:#ffffff; --bg:#f3f5f8; --go:#1f6f4a; --go-bg:#e6f4ec; --accent:#23409a; }}
  * {{ box-sizing:border-box; }}
  body {{ margin:0; background:var(--bg); color:var(--ink); font:17px/1.5 -apple-system, "Segoe UI", Roboto, Helvetica, Arial, sans-serif; }}
  main {{ max-width:720px; margin:0 auto; padding:24px 16px 48px; }}
  h1 {{ font-size:22px; margin:0 0 12px; }}
  h2 {{ font-size:15px; text-transform:uppercase; letter-spacing:.06em; color:var(--muted); margin:0 0 8px; }}
  .status {{ background:var(--go-bg); color:var(--go); border-radius:10px; padding:10px 14px; font-size:15px; margin-bottom:16px; }}
  .card {{ background:var(--card); border:1px solid var(--line); border-radius:14px; padding:20px; margin-bottom:16px; }}
  #kirundi {{ font-size:22px; line-height:1.55; white-space:pre-wrap; word-wrap:break-word; margin:0 0 18px; }}
  .buttons {{ display:grid; grid-template-columns:repeat(auto-fit, minmax(150px, 1fr)); gap:10px; }}
  button {{ font:inherit; font-size:16px; border-radius:10px; padding:12px 14px; cursor:pointer; border:1px solid var(--line); background:#fff; color:var(--ink); }}
  button.main {{ background:var(--accent); color:#fff; border-color:var(--accent); font-weight:600; grid-column:1 / -1; font-size:18px; padding:14px; }}
  button.ghost {{ margin-top:8px; }}
  button:focus-visible {{ outline:3px solid #8aa4f0; outline-offset:2px; }}
  .steps {{ display:grid; grid-template-columns:repeat(3, 1fr); gap:10px; }}
  .step {{ background:var(--card); border:1px solid var(--line); border-radius:12px; padding:14px; text-align:center; }}
  .num {{ display:inline-flex; width:34px; height:34px; border-radius:50%; background:var(--accent); color:#fff; align-items:center; justify-content:center; font-weight:700; margin-bottom:6px; }}
  .en p {{ margin:0; color:var(--ink); }}
  .note {{ color:var(--muted); font-size:14px; }}
  #done {{ position:fixed; left:50%; bottom:24px; transform:translateX(-50%); background:var(--ink); color:#fff; padding:10px 18px; border-radius:999px; opacity:0; transition:opacity .2s; pointer-events:none; }}
  #done.show {{ opacity:1; }}
  @media (max-width:520px) {{ .steps {{ grid-template-columns:1fr; }} #kirundi {{ font-size:20px; }} }}
  @media print {{ body {{ background:#fff; }} .buttons, .steps, button, #done, .note, .status, .en, h1, h2 {{ display:none !important; }} .card {{ border:none; padding:0; }} }}
</style>
</head>
<body>
<main>
  <h1>Message for the family</h1>
  <div class="status" role="status">{status}</div>
  <section class="card">
    <h2>Kirundi message</h2>
    <p id="kirundi">{kirundi}</p>
    <div class="buttons">
      <button type="button" class="main" data-action="copy">Copy the message</button>
      <button type="button" data-action="text">Download as text</button>
      <button type="button" data-action="picture">Save as a picture</button>
      <button type="button" data-action="print">Print or save as PDF</button>
    </div>
  </section>
  <section class="steps" aria-label="How to send it">
    <div class="step"><div class="num">1</div><div>Press <b>Copy the message</b></div></div>
    <div class="step"><div class="num">2</div><div>Paste it into your text, email or school app</div></div>
    <div class="step"><div class="num">3</div><div>Send it</div></div>
  </section>
  <section class="card">{english_block}</section>
  <p class="note">Made by Translate {version}. Dates, times and numbers were filled in by the checker; the wording has not been read by a Kirundi speaker.</p>
</main>
<div id="done" aria-live="polite"></div>
<script id="message-data" type="application/json">{data}</script>
<script>
(function () {{
  var data = JSON.parse(document.getElementById("message-data").textContent);
  function tell(text) {{
    var box = document.getElementById("done");
    box.textContent = text; box.className = "show";
    setTimeout(function () {{ box.className = ""; }}, 2200);
  }}
  function copy(text, label) {{
    function fallback() {{
      var area = document.createElement("textarea");
      area.value = text; area.setAttribute("readonly", ""); area.style.position = "fixed"; area.style.opacity = "0";
      document.body.appendChild(area); area.select();
      var ok = false; try {{ ok = document.execCommand("copy"); }} catch (e) {{}}
      document.body.removeChild(area);
      tell(ok ? label : "Select the message and copy it yourself");
    }}
    if (navigator.clipboard && window.isSecureContext) {{
      navigator.clipboard.writeText(text).then(function () {{ tell(label); }}, fallback);
    }} else {{ fallback(); }}
  }}
  function save(blob, name) {{
    var link = document.createElement("a");
    link.href = URL.createObjectURL(blob); link.download = name;
    document.body.appendChild(link); link.click(); document.body.removeChild(link);
    setTimeout(function () {{ URL.revokeObjectURL(link.href); }}, 4000);
    tell("Saved " + name);
  }}
  function lines(ctx, text, width) {{
    var out = [];
    text.split("\\n").forEach(function (paragraph) {{
      var line = "";
      paragraph.split(" ").forEach(function (word) {{
        var trial = line ? line + " " + word : word;
        if (ctx.measureText(trial).width > width && line) {{ out.push(line); line = word; }} else {{ line = trial; }}
      }});
      out.push(line);
    }});
    return out;
  }}
  function picture() {{
    var scale = 2, width = 1080, pad = 72, size = 44, gap = 66;
    var canvas = document.createElement("canvas"), ctx = canvas.getContext("2d");
    ctx.font = size + "px -apple-system, Segoe UI, Roboto, Helvetica, Arial, sans-serif";
    var rows = lines(ctx, data.kirundi, width - pad * 2);
    var height = pad * 2 + rows.length * gap;
    if (height * scale > 16000) {{ tell("Too long for a picture. Use Download as text or Print."); return; }}
    canvas.width = width * scale; canvas.height = height * scale;
    ctx.scale(scale, scale);
    ctx.fillStyle = "#ffffff"; ctx.fillRect(0, 0, width, height);
    ctx.fillStyle = "#1d2433"; ctx.textBaseline = "top";
    ctx.font = size + "px -apple-system, Segoe UI, Roboto, Helvetica, Arial, sans-serif";
    rows.forEach(function (row, i) {{ ctx.fillText(row, pad, pad + i * gap); }});
    canvas.toBlob(function (blob) {{
      if (blob) {{ save(blob, "Kirundi-message.png"); }} else {{ tell("The picture could not be made. Use Download as text or Print."); }}
    }}, "image/png");
  }}
  document.addEventListener("click", function (event) {{
    var action = event.target && event.target.getAttribute && event.target.getAttribute("data-action");
    if (action === "copy") copy(data.kirundi, "Copied. Now paste it into your message.");
    else if (action === "copy-english") copy(data.english, "English copied");
    else if (action === "text") save(new Blob([data.kirundi + "\\n"], {{ type: "text/plain;charset=utf-8" }}), "Kirundi-message.txt");
    else if (action === "picture") picture();
    else if (action === "print") window.print();
  }});
}})();
</script>
</body>
</html>
"""


def main(argv: list[str], stdin: TextIO, stdout: TextIO) -> int:
    commands = {"scan": run_scan, "check": run_check}

    def emit(body: dict, code: int) -> int:
        json.dump(body, stdout, ensure_ascii=False, indent=2)
        stdout.write("\n")
        return code

    if len(argv) != 1 or argv[0] not in commands:
        return emit({"error": 'Use one command: "scan" or "check".', "version": VERSION}, EXIT_USAGE)
    try:
        raw = stdin.read()
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise UsageError(f"The input is not valid JSON ({exc.msg}).") from exc
        result = commands[argv[0]](payload)
    except UsageError as exc:
        return emit({"error": str(exc), "version": VERSION}, EXIT_USAGE)
    except Exception as exc:  # a crash must never look like a pass
        return emit(
            {"error": f"The checker failed ({type(exc).__name__}). Nothing was checked.", "version": VERSION},
            EXIT_USAGE,
        )
    return emit(result, EXIT_OK if result["passed"] else EXIT_PROBLEMS)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:], sys.stdin, sys.stdout))
