"""Restricted workstation adapter: identity readback and Verse build only."""

import argparse
import json
import re
import urllib.request
from pathlib import Path


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise ValueError("Editor endpoints must not redirect.")


def post(url, payload, headers=None):
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
    request = urllib.request.Request(
        url, json.dumps(payload).encode(), {"Content-Type": "application/json", **(headers or {})}
    )
    with opener.open(request, timeout=40) as response:
        return json.loads(response.read(1024 * 1024)), response.headers.get("Mcp-Session-Id")


def identity(project):
    if not project.is_file() or project.suffix != ".uefnproject" or not re.fullmatch(r"[A-Za-z0-9_]+", project.stem):
        raise ValueError("Select an existing UEFN project with a valid plugin name.")
    # The bridge receives only this fixed readback; no prompt or web input is evaluated.
    code = (
        "import unreal\n_result = {'mount': unreal.PluginBlueprintLibrary.get_plugin_base_dir("
        + repr(project.stem)
        + "), 'world': unreal.EditorLevelLibrary.get_editor_world().get_path_name()}"
    )
    response, _ = post("http://127.0.0.1:8765/execute", {"code": code})
    result = response.get("result", {})
    if not response.get("ok") or Path(result.get("mount", "")).resolve() != project.parent.resolve():
        raise ValueError("The editor has a different project open. No build was requested.")
    if not result.get("world", "").startswith("/" + project.stem + "/"):
        raise ValueError("The editor world does not belong to the configured project.")
    return result


def inspect_or_build(project, action):
    result = {"identity": identity(project), "action": action}
    if action == "uefn_inspect":
        return result
    if action != "uefn_build":
        raise ValueError("Only identity inspection and Verse builds are allowed.")
    headers = {"Accept": "application/json, text/event-stream", "MCP-Protocol-Version": "2025-11-25"}
    initialized, session = post(
        "http://127.0.0.1:8000/mcp",
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2025-11-25",
                "capabilities": {},
                "clientInfo": {"name": "gamedev-hq-verse", "version": "1"},
            },
        },
        headers,
    )
    if initialized.get("error"):
        raise ValueError("The native editor connection could not initialize.")
    if session:
        headers["Mcp-Session-Id"] = session
    built, _ = post(
        "http://127.0.0.1:8000/mcp",
        {
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/call",
            "params": {
                "name": "call_tool",
                "arguments": {"toolset_name": "ValkyrieToolset.VerseToolset", "tool_name": "BuildAll", "arguments": {}},
            },
        },
        headers,
    )
    if built.get("error") or built.get("result", {}).get("isError"):
        raise ValueError("Verse build request failed.")
    value = json.loads(built["result"]["content"][0]["text"])
    diagnostics = value.get("returnValue")
    result["diagnostics"] = diagnostics
    result["passed"] = diagnostics == []
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", type=Path, required=True)
    parser.add_argument("--action", choices=("uefn_inspect", "uefn_build"), required=True)
    args = parser.parse_args()
    print(json.dumps(inspect_or_build(args.project.resolve(), args.action)))


if __name__ == "__main__":
    main()
