# allocation_logic.py


"""
allocation_logic.py

Core logic for parsing MS Forms responses and allocating students to business
elective modules.

This refactored version uses a minimum-cost maximum-flow model.

Guarantees:
-----------
1. Maximum allocation:
   The allocator maximizes the total number of allocated module slots.
   If a feasible allocation exists where every student receives all allowed
   modules, this allocation will be found.

2. Preference-ranked optimization:
   Among all maximum-allocation solutions, the allocator optimizes preference
   ranking lexicographically:
       - maximize rank 1 allocations,
       - then maximize rank 2 allocations,
       - then maximize rank 3 allocations,
       - and so on.

3. Constraints:
   - Elective capacity = classes_open * max_class_size.
   - Each course has a module quota.
   - A student cannot receive the same elective twice.
   - Only selected courses are allocated.
"""

from __future__ import annotations

import heapq
import re
import unicodedata
from collections import Counter
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple, Union

import pandas as pd


# ---------------------------------------------------------------------------
# Master data
# ---------------------------------------------------------------------------

CANONICAL_ELECTIVES: List[str] = [
    "Business in the Circular Economy",
    "Carbon Management",
    "Governance & Sustainability Reporting",
    "Sustainable Finance",
    "Advanced Analytics & Strategic Insights",
    "AI in Business",
    "Digital Innovation & Transformation",
    "Behavioural Insights in Consumer Experiences",
    "Behavioural Insights in Change Management",
    "Behavioural Insights in Organisational Experiences",
]

ALL_COURSES: List[str] = ["ACC", "BF", "BS", "ITB", "TRM"]

DEFAULT_MAX_CLASS_SIZE: int = 25


# ---------------------------------------------------------------------------
# Course and elective synonyms
# ---------------------------------------------------------------------------

COURSE_SYNONYMS: Dict[str, List[str]] = {
    "ACC": [
        "ACC",
        "ACCOUNTANCY",
        "ACCOUNTING",
        "ACCOUNTANCY & FINANCE",
        "ACCOUNTING & FINANCE",
        "ACCOUNTING AND FINANCE",
    ],
    "BF": [
        "BF",
        "BANKING & FINANCE",
        "BANKING AND FINANCE",
        "BUSINESS FINANCE",
        "FINANCE",
    ],
    "BS": [
        "BS",
        "BUSINESS STUDIES",
    ],
    "ITB": [
        "ITB",
        "INTERNATIONAL TRADE & BUSINESS",
        "INTERNATIONAL TRADE AND BUSINESS",
    ],
    "TRM": [
        "TRM",
        "TOURISM & RESORT MANAGEMENT",
        "TOURISM AND RESORT MANAGEMENT",
        "TOURISM MANAGEMENT",
        "TOURISM",
    ],
}


_EXTRA_ELECTIVE_SYNONYMS: Dict[str, List[str]] = {
    "AI in Business": [
        "AI Business",
        "Artificial Intelligence in Business",
    ],
    "Business in the Circular Economy": [
        "Circular Economy",
        "Business in Circular Economy",
    ],
    "Behavioural Insights in Consumer Experiences": [
        "Behavioral Insights in Consumer Experiences",
    ],
    "Behavioural Insights in Change Management": [
        "Behavioral Insights in Change Management",
    ],
    "Behavioural Insights in Organisational Experiences": [
        "Behavioural Insights in Organizational Experiences",
        "Behavioral Insights in Organisational Experiences",
        "Behavioral Insights in Organizational Experiences",
    ],
}


# ---------------------------------------------------------------------------
# Text normalization helpers
# ---------------------------------------------------------------------------

def _clean_text(value: object) -> str:
    """
    Convert a cell value into clean single-line text.

    Handles None, NaN, Unicode normalization, and repeated spaces.
    """
    if value is None:
        return ""

    try:
        if pd.isna(value):
            return ""
    except TypeError:
        pass

    text = str(value)
    text = unicodedata.normalize("NFKC", text)
    text = text.replace("\u00a0", " ")
    text = re.sub(r"\s+", " ", text).strip()
    return text


