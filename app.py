import marimo

__generated_with = "0.25.2"
app = marimo.App(width="full", layout_file="layouts/app.grid.json")

with app.setup(hide_code=True):
    import contextlib
    import dataclasses
    import enum
    import importlib
    import importlib.abc
    import importlib.machinery
    import importlib.resources
    import importlib.util
    import inspect
    import io
    import itertools
    import os
    import sys
    import traceback
    import typing
    import warnings
    import zipfile
    from pathlib import Path

    import altair as alt
    import marimo as mo
    from spacecraft_autonomy_challenge import (
        Commander,
        PropulsionMode,
        SetHeater,
        SetMomentumDump,
        SetPropulsionMode,
        Strategy,
        Telemetry,
        __version__,
    )
    from spacecraft_autonomy_challenge.challenge import (
        problem,
        scenario,
        scenario_names,
    )
    from spacecraft_autonomy_challenge.commands import Command
    from spacecraft_autonomy_challenge.evaluation import evaluate
    from spacecraft_autonomy_challenge.judge.dependencies import DependencyError
    from spacecraft_autonomy_challenge.judge.judging import check_submission
    from spacecraft_autonomy_challenge.judge.reporting import (
        EvaluationResult,
        ResultFormatError,
    )
    from spacecraft_autonomy_challenge.judge.submission import (
        SubmissionError,
        build_submission,
    )
    from spacecraft_autonomy_challenge.judge.views import (
        dependencies_view,
        results_view,
        text_view,
    )
    from spacecraft_autonomy_challenge.results import TerminationReason
    from spacecraft_autonomy_challenge.simulation import run

    from submit import (
        FINAL_COLUMNS,
        PRACTICE_COLUMNS,
        TOKEN_VARIABLE,
        URL_VARIABLE,
        EvaluationServer,
        ServerError,
        board_rows,
        boards,
        final_status,
        git_config,
        later_text,
        receipt_lines,
        record_submission,
        saved_note,
        score_text,
    )

    REPOSITORY_DIR = Path(__file__).resolve().parent
    SUBMISSION_DIR = (REPOSITORY_DIR / "submission").resolve()
    STRATEGY_FILE = SUBMISSION_DIR / "strategy.py"
    STRATEGY_CLASS = "MyStrategy"
    # Interval at which the run result plots sample the true state.
    SAMPLE_PERIOD_S = 60.0
    CORE_DOCS_URL = (
        "https://github.com/conjikidow/spacecraft-autonomy-challenge-core"
        f"/blob/v{__version__}/docs"
    )
    CORE_EXAMPLES_URL = (
        "https://github.com/conjikidow/spacecraft-autonomy-challenge-core"
        f"/tree/v{__version__}/examples"
    )

    FIELD_UNITS = {
        "_nms_per_s": "Nms/s",
        "_j_per_k": "J/K",
        "_w_per_k": "W/K",
        "_mps2": "m/s²",
        "_mps": "m/s",
        "_nms": "Nms",
        "_wh": "Wh",
        "_w": "W",
        "_c": "°C",
        "_s": "s",
    }

    def field_unit(name):
        """Return the unit encoded in a field name's suffix, or `-` if there is none."""
        for suffix, unit in FIELD_UNITS.items():
            if name.endswith(suffix):
                return unit
        return "-"

    def doc_line(obj):
        """Return an object's docstring on a single line."""
        return " ".join(inspect.getdoc(obj).split())

    def type_label(annotation):
        """Return a type's name as inline code."""
        return f"`{annotation.__name__}`"

    def markdown_table(headers, rows):
        """Return a Markdown table with left-aligned columns."""
        return "\n".join(
            [f"| {' | '.join(headers)} |", "|:---" * len(headers) + "|", *rows]
        )

    def value_rows(obj, prefix=""):
        """Return field, value, and unit rows, flattening nested dataclasses."""
        rows = []
        for field in dataclasses.fields(obj):
            value = getattr(obj, field.name)
            if dataclasses.is_dataclass(value):
                rows.extend(value_rows(value, f"{prefix}{field.name}."))
            else:
                rows.append(
                    f"| `{prefix}{field.name}` | {value} | {field_unit(field.name)} |"
                )
        return rows

    class SubmissionLoadError(Exception):
        """Raised when the participant's strategy cannot be imported or instantiated."""

    class VegaLiteOutput:
        """Show a Vega-Lite JSON spec as is, through marimo's MIME protocol."""

        def __init__(self, spec):
            self.spec = spec

        def _mime_(self):
            return "application/vnd.vegalite.v6+json", self.spec

    def display_name(path):
        """Return a path under `submission/`, such as `submission/strategy.py`."""
        return "submission/" + path.relative_to(SUBMISSION_DIR).as_posix()

    def submission_path(text):
        """Return the path under `submission/` that `text` names, or raise."""
        name = text.strip()
        # Empty parts cover an empty name and a leading or trailing "/".
        if "\\" in name or any(part in ("", ".", "..") for part in name.split("/")):
            raise ValueError(
                "Enter a path relative to submission/, such as helpers/model.py."
            )
        try:
            path = (SUBMISSION_DIR / name).resolve()
        except RuntimeError:
            raise ValueError("The path contains a symbolic link loop.") from None
        if path == SUBMISSION_DIR or not path.is_relative_to(SUBMISSION_DIR):
            raise ValueError("The path must stay inside submission/.")
        # The submission archive leaves these out, so they would not be evaluated.
        if "__pycache__" in path.relative_to(SUBMISSION_DIR).parts or path.suffix in (
            ".pyc",
            ".pyo",
        ):
            raise ValueError("Bytecode and __pycache__ are not part of the submission.")
        return path

    def save_on_change(path, unsaved, on_error, on_saved):
        """Return an `on_change` callback that writes the editor's text to `path`.

        The text stays in `unsaved` until it is written, so that a failed save does not
        lose it when the editor is created again.
        """

        def save(text):
            was_pending = path in unsaved
            unsaved[path] = text
            if not writable_in_submission(path):
                on_error(path, ValueError("it lies outside submission/ or is a link"))
                return
            try:
                try:
                    same = path.read_text(encoding="utf-8") == text
                except (OSError, ValueError):
                    same = False
                if not same:
                    path.write_text(text, encoding="utf-8")
            except OSError as error:
                on_error(path, error)
            else:
                unsaved.pop(path, None)
                on_saved(was_pending)

        return save

    # Larger files are not loaded into the editor.
    EDITOR_MAX_BYTES = 1024 * 1024

    def bytecode_only(path):
        """Return True if `path` is an empty folder or holds only bytecode, recursively.

        Symbolic links are never bytecode, so nothing outside `submission/` is touched.
        """
        if path.is_symlink():
            return False
        if path.is_dir():
            return all(bytecode_only(child) for child in path.iterdir())
        return path.suffix in (".pyc", ".pyo")

    def inside_submission(item):
        """Return True if `item` exists and resolves to a path under `submission/`."""
        try:
            return item.exists() and item.resolve().is_relative_to(SUBMISSION_DIR)
        except (OSError, RuntimeError):
            return False

    def writable_in_submission(path):
        """Return True if writing `path` stays inside `submission/`.

        A link is never written through, so a link to a file elsewhere is refused.
        """
        try:
            return not path.is_symlink() and path.parent.resolve().is_relative_to(
                SUBMISSION_DIR
            )
        except (OSError, RuntimeError):
            return False

    def is_strategy(item):
        """Return True if `item` is `submission/strategy.py`, or the file it links to.

        A link to `strategy.py` is not protected: deleting it removes only the link.
        """
        if item is None:
            return False
        try:
            if item.is_symlink():
                return item.parent.resolve() / item.name == STRATEGY_FILE
            return item.resolve() == STRATEGY_FILE.resolve()
        except (OSError, RuntimeError):
            return False

    def remove_bytecode(path):
        """Remove `path` and everything under it, rechecking each entry as it goes."""
        if not bytecode_only(path):
            raise ValueError(f"{path.name} is no longer bytecode only.")
        if path.is_dir():
            for child in path.iterdir():
                remove_bytecode(child)
            path.rmdir()
        else:
            path.unlink()


