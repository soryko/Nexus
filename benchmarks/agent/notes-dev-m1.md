# Notes from earlier work on this repository

Rendered from `dev-m1` by `render_notes.py`. Same content as the memory store, grouped by kind, in corpus order.

## Decisions

- In `Option.__init__`, flag/value-ness is auto-detected when `is_flag` is left as `None`: a set `flag_value` makes the option a flag, while an explicit `is_flag=False` keeps it a value-taking option (documented in tests as a "non-flag with flag_value"). The two declarations are deliberately independent, so `flag_value` can serve non-flag options as the value used when the option is passed without one. When `is_flag` is explicitly `False`, the code also derives `_flag_needs_value` from whether `default` is `UNSET`. _(tags: flags, is_flag, options)_

- The parser only treats a following token as a value for a value-taking option when the option is not flagged `_flag_needs_value`, or when the token does not look like the start of another option (prefix character and length > 1). This is what lets an optional-value option be followed by another option instead of swallowing it; the check lives in `_get_value_from_state` in src/click/parser.py. _(tags: options, parsing)_

- Option.__init__ reconciles an explicit default=True with an explicit flag_value: it discards the True and stores the flag_value as the option's own default. The in-code comment justifies this (citing issue 3024 / PR 3030) by noting that, given the condition, a flag cannot have default True and a different flag_value. As a result, when such an option is not supplied on the command line, default resolution yields the flag_value. Option.get_default is inherited from Parameter; the flag_value is stored directly in self.default rather than through any special wrapper. _(tags: core, defaults, flags)_

- A flag option carries its activated value in Option.flag_value. When the option is activated, that value is substituted more or less directly: consume_value replaces a value-less flag with the flag_value, and value_from_envvar returns the flag_value when an envvar string equals it. When flag_value is set and no explicit type is given, the option's type is inferred from it (types.convert_type), and a boolean flag_value selects BoolParamType. So a flag_value is treated as a value throughout; it is also what Option.to_info_dict reports as the flag value. _(tags: core, flags, type-detection)_

- `Command.get_help_option(ctx)` CONSTRUCTS an option from `ctx.help_option_names` each time it is called, and `Command.get_params(ctx)` appends whatever it returns to the parameter list it hands back. `iter_params_for_processing` orders parameters by whether they appear in the invocation order and by `is_eager`, comparing the parameter OBJECTS it is given. _(tags: core, help, params)_

- Callbacks registered with `ctx.call_on_close()` go onto the context's `ExitStack` -- `self._exit_stack.callback(f)` -- and `Context.close()` is just `self._exit_stack.close()`. There is no separate `_close_callbacks` list; the stack is the only place a cleanup is held. _(tags: context, cleanup, core)_

- `Command.get_help_option_names(ctx)` preserves the order given in `ctx.help_option_names`, so the first name declared there is the first returned and the option's primary spelling is stable. _(tags: core, help, params)_

- `UsageError` carries an optional context and prints the command's usage line before its message; `ParamType.fail()` raises the parameter-scoped variant so the offending parameter is named in the output. _(tags: exceptions, usage)_

## Constraints

- Click has two distinct notions of "used without a value" for options. `Option.is_flag` means the option never consumes a following token. Separately, `Option._flag_needs_value` means the option does take a value but is allowed to be supplied without one: the parser (`_get_value_from_state` in src/click/parser.py) then stores the `FLAG_NEEDS_VALUE` sentinel instead of raising, and `Option.consume_value` resolves that sentinel to `flag_value` (or starts a prompt when a prompt is configured). The sentinel and its documented meaning live in src/click/_utils.py. _(tags: flags, options, parsing)_

- A parameter's `default` and its `flag_value` answer different questions and are kept independent: the default is the value when the option is absent from the command line, while `flag_value` is the value when the option is present but supplied with no argument. tests/test_options.py documents this explicitly across the flag_value/default and optional-value groups. _(tags: defaults, flags, options)_

- A parameter's default can be a callable factory. When a default is resolved with the "call" switch enabled — Parameter.get_default(ctx, call=True) and Context.lookup_default(name, call=True) — any callable value is invoked and its return value is used instead; with call=False the callable object is returned unchanged. consume_value relies on this to turn a callable default (and a callable entry in a context's default_map) into a concrete value. So "callable" is a meaningful property of defaults, whereas a value that merely happens to be callable in some other role is not meant to be invoked by this path. _(tags: callable, core, defaults)_