def _normalize_key(value: object) -> str:
    """
    Normalize text for robust matching.

    Example:
        "Governance & Sustainability Reporting"
        becomes:
        "governanceandsustainabilityreporting"
    """
    text = _clean_text(value).lower()
    text = text.replace("&", " and ")
    text = re.sub(r"[^a-z0-9]+", "", text)
    return text


# Build elective lookup dictionary.
_ELECTIVE_LOOKUP: Dict[str, str] = {}

for _elective in CANONICAL_ELECTIVES:
    _ELECTIVE_LOOKUP[_normalize_key(_elective)] = _elective

for _canonical, _synonyms in _EXTRA_ELECTIVE_SYNONYMS.items():
    for _synonym in _synonyms:
        _ELECTIVE_LOOKUP[_normalize_key(_synonym)] = _canonical


# Build course lookup dictionary.
_COURSE_LOOKUP: Dict[str, str] = {}

for _code, _synonyms in COURSE_SYNONYMS.items():
    for _synonym in _synonyms:
        _COURSE_LOOKUP[_normalize_key(_synonym)] = _code


# ---------------------------------------------------------------------------
# Parsing helpers
# ---------------------------------------------------------------------------

def normalize_elective(raw: object) -> Optional[str]:
    """
    Convert raw elective text into a canonical elective name.

    Returns None if the elective cannot be recognized.
    """
    return _ELECTIVE_LOOKUP.get(_normalize_key(raw))


def parse_preference_string(raw: object) -> Tuple[List[str], List[str]]:
    """
    Parse the MS Forms ranking column.

    Expected format:
        "AI in Business ;Carbon Management ;Sustainable Finance ;..."

    The returned preference list is ordered by rank:
        preferences[0] is rank 1,
        preferences[1] is rank 2,
        etc.

    Returns:
        preferences:
            Ordered list of recognized canonical elective names.
        warnings:
            Row-level parsing warnings.
    """
    warnings: List[str] = []
    text = _clean_text(raw)

    if not text:
        return [], ["Preference ranking is empty."]

    # Some exports may contain HTML line breaks.
    text = re.sub(r"(?i)<br\s*/?>", ";", text)

    # Primary expected separator is semicolon.
    if ";" in text:
        tokens = text.split(";")
    else:
        # Fallbacks: newline, pipe, or comma.
        tokens = re.split(r"[\n\r|]+", text)
        if len(tokens) == 1:
            tokens = text.split(",")

    preferences: List[str] = []
    seen = set()

    for token in tokens:
        token = _clean_text(token)
        if not token:
            continue

        # Remove leading rank numbers such as:
        # "1. AI in Business"
        # "2) Carbon Management"
        # "3: Sustainable Finance"
        token = re.sub(r"^\s*\d+[\.\)\-:]*\s*", "", token)
        token = token.strip().strip(";,").strip()

        canonical = normalize_elective(token)

        if canonical is None:
            warnings.append(f"Unrecognized elective: '{token}'")
            continue

        if canonical in seen:
            warnings.append(f"Duplicate elective ignored: '{canonical}'")
            continue

        seen.add(canonical)
        preferences.append(canonical)

    if not preferences:
        warnings.append("No recognized electives found in preference ranking.")
    elif len(preferences) < len(CANONICAL_ELECTIVES):
        warnings.append(
            f"Only {len(preferences)} recognized electives found "
            f"out of {len(CANONICAL_ELECTIVES)}."
        )

    return preferences, warnings


def map_course(raw: object) -> Optional[str]:
    """
    Map raw Diploma/Course text to one of the allowed course codes:
        ACC, BF, BS, ITB, TRM

    Returns None if no mapping is found.
    """
    text = _clean_text(raw)
    if not text:
        return None

    upper = text.upper()

    # Exact course code.
    for code in ALL_COURSES:
        if upper == code:
            return code

    # Course code as standalone word.
    for code in ALL_COURSES:
        if re.search(rf"\b{code}\b", upper):
            return code

    # Exact normalized synonym.
    norm = _normalize_key(text)
    if norm in _COURSE_LOOKUP:
        return _COURSE_LOOKUP[norm]

    # Fallback substring synonym match.
    for synonym_norm, code in _COURSE_LOOKUP.items():
        if synonym_norm and synonym_norm in norm:
            return code

    return None