@app.cell(hide_code=True)
def _():
    _overview = [
        (
            "Try a scenario",
            "one public scenario you pick",
            "in this notebook",
            "full details of the run",
        ),
        (
            "Check on public scenarios",
            "all public scenarios",
            "packed, as the server runs them",
            "scores",
        ),
        (
            "Practice on hidden scenarios",
            "hidden practice scenarios",
            "on your event's evaluation server",
            "scores; ranked on the **Leaderboard**",
        ),
        (
            "Submit the final version",
            "hidden final scenarios",
            "later, on your event's evaluation server",
            "a receipt now",
        ),
    ]
    mo.vstack(
        [
            mo.md(r"""
            # Spacecraft Autonomy Challenge
            """),
            mo.md(r"""
            Your solution is the `submission/` directory.
            The entry point is the `MyStrategy` class in `submission/strategy.py`,
            and every other file it needs (Python modules, planning models,
            model weights, configuration, data) also belongs under `submission/`.
            """),
            mo.md(r"""
            Edit the files under `submission/` in the **Submission** section below,
            starting with `submission/strategy.py`, or in your own editor,
            and add packages with `uv add`.
            """),
            mo.md(r"""
            Then test and submit it in these sections, each using the current files:
            """),
            mo.md(
                "\n".join(
                    [
                        "| Section | Scenarios | Runs | Shows |",
                        "|:---|:---|:---|:---|",
                        *(
                            f"| **{section}** | {' | '.join(details)} |"
                            for section, *details in _overview
                        ),
                    ]
                )
            ),
        ]
    )
    return


@app.cell(hide_code=True)
def _():
    mo.vstack(
        [
            mo.md("## Spacecraft"),
        ]
    )
    return


@app.cell(hide_code=True)
def _():
    _telemetry_rows = [
        f"| `{field.name}` | {type_label(field.type)} | {field_unit(field.name)} |"
        for field in dataclasses.fields(Telemetry)
    ]
    mo.vstack(
        [
            mo.md("### Telemetry"),
            mo.md(markdown_table(("Telemetry field", "Type", "Unit"), _telemetry_rows)),
        ]
    )
    return


@app.cell(hide_code=True)
def _():
    mo.image(
        importlib.resources.files("spacecraft_autonomy_challenge")
        / "figures"
        / "spacecraft-geometry.svg",
        alt="Two thrusters offset from the center of mass "
        "and a single-axis reaction wheel",
        width=480,
        style={"min-width": "0"},
    )
    return


@app.cell(hide_code=True)
def _():
    def _arguments(command):
        fields = dataclasses.fields(command)
        return ", ".join(
            f"`{field.name}`:&nbsp;{type_label(field.type)}" for field in fields
        )

    _command_rows = [
        f"| `{command.__name__}` | {_arguments(command) or '-'} | {doc_line(command)} |"
        for command in typing.get_args(Command)
    ]
    _types = [field.type for field in dataclasses.fields(Telemetry)] + [
        field.type
        for command in typing.get_args(Command)
        for field in dataclasses.fields(command)
    ]
    _enum_lines = [
        f"`{annotation.__name__}` is one of "
        + ", ".join(f"`{member.name}`" for member in annotation)
        + "."
        for annotation in dict.fromkeys(_types)
        if issubclass(annotation, enum.Enum)
    ]
    mo.vstack(
        [
            mo.md("### Commands"),
            mo.md(markdown_table(("Command", "Arguments", "Effect"), _command_rows)),
            mo.md("\n\n".join(_enum_lines)),
            mo.md(
                f"Details: [Spacecraft Model]({CORE_DOCS_URL}/spacecraft-model.md), "
                f"[Commands and Timeline]({CORE_DOCS_URL}/timeline.md)"
            ),
        ]
    )
    return


@app.cell(hide_code=True)
def _():
    mo.vstack(
        [
            mo.md("### Nominal parameters"),
            mo.md(
                "The published nominal model (`brief.nominal_parameters`); "
                "the actual spacecraft may deviate."
            ),
            mo.md(
                markdown_table(
                    ("Parameter", "Value", "Unit"), value_rows(problem().parameters)
                )
            ),
        ]
    )
    return


