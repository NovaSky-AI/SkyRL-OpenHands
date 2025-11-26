from dataclasses import dataclass
from typing import ClassVar, List, Optional
from openhands.events.action.action import Action, ActionSecurityRisk
from openhands.core.schema import ActionType

@dataclass
class SearchAction(Action):
    """Searches for code entities, snippets, or line-based context within a codebase.

    Attributes:
        search_terms (List[str] | None): List of names, keywords, or code fragments to search.
            Can include function names, class names, or general snippets. Optional if `line_nums` is provided.
        line_nums (List[int] | None): Specific line numbers to extract context from. Must be paired
            with a concrete `file_path_or_pattern` referring to a single file.
        file_path_or_pattern (str): A glob pattern or exact file path used to restrict the search scope.
            Defaults to "**/*.py". If `line_nums` is provided, must be a specific file path.
        
        thought (str): Reasoning behind the action.
        action (str): Always ActionType.SEARCH.
        runnable (bool): Always True.
        security_risk (ActionSecurityRisk | None): Optional risk annotation.

    Behavior (expected from the executor):
        * When `search_terms` is provided:
            - Searches across files matching `file_path_or_pattern` (default: Python files).
            - Uses graph-based entity index + BM25 content retrieval.
            - Returns entities, code snippets, and relevant context.
        * When `line_nums` is provided:
            - Extracts code context around the given lines in the specified file.
            - Can also filter by `search_terms` to narrow down context.
        * Structured results should be returned (entity definitions, snippets, file locations).

    Usage:
        - Entity search across repo:   SearchAction(search_terms=["MyClass"], file_path_or_pattern="**/*.py")
        - Restrict search to file:     SearchAction(search_terms=["def foo"], file_path_or_pattern="app/utils.py")
        - Extract context by lines:    SearchAction(line_nums=[42, 43], file_path_or_pattern="main.py")
        - Combined search:             SearchAction(search_terms=["load_data"], line_nums=[100], file_path_or_pattern="data.py")
    """

    # Core inputs
    search_terms: Optional[List[str]] = None
    line_nums: Optional[List[int]] = None
    file_path_or_pattern: str = "**/*.py"

    # Shared
    thought: str = ""
    action: str = ActionType.SEARCH
    runnable: ClassVar[bool] = True
    security_risk: Optional[ActionSecurityRisk] = None

    def __repr__(self) -> str:
        ret = "**SearchAction**\n"
        ret += f"File Pattern/Path: [{self.file_path_or_pattern}]\n"
        ret += f"Thought: {self.thought}\n"
        if self.search_terms:
            ret += f"Search Terms: {self.search_terms}\n"
        if self.line_nums:
            ret += f"Line Numbers: {self.line_nums}\n"
        return ret
