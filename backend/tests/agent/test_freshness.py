"""The rule that decides when to search the web before the model answers."""

import pytest

from app.agent.freshness import needs_fresh_information

CUTOFF = 2024


@pytest.mark.parametrize(
    "text",
    [
        "who won 2026 fifa world cup",
        "who won the fifa world cup 2026",
        "What happened in the 2025 elections?",
        "what is the latest version of python?",
        "who is the current prime minister of the UK?",
        "what is the price of bitcoin today?",
        "any news about OpenAI this week?",
        "who won the champions league final this year?",
        "What was the score in last night's match?",
        "when was the newest iPhone released?",
        "Who is the CEO of Twitter right now?",
        "recent developments in fusion energy",
        "Who is the winner of the Ballon d'Or?",
    ],
)
def test_fresh_questions_search(text: str) -> None:
    assert needs_fresh_information(text, cutoff_year=CUTOFF)


@pytest.mark.parametrize(
    "text",
    [
        "what is harness?",
        "what is 17 * 23",
        "hello, how are you?",
        "explain how photosynthesis works",
        "write a python function to reverse a string",
        "tell me about the one digital security assement review",
        "who won the 2018 world cup?",
        "how many km is 26.2 miles",
        # Other tools answer these better than a web search.
        "what is the current temperature in lahore",
        "what's the weather like in Paris today?",
        "what time is it?",
        "what is the current date and time?",
        "If it's 3pm in Karachi, what time is it in New York?",
        "summarize https://example.com/news/latest",
        "",
    ],
)
def test_other_questions_do_not_search(text: str) -> None:
    assert not needs_fresh_information(text, cutoff_year=CUTOFF)


def test_cutoff_year_moves_the_line() -> None:
    assert needs_fresh_information("who won in 2025?", cutoff_year=2024)
    assert not needs_fresh_information("who won in 2025?", cutoff_year=2025)
