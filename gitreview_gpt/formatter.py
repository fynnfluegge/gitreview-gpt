import re
import textwrap
import os
import json
from typing import Tuple


class CodeChunk:
    start_line: int
    end_line: int
    code: str

    def __init__(self, start_line, end_line, code):
        self.start_line = start_line
        self.end_line = end_line
        self.code = code


# Format the git diff into a format that can be used by the GPT-3.5 API
# Add line numbers to the diff
# Split the diff into chunks per file
def format_git_diff(diff_text) -> Tuple[str, dict, dict, list, list]:
    diff_formatted = ""
    file_chunks = {}
    code_change_chunks = {}
    file_names = []
    file_paths = []

    # Split git diff into chunks with separator +++ line inclusive,
    # the line with the filename
    pattern = r"(?=^(\+\+\+).*$)"
    parent_chunks = re.split(r"\n\+{3,}\s", diff_text, re.MULTILINE)
    for j, file_chunk in enumerate(parent_chunks, -1):
        # Skip first chunk (it's the head info)
        if j == -1:
            continue

        # Remove git --diff section
        file_chunk = re.sub(
            r"(diff --git.*\n)(.*\n)", lambda match: match.group(1), file_chunk
        )
        file_chunk = re.sub(r"^diff --git.*\n", "", file_chunk, flags=re.MULTILINE)

        # Split chunk into chunks with separator @@ -n,n +n,n @@ inclusive,
        # the changes in the file
        pattern = r"(?=@@ -\d+,\d+ \+\d+,\d+ @@)"
        changes_per_file = re.split(pattern, file_chunk)
        for i, code_change_chunk in enumerate(changes_per_file, 1):
            # Skip first chunk (it's the file name)
            if i == 1:
                diff_formatted += code_change_chunk.rsplit("/", 1)[-1]
                file_chunks[j] = code_change_chunk.rsplit("/", 1)[-1]
                code_change_chunks[j] = {}
                file_names.append(code_change_chunk.rstrip("\n").rsplit("/", 1)[-1])
                file_paths.append(code_change_chunk[2:].rstrip("\n"))
                continue

            # Extract the line numbers from the changes pattern
            pattern = r"@@ -(\d+),(\d+) \+(\d+),(\d+) @@"
            match = re.findall(pattern, code_change_chunk)
            chunk_dividers = []
            for m in match:
                chunk_dividers.append(
                    {
                        "original_start_line": int(m[0]),
                        "original_end_line": int(m[1]),
                        "new_start_line": int(m[2]),
                        "new_end_line": int(m[3]),
                    }
                )
            line_counter = -1 + chunk_dividers[0]["new_start_line"]

            chunk_formatted = ""
            optional_selection_marker = ""
            for line in code_change_chunk.splitlines():
                if line.startswith("@@ -"):
                    diff_formatted += line + "\n"
                    chunk_formatted += line + "\n"
                    # Extract selection marker
                    parts = line.split("def", 1)
                    if len(parts) > 1:
                        optional_selection_marker = parts[1].strip()
                    else:
                        optional_selection_marker = ""
                    continue
                if line.startswith("---"):
                    continue
                if line.startswith("-"):
                    continue
                else:
                    line_counter += 1

                new_line = str(line_counter) + " " + line + "\n"
                diff_formatted += new_line
                chunk_formatted += new_line

            code_chunk = CodeChunk(
                start_line=chunk_dividers[0]["new_start_line"],
                end_line=chunk_dividers[0]["new_end_line"]
                + chunk_dividers[0]["new_start_line"]
                - 1,
                code=chunk_formatted,
            )
            file_chunks[j] += chunk_formatted
            if optional_selection_marker not in code_change_chunks[j]:
                code_change_chunks[j][optional_selection_marker] = [code_chunk]
            else:
                code_change_chunks[j][optional_selection_marker].append(code_chunk)

    return diff_formatted, file_chunks, code_change_chunks, file_names, file_paths


def parse_repair_review(review_result):
    pattern = r"json\s+(.*?)\s+"
    match = re.search(pattern, review_result, re.DOTALL)
    if match:
        return json.loads(match.group(1))
    else:
        return json.loads(review_result)


def parse_review_result(review_result):
    return remove_unused_suggestions(json.loads(review_result))


def remove_unused_suggestions(review_result):
    # Function to check if feedback contains "not used" or "unused"
    def has_not_used_or_unused(feedback):
        return (
            "not used" in feedback.lower()
            or "unused" in feedback.lower()
            or "not being used" in feedback.lower()
            or "variable name" in feedback.lower()
            or "more descriptive" in feedback.lower()
            or "more specific" in feedback.lower()
            or "never used" in feedback.lower()
        )

    # Filter out the entries based on the condition
    return {
        file: {
            line: value
            for line, value in file_data.items()
            if not has_not_used_or_unused(value["feedback"])
        }
        for file, file_data in review_result.items()
    }


# Draw review output box
def draw_box(filename, feedback_lines):
    max_length = os.get_terminal_size()[0] - 2
    border = "╭" + "─" * (max_length) + "╮"
    bottom_border = "│" + "─" * (max_length) + "│"
    filename_line = "│ " + filename.ljust(max_length - 1) + "│"
    result = [border, filename_line, bottom_border]

    for entry in feedback_lines:
        line_string = (
            f"◇ \033[01mLine {entry}\033[0m: {feedback_lines[entry]['feedback']}"
        )
        if "suggestion" in feedback_lines[entry]:
            line_string += f" {feedback_lines[entry]['suggestion']}"
        if len(line_string) > max_length - 2:
            wrapped_lines = textwrap.wrap(line_string, width=max_length - 4)
            result.append("│ " + wrapped_lines[0].ljust(max_length + 7) + " │")
            for wrapped_line in wrapped_lines[1:]:
                result.append("│   " + wrapped_line.ljust(max_length - 4) + " │")
        else:
            result.append("│ " + line_string.ljust(max_length + 7) + " │")

    result.append("╰" + "─" * (max_length) + "╯")
    return "\n".join(result)


def parse_apply_review_per_code_hunk(code_changes, review_json, line_number_stack):
    line_number = line_number_stack.pop()
    hunk_review_payload = []
    # print(review_json)
    for code_change_hunk in code_changes:
        review_per_chunk = {}
        while (
            code_change_hunk.start_line
            <= line_number
            < code_change_hunk.start_line + code_change_hunk.end_line
        ):
            review_per_chunk[line_number] = review_json[str(line_number)]

            if not line_number_stack:
                break
            line_number = line_number_stack.pop()

        if review_per_chunk:
            # print(review_per_chunk)
            hunk_review_payload.append(
                code_change_hunk.code + "\n" + json.dumps(review_per_chunk) + "\n"
            )

        if not line_number_stack:
            break
    return hunk_review_payload
