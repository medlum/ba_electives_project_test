# streamlit_app.py

"""
streamlit_app.py

Streamlit UI for the Business Electives Preference Allocation app.

This UI allows the user to:
1. Upload the MS Forms Excel response.
2. Select eligible courses.
3. Set number of classes open for each elective.
4. Set number of elective modules allowed per course.
5. Run allocation.
6. View, filter, and download results.
"""

import pandas as pd
import streamlit as st

import allocation_logic as logic


# ---------------------------------------------------------------------------
# Page configuration
# ---------------------------------------------------------------------------

st.set_page_config(
    page_title="Business Electives Allocator",
    page_icon="🎓",
    layout="wide",
)

st.title("Business Electives Preference Allocation")

st.markdown(
    """
    This app allocates students to business elective modules based on their
    MS Forms preference ranking, constrained by:

    - selected courses,
    - number of classes open per elective,
    - maximum class size of 25,
    - number of elective modules each course may select.
    """
)


# ---------------------------------------------------------------------------
# Optional built-in sample data
# ---------------------------------------------------------------------------

def make_sample_df() -> pd.DataFrame:
    """
    Create a one-row sample DataFrame matching the provided MS Forms export.
    Useful for testing without uploading a file.
    """
    ranking = (
        "AI in Business ;Carbon Management ;Governance & Sustainability Reporting;"
        "Behavioural Insights in Consumer Experiences ;Business in the Circular Economy ;"
        "Behavioural Insights in Organisational Experiences;Sustainable Finance ;"
        "Behavioural Insights in Change Management;Advanced Analytics & Strategic Insights ;"
        "Digital Innovation & Transformation ;"
    )

    data = {
        "Id": [1],
        "Start time": ["8/26/26 10:05"],
        "Completion time": ["8/26/26 10:06"],
        "Email": ["och2@np.edu.sg"],
        "Name": ["Chee Hwee Andy Oh"],
        "Please type your name (registered name in NP)": ["WARREN CHRISTOPHER"],
        'Please type your Student Number (exclude prefix "S" in front)': [
            "10268590K"
        ],
        "Please select your Diploma/Course": ["Business Studies"],
        "Please type your class (e.g. TB01, TI02)": ["TB01"],
        "Please indicate which semester you will be doing your 22-week internship": [
            "Oct 2027 semester"
        ],
        "Rank the Business Electives modules in ascending order (1, 2...10) below according to your preference (1 = highest preference)": [
            ranking
        ],
    }

    return pd.DataFrame(data)


# ---------------------------------------------------------------------------
# Sidebar: data source and eligible courses
# ---------------------------------------------------------------------------

with st.sidebar:
    st.header("1. Data source")

    uploaded_file = st.file_uploader(
        "Upload MS Forms Excel response",
        type=["xlsx", "xls"],
        help="Upload the Excel export from the Business Electives Preference Ranking Exercise form.",
    )

    use_sample = st.checkbox(
        "Use built-in one-row sample",
        value=False,
        help="Uses the sample row from the provided knowledge base for testing.",
    )

    st.divider()

    st.header("2. Eligible courses")

    selected_courses = st.multiselect(
        "Select courses included in this exercise",
        options=logic.ALL_COURSES,
        default=logic.ALL_COURSES,
        help="Only students whose course maps to these codes will be allocated.",
    )

    st.divider()

    st.header("3. Allocation settings")

    seed = st.number_input(
        "Tie-break seed",
        min_value=0,
        max_value=999_999,
        value=2026,
        step=1,
        help=(
            "Deterministic tie-breaker for oversubscribed electives. "
            "Change this to produce a different fair tie-break outcome."
        ),
    )

    st.divider()

    if st.button("Clear saved results"):
        st.session_state.pop("result", None)
        st.rerun()


# ---------------------------------------------------------------------------
# Load data
# ---------------------------------------------------------------------------

students = []
load_warnings = []

if uploaded_file is not None:
    students, load_warnings = logic.load_responses(uploaded_file)
