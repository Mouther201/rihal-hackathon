import streamlit as st
import pandas as pd
import random
from pathlib import Path
from ortools.sat.python import cp_model
import matplotlib.pyplot as plt
import numpy as np
import io

# --- Config ---
st.set_page_config(page_title="Seating Planner", layout="wide")
MAX_DEPT_PERCENT = 0.6
FLOORS = {1: 50, 2: 48}

# --- Solver Function ---
def solve_seating(df, floors, max_dept_percent):
    employees = list(df['ID'])
    random.seed(42)
    random.shuffle(employees)

    departments = df.groupby('Department')['ID'].apply(list).to_dict()
    model = cp_model.CpModel()

    emp_floor = {(e, f): model.NewBoolVar(f'emp_{e}_floor_{f}') for e in employees for f in floors}
    emp_offsite = {e: model.NewBoolVar(f'emp_{e}_offsite') for e in employees}
    emp_seat = {(e, f): model.NewIntVar(0, floors[f], f'emp_{e}_seat_{f}') for e in employees for f in floors}
    emp_seat_bool = {(e, f, seat): model.NewBoolVar(f'emp_{e}_floor_{f}_seat_{seat}')
                     for e in employees for f, cap in floors.items() for seat in range(1, cap + 1)}

    for e in employees:
        model.Add(sum(emp_floor[(e, f)] for f in floors) + emp_offsite[e] == 1)

    for e in employees:
        for f, cap in floors.items():
            model.Add(emp_seat[(e, f)] == sum(seat * emp_seat_bool[(e, f, seat)]
                                              for seat in range(1, cap + 1)))
            model.Add(sum(emp_seat_bool[(e, f, seat)] for seat in range(1, cap + 1)) == emp_floor[(e, f)])

    for f, cap in floors.items():
        model.Add(sum(emp_floor[(e, f)] for e in employees) <= cap)

    for f, cap in floors.items():
        for seat in range(1, cap + 1):
            model.AddAtMostOne(emp_seat_bool[(e, f, seat)] for e in employees)

    for dept, members in departments.items():
        max_on_site = int(max_dept_percent * len(members))
        model.Add(sum(emp_floor[(e, f)] for e in members for f in floors) <= max_on_site)

    for dept, members in departments.items():
        for f in floors:
            for e in members:
                for m in members:
                    if m != e:
                        model.Add(emp_floor[(e, f)] <= emp_offsite[m] + emp_floor[(m, f)])

    on_site = {e: model.NewBoolVar(f'on_site_{e}') for e in employees}
    for e in employees:
        model.Add(on_site[e] == sum(emp_floor[(e, f)] for f in floors))

    model.Maximize(sum(on_site[e] for e in employees) * 1000 +
                   sum(emp_floor[(e, f)] for e in employees for f in floors))

    solver = cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = 30
    solver.parameters.search_branching = cp_model.PORTFOLIO_SEARCH
    status = solver.Solve(model)

    seating_plan = []
    if status in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        for e in employees:
            assigned_floor = None
            assigned_seat = None
            if solver.Value(emp_offsite[e]) == 1:
                assigned_floor = 'Offsite'
            else:
                for f in floors:
                    if solver.Value(emp_floor[(e, f)]) == 1:
                        assigned_floor = f
                        assigned_seat = solver.Value(emp_seat[(e, f)])
                        break
            seating_plan.append({
                'ID': e,
                'Department': df.loc[df['ID'] == e, 'Department'].values[0],
                'Assigned_Floor': assigned_floor,
                'Assigned_Seat': assigned_seat
            })
    return pd.DataFrame(seating_plan)

# --- Visualization ---
def create_floor_diagram(floor_num, seats, df):
    plt.figure(figsize=(10, 8))
    SEATS_PER_TABLE = 6
    num_tables = (seats + SEATS_PER_TABLE - 1) // SEATS_PER_TABLE
    tables_per_row = 4
    table_rows = (num_tables + tables_per_row - 1) // tables_per_row

    df['Assigned_Floor'] = pd.to_numeric(df['Assigned_Floor'], errors='coerce')

    for table_row in range(table_rows):
        for table_col in range(tables_per_row):
            table_num = table_row * tables_per_row + table_col + 1
            if table_num <= num_tables:
                center_x = table_col * 3
                center_y = -table_row * 4
                table_width, table_height = 2, 2
                plt.gca().add_patch(plt.Rectangle(
                    (center_x - table_width/2, center_y - table_height/2),
                    table_width, table_height, fill=False, color='gray'
                ))

                seats_this_table = min(SEATS_PER_TABLE, seats - (table_num - 1) * SEATS_PER_TABLE)
                for seat_idx in range(seats_this_table):
                    angle = seat_idx * (2 * np.pi / SEATS_PER_TABLE)
                    radius = 1.2
                    seat_x = center_x + radius * np.cos(angle)
                    seat_y = center_y + radius * np.sin(angle)
                    seat_num = (table_num - 1) * SEATS_PER_TABLE + seat_idx + 1
                    if seat_num <= seats:
                        mask = (df['Assigned_Floor'] == float(floor_num)) & (df['Assigned_Seat'] == float(seat_num))
                        employee = df[mask]
                        if not employee.empty:
                            emp_id = employee['ID'].iloc[0]
                            plt.plot(seat_x, seat_y, 'o', markersize=15, color='lightblue')
                            plt.text(seat_x, seat_y, str(int(emp_id)), ha='center', va='center', fontsize=8)
                        else:
                            plt.plot(seat_x, seat_y, 'o', markersize=15, color='lightgray')

    plt.title(f'Floor {floor_num}')
    plt.axis('equal')
    plt.axis('off')
    buf = io.BytesIO()
    plt.savefig(buf, format="png", dpi=150, bbox_inches="tight")
    plt.close()
    buf.seek(0)
    return buf

# --- UI ---
st.title("🏢 Office Seating Planner")
uploaded_file = st.file_uploader("Upload Employees CSV", type=["csv"])

if uploaded_file:
    df = pd.read_csv(uploaded_file)
    st.write("### Uploaded Data", df.head())

    if st.button("Run Seating Solver"):
        seating_df = solve_seating(df, FLOORS, MAX_DEPT_PERCENT)
        st.success("Seating plan generated!")
        st.write(seating_df)

        col1, col2 = st.columns(2)
        with col1:
            img1 = create_floor_diagram(1, FLOORS[1], seating_df)
            st.image(img1, caption="Floor 1")
        with col2:
            img2 = create_floor_diagram(2, FLOORS[2], seating_df)
            st.image(img2, caption="Floor 2")

        csv_buf = io.BytesIO()
        seating_df.to_csv(csv_buf, index=False)
        csv_buf.seek(0)
        st.download_button("Download Seating Plan CSV", data=csv_buf,
                           file_name="seating_plan.csv", mime="text/csv")