def _find_column(
    df: pd.DataFrame,
    keywords: List[str],
    exclude: Optional[List[str]] = None,
) -> Optional[str]:
    """
    Find a DataFrame column whose cleaned lowercase name contains all keywords.

    This makes parsing robust against small MS Forms column-name changes.
    """
    exclude = exclude or []

    for col in df.columns:
        col_norm = _clean_text(col).lower()

        if any(ex in col_norm for ex in exclude):
            continue

        if all(keyword in col_norm for keyword in keywords):
            return col

    return None


def _valid_timestamp(ts: Optional[pd.Timestamp]) -> bool:
    """Return True if timestamp exists and is not pandas NaT."""
    if ts is None:
        return False

    try:
        return not pd.isna(ts)
    except TypeError:
        return False


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class Student:
    """
    Represents one parsed student response.

    key:
        Internal unique key used by the allocation engine.
    response_id:
        MS Forms Id if available.
    student_number:
        Student number entered in the form.
    course_code:
        Mapped course code: ACC/BF/BS/ITB/TRM, or None if unmapped.
    preferences:
        Ordered canonical elective list. preferences[0] is rank 1.
    warnings:
        Row-level parsing warnings.
    """

    key: str
    response_id: str
    student_number: str
    name: str
    email: str
    raw_course: str
    course_code: Optional[str]
    class_name: str
    internship_semester: str
    preferences: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    completion_time: Optional[pd.Timestamp] = None


@dataclass
class AllocationResult:
    """
    Result object returned by allocate_students().
    """

    assignment_rows: List[dict]
    elective_summary: List[dict]
    metrics: Dict
    warnings: List[str]
    assignments_by_student: Dict[str, List[str]]
    assignments_by_elective: Dict[str, List[str]]


# ---------------------------------------------------------------------------
# Deduplication and loading
# ---------------------------------------------------------------------------

def _deduplicate_students(students: List[Student]) -> Tuple[List[Student], int]:
    """
    Deduplicate by student number, keeping the latest completion time.

    If student number is missing, the response is kept as unique by its
    internal key.
    """
    best: Dict[str, Student] = {}
    removed = 0

    for student in students:
        if student.student_number:
            dedupe_key = student.student_number.strip().upper()
        else:
            dedupe_key = student.key

        if dedupe_key not in best:
            best[dedupe_key] = student
            continue

        removed += 1
        current = best[dedupe_key]

        # Keep the later completion time if available.
        if _valid_timestamp(student.completion_time):
            if (
                not _valid_timestamp(current.completion_time)
                or student.completion_time > current.completion_time
            ):
                best[dedupe_key] = student
        else:
            # If neither has valid timestamp, keep the later parsed row.
            best[dedupe_key] = student

    return list(best.values()), removed


