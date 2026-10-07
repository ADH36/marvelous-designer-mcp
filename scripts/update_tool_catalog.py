"""Generate/check the README catalog from the actual FastMCP tool schemas."""
import argparse
import asyncio
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from marvelous_designer_mcp.server import mcp
from marvelous_designer_mcp import __version__

START, END = '<!-- TOOLS:START -->', '<!-- TOOLS:END -->'


async def catalog():
    tools = sorted(await mcp.list_tools(), key=lambda tool: tool.name)
    rows = [START, '', f'**Total: {len(tools)} MCP tools (v{__version__}).**', '',
            'Every registered tool is listed individually below. Required inputs are shown;',
            'the MCP schema supplies optional settings and defaults.', '',
            '| Tool | Required inputs | What it does |', '|---|---|---|']
    for tool in tools:
        required = ', '.join(f'`{name}`' for name in tool.inputSchema.get('required', [])) or '—'
        description = tool.description.strip().splitlines()[0].replace('|', '\\|')
        rows.append(f'| `{tool.name}` | {required} | {description} |')
    rows.extend(['', END])
    return '\n'.join(rows)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--check', action='store_true')
    args = parser.parse_args()
    file = ROOT / 'README.md'
    content = file.read_text(encoding='utf-8')
    if START not in content or END not in content:
        raise SystemExit('README tool catalog markers are missing')
    first, last = content.index(START), content.index(END) + len(END)
    updated = content[:first] + asyncio.run(catalog()) + content[last:]
    if args.check:
        if updated != content:
            raise SystemExit('README tool catalog is stale. Run scripts/update_tool_catalog.py')
        print('README catalog matches all registered tool schemas.')
    else:
        file.write_text(updated, encoding='utf-8')
        print('Updated README tool catalog.')


if __name__ == '__main__':
    main()
