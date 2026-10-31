# Spacecraft Autonomy Challenge: Template

[![License](https://img.shields.io/badge/license-MIT--0-blue.svg?style=flat)](LICENSE)

**English** | [日本語](README.ja.md)

Start here to write a strategy for the Spacecraft Autonomy Challenge.
A strategy operates a simulated spacecraft: it reads telemetry and schedules commands
so that the spacecraft accumulates a target delta-v without violating its limits.

The challenge problem, the simulator, and the `autonomy-challenge` command come from the
[`spacecraft-autonomy-challenge-core`](https://github.com/conjikidow/spacecraft-autonomy-challenge-core) package,
pinned to a release tag in `pyproject.toml`.

## Getting Started

### Requirements

- Python 3.12 or later
- [uv](https://docs.astral.sh/uv/)
- [Git](https://git-scm.com/)
- A GitHub account, to create your own repository from this template

### Create Your Repository

Click the **Use this template** button at the top of this repository on GitHub
to create your own repository, then clone it:

```sh
git clone https://github.com/<your-account>/<your-repository>.git
cd <your-repository>
```

> Note: if you do not need to keep your own strategy, you may clone this template directly instead.

### Open the Notebook

Run the notebook as an app:

```sh
uv run marimo run app.py
```

This opens the app in its grid layout in your browser.
In it, you edit `submission/strategy.py`, run it on a public scenario, check the submission, and submit it.

To see or change the notebook itself, open it in the editor:

```sh
uv run marimo edit app.py
```

In the editor, switch to the grid layout with the **Toggle app view** button at the bottom right,
or with `Ctrl+.` (`Cmd+.` on macOS).

## Working in the Notebook

The notebook shows the spacecraft, the mission, and the strategy API.
It then has one section for each step of working on your strategy:

- **Submission**: pick a file under `submission/` in the tree and edit it, starting with `submission/strategy.py`.
  Create a new file, or delete the selected file or empty folder, with the buttons next to the tree.
  `submission/strategy.py` cannot be deleted.
- **Try a scenario**: run your strategy on one public scenario, with full details.
- **Pack**: build `submission.zip` for download.
- **Check on public scenarios**: pack `submission/` and `pyproject.toml` and evaluate them on all public scenarios
  the way an evaluation server runs it.
- **Evaluation server**: enter the server settings.
- **Practice on hidden scenarios**: run a practice evaluation on the server.
- **Submit the final version**: make the final submission.
- **Leaderboard**: show the leaderboard.

### Submitting from the Notebook

If your event provides an evaluation server, the organizers give you the URL of its API, such as `http://<host>:<port>/api/v1`,
and, if it needs one, a token.
A URL without a path, such as `http://<host>:<port>`, is taken as `http://<host>:<port>/api/v1`.

Enter the server settings under **Evaluation server**.
The notebook pre-fills the URL and the token from the environment variables `EVALUATION_SERVER_URL` and `EVALUATION_SERVER_TOKEN`,
and your email and name from `git config`.
Submit from **Practice on hidden scenarios** for a practice evaluation,
or from **Submit the final version** for the final submission.

If the server has a leaderboard, the **Leaderboard** section shows it,
with whether final submissions are open and the time left, and the final ranking once it is published.

## Writing Your Strategy

Your strategy is the `MyStrategy` class in `submission/strategy.py`.
Put all of your code and files under `submission/`.
The directory of the strategy file is put first on the import path,
so `submission/strategy.py` can import other modules in `submission/`.
To read a data file next to your code, build its path from `Path(__file__).parent`.

Add the Python packages you need with uv, for example:

```sh
uv add numpy
```

## Strategy API

A strategy schedules commands with `commander.schedule(time_s, command)` and reads the telemetry it is given.
`brief.nominal_initial_telemetry` holds the nominal initial state; the actual readings may deviate from it.

| Command                   | Argument                 | Effect                                                                         |
| ------------------------- | ------------------------ | ------------------------------------------------------------------------------ |
| `SetPropulsionMode(mode)` | `mode`: `PropulsionMode` | Selects the propulsion mode                                                    |
| `SetHeater(on)`           | `on`: `bool`             | Switches the propulsion heater on or off                                       |
| `SetMomentumDump(on)`     | `on`: `bool`             | Switches the momentum dump on or off                                           |
| `DeclareCompletion()`     | none                     | Declares that the strategy considers the maneuver complete, which ends the run |

`PropulsionMode` has the members `OFF`, `THRUSTER_1`, and `THRUSTER_2`.
`Telemetry` has the fields `time_s`, `delta_v_mps`, `propulsion_temperature_c`, `battery_energy_wh`,
`wheel_momentum_nms`, `heater_on`, `propulsion_mode`, and `momentum_dump_on`.
The core documentation linked below describes the commands, the brief, and the spacecraft model in detail.

## How Practice Evaluations Work

- The result shows the last lines of the standard output and standard error of each case,
  so `print` is a way to debug a strategy on the server.
- The practice cases include random ones that are redrawn on every submission,
  so the same strategy can score differently from one submission to the next.
  The fixed cases are the same every time.
- The leaderboard ranks on the fixed cases only.
  The practice leaderboard ranks by the score on the server's fixed practice cases alone,
  not on all cases of a practice evaluation;
  a practice result also shows this score, as **Fixed cases**, when the server reports it.
- You can have one practice submission in flight at a time; submit again after the previous one has finished.
- By default, a practice submission has a total limit of 600 s for all its case executions together,
  and each case a limit of 300 s.
  The total limit does not include installing the submission's packages.
  These are settings of the evaluation server, and the organizers may change them.
  A case that exceeds its limit shows as `TIMEOUT`.
  When the total limit is exceeded, the case that was running and the remaining cases show as `TIMEOUT`,
  and the submission status stays completed.
- Use `tempfile` for temporary files, and do not use fixed paths or ports.
- If you use a planning engine, note that `up-lpg` needs `setuptools<81` with the current releases.
  Java-based engines need a Java runtime, and the server may apply a default heap setting to them.

## Using the Command Line

The command line is an alternative to the notebook for those who prefer it.

### Running Your Strategy

Run your strategy on the `nominal` scenario:

```sh
uv run autonomy-challenge run submission/strategy.py:MyStrategy
```

The command prints how the run ended and its metrics, computed from the true spacecraft state.

List the available scenarios and run another one:

```sh
uv run autonomy-challenge scenarios
uv run autonomy-challenge run submission/strategy.py:MyStrategy --scenario thruster-degradation
```

Add `--json` to print the metrics as JSON.

### Checking and Packing Your Submission

Pack `submission/` and `pyproject.toml` into `submission.zip` in memory
and evaluate it on all public scenarios the way an evaluation server runs it:

```sh
uv run autonomy-challenge check
```

Each scenario starts your strategy in a new Python process,
and packages of `pyproject.toml` that are missing are installed for the check only.
The command prints the scores, a summary, and a table of the cases.
`uv run autonomy-challenge pack` writes `submission.zip` to a file without evaluating it.

### Submitting to an Evaluation Server

Use the URL and the token that the organizers give you, as described in [Submitting from the Notebook](#submitting-from-the-notebook).

```sh
export EVALUATION_SERVER_URL=<URL>
export EVALUATION_SERVER_TOKEN=<token>
uv run submit.py            # practice evaluation on hidden scenarios: waits for the result and shows it
uv run submit.py --final    # final submission: shows the receipt
```

On Windows, or instead of the environment variables, pass `--server <URL>` and `--token <token>`.
If you stop waiting for a practice evaluation, it continues on the server; `uv run submit.py --status <id>` shows it later.
If the server stops answering while you wait, for example because it restarts, the client tries again for up to five minutes.
After any error that follows the upload, it prints the `--status` command that shows the result later.
Every accepted submission, from the command line or the notebook, is appended to `.submissions.log` in the project directory.
Each line has the id, kind, time, and server address, so you can find an id again, and git ignores the file.
`<id>` can be the full id or its start or end, such as the 8 characters that the leaderboard shows.
`submit.py` looks for it in `.submissions.log` first and then among your submissions on the server.
If more than one submission matches, it lists them.
You are identified by `git config user.email` and `git config user.name`;
`--participant-id` and `--participant-name` override them, and `uv run submit.py --help` lists all options.

If the server has a leaderboard, `uv run submit.py --board` shows it,
with whether final submissions are open and the time left, and the final ranking once it is published;
`--mine` lists only your practice submissions.

## Documentation

The core repository documents the API and the challenge, at the commit this template uses:

- [Writing a Strategy](https://github.com/conjikidow/spacecraft-autonomy-challenge-core/blob/v2.0.2/docs/strategy.md): lifecycle, mission brief, and commander
- [Commands and Timeline](https://github.com/conjikidow/spacecraft-autonomy-challenge-core/blob/v2.0.2/docs/timeline.md): command types and ordering rules
- [Spacecraft Model](https://github.com/conjikidow/spacecraft-autonomy-challenge-core/blob/v2.0.2/docs/spacecraft-model.md): state, dynamics, and telemetry
- [Simulation](https://github.com/conjikidow/spacecraft-autonomy-challenge-core/blob/v2.0.2/docs/simulation.md): order of events and termination
- [Challenge Problem](https://github.com/conjikidow/spacecraft-autonomy-challenge-core/blob/v2.0.2/docs/challenge.md): the problem and its public scenarios
- [Evaluation](https://github.com/conjikidow/spacecraft-autonomy-challenge-core/blob/v2.0.2/docs/evaluation.md): the metrics printed after a run
- [Command-Line Runner](https://github.com/conjikidow/spacecraft-autonomy-challenge-core/blob/v2.0.2/README.md#command-line-runner): options and exit status of `run`, `pack`, and `check`
- [Submission Archives](https://github.com/conjikidow/spacecraft-autonomy-challenge-core/blob/v2.0.2/docs/submission.md): what `submission.zip` contains
- [Participant Packages](https://github.com/conjikidow/spacecraft-autonomy-challenge-core/blob/v2.0.2/docs/dependencies.md): how the packages of `pyproject.toml` are reproduced
- [Scoring](https://github.com/conjikidow/spacecraft-autonomy-challenge-core/blob/v2.0.2/docs/scoring.md): how a case and a suite are scored

## License

This template is licensed under [MIT-0](LICENSE), so you can build your submission on it without carrying a notice along.
Files you add are yours, and you may license them however you like.
