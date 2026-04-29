"""Parse customer postmortem documents and extract structured knowledge.

Converts unstructured postmortem markdown into structured data:
- root_cause: The underlying problem that was fixed
- remediation_steps: Steps taken to resolve the incident
- timeline: When key events happened
- lessons: Key learnings to apply to future incidents
"""

import logging
import re
from typing import Any, Optional

logger = logging.getLogger("runbookai.brain.postmortem_parser")


def parse_postmortem(markdown_content: str) -> dict[str, Any]:
    """Parse postmortem markdown and extract structured knowledge.

    Args:
        markdown_content: The raw markdown postmortem text

    Returns:
        Dict with keys: root_cause, remediation_steps, timeline, lessons
        All values are optional and set to None if not found in the markdown.
    """
    result = {
        "root_cause": None,
        "remediation_steps": None,
        "timeline": None,
        "lessons": None,
    }

    # Extract root cause from "Root Cause" section
    root_cause = _extract_section(markdown_content, r"#+\s*(?:root\s+cause|root cause)", limit=500)
    if root_cause:
        result["root_cause"] = root_cause

    # Extract remediation steps from "Remediation" or "Resolution" section
    remediation = _extract_section(
        markdown_content,
        r"#+\s*(?:remediation|resolution|fix|action)",
        limit=1000
    )
    if remediation:
        # Parse bullet points as individual steps
        result["remediation_steps"] = _parse_bullet_points(remediation)

    # Extract timeline from "Timeline" section
    timeline = _extract_section(markdown_content, r"#+\s*timeline", limit=1000)
    if timeline:
        result["timeline"] = _parse_timeline(timeline)

    # Extract lessons from "Lessons" or "Recommendations" section
    lessons = _extract_section(
        markdown_content,
        r"#+\s*(?:lessons?|recommendations?|learning)",
        limit=1000
    )
    if lessons:
        result["lessons"] = _parse_bullet_points(lessons)

    logger.info(
        "parsed postmortem: root_cause=%s, steps=%d, timeline=%s, lessons=%d",
        bool(result["root_cause"]),
        len(result["remediation_steps"]) if result["remediation_steps"] else 0,
        bool(result["timeline"]),
        len(result["lessons"]) if result["lessons"] else 0,
    )

    return result


def _extract_section(text: str, header_pattern: str, limit: int = 500) -> Optional[str]:
    """Extract text from a section headed by the given pattern.

    Captures from the header until the next header or end of text.
    """
    match = re.search(header_pattern, text, re.IGNORECASE)
    if not match:
        return None

    # Start from the header
    start = match.start()

    # Find the next header (^# or ^##, etc) or end of text
    next_header = re.search(r"\n#+\s+", text[start + 1 :])
    end = (start + 1 + next_header.start()) if next_header else len(text)

    section = text[start:end].strip()

    # Remove the header line itself
    lines = section.split("\n")
    content_lines = lines[1:] if lines else []

    content = "\n".join(content_lines).strip()
    return content[:limit] if content else None


def _parse_bullet_points(text: str) -> list[str]:
    """Extract bullet points from markdown text.

    Returns non-empty bullet points as a list of strings.
    """
    lines = text.split("\n")
    points = []

    for line in lines:
        # Match markdown bullets: -, *, +, or numbered 1.
        match = re.match(r"^\s*(?:[-*+]|\d+\.)\s+(.+)$", line)
        if match:
            point = match.group(1).strip()
            if point:
                points.append(point)

    return points if points else None


def _parse_timeline(text: str) -> Optional[dict]:
    """Parse timeline section into structured events.

    Looks for patterns like:
    - HH:MM: Event description
    - 14:30 - Event description
    - +Xs - Event description
    - T+10s - Event description
    """
    events = []
    lines = text.split("\n")

    for line in lines:
        line = line.strip()
        if not line:
            continue

        # Try to extract time and description
        # Pattern 1: HH:MM or +Xs or T+Xs
        match = re.match(
            r"^(?:(\d{1,2}):(\d{2})|(\+?)(\d+)s?)\s*[-:]\s*(.+)$",
            line
        )
        if match:
            groups = match.groups()
            time_str = "".join([g for g in groups[:4] if g])
            description = groups[4]
            events.append({"time": time_str, "event": description})
        else:
            # Fallback: treat any line as an event
            if line and not line.startswith("|"):  # skip markdown table lines
                events.append({"event": line})

    return {"events": events} if events else None
