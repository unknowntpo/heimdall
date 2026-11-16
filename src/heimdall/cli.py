"""`heimdall` command line: data, train, evaluate, sweep, guard, serve."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="heimdall")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("data", help="build data/*.jsonl and configs/data.lock.json from configs/sources.toml")
    t = sub.add_parser("train", help="fine-tune the encoder classifier")
    t.add_argument("--epochs", type=int, default=4)
    t.add_argument("--batch-size", type=int, default=16)
    t.add_argument("--lr", type=float, default=5e-5)
    t.add_argument("--cpu", action="store_true", help="force CPU")
    t.add_argument("--seed", type=int, default=42, help="seeds head init, dropout and shuffle order")
    t.add_argument("--artifact-dir", type=Path, default=Path("artifacts/encoder"))
    t.add_argument("--no-mlflow", action="store_true", help="do not track this run in MLflow")
    t.add_argument("--log-every", type=int, default=10, help="log train loss and lr every k steps")
    e = sub.add_parser("evaluate", help="evaluate keyword + encoder on test and the golden set")
    e.add_argument("--name", default="walking-skeleton")
    e.add_argument("--encoder-dir", type=Path, default=None, help="checkpoint dir (default artifacts/encoder)")
    e.add_argument("--golden", type=Path, default=Path("golden/zh-tw.jsonl"), help="golden set (skipped if absent)")
    e.add_argument("--no-mlflow", action="store_true", help="do not log metrics into the model's MLflow run")
    s = sub.add_parser("sweep", help="train and evaluate once per seed; report mean, std, min, max")
    s.add_argument("--seeds", type=int, nargs="+", required=True)
    s.add_argument("--name", required=True, help="writes artifacts/sweep/<name>/ and reports/<name>.json")
    s.add_argument("--epochs", type=int, default=4)
    s.add_argument("--batch-size", type=int, default=16)
    s.add_argument("--lr", type=float, default=5e-5)
    s.add_argument("--cpu", action="store_true", help="force CPU")
    s.add_argument("--no-mlflow", action="store_true")
    s.add_argument("--golden", type=Path, default=Path("golden/zh-tw.jsonl"), help="golden set (skipped if absent)")
    s.add_argument("--log-every", type=int, default=10)
    g = sub.add_parser("guard", help="classify one text")
    g.add_argument("text")
    g.add_argument("--model", choices=["encoder", "keyword"], default="encoder")
    v = sub.add_parser("serve", help="local HTTP service: GET /health, POST /classify (loopback only)")
    v.add_argument("--host", default="127.0.0.1", help="loopback address only; the service has no auth")
    v.add_argument("--port", type=int, default=8765)
    v.add_argument("--encoder-dir", type=Path, default=Path("artifacts/encoder"))
    v.add_argument("--model", choices=["encoder", "keyword"], default="encoder")
    v.add_argument("--cpu", action="store_true", help="force CPU")
    args = p.parse_args(argv)

    if args.cmd == "data":
        import heimdall.data as data
        try:
            data.prepare()
        except data.PipelineError as err:
            print(f"error: {err}", file=sys.stderr)
            return 1
    elif args.cmd == "train":
        from heimdall.encoder import train
        train(args.epochs, args.batch_size, args.lr, args.cpu, seed=args.seed, artifact_dir=args.artifact_dir,
              track=not args.no_mlflow, log_every=args.log_every)
    elif args.cmd == "evaluate":
        from heimdall.evaluate import evaluate
        try:
            evaluate(args.name, args.encoder_dir, args.golden, track=not args.no_mlflow)
        except FileNotFoundError as err:
            print(f"error: {err}", file=sys.stderr)
            return 2
    elif args.cmd == "sweep":
        from heimdall.sweep import sweep
        try:
            sweep(args.name, args.seeds, epochs=args.epochs, batch_size=args.batch_size, lr=args.lr,
                  force_cpu=args.cpu, track=not args.no_mlflow, golden_path=args.golden, log_every=args.log_every)
        except (FileNotFoundError, ValueError) as err:
            print(f"error: {err}", file=sys.stderr)
            return 2
    elif args.cmd == "guard":
        if args.model == "keyword":
            from heimdall.keyword import classify
            out = classify(args.text)
        else:
            from heimdall.encoder import EncoderGuard
            try:
                guard = EncoderGuard()
            except FileNotFoundError as err:
                print(f"error: {err}", file=sys.stderr)
                return 2
            out = guard.classify(args.text)
        print(json.dumps(out, ensure_ascii=False))
    elif args.cmd == "serve":
        import heimdall.serve as serve
        return serve.run(host=args.host, port=args.port, encoder_dir=args.encoder_dir, model=args.model,
                         force_cpu=args.cpu)
    return 0


if __name__ == "__main__":
    sys.exit(main())
