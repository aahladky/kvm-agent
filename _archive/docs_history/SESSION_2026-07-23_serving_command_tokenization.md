# SESSION 2026-07-23 — Shell-aware serving command tokenization

## Outcome

The remaining concrete finding from the 2026-07-23 codebase review is fixed.
`parse_serving_cmd` no longer deletes every backslash and whitespace-splits a shell
command. It now preserves quoted paths, escaped spaces, and literal backslashes while
remaining fail-soft.

## Change

`kvm_agent/llm/serving.py` now:

- normalizes only backslash-newline and backslash-CRLF shell continuations;
- tokenizes the resulting command with standard-library `shlex.split(posix=True)`;
- returns `{}` for malformed shell syntax rather than raising or recording guessed
  parameters; and
- describes an unparsed resident command as `mmproj=unknown`, not as proof that the
  projector is absent.

This is parsing only. It does not evaluate variables, expand paths, execute a shell,
or adopt the external llama-swap configuration.

## Offline evidence

`tests/test_serving.py` retains the exact Holo launch command captured from
llama-swap and adds cases for:

- single-quoted model and mmproj paths containing spaces;
- an escaped space outside quotes;
- a literal backslash inside quotes;
- CRLF shell continuations; and
- an unterminated quote, which produces no claims and no exception.

The focused suite passes 16/16:
`runs/serving_parser_final_20260723_202026/focused_pytest.txt`.

The complete offline suite passes 184/184:
`runs/serving_parser_suite_20260723_202048/full_pytest.txt`.

## Live evidence and observed limitation

The first read-only probe found Holo configured but cold, with fast-7b resident:
`runs/serving_probe_20260723_201848/probe.json`. Warming Holo then exercised the
parser against the current live command and reported:

- Q4_K_M;
- context 64000;
- parallel 1;
- image token floor 1024;
- KV cache q8_0/q4_0;
- split mode `none`, tensor split `4,1`;
- mmproj present; and
- fast-7b still co-resident.

Cold load was 11.0 seconds and the immediate warm call was 0.1 seconds:
`runs/serving_probe_20260723_201921/probe.json`.

A deliberate probe of resident `fast-7b` parsed its OpenVINO source model and target
device correctly, but the command exited failed because `tools/serving_probe.py`
assumes the requested model is a vision model and requires mmproj:
`runs/serving_probe_20260723_201900/probe.json`. That assumption is correct for the
tool's normal/default Holo preflight and irrelevant to this parser fix, but
`--model` is not presently a general text-model health-check interface. No extra
option or model registry was added without a demonstrated project need.
