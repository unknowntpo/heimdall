"""`heimdall` command line: data, train, evaluate, guard."""

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
    e = sub.add_parser("evaluate", help="evaluate keyword + encoder on test and the golden set")
    e.add_argument("--name", default="walking-skeleton")
    e.add_argument("--encoder-dir", type=Path, default=None, help="checkpoint dir (default artifacts/encoder)")
    e.add_argument("--golden", type=Path, default=Path("golden/zh-tw.jsonl"), help="golden set (skipped if absent)")
    g = sub.add_parser("guard", help="classify one text")
    g.add_argument("text")
    g.add_argument("--model", choices=["encoder", "keyword"], default="encoder")
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
        train(args.epochs, args.batch_size, args.lr, args.cpu)
    elif args.cmd == "evaluate":
        from heimdall.evaluate import evaluate
        try:
            evaluate(args.name, args.encoder_dir, args.golden)
        except FileNotFoundError as err:
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
    return 0


if __name__ == "__main__":
    sys.exit(main())
