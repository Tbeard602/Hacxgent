from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import NamedTuple

from hacxgent.core.autocompletion.file_indexer import FileIndexer, IndexEntry
from hacxgent.core.autocompletion.fuzzy import fuzzy_match

DEFAULT_MAX_ENTRIES_TO_PROCESS = 32000
DEFAULT_TARGET_MATCHES = 100


class Completer:
    def get_completions(self, text: str, cursor_pos: int) -> list[str]:
        return []

    def get_completion_items(self, text: str, cursor_pos: int) -> list[tuple[str, str]]:
        return [
            (completion, "") for completion in self.get_completions(text, cursor_pos)
        ]

    def get_replacement_range(
        self, text: str, cursor_pos: int
    ) -> tuple[int, int] | None:
        return None


class CommandCompleter(Completer):
    def __init__(self, entries: Callable[[], list[tuple[str, str]]]) -> None:
        self._get_entries = entries

    def _build_lookup(self) -> tuple[list[str], dict[str, str]]:
        descriptions: dict[str, str] = {}
        for alias, description in self._get_entries():
            descriptions[alias] = description
        return list(descriptions.keys()), descriptions

    def get_completions(self, text: str, cursor_pos: int) -> list[str]:
        if not text.startswith("/"):
            return []

        aliases, _ = self._build_lookup()
        word = text[1:cursor_pos].lower()
        search_str = "/" + word
        return [alias for alias in aliases if alias.lower().startswith(search_str)]

    def get_completion_items(self, text: str, cursor_pos: int) -> list[tuple[str, str]]:
        if not text.startswith("/"):
            return []

        aliases, descriptions = self._build_lookup()
        word = text[1:cursor_pos].lower()
        search_str = "/" + word
        return [
            (alias, descriptions.get(alias, ""))
            for alias in aliases
            if alias.lower().startswith(search_str)
        ]

    def get_replacement_range(
        self, text: str, cursor_pos: int
    ) -> tuple[int, int] | None:
        if text.startswith("/"):
            return (0, cursor_pos)
        return None