def load_responses(
    source: Union[pd.DataFrame, object]
) -> Tuple[List[Student], List[str]]:
    """
    Load and parse MS Forms Excel response.

    source:
        Either an uploaded Excel file object or a pandas DataFrame.

    Returns:
        students:
            Parsed and deduplicated student records.
        warnings:
            Global load warnings.
    """
    warnings: List[str] = []

    try:
        if isinstance(source, pd.DataFrame):
            df = source.copy()
        else:
            df = pd.read_excel(source)
    except Exception as exc:
        warnings.append(f"Could not read Excel file: {exc}")
        return [], warnings

    if df.empty:
        warnings.append("Uploaded file is empty.")
        return [], warnings

    # Locate columns robustly.
    id_col = _find_column(df, ["id"])
    completion_col = _find_column(df, ["completion", "time"])
    email_col = _find_column(df, ["email"])

    name_col = (
        _find_column(df, ["registered", "name"])
        or _find_column(
            df,
            ["name"],
            exclude=[
                "email",
                "class",
                "student",
                "number",
                "elective",
                "diploma",
                "course",
                "rank",
                "preference",
            ],
        )
    )

    student_no_col = (
        _find_column(df, ["student", "number"])
        or _find_column(df, ["student", "id"])
    )

    course_col = (
        _find_column(df, ["diploma", "course"])
        or _find_column(
            df,
            ["course"],
            exclude=["elective", "rank", "preference"],
        )
    )

    class_col = _find_column(df, ["class"])
    internship_col = _find_column(df, ["internship"])

    rank_col = (
        _find_column(df, ["rank", "electives"])
        or _find_column(df, ["rank"])
        or _find_column(df, ["preference", "electives"])
        or _find_column(df, ["preference"])
    )

    if rank_col is None:
        warnings.append(
            "Could not find the preference/ranking column. "
            "Expected a column containing 'Rank' and 'Electives'."
        )
        return [], warnings

    students: List[Student] = []

    for idx, row in df.iterrows():
        response_id = _clean_text(row.get(id_col)) if id_col else ""
        if not response_id:
            response_id = f"ROW_{idx}"

        # Internal key ensures uniqueness even if Id is missing/duplicated.
        key = f"{response_id}_{idx}"

        completion_time = None
        if completion_col:
            completion_time = pd.to_datetime(
                row.get(completion_col),
                errors="coerce",
            )
            if pd.isna(completion_time):
                completion_time = None

        raw_course = _clean_text(row.get(course_col)) if course_col else ""
        course_code = map_course(raw_course)

        preferences, pref_warnings = parse_preference_string(
            row.get(rank_col) if rank_col else ""
        )

        student_number = (
            _clean_text(row.get(student_no_col)) if student_no_col else ""
        )

        # Remove optional leading S if present, e.g. S10268590K.
        if student_number:
            student_number = re.sub(
                r"^S(?=[0-9])",
                "",
                student_number,
                flags=re.IGNORECASE,
            )

        name = _clean_text(row.get(name_col)) if name_col else ""
        email = _clean_text(row.get(email_col)) if email_col else ""
        class_name = _clean_text(row.get(class_col)) if class_col else ""
        internship_semester = (
            _clean_text(row.get(internship_col)) if internship_col else ""
        )

        row_warnings = pref_warnings.copy()

        if not course_code:
            row_warnings.append(
                f"Course not mapped to ACC/BF/BS/ITB/TRM: "
                f"'{raw_course or 'blank'}'"
            )

        if not student_number:
            row_warnings.append("Missing student number.")

        students.append(
            Student(
                key=key,
                response_id=response_id,
                student_number=student_number,
                name=name,
                email=email,
                raw_course=raw_course,
                course_code=course_code,
                class_name=class_name,
                internship_semester=internship_semester,
                preferences=preferences,
                warnings=row_warnings,
                completion_time=completion_time,
            )
        )

    students, removed_duplicates = _deduplicate_students(students)

    if removed_duplicates:
        warnings.append(
            f"Removed {removed_duplicates} duplicate student number(s), "
            f"keeping the latest submission."
        )

    row_warning_count = sum(1 for s in students if s.warnings)
    if row_warning_count:
        warnings.append(
            f"{row_warning_count} student record(s) have parsing warnings. "
            f"See the Warnings/Raw tabs."
        )

    return students, warnings


# ---------------------------------------------------------------------------
# Minimum-cost maximum-flow implementation
# ---------------------------------------------------------------------------

@dataclass
class _Edge:
    """
    Directed residual edge for min-cost max-flow.
    """

    to: int
    rev: int
    cap: int
    cost: int


