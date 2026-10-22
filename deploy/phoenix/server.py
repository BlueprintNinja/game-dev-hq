"""Pinned Phoenix HTTP-only launcher. Run in the separate Python 3.13 runtime."""

import argparse
import importlib
import json
import os
import sys
from importlib.metadata import version
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    args = parser.parse_args()
    if version("arize-phoenix") != "20.24.0":
        raise RuntimeError("Revalidate the HTTP-only adapter before changing the pinned Phoenix version.")
    config = json.loads(args.config.read_text(encoding="utf-8"))
    os.environ.update(config["environment"])
    # This Phoenix release otherwise always binds its gRPC listener to [::].
    # Its own disabled mode suppresses that listener; OTLP/HTTP remains enabled.
    app_module = importlib.import_module("phoenix.server.app")
    grpc_module = importlib.import_module("phoenix.server.grpc_server")

    class HttpOnlyGrpcServer(grpc_module.GrpcServer):
        def __init__(self, *values, **keywords):
            keywords["disabled"] = True
            super().__init__(*values, **keywords)

    app_module.GrpcServer = HttpOnlyGrpcServer
    sys.argv = ["phoenix", "serve", "--host", "127.0.0.1", "--port", "6006", "--no-internet"]
    importlib.import_module("phoenix.server.main").main()


if __name__ == "__main__":
    main()
