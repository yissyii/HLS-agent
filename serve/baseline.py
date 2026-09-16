import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from serve.inference import Failure, generate, load_config, project_path
from evaluation.lifecycle import evaluate as development_evaluation, output_path


def main():
    parser = argparse.ArgumentParser(description="One problem-only API generation; no tools or retries")
    parser.add_argument("problem")
    parser.add_argument("output")
    parser.add_argument("--config")
    args = parser.parse_args()
    return development_evaluation(args, _evaluate, output=args.output, output_is_file=True)


def _evaluate(args):
    try:
        problem = project_path(args.problem).read_text(encoding="utf-8")
        output = output_path(project_path(args.output))
        generate(problem, load_config(args.config), output)
        print("Baseline response saved: " + str(output))
        return 0
    except Failure as error:
        print(error.category + ": " + str(error), file=sys.stderr)
    except (OSError, ValueError, KeyError, TypeError):
        print("configuration_or_io_error: check input files and runtime configuration", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
