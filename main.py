import pandas as pd
import random
from ortools.sat.python import cp_model

# Load data
df = pd.read_csv("employees_350.csv")

# Constants
FLOORS = {1: 50, 2: 48}  # Floor: Seats
MAX_DEPT_PERCENT = 0.6

# Shuffle employee order to avoid bias
employees = list(df['ID'])
random.seed(42)
random.shuffle(employees)

# Group employees by department
departments = df.groupby('Department')['ID'].apply(list).to_dict()


# Model
model = cp_model.CpModel()

# Variables
emp_floor = {
    (e, f): model.NewBoolVar(f'emp_{e}_floor_{f}')
    for e in employees
    for f in FLOORS
}

emp_offsite = {
    e: model.NewBoolVar(f'emp_{e}_offsite')
    for e in employees
}

# Seat integer + boolean assignment variables
emp_seat = {
    (e, f): model.NewIntVar(0, FLOORS[f], f'emp_{e}_seat_{f}')
    for e in employees
    for f in FLOORS
}

emp_seat_bool = {}
for e in employees:
    for f, cap in FLOORS.items():
        for seat in range(1, cap + 1):
            emp_seat_bool[(e, f, seat)] = model.NewBoolVar(f'emp_{e}_floor_{f}_seat_{seat}')

# Each employee assigned exactly one place (a floor or offsite)
for e in employees:
    model.Add(sum(emp_floor[(e, f)] for f in FLOORS) + emp_offsite[e] == 1)

# Link emp_seat integer with seat bool vars per employee-floor
for e in employees:
    for f, cap in FLOORS.items():
        model.Add(emp_seat[(e, f)] == sum(seat * emp_seat_bool[(e, f, seat)] for seat in range(1, cap + 1)))
        model.Add(sum(emp_seat_bool[(e, f, seat)] for seat in range(1, cap + 1)) == emp_floor[(e, f)])

# Floor seat capacity constraints
for f, cap in FLOORS.items():
    model.Add(sum(emp_floor[(e, f)] for e in employees) <= cap)

# No two employees share the same seat on the same floor
for f, cap in FLOORS.items():
    for seat in range(1, cap + 1):
        model.AddAtMostOne(emp_seat_bool[(e, f, seat)] for e in employees)

# Max 60% of each department on-site (physical floors only)
for dept, members in departments.items():
    max_on_site = int(MAX_DEPT_PERCENT * len(members))
    model.Add(
        sum(emp_floor[(e, f)] for e in members for f in FLOORS) <= max_on_site
    )

# Teams sit on the same floor
for dept, members in departments.items():
    for f in FLOORS:
        for e in members:
            for m in members:
                if m != e:
                    # If both e and m are on-site, they must be on the same floor
                    model.Add(emp_floor[(e, f)] <= emp_offsite[m] + emp_floor[(m, f)])



# On-site indicator per employee
on_site = {}
for e in employees:
    on_site[e] = model.NewBoolVar(f'on_site_{e}')
    model.Add(on_site[e] == sum(emp_floor[(e, f)] for f in FLOORS))

# Fairness-based objective: maximize unique employees on-site first, then fill seats
model.Maximize(
    sum(on_site[e] for e in employees) * 1000  # primary: unique employees on-site
    + sum(emp_floor[(e, f)] for e in employees for f in FLOORS)  # secondary: seat fill
)

# Solve
solver = cp_model.CpSolver()
solver.parameters.max_time_in_seconds = 30
solver.parameters.search_branching = cp_model.PORTFOLIO_SEARCH
status = solver.Solve(model)

# Output
print(f"Solver status: {solver.StatusName(status)}")

if status in (cp_model.OPTIMAL, cp_model.FEASIBLE):
    seating_plan = []
    for e in employees:
        assigned_floor = None
        assigned_seat = None
        is_offsite = solver.Value(emp_offsite[e]) == 1
        if is_offsite:
            assigned_floor = 'Offsite'
            assigned_seat = None
        else:
            for f in FLOORS:
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
    seating_df = pd.DataFrame(seating_plan)
    seating_df.to_csv("seating_plan.csv", index=False)
    print("Seating plan saved to seating_plan.csv")
else:
    print("No feasible solution found.")