elif use_sample:
    students, load_warnings = logic.load_responses(make_sample_df())
else:
    st.info("Upload the MS Forms Excel file or tick the sample-data checkbox to begin.")


# ---------------------------------------------------------------------------
# Display load status
# ---------------------------------------------------------------------------

if uploaded_file is not None or use_sample:
    if students:
        st.success(f"Loaded {len(students)} student response(s).")

        course_counts = (
            pd.Series([s.course_code or "UNMAPPED" for s in students])
            .value_counts()
            .to_dict()
        )
        st.caption(f"Loaded course counts: {course_counts}")
    else:
        st.warning("No student records were loaded. Check warnings below.")

    if load_warnings:
        with st.expander("Load warnings", expanded=False):
            for warning in load_warnings:
                st.warning(warning)


# ---------------------------------------------------------------------------
# User option: classes open per elective
# ---------------------------------------------------------------------------

st.header("4. Number of classes open for each elective")

st.caption(
    f"Each class has a maximum of {logic.DEFAULT_MAX_CLASS_SIZE} places. "
    "Total capacity = classes × 25."
)

classes_by_elective = {}

class_cols = st.columns(2)

for i, elective in enumerate(logic.CANONICAL_ELECTIVES):
    with class_cols[i % len(class_cols)]:
        classes_by_elective[elective] = st.number_input(
            f"{elective}",
            min_value=0,
            max_value=100,
            value=1,
            step=1,
            key=f"classes_{elective}",
            help=f"Number of classes open for {elective}.",
        )

total_input_capacity = sum(
    int(v) * logic.DEFAULT_MAX_CLASS_SIZE for v in classes_by_elective.values()
)

st.write(f"**Total elective capacity:** {total_input_capacity} places")


# ---------------------------------------------------------------------------
# User option: modules allowed per course
# ---------------------------------------------------------------------------

st.header("5. Number of elective modules allowed per course")

st.caption(
    "Example: BS = 1 means a Business Studies student may receive 1 elective. "
    "ITB = 2 means an ITB student may receive 2 electives."
)

course_module_quota = {}

if selected_courses:
    quota_cols = st.columns(min(len(selected_courses), 5))

    for i, course in enumerate(selected_courses):
        with quota_cols[i % len(quota_cols)]:
            course_module_quota[course] = st.number_input(
                f"{course} modules",
                min_value=0,
                max_value=len(logic.CANONICAL_ELECTIVES),
                value=1,
                step=1,
                key=f"quota_{course}",
                help=f"Number of elective modules allowed for {course} students.",
            )
else:
    st.warning("Select at least one eligible course.")


# ---------------------------------------------------------------------------
# Run allocation
# ---------------------------------------------------------------------------

st.divider()

allocate_disabled = not (students and selected_courses)

allocate_clicked = st.button(
    "Allocate electives",
    type="primary",
    disabled=allocate_disabled,
)

if allocate_clicked:
    result = logic.allocate_students(
        students=students,
        selected_courses=selected_courses,
        course_module_quota=course_module_quota,
        classes_by_elective=classes_by_elective,
        max_class_size=logic.DEFAULT_MAX_CLASS_SIZE,
        seed=int(seed),
    )

    st.session_state.result = result


# ---------------------------------------------------------------------------
# Display results
# ---------------------------------------------------------------------------

