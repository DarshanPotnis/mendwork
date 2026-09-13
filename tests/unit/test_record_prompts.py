"""Prompts read one line per question, in a terminal or from piped input."""

import io

from mendwork.apps.cli.record_prompts import LinePrompter


class Terminal(io.StringIO):
    """Input a person types; the terminal already shows their Enter."""

    def isatty(self) -> bool:
        return True


def test_piped_answers_are_echoed_so_every_question_starts_its_own_line() -> None:
    shown = io.StringIO()
    prompter = LinePrompter(io.StringIO("portal_url\n\n"), shown)

    assert prompter.ask("  start URL · input name", "start_url") == "portal_url"
    assert prompter.ask("  email · input name", "email") == ""
    assert shown.getvalue() == (
        "  start URL · input name [start_url]: portal_url\n  email · input name [email]: \n"
    )


def test_a_last_answer_without_a_newline_still_ends_its_line() -> None:
    shown = io.StringIO()

    assert LinePrompter(io.StringIO("portal_url"), shown).ask("name", "start_url") == "portal_url"
    assert shown.getvalue() == "name [start_url]: portal_url\n"


def test_the_end_of_input_means_the_default() -> None:
    shown = io.StringIO()

    assert LinePrompter(io.StringIO(""), shown).ask("name", "start_url") == ""
    assert shown.getvalue() == "name [start_url]: (no answer; using the default)\n"


def test_a_terminal_answer_is_not_echoed_a_second_time() -> None:
    shown = io.StringIO()

    assert LinePrompter(Terminal("portal_url\n"), shown).ask("name", "start_url") == "portal_url"
    assert shown.getvalue() == "name [start_url]: "
