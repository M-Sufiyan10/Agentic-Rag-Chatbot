"""A fixed rule for "does this message need facts newer than the model's training data?".

Small local models don't reliably decide this themselves: asked "who won the
2026 FIFA World Cup?", a 4B model answers from memory that the tournament
hasn't happened, and never calls ``web_search``, even when told today's date
(see ``docs/learning/05-auto-retrieval.md``). So the server decides instead,
with a rule that is predictable and testable, and runs the search before the
model answers. The rule will sometimes miss (no signal word) or search
needlessly; the model can still call ``web_search`` itself in both cases.
"""

import re

_YEAR = re.compile(r"\b(19\d{2}|20\d{2})\b")
_URL = re.compile(r"https?://", re.IGNORECASE)

# Other tools answer these better than a web search: get_weather, current_datetime,
# convert_time and fetch.
_BETTER_TOOL = re.compile(
    r"\b(weather|temperature|forecast|raining|humidity|what time|time is it|time zone|timezone"
    r"|current (date|time)|today'?s date|what day is|what date is)\b",
    re.IGNORECASE,
)

_RECENCY = re.compile(
    r"\b(latest|newest|current|currently|today|tonight|yesterday|right now|as of now"
    r"|this (week|month|year|season)|last (night|week|month)|news|recent|recently"
    r"|prices?|stock|scores?|won|wins|winner|champions?|released?|election)\b",
    re.IGNORECASE,
)


def needs_fresh_information(text: str, *, cutoff_year: int) -> bool:
    """Whether answering ``text`` likely needs information from after ``cutoff_year``.

    A message naming a year decides by the year alone: after the cutoff means
    search, and only earlier years ("who won the 2018 world cup?") means the
    model already knows. Otherwise recency words (latest, current, news, price,
    won, ...) decide. Weather, time, time-zone and URL questions never search:
    other tools handle them.
    """
    if not text.strip() or _URL.search(text) or _BETTER_TOOL.search(text):
        return False
    years = [int(year) for year in _YEAR.findall(text)]
    if years:
        return any(year > cutoff_year for year in years)
    return _RECENCY.search(text) is not None
