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
    
    # Define floor-specific color palettes - more professional and coordinated
    floor_palettes = {
        1: [  # Floor 1: Blue palette (from dark blue to blue-grey)
            "#1A237E", "#283593", "#303F9F", "#3949AB", "#3F51B5", 
            "#5C6BC0", "#7986CB", "#9FA8DA", "#C5CAE9", "#8C9EFF"
        ],
        2: [  # Floor 2: Green palette (from dark green to teal)
            "#1B5E20", "#2E7D32", "#388E3C", "#43A047", "#4CAF50", 
            "#66BB6A", "#81C784", "#A5D6A7", "#C8E6C9", "#00C853"
        ]
    }
    
    # Use a default palette if the floor isn't specifically defined
    default_palette = [
        "#3366CC", "#DC3912", "#FF9900", "#109618", "#990099", "#0099C6",
        "#DD4477", "#66AA00", "#B82E2E", "#316395"
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
    floor_accent_colors = {1: "#3F51B5", 2: "#4CAF50"}
    accent_color = floor_accent_colors.get(floor_num, "#4285F4")
    
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
                base_color = dept_colors.get(dept, "#cccccc")
                
                # Create a more subtle gradient variation using seat position
                # This creates a consistent look within departments
                if floor_num == 1:  # Blue floor - darker to lighter gradient
                    gradient_end = "#E8EAF6"  # Light blue-grey
                elif floor_num == 2:  # Green floor - darker to lighter gradient
                    gradient_end = "#E8F5E9"  # Light green-grey
                else:
                    gradient_end = "#F5F5F5"  # Light grey
                
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
                    empty_seat_style = "background: linear-gradient(135deg, #E3F2FD, #C5CAE9);"
                elif floor_num == 2:
                    empty_seat_style = "background: linear-gradient(135deg, #E8F5E9, #C8E6C9);"
                else:
                    empty_seat_style = "background: linear-gradient(135deg, #e0e0e0, #d0d0d0);"
                
                tables_html += f"""
                <div class="seat empty-seat" style="left: {left}%; top: {top}%; {empty_seat_style}">
                    <span class="seat-num">{seat_num}</span>
                </div>
                """
        
        tables_html += '</div>\n</div>\n'
    
    return floor_summary + legend_html + tables_html

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
            
            # Return complete HTML with all floor plans
            return f"""
            <style>
                .floor-plot-container {{
                    margin-bottom: 40px;
                    padding: 20px;
                    background-color: #ffffff;
                    border-radius: 8px;
                    box-shadow: 0 4px 12px rgba(0,0,0,0.1);
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
                    background: radial-gradient(circle, #ffffff 0%, #f0f0f0 100%);
                    border: 2px solid #ddd;
                    margin: 20px auto;
                    box-shadow: 0 3px 10px rgba(0,0,0,0.08);
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
                    color: white;
                    font-size: 14px;
                    box-shadow: 0 2px 5px rgba(0,0,0,0.2);
                    transform: translate(-50%, -50%);
                    transition: transform 0.2s ease, box-shadow 0.2s ease;
                    border: 2px solid rgba(255,255,255,0.7);
                }}
                .seat:hover {{
                    transform: translate(-50%, -50%) scale(1.1);
                    box-shadow: 0 4px 8px rgba(0,0,0,0.3);
                    z-index: 10;
                }}
                .empty-seat {{
                    color: #999;
                    border: 1px dashed #bbb;
                }}
                .emp-id {{
                    font-size: 14px;
                    font-weight: bold;
                    text-shadow: 0 1px 2px rgba(0,0,0,0.4);
                }}
                .seat-num {{
                    position: absolute;
                    font-size: 10px;
                    background: rgba(255,255,255,0.9);
                    border-radius: 50%;
                    width: 16px;
                    height: 16px;
                    text-align: center;
                    line-height: 16px;
                    color: #333;
                    top: -5px;
                    right: -5px;
                    border: 1px solid #ddd;
                    box-shadow: 0 1px 3px rgba(0,0,0,0.1);
                }}
                .employee-tooltip {{
                    visibility: hidden;
                    width: 120px;
                    background-color: rgba(51,51,51,0.95);
                    color: #fff;
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
                    box-shadow: 0 5px 15px rgba(0,0,0,0.2);
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
                    color: #444;
                    font-size: 14px;
                }}
                .legend {{
                    margin-top: 15px;
                    margin-bottom: 20px;
                    padding: 12px 15px;
                    border-radius: 6px;
                    background-color: #f9f9f9;
                    border: 1px solid #eaeaea;
                    box-shadow: 0 1px 3px rgba(0,0,0,0.05);
                }}
                .legend-item {{
                    display: inline-block;
                    margin-right: 15px;
                    margin-bottom: 8px;
                    font-size: 13px;
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
                    background-color: #f5f5f5;
                    border-radius: 6px;
                    font-size: 15px;
                    box-shadow: 0 1px 3px rgba(0,0,0,0.05);
                }}
                h2, h3 {{
                    font-family: Arial, sans-serif;
                    color: #333;
                    margin-bottom: 15px;
                }}
                h2 {{
                    font-size: 22px;
                    font-weight: 500;
                    text-align: center;
                    margin-bottom: 20px;
                    color: #1a73e8;
                }}
            </style>
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


if __name__ == "__main__":
    # Create necessary directories
    Path("static").mkdir(exist_ok=True)
    Path("uploads").mkdir(exist_ok=True)
    Path("processed").mkdir(exist_ok=True)
    
    uvicorn.run(app, host="0.0.0.0", port=8000)
