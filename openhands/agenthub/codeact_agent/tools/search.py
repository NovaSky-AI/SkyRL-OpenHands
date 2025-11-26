from litellm import (
    ChatCompletionToolParam,
    ChatCompletionToolParamFunctionChunk,
)

_SEARCH_ENTITY_DESCRIPTION = """
  Advanced code search tool that searches for code snippets, entities, and functions using graph-based indexing.
    •	Searches for code entities (classes, functions, files) by name or keywords using pre-built code indices.
    •	Can search for specific entities using qualified names (e.g., 'file.py:ClassName.method_name').
    •	Uses graph-based entity search combined with exact substring matching and BM25 semantic search.
    •	Filters results by file patterns or specific file paths.
    •	Returns structured results with code snippets, entity definitions, and relevant context.
  Usage combinations:
    - search_terms only: searches across all Python files (or pattern-matched files)
    - search_terms + file_path_or_pattern: limits search to specific file(s) matching pattern
  
  Example:
    <function=search>
    <parameter=search_terms>["Series","Index"]</parameter>
    <parameter=file_path_or_pattern>/testbed/**/*.py</parameter>
    </function>

    <function=search>
    <parameter=search_terms>["aiohttp/client.py:ClientSession"]</parameter>
    </function>

    <function=search>
    <parameter=search_terms>["aiohttp/client.py:ClientSession.__init__"]</parameter>
    </function>
"""

def create_search_files_tool(
    use_short_description: bool = False,
) -> ChatCompletionToolParam:
    
    return ChatCompletionToolParam(
        type='function',
        function=ChatCompletionToolParamFunctionChunk(
            name='search',
            description=_SEARCH_ENTITY_DESCRIPTION,
            parameters={
                'type': 'object',
                'properties': {
                    'search_terms': {
                        'type': 'array',
                        'items': {'type': 'string'},
                        'description': 'A list of names, keywords, or code snippets to search for within the codebase. '
                        'This can include potential function names, class names, or general code fragments.'
                    },
                    'file_path_or_pattern': {
                        'type': 'string',
                        'description': 'A glob pattern or specific relative file path used to filter search results '
                        'to particular files or directories. Defaults to "**/*.py", meaning all Python files are searched by default.'
                    },
                },
                'required': ['search_terms'],
            },
        ),
    )