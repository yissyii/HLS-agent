import argparse
import sys

from inference import Failure, generate, load_config, project_path


def main():
    parser = argparse.ArgumentParser(description="One problem-only API generation; no tools or retries")
    parser.add_argument("problem")
    parser.add_argument("output")
    parser.add_argument("--config")
    args = parser.parse_args()
    try:
        problem = project_path(args.problem).read_text(encoding="utf-8")
        output = project_path(args.output)
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
