from fastapi import FastAPI, UploadFile, File
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, HTMLResponse
import uvicorn
import pandas as pd
from pathlib import Path
import random
from ortools.sat.python import cp_model
import numpy as np
import plotly.graph_objects as go
import plotly.express as px
from plotly.subplots import make_subplots
import json

app = FastAPI()

# Mount static files directory
app.mount("/static", StaticFiles(directory="static"), name="static")

@app.get("/")
async def root():
    return FileResponse("static/index.html")

@app.post("/upload")
async def upload_file(file: UploadFile = File(...)):
    try:
        # Create directories if they don't exist
        Path("uploads").mkdir(exist_ok=True)
        Path("processed").mkdir(exist_ok=True)
        
        # Save uploaded file
        content = await file.read()
        upload_path = Path("uploads") / file.filename
        with open(upload_path, "wb") as f:
            f.write(content)
        
        # Process the CSV and solve the seating plan
        df = pd.read_csv(upload_path)
        df.to_csv("employees_350.csv", index=False)
        
        # Call solve endpoint
        await solve_seating()
        
        # Copy the solved seating plan to processed directory
        if Path("seating_plan.csv").exists():
            processed_path = Path("processed") / file.filename
            seating_df = pd.read_csv("seating_plan.csv")
            seating_df.to_csv(processed_path, index=False)
            print(f"Saved processed file to: {processed_path}")
            
            # Added debug information
            print(f"Generated seating plan with {len(seating_df)} employees")
            print(f"Floors: {seating_df['Assigned_Floor'].unique().tolist()}")
            print(f"Tables per floor: {seating_df.groupby('Assigned_Floor')['Assigned_Table'].nunique().to_dict()}")
            
            return {"filename": file.filename}
        else:
            return {"error": "Could not generate seating plan"}
    except Exception as e:
        print(f"Error processing file: {str(e)}")
        return {"error": f"Error processing file: {str(e)}"}

@app.get("/download/{filename}")
async def download_file(filename: str):
    try:
        file_path = Path("processed") / filename
        if not file_path.exists():
            return {"error": f"File {filename} not found"}
        
        return FileResponse(
            path=file_path,
            media_type="text/csv",
            filename=f"processed_{filename}",
            headers={"Content-Disposition": f"attachment; filename=processed_{filename}"}
        )
    except Exception as e:
        print(f"Error downloading file: {str(e)}")
        return {"error": f"Error downloading file: {str(e)}"}

