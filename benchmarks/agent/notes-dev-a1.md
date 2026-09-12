# Notes from earlier work on this repository

Rendered from `dev-a1` by `render_notes.py`. Same content as the memory store, grouped by kind, in corpus order.

## Decisions

- Value precedence is command line, then environment variable, then default. A parameter consults the environment only when the command line supplied nothing, which is why an envvar that resolves to a value suppresses the default rather than merging with it. _(tags: envvar, resolution, precedence)_

- A default_map entry may be a callable, and is invoked to produce the value when the lookup is asked to call it. Membership in the map is therefore decided before the value is inspected, because inspecting it can mean running it. _(tags: defaults, default_map, callable)_

- Public API is fully annotated and the package ships a py.typed marker, so a change to a public signature is a typing change for downstream users even when runtime behaviour is unchanged. _(tags: types, annotations)_

## Constraints

- Environment-variable resolution treats an empty string as absent, not as a value. Parameter.resolve_envvar_value returns a value only when it is truthy, so FOO= and an unset FOO are the same thing to a parameter. _(tags: envvar, resolution)_

- show_envvar is independent of whether envvar is set. An option can ask to show its environment variable while having none configured, in which case any name shown has to come from the context's auto_envvar_prefix. _(tags: envvar, help, show_envvar)_

- UNSET is an internal sentinel that distinguishes 'no value was supplied' from 'the supplied value was None'. It is never a user-facing value: anything that reads a mapping of supplied values has to treat a stored UNSET as absence, not as data to hand back. _(tags: sentinel, defaults, unset)_

- Click has no runtime dependencies and targets the standard library only. A fix that would require a third-party package is out of bounds regardless of how well it reads. _(tags: packaging, dependencies)_

## Procedures

- Run the suite from the checkout with PYTHONPATH=src and pass -p no:randomly. Click's own test group includes pytest-randomly, which reorders tests per run; leaving it on makes two runs of the same commit differ for reasons unrelated to the change. _(tags: testing, pytest)_

- CliRunner invokes a command in-process and returns a result carrying output, exit_code and exception. Assert on exit_code for failure paths rather than on the presence of a traceback, because a handled usage error exits non-zero without raising. _(tags: testing, clirunner)_

- Every user-visible change gets a CHANGES entry naming the version it lands in. The entry is written with the change, not at release time. _(tags: release, changelog)_

- Test configuration lives in setup.cfg; add pytest settings to its [tool:pytest] section. _(tags: testing, pytest)_

## Observed failures

- Older test modules fail at collection, not at assertion, under pytest 9.x: 'PytestRemovedIn10Warning: Passing a non-Collection iterable to parametrize is deprecated' aborts the whole file. The cause is the pinned pytest, not the code under test. Pinning pytest below 9 collects and runs them. _(tags: testing, pytest, collection)_

## Observations

- Option overrides resolve_envvar_value to add the automatic-envvar path: when no explicit envvar is set and the context carries an auto_envvar_prefix, the name is derived as PREFIX_PARAMNAME. Explicit envvars are handled by the base implementation. _(tags: envvar, option, core)_