@app.cell(hide_code=True)
def _():
    _problem = problem()
    _goal_rows = [
        f"| `{name}` | {getattr(_problem, name)} | {field_unit(name)} |"
        for name in ("target_delta_v_mps", "time_limit_s", "decision_period_s")
    ]
    mo.vstack(
        [
            mo.md("## Mission"),
            mo.md("The public problem shared by all scenarios."),
            mo.md(
                markdown_table(
                    ("Field", "Value", "Unit"),
                    [
                        "| **Initial state** | | |",
                        *value_rows(_problem.initial_state),
                        "| **Goal and timing** | | |",
                        *_goal_rows,
                        "| **Safety limits** | | |",
                        *value_rows(_problem.limits),
                    ],
                )
            ),
        ]
    )
    return


@app.cell(hide_code=True)
def _():
    def _call(function):
        parameters = list(inspect.signature(function).parameters)[1:]
        return f"{function.__name__}({', '.join(parameters)})"

    _contents = {
        "brief": (
            "Values under **Mission**, e.g. `brief.decision_period_s`; "
            "`brief.nominal_parameters` under **Nominal parameters**; "
            "`brief.nominal_initial_telemetry` is the nominal initial state "
            "under **Initial state**"
        ),
        "telemetry": (
            "Fields under **Spacecraft** at the callback time, "
            "e.g. `telemetry.battery_energy_wh`; may differ from the true state"
        ),
        "commander": (
            "Members below, e.g. `commander.schedule(600.0, SetHeater(on=True))`"
        ),
    }
    _callback_rows = [
        f"| `{_call(callback)}` | {doc_line(callback)} |"
        for callback in (Strategy.initialize, Strategy.update)
    ]
    _argument_rows = [
        f"| `{name}` | `{parameter.annotation.__name__}` | {_contents.get(name, '')} |"
        for name, parameter in inspect.signature(Strategy.initialize).parameters.items()
        if name != "self"
    ]
    _commander_rows = [
        f"| `{name if isinstance(member, property) else _call(member)}` "
        f"| {doc_line(member)} |"
        for name, member in vars(Commander).items()
        if not name.startswith("_")
    ]
    mo.vstack(
        [
            mo.md("## Strategy API"),
            mo.vstack(
                [
                    mo.md(markdown_table(("Callback", "When"), _callback_rows)),
                    mo.md(
                        markdown_table(("Argument", "Type", "Content"), _argument_rows)
                    ),
                    mo.md(
                        markdown_table(
                            ("Commander member", "Behavior"), _commander_rows
                        )
                    ),
                ],
                gap=1.5,
            ),
            mo.md(
                f"Details: [Writing a Strategy]({CORE_DOCS_URL}/strategy.md), "
                f"[Examples]({CORE_EXAMPLES_URL})"
            ),
        ]
    )
    return


@app.cell(hide_code=True)
def _():
    # Bumping the revision re-creates the file tree, so that its listing is read again.
    get_revision, set_revision = mo.state(0, allow_self_loops=True)
    get_notice, set_notice = mo.state(None, allow_self_loops=True)
    get_pending, set_pending = mo.state(0, allow_self_loops=True)
    # The item the tree shows as selected, which a re-created tree starts from, and the
    # last file selected, which is the one being edited.
    selection = {"path": STRATEGY_FILE, "file": STRATEGY_FILE}
    # Text typed in the editor and not saved yet, by file.
    unsaved = {}
    # Typed in the operations column, kept when the cell is re-created.
    typed = {"name": "", "confirm": None}
    return (
        get_notice,
        get_pending,
        get_revision,
        selection,
        set_notice,
        set_pending,
        set_revision,
        typed,
        unsaved,
    )


@app.cell(hide_code=True)
def _(get_revision, selection):
    _ = get_revision()

    def _remember(files):
        if files:
            selection["path"] = files[0].path
            if files[0].path.is_file():
                selection["file"] = files[0].path

    # Bytecode and __pycache__ files are left out, as `build_submission` leaves them out
    # of the archive, so a __pycache__ folder shows as empty. Empty folders are shown,
    # so that a folder stays listed after its last file is deleted.
    submission_files = mo.ui.file_browser(
        initial_path=SUBMISSION_DIR,
        value=next(
            (p for p in (selection["path"], STRATEGY_FILE) if inside_submission(p)),
            None,
        ),
        multiple=False,
        selection_mode="all",
        filter=lambda path: (
            path.suffix not in (".pyc", ".pyo")
            and "__pycache__" not in path.relative_to(SUBMISSION_DIR).parts
        ),
        on_change=_remember,
    )
    return (submission_files,)


