"""Generate/check the README catalog from the actual FastMCP tool schemas."""
import argparse
import ast
import asyncio
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))

START, END = '<!-- TOOLS:START -->', '<!-- TOOLS:END -->'


async def catalog():
    from marvelous_designer_mcp.server import mcp
    from marvelous_designer_mcp import __version__
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


def static_catalog():
    """Documentation generation only: never import the server or call MD."""
    version_tree=ast.parse((ROOT/'src/marvelous_designer_mcp/__init__.py').read_text(encoding='utf-8'))
    version=next(ast.literal_eval(node.value) for node in version_tree.body if isinstance(node,ast.Assign)
                 and any(isinstance(target,ast.Name) and target.id=='__version__' for target in node.targets))
    tree=ast.parse((ROOT/'src/marvelous_designer_mcp/server.py').read_text(encoding='utf-8'))
    tools=sorted((node for node in tree.body if isinstance(node,(ast.FunctionDef,ast.AsyncFunctionDef))
                  and any(isinstance(d,ast.Call) and isinstance(d.func,ast.Attribute) and d.func.attr=='tool'
                          for d in node.decorator_list)),key=lambda node:node.name)
    rows=[START,'',f'**Total: {len(tools)} MCP tools (v{version}).**','',
          'Every registered tool is listed individually below. Required inputs are shown;',
          'the MCP schema supplies optional settings and defaults.','',
          '| Tool | Required inputs | What it does |','|---|---|---|']
    for node in tools:
        positional=node.args.posonlyargs+node.args.args
        required=[arg.arg for arg in positional[:len(positional)-len(node.args.defaults)]]
        required.extend(arg.arg for arg,default in zip(node.args.kwonlyargs,node.args.kw_defaults) if default is None)
        inputs=', '.join(f'`{name}`' for name in required) or '—'
        description=(ast.get_docstring(node) or '').strip().splitlines()[0].replace('|','\\|')
        rows.append(f'| `{node.name}` | {inputs} | {description} |')
    rows.extend(['',END])
    return '\n'.join(rows)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--check', action='store_true')
    parser.add_argument('--static',action='store_true',help='Generate documentation from source only; no server imports or API calls')
    args = parser.parse_args()
    file = ROOT / 'README.md'
    content = file.read_text(encoding='utf-8')
    if START not in content or END not in content:
        raise SystemExit('README tool catalog markers are missing')
    first, last = content.index(START), content.index(END) + len(END)
    updated = content[:first] + (static_catalog() if args.static else asyncio.run(catalog())) + content[last:]
    if args.check:
        if updated != content:
            raise SystemExit('README tool catalog is stale. Run scripts/update_tool_catalog.py')
        print('README catalog matches all registered tool schemas.')
    else:
        file.write_text(updated, encoding='utf-8')
        print('Updated README tool catalog.')


if __name__ == '__main__':
    main()
