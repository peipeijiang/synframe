"""Local CLI. Only `generate --submit` spends money, and only within the spec's caps."""
import argparse
import json
import sys
from pathlib import Path

from .planning import PlanError, plan_from_data
from .speech import compare
from .batch import instantiate, ready_tasks, retryable_candidates
from .catalog import select


def main():
    parser = argparse.ArgumentParser(prog="synframe")
    parser.add_argument("command", choices=["generate", "select", "render", "qc", "doctor",
                                            "run", "intake", "shots", "plan", "speech", "batch", "catalog",
                                            "timeline", "preview", "prompt", "continuity", "upstream", "sequence",
                                            "fastcut"])
    parser.add_argument("input", type=Path, nargs="?", help="JSON input file (qc: run directory)")
    parser.add_argument("--output", type=Path, help="Timeline output file")
    spend = parser.add_mutually_exclusive_group()
    spend.add_argument("--quote", action="store_true", help="generate: read live prices, spend nothing")
    spend.add_argument("--submit", action="store_true", help="generate: submit within the spec's caps")
    parser.add_argument("--poll-timeout", type=float, help="generate: seconds to keep polling")
    parser.add_argument("--count", type=int, help="render: number of videos")
    parser.add_argument("--no-transcribe", action="store_true", help="qc: skip local Whisper")
    parser.add_argument("--whisper-model", default="large-v3-turbo", help="qc: cached Whisper model")
    args = parser.parse_args()
    if args.command == "doctor":
        from .doctor import check, print_report
        return 0 if print_report(check())["ready"] else 1
    if args.input is None:
        parser.error("input is required for " + args.command)
    try:
        if args.command in ("generate", "select", "render", "qc"):
            log = lambda message: print(message, file=sys.stderr, flush=True)  # noqa: E731
            if args.command == "generate":
                from .generate import run as generate
                mode = "submit" if args.submit else ("quote" if args.quote else "dry-run")
                result = generate(args.input, mode, timeout_sec=args.poll_timeout, log=log)
            elif args.command == "select":
                from .generate import select
                result = select(args.input)
            elif args.command == "render":
                if not args.output:
                    raise ValueError("render requires --output RUN_DIR")
                from .assemble import render_run
                result = render_run(args.input, args.output, args.count, log=log)
            else:
                from .assemble import qc_run
                result = qc_run(args.input, not args.no_transcribe, args.whisper_model, log=log)
            print(json.dumps(result, ensure_ascii=False, indent=2))
            return 0 if result.get("status") not in ("blocked", "stopped_before_spend", "over_budget") else 3
        data = json.loads(args.input.read_text(encoding="utf-8"))
        if args.command == "run":
            if not args.output:
                raise ValueError("run requires --output")
            from .workflow import run
            result = run(data, args.output)
            if result["status"] != "technical_handoff_ready":
                print(json.dumps(result, ensure_ascii=False, indent=2))
                return 2
        elif args.command == "intake":
            if not args.output:
                raise ValueError("intake requires --output")
            from .intake import intake
            result = intake(data, args.output)
        elif args.command == "shots":
            from .shots import review_shots
            evidence_dir = Path(data["evidence_dir"])
            source = json.loads((evidence_dir / "source_manifest.json").read_text(encoding="utf-8"))
            result = review_shots(evidence_dir, data["shots"], source["duration_seconds"])
            if args.output:
                from .output import new_output
                with new_output(args.output, ".json") as temp:
                    temp.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        elif args.command == "plan":
            result = plan_from_data(data)
        elif args.command == "sequence":
            from .sequence import plan as sequence_plan, to_tracks
            result = sequence_plan(data)
            if args.output:
                if args.output.exists():
                    raise ValueError("output already exists: " + str(args.output))
                args.output.mkdir(parents=True)
                for video in result["videos"]:
                    (args.output / (video["name"] + ".json")).write_text(
                        json.dumps(video, ensure_ascii=False, indent=1), encoding="utf-8")
                    (args.output / (video["name"] + "-tracks.json")).write_text(
                        json.dumps(to_tracks(video), ensure_ascii=False, indent=1), encoding="utf-8")
                (args.output / "plan.json").write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
                result = {k: v for k, v in result.items() if k != "videos"}
                result["written"] = str(args.output)
        elif args.command == "fastcut":
            if not args.output:
                raise ValueError("fastcut requires --output")
            from .fastcut import render, measure
            result = render(data, args.output)
            result["measure"] = measure(args.output)
        elif args.command == "speech":
            if "asr" in data:
                transcript = data["asr"]
                words = [word for segment in transcript.get("segments", []) for word in segment.get("words", [])]
                observed = transcript["text"]
            else:
                words = None
                observed = data["observed"]
            result = compare(data["expected"], observed, data["locale"], data.get("critical_terms", []), words)
        elif args.command == "batch":
            result = {"ready": ready_tasks(data["tasks"]), "retryable": retryable_candidates(data.get("candidates", []))}
        elif args.command == "catalog":
            if "db_path" in data:
                from .catalog import open_catalog, query, record
                with open_catalog(data["db_path"]) as db:
                    for asset in data.get("record", []):
                        record(db, asset)
                    result = query(db, data["sku"], data.get("persona"))
            else:
                result = select(data["assets"], data["sku"], data.get("persona"))
        elif args.command == "prompt":
            from .prompt import compose
            result = compose(data["action"], data["dialogue"], data["locale"],
                             data["duration_sec"], data["max_duration_sec"])
        elif args.command == "continuity":
            from .continuity import validate_piece
            result = validate_piece(data)
        elif args.command == "upstream":
            from .upstream import validate
            result = validate(data["skill"], data["bundle"])
        elif args.command == "timeline":
            if not args.output:
                raise ValueError("timeline requires --output")
            from .timeline import export_timeline
            result = export_timeline(data, args.output)
        else:
            if not args.output:
                raise ValueError("preview requires --output")
            from .preview import render_preview
            result = render_preview(data, args.output)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except (OSError, ValueError, KeyError, TypeError, RuntimeError, PlanError) as exc:
        print("synframe: " + str(exc), file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