@app.cell(hide_code=True)
def _(
    get_notice,
    get_pending,
    selection,
    set_notice,
    set_pending,
    set_revision,
    submission_files,
    typed,
    unsaved,
):
    _ = get_pending()
    # Delete acts on the item selected in the tree, which is none after a click on the
    # selected item. The file being edited stays the last file selected.
    _item = submission_files.path()
    _current = selection["file"]
    # Only files that stay inside submission/ are edited.
    if not _current.is_file() or not inside_submission(_current):
        _current = STRATEGY_FILE

    new_name = mo.ui.text(
        value=typed["name"],
        placeholder="helpers/model.py",
        label="New file",
        on_change=lambda value: typed.__setitem__("name", value),
    )
    confirm = mo.ui.checkbox(
        value=_item is not None and typed["confirm"] == _item,
        label="Confirm deletion",
        on_change=lambda value: typed.__setitem__("confirm", _item if value else None),
    )

    def _create(_):
        set_notice(None)
        try:
            _target = submission_path(new_name.value)
            if _target.exists():
                raise ValueError(f"{display_name(_target)} already exists.")
            _target.parent.mkdir(parents=True, exist_ok=True)
            _target.touch()
        except (ValueError, OSError) as error:
            set_notice(("danger", "Could not create the file", str(error)))
        else:
            typed["name"] = ""
            selection["path"] = selection["file"] = _target
            set_revision(lambda value: value + 1)

    def _delete(_):
        set_notice(None)
        if not confirm.value:
            set_notice(("warn", "Not deleted", 'Tick "Confirm deletion" first.'))
            return
        try:
            if _item is None or is_strategy(_item):
                raise ValueError("submission/strategy.py cannot be deleted.")
            try:
                parent_inside = _item.parent.resolve().is_relative_to(SUBMISSION_DIR)
            except (OSError, RuntimeError):
                parent_inside = False
            if not parent_inside:
                raise ValueError("The item lies outside submission/.")
            if _item.is_dir() and not _item.is_symlink():
                # Only an empty folder, or one holding only bytecode, is removed, so
                # that no source file is lost by a click.
                if not bytecode_only(_item):
                    raise ValueError(
                        f"{display_name(_item)} is not empty. Delete its files first. "
                        "Remove files hidden in __pycache__ outside the notebook."
                    )
                remove_bytecode(_item)
            else:
                _item.unlink()
        except (ValueError, OSError) as error:
            set_notice(("danger", "Could not delete", str(error)))
        else:
            typed["confirm"] = None
            for stale in [p for p in unsaved if p == _item or _item in p.parents]:
                del unsaved[stale]
            edited = selection["file"]
            if edited == _item or _item in edited.parents:
                selection["file"] = STRATEGY_FILE
            # The tree selects the file being edited, so that the two always agree.
            selection["path"] = selection["file"]
            set_revision(lambda value: value + 1)

    # `submission/strategy.py` is the entry point, so it cannot be deleted.
    create_button = mo.ui.button(label="Create", on_click=_create)
    delete_button = mo.ui.button(
        label=f"Delete {_item.name}" if _item is not None else "Delete",
        kind="danger",
        on_click=_delete,
        disabled=_item is None or is_strategy(_item),
    )

    def _report_save_error(path, error):
        set_notice(
            ("danger", f"Could not save {display_name(path)}", str(error), "save")
        )

    def _on_saved(was_pending):
        notice = get_notice()
        # A save error goes once nothing is unsaved; other notices stay.
        if notice is not None and len(notice) == 4 and not unsaved:
            set_notice(None)
        elif was_pending:
            set_pending(lambda value: value + 1)

    def _make_editor(text):
        return mo.ui.code_editor(
            value=text,
            language="python",
            # Saved when the editor loses focus, so the file is written once per edit
            # instead of on every keystroke, and the rest of this cell is not re-run.
            debounce=True,
            on_change=save_on_change(_current, unsaved, _report_save_error, _on_saved),
        )

    _name = display_name(_current)
    if is_strategy(_current) and _current.is_symlink():
        file_editor = mo.callout(
            "`submission/strategy.py` is a link. Replace it with a regular file "
            "outside the notebook.",
            kind="warn",
        )
    elif not _current.exists():
        file_editor = mo.callout(
            f"`{_name}` is missing. Create it with New file.", kind="warn"
        )
    elif not writable_in_submission(_current):
        file_editor = mo.callout(
            f"`{_name}` is a link. Edit the file it points to, or delete the link.",
            kind="warn",
        )
    elif _current in unsaved:
        file_editor = _make_editor(unsaved[_current])
    elif _current.stat().st_size > EDITOR_MAX_BYTES:
        file_editor = mo.callout(
            f"`{_name}` is larger than {EDITOR_MAX_BYTES // 1024} KB, "
            "so it cannot be edited here.",
            kind="warn",
        )
    else:
        try:
            file_editor = _make_editor(_current.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError):
            file_editor = mo.callout(
                "This file cannot be edited here: it is not readable UTF-8 text.",
                kind="warn",
            )

    _notice = get_notice()
    _messages = (
        []
        if _notice is None
        else [mo.callout(_notice[2], kind=_notice[0], title=_notice[1])]
    )
    _pending_names = [display_name(p) for p in unsaved]
    if _pending_names:
        _messages.insert(
            0,
            mo.callout(
                f"Not saved yet: {', '.join(_pending_names)}. "
                "Other sections use the files on disk.",
                kind="warn",
            ),
        )
    mo.vstack(
        [
            mo.md("## Submission"),
            mo.md(
                """
                Select a file to edit. It is saved when the editor loses focus.
                After editing a file elsewhere, restart the kernel and run all cells.
                """
            ),
            mo.hstack(
                [
                    mo.vstack([submission_files]).style(flex="0 0 500px"),
                    mo.vstack(
                        [
                            mo.hstack(
                                [new_name, create_button],
                                justify="start",
                                align="end",
                                gap=0.5,
                                wrap=True,
                            ),
                            mo.hstack(
                                [confirm, delete_button],
                                justify="start",
                                align="center",
                                gap=0.5,
                                wrap=True,
                            ),
                            *_messages,
                        ]
                    ).style(flex="0 0 340px", min_width="0"),
                ],
                align="start",
                wrap=True,
                gap=1,
            ),
            mo.md(f"`{_name}`"),
            file_editor,
        ]
    )
    return new_name, confirm, create_button, delete_button, file_editor


@app.cell(hide_code=True)
def _():
    _names = scenario_names()
    scenario_picker = mo.ui.dropdown(
        options=_names,
        value="nominal" if "nominal" in _names else _names[0],
        label="Scenario",
    )
    run_button = mo.ui.run_button(label="Run", kind="success")
    return run_button, scenario_picker


@app.function(hide_code=True)
def forget_submission_modules():
    """Remove imported modules whose files are under `submission/`.

    All other modules stay imported.
    """
    for name, module in list(sys.modules.items()):
        locations = [
            getattr(module, "__file__", None),
            *getattr(module, "__path__", ()),
        ]
        if any(
            location and Path(location).resolve().is_relative_to(SUBMISSION_DIR)
            for location in locations
        ):
            del sys.modules[name]


@app.class_definition(hide_code=True)
class SourceOnlyLoader(importlib.machinery.SourceFileLoader):
    """Loader that compiles from source and never reads or writes bytecode caches.

    A cache that still matches a file's size and timestamp, which has a resolution of
    one second, could otherwise hide an edit made right after the previous run.
    """

    def get_code(self, fullname):
        return compile(self.get_data(self.path), self.path, "exec", dont_inherit=True)


