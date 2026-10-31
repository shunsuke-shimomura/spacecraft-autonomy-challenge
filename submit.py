"""Submit this project to an evaluation server, if your event provides one.

    uv run submit.py              # practice evaluation: wait for the result, show it
    uv run submit.py --final      # final submission: show the receipt
    uv run submit.py --status ID  # show a submission, waiting for its result
    uv run submit.py --board      # show the leaderboard and whether finals are open

The archive is `submission.zip` as `autonomy-challenge pack` builds it: the
`submission/` directory and `pyproject.toml`. The server address and token come from
`--server` and `--token`, or from the environment variables `EVALUATION_SERVER_URL`
and `EVALUATION_SERVER_TOKEN`; an address without a path, such as
`http://<host>:<port>`, means its API at `http://<host>:<port>/api/v1`. You are
identified by `git config user.email` and `git config user.name` unless
`--participant-id` and `--participant-name` say otherwise.

Every accepted submission is appended to `.submissions.log` in this directory, with its
id, kind, time, and the server address. `--status` takes a full id, or the start or the
end of one, such as the 8 characters of the leaderboard; it looks for it in the log
first and then among your submissions at the server.

The server speaks a small HTTP protocol, relative to its base URL:

    POST submissions?kind=practice|final&participant_id=...&participant_name=...
        body: submission.zip; answers with a receipt: id, status, sha256, ...
    GET  submissions?participant_id=...
        the receipts of the submissions of one participant, as a list
    GET  submissions/{id}
        the receipt with its current status; a completed practice submission also
        carries the result document of `autonomy-challenge check --json`
        under "result" and, if the server has fixed practice cases, the score on
        them alone, by which the practice leaderboard ranks, under "fixed_score"
    GET  board/practice?limit=...&participant_id=...
        the practice leaderboard, if the server has one: "total" and "rows" of
        rank, name, score, submitted_at, submission (the last 8 characters of the
        id), and count
    GET  board/final
        the final submissions, if the server has this board: "window", null if the
        server has no final submission window, with its phase (not_started, open,
        or closed), now, opened_at, closes_at, closed_at, and remaining_s, and,
        once "published" is true, "rows" of rank, name, score, and submitted_at

Errors are JSON objects with a "detail" message.
"""

import argparse
import http.client
import json
import os
import re
import subprocess
import sys
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable, Iterable, Sequence
from datetime import datetime
from pathlib import Path
from typing import Any

PROJECT_DIR = Path(__file__).resolve().parent
URL_VARIABLE = "EVALUATION_SERVER_URL"
TOKEN_VARIABLE = "EVALUATION_SERVER_TOKEN"
SUBMISSION_LOG = PROJECT_DIR / ".submissions.log"
"""Where this project records the submissions the server accepted, one JSON object per
line with the keys id, kind, time, and server."""
PENDING_STATUSES = frozenset({"queued", "running"})
TRANSIENT_HTTP_STATUSES = frozenset({502, 503, 504})
"""HTTP statuses of a server that is restarting or unreachable behind a proxy."""
RETRY_S = 300.0
"""Longest time to wait for a server that does not answer while waiting for a result."""
MAX_BACKOFF_S = 15.0
UPLOAD_BYTES_PER_S = 100_000
"""Slowest upload rate to allow for, which extends the timeout of large submissions."""
API_URL_HINT = (
    "check that the server address is the API URL that the organizers gave you, "
    "such as http://<host>:<port>/api/v1"
)


class ServerError(Exception):
    """Raised when the evaluation server cannot be reached or rejects a request.

    `status` is the HTTP status of a rejection, `None` if there was no answer.
    `transient` is true if asking again later may succeed: the server did not answer
    or is restarting.
    """

    def __init__(
        self, message: str, status: int | None = None, *, transient: bool = False
    ):
        super().__init__(message)
        self.status = status
        self.transient = transient


