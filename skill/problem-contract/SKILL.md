---
name: problem-contract
description: Extract a compact behavioral contract and an implementation-independent test plan from an HLS task before the first candidate is generated.
---

# Problem Contract

## Trigger

Use once before initial code generation when the task contains enough information to identify the required top-level behavior. Do not use during repair to reinterpret a specification around the current candidate.

## Inputs

- Original problem statement.
- Declared top function and model-visible public interface files.
- Target configuration only when it changes observable requirements.

Candidate source, hidden tests, diagnostic logs, and retrieved historical solutions are not inputs.

## Outcome

Produce `contract.json` and `test_plan.json` following [the artifact contract](references/artifacts.md). The artifacts must distinguish explicit requirements from assumptions and unresolved ambiguities. They are shared inputs to implementation and test generation; neither may silently change them.

## Procedure

1. Identify the top-level interface and observable behavior.
2. Record input domains, output meaning, state across invocations, initialization, boundary behavior, numeric semantics, and explicit performance constraints.
3. List only risks that would change an implementation decision or test category.
4. Create a test plan from the specification and contract before reading any candidate implementation.
5. Mark missing facts as unresolved; do not invent convenient values.

## Acceptance

Accept only if every contract claim is traceable to a model-visible source or is explicitly labeled as an assumption. If an unresolved ambiguity prevents a unique implementation, return `needs_clarification` rather than manufacturing an oracle.

## Retrieval contract

Retrieval is optional. When available, use only a pattern namespace to clarify common behavioral shapes, with at most three compact evidence items. Retrieved examples may suggest questions or risks but may not override the task or supply a task solution.

## Failure modes

- Copying a candidate implementation into the behavior contract.
- Turning an example input into a universal rule.
- Mixing optimization choices with required behavior.
- Hiding ambiguity by emitting a precise but unsupported field.

## Validation status

Integrated into the Agent behind a default-off policy flag. Contract generation is frozen before candidate 0 and covered by synthetic contract tests; no benchmark benefit claim has been established yet.