@app.class_definition(hide_code=True)
class SubmissionFinder(importlib.abc.MetaPathFinder):
    """Import hook that loads source files under `submission/` from source only."""

    def find_spec(self, fullname, path, target=None):
        spec = importlib.machinery.PathFinder.find_spec(fullname, path)
        if not (
            spec is not None
            and isinstance(spec.loader, importlib.machinery.SourceFileLoader)
            and Path(spec.origin).resolve().is_relative_to(SUBMISSION_DIR)
        ):
            return None
        spec.loader = SourceOnlyLoader(fullname, spec.origin)
        return spec


@app.function(hide_code=True)
@contextlib.contextmanager
def submission_import_hook():
    """Install a `SubmissionFinder` for the duration of the block.

    It is removed on exit because `marimo run` serves every session from one process
    and clears a closed session's globals, which the finder's methods need.
    """
    finder = SubmissionFinder()
    # Right before the path-based finder, so built-in and frozen modules keep
    # precedence as they do under the command-line runner.
    sys.meta_path.insert(sys.meta_path.index(importlib.machinery.PathFinder), finder)
    try:
        yield
    finally:
        sys.meta_path.remove(finder)


@app.function(hide_code=True)
def load_strategy():
    """Instantiate `MyStrategy` from the current files under `submission/`.

    Like the command-line runner, `submission/` is put first on the import path so that
    `strategy.py` can import sibling modules, which are also read again from disk as
    long as this is called inside `submission_import_hook()`.
    Raises `SubmissionLoadError`, chained to the participant's exception, if the code
    cannot be imported or instantiated.
    """
    forget_submission_modules()
    importlib.invalidate_caches()
    entry = str(SUBMISSION_DIR)
    while entry in sys.path:
        sys.path.remove(entry)
    sys.path.insert(0, entry)
    loader = SourceOnlyLoader("_submission_strategy", str(STRATEGY_FILE))
    module = importlib.util.module_from_spec(
        importlib.util.spec_from_loader(loader.name, loader)
    )
    sys.modules[loader.name] = module
    try:
        loader.exec_module(module)
    except Exception as error:
        raise SubmissionLoadError(f"cannot import {STRATEGY_FILE.name}") from error
    strategy_class = getattr(module, STRATEGY_CLASS, None)
    if not (isinstance(strategy_class, type) and issubclass(strategy_class, Strategy)):
        raise SubmissionLoadError(
            f"{STRATEGY_FILE.name} must define {STRATEGY_CLASS} as a Strategy subclass"
        )
    try:
        return strategy_class()
    except Exception as error:
        raise SubmissionLoadError(f"cannot instantiate {STRATEGY_CLASS}") from error