- Value resolution for a parameter follows a fixed precedence, described in the Parameter.consume_value docstring: a value supplied on the command line wins, then an environment variable, then an entry in the context's default_map, then the parameter's own default. get_default inspects the default_map first and only falls back to the parameter's own default when the map does not contain the name; an explicit None entry in the map still counts as present and therefore takes precedence. A callable found through the default_map is invoked when resolved with call enabled, just like a callable parameter default. _(tags: core, default_map, defaults)_

- Cleanup callbacks registered with `ctx.call_on_close()` are collected in `Context._close_callbacks` and run by `Context.close()`, which `Context.__exit__` calls. `Command.make_context` parses arguments inside `with ctx.scope(cleanup=False):` and then RETURNS the context to its caller, so closing it is the caller's responsibility and does not happen inside `make_context`. _(tags: context, cleanup, core)_

- An option's `_flag_needs_value` is set from whether a `flag_value` was supplied: `self._flag_needs_value = flag_value is not None`. There is no separate sentinel for a flag supplied without a value; the parser stores `None` and `consume_value` resolves it. _(tags: flags, options, parsing)_

- `click.style()` composes ANSI escape sequences and `click.secho()` is `echo` with `style` applied to its message; `click.unstyle()` strips them again. Colour is stripped automatically when the output stream is not a terminal. _(tags: termui, output)_

- Shell completion lives in `src/click/shell_completion.py`: a `CompletionItem` carries the value and its help text, and `ParamType.shell_complete` is the hook a custom type implements to offer completions. _(tags: completion, shell)_

- A `Group` resolves a subcommand name through `Group.get_command(ctx, name)` and lists what it can offer through `list_commands(ctx)`; `invoke_without_command` decides whether the group's own callback runs when no subcommand is given. _(tags: groups, invocation)_

## Procedures

- Option behaviour in this repo is exercised with parametrized cases over `(option declaration / CLI args, expected value)` driven through `click.testing.CliRunner`, commonly invoking with `standalone_mode=False, catch_exceptions=False` so the callback's return value is asserted directly. The optional-value and flag_value/default groups in tests/test_options.py follow this shape. _(tags: conventions, options, tests)_

- Behavior in this repository is exercised through click.testing.CliRunner: the test suite supplies a `runner` fixture, a CLI is invoked with runner.invoke(cli, args), and assertions are made against the resulting .output/.return_value (with standalone_mode=False to capture return values). Command-line behavior can also be checked ad hoc by putting `src` on PYTHONPATH and constructing CliRunner directly. I used both approaches in this session: an ad-hoc CliRunner script to confirm behavior, and the pytest suite to check for regressions. _(tags: clirunner, testing)_

- The checkout is a `src/` layout and is not installed. Run anything against it with `PYTHONPATH=src`, and the test suite with `PYTHONPATH=src <interpreter> -m pytest <paths> -q`. A bare `python3` on this machine may resolve to an interpreter with no pytest. _(tags: pytest, testing, tooling)_

- The `click.Path` type checks existence, file/directory kind and readability before conversion, and `resolve_path=True` makes it return an absolute, symlink-resolved path rather than the string the user typed. _(tags: paths, types)_

## Observed failures

- `uv run ...` does not work in this sandbox: it exits with "Failed to discover managed Python installations ... failed to read directory ~/.local/share/uv/python: Operation not permitted (os error 1)". Running against the checkout with the system interpreter instead works, e.g. `PYTHONPATH=src python3 -m pytest tests/test_options.py`. _(tags: sandbox, tests, tooling)_

- pyproject.toml sets pytest `filterwarnings = ["error"]`. With a pytest new enough to emit `PytestRemovedIn10Warning` for parametrize iterators, collection of tests/test_basic.py aborts as an error before any test runs. Running with `-W "ignore::pytest.PytestRemovedIn10Warning"` lets the suite run. _(tags: pytest, tests, tooling)_

- Running the test suite via `uv run` fails in this sandbox: uv cannot read its managed Python directory (`/Users/soko/.local/share/uv/python`, "Operation not permitted") and aborts with "Failed to discover managed Python installations". What worked instead was running pytest from the Homebrew Python with the source tree on the path, e.g. `PYTHONPATH=src /opt/homebrew/bin/python3.14 -m pytest tests/ ...`. Under the installed pytest major version, collection errors out on `PytestRemovedIn10Warning` for existing parametrizations that pass a non-Collection iterable; suppressing that warning category (`-W ignore::pytest.PytestRemovedIn10Warning`) lets the suite run. Separately, one test that resolves a default directory path fails in this sandbox because `/tmp` is not readable here, which is environmental and independent of source edits. _(tags: environment, pytest, testing)_