def git_config(key: str, directory: Path = PROJECT_DIR) -> str | None:
    """The value of `git config <key>` in `directory`, or `None` if it is not set."""
    try:
        completed = subprocess.run(
            ["git", "config", key],
            cwd=directory,
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError:  # Git is not installed
        return None
    return completed.stdout.strip() or None


class EvaluationServer:
    """Client of the evaluation server at the base URL `url`."""

    def __init__(self, url: str, token: str | None = None, timeout_s: float = 60.0):
        url = url.strip()
        if not url:
            raise ServerError("no evaluation server address is set")
        parts = urllib.parse.urlsplit(url)
        if parts.scheme not in {"http", "https"} or not parts.netloc:
            raise ServerError(
                f"the server address {url!r} must start with http:// or https://"
            )
        if not parts.path.strip("/"):  # the server root: its API is under /api/v1
            url = f"{parts.scheme}://{parts.netloc}/api/v1"
        self.url = url.rstrip("/") + "/"
        self.token = (token or "").strip() or None
        self.timeout_s = timeout_s

    @property
    def public_url(self) -> str:
        """The base URL without any credentials in it, as recorded in the log."""
        parts = urllib.parse.urlsplit(self.url)
        return f"{parts.scheme}://{parts.netloc.rpartition('@')[2]}{parts.path}"

    def _request(
        self,
        method: str,
        path: str,
        params: dict[str, str] | None = None,
        data: bytes | None = None,
        *,
        expect: type = dict,
    ) -> Any:
        url = urllib.parse.urljoin(self.url, path)
        if params:
            url += "?" + urllib.parse.urlencode(params)
        request = urllib.request.Request(url, data=data, method=method)
        if data is not None:
            request.add_header("Content-Type", "application/zip")
        if self.token:
            request.add_header("Authorization", f"Bearer {self.token}")
        # A socket timeout bounds the whole upload, so it grows with the archive.
        timeout_s = self.timeout_s + len(data or b"") / UPLOAD_BYTES_PER_S
        try:
            with urllib.request.urlopen(request, timeout=timeout_s) as response:
                body = response.read()
        except urllib.error.HTTPError as error:
            try:
                detail = json.load(error).get("detail")
            except (ValueError, AttributeError):
                detail = None
            if error.code == 404 and (detail is None or detail == "Not Found"):
                # no such route: the address is likely not the API URL
                target = url.partition("?")[0]
                message = f"the server answered 404 at {target}; {API_URL_HINT}"
            else:
                message = f"the server answered {error.code}: {detail or error.reason}"
            raise ServerError(
                message, error.code, transient=error.code in TRANSIENT_HTTP_STATUSES
            ) from None
        except (urllib.error.URLError, OSError) as error:
            reason = getattr(error, "reason", error)
            raise ServerError(
                f"cannot reach {self.url}: {reason}", transient=True
            ) from None
        except (http.client.HTTPException, ValueError) as error:
            raise ServerError(
                f"the server sent an invalid answer: {error!r}",
                transient=isinstance(error, http.client.HTTPException),
            ) from None
        try:
            answer = json.loads(body)
        except ValueError:
            raise ServerError(
                f"{url.partition('?')[0]} answered {response.status} with "
                f"{response.headers.get_content_type()}, not JSON; {API_URL_HINT}"
            ) from None
        if not isinstance(answer, expect):
            kind = "object" if expect is dict else "array"
            raise ServerError(f"the server sent an invalid answer: not a JSON {kind}")
        return answer

    def submit(
        self,
        archive: bytes,
        participant_id: str,
        participant_name: str,
        *,
        final: bool = False,
    ) -> dict[str, Any]:
        """Send `archive` as a practice or final submission and return the receipt."""
        params = {
            "kind": "final" if final else "practice",
            "participant_id": participant_id,
            "participant_name": participant_name,
        }
        return self._request("POST", "submissions", params, archive)

    def submission(self, submission_id: str) -> dict[str, Any]:
        """The receipt of the submission `submission_id` with its current status."""
        quoted = urllib.parse.quote(submission_id, safe="")
        return self._request("GET", f"submissions/{quoted}")

    def submissions(self, participant_id: str) -> list[dict[str, Any]]:
        """The receipts of the submissions of the participant `participant_id`."""
        answer = self._request(
            "GET", "submissions", {"participant_id": participant_id}, expect=list
        )
        return [receipt for receipt in answer if isinstance(receipt, dict)]

    def wait(
        self,
        submission_id: str,
        *,
        receipt: dict[str, Any] | None = None,
        poll_s: float = 2.0,
        retry_s: float = RETRY_S,
        on_status: Callable[[str], None] | None = None,
        on_retry: Callable[[str], None] | None = None,
    ) -> dict[str, Any]:
        """Poll the submission until its evaluation has ended, and return it.

        `receipt` is the document the caller already has: its status is not reported
        again, and the first request is made after one poll interval. If the server
        does not answer (it may be restarting), ask again with a growing delay for up
        to `retry_s` seconds, telling `on_retry` once when this starts and once when
        the server answers again; after that, or on any other error, raise
        `ServerError`.
        """
        document = receipt
        status = None if receipt is None else receipt.get("status")
        down_since = None
        failures = 0
        while True:
            if document is None:
                try:
                    document = self.submission(submission_id)
                except ServerError as error:
                    if not error.transient:
                        raise
                    now = time.monotonic()
                    if down_since is None:
                        down_since = now
                        if on_retry is not None:
                            on_retry(
                                f"{error}; trying again for up to {clock(retry_s)}"
                            )
                    left = retry_s - (now - down_since)
                    if left <= 0:
                        raise ServerError(
                            f"{error} (no answer for {clock(retry_s)}, giving up)"
                        ) from None
                    time.sleep(min(poll_s * 2**failures, MAX_BACKOFF_S, left))
                    failures += 1
                    continue
                if down_since is not None:
                    down_since = None
                    failures = 0
                    if on_retry is not None:
                        on_retry("The server answers again")
            if document.get("status") != status:
                status = document.get("status")
                if on_status is not None:
                    on_status(str(status))
            if status not in PENDING_STATUSES:
                return document
            document = None
            time.sleep(poll_s)

    def practice_board(
        self, *, limit: int = 50, participant_id: str | None = None
    ) -> dict[str, Any]:
        """The practice leaderboard: the top `limit` rows, or the participant's."""
        params = {"limit": str(limit)}
        if participant_id:
            params["participant_id"] = participant_id
        return self._request("GET", "board/practice", params)

    def final_board(self) -> dict[str, Any]:
        """The final window and, once published, the final ranking."""
        return self._request("GET", "board/final")


def local_time(text: object) -> str:
    """An ISO 8601 time from the server in local time, or the text as given."""
    if text is None:
        return ""
    try:
        moment = datetime.fromisoformat(str(text))
    except ValueError:
        return str(text)
    return moment.astimezone().strftime("%Y-%m-%d %H:%M:%S")


def clock(seconds: float) -> str:
    """`seconds` as `H:MM:SS`."""
    total = max(0, int(seconds)) if isinstance(seconds, int | float) else 0
    return f"{total // 3600}:{total % 3600 // 60:02d}:{total % 60:02d}"


def boards(
    server: EvaluationServer, participant_id: str | None = None
) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    """The practice leaderboard and the final board of `server`, `None` for a board
    that the server does not have. Raises `ServerError` for any other failure."""

    def board(fetch: Callable[[], dict[str, Any]]) -> dict[str, Any] | None:
        try:
            return fetch()
        except ServerError as error:
            if error.status == 404:
                return None
            raise

    return (
        board(lambda: server.practice_board(participant_id=participant_id)),
        board(server.final_board),
    )


def final_status(board: dict[str, Any]) -> str | None:
    """One line on the final submissions of `board` from `final_board()`, or `None`
    if the server has no final submission window."""
    window = board.get("window")
    if not isinstance(window, dict):
        return None
    phase = window.get("phase")
    if phase == "open":
        left = window.get("remaining_s")
        left = f": {clock(left)} left" if isinstance(left, int | float) else ""
        until = local_time(window.get("closes_at"))
        return f"Final submissions are open{left}" + (
            f" (until {until})" if until else ""
        )
    if phase == "closed":
        closed_at = local_time(window.get("closed_at"))
        since = f"Final submissions are closed (since {closed_at})."
        return (
            f"{since} The final ranking is published."
            if board.get("published")
            else since
        )
    if phase == "not_started":
        return "Final submissions have not opened yet."
    return f"Final submission window: {phase or 'unknown'}"


def score_text(value: object) -> str:
    """A score to two decimals, or an empty text without one."""
    return f"{value:.2f}" if isinstance(value, int | float) else ""


def board_rows(
    board: dict[str, Any], *columns: str, formatted: bool = True
) -> list[dict[str, Any]]:
    """The rows of a leaderboard with the given columns, for display, under headings
    such as `Submitted at`: times in local time and, if `formatted`, scores to two
    decimals; otherwise scores stay numbers, so that tables sort them as numbers."""

    def shown(column: str, value: object) -> object:
        if column == "score":
            return score_text(value) if formatted else value
        if column == "submitted_at":
            return local_time(value)
        return "" if value is None else value

    rows = board.get("rows")
    return [
        {
            column.replace("_", " ").capitalize(): shown(column, row.get(column))
            for column in columns
        }
        for row in (rows if isinstance(rows, list) else [])
        if isinstance(row, dict)
    ]


def _width(text: str) -> int:
    """Display width of `text` in a terminal: wide characters such as kanji take
    two columns."""
    return sum(2 if unicodedata.east_asian_width(c) in "WF" else 1 for c in text)


def table_lines(rows: list[dict[str, Any]]) -> list[str]:
    """`rows` as a plain-text table, aligned for wide characters too."""
    if not rows:
        return []
    columns = list(rows[0])
    widths = [max(_width(c), *(_width(str(row[c])) for row in rows)) for c in columns]

    def line(cells: list[str]) -> str:
        padded = (cell + " " * (w - _width(cell)) for cell, w in zip(cells, widths))
        return "  ".join(padded).rstrip()

    return [line(columns), *(line([str(row[c]) for c in columns]) for row in rows)]


PRACTICE_COLUMNS = ("rank", "name", "score", "submitted_at", "submission", "count")
FINAL_COLUMNS = ("rank", "name", "score", "submitted_at")


def receipt_lines(receipt: dict[str, Any]) -> list[str]:
    """The receipt as `label: value` lines."""
    participant = receipt.get("participant") or {}
    return [
        f"Submission: {receipt.get('id')}",
        f"Kind:       {receipt.get('kind')}",
        f"Status:     {receipt.get('status')}",
        f"Received:   {receipt.get('received_at')}",
        f"SHA-256:    {receipt.get('sha256')}",
        f"Submitter:  {participant.get('name')} <{participant.get('id')}>",
    ]


class SubmissionIdError(Exception):
    """Raised when a given id does not select exactly one submission."""


FULL_ID = re.compile(r"[0-9a-f]{32}")
HEX_ID = re.compile(r"[0-9a-f]{1,32}")


def record_submission(
    server: EvaluationServer,
    receipt: dict[str, Any],
    log_path: Path = SUBMISSION_LOG,
) -> str | None:
    """Append the accepted submission of `receipt` to the log at `log_path`.

    Returns `None`, or the reason why it could not be written; a submission is
    never lost to the log, so this does not raise.
    """
    entry = {
        "id": receipt.get("id"),
        "kind": receipt.get("kind"),
        "time": datetime.now().astimezone().isoformat(timespec="seconds"),
        "server": server.public_url,
    }
    try:
        with log_path.open("a", encoding="utf-8") as log:
            log.write(json.dumps(entry) + "\n")
    except OSError as error:
        return str(error)
    return None


def logged_submissions(
    server: EvaluationServer, log_path: Path = SUBMISSION_LOG
) -> list[dict[str, Any]]:
    """The entries of the log at `log_path` that were made at the same server."""
    try:
        lines = log_path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    entries = []
    for line in lines:
        try:
            entry = json.loads(line)
        except ValueError:
            continue
        if (
            isinstance(entry, dict)
            and isinstance(entry.get("id"), str)
            and entry.get("server") == server.public_url
        ):
            entries.append(entry)
    return entries


def matching_submissions(
    query: str, entries: Iterable[dict[str, Any]]
) -> list[dict[str, Any]]:
    """The entries with distinct ids that start or end with `query`, in order."""
    found: dict[str, dict[str, Any]] = {}
    for entry in entries:
        submission_id = entry["id"].lower()
        if submission_id.startswith(query) or submission_id.endswith(query):
            found.setdefault(submission_id, entry)
    return list(found.values())


def resolve_submission_id(
    query: str,
    server: EvaluationServer,
    participant_id: str | None,
    log_path: Path = SUBMISSION_LOG,
) -> str:
    """The full id of the submission that `query` names.

    `query` is a full id or the start or the end of one, such as the 8 characters
    that the leaderboard shows. It is looked up in the log at `log_path` first and,
    if the log has no such submission, among the submissions that `participant_id`
    has made at the server. Raises `SubmissionIdError` unless exactly one matches.
    """
    text = query.strip().lower()
    if FULL_ID.fullmatch(text):
        return text
    if not HEX_ID.fullmatch(text):
        raise SubmissionIdError(
            f"{query!r} is not a submission id; "
            "use the id shown when you submitted, or its start or end"
        )
    found = matching_submissions(text, logged_submissions(server, log_path))
    where = f"in {log_path.name}"
    if not found and participant_id:
        try:
            receipts = server.submissions(participant_id)
        except ServerError as error:
            if error.status not in {404, 405}:  # the server cannot list submissions
                raise
            receipts = []
        found = matching_submissions(
            text,
            (
                {"id": r["id"], "kind": r.get("kind"), "time": r.get("received_at")}
                for r in receipts
                if isinstance(r.get("id"), str)
            ),
        )
        where += " or among your submissions on the server"
    elif not found:
        where += (
            "; to search your submissions on the server, "
            "set git config user.email or pass --participant-id"
        )
    if not found:
        raise SubmissionIdError(f"no submission matches {query!r} {where}")
    if len(found) > 1:
        shown = [
            f"  {e['id']}  {e.get('kind') or ''}  {local_time(e.get('time'))}".rstrip()
            for e in found[:10]
        ]
        more = [f"  ... and {len(found) - 10} more"] if len(found) > 10 else []
        raise SubmissionIdError(
            f"{query!r} matches {len(found)} submissions; give more characters:\n"
            + "\n".join([*shown, *more])
        )
    return found[0]["id"].lower()


def saved_note(problem: str | None) -> str:
    """Where the id of a submission was saved, or why it could not be, given the
    result of `record_submission`."""
    if problem is None:
        return f"The submission id is saved in `{SUBMISSION_LOG.name}`."
    return f"Could not write `{SUBMISSION_LOG.name}`: {problem}. Note the id above."


def later_text(receipt: dict[str, Any]) -> str:
    """How to fetch the result of the submission of `receipt` again later."""
    return (
        "The server keeps the submission; show it later with "
        f"`uv run submit.py --status {receipt.get('id')}`."
    )


def without_header(report: str) -> str:
    """`report` from `format_check_report` without its `Submission:` and `SHA-256:`
    lines, which the receipt has already shown, and without leading blank lines."""
    lines = report.split("\n")
    start = 0
    for prefix in ("Submission:", "SHA-256:"):
        if start < len(lines) and lines[start].startswith(prefix):
            start += 1
    return "\n".join(lines[start:]).lstrip("\n")


def main(argv: Sequence[str] | None = None) -> int:
    """Pack the project, submit it, and show the receipt or the practice result."""
    parser = argparse.ArgumentParser(
        prog="uv run submit.py", description=__doc__.split("\n\n")[0]
    )
    action = parser.add_mutually_exclusive_group()
    action.add_argument(
        "--final", action="store_true", help="submit as your final submission"
    )
    action.add_argument(
        "--status",
        metavar="ID",
        help="show the submission ID, or its start or end, such as the 8 characters "
        "of the leaderboard, instead of submitting",
    )
    action.add_argument(
        "--board",
        action="store_true",
        help="show the leaderboard and whether final submissions are open "
        "instead of submitting",
    )
    parser.add_argument(
        "--mine",
        action="store_true",
        help="with --board, only your practice submissions",
    )
    parser.add_argument("--server", help=f"base URL (default: ${URL_VARIABLE})")
    parser.add_argument("--token", help=f"server token (default: ${TOKEN_VARIABLE})")
    parser.add_argument("--participant-id", help="default: git config user.email")
    parser.add_argument("--participant-name", help="default: git config user.name")
    args = parser.parse_args(argv)

    from spacecraft_autonomy_challenge.cli import format_check_report
    from spacecraft_autonomy_challenge.judge.reporting import (
        EvaluationResult,
        ResultFormatError,
        problem_outcomes,
    )
    from spacecraft_autonomy_challenge.judge.submission import (
        SubmissionError,
        build_submission,
    )

    if args.mine and not args.board:
        parser.error("--mine needs --board")
    participant_id = args.participant_id or git_config("user.email")
    participant_name = args.participant_name or git_config("user.name")
    if args.board:
        if args.mine and not participant_id:
            parser.error("set git config user.email or pass --participant-id")
        return _show_boards(args, participant_id)
    if args.status is None and not (participant_id and participant_name):
        parser.error(
            "set git config user.email and user.name, "
            "or pass --participant-id and --participant-name"
        )
    receipt = None
    try:
        server = EvaluationServer(
            args.server or os.environ.get(URL_VARIABLE, ""),
            args.token or os.environ.get(TOKEN_VARIABLE),
        )
        if args.status is not None:
            receipt = server.submission(
                resolve_submission_id(args.status, server, participant_id)
            )
            problem = None
        else:
            archive = build_submission(PROJECT_DIR)
            kind = "final" if args.final else "practice"
            print(f"Submitting as {participant_name} <{participant_id}> ({kind})")
            receipt = server.submit(
                archive, participant_id, participant_name, final=args.final
            )
            problem = record_submission(server, receipt)
        print("\n".join(receipt_lines(receipt)))
        if args.status is None:
            print(f"\n{saved_note(problem)}", later_text(receipt))
        if receipt.get("kind") == "final":
            return 0
        document = server.wait(
            str(receipt.get("id")),
            receipt=receipt,
            on_status=lambda status: print(f"Status: {status}"),
            on_retry=lambda message: print(message, file=sys.stderr),
        )
    except (ServerError, SubmissionIdError, SubmissionError, OSError) as error:
        print(f"error: {error}", file=sys.stderr)
        if receipt is not None:
            print(later_text(receipt), file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print("interrupted", file=sys.stderr)
        if receipt is not None:
            print(later_text(receipt), file=sys.stderr)
        return 130
    if document.get("status") != "completed":
        print(f"The evaluation {document.get('status')}: {document.get('error')}")
        return 1
    try:
        result = EvaluationResult.from_dict(document["result"])
    except (KeyError, TypeError, ResultFormatError) as error:
        print(f"error: the server sent an invalid result: {error}", file=sys.stderr)
        print(later_text(receipt), file=sys.stderr)
        return 2
    print()
    print(without_header(format_check_report(str(receipt.get("id")), result)))
    fixed = score_text(document.get("fixed_score"))
    if fixed:
        print(f"\nFixed cases: {fixed} (the leaderboard ranks by this score)")
    return 1 if problem_outcomes(result.outcomes) else 0


def _show_boards(args: argparse.Namespace, participant_id: str | None) -> int:
    try:
        server = EvaluationServer(
            args.server or os.environ.get(URL_VARIABLE, ""),
            args.token or os.environ.get(TOKEN_VARIABLE),
        )
        practice, final = boards(server, participant_id if args.mine else None)
    except ServerError as error:
        print(f"error: {error}", file=sys.stderr)
        return 2
    print("\n".join(board_lines(practice, final, mine=args.mine, url=server.url)))
    return 0


def board_lines(
    practice: dict[str, Any] | None,
    final: dict[str, Any] | None,
    *,
    mine: bool = False,
    url: str = "",
) -> list[str]:
    """The boards from `boards()` as text: the final window and ranking, if any,
    then the practice leaderboard, if any."""
    blocks = []
    if final is not None:
        block = []
        status = final_status(final)
        if status is not None:
            block.append(status)
        if final.get("published"):
            rows = table_lines(board_rows(final, *FINAL_COLUMNS))
            block += [
                *([""] if block else []),
                "Final ranking",
                *(rows or ["No final ranking."]),
            ]
        if block:
            blocks.append(block)
    if practice is not None:
        rows = board_rows(practice, *PRACTICE_COLUMNS)
        total = practice.get("total", len(rows))
        empty = (
            "None of your practice submissions has been evaluated."
            if mine
            else "No evaluated practice submissions."
        )
        heading = f"Practice leaderboard ({len(rows)} of {total})"
        blocks.append([heading, *(table_lines(rows) or [empty])])
    if not blocks:
        return [f"No leaderboard at {url}; check the server address."]
    return [line for block in blocks for line in [*block, ""]][:-1]


if __name__ == "__main__":
    sys.exit(main())