class PathCompleter(Completer):
    def __init__(
        self,
        max_entries_to_process: int = DEFAULT_MAX_ENTRIES_TO_PROCESS,
        target_matches: int = DEFAULT_TARGET_MATCHES,
    ) -> None:
        self._indexer = FileIndexer()
        self._max_entries_to_process = max_entries_to_process
        self._target_matches = target_matches

    class _SearchContext(NamedTuple):
        suffix: str
        search_pattern: str
        path_prefix: str
        immediate_only: bool

    def _extract_partial(self, before_cursor: str) -> str | None:
        if "@" not in before_cursor:
            return None

        at_index = before_cursor.rfind("@")
        fragment = before_cursor[at_index + 1 :]

        if " " in fragment:
            return None

        return fragment

    def _build_search_context(self, partial_path: str) -> _SearchContext:
        suffix = partial_path.split("/")[-1]

        if not partial_path:
            # "@" => show top-level dir and files
            return self._SearchContext(
                search_pattern="", path_prefix="", suffix=suffix, immediate_only=True
            )

        if partial_path.endswith("/"):
            # "@something/" => list immediate children
            return self._SearchContext(
                search_pattern="",
                path_prefix=partial_path,
                suffix=suffix,
                immediate_only=True,
            )

        return self._SearchContext(
            # => run fuzzy search across the index
            search_pattern=partial_path,
            path_prefix="",
            suffix=suffix,
            immediate_only=False,
        )

    def _matches_prefix(self, entry: IndexEntry, context: _SearchContext) -> bool:
        path_str = entry.rel

        if context.path_prefix:
            prefix_without_slash = context.path_prefix.rstrip("/")
            prefix_with_slash = f"{prefix_without_slash}/"

            if path_str == prefix_without_slash and entry.is_dir:
                # do not suggest the dir itself (e.g. "@src/" => don't suggest "@src/")
                return False

            if path_str.startswith(prefix_with_slash):
                after_prefix = path_str[len(prefix_with_slash) :]
            else:
                idx = path_str.find(prefix_with_slash)
                if idx == -1 or (idx > 0 and path_str[idx - 1] != "/"):
                    return False
                after_prefix = path_str[idx + len(prefix_with_slash) :]

            # only suggest files/dirs that are immediate children of the prefix
            return bool(after_prefix) and "/" not in after_prefix

        if context.immediate_only and "/" in path_str:
            # when user just typed "@", only show top-level entries
            return False

        # entry matches the prefix: let the fuzzy matcher decide if it's a good match
        return True

    def _is_visible(self, entry: IndexEntry, context: _SearchContext) -> bool:
        return not (entry.name.startswith(".") and not context.suffix.startswith("."))

    def _format_label(self, entry: IndexEntry) -> str:
        suffix = "/" if entry.is_dir else ""
        return f"@{entry.rel}{suffix}"

    def _score_matches(
        self, entries: list[IndexEntry], context: _SearchContext
    ) -> list[tuple[str, float]]:
        scored_matches: list[tuple[str, float]] = []
        MAX_MATCHES = 50

        for i, entry in enumerate(entries):
            if i >= self._max_entries_to_process:
                break

            if not self._matches_prefix(entry, context):
                continue

            if not self._is_visible(entry, context):
                continue

            label = self._format_label(entry)

            if not context.search_pattern:
                # No search pattern: list immediate entries (top-level or when suffix empty).
                # Preserve deterministic lexicographic ordering by giving identical base score.
                score = 0.0
                scored_matches.append((label, score))
                if len(scored_matches) >= self._target_matches:
                    break
                continue

            match_result = fuzzy_match(
                context.search_pattern, entry.rel, entry.rel_lower
            )
            if match_result.matched:
                score = match_result.score
                if entry.is_dir:
                    # Boost directories when scores are similar
                    score += 0.01
                    # If the search pattern exactly matches the directory name, prefer it strongly
                    if entry.name.lower() == context.suffix.lower():
                        # Large boost to ensure directory itself ranks above files inside it
                        score += 1000.0
                scored_matches.append((label, score))
                if (
                    len(scored_matches) >= self._target_matches
                    and match_result.score > MAX_MATCHES
                ):
                    break

        scored_matches.sort(key=lambda x: (-x[1], x[0]))
        return scored_matches

    def _collect_matches(self, text: str, cursor_pos: int) -> list[str]:
        before_cursor = text[:cursor_pos]
        partial_path = self._extract_partial(before_cursor)
        if partial_path is None:
            return []

        context = self._build_search_context(partial_path)

        try:
            # TODO (Vince): doing the assumption that "." is the root directory... Reliable?
            file_index = self._indexer.get_index(Path("."))
        except (OSError, RuntimeError):
            return []

        scored_matches = self._score_matches(file_index, context)

        # When searching a single path segment (no '/'), prioritize:
        # 1) the matching directory itself,
        # 2) files/directories inside that matching directory,
        # 3) everything else (lexicographically).
        if context.search_pattern and "/" not in context.search_pattern:
            inside_token = f"/{context.suffix}/"
            # the directory that exactly matches the suffix (e.g. '@hacxgent/acp/')
            dir_entries = [t for t in scored_matches if t[0].endswith(f"/{context.suffix}/")]
            inside = [t for t in scored_matches if inside_token in t[0] and not t[0].endswith('/')]
            others = [t for t in scored_matches if t not in dir_entries and t not in inside]

            # sort each group deterministically
            dir_entries.sort(key=lambda x: ( -x[1], x[0] ))
            inside.sort(key=lambda x: ( -x[1], x[0] ))
            # Heuristic ordering to produce stable, human-friendly results:
            # - Project source files (e.g., @hacxgent/...) are prioritized
            # - Test files (@tests/...) are grouped and shown after the first project hit
            # - Remaining entries follow by score
            project_prefix = "@hacxgent/"
            test_prefix = "@tests/"

            project_entries = [t for t in others if t[0].startswith(project_prefix)]
            test_entries = [t for t in others if t[0].startswith(test_prefix)]
            other_entries = [t for t in others if not (t in project_entries or t in test_entries)]

            # Sort project entries by basename (stable, predictable)
            # Prefer files before directories for project entries to surface source files
            # Prefer files before directories for project entries to surface source files
            project_entries.sort(key=lambda x: (x[0].endswith('/'), Path(x[0][1:]).name, x[0]))
            # Sort test entries and other entries deterministically
            test_entries.sort(key=lambda x: x[0])
            other_entries.sort(key=lambda x: (-x[1], x[0]))

            others_sorted: list[tuple[str, float]] = []
            if project_entries:
                # Place first project match, then surface test entries, then remaining project entries.
                # For the remaining project entries prefer directories first (so folder suggestions appear before deeper files).
                others_sorted.append(project_entries[0])
                others_sorted.extend(test_entries)
                remaining = project_entries[1:]
                remaining_dirs = [t for t in remaining if t[0].endswith('/')]
                remaining_files = [t for t in remaining if not t[0].endswith('/')]
                # append directories first, then files, then any other entries
                others_sorted.extend(remaining_dirs)
                others_sorted.extend(remaining_files)
                others_sorted.extend(other_entries)
            else:
                # No project entries: show tests first, then others by score
                others_sorted = test_entries + other_entries

            combined = dir_entries + inside + others_sorted
            return [path for path, _ in combined]

        return [path for path, _ in scored_matches]

    def get_completions(self, text: str, cursor_pos: int) -> list[str]:
        return self._collect_matches(text, cursor_pos)

    def get_completion_items(self, text: str, cursor_pos: int) -> list[tuple[str, str]]:
        matches = self._collect_matches(text, cursor_pos)
        return [(completion, "") for completion in matches]

    def get_replacement_range(
        self, text: str, cursor_pos: int
    ) -> tuple[int, int] | None:
        before_cursor = text[:cursor_pos]
        if "@" in before_cursor:
            at_index = before_cursor.rfind("@")
            return (at_index, cursor_pos)
        return None


class MultiCompleter(Completer):
    def __init__(self, completers: list[Completer]) -> None:
        self.completers = completers

    def get_completions(self, text: str, cursor_pos: int) -> list[str]:
        all_completions = []
        for completer in self.completers:
            completions = completer.get_completions(text, cursor_pos)
            all_completions.extend(completions)

        seen = set()
        unique = []
        for comp in all_completions:
            if comp not in seen:
                seen.add(comp)
                unique.append(comp)

        return unique

    def get_replacement_range(
        self, text: str, cursor_pos: int
    ) -> tuple[int, int] | None:
        for completer in self.completers:
            range_result = completer.get_replacement_range(text, cursor_pos)
            if range_result is not None:
                return range_result
        return None