if "result" in st.session_state:
    result: logic.AllocationResult = st.session_state.result

    st.header("Allocation results")

    if result.warnings:
        with st.expander("Allocation warnings", expanded=False):
            for warning in result.warnings:
                st.warning(warning)

    metrics = result.metrics

    if metrics:
        m1, m2, m3, m4, m5 = st.columns(5)

        m1.metric(
            "Students selected",
            metrics.get("total_students_selected", 0),
        )

        m2.metric(
            "Module slots requested",
            metrics.get("total_demand", 0),
        )

        m3.metric(
            "Allocated slots",
            metrics.get("total_allocated", 0),
        )

        m4.metric(
            "Unmet demand",
            metrics.get("unmet_demand", 0),
        )

        m5.metric(
            "Seats filled / capacity",
            f"{metrics.get('total_allocated', 0)} / {metrics.get('total_capacity', 0)}",
        )

        st.subheader("Allocations by preference rank")

        rank_distribution = metrics.get("rank_distribution", {})

        rank_df = pd.DataFrame(
            {
                "Preference rank": list(rank_distribution.keys()),
                "Allocations": list(rank_distribution.values()),
            }
        )

        st.bar_chart(rank_df.set_index("Preference rank"))

    tabs = st.tabs(
        [
            "Student assignments",
            "Elective summary",
            "Raw parsed students",
            "Warnings",
        ]
    )

    # -----------------------------------------------------------------------
    # Student assignments tab
    # -----------------------------------------------------------------------
    with tabs[0]:
        assignment_df = pd.DataFrame(result.assignment_rows)

        if assignment_df.empty:
            st.info("No assignment rows were produced.")
        else:
            st.dataframe(assignment_df, use_container_width=True)

            csv = assignment_df.to_csv(index=False).encode("utf-8")
            st.download_button(
                "Download student assignments CSV",
                data=csv,
                file_name="student_elective_assignments.csv",
                mime="text/csv",
            )

            unmet_df = assignment_df[assignment_df["Unmet Demand"] > 0]

            if not unmet_df.empty:
                st.subheader("Students with unmet demand")
                st.dataframe(unmet_df, use_container_width=True)

    # -----------------------------------------------------------------------
    # Elective summary tab
    # -----------------------------------------------------------------------
    with tabs[1]:
        summary_df = pd.DataFrame(result.elective_summary)

        if summary_df.empty:
            st.info("No elective summary was produced.")
        else:
            st.dataframe(summary_df, use_container_width=True)

            chart_df = summary_df.set_index("Elective")[
                ["Allocated", "Capacity"]
            ]
            st.bar_chart(chart_df)

            csv = summary_df.to_csv(index=False).encode("utf-8")
            st.download_button(
                "Download elective summary CSV",
                data=csv,
                file_name="elective_summary.csv",
                mime="text/csv",
            )

    # -----------------------------------------------------------------------
    # Raw parsed students tab
    # -----------------------------------------------------------------------
    with tabs[2]:
        raw_rows = []

        for s in students:
            raw_rows.append(
                {
                    "Response ID": s.response_id,
                    "Student Number": s.student_number,
                    "Name": s.name,
                    "Email": s.email,
                    "Raw Course": s.raw_course,
                    "Mapped Course": s.course_code or "UNMAPPED",
                    "Class": s.class_name,
                    "Internship Semester": s.internship_semester,
                    "Completion Time": str(s.completion_time or ""),
                    "Preferences": "; ".join(s.preferences),
                    "Row Warnings": "; ".join(s.warnings),
                }
            )

        raw_df = pd.DataFrame(raw_rows)

        if raw_df.empty:
            st.info("No parsed student rows are available.")
        else:
            st.dataframe(raw_df, use_container_width=True)

    # -----------------------------------------------------------------------
    # Warnings tab
    # -----------------------------------------------------------------------
    with tabs[3]:
        st.subheader("Load warnings")

        if load_warnings:
            for warning in load_warnings:
                st.warning(warning)
        else:
            st.success("No load warnings.")

        st.subheader("Allocation warnings")

        if result.warnings:
            for warning in result.warnings:
                st.warning(warning)
        else:
            st.success("No allocation warnings.")

        st.subheader("Student-level parsing warnings")

        warning_rows = []

        for s in students:
            if s.warnings:
                warning_rows.append(
                    {
                        "Student Number": s.student_number,
                        "Name": s.name,
                        "Course": s.course_code or "UNMAPPED",
                        "Warnings": "; ".join(s.warnings),
                    }
                )

        if warning_rows:
            st.dataframe(pd.DataFrame(warning_rows), use_container_width=True)
        else:
            st.success("No student-level parsing warnings.")