@app.function(hide_code=True)
def time_series_chart(result):
    """Chart the true state of a run over time, with the limits it must respect.

    States are sampled once per `SAMPLE_PERIOD_S`, plus the last one. The strip at the
    bottom shows the propulsion mode, the heater, and the momentum dump.
    """
    problem = result.scenario.problem
    limits = problem.limits
    samples = {}
    for state in result.trajectory:
        samples.setdefault(state.time_s // SAMPLE_PERIOD_S, state)
    samples = [*samples.values()]
    if samples[-1] is not result.trajectory[-1]:
        samples.append(result.trajectory[-1])
    panels = [
        ("delta_v_mps", "Delta-v (m/s)", {"target": problem.target_delta_v_mps}),
        (
            "battery_energy_wh",
            "Battery energy (Wh)",
            {"min": limits.min_battery_energy_wh},
        ),
        (
            "propulsion_temperature_c",
            "Propulsion temperature (°C)",
            {
                "max": limits.max_propulsion_temperature_c,
                "min firing": problem.parameters.min_firing_temperature_c,
            },
        ),
        (
            "wheel_momentum_nms",
            "Wheel momentum (Nms)",
            {
                "max": limits.max_abs_wheel_momentum_nms,
                "min": -limits.max_abs_wheel_momentum_nms,
            },
        ),
    ]
    data = alt.InlineData(
        values=[
            {
                "time_s": state.time_s,
                "time_h": state.time_s / 3600,
                **{field: getattr(state, field) for field, *_ in panels},
            }
            for state in samples
        ]
    )
    x_scale = alt.Scale(domain=[0, problem.time_limit_s / 3600], nice=False)
    width, line_color, limit_color = "container", "#2a78d6", "#7a7974"
    hover = alt.selection_point(
        name="hover",
        fields=["time_s"],
        nearest=True,
        on="pointerover",
        clear="pointerout",
        empty=False,
    )
    cursor = (
        alt.Chart()
        .mark_rule(color=limit_color)
        .encode(x=alt.X("time_h:Q", scale=x_scale))
        .transform_filter(hover)
    )

    def _panel(field, title, references):
        base = alt.Chart().encode(
            x=alt.X("time_h:Q", scale=x_scale, title=None),
            y=alt.Y(f"{field}:Q", title=None),
        )
        reference = alt.Chart(
            alt.InlineData(
                values=[
                    {"label": f"{k} {v:g}", "value": v} for k, v in references.items()
                ]
            )
        ).encode(y="value:Q")
        return alt.layer(
            reference.mark_rule(color=limit_color, strokeDash=[4, 4]),
            reference.mark_text(
                align="right", baseline="bottom", dx=-2, dy=-2, color=limit_color
            ).encode(x=alt.datum(problem.time_limit_s / 3600), text="label:N"),
            base.mark_line(color=line_color, strokeWidth=2),
            cursor,
            base.mark_point(filled=True, color=line_color, size=40).transform_filter(
                hover
            ),
            base.mark_point(opacity=0)
            .encode(
                tooltip=[
                    alt.Tooltip("time_s:Q", title="Time (s)", format=".0f"),
                    alt.Tooltip(f"{field}:Q", title=title, format=".4~f"),
                ]
            )
            .add_params(hover),
        ).properties(
            title=alt.Title(title, anchor="start", fontSize=12),
            width=width,
            height=80,
        )

    initial = result.trajectory[0]
    changes = {
        "Mode": [(0.0, initial.propulsion_mode.value)],
        "Heater": [(0.0, "Heater on" if initial.heater_on else None)],
        "Dump": [],
    }
    for scheduled in result.executed_commands:
        match scheduled.command:
            case SetPropulsionMode(mode=mode):
                changes["Mode"].append((scheduled.time_s, mode.value))
            case SetHeater(on=on):
                changes["Heater"].append(
                    (scheduled.time_s, "Heater on" if on else None)
                )
    # A command runs after the state of its time is recorded, and the dump switches
    # itself off within a step. The dump is active from each recorded time on when it
    # is on after the commands of that time.
    dump_commands = {
        scheduled.time_s: scheduled.command.on
        for scheduled in result.executed_commands
        if isinstance(scheduled.command, SetMomentumDump)
    }
    dump_active = [
        dump_commands.get(state.time_s, state.momentum_dump_on)
        for state in result.trajectory
    ]
    changes["Dump"].append((0.0, "Momentum dump on" if dump_active[0] else None))
    for state, active, previous_active in zip(
        result.trajectory[1:], dump_active[1:], dump_active, strict=False
    ):
        if active != previous_active:
            changes["Dump"].append(
                (state.time_s, "Momentum dump on" if active else None)
            )
    bars = [
        {"channel": channel, "state": state, "start_s": start_s, "end_s": end_s}
        for channel, values in changes.items()
        for (start_s, state), (end_s, _) in itertools.pairwise(
            [*values, (result.final_state.time_s, None)]
        )
        if state not in (None, PropulsionMode.OFF.value) and end_s > start_s
    ]
    strip = (
        alt.Chart(alt.InlineData(values=bars))
        .transform_calculate(start_h="datum.start_s / 3600", end_h="datum.end_s / 3600")
        .mark_bar()
        .encode(
            x=alt.X("start_h:Q", scale=x_scale, title="Time (h)"),
            x2="end_h:Q",
            y=alt.Y("channel:N", scale=alt.Scale(domain=[*changes]), title=None),
            color=alt.Color(
                "state:N",
                scale=alt.Scale(
                    domain=[
                        PropulsionMode.THRUSTER_1.value,
                        PropulsionMode.THRUSTER_2.value,
                        "Heater on",
                        "Momentum dump on",
                    ],
                    range=[line_color, "#eb6834", "#1baf7a", "#9467bd"],
                ),
                legend=alt.Legend(title=None, orient="bottom"),
            ),
            tooltip=[
                alt.Tooltip("state:N", title="State"),
                alt.Tooltip("start_s:Q", title="From (s)", format=".1f"),
                alt.Tooltip("end_s:Q", title="To (s)", format=".1f"),
            ],
        )
    )
    # All panels share the hover selection; altair merges it and warns about that.
    with warnings.catch_warnings():
        warnings.filterwarnings("ignore", "Automatically deduplicated", UserWarning)
        chart = alt.vconcat(
            *(_panel(*panel) for panel in panels),
            alt.layer(strip, cursor).properties(
                title=alt.Title(
                    "Propulsion mode, heater, and momentum dump (blank: off)",
                    anchor="start",
                    fontSize=12,
                ),
                width=width,
                height=60,
            ),
            data=data,
            autosize=alt.AutoSizeParams(type="fit-x", contains="padding"),
        )
    # marimo re-measures a chart when its output resizes only if the top-level width
    # is "container". The Vega-Lite schema has no width for concatenated charts, so
    # the spec is serialized without validation and shown as is.
    chart["width"] = "container"
    return VegaLiteOutput(chart.to_json(validate=False))


@app.cell(hide_code=True)
def _(run_button, scenario_picker):
    load_error = ""
    run_error = ""
    result = None
    strategy_output = ""
    if run_button.value:
        _output = io.StringIO()
        with (
            contextlib.redirect_stdout(_output),
            contextlib.redirect_stderr(_output),
            submission_import_hook(),
        ):
            try:
                result = run(load_strategy(), scenario(scenario_picker.value))
            except SubmissionLoadError as error:
                load_error = "".join(traceback.format_exception(error))
            except KeyboardInterrupt:
                run_error = "The run was interrupted."
            except SystemExit:
                run_error = "The strategy called sys.exit()."
        strategy_output = _output.getvalue()
    return load_error, result, run_error, strategy_output


@app.cell(hide_code=True)
def _(
    load_error,
    result,
    run_button,
    run_error,
    scenario_picker,
    strategy_output,
):
    _controls = [
        mo.md("## Try a scenario"),
        mo.md("One public scenario, run in this notebook, with full details."),
        mo.hstack([scenario_picker, run_button], justify="start", gap=1),
        mo.md(scenario(scenario_picker.value).description).style(contain="inline-size"),
    ]
    _columns = []
    _sections = []
    if load_error:
        _body = mo.callout(
            text_view(load_error),
            kind="danger",
            title=f"Could not load submission/strategy.py:{STRATEGY_CLASS}",
        )
    elif run_error:
        _body = mo.callout(text_view(run_error), kind="danger", title="The run stopped")
    elif result is None:
        _body = mo.md("Select a scenario and click **Run** to see the result.")
    else:
        _metrics = evaluate(result)
        _declared = (
            f"yes, at {_metrics.completion_declared_time_s:.1f} s"
            if _metrics.completion_declared
            else "no"
        )
        _rows = [
            ("Scenario", f"`{result.scenario.name}` (seed {result.seed})"),
            ("Termination reason", f"`{_metrics.termination_reason}`"),
            (
                "End time",
                (
                    f"{_metrics.end_time_s:.1f} s "
                    f"(time limit {_metrics.time_limit_s:.1f} s)"
                ),
            ),
            ("Completion declared", _declared),
            ("Safe", "yes" if _metrics.safe else "no"),
            ("Achieved delta-v", f"{_metrics.delta_v_mps:.4f} m/s"),
            ("Target delta-v", f"{_metrics.target_delta_v_mps:.4f} m/s"),
            (
                "Delta-v error (achieved - target)",
                f"{_metrics.delta_v_error_mps:+.4f} m/s",
            ),
            ("Minimum battery energy", f"{_metrics.min_battery_energy_wh:.2f} Wh"),
            (
                "Propulsion temperature range",
                (
                    f"{_metrics.min_propulsion_temperature_c:.2f} to "
                    f"{_metrics.max_propulsion_temperature_c:.2f} °C"
                ),
            ),
            (
                "Maximum absolute wheel momentum",
                f"{_metrics.max_abs_wheel_momentum_nms:.4f} Nms",
            ),
        ]
        _body = mo.md(
            "\n".join(
                ["| Metric | Value |", "|:---|:---|"]
                + [f"| {label} | {value} |" for label, value in _rows]
            )
        )
        _columns.append(
            mo.vstack(
                [
                    mo.md(
                        "True state from the simulator, sampled every "
                        f"{SAMPLE_PERIOD_S:g} s."
                    ),
                    time_series_chart(result),
                ]
            ).style(flex="1 1 360px", min_width="320px", align_self="baseline")
        )
        if _metrics.safety_violations:
            _sections.append(
                mo.callout(
                    mo.md(
                        "\n".join(
                            f"- `{violation.reason}` at {violation.time_s:.1f} s: "
                            f"{violation.message}"
                            for violation in _metrics.safety_violations
                        )
                    ),
                    kind="warn",
                    title="Safety violations",
                )
            )
        if _metrics.termination_reason in (
            TerminationReason.STRATEGY_ERROR,
            TerminationReason.INVALID_COMMAND,
        ):
            _sections.append(
                mo.callout(
                    text_view(result.termination.detail),
                    kind="danger",
                    title=f"The run ended with {_metrics.termination_reason}",
                )
            )
    if result is not None or load_error or run_error:
        _sections.append(
            mo.accordion(
                {
                    "Strategy output (stdout and stderr)": text_view(strategy_output)
                    if strategy_output
                    else mo.md("The strategy printed nothing.")
                }
            )
        )
    mo.vstack(
        [
            # Both columns align on their first baseline, so the chart's caption sits
            # on the same line as the heading.
            mo.hstack(
                [
                    mo.vstack([mo.vstack(_controls), _body], gap=1.5).style(
                        flex="0 1 auto", align_self="baseline"
                    ),
                    *_columns,
                ],
                justify="start",
                align="start",
                wrap=True,
                gap=1,
            ),
            *_sections,
        ]
    )
    return


@app.cell(hide_code=True)
def _():
    prepare_button = mo.ui.run_button(label="Pack")
    return (prepare_button,)


@app.cell(hide_code=True)
def _(prepare_button):
    _sections = [
        mo.md("## Pack"),
        mo.md("Build `submission.zip` for download."),
        prepare_button,
    ]
    if prepare_button.value:
        try:
            _data = build_submission(REPOSITORY_DIR)
        except (SubmissionError, OSError) as error:
            _sections.append(
                mo.callout(str(error), kind="danger", title="Could not pack")
            )
        else:
            with zipfile.ZipFile(io.BytesIO(_data)) as _archive:
                _names = _archive.namelist()
            _sections.append(
                mo.download(
                    data=_data,
                    filename="submission.zip",
                    mimetype="application/zip",
                    label="Download submission.zip",
                )
            )
            _sections.append(
                mo.md("<small>Packed from the files at click time.</small>")
            )
            _sections.append(
                mo.accordion(
                    {f"Contents ({len(_names)} files)": text_view("\n".join(_names))}
                )
            )
    mo.vstack(_sections)
    return


@app.cell(hide_code=True)
def _():
    check_button = mo.ui.run_button(
        label="Check",
        kind="success",
        tooltip=(
            "Each scenario runs in a new process. "
            "Missing packages are installed for this check only."
        ),
    )
    return (check_button,)


@app.cell(hide_code=True)
def _(check_button):
    _sections = [
        mo.md("## Check on public scenarios"),
        mo.md("All public scenarios, packed and run as the server does."),
        check_button,
    ]
    if check_button.value:
        # Shown now, so the spinner appears below the heading instead of replacing it.
        mo.output.replace(mo.vstack(_sections))
        try:
            with mo.status.spinner(
                title="Checking on the public scenarios"
            ) as _spinner:
                _evaluation = check_submission(
                    build_submission(REPOSITORY_DIR),
                    on_status=lambda message: _spinner.update(subtitle=message),
                    on_case_done=lambda outcome: _spinner.update(
                        subtitle=f"{outcome.case_id}: {outcome.status.value}"
                    ),
                )
        except (SubmissionError, DependencyError, OSError) as error:
            _sections.append(
                mo.callout(str(error), kind="danger", title="Could not check")
            )
        else:
            _result = EvaluationResult.from_evaluation(_evaluation)
            _sections += [
                results_view(_result),
                mo.accordion({"Packages": dependencies_view(_result.dependencies)}),
            ]
    mo.vstack(_sections)
    return


@app.cell(hide_code=True)
def _():
    server_form = mo.ui.dictionary(
        {
            "url": mo.ui.text(
                value=os.environ.get(URL_VARIABLE, ""),
                label="Server URL",
                placeholder="http://<host>:<port>/api/v1",
                full_width=True,
            ),
            "token": mo.ui.text(
                value=os.environ.get(TOKEN_VARIABLE, ""),
                kind="password",
                label="Token (if any)",
                full_width=True,
            ),
            "participant_id": mo.ui.text(
                value=git_config("user.email") or "",
                label="Your email",
                full_width=True,
            ),
            "participant_name": mo.ui.text(
                value=git_config("user.name") or "",
                label="Your name",
                full_width=True,
            ),
        }
    )
    mo.vstack(
        [
            mo.md("## Evaluation server"),
            mo.md("Needed only if your event has an evaluation server."),
            *server_form.values(),
        ]
    )
    return (server_form,)


@app.cell(hide_code=True)
def _():
    practice_button = mo.ui.run_button(
        label="Submit for practice",
        kind="success",
        tooltip=(
            "Packs the current files and runs them on hidden scenarios. "
            "The result appears here."
        ),
    )
    return (practice_button,)


@app.cell(hide_code=True)
def _(practice_button, server_form):
    _sections = [
        mo.md("## Practice on hidden scenarios"),
        mo.md("Scored on the server; the leaderboard ranks the fixed cases only."),
        practice_button,
    ]
    if practice_button.value:
        # Shown now, so the spinner appears below the heading instead of replacing it.
        mo.output.replace(mo.vstack(_sections))
        _form = server_form.value
        _receipt = None
        try:
            if not (
                _form["participant_id"].strip() and _form["participant_name"].strip()
            ):
                raise ServerError("enter your email and name")
            _server = EvaluationServer(_form["url"], _form["token"])
            _receipt = _server.submit(
                build_submission(REPOSITORY_DIR),
                _form["participant_id"].strip(),
                _form["participant_name"].strip(),
                final=False,
            )
            _problem = record_submission(_server, _receipt)
            with mo.status.spinner(
                title="Waiting for the practice evaluation"
            ) as _spinner:
                _document = _server.wait(
                    _receipt["id"],
                    on_status=lambda status: _spinner.update(subtitle=status),
                    on_retry=lambda message: _spinner.update(subtitle=message),
                )
        except (ServerError, SubmissionError, OSError) as error:
            _sections.append(
                mo.callout(
                    str(error)
                    if _receipt is None
                    else f"{error}\n\n{later_text(_receipt)}",
                    kind="danger",
                    title="Could not submit"
                    if _receipt is None
                    else "Could not follow the evaluation",
                )
            )
        else:
            _sections.append(
                mo.callout(
                    text_view("\n".join(receipt_lines(_document))),
                    kind="success" if _document.get("status") != "failed" else "danger",
                    title="Submission",
                )
            )
            _sections.append(mo.md(saved_note(_problem)))
            if _document.get("status") == "failed":
                _sections.append(mo.md(f"**Not evaluated:** {_document.get('error')}"))
            elif _document.get("result") is not None:
                _fixed = score_text(_document.get("fixed_score"))
                _stats = (
                    [
                        mo.stat(
                            _fixed,
                            label="Fixed cases",
                            caption="The leaderboard ranks by this score.",
                            bordered=True,
                        )
                    ]
                    if _fixed
                    else []
                )
                try:
                    _sections.append(
                        results_view(
                            EvaluationResult.from_dict(_document["result"]),
                            stats=_stats,
                        )
                    )
                except ResultFormatError as error:
                    _sections.append(
                        mo.callout(
                            str(error), kind="danger", title="Invalid result document"
                        )
                    )
    mo.vstack(_sections)
    return


@app.cell(hide_code=True)
def _():
    final_button = mo.ui.run_button(
        label="Submit as final",
        kind="warn",
        tooltip=(
            "Packs the current files and stores them as your final version. "
            "You get a receipt."
        ),
    )
    return (final_button,)


@app.cell(hide_code=True)
def _(final_button, server_form):
    _sections = [
        mo.md("## Submit the final version"),
        mo.md("Stored on the evaluation server for the final evaluation."),
        final_button,
    ]
    if final_button.value:
        # Shown now, so an earlier receipt does not stay visible while submitting.
        mo.output.replace(mo.vstack(_sections))
        _form = server_form.value
        try:
            if not (
                _form["participant_id"].strip() and _form["participant_name"].strip()
            ):
                raise ServerError("enter your email and name")
            _server = EvaluationServer(_form["url"], _form["token"])
            _receipt = _server.submit(
                build_submission(REPOSITORY_DIR),
                _form["participant_id"].strip(),
                _form["participant_name"].strip(),
                final=True,
            )
            _problem = record_submission(_server, _receipt)
        except (ServerError, SubmissionError, OSError) as error:
            _sections.append(
                mo.callout(str(error), kind="danger", title="Could not submit")
            )
        else:
            _sections.append(
                mo.callout(
                    text_view("\n".join(receipt_lines(_receipt))),
                    kind="success" if _receipt.get("status") != "failed" else "danger",
                    title="Final submission received",
                )
            )
            _sections.append(mo.md(saved_note(_problem)))
            if _receipt.get("status") == "failed":
                _sections.append(mo.md(f"**Not evaluated:** {_receipt.get('error')}"))
    mo.vstack(_sections)
    return


@app.cell(hide_code=True)
def _():
    board_button = mo.ui.run_button(
        label="Load leaderboard", tooltip="Click again to refresh."
    )
    mine_checkbox = mo.ui.checkbox(label="Only my practice submissions")
    return board_button, mine_checkbox


@app.cell(hide_code=True)
def _(board_button, mine_checkbox, server_form):
    _sections = [
        mo.md("## Leaderboard"),
        mo.hstack([board_button, mine_checkbox], justify="start", gap=1),
    ]
    if board_button.value:
        _form = server_form.value
        try:
            _server = EvaluationServer(_form["url"], _form["token"])
            _mine = _form["participant_id"].strip() if mine_checkbox.value else None
            if mine_checkbox.value and not _mine:
                raise ServerError(
                    "enter your email to see only your practice submissions"
                )
            _practice, _final = boards(_server, _mine)
        except ServerError as error:
            _sections.append(
                mo.callout(str(error), kind="danger", title="Could not load the board")
            )
        else:
            _score = {"Score": score_text}
            _content = []
            if _final is not None:
                _status = final_status(_final)
                if _status is not None:
                    _content.append(mo.callout(_status, kind="info"))
                if _final.get("published"):
                    _final_rows = board_rows(_final, *FINAL_COLUMNS, formatted=False)
                    _content += [
                        mo.md("### Final ranking"),
                        mo.ui.table(_final_rows, selection=None, format_mapping=_score)
                        if _final_rows
                        else mo.md("No final ranking."),
                    ]
            if _practice is not None:
                _rows = board_rows(_practice, *PRACTICE_COLUMNS, formatted=False)
                _total = _practice.get("total", len(_rows))
                _empty = (
                    "None of your practice submissions has been evaluated."
                    if mine_checkbox.value
                    else "No evaluated practice submissions."
                )
                _content += [
                    mo.md(f"### Practice leaderboard ({len(_rows)} of {_total})"),
                    mo.ui.table(_rows, selection=None, format_mapping=_score)
                    if _rows
                    else mo.md(_empty),
                ]
            if _practice is None and _final is None:
                _content.append(
                    mo.md(f"No leaderboard at {_server.url}; check the server address.")
                )
            elif not _content:
                _content.append(mo.md("Nothing to show yet."))
            _sections += _content
    mo.vstack(_sections)
    return


if __name__ == "__main__":
    app.run()