class _MinCostMaxFlow:
    """
    Pure-Python min-cost max-flow solver using successive shortest augmenting
    paths with potentials and Dijkstra's algorithm.

    This is suitable for this allocation problem because the graph is small:
        source + students + 10 electives + sink
    """

    def __init__(self, node_count: int):
        """
        Create an empty flow network.

        node_count:
            Total number of nodes in the graph.
        """
        self.graph: List[List[_Edge]] = [[] for _ in range(node_count)]

    def add_edge(self, fr: int, to: int, cap: int, cost: int) -> _Edge:
        """
        Add a directed edge and its reverse residual edge.

        Returns the forward edge object so that final flow can be inspected.
        """
        forward_index = len(self.graph[fr])
        reverse_index = len(self.graph[to])

        forward = _Edge(to, reverse_index, cap, cost)
        backward = _Edge(fr, forward_index, 0, -cost)

        self.graph[fr].append(forward)
        self.graph[to].append(backward)

        return forward

    def min_cost_max_flow(
        self,
        source: int,
        sink: int,
        max_flow: Optional[int] = None,
    ) -> Tuple[int, int]:
        """
        Send as much flow as possible up to max_flow, while minimizing cost.

        If max_flow is None, send the maximum possible flow.

        Returns:
            flow:
                Total flow sent.
            cost:
                Total cost of the flow.
        """
        n = len(self.graph)
        potential = [0] * n

        total_flow = 0
        total_cost = 0

        # Large integer infinity. Python integers are unbounded, but this is
        # safely larger than any realistic path cost in this application.
        INF = 10**1000

        while max_flow is None or total_flow < max_flow:
            dist = [INF] * n
            prev_node = [-1] * n
            prev_edge = [-1] * n

            dist[source] = 0
            heap = [(0, source)]

            # Dijkstra on reduced costs.
            while heap:
                d, v = heapq.heappop(heap)

                if d != dist[v]:
                    continue

                for edge_index, edge in enumerate(self.graph[v]):
                    if edge.cap <= 0:
                        continue

                    reduced_cost = (
                        edge.cost
                        + potential[v]
                        - potential[edge.to]
                    )

                    nd = d + reduced_cost

                    if nd < dist[edge.to]:
                        dist[edge.to] = nd
                        prev_node[edge.to] = v
                        prev_edge[edge.to] = edge_index
                        heapq.heappush(heap, (nd, edge.to))

            # No augmenting path exists.
            if dist[sink] == INF:
                break

            # Update potentials for next iteration.
            for v in range(n):
                if dist[v] != INF:
                    potential[v] += dist[v]

            # Determine bottleneck capacity along the shortest path.
            add_flow = INF
            if max_flow is not None:
                add_flow = max_flow - total_flow

            v = sink
            while v != source:
                if prev_node[v] == -1:
                    add_flow = 0
                    break

                edge = self.graph[prev_node[v]][prev_edge[v]]
                if edge.cap < add_flow:
                    add_flow = edge.cap

                v = prev_node[v]

            if add_flow <= 0:
                break

            # Augment flow along the path.
            v = sink
            while v != source:
                edge = self.graph[prev_node[v]][prev_edge[v]]

                edge.cap -= add_flow
                self.graph[v][edge.rev].cap += add_flow

                total_cost += add_flow * edge.cost

                v = prev_node[v]

            total_flow += add_flow

        return total_flow, total_cost


# ---------------------------------------------------------------------------
# Preference cost construction
# ---------------------------------------------------------------------------

def _build_lexicographic_rank_costs(
    max_rank: int,
    max_possible_assignments: int,
) -> List[int]:
    """
    Build integer costs that lexicographically optimize preference ranks.

    For a fixed total number of assignments, we want to maximize:
        number of rank 1 assignments,
        then number of rank 2 assignments,
        then number of rank 3 assignments,
        ...

    This is equivalent to minimizing:
        number of assignments with rank >= 2,
        then number of assignments with rank >= 3,
        then number of assignments with rank >= 4,
        ...

    Let N_r be the number of assignments with rank >= r.

    We minimize:
        W_2 * N_2 + W_3 * N_3 + ... + W_max_rank * N_max_rank

    where:
        W_2 >> W_3 >> ... >> W_max_rank

    An assignment with rank k contributes to N_2, N_3, ..., N_k.

    Therefore, the per-assignment cost for rank k is:
        cost[k] = W_2 + W_3 + ... + W_k

    The base is chosen to be larger than the maximum possible number of
    assignments, so one improvement at a higher priority level dominates any
    possible changes at lower priority levels.
    """
    base = max(2, max_possible_assignments + 1)

    costs = [0] * (max_rank + 1)
    cumulative = 0

    for rank in range(2, max_rank + 1):
        weight = base ** (max_rank - rank)
        cumulative += weight
        costs[rank] = cumulative

    return costs


# ---------------------------------------------------------------------------
# Allocation engine
# ---------------------------------------------------------------------------

