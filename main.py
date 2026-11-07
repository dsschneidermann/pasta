"""Entrypoint for the pasta MCP server.

Runs as an HTTP server on port 8000 by default with hot module reload (see
`src.hmr_server`); pass `--stdio` to run over stdio instead (for an MCP client
that launches the server as a subprocess).
"""

import argparse


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the pasta MCP server.")
    parser.add_argument(
        "--stdio",
        action="store_true",
        help="Run over stdio (for a client that spawns the server) instead of HTTP.",
    )
    parser.add_argument("--host", default="0.0.0.0", help="HTTP host to bind (default: 0.0.0.0).")
    parser.add_argument("--port", type=int, default=8000, help="HTTP port to bind (default: 8000).")
    args = parser.parse_args()

    if args.stdio:
        # Local import: the stdio path has no hot reload, so importing the server module
        # directly is fine. The HTTP path must NOT import it here - src.hmr_server
        # imports it through the reload finder so it becomes hot-reloadable.
        from src.server import mcp

        mcp.run(transport="stdio")
    else:
        from src.hmr_server import run_dev_server

        run_dev_server(host=args.host, port=args.port)


if __name__ == "__main__":
    main()
