from fastapi import FastAPI, UploadFile, File
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, HTMLResponse
import uvicorn
import pandas as pd
from pathlib import Path
import random
from ortools.sat.python import cp_model
import matplotlib.pyplot as plt
import numpy as np

app = FastAPI()

# Mount static files directory
app.mount("/static", StaticFiles(directory="static"), name="static")

@app.get("/")
async def root():
    return FileResponse("static/index.html")

@app.post("/upload")
async def upload_file(file: UploadFile = File(...)):
    # Save uploaded file
    content = await file.read()
    with open(f"uploads/{file.filename}", "wb") as f:
        f.write(content)
    
    # Process the CSV and solve the seating plan
    df = pd.read_csv(f"uploads/{file.filename}")
    df.to_csv("employees_350.csv", index=False)  # Save as employees_350.csv for solver
    
    # Call solve endpoint
    await solve_seating()
    
    # Copy the solved seating plan to processed directory
    if Path("seating_plan.csv").exists():
        output_path = f"processed/{file.filename}"
        seating_df = pd.read_csv("seating_plan.csv")
        seating_df.to_csv(output_path, index=False)
        return {"filename": file.filename}
    else:
        return {"error": "Could not generate seating plan"}

@app.get("/download/{filename}")
async def download_file(filename: str):
    return FileResponse(
        f"processed/{filename}",
        media_type="text/csv",
        filename=f"processed_{filename}"
    )

# Constants
FLOORS = {1: 50, 2: 48}  # Floor: Seats
MAX_DEPT_PERCENT = 0.6

# Solver
@app.post("/solve")
async def solve_seating():
    # Load data
    df = pd.read_csv("employees_350.csv")

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
        # Sort by Department first, then by ID within each department
        seating_df = seating_df.sort_values(['Department', 'ID'])
        seating_df.to_csv("seating_plan.csv", index=False)
        print("Seating plan saved to seating_plan.csv")
    else:
        print("No feasible solution found.")

def create_floor_diagram(floor_num, seats, df):
    plt.figure(figsize=(20, 15))
    
    # Table configuration
    SEATS_PER_TABLE = 6
    num_tables = (seats + SEATS_PER_TABLE - 1) // SEATS_PER_TABLE  # Round up division
    tables_per_row = 4  # 4 tables per row looks good
    
    # Calculate grid dimensions for tables
    table_rows = (num_tables + tables_per_row - 1) // tables_per_row
    
    # Debug prints
    print(f"Floor {floor_num}: {seats} seats need {num_tables} tables")
    
    # Convert Assigned_Floor to numeric, handling 'Offsite'
    df['Assigned_Floor'] = pd.to_numeric(df['Assigned_Floor'], errors='coerce')
    
    # Create table positions
    for table_row in range(table_rows):
        for table_col in range(tables_per_row):
            table_num = table_row * tables_per_row + table_col + 1
            if table_num <= num_tables:  # Changed condition to check table number
                # Calculate center position for this table
                center_x = table_col * 3
                center_y = -table_row * 4
                
                # Draw table outline (rectangle)
                table_width = 2
                table_height = 2
                plt.gca().add_patch(plt.Rectangle(
                    (center_x - table_width/2, center_y - table_height/2),
                    table_width, table_height, fill=False, color='gray'
                ))
                
                # Calculate how many seats this table should have
                seats_this_table = min(SEATS_PER_TABLE, seats - (table_num - 1) * SEATS_PER_TABLE)
                
                # Place seats around the table
                for seat_idx in range(seats_this_table):
                    # Calculate seat position (hexagonal arrangement)
                    angle = seat_idx * (2 * np.pi / SEATS_PER_TABLE)
                    radius = 1.2  # Distance from table center
                    seat_x = center_x + radius * np.cos(angle)
                    seat_y = center_y + radius * np.sin(angle)
                    
                    # Calculate actual seat number
                    seat_num = (table_num - 1) * SEATS_PER_TABLE + seat_idx + 1
                    if seat_num <= seats:  # Keep this check for safety
                        # Find employee assigned to this seat
                        mask = (df['Assigned_Floor'] == float(floor_num)) & (df['Assigned_Seat'] == float(seat_num))
                        employee = df[mask]
                        
                        if not employee.empty:
                            emp_id = employee['ID'].iloc[0]
                            plt.plot(seat_x, seat_y, 'o', markersize=20, color='lightblue')
                            plt.text(seat_x, seat_y, str(int(emp_id)), 
                                   horizontalalignment='center',
                                   verticalalignment='center',
                                   fontsize=10,
                                   weight='bold')
                        else:
                            plt.plot(seat_x, seat_y, 'o', markersize=20, color='lightgray')
                
                # Add table number
                plt.text(center_x, center_y, f'Table {table_num}', 
                        horizontalalignment='center',
                        verticalalignment='center',
                        fontsize=8,
                        color='gray')

    plt.title(f'Floor {floor_num} - {seats} seats ({num_tables} tables)')
    plt.grid(False)
    plt.axis('equal')
    plt.axis('off')
    plt.tight_layout()
    plt.savefig(f'static/floor_{floor_num}.png', dpi=300, bbox_inches='tight')
    plt.close()

@app.get("/visualize")
async def visualize_floors():
    if Path("seating_plan.csv").exists():
        df = pd.read_csv("seating_plan.csv")
        
        # Create floor diagrams
        create_floor_diagram(1, 50, df)
        create_floor_diagram(2, 48, df)
        
        return {
            "floor1_url": "/static/floor_1.png",
            "floor2_url": "/static/floor_2.png"
        }
    return {"error": "No seating plan available"}

if __name__ == "__main__":
    # Create necessary directories
    Path("static").mkdir(exist_ok=True)
    Path("uploads").mkdir(exist_ok=True)
    Path("processed").mkdir(exist_ok=True)
    
    uvicorn.run(app, host="0.0.0.0", port=8000)

