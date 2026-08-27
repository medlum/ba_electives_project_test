# allocation_logic.py
# This is a greedy algorithmn that does not guarantee total demand are allocated
# by total capacity assuming capacity > demand

"""
allocation_logic.py

Contains the core business logic for parsing MS Forms responses and allocating
students to business elective modules.

The allocation approach is a round-based deferred-acceptance-style greedy
algorithm:

- Each course has a quota: number of elective modules its students may take.
- Each elective has capacity: number_of_classes * 25.
- Allocation happens in module rounds:
    Round 1 allocates the first module for eligible students.
    Round 2 allocates the second module for eligible students, etc.
- In each round, students propose to their highest-ranked remaining elective.
- Electives accept up to capacity using a priority rule:
    1. Fewer already-allocated modules first.
    2. Better preference rank.
    3. Smaller course module entitlement.
    4. Deterministic hash tie-breaker.
"""

from __future__ import annotations

import hashlib
import re
import unicodedata
from collections import Counter, defaultdict
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


# Course mapping synonyms.
# If your actual MS Form dropdown uses different names, add them here.
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


# Extra elective synonyms, including US spelling variants.
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
    Convert a cell value to clean single-line text.

    Handles None, NaN, extra spaces, and Unicode normalization.
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
    Normalize text for matching.

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
    Convert raw elective text to canonical elective name.

    Returns None if the elective is not recognized.
    """
    return _ELECTIVE_LOOKUP.get(_normalize_key(raw))


def parse_preference_string(raw: object) -> Tuple[List[str], List[str]]:
    """
    Parse the MS Forms ranking column.

    The expected format is a semicolon-separated ordered list:
        "AI in Business ;Carbon Management ;Sustainable Finance ;..."

    Returns:
        preferences:
            Ordered list of canonical elective names.
            Index 0 is rank 1.
        warnings:
            Parsing warnings for this response row.
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
        # Fallbacks: newline, pipe, or simple comma separation.
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

    # Course code as a standalone word.
    for code in ALL_COURSES:
        if re.search(rf"\b{code}\b", upper):
            return code

    # Exact normalized synonym.
    norm = _normalize_key(text)
    if norm in _COURSE_LOOKUP:
        return _COURSE_LOOKUP[norm]

    # Fallback: substring synonym match.
    # This helps with longer dropdown values.
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

    This makes the parser robust to small MS Forms column name changes.
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
        Internal unique key used by allocation engine.
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
    Allocate electives to students based on preferences and constraints.

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
        Seed used for deterministic tie-breaking.

    Returns
    -------
    AllocationResult
        Contains assignment rows, elective summary, metrics, and warnings.
    """
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
            "No students remain after filtering by selected courses.")

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

    total_demand = sum(
        quota_by_course.get(s.course_code, 0) for s in selected_students
    )
    total_capacity = sum(capacities.values())

    if total_capacity < total_demand:
        warnings.append(
            f"Total capacity ({total_capacity}) is less than total demand "
            f"({total_demand}). Some module requests may not be satisfied."
        )

    # Allocation state.
    assignments: Dict[str, List[str]] = {
        s.key: [] for s in selected_students
    }

    assigned_by_elective: Dict[str, List[str]] = {
        e: [] for e in CANONICAL_ELECTIVES
    }

    # Global rejected electives per student.
    # If an elective rejected a student in an earlier round because it was
    # full, it will remain full because allocations do not free seats.
    global_rejected: Dict[str, set] = {
        s.key: set() for s in selected_students
    }

    student_map: Dict[str, Student] = {
        s.key: s for s in selected_students
    }

    # Preference rank lookup:
    # pref_rank_map[student_key][elective] = rank number, 1 is best.
    pref_rank_map: Dict[str, Dict[str, int]] = {
        s.key: {
            elective: rank
            for rank, elective in enumerate(s.preferences, start=1)
        }
        for s in selected_students
    }

    # Deterministic tie-breaker.
    # Changing the seed changes oversubscription tie outcomes.
    tie_cache: Dict[str, str] = {}
    for s in selected_students:
        basis = f"{seed}:{s.student_number}:{s.response_id}:{s.key}"
        tie_cache[s.key] = hashlib.sha256(basis.encode("utf-8")).hexdigest()

    max_slots = max(quota_by_course.values(), default=0)

    # -------------------------------------------------------------------------
    # Round-based allocation
    # -------------------------------------------------------------------------
    for slot in range(1, max_slots + 1):
        # Eligible students for this module slot:
        # - course allows at least this slot
        # - student has not yet reached their course quota
        active_keys = [
            s.key
            for s in selected_students
            if quota_by_course.get(s.course_code, 0) >= slot
            and len(assignments[s.key]) < quota_by_course.get(s.course_code, 0)
        ]

        if not active_keys:
            continue

        # Base counts are fixed during this slot.
        # They represent allocations from previous slots only.
        base_counts = {
            key: len(assignments[key]) for key in active_keys
        }

        base_quotas = {
            key: quota_by_course.get(student_map[key].course_code, 0)
            for key in active_keys
        }

        # Tentative holds for the current slot.
        tentative: Dict[str, List[str]] = {
            elective: [] for elective in CANONICAL_ELECTIVES
        }

        # Electives rejected during this slot.
        slot_rejected: Dict[str, set] = {
            key: set() for key in active_keys
        }

        iteration = 0
        max_iterations = len(CANONICAL_ELECTIVES) + 5

        while True:
            iteration += 1

            if iteration > max_iterations:
                warnings.append(
                    f"Allocation slot {slot}: stopped after {iteration} "
                    f"iterations to prevent an infinite loop."
                )
                break

            held_keys = set()
            for keys in tentative.values():
                held_keys.update(keys)

            proposals: List[Tuple[str, str]] = []

            # Each unheld active student proposes to next acceptable elective.
            for key in active_keys:
                if key in held_keys:
                    continue

                student = student_map[key]
                chosen_elective = None

                for elective in student.preferences:
                    if elective in assignments[key]:
                        continue

                    if elective in global_rejected[key]:
                        continue

                    if elective in slot_rejected[key]:
                        continue

                    if capacities.get(elective, 0) <= 0:
                        continue

                    chosen_elective = elective
                    break

                if chosen_elective is not None:
                    proposals.append((key, chosen_elective))

            if not proposals:
                break

            proposals_by_elective: Dict[str, List[str]] = defaultdict(list)
            for key, elective in proposals:
                proposals_by_elective[elective].append(key)

            # Each elective processes proposals and tentative holds.
            for elective, proposing_keys in proposals_by_elective.items():
                cap_left = capacities.get(elective, 0) - len(
                    assigned_by_elective[elective]
                )

                existing = tentative.get(elective, [])
                existing_set = set(existing)

                candidate_list = list(existing)

                for key in proposing_keys:
                    if key not in existing_set:
                        candidate_list.append(key)
                        existing_set.add(key)

                if cap_left <= 0:
                    accepted: List[str] = []
                    rejected = candidate_list
                else:
                    # Priority:
                    # 1. Fewer previous allocations.
                    # 2. Better preference rank.
                    # 3. Smaller course entitlement.
                    # 4. Deterministic hash tie-breaker.
                    candidate_list.sort(
                        key=lambda k: (
                            base_counts[k],
                            pref_rank_map[k].get(elective, 999),
                            base_quotas[k],
                            tie_cache[k],
                        )
                    )

                    accepted = candidate_list[:cap_left]
                    rejected = candidate_list[cap_left:]

                tentative[elective] = accepted

                for key in rejected:
                    slot_rejected[key].add(elective)

        # Finalize tentative allocations for this slot.
        for elective, accepted_keys in tentative.items():
            for key in accepted_keys:
                if elective not in assignments[key]:
                    assignments[key].append(elective)
                    assigned_by_elective[elective].append(key)

        # Add slot rejections to global rejected set.
        for key, rejected_set in slot_rejected.items():
            global_rejected[key].update(rejected_set)

    # -------------------------------------------------------------------------
    # Build output rows
    # -------------------------------------------------------------------------

    assignment_rows: List[dict] = []

    for s in selected_students:
        assigned = assignments.get(s.key, [])
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
        allocated = len(assigned_by_elective.get(elective, []))

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

    for key, assigned in assignments.items():
        for elective in assigned:
            rank = pref_rank_map[key].get(elective)
            if rank is not None:
                rank_distribution[rank] += 1

    rank_distribution_dict = {
        rank: rank_distribution.get(rank, 0)
        for rank in range(1, len(CANONICAL_ELECTIVES) + 1)
    }

    metrics = {
        "selected_courses": sorted(selected_course_set),
        "total_students_loaded": len(students),
        "total_students_selected": len(selected_students),
        "total_demand": total_demand,
        "total_allocated": sum(len(v) for v in assignments.values()),
        "unmet_demand": max(
            0, total_demand - sum(len(v) for v in assignments.values())
        ),
        "total_capacity": total_capacity,
        "seats_vacant": max(
            0, total_capacity - sum(len(v) for v in assignments.values())
        ),
        "students_full_quota": sum(
            1
            for s in selected_students
            if quota_by_course.get(s.course_code, 0) > 0
            and len(assignments[s.key]) == quota_by_course.get(s.course_code, 0)
        ),
        "students_zero_allocation": sum(
            1
            for s in selected_students
            if quota_by_course.get(s.course_code, 0) > 0
            and len(assignments[s.key]) == 0
        ),
        "rank_distribution": rank_distribution_dict,
    }

    return AllocationResult(
        assignment_rows=assignment_rows,
        elective_summary=elective_summary,
        metrics=metrics,
        warnings=warnings,
        assignments_by_student=assignments,
        assignments_by_elective=assigned_by_elective,
    )