# Constants
FLOORS = {1: 50, 2: 48}  # Floor: Seats
MAX_DEPT_PERCENT = 0.6
SEATS_PER_TABLE = 6

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
        table_counters = {}  # Track seats per table
        
        for e in employees:
            assigned_floor = None
            assigned_seat = None
            assigned_table = None
            
            if solver.Value(emp_offsite[e]) == 1:
                assigned_floor = 'Offsite'
            else:
                for f in FLOORS:
                    if solver.Value(emp_floor[(e, f)]) == 1:
                        assigned_floor = f
                        seat_num = solver.Value(emp_seat[(e, f)])
                        assigned_seat = seat_num
                        # Calculate table number based on seat number
                        assigned_table = ((seat_num - 1) // SEATS_PER_TABLE) + 1
                        break
            
            seating_plan.append({
                'ID': e,
                'Department': df.loc[df['ID'] == e, 'Department'].values[0],
                'Assigned_Floor': assigned_floor,
                'Assigned_Table': assigned_table,
                'Assigned_Seat': assigned_seat
            })
        seating_df = pd.DataFrame(seating_plan)
        # Sort by Department first, then by ID within each department
        seating_df = seating_df.sort_values(['Department', 'ID'])
        seating_df.to_csv("seating_plan.csv", index=False)
        print("Seating plan saved to seating_plan.csv")
    else:
        print("No feasible solution found.")

# Create a modern interactive floor diagram using Plotly
def create_interactive_floor_diagram(floor_number, max_seats, seating_df):
    # Filter data for this floor
    floor_df = seating_df[seating_df['Assigned_Floor'] == floor_number].copy()
    
    # Calculate the grid size based on the number of tables needed
    num_tables = max(1, (max_seats + SEATS_PER_TABLE - 1) // SEATS_PER_TABLE)
    grid_size = max(1, int(np.ceil(np.sqrt(num_tables))))
    
    # Create figure with subplots for tables
    fig = make_subplots(
        rows=grid_size, cols=grid_size,
        subplot_titles=[f"Table {i+1}" for i in range(num_tables)],
        vertical_spacing=0.15,
        horizontal_spacing=0.05
    )
    
    # Create a colormap for departments
    departments = seating_df['Department'].unique()
    dept_colors = px.colors.qualitative.Bold[:min(len(departments), len(px.colors.qualitative.Bold))]
    color_map = dict(zip(departments, dept_colors))
    
    # For each table, create a visualization
    for table_num in range(1, num_tables + 1):
        row = (table_num - 1) // grid_size + 1
        col = (table_num - 1) % grid_size + 1
        
        # Get employees at this table
        table_df = floor_df[floor_df['Assigned_Table'] == table_num].copy()
        
        # Table shape (hexagon to represent a round table)
        table_r = 0.4
        table_points = []
        for i in range(7):
            angle = i * 2 * np.pi / 6
            x = table_r * np.cos(angle)
            y = table_r * np.sin(angle)
            table_points.append((x, y))
        
        table_x, table_y = zip(*table_points)
        
        # Add table shape
        fig.add_trace(
            go.Scatter(
                x=table_x, y=table_y,
                fill="toself",
                fillcolor='rgba(240,240,240,0.8)',
                line=dict(color='rgba(180,180,180,1)', width=2),
                hoverinfo='skip',
                showlegend=False
            ),
            row=row, col=col
        )
        
        # Add seats around the table
        seat_positions = []
        seat_r = 0.65
        for i in range(SEATS_PER_TABLE):
            angle = i * 2 * np.pi / SEATS_PER_TABLE
            x = seat_r * np.cos(angle)
            y = seat_r * np.sin(angle)
            seat_positions.append((x, y, i+1))
        
        # Plot seats and employees
        for x, y, seat_num in seat_positions:
            # Find employee at this table and seat number
            seat_position = (table_num - 1) * SEATS_PER_TABLE + seat_num
            seat_employee = floor_df[floor_df['Assigned_Seat'] == seat_position]
            
            if not seat_employee.empty:
                emp_id = seat_employee['ID'].values[0]
                dept = seat_employee['Department'].values[0]
                seat_color = color_map.get(dept, 'grey')
                
                # Plot filled seat with employee info
                fig.add_trace(
                    go.Scatter(
                        x=[x], y=[y],
                        mode='markers+text',
                        marker=dict(size=25, color=seat_color, line=dict(color='white', width=2)),
                        text=str(emp_id),
                        textposition="middle center",
                        hoverinfo='text',
                        hovertext=f"ID: {emp_id}<br>Department: {dept}<br>Table: {int(table_num)}<br>Seat: {seat_num}", # Fixed: replaced local_seat_num with seat_num
                        showlegend=False
                    ),
                    row=row, col=col
                )
            else:
                # Plot empty seat
                fig.add_trace(
                    go.Scatter(
                        x=[x], y=[y],
                        mode='markers',
                        marker=dict(size=20, color='rgba(200,200,200,0.5)', line=dict(color='grey', width=1)),
                        hoverinfo='text',
                        hovertext=f"Empty Seat {seat_num}", # Fixed: replaced local_seat_num with seat_num
                        showlegend=False
                    ),
                    row=row, col=col
                )
    
    # Create a department color legend (but only once)
    for dept, color in color_map.items():
        fig.add_trace(
            go.Scatter(
                x=[None], y=[None],
                mode='markers',
                marker=dict(size=10, color=color),
                name=dept,
                showlegend=True
            )
        )
    
    # Update layout for a modern look
    fig.update_layout(
        title=f"Floor {floor_number} Seating Plan",
        height=grid_size * 350,
        width=max(800, grid_size * 350),  # Minimum width
        plot_bgcolor='rgba(255,255,255,1)',
        paper_bgcolor='rgba(255,255,255,1)',
        margin=dict(l=20, r=20, t=60, b=20),
        legend=dict(
            title="Departments",
            orientation="h",
            yanchor="bottom",
            y=-0.15,
            xanchor="center",
            x=0.5,
            bgcolor='rgba(255,255,255,0.8)',
            bordercolor='rgba(0,0,0,0.1)',
            borderwidth=1
        ),
        font=dict(family="Arial, sans-serif"),
    )
    
    # Set all subplots to have the same scale with no axes
    fig.update_xaxes(showgrid=False, zeroline=False, visible=False, range=[-1, 1])
    fig.update_yaxes(showgrid=False, zeroline=False, visible=False, range=[-1, 1])
    
    return fig

# Create a simple fallback visualization 
def create_simple_fallback_visualization(df):
    result = []
    
    # Generate a summary table per floor
    for floor_num in sorted(df['Assigned_Floor'].dropna().unique()):
        floor_df = df[df['Assigned_Floor'] == floor_num]
        floor_tables = floor_df.groupby('Assigned_Table').size().to_dict()
        
        result.append(f"<h4>Floor {int(floor_num)}</h4>")
        result.append("<table class='fallback-table'>")
        result.append("<tr><th>Table</th><th>Employees</th><th>Departments</th></tr>")
        
        for table_num in sorted(floor_df['Assigned_Table'].dropna().unique()):
            table_df = floor_df[floor_df['Assigned_Table'] == table_num]
            table_depts = ", ".join(sorted(table_df['Department'].unique()))
            result.append(f"<tr><td>Table {int(table_num)}</td><td>{len(table_df)}</td><td>{table_depts}</td></tr>")
        
        result.append("</table>")
    
    return "\n".join(result)

def create_simple_floor_diagram(floor_num, floor_df):
    """Create a simple HTML visualization of tables and seats"""
    # Get unique departments for color coding
    departments = floor_df['Department'].unique()
    
    # Define floor-specific color palettes using the new theme colors
    floor_palettes = {
        1: [  # Floor 1: Blue palette based on #95BBFE and #668BCC
            "#668BCC", "#7597D1", "#85A3D6", "#95AFDB", "#95BBFE", 
            "#A5C5FE", "#B5CFFE", "#C5DAFE", "#D5E6FE", "#E5F2FE"
        ],
        2: [  # Floor 2: Purple-tinged palette based on primary colors with some #9A47AA
            "#9A47AA", "#8A5CB8", "#7A71C6", "#6986D4", "#5A9BE2", 
            "#4AB0F0", "#3AC5FE", "#95BBFE", "#668BCC", "#566BA9"
        ]
    }
    
    # Use a default palette if the floor isn't specifically defined
    default_palette = [
        "#95BBFE", "#668BCC", "#9A47AA", "#7A71C6", "#6986D4", 
        "#5A9BE2", "#4AB0F0", "#3AC5FE"
    ]
    
    # Get the appropriate color palette for this floor
    colors = floor_palettes.get(floor_num, default_palette)
    
    # Create department color mapping with consistent colors
    dept_colors = {}
    for i, dept in enumerate(sorted(departments)):  # Sort for consistency
        base_color = colors[i % len(colors)]
        # Store base color for use in legend
        dept_colors[dept] = base_color
    
    # Create legend with department counts
    legend_html = '<div class="legend"><strong>Departments:</strong><br>'
    for dept, color in sorted(dept_colors.items()):
        dept_count = len(floor_df[floor_df['Department'] == dept])
        legend_html += f'<div class="legend-item"><span class="legend-color" style="background-color: {color};"></span> {dept} ({dept_count})</div>'
    legend_html += '</div>'
    
    # Add floor summary with floor-specific styling
    floor_accent_colors = {1: "#95BBFE", 2: "#9A47AA"}
    accent_color = floor_accent_colors.get(floor_num, "#668BCC")
    
    floor_summary = f"""
    <div class="floor-summary" style="border-left: 4px solid {accent_color};">
        <strong>Floor {floor_num} Summary:</strong> 
        {len(floor_df)} employees, 
        {len(floor_df['Assigned_Table'].unique())} tables
    </div>
    """
    
    # Group employees by table
    tables_html = ""
    for table_num in sorted(floor_df['Assigned_Table'].dropna().unique()):
        table_df = floor_df[floor_df['Assigned_Table'] == table_num]
        
        # Create table container
        tables_html += f'<div class="table-container">\n'
        tables_html += f'<div class="table-title">Table {int(table_num)} ({len(table_df)} employees)</div>\n'
        tables_html += f'<div class="table">\n'
        
        # Add seats around the table
        for i in range(SEATS_PER_TABLE):
            angle = i * 360 / SEATS_PER_TABLE
            # Calculate position on a circle
            left = 50 + 40 * np.cos(np.radians(angle))
            top = 50 + 40 * np.sin(np.radians(angle))
            
            # Find the employee at this relative seat position within the table
            seat_num = i + 1  # Relative seat number (1-6)
            
            # Find employees with this relative position
            emp_df = table_df[table_df['Assigned_Seat'].apply(
                lambda x: isinstance(x, (int, float)) and ((int(x) - 1) % SEATS_PER_TABLE) + 1 == seat_num)]
            
            if len(emp_df) > 0:
                emp = emp_df.iloc[0]
                emp_id = emp['ID']
                dept = emp['Department']
                
                # Get base color for this department
                base_color = dept_colors.get(dept, "#95BBFE")
                
                # Create a gradient using the new theme colors
                if floor_num == 1:
                    gradient_end = "#FFFFFF"  # White end for gradient
                elif floor_num == 2:
                    gradient_end = "#F5E6FA"  # Light purple-white for floor 2
                else:
                    gradient_end = "#FFFFFF"
                
                # Add a subtle gradient effect using CSS
                tables_html += f"""
                <div class="seat" style="left: {left}%; top: {top}%; background: linear-gradient(135deg, {base_color}, {gradient_end});">
                    <span class="emp-id">{emp_id}</span>
                    <span class="seat-num">{seat_num}</span>
                    <div class="employee-tooltip">ID: {emp_id}<br>Dept: {dept}<br>Seat: {seat_num}</div>
                </div>
                """
            else:
                # Empty seats with floor-specific styling
                empty_seat_style = ""
                if floor_num == 1:
                    empty_seat_style = "background: linear-gradient(135deg, #D5E6FE, #FFFFFF);"
                elif floor_num == 2:
                    empty_seat_style = "background: linear-gradient(135deg, #E5D5EA, #FFFFFF);"
                else:
                    empty_seat_style = "background: linear-gradient(135deg, #D9D9D9, #FFFFFF);"
                
                tables_html += f"""
                <div class="seat empty-seat" style="left: {left}%; top: {top}%; {empty_seat_style}">
                    <span class="seat-num">{seat_num}</span>
                </div>
                """
        
        tables_html += '</div>\n</div>\n'
    
    return floor_summary + legend_html + tables_html

@app.get("/departments")
async def get_departments():
    """Return list of departments from the seating plan"""
    if Path("seating_plan.csv").exists():
        df = pd.read_csv("seating_plan.csv")
        departments = sorted(df['Department'].unique().tolist())
        return {"departments": departments}
    return {"departments": []}

@app.get("/filter/{department}")
async def filter_by_department(department: str):
    """Return filtered seating data for a specific department"""
    if Path("seating_plan.csv").exists():
        df = pd.read_csv("seating_plan.csv")
        if department.lower() == "all":
            filtered_df = df
        else:
            filtered_df = df[df['Department'] == department]
        
        # Convert to list of dictionaries for JSON response
        filtered_data = filtered_df.to_dict(orient='records')
        return {"data": filtered_data, "count": len(filtered_data)}
    return {"error": "No seating plan available"}

@app.get("/calendar-data")
async def get_calendar_data(department: str = "All"):
    """Return calendar attendance data, optionally filtered by department"""
    if Path("seating_plan.csv").exists():
        df = pd.read_csv("seating_plan.csv")
        
        # Filter by department if specified
        if department.lower() != "all":
            df = df[df['Department'] == department]
        
        # Count employees by floor for calendar view
        floor_counts = df['Assigned_Floor'].value_counts().to_dict()
        
        # Format data for calendar display
        # Let's assume a 5-day work week (Mon-Fri)
        # In a real app, you would use actual dates
        weekdays = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday"]
        
        calendar_data = []
        # Generate simple pattern: floor 1 on Mon/Wed/Fri, floor 2 on Tue/Thu
        # In a real app, this would be based on actual scheduling data
        for day_idx, day in enumerate(weekdays):
            day_data = {
                "day": day,
                "floor1": 0,
                "floor2": 0,
                "offsite": 0
            }
            
            # Alternate between floors based on day of week
            if day_idx % 2 == 0:  # Mon, Wed, Fri
                # Everyone assigned to floor 1 works on these days
                day_data["floor1"] = len(df[df['Assigned_Floor'] == 1])
                day_data["offsite"] = len(df[df['Assigned_Floor'] == 'Offsite']) 
            else:  # Tue, Thu
                # Everyone assigned to floor 2 works on these days
                day_data["floor2"] = len(df[df['Assigned_Floor'] == 2])
                day_data["offsite"] = len(df[df['Assigned_Floor'] == 'Offsite'])
            
            calendar_data.append(day_data)
        
        return {"calendar": calendar_data, "department": department}
    
    return {"error": "No seating plan available"}

@app.get("/visualize", response_class=HTMLResponse)
async def visualize_floors():
    if Path("seating_plan.csv").exists():
        try:
            df = pd.read_csv("seating_plan.csv")
            df['Assigned_Floor'] = pd.to_numeric(df['Assigned_Floor'], errors='coerce')
            
            # Debug information
            print(f"Found {len(df)} employees in seating plan")
            print(f"Floor numbers: {df['Assigned_Floor'].dropna().unique().tolist()}")
            
            # Check if we have any seat assignments
            if df['Assigned_Seat'].isna().all():
                return "<p>No seat assignments found in the data</p>"
            
            # Create a div to hold the plots
            plot_divs = ""
            
            # Generate simple plot for each floor
            for floor_num in sorted(df['Assigned_Floor'].dropna().unique()):
                if floor_num == 'Offsite':
                    continue
                    
                floor_num = int(floor_num)  # Ensure floor_num is an integer
                print(f"Creating diagram for floor {floor_num}")
                
                # Get employees on this floor
                floor_df = df[df['Assigned_Floor'] == floor_num]
                print(f"Employees on floor {floor_num}: {len(floor_df)}")
                
                if len(floor_df) == 0:
                    continue
                
                # Generate HTML for this floor's visualization
                floor_html = create_simple_floor_diagram(floor_num, floor_df)
                
                # Add this plot to our HTML
                plot_divs += f"""
                <div class="floor-plot-container">
                    <h2>Floor {floor_num} Seating Arrangement</h2>
                    {floor_html}
                </div>
                """
            
            # Generate calendar data directly here
            weekdays = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday"]
            calendar_html = ""
            
            # Get total employees count
            total_employees = len(df)
            
            # Calculate calendar attendance
            # Define ranges for each floor
            floor1_min, floor1_max = 45, 50
            floor2_min, floor2_max = 44, 48
            
            # Generate floor counts for each day
            calendar_data = []
            for day_idx, day in enumerate(weekdays):
                # Vary the counts slightly by day to make it more realistic
                if day_idx % 2 == 0:  # Monday, Wednesday, Friday - higher Floor 1 attendance
                    floor1_count = min(floor1_max, max(floor1_min, int(floor1_max * (0.95 + 0.05 * (day_idx % 3)))))
                    floor2_count = min(floor2_max, max(floor2_min, int(floor2_min * (0.9 + 0.05 * (day_idx % 3)))))
                else:  # Tuesday, Thursday - higher Floor 2 attendance
                    floor1_count = min(floor1_max, max(floor1_min, int(floor1_min * (0.9 + 0.05 * (day_idx % 2)))))
                    floor2_count = min(floor2_max, max(floor2_min, int(floor2_max * (0.95 + 0.05 * (day_idx % 2)))))
                
                # Calculate offsite as remaining employees
                offsite_count = total_employees - (floor1_count + floor2_count)
                
                # Ensure offsite count is not negative
                if offsite_count < 0:
                    # Adjust floor counts to ensure offsite is at least 0
                    excess = abs(offsite_count)
                    floor1_reduction = min(excess // 2, floor1_count - floor1_min)
                    floor1_count -= floor1_reduction
                    excess -= floor1_reduction
                    
                    floor2_reduction = min(excess, floor2_count - floor2_min)
                    floor2_count -= floor2_reduction
                    
                    offsite_count = total_employees - (floor1_count + floor2_count)
                day_class = "calendar-day" + (" calendar-day-today" if day_idx == 0 else "")
                
                calendar_html += f"""
                <div class="{day_class}" data-day="{day.lower()}">
                    <h3>{day}</h3>
                    <div class="calendar-attendance">
                        <div class="attendance-item">
                            <span>Floor 1:</span>
                            <span class="attendance-floor1">{floor1_count} employees</span>
                        </div>
                        <div class="attendance-item">
                            <span>Floor 2:</span>
                            <span class="attendance-floor2">{floor2_count} employees</span>
                        </div>
                        <div class="attendance-item">
                            <span>Offsite:</span>
                            <span class="attendance-offsite">{offsite_count} employees</span>
                        </div>
                    </div>
                </div>
                """
            
            # Return complete HTML with all floor plans and new calendar view
            return f"""
            <style>
                @import url('https://fonts.googleapis.com/css2?family=Courier+Prime:wght@400;700&display=swap');
                
                * {{
                    font-family: 'Courier Prime', monospace;
                }}
                
                .floor-plot-container {{
                    margin-bottom: 40px;
                    padding: 20px;
                    background-color: #FFFFFF;
                    border-radius: 8px;
                    box-shadow: 0 4px 12px rgba(102, 139, 204, 0.2);
                    border-top: 3px solid #95BBFE;
                }}
                
                .table-container {{
                    display: inline-block;
                    margin: 10px;
                    vertical-align: top;
                }}
                .table {{
                    position: relative;
                    width: 200px;
                    height: 200px;
                    border-radius: 50%;
                    background: radial-gradient(circle, #FFFFFF 0%, #F5F5F5 100%);
                    border: 2px solid #95BBFE;
                    margin: 20px auto;
                    box-shadow: 0 3px 10px rgba(102, 139, 204, 0.15);
                }}
                .seat {{
                    position: absolute;
                    width: 46px;
                    height: 46px;
                    border-radius: 50%;
                    display: flex;
                    align-items: center;
                    justify-content: center;
                    font-weight: bold;
                    color: #333333;
                    font-size: 14px;
                    box-shadow: 0 2px 5px rgba(0,0,0,0.1);
                    transform: translate(-50%, -50%);
                    transition: transform 0.2s ease, box-shadow 0.2s ease;
                    border: 2px solid rgba(255,255,255,0.7);
                    font-family: 'Courier Prime', monospace;
                }}
                .seat:hover {{
                    transform: translate(-50%, -50%) scale(1.1);
                    box-shadow: 0 4px 8px rgba(102, 139, 204, 0.4);
                    z-index: 10;
                }}
                .empty-seat {{
                    color: #888;
                    border: 1px dashed #95BBFE;
                }}
                .emp-id {{
                    font-size: 14px;
                    font-weight: bold;
                    text-shadow: 0 1px 2px rgba(255,255,255,0.5);
                    font-family: 'Courier Prime', monospace;
                }}
                .seat-num {{
                    position: absolute;
                    font-size: 10px;
                    background: #FFFFFF;
                    border-radius: 50%;
                    width: 16px;
                    height: 16px;
                    text-align: center;
                    line-height: 16px;
                    color: #668BCC;
                    top: -5px;
                    right: -5px;
                    border: 1px solid #95BBFE;
                    box-shadow: 0 1px 3px rgba(102, 139, 204, 0.2);
                    font-family: 'Courier Prime', monospace;
                }}
                .employee-tooltip {{
                    visibility: hidden;
                    width: 120px;
                    background-color: #668BCC;
                    color: #FFFFFF;
                    text-align: center;
                    border-radius: 6px;
                    padding: 8px;
                    position: absolute;
                    z-index: 20;
                    bottom: 125%;
                    left: 50%;
                    margin-left: -60px;
                    opacity: 0;
                    transition: opacity 0.3s, transform 0.3s;
                    transform: translateY(10px);
                    box-shadow: 0 5px 15px rgba(102, 139, 204, 0.4);
                    font-family: 'Courier Prime', monospace;
                }}
                .seat:hover .employee-tooltip {{
                    visibility: visible;
                    opacity: 1;
                    transform: translateY(0);
                }}
                .table-title {{
                    text-align: center;
                    font-weight: 500;
                    margin-bottom: 5px;
                    color: #668BCC;
                    font-size: 14px;
                    font-family: 'Courier Prime', monospace;
                }}
                .legend {{
                    margin-top: 15px;
                    margin-bottom: 20px;
                    padding: 12px 15px;
                    border-radius: 6px;
                    background-color: #FFFFFF;
                    border: 1px solid #D9D9D9;
                    box-shadow: 0 1px 3px rgba(0,0,0,0.05);
                    font-family: 'Courier Prime', monospace;
                }}
                .legend-item {{
                    display: inline-block;
                    margin-right: 15px;
                    margin-bottom: 8px;
                    font-size: 13px;
                    color: #555;
                    font-family: 'Courier Prime', monospace;
                }}
                .legend-color {{
                    display: inline-block;
                    width: 15px;
                    height: 15px;
                    border-radius: 50%;
                    margin-right: 5px;
                    vertical-align: middle;
                    box-shadow: 0 1px 2px rgba(0,0,0,0.1);
                    border: 1px solid rgba(255,255,255,0.7);
                }}
                .floor-summary {{
                    margin-bottom: 15px;
                    padding: 12px 15px;
                    background-color: #F5F9FF;
                    border-radius: 6px;
                    font-size: 15px;
                    box-shadow: 0 1px 3px rgba(102, 139, 204, 0.1);
                    color: #668BCC;
                    font-family: 'Courier Prime', monospace;
                }}
                h2, h3, strong, p, div {{
                    font-family: 'Courier Prime', monospace;
                }}
                h2 {{
                    font-size: 22px;
                    font-weight: 700;
                    text-align: center;
                    margin-bottom: 20px;
                    background: linear-gradient(135deg, #668BCC, #9A47AA);
                    -webkit-background-clip: text;
                    -webkit-text-fill-color: transparent;
                    background-clip: text;
                    font-family: 'Courier Prime', monospace;
                }}
                
                /* Filter styles */
                .filter-container {{
                    margin-bottom: 20px;
                    padding: 15px;
                    background-color: #FFFFFF;
                    border-radius: 8px;
                    box-shadow: 0 4px 12px rgba(102, 139, 204, 0.2);
                }}
                
                .filter-label {{
                    display: inline-block;
                    margin-right: 10px;
                    font-weight: bold;
                    color: #668BCC;
                }}
                
                .filter-select {{
                    padding: 8px 12px;
                    border: 2px solid #95BBFE;
                    border-radius: 4px;
                    background-color: white;
                    font-family: 'Courier Prime', monospace;
                    color: #333;
                    cursor: pointer;
                    min-width: 200px;
                }}
                
                .filter-select:focus {{
                    outline: none;
                    border-color: #9A47AA;
                    box-shadow: 0 0 0 2px rgba(154, 71, 170, 0.2);
                }}
                
                /* Calendar styles */
                .calendar-container {{
                    margin-bottom: 40px;
                    padding: 20px;
                    background-color: #FFFFFF;
                    border-radius: 8px;
                    box-shadow: 0 4px 12px rgba(102, 139, 204, 0.2);
                    border-top: 3px solid #9A47AA;
                }}
                
                .calendar-title {{
                    text-align: center;
                    margin-bottom: 20px;
                    background: linear-gradient(135deg, #9A47AA, #668BCC);
                    -webkit-background-clip: text;
                    -webkit-text-fill-color: transparent;
                    background-clip: text;
                    font-size: 22px;
                    font-weight: 700;
                }}
                
                .calendar-grid {{
                    display: flex;
                    flex-wrap: wrap;
                    gap: 12px;
                    justify-content: space-between;
                }}
                
                .calendar-day {{
                    flex: 1;
                    min-width: 150px;
                    padding: 15px;
                    background: linear-gradient(to bottom, rgba(149, 187, 254, 0.1), rgba(255, 255, 255, 1));
                    border-radius: 8px;
                    box-shadow: 0 2px 6px rgba(0, 0, 0, 0.05);
                    border-left: 3px solid #95BBFE;
                    transition: transform 0.2s ease, box-shadow 0.2s ease;
                }}
                
                .calendar-day:hover {{
                    transform: translateY(-3px);
                    box-shadow: 0 5px 15px rgba(102, 139, 204, 0.15);
                }}
                
                .calendar-day-today {{
                    border-left: 3px solid #9A47AA;
                    background: linear-gradient(to bottom, rgba(154, 71, 170, 0.1), rgba(255, 255, 255, 1));
                    box-shadow: 0 3px 8px rgba(154, 71, 170, 0.15);
                }}
                
                .calendar-day h3 {{
                    margin-top: 0;
                    color: #668BCC;
                    font-size: 16px;
                    border-bottom: 1px solid #eee;
                    padding-bottom: 8px;
                }}
                
                .calendar-day-today h3 {{
                    color: #9A47AA;
                }}
                
                .calendar-attendance {{
                    margin-top: 10px;
                    font-size: 14px;
                }}
                
                .attendance-item {{
                    display: flex;
                    justify-content: space-between;
                    margin-bottom: 5px;
                    padding: 3px 0;
                }}
                
                .attendance-floor1 {{
                    color: #95BBFE;
                    font-weight: bold;
                }}
                
                .attendance-floor2 {{
                    color: #9A47AA;
                    font-weight: bold;
                }}
                
                .attendance-offsite {{
                    color: #888;
                    font-weight: bold;
                }}
                
                .hidden {{
                    display: none !important;
                }}
            </style>
            
            <div class="calendar-container">
                <h2 class="calendar-title">Weekly Attendance Calendar</h2>
                <div class="calendar-grid">
                    {calendar_html}
                </div>
            </div>
            
            <div id="floor-plans-container">
                {plot_divs}
            </div>
            """
        except Exception as e:
            import traceback
            error_trace = traceback.format_exc()
            print(f"Error generating visualizations: {error_trace}")
            return f"""
            <div style="color: red; padding: 20px; text-align: center;">
                <h2>Error generating floor plans</h2>
                <p>{str(e)}</p>
                <button onclick="document.querySelector('.debug-trace').style.display='block'">Show Details</button>
                <pre class="debug-trace" style="display:none; text-align:left; background:#f8f8f8; padding:15px; margin-top:20px;">
                {error_trace}
                </pre>
            </div>
            """
    return "<p>No seating plan available</p>"


@app.get("/generate-calendar")
async def generate_calendar_events():
    """Generate calendar events for onsite/offsite days for specific week of August 18-22"""
    if not Path("seating_plan.csv").exists():
        return {"error": "No seating plan available"}
    
    try:
        df = pd.read_csv("seating_plan.csv")
        
        # Use fixed dates: August 18-22, 2023 (Monday-Friday)
        from datetime import datetime
        
        # Define the specific dates we want (August 18-22, 2023)
        specific_dates = [
            {"date": "20230818", "formatted_date": "Aug 18", "day_name": "Monday"},
            {"date": "20230819", "formatted_date": "Aug 19", "day_name": "Tuesday"},
            {"date": "20230820", "formatted_date": "Aug 20", "day_name": "Wednesday"},
            {"date": "20230821", "formatted_date": "Aug 21", "day_name": "Thursday"},
            {"date": "20230822", "formatted_date": "Aug 22", "day_name": "Friday"}
        ]
        
        # Create calendar events based on floor assignment
        calendar_events = []
        
        for day_idx, date_info in enumerate(specific_dates):
            date = date_info["date"]
            day_name = date_info["day_name"]
            formatted_date = date_info["formatted_date"]
            
            # Determine if it's a Floor 1 day or Floor 2 day
            if day_idx % 2 == 0:  # Mon, Wed, Fri
                title = f"Office Day - Floor 1 ({day_name}, {formatted_date})"
                location = "Office Floor 1"
                description = f"Working onsite at Floor 1 on {day_name}, {formatted_date}"
            else:  # Tue, Thu
                title = f"Office Day - Floor 2 ({day_name}, {formatted_date})"
                location = "Office Floor 2"
                description = f"Working onsite at Floor 2 on {day_name}, {formatted_date}"
                
            # Start and end times (9am to 5pm)
            start_time = "090000"
            end_time = "170000"
            
            # Create Google Calendar event URL
            event_url = (
                f"https://calendar.google.com/calendar/render?"
                f"action=TEMPLATE"
                f"&text={title}"
                f"&dates={date}T{start_time}/{date}T{end_time}"
                f"&details={description}"
                f"&location={location}"
                f"&sf=true"
                f"&output=xml"
            )
            
            calendar_events.append({
                "date": date,
                "day_name": day_name,
                "formatted_date": formatted_date,
                "title": title,
                "location": location,
                "event_url": event_url
            })
        
        # Return events for the specific week
        return {
            "events": calendar_events,
            "week_start": "2023-08-18",
            "week_end": "2023-08-22",
            "all_events_url": generate_combined_calendar_url(calendar_events)
        }
    except Exception as e:
        import traceback
        print(f"Error generating calendar events: {traceback.format_exc()}")
        return {"error": f"Error generating calendar events: {str(e)}"}

def generate_combined_calendar_url(events):
    """Generate a URL to add all events to calendar at once"""
    # Unfortunately, Google Calendar doesn't support adding multiple events at once via URL
    # So we'll just return the URL for the first event as a fallback
    if events:
        return events[0]["event_url"]
    return ""

if __name__ == "__main__":
    # Create necessary directories
    Path("static").mkdir(exist_ok=True)
    Path("uploads").mkdir(exist_ok=True)
    Path("processed").mkdir(exist_ok=True)
    
    uvicorn.run(app, host="0.0.0.0", port=8000)
if __name__ == "__main__":
    # Create necessary directories
    Path("static").mkdir(exist_ok=True)
    Path("uploads").mkdir(exist_ok=True)
    Path("processed").mkdir(exist_ok=True)
    
    uvicorn.run(app, host="0.0.0.0", port=8000)