def allocate_students(
    students: List[Student],
    selected_courses: List[str],
    course_module_quota: Dict[str, int],
    classes_by_elective: Dict[str, int],
    max_class_size: int = DEFAULT_MAX_CLASS_SIZE,
    seed: int = 2026,
) -> AllocationResult:
    """
    Allocate electives to students using minimum-cost maximum-flow.

    Objective:
    ----------
    1. Maximize total allocated module slots.
    2. Among maximum-slot allocations, lexicographically optimize preference
       rank distribution:
           maximize rank 1 allocations,
           then maximize rank 2 allocations,
           then maximize rank 3 allocations,
           and so on.

    Parameters
    ----------
    students:
        Parsed student records from load_responses().
    selected_courses:
        Course codes included in this allocation exercise.
    course_module_quota:
        Number of elective modules allowed per student for each course.
        Example: {"BS": 1, "ITB": 2}
    classes_by_elective:
        Number of classes open for each elective.
        Capacity = classes * max_class_size.
    max_class_size:
        Maximum students per class. Default is 25.
    seed:
        Retained for backward compatibility with previous UI. The optimizer is
        deterministic for a given input order and does not require a seed.

    Returns
    -------
    AllocationResult
        Contains assignment rows, elective summary, metrics, and warnings.
    """
    # Seed is retained for API compatibility with earlier versions.
    _ = seed

    warnings: List[str] = []

    selected_course_set = set(selected_courses or [])

    if not selected_course_set:
        warnings.append("No courses selected.")
        return AllocationResult(
            assignment_rows=[],
            elective_summary=[],
            metrics={},
            warnings=warnings,
            assignments_by_student={},
            assignments_by_elective={},
        )

    # Ensure quota exists for every selected course.
    quota_by_course: Dict[str, int] = {
        course: int(course_module_quota.get(course, 0) or 0)
        for course in selected_course_set
    }

    # Build elective capacity.
    classes_by_elective = classes_by_elective or {}
    capacities: Dict[str, int] = {}

    for elective in CANONICAL_ELECTIVES:
        classes_open = int(classes_by_elective.get(elective, 0) or 0)
        classes_open = max(0, classes_open)
        capacities[elective] = classes_open * max_class_size

    # Warnings for excluded/unmapped students.
    unmapped_count = sum(1 for s in students if s.course_code is None)
    if unmapped_count:
        warnings.append(
            f"{unmapped_count} student(s) excluded because their "
            f"Diploma/Course could not be mapped to ACC/BF/BS/ITB/TRM."
        )

    not_selected_count = sum(
        1
        for s in students
        if s.course_code and s.course_code not in selected_course_set
    )
    if not_selected_count:
        warnings.append(
            f"{not_selected_count} student(s) excluded because their course "
            f"is not selected for this exercise."
        )

    # Only selected-course students are allocated.
    selected_students = [
        s for s in students if s.course_code in selected_course_set
    ]

    # Sort for stable output display.
    selected_students = sorted(
        selected_students,
        key=lambda s: (s.course_code or "", s.student_number or s.name),
    )

    if not selected_students:
        warnings.append(
            "No students remain after filtering by selected courses."
        )

    # Data quality warnings.
    no_prefs_count = sum(1 for s in selected_students if not s.preferences)
    if no_prefs_count:
        warnings.append(
            f"{no_prefs_count} selected student(s) have no recognized "
            f"elective preferences."
        )

    zero_quota_count = sum(
        1
        for s in selected_students
        if quota_by_course.get(s.course_code, 0) == 0
    )
    if zero_quota_count:
        warnings.append(
            f"{zero_quota_count} selected student(s) belong to courses with "
            f"module quota set to 0."
        )

    short_pref_count = sum(
        1
        for s in selected_students
        if len(s.preferences) < quota_by_course.get(s.course_code, 0)
    )
    if short_pref_count:
        warnings.append(
            f"{short_pref_count} selected student(s) have fewer recognized "
            f"preferences than the number of modules their course allows."
        )

    total_allowed_demand = sum(
        quota_by_course.get(s.course_code, 0) for s in selected_students
    )

    total_capacity = sum(capacities.values())

    if total_capacity < total_allowed_demand:
        warnings.append(
            f"Total capacity ({total_capacity}) is less than total demand "
            f"({total_allowed_demand}). Some module requests cannot be "
            f"satisfied."
        )

    # -------------------------------------------------------------------------
    # Build flow network
    # -------------------------------------------------------------------------

    n_students = len(selected_students)
    n_electives = len(CANONICAL_ELECTIVES)

    source = 0
    student_offset = 1
    elective_offset = student_offset + n_students
    sink = elective_offset + n_electives
    node_count = sink + 1

    mcmf = _MinCostMaxFlow(node_count)

    # Elective -> sink edges.
    elective_index: Dict[str, int] = {}
    for j, elective in enumerate(CANONICAL_ELECTIVES):
        node = elective_offset + j
        elective_index[elective] = node
        mcmf.add_edge(node, sink, capacities[elective], 0)

    # Determine how many source edges are actually possible.
    # A student cannot be assigned more modules than:
    #   - course quota,
    #   - number of recognized preferences with positive capacity.
    source_cap_by_student: Dict[str, int] = {}
    possible_demand = 0

    for s in selected_students:
        quota = quota_by_course.get(s.course_code, 0)

        if quota <= 0:
            source_cap_by_student[s.key] = 0
            continue

        open_preference_count = sum(
            1
            for elective in s.preferences
            if capacities.get(elective, 0) > 0
        )

        source_cap = min(quota, open_preference_count)
        source_cap_by_student[s.key] = source_cap
        possible_demand += source_cap

    # The maximum flow cannot exceed possible demand or total capacity.
    target_flow = min(possible_demand, total_capacity)

    # Build lexicographic preference costs.
    max_possible_assignments = max(
        1,
        min(total_allowed_demand, possible_demand, total_capacity),
    )

    rank_costs = _build_lexicographic_rank_costs(
        max_rank=len(CANONICAL_ELECTIVES),
        max_possible_assignments=max_possible_assignments,
    )

    # Student -> elective edges.
    assignment_edges: List[Tuple[str, str, _Edge]] = []

    for i, s in enumerate(selected_students):
        source_cap = source_cap_by_student.get(s.key, 0)

        if source_cap <= 0:
            continue

        student_node = student_offset + i

        # Source -> student edge.
        mcmf.add_edge(source, student_node, source_cap, 0)

        # Student -> elective edges.
        #
        # We preserve the original preference rank even if higher-ranked
        # electives have zero capacity.
        for rank, elective in enumerate(s.preferences, start=1):
            if capacities.get(elective, 0) <= 0:
                continue

            if rank < len(rank_costs):
                cost = rank_costs[rank]
            else:
                # Safety fallback if rank somehow exceeds canonical max rank.
                cost = rank_costs[-1]

            edge = mcmf.add_edge(
                student_node,
                elective_index[elective],
                1,
                cost,
            )

            assignment_edges.append((s.key, elective, edge))

    # -------------------------------------------------------------------------
    # Solve maximum-flow / minimum-cost-flow
    # -------------------------------------------------------------------------

    allocated_flow, _optimizer_cost = mcmf.min_cost_max_flow(
        source=source,
        sink=sink,
        max_flow=target_flow,
    )

    if total_allowed_demand > 0 and allocated_flow < total_allowed_demand:
        if possible_demand < total_allowed_demand:
            warnings.append(
                "Some requested modules cannot be attempted because students "
                "do not have enough valid preferences/open electives."
            )

        if total_capacity >= total_allowed_demand:
            warnings.append(
                "Aggregate capacity is sufficient, but no feasible allocation "
                "satisfies all requested modules under preferences, capacity "
                "distribution, and distinct-elective constraints."
            )
        else:
            warnings.append(
                "Total capacity is insufficient to satisfy all requested "
                "modules."
            )

    # -------------------------------------------------------------------------
    # Extract assignments from residual edge capacities
    # -------------------------------------------------------------------------

    pref_rank_map: Dict[str, Dict[str, int]] = {
        s.key: {
            elective: rank
            for rank, elective in enumerate(s.preferences, start=1)
        }
        for s in selected_students
    }

    assignments_by_student: Dict[str, List[str]] = {
        s.key: [] for s in selected_students
    }

    for student_key, elective, edge in assignment_edges:
        # Forward edge capacity was originally 1.
        # If final residual capacity is 0, one unit of flow was sent.
        if edge.cap == 0:
            assignments_by_student[student_key].append(elective)

    # Sort each student's assigned electives by original preference rank.
    for student_key, electives in assignments_by_student.items():
        electives.sort(
            key=lambda e: pref_rank_map.get(student_key, {}).get(e, 999)
        )

    assignments_by_elective: Dict[str, List[str]] = {
        elective: [] for elective in CANONICAL_ELECTIVES
    }

    for student_key, electives in assignments_by_student.items():
        for elective in electives:
            assignments_by_elective[elective].append(student_key)

    # -------------------------------------------------------------------------
    # Build output rows
    # -------------------------------------------------------------------------

    student_map: Dict[str, Student] = {
        s.key: s for s in selected_students
    }

    assignment_rows: List[dict] = []

    for s in selected_students:
        assigned = assignments_by_student.get(s.key, [])
        quota = quota_by_course.get(s.course_code, 0)

        ranks_text = "; ".join(
            str(pref_rank_map[s.key].get(elective, "?"))
            for elective in assigned
        )

        assignment_rows.append(
            {
                "Student Number": s.student_number,
                "Name": s.name,
                "Email": s.email,
                "Course": s.course_code,
                "Raw Course": s.raw_course,
                "Class": s.class_name,
                "Internship Semester": s.internship_semester,
                "Allowed Modules": quota,
                "Allocated Modules": len(assigned),
                "Unmet Demand": max(0, quota - len(assigned)),
                "Allocated Electives": "; ".join(assigned),
                "Allocated Preference Ranks": ranks_text,
                "Student Warnings": "; ".join(s.warnings),
            }
        )

    elective_summary: List[dict] = []

    for elective in CANONICAL_ELECTIVES:
        classes_open = int(classes_by_elective.get(elective, 0) or 0)
        capacity = capacities[elective]
        allocated = len(assignments_by_elective.get(elective, []))

        utilization = (
            round(allocated / capacity * 100, 1) if capacity > 0 else 0.0
        )

        elective_summary.append(
            {
                "Elective": elective,
                "Classes Open": classes_open,
                "Capacity": capacity,
                "Allocated": allocated,
                "Vacant": max(0, capacity - allocated),
                "Utilization %": utilization,
            }
        )

    rank_distribution = Counter()

    for student_key, assigned in assignments_by_student.items():
        for elective in assigned:
            rank = pref_rank_map[student_key].get(elective)
            if rank is not None:
                rank_distribution[rank] += 1

    rank_distribution_dict = {
        rank: rank_distribution.get(rank, 0)
        for rank in range(1, len(CANONICAL_ELECTIVES) + 1)
    }

    total_allocated = sum(
        len(electives) for electives in assignments_by_student.values()
    )

    metrics = {
        "selected_courses": sorted(selected_course_set),
        "total_students_loaded": len(students),
        "total_students_selected": len(selected_students),
        "total_demand": total_allowed_demand,
        "possible_demand": possible_demand,
        "total_allocated": total_allocated,
        "unmet_demand": max(0, total_allowed_demand - total_allocated),
        "total_capacity": total_capacity,
        "seats_vacant": max(0, total_capacity - total_allocated),
        "students_full_quota": sum(
            1
            for s in selected_students
            if quota_by_course.get(s.course_code, 0) > 0
            and len(assignments_by_student.get(s.key, []))
            == quota_by_course.get(s.course_code, 0)
        ),
        "students_zero_allocation": sum(
            1
            for s in selected_students
            if quota_by_course.get(s.course_code, 0) > 0
            and len(assignments_by_student.get(s.key, [])) == 0
        ),
        "rank_distribution": rank_distribution_dict,
        "optimizer": "min-cost-max-flow",
        "preference_objective": "lexicographic rank optimization",
    }

    return AllocationResult(
        assignment_rows=assignment_rows,
        elective_summary=elective_summary,
        metrics=metrics,
        warnings=warnings,
        assignments_by_student=assignments_by_student,
        assignments_by_elective=assignments_by_elective,
    )
